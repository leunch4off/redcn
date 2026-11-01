"""Bounded offline comparison of current and candidate text adapters on a Mac.

The immutable report is private. It never changes selected weights by itself;
--promote additionally runs the independent acceptance gate after evaluation.
"""
import argparse
from collections import defaultdict
from contextlib import contextmanager
import gc
import json
import os
from pathlib import Path
import platform
import signal
import socket
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'cli'))
from learning_scheduler import active_imports, model_resource, _read, _write
from model_promotion import CASES, POLICY, REPORT_VERSION, capture_inputs, compare_rows, promote, record_evaluation
from advertising_evaluation import active_rules, assess, prepare_probes, request_for
from rental_heldout_quality import load_cases
from text_model_contract import expected_parameters, sampling_parameters
from text_quality import parse_card


@contextmanager
def offline():
    previous = socket.socket.connect
    def blocked(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            raise OSError('Candidate evaluation is offline')
        return previous(sock, address)
    socket.socket.connect = blocked
    try:
        yield
    finally:
        socket.socket.connect = previous


def run(args):
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        raise ValueError('Нужен нативный ARM64 Python на Mac')
    cache, target = Path(args.cache).resolve(), Path(args.output)
    if target.exists() or target.is_symlink():
        raise ValueError('Для проверки нужен новый выходной файл')
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    corpus = load_cases(CASES)
    cancel = cache/'learning/training.cancel'
    interrupted = [False]
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: interrupted.__setitem__(0, True))
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    started = time.monotonic()
    with model_resource(cache, kind='background', timeout=0), offline():
        if active_imports(cache) != 0:
            raise ValueError('Проверка ждёт завершения импорта объявлений')
        foreground = _read(cache/'learning/foreground.json', {})
        if cancel.is_symlink():
            raise ValueError('Маркер отмены не должен быть ссылкой')
        cancel.unlink(missing_ok=True)
        def must_stop():
            return (interrupted[0] or cancel.exists() or active_imports(cache) != 0
                    or _read(cache/'learning/foreground.json', {}) != foreground
                    or time.monotonic()-started >= args.max_seconds)
        inputs = capture_inputs(cache, args.candidate, args.training_dataset)
        if must_stop():
            raise ValueError('Проверка уступила место текущему запросу')
        expected_parameters(json.loads((Path(inputs['modelPath'])/'config.json').read_text()))
        import mlx.core as mx
        from mlx_lm import load, stream_generate
        from mlx_lm.sample_utils import make_sampler
        mx.set_memory_limit(2_500_000_000)
        mx.set_cache_limit(64_000_000)
        prepared = prepare_probes(corpus, rules=active_rules(cache))
        report = {'version': REPORT_VERSION, 'startedAt': time.time(), 'inputs': inputs,
                  'nativeSpeakerReviewCompleted': False, 'automaticPromotion': False,
                  'limits': {'memoryBytes': 2_500_000_000, 'seconds': args.max_seconds,
                             'offline': True, 'foregroundPriority': True}}
        for label in ('current', 'candidate'):
            identity = inputs[label]
            adapter = identity['path'] if identity else None
            if must_stop():
                report[label] = []
                continue
            model, tokenizer = load(inputs['modelPath'], adapter_path=adapter)
            model.eval()
            history, rows = defaultdict(list), []
            for index, probe in enumerate(prepared):
                if must_stop():
                    break
                seed = POLICY['seed']+index
                mx.random.seed(seed)
                request = request_for(probe, history[probe['caseId']], seed, POLICY)
                prompt = tokenizer.apply_chat_template(request['messages'], tokenize=False, add_generation_prompt=True)
                if len(tokenizer.encode(prompt)) > 2048:
                    raise ValueError('Проверочный запрос превышает рабочее окно модели')
                begin = time.monotonic()
                chunks, limited, final_item = [], False, None
                stream = stream_generate(model, tokenizer, prompt=prompt, max_tokens=request['maxTokens'],
                    sampler=make_sampler(**sampling_parameters(request['temperature'], top_p=request['topP'])),
                    prefill_step_size=256)
                try:
                    for item in stream:
                        final_item = item
                        chunks.append(item.text)
                        if must_stop() or time.monotonic()-begin > POLICY['probeSeconds']:
                            limited = True
                            break
                finally:
                    stream.close()
                generated = getattr(final_item, 'generation_tokens', len(chunks))
                limited = limited or generated >= request['maxTokens']
                output = ''.join(chunks)
                row = {'id': probe['id'], 'caseId': probe['caseId'], 'task': probe['task'],
                       'status': 'incomplete' if limited else 'measured', 'seed': seed,
                       'maxTokens': request['maxTokens'], 'temperature': request['temperature'], 'topP': request['topP'],
                       'backend': 'mlx', 'fallback': False,
                       'text': output, 'seconds': time.monotonic()-begin, 'checks': assess(probe, output)}
                rows.append(row)
                if probe['task'] == 'listing' and not limited and row['checks']['passed']:
                    try:
                        history[probe['caseId']].append(parse_card(output))
                    except ValueError:
                        pass
                print(json.dumps({'model': label, 'probe': probe['id'], 'status': row['status'],
                                  'passed': row['checks'].get('passed')}, ensure_ascii=False), flush=True)
            report[label] = rows
            del model, tokenizer
            gc.collect()
            mx.clear_cache()
        if capture_inputs(cache, args.candidate, args.training_dataset) != inputs:
            raise ValueError('Данные или настройки изменились во время оценки; результат не принимается')
        report.update(finishedAt=time.time(), elapsedSeconds=time.monotonic()-started,
                      decision=compare_rows(corpus, report.get('current'), report.get('candidate'), prepared=prepared))
        _write(target, report)
        record_evaluation(cache, target)
    result = report['decision']
    if args.promote:
        result = promote(cache, target, automatic=args.automatic)
    print(json.dumps({'report': str(target), **result}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('cache', 'candidate', 'training-dataset', 'output'):
        parser.add_argument('--'+name, required=True)
    parser.add_argument('--max-seconds', type=int, default=900)
    parser.add_argument('--promote', action='store_true')
    parser.add_argument('--automatic', action='store_true')
    args = parser.parse_args()
    if not 60 <= args.max_seconds <= 1800 or args.automatic and not args.promote:
        parser.error('Нужен предел 60–1800 секунд; --automatic требует --promote')
    run(args)
