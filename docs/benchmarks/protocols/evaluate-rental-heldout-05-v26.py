"""Run one immutable base/adapter phase on independent real-listing extracts."""
import argparse
from collections import defaultdict
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import platform
import socket
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'cli'))
from rental_heldout_quality import (assess, check_training_disjoint, digest,
    generation_messages, load_cases, probes, variation_summary)
from learning_scheduler import model_resource
from text_model_contract import expected_parameters, load_dataset
from text_quality import parse_card

BASE_SHA = 'ddffab9cbc7bf6dde941c6724841eeca8981fcfa81ca20ff8efff1396326d153'
ADAPTER_SHA = '3b5a50a32bb1485eee9f6501ed870b822d57e0bbc22bd8704551977e79d22581'


def idle():
    with urllib.request.urlopen('http://127.0.0.1:8765/api/local/health', timeout=3) as response:
        state = json.load(response)
    if state.get('app') != 'redcn' or state.get('activeJobs') != 0:
        raise ValueError('Нет подтверждения отсутствия активного импорта')


@contextmanager
def offline():
    original = socket.socket.connect
    def connect(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6) and not (isinstance(address, tuple) and address[0] == '127.0.0.1' and address[1] == 8765):
            raise OSError('Heldout evaluation cannot access the network')
        return original(sock, address)
    socket.socket.connect = connect
    try:
        yield
    finally:
        socket.socket.connect = original


def run(args):
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        raise ValueError('Нужен нативный ARM64 Python на Mac')
    target = Path(args.output)
    checkpoint = target.with_suffix('.checkpoint.jsonl')
    if target.exists() or checkpoint.exists():
        raise ValueError('Для каждого измерения нужен новый выходной файл')
    corpus = load_cases(args.cases)
    independence = check_training_disjoint(corpus, load_dataset(args.training_dataset))
    fingerprints = {str(path): digest(path) for path in [Path(args.cases), Path(args.training_dataset), Path(__file__),
        *[ROOT/'cli'/name for name in ('rental_heldout_quality.py', 'text_quality.py', 'local_content.py')]]}
    model_path = Path(args.model)
    weights = list(model_path.glob('*.safetensors'))
    if len(weights) != 1 or weights[0].is_symlink() or digest(weights[0]) != BASE_SHA:
        raise ValueError('Веса базовой модели изменились')
    expected_parameters(json.loads((model_path/'config.json').read_text()))
    if args.adapter and digest(Path(args.adapter)/'adapters.safetensors') != ADAPTER_SHA:
        raise ValueError('Веса balanced-адаптера изменились')
    target.parent.mkdir(parents=True, exist_ok=True)
    rows, history = [], defaultdict(list)
    begin = time.monotonic()
    with model_resource(args.cache, timeout=3), offline(), checkpoint.open('x') as receipt:
        idle()
        import mlx.core as mx
        from mlx_lm import load, stream_generate
        from mlx_lm.sample_utils import make_sampler
        mx.set_memory_limit(2_500_000_000)
        mx.set_cache_limit(64_000_000)
        mx.random.seed(20261101)
        model, tokenizer = load(str(model_path), adapter_path=args.adapter)
        model.eval()
        for probe in probes(corpus):
            idle()
            if time.monotonic()-begin > args.max_seconds:
                row = {'id': probe['id'], 'caseId': probe['caseId'], 'task': probe['task'], 'status': 'incomplete', 'reason': 'phase-deadline'}
            else:
                messages = generation_messages(probe, history[probe['caseId']]) if probe['task'] == 'listing' else probe['messages']
                prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
                started = time.monotonic()
                chunks, limited = [], False
                stream = stream_generate(model, tokenizer, prompt=prompt, max_tokens=args.max_tokens,
                    sampler=make_sampler(temp=.35 if probe['task'] == 'listing' else 0), prefill_step_size=256)
                try:
                    for item in stream:
                        chunks.append(item.text)
                        if time.monotonic()-begin > args.max_seconds or time.monotonic()-started > 20:
                            limited = True
                            break
                finally:
                    stream.close()
                limited = limited or len(chunks) >= args.max_tokens
                text = ''.join(chunks)
                checks = assess(probe, text)
                if limited:
                    checks['passed'] = False
                    checks['issues'].append('Ответ ограничен временем или токенами')
                row = {'id': probe['id'], 'caseId': probe['caseId'], 'task': probe['task'],
                       'status': 'incomplete' if limited else 'measured', 'text': text,
                       'seconds': time.monotonic()-started, 'checks': checks}
                if probe['task'] == 'listing' and not limited:
                    try:
                        history[probe['caseId']].append(parse_card(text))
                    except ValueError:
                        pass
            rows.append(row)
            receipt.write(json.dumps(row, ensure_ascii=False)+'\n')
            receipt.flush()
            print(json.dumps({key: row[key] for key in ('id', 'status')} | {'passed': row.get('checks', {}).get('passed')}, ensure_ascii=False), flush=True)
    summary = {}
    for task in ('ru-zh', 'zh-ru', 'listing'):
        selected = [row for row in rows if row['task'] == task]
        summary[task] = {'requested': len(selected), 'measured': sum(row['status'] == 'measured' for row in selected),
                         'checksPassed': sum(row.get('checks', {}).get('passed') is True for row in selected)}
    if any(digest(path) != expected for path, expected in fingerprints.items()):
        raise ValueError('Проверочный корпус или программа изменились во время измерения; checkpoint остаётся неприёмочным')
    report = {'version': 1, 'phase': 'adapter' if args.adapter else 'base', 'caseSha256': digest(args.cases),
              'trainingDatasetSha256': digest(args.training_dataset), 'baseSha256': BASE_SHA,
              'adapterSha256': ADAPTER_SHA if args.adapter else None, 'independence': independence,
              'scriptSha256': digest(__file__), 'checkerSha256': {name: digest(ROOT/'cli'/name) for name in ('rental_heldout_quality.py', 'text_quality.py', 'local_content.py')},
              'annotation': corpus['annotation'], 'summary': summary, 'originality': variation_summary(rows),
              'rows': rows, 'seconds': time.monotonic()-begin, 'automaticPromotion': False,
              'nativeSpeakerReviewCompleted': False, 'realListingAvailabilityVerified': False,
              'limits': corpus['limitations']+['Known-fact checks and text-surface variation do not establish complete semantic or native editorial quality.']}
    with target.open('x') as output:
        output.write(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({'summary': summary, 'seconds': report['seconds'], 'output': str(target)}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('cases', 'training-dataset', 'model', 'cache', 'output'):
        parser.add_argument('--'+name, required=True)
    parser.add_argument('--adapter')
    parser.add_argument('--max-seconds', type=int, default=240)
    parser.add_argument('--max-tokens', type=int, default=360)
    args = parser.parse_args()
    if not 30 <= args.max_seconds <= 300 or not 64 <= args.max_tokens <= 512:
        parser.error('Use a bounded evaluation phase')
    run(args)
