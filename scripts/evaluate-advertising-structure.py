#!/usr/bin/env python3
"""Private native structure comparison and 10 x 20 history-aware series.

This is diagnostic only. Rendering copied facts does not count as the model
authoring correct facts. A supplied free-generation report must bind the same
model, cases, settings and validators. Every raw selector answer is journaled.
"""
import argparse
from collections import Counter, defaultdict
from contextlib import contextmanager
import json
import os
from pathlib import Path
import platform
import re
import signal
import socket
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'cli'))
from advertising_evaluation import active_rules, prepare_probes, assess
from advertising_structure import (PLAN_COUNT, TokenChoiceConstraint, fingerprint,
    parse_plan, render_plan, request_for_structure)
from learning_scheduler import active_imports, model_resource, _read
from local_content import _advertising_issues
from model_promotion import capture_inputs, POLICY
from rental_heldout_quality import load_cases
from text_model_contract import file_sha, sampling_parameters
from text_native_evaluation import measure_stream


def write_new(path, value):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


@contextmanager
def offline():
    previous = socket.socket.connect
    def blocked(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            raise OSError('Structure evaluation is offline')
        return previous(sock, address)
    socket.socket.connect = blocked
    try:
        yield
    finally:
        socket.socket.connect = previous


def assess_rendered(probe, rendered):
    # Full immutable facts can exceed the prose generator's 280-Han budget.
    # Report that contract change explicitly instead of silently dropping facts.
    maximum = max(280, len(re.findall(r'[\u3400-\u9fff]', probe['reference']))+100)
    issues = _advertising_issues(rendered, probe['baseline'], probe['fields'], max_body=maximum)
    text = (rendered['title']+' '+rendered['body']).casefold()
    for station in probe['case']['observedStations']:
        if not any(name.casefold() in text for name in station['aliases']):
            issues.append('Пропущена наблюдавшаяся станция: '+station['ru'])
    if rendered['provenance']['allFactBlocksCopiedExactly'] is not True:
        issues.append('Изменён подтверждённый блок фактов')
    return {'passed': not issues, 'issues': list(dict.fromkeys(issues)),
            'maximumBodyHan': maximum, 'allFactBlocksCopiedExactly':
                rendered['provenance']['allFactBlocksCopiedExactly'],
            'modelAuthoredFactClaims': 0}


def aggregate(rows):
    seconds = [row['seconds'] for row in rows if type(row.get('seconds')) in (int, float)]
    return {'attempted': len(rows), 'completed': sum(row.get('status') == 'measured' for row in rows),
            'validModelSchema': sum(row.get('validSchema') is True for row in rows),
            'acceptedUniqueRendered': sum(row.get('accepted') is True for row in rows),
            'repeats': sum(row.get('repeated') is True for row in rows),
            'rendererFactsPassed': sum(row.get('checks', {}).get('passed') is True for row in rows),
            'stopReasons': dict(Counter(row.get('stopReason') for row in rows)),
            'meanSeconds': sum(seconds)/len(seconds) if seconds else None,
            'maximumSeconds': max(seconds) if seconds else None,
            'modelAuthoredFactsCredited': 0}


def verified_free_report(path, inputs, side, prepared):
    if not path:
        return None
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 20_000_000:
        raise ValueError('A bounded regular raw free-generation report is required')
    data = json.loads(path.read_text())
    before = data.get('inputs', {})
    for key in ('base', 'runtimeConfig', 'casesSha256', 'checkers', 'rendererArtifacts',
                'rendererRulesSha256', 'advertisingBasisSha256', side):
        if before.get(key) != inputs.get(key):
            raise ValueError('Free-generation report has different '+key)
    expected = {row['id']: row for row in prepared if row['task'] == 'listing'}
    rows = []
    for raw in data.get(side, []):
        if raw.get('task') != 'listing':
            continue
        probe = expected.get(raw.get('id'))
        if probe is None:
            raise ValueError('Free-generation report has an unexpected listing probe')
        if (raw.get('backend') != 'mlx' or raw.get('fallback') is not False
                or raw.get('maxTokens') != POLICY['listingMaxTokens']
                or raw.get('temperature') != POLICY['listingTemperature']
                or raw.get('topP') != POLICY['topP']):
            raise ValueError('Free-generation report has a different sampling contract')
        checks = assess(probe, raw.get('text', ''))
        rows.append({**raw, 'checks': checks, 'validSchema': checks.get('jsonShapeCorrect') is True})
    if len({row['id'] for row in rows}) != len(rows):
        raise ValueError('Duplicate raw free-generation probes')
    return {'reportSha256': file_sha(path), 'adapterSide': side, 'requested': len(expected),
            'allExpectedAttemptsPresent': len(rows) == len(expected),
            'measured': sum(row['status'] == 'measured' for row in rows),
            'validSchema': sum(row['validSchema'] for row in rows),
            'factsPassed': sum(row['checks']['passed'] for row in rows),
            'stopReasons': dict(Counter(row.get('stopReason', 'unrecorded') for row in rows)),
            'meanSeconds': sum(row['seconds'] for row in rows)/len(rows) if rows else None,
            'maximumBodyHan': POLICY['listingBodyHanLimit'],
            'maximumOutputTokens': POLICY['listingMaxTokens'],
            'modelContribution': 'full-title-and-body'}


def run(args):
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        raise ValueError('Native ARM64 Python on Mac required')
    cache, target = Path(args.cache).resolve(), Path(args.output)
    journal_path = target.with_suffix(target.suffix+'.answers.jsonl')
    if any(path.exists() or path.is_symlink() for path in (target, journal_path)):
        raise ValueError('Fresh private report and journal paths required')
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    corpus = load_cases(args.cases)
    if len(corpus['cases']) < args.case_limit:
        raise ValueError('The independent corpus has fewer requested groups')
    interrupted = [False]
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: interrupted.__setitem__(0, True))
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    started = time.monotonic()
    with model_resource(cache, kind='background', timeout=0), offline():
        if active_imports(cache) != 0:
            raise ValueError('Active foreground import')
        cancel = cache/'learning/training.cancel'
        if cancel.is_symlink():
            raise ValueError('Cancellation marker cannot be a symlink')
        cancel.unlink(missing_ok=True)
        foreground = _read(cache/'learning/foreground.json', {})
        def stop_reason():
            if interrupted[0] or cancel.exists():
                return 'cancelled'
            if active_imports(cache) != 0 or _read(cache/'learning/foreground.json', {}) != foreground:
                return 'foreground-request'
            if time.monotonic()-started >= args.max_seconds:
                return 'time-limit'
            return None
        inputs = capture_inputs(cache, args.candidate, args.training_dataset, args.cases)
        prepared = prepare_probes(corpus, rules=active_rules(cache))
        free = verified_free_report(args.free_report, inputs, args.adapter_side, prepared)
        bases = [row for row in prepared if row['task'] == 'listing' and row['variant'] == 1][:args.case_limit]
        import mlx.core as mx
        from mlx_lm import load, stream_generate
        from mlx_lm.sample_utils import make_sampler
        mx.set_memory_limit(2_500_000_000)
        mx.set_cache_limit(64_000_000)
        identity = inputs[args.adapter_side]
        model, tokenizer = load(inputs['modelPath'], adapter_path=identity['path'] if identity else None)
        model.eval()
        histories, rows, comparisons, series = defaultdict(list), [], [], []
        job_index = 0
        descriptor = os.open(journal_path, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w') as journal:
            for case_index, probe in enumerate(bases):
                requested = args.series_length if case_index < args.series_groups else 3
                for ordinal in range(requested):
                    if stop_reason():
                        break
                    history = histories[probe['caseId']]
                    titles = {fingerprint(row['title']) for row in history}
                    bodies = {fingerprint(row['body']) for row in history}
                    allowed = [plan for plan in range(PLAN_COUNT)
                        if fingerprint(render_plan(probe['baseline'], plan)['title']) not in titles
                        and fingerprint(render_plan(probe['baseline'], plan)['body']) not in bodies]
                    seed = POLICY['seed']+job_index
                    job_index += 1
                    if not allowed:
                        row = {'caseId': probe['caseId'], 'ordinal': ordinal+1, 'status': 'incomplete',
                               'stopReason': 'variants-exhausted', 'accepted': False,
                               'validSchema': False, 'seconds': 0, 'text': ''}
                    else:
                        request = request_for_structure(probe['reference'], allowed, seed=seed)
                        prompt = tokenizer.apply_chat_template(request['messages'], tokenize=False, add_generation_prompt=True)
                        prompt_tokens = list(tokenizer.encode(prompt))
                        if len(prompt_tokens) > 2048:
                            raise ValueError('Structure prompt exceeds production limit')
                        mx.random.seed(seed)
                        measured = measure_stream(lambda: stream_generate(model, tokenizer, prompt=prompt,
                            max_tokens=request['maxTokens'],
                            logits_processors=[TokenChoiceConstraint(tokenizer, allowed, prompt_tokens)],
                            sampler=make_sampler(**sampling_parameters(request['temperature'])),
                            prefill_step_size=256), maximum_tokens=request['maxTokens'],
                            probe_seconds=POLICY['probeSeconds'], stop_reason=stop_reason)
                        row = {**measured, 'caseId': probe['caseId'], 'ordinal': ordinal+1,
                               'seed': seed, 'allowedPlans': allowed, 'historyCount': len(history),
                               'accepted': False, 'validSchema': False, 'repeated': False,
                               'modelContribution': 'selection-of-layout-only', 'fallback': False,
                               'maxTokens': request['maxTokens'], 'temperature': request['temperature'],
                               'topP': POLICY['topP']}
                        try:
                            plan = parse_plan(row['text'], allowed)
                            rendered = render_plan(probe['baseline'], plan)
                            row.update(validSchema=True, plan=plan, rendered=rendered,
                                       checks=assess_rendered(probe, rendered))
                            row['repeated'] = (fingerprint(rendered['title']) in titles
                                               or fingerprint(rendered['body']) in bodies)
                            row['accepted'] = row['status'] == 'measured' and row['checks']['passed'] and not row['repeated']
                            if row['accepted']:
                                history.append(rendered)
                        except ValueError:
                            row['checks'] = {'passed': False, 'issues': ['Незавершённая допустимая схема'],
                                             'modelAuthoredFactClaims': 0}
                    rows.append(row)
                    if ordinal < 3:
                        comparisons.append(row)
                    if case_index < args.series_groups:
                        series.append(row)
                    journal.write(json.dumps(row, ensure_ascii=False)+'\n')
                    journal.flush()
                    if len(rows) % 10 == 0:
                        print(json.dumps({'attempted': len(rows), 'accepted': sum(r['accepted'] for r in rows),
                                          'elapsedSeconds': time.monotonic()-started}), flush=True)
                    if row['stopReason'] in ('memory-error', 'runtime-error'):
                        interrupted[0] = True
                        break
        fresh = capture_inputs(cache, args.candidate, args.training_dataset, args.cases) == inputs
        summary = aggregate(series)
        summary.update(requestedGroups=args.series_groups, requestedPerGroup=args.series_length,
                       acceptedUniqueFraction=summary['acceptedUniqueRendered']/(args.series_groups*args.series_length)
                           if args.series_groups else None,
                       normalization='NFKC-casefold-alphanumeric; punctuation/emoji/spacing do not count',
                       groups=[{'caseId': probe['caseId'], **aggregate([r for r in series if r['caseId'] == probe['caseId']])}
                               for probe in bases[:args.series_groups]])
        report = {'version': 1, 'scope': 'diagnostic-structure-only', 'inputs': inputs,
                  'scriptSha256': file_sha(__file__), 'adapterSide': args.adapter_side,
                  'automaticPromotion': False, 'nativeSpeakerReviewCompleted': False,
                  'inputsStillFresh': fresh, 'rawAnswerJournalSha256': file_sha(journal_path),
                  'freeGeneration': free, 'structureComparison': {**aggregate(comparisons),
                       'requested': args.case_limit*3, 'requestedGroups': args.case_limit,
                       'maximumOutputTokens': 48, 'maximumBodyHan': 'full verified facts + 100 framing characters'},
                  'series': summary, 'completeFullThirtyGroupComparison': fresh and args.case_limit >= 30
                       and len(comparisons) == args.case_limit*3 and bool(free and free['allExpectedAttemptsPresent']),
                  'completeTenByTwentySeries': fresh and args.series_groups >= 10 and args.series_length >= 20
                       and len(series) == args.series_groups*args.series_length,
                  'rendererFactsAreNotModelQuality': True, 'finitePlanCount': PLAN_COUNT,
                  'elapsedSeconds': time.monotonic()-started, 'peakMemoryBytes': mx.get_peak_memory(),
                  'stopReason': stop_reason() or 'finished', 'qualityAccepted': False,
                  'limitations': ['Project-authored synthetic independent groups, not native-editor reviewed.',
                      'Structure mode preserves complete fact blocks; it changes framing/order, not factual prose.',
                      '48 finite variants per unchanged source; indefinite uniqueness is not promised.']}
        write_new(target, report)
    print(json.dumps({key: value for key, value in report.items() if key not in ('inputs', 'series')}
        | {'series': {key: value for key, value in summary.items() if key != 'groups'}}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('cache', 'candidate', 'training-dataset', 'output'):
        parser.add_argument('--'+name, required=True)
    parser.add_argument('--cases', default=str(ROOT/'data/quality/rental-heldout-v29.json'))
    parser.add_argument('--adapter-side', choices=('current', 'candidate'), default='current')
    parser.add_argument('--free-report')
    parser.add_argument('--case-limit', type=int, default=30)
    parser.add_argument('--series-groups', type=int, default=10)
    parser.add_argument('--series-length', type=int, default=20)
    parser.add_argument('--max-seconds', type=int, default=1800)
    args = parser.parse_args()
    if not (1 <= args.case_limit <= 60 and 0 <= args.series_groups <= args.case_limit
            and 3 <= args.series_length <= 48 and 60 <= args.max_seconds <= 3600):
        parser.error('Bounded cases 1–60, series 3–48, time 60–3600 seconds required')
    run(args)
