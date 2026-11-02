#!/usr/bin/env python3
"""Read-only, allowlisted public summaries of the frozen v29 paired reports.

This standalone tool imports neither MLX nor the project. It does not recheck
facts or grant acceptance. Inputs are private; output has hashes and aggregates,
never prompts, answers, free-form issues, case IDs, or filesystem paths.
"""
import argparse
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import statistics
import sys


TASK_COUNTS = {'ru-zh': 30, 'zh-ru': 30, 'listing': 90}
EXPECTED = [f'heldout-{number:02d}-{suffix}' for number in range(1, 31)
            for suffix in ('ru-zh', 'zh-ru', 'listing-1', 'listing-2', 'listing-3')]
TASK_BY_ID = {identity: ('listing' if '-listing-' in identity else identity[-5:]) for identity in EXPECTED}
HASH = re.compile(r'[a-f0-9]{64}')
FROZEN_CASES_SHA = 'b351584ef81f8ffdca04d9548ba5c8592d4f43d677b27728430e449f2bbe6b7d'
FROZEN_DATASET_SHAS = {
    'advertising-only': '36e1adf266425de49763b10fed1eabb0f4b7bded8faf3a5adfe088dd40231593',
    'translation-retention': 'bda5c2b6561235040beccf324b585c8bdfd48d5b4687e4e075478553c321a7f6'}
BOOL_CHECKS = ('numbersPreserved', 'unitsPreserved', 'moneyRolesPreserved',
               'paymentPeriodsPreserved', 'conditionalPreserved', 'stationNamesPreserved', 'jsonShapeCorrect')
STOPS = {'eos', 'stream-ended', 'token-limit', 'time-limit', 'cancelled', 'foreground-request',
         'memory-error', 'runtime-error', 'missing-runtime-metrics', 'variants-exhausted',
         'finished', 'incomplete-worker', 'parent-exited', 'deadline', 'measurement-unavailable',
         'process-rss-limit', 'available-memory-low', 'swap-growth-limit'}
ISSUE_CLASSES = {
    'format': ('json', 'формат'),
    'numbers-units': ('числа', 'единиц', 'суммы, площадь', 'площадь', 'этаж', 'количество', 'валют'),
    'money-role': ('назначение суммы', 'суммы аренды и залога'),
    'payment-period': ('период оплаты',),
    'station-route': ('станц', 'маршрут', 'метро', 'локация'),
    'missing-fact': ('пропущен', 'пропущены', 'пропущено', 'не сохранен', 'не сохранён',
                     'не сохранена', 'не сохранены', 'не сохранено'),
    'unsupported-fact': ('добавлен', 'не подтвержд', 'отсутствующие'),
    'negation-polarity-condition': ('наличие, отрицание', 'ограничение', 'условие', 'неопредел', 'отрицание'),
    'fee-scope': ('оплат', 'комисс', 'бесплат', 'регистрац'),
    'language': ('китайский текст', 'на китайском', 'перевод на выбранном'),
    'length': ('сократите', 'знаков'),
}
COMPARABLE_INPUTS = ('base', 'runtimeConfig', 'current', 'casesSha256', 'checkers',
                     'rendererArtifacts', 'rendererRulesSha256', 'advertisingBasisSha256',
                     'evaluatorSha256', 'policy')


class InvalidReport(ValueError):
    """Only fixed public error codes may be carried by this exception."""


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def reject_duplicates(items):
    result = {}
    for key, value in items:
        if key in result:
            raise InvalidReport('duplicate-json-key')
        result[key] = value
    return result


def read_report(path):
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= 32_000_000:
            raise InvalidReport('bounded-regular-report-required')
        raw = stream.read(32_000_001)
    if len(raw) > 32_000_000:
        raise InvalidReport('report-too-large')
    try:
        value = json.loads(raw, object_pairs_hook=reject_duplicates,
                           parse_constant=lambda _: (_ for _ in ()).throw(InvalidReport('nonfinite-json-number')))
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as error:
        raise InvalidReport('invalid-json-report') from error
    if not isinstance(value, dict):
        raise InvalidReport('object-report-required')
    return value, hashlib.sha256(raw).hexdigest()


def number(value, *, optional=False, integer=False):
    if optional and value is None:
        return None
    if (type(value) not in ((int,) if integer else (int, float))
            or value < 0 or value > 10**18 or not math.isfinite(value)):
        raise InvalidReport('invalid-numeric-metric')
    return value


def boolean(value):
    if type(value) is not bool:
        raise InvalidReport('invalid-boolean-field')
    return value


def sha(value, *, optional=False):
    if optional and value is None:
        return None
    if not isinstance(value, str) or not HASH.fullmatch(value):
        raise InvalidReport('invalid-source-hash')
    return value


def fixed_stop(value):
    if isinstance(value, str) and value in STOPS:
        return value
    if isinstance(value, str) and re.fullmatch(r'native-signal-[1-9][0-9]?', value):
        return 'native-signal'
    return 'other-or-unrecorded'


def distribution(values):
    values = sorted(number(value) for value in values if value is not None)
    return {'n': len(values), 'min': values[0] if values else None,
            'median': statistics.median(values) if values else None,
            'mean': statistics.fmean(values) if values else None,
            'p95': values[math.ceil(.95 * len(values)) - 1] if values else None,
            'max': values[-1] if values else None}


def validate_rows(rows):
    if not isinstance(rows, list) or len(rows) > 150:
        raise InvalidReport('bounded-row-list-required')
    identities = []
    for row in rows:
        if not isinstance(row, dict):
            raise InvalidReport('object-row-required')
        identity = row.get('id')
        if identity not in TASK_BY_ID or row.get('task') != TASK_BY_ID[identity] or row.get('caseId') != identity[:10]:
            raise InvalidReport('unknown-or-mismatched-probe')
        identities.append(identity)
        if row.get('status') not in ('measured', 'incomplete') or not isinstance(row.get('stopReason'), str):
            raise InvalidReport('invalid-completion-status')
        if not isinstance(row.get('text'), str) or len(row['text']) > 100_000:
            raise InvalidReport('invalid-bounded-answer')
        if row.get('backend') != 'mlx' or row.get('fallback') is not False:
            raise InvalidReport('raw-native-row-required')
        number(row.get('seconds'))
        measured = boolean(row.get('generationTokensMeasured'))
        number(row.get('generationTokens'), optional=not measured, integer=True)
        if not measured and row.get('generationTokens') is not None:
            raise InvalidReport('unmeasured-token-counter-present')
        number(row.get('tokensPerSecond'), optional=True)
        number(row.get('maxTokens'), integer=True)
        number(row.get('temperature'))
        number(row.get('topP'))
        number(row.get('seed'), integer=True)
        if row['status'] == 'measured' and (not measured or row['stopReason'] not in ('eos', 'stream-ended') or not row['text']):
            raise InvalidReport('measured-status-without-completed-native-answer')
        checks = row.get('checks')
        if not isinstance(checks, dict) or not isinstance(checks.get('issues'), list):
            raise InvalidReport('invalid-checker-result')
        boolean(checks.get('passed'))
        if len(checks['issues']) > 100 or any(not isinstance(item, str) or len(item) > 8000 for item in checks['issues']):
            raise InvalidReport('invalid-bounded-checker-issues')
        if checks['passed'] != (not checks['issues']):
            raise InvalidReport('inconsistent-checker-decision')
        for key in BOOL_CHECKS:
            if key in checks:
                boolean(checks[key])
    if identities != EXPECTED[:len(identities)]:
        raise InvalidReport('missing-duplicate-or-reordered-probe')
    return rows


def issue_classes(issues):
    lowered = [issue.casefold() for issue in issues]
    found = {name for name, words in ISSUE_CLASSES.items() if any(word in issue for issue in lowered for word in words)}
    if issues and not found:
        found.add('unclassified')
    return sorted(found)


def summarize_rows(rows, requested, eligible):
    completed = [row for row in rows if row['status'] == 'measured']
    partial = [row for row in rows if row['status'] != 'measured']
    flags = {}
    for key in BOOL_CHECKS:
        observed = [row for row in rows if key in row['checks']]
        if observed:
            flags[key] = {'reported': len(observed), 'notReported': len(rows) - len(observed),
                          'passedObserved': sum(row['checks'][key] for row in observed),
                          'failedObserved': sum(not row['checks'][key] for row in observed),
                          'completedPassed': sum(row['checks'][key] for row in observed if row['status'] == 'measured'),
                          'completedFailed': sum(not row['checks'][key] for row in observed if row['status'] == 'measured')}
    issue_counts = Counter()
    completed_issue_counts = Counter()
    for row in rows:
        kinds = issue_classes(row['checks']['issues'])
        issue_counts.update(kinds)
        if row['status'] == 'measured':
            completed_issue_counts.update(kinds)
    factual = sum(row['checks']['passed'] for row in completed)
    return {'requested': requested, 'saved': len(rows), 'unobserved': requested - len(rows),
            'measuredCompleted': len(completed), 'incomplete': len(partial),
            'completedFactsPassed': factual if eligible else None,
            'completedFactsPassedFractionOfRequested': factual / requested if eligible else None,
            'diagnosticCompletedFactChecksPassedObserved': factual,
            'partialOutputsPassingOnlyFactChecks': sum(row['checks']['passed'] for row in partial),
            'qualityEligible': eligible, 'partialOutputsCredited': False,
            'stopReasons': dict(sorted(Counter(fixed_stop(row['stopReason']) for row in rows).items())),
            'checkerBooleans': flags, 'issueClassesObserved': dict(sorted(issue_counts.items())),
            'issueClassesAmongCompleted': dict(sorted(completed_issue_counts.items())),
            'seconds': distribution(row['seconds'] for row in rows),
            'secondsAmongCompleted': distribution(row['seconds'] for row in completed),
            'generationTokensMeasured': distribution(row['generationTokens'] for row in rows if row['generationTokensMeasured']),
            'generationTokensMetricMissing': sum(not row['generationTokensMeasured'] for row in rows),
            'tokensPerSecondMeasured': distribution(row.get('tokensPerSecond') for row in rows),
            'tokensPerSecondMetricMissing': sum(row.get('tokensPerSecond') is None for row in rows)}


def transitions(before, after, requested, eligible, predicate, *, require_completed=False):
    result = {'requested': requested, 'bothPass': 0, 'bothFail': 0, 'improved': 0, 'regressed': 0, 'nonComparable': requested}
    if not eligible:
        return result
    for identity in before.keys() & after.keys():
        a, b = before[identity], after[identity]
        if require_completed and (a['status'] != 'measured' or b['status'] != 'measured'):
            continue
        first, second = predicate(a), predicate(b)
        if first is None or second is None:
            continue
        key = 'bothPass' if first and second else 'bothFail' if not first and not second else 'improved' if second else 'regressed'
        result[key] += 1
        result['nonComparable'] -= 1
    return result


def compare_sides(before, after, eligible):
    result = {}
    for task, requested in {'all': 150, **TASK_COUNTS}.items():
        left = {row['id']: row for row in before if task == 'all' or row['task'] == task}
        right = {row['id']: row for row in after if task == 'all' or row['task'] == task}
        result[task] = {
            'completion': transitions(left, right, requested, eligible, lambda row: row['status'] == 'measured'),
            'completeAndFactCorrect': transitions(left, right, requested, eligible,
                 lambda row: row['status'] == 'measured' and row['checks']['passed']),
            'factsAmongBothCompleted': transitions(left, right, requested, eligible,
                 lambda row: row['checks']['passed'], require_completed=True),
            'factFlagsAmongBothCompleted': {key: transitions(left, right, requested, eligible,
                 lambda row, key=key: row['checks'].get(key), require_completed=True)
                 for key in BOOL_CHECKS if any(key in row['checks'] for row in [*left.values(), *right.values()])}}
    return result


def source_identity(inputs):
    if not isinstance(inputs, dict) or any(key not in inputs for key in COMPARABLE_INPUTS):
        raise InvalidReport('missing-bound-inputs')
    identities = {}
    for side in ('current', 'candidate'):
        value = inputs.get(side)
        if value is None and side == 'current':
            identities[side] = {'weightsSha256': None, 'receiptSha256': None}
        elif isinstance(value, dict):
            identities[side] = {'weightsSha256': sha(value.get('weightsSha256')),
                                'receiptSha256': sha(value.get('receiptSha256'))}
        else:
            raise InvalidReport('invalid-adapter-identity')
    if inputs.get('casesSha256') != FROZEN_CASES_SHA:
        raise InvalidReport('frozen-thirty-group-corpus-required')
    return {'inputsSha256': digest(inputs), 'casesSha256': sha(inputs.get('casesSha256')),
            'datasetSha256': sha(inputs.get('datasetSha256')), 'adapters': identities,
            **{key + 'Sha256': digest(inputs[key]) for key in ('base', 'runtimeConfig', 'checkers', 'rendererArtifacts', 'policy')},
            **{key: sha(inputs[key]) for key in ('rendererRulesSha256', 'advertisingBasisSha256', 'evaluatorSha256')}}


def process_summary(value):
    if not isinstance(value, dict):
        raise InvalidReport('invalid-process-attempt')
    boolean(value.get('admitted'))
    code = value.get('returnCode')
    if code is not None and (type(code) is not int or not -255 <= code <= 255):
        raise InvalidReport('invalid-return-code')
    terminal = value.get('terminal') or {}
    resources = value.get('resources') or {}
    if not isinstance(terminal, dict) or not isinstance(resources, dict):
        raise InvalidReport('invalid-terminal-or-resources')
    last_state = value.get('lastState') or {}
    if not isinstance(last_state, dict):
        raise InvalidReport('invalid-worker-state')
    environment = terminal.get('environment') or last_state.get('environment') or {}
    if not isinstance(environment, dict):
        raise InvalidReport('invalid-runtime-environment')
    versions = {}
    packages = environment.get('packages') or {}
    if not isinstance(packages, dict):
        raise InvalidReport('invalid-package-versions')
    for name in ('mlx', 'mlx-lm', 'transformers', 'tokenizers'):
        version = packages.get(name)
        if version is not None and (not isinstance(version, str) or not re.fullmatch(r'[0-9][0-9A-Za-z.+-]{0,39}', version)):
            raise InvalidReport('invalid-package-version')
        versions[name] = version
    samples = resources.get('samples', [])
    if not isinstance(samples, list) or len(samples) > 720 or any(not isinstance(row, dict) for row in samples):
        raise InvalidReport('invalid-resource-samples')
    physical = [number(row['physicalBytes']) for row in samples if row.get('physicalBytes') is not None]
    swap = [number(row['swapUsedBytes']) for row in samples if row.get('swapUsedBytes') is not None]
    return {'admitted': value['admitted'], 'returnCode': code,
            'signal': -code if code is not None and code < 0 else None,
            'stopReason': fixed_stop(value.get('stopReason')), 'journalErrorPresent': bool(value.get('journalError')),
            'journalRows': number(value.get('journalRows'), integer=True),
            'elapsedSeconds': number(value.get('elapsedSeconds'), optional=True),
            'peakMlxBytes': number(terminal.get('peakMemoryBytes'), optional=True),
            'resourceSamples': len(samples),
            'peakProcessGroupRssBytes': number(resources.get('peakProcessGroupRssBytes'), optional=True),
            'minimumAvailableEstimateBytes': number(resources.get('minimumAvailableEstimateBytes'), optional=True),
            'physicalBytes': min(physical) if physical else None,
            'maximumSwapGrowthBytesObserved': max(0, max(swap) - swap[0]) if swap else None,
            'resourceStopReason': fixed_stop(resources.get('stopReason')) if resources.get('stopReason') else None,
            'nativeArm64Reported': environment.get('nativeArm64') is True, 'packageVersions': versions}


def verify_admitted_binding(metadata, side, inputs, rows):
    if metadata.get('admitted') is not True:
        return
    terminal = metadata.get('terminal')
    expected = {'version': 1, 'modelSide': side, 'inputsSha256': digest(inputs),
                'expectedIdsSha256': digest(EXPECTED[:len(rows)]), 'rowCount': len(rows),
                'rowsSha256': digest(rows), 'complete': True, 'inputsStillFresh': True, 'nativeArm64': True}
    if metadata.get('returnCode') != 0 or metadata.get('journalError') is not None or not isinstance(terminal, dict):
        raise InvalidReport('inconsistent-admitted-worker')
    if any(type(terminal.get(key)) is not type(value) or terminal[key] != value for key, value in expected.items()):
        raise InvalidReport('admitted-worker-source-or-row-hash-mismatch')


def verify_sampling(inputs, rows):
    policy = inputs.get('policy')
    expected = {'seed': 20261102, 'listingMaxTokens': 768, 'translationMaxTokens': 512,
                'listingTemperature': .75, 'translationTemperature': 0, 'topP': .9, 'probeSeconds': 55}
    if not isinstance(policy, dict) or any(policy.get(key) != value or isinstance(policy.get(key), bool)
                                         for key, value in expected.items()):
        raise InvalidReport('frozen-sampling-policy-required')
    for index, row in enumerate(rows):
        if (row['seed'] != policy['seed'] + index
                or row['maxTokens'] != policy['listingMaxTokens' if row['task'] == 'listing' else 'translationMaxTokens']
                or row['temperature'] != policy['listingTemperature' if row['task'] == 'listing' else 'translationTemperature']
                or row['topP'] != policy['topP']):
            raise InvalidReport('raw-row-sampling-contract-mismatch')


def summarize_report(report, report_sha):
    if report.get('executionVersion') != 1 or report.get('modelProcessIsolation') is not True:
        raise InvalidReport('isolated-v29-report-required')
    for key in ('inputsStillFresh', 'fullGateEligible', 'fullProbeSetRequested'):
        boolean(report.get(key))
    if report.get('attemptOutcome') not in ('completed', 'failed'):
        raise InvalidReport('invalid-attempt-outcome')
    if report.get('automaticPromotion') is not False or report.get('acceptanceScope') != 'diagnostic-custom-heldout':
        raise InvalidReport('diagnostic-v29-report-required')
    identity = source_identity(report.get('inputs'))
    decision = report.get('decision')
    if not isinstance(decision, dict):
        raise InvalidReport('invalid-reported-decision')
    boolean(decision.get('accepted'))
    process = report.get('processAttempts')
    recovered = report.get('recoveredAnswers')
    if not isinstance(process, dict) or not isinstance(recovered, dict) or set(process) - {'current', 'candidate'} or set(recovered) - {'current', 'candidate'}:
        raise InvalidReport('invalid-side-mapping')
    rows, process_public = {}, {}
    top = {}
    for side in ('current', 'candidate'):
        top[side] = validate_rows(report.get(side))
        saved = validate_rows(recovered.get(side, []))
        if top[side] and saved:
            raise InvalidReport('duplicate-top-and-recovered-side')
        rows[side] = top[side] or saved
        verify_sampling(report['inputs'], rows[side])
        if side in process:
            process_public[side] = process_summary(process[side])
            if process_public[side]['journalRows'] != len(rows[side]):
                raise InvalidReport('journal-row-count-mismatch')
            verify_admitted_binding(process[side], side, report['inputs'], rows[side])
        elif rows[side]:
            raise InvalidReport('saved-rows-without-owned-attempt')
    eligible = (report['attemptOutcome'] == 'completed' and report['inputsStillFresh']
                and report['fullGateEligible'] and report['fullProbeSetRequested']
                and all(len(top[side]) == 150 and process_public.get(side, {}).get('admitted') is True
                        and process_public[side]['returnCode'] == 0 for side in ('current', 'candidate')))
    if report['fullGateEligible'] and not eligible:
        raise InvalidReport('inconsistent-full-gate-claim')
    by_side = {}
    for side in ('current', 'candidate'):
        by_side[side] = {'admittedRows': len(top[side]), 'recoveredOnlyRows': len(rows[side]) if not top[side] else 0,
                        'all': summarize_rows(rows[side], 150, eligible),
                        'tasks': {task: summarize_rows([row for row in rows[side] if row['task'] == task], count, eligible)
                                  for task, count in TASK_COUNTS.items()}}
    return {'reportSha256': sha(report_sha), 'sourceIdentity': identity,
            'attemptOutcome': report['attemptOutcome'], 'inputsStillFresh': report['inputsStillFresh'],
            'fullGateEligibleReported': report['fullGateEligible'], 'completePairComparable': eligible,
            'requestedPerSide': 150, 'requestedByTask': TASK_COUNTS,
            'elapsedSeconds': number(report.get('elapsedSeconds')),
            'processAttempts': process_public, 'sides': by_side,
            'currentToCandidate': compare_sides(rows['current'], rows['candidate'], eligible),
            'reportedGateAccepted': decision['accepted'],
            'productionAcceptanceGranted': False}, rows


def summarize(reports):
    summaries, private_rows = {}, {}
    for label, (report, report_sha) in reports.items():
        if label not in ('advertising-only', 'translation-retention'):
            raise InvalidReport('unknown-mixture-label')
        if not isinstance(report.get('inputs'), dict) or report['inputs'].get('datasetSha256') != FROZEN_DATASET_SHAS[label]:
            raise InvalidReport('frozen-mixture-dataset-required')
        summaries[label], private_rows[label] = summarize_report(report, report_sha)
    comparison = None
    if set(reports) == {'advertising-only', 'translation-retention'}:
        first, second = (reports[key][0] for key in ('advertising-only', 'translation-retention'))
        binding = {key: first['inputs'][key] == second['inputs'][key] for key in COMPARABLE_INPUTS}
        comparable = all(binding.values()) and all(value['completePairComparable'] for value in summaries.values())
        before = private_rows['advertising-only']['candidate']
        after = private_rows['translation-retention']['candidate']
        comparison = {'sameComparisonInputs': binding, 'completeComparisonEligible': comparable,
                      'candidateAdvertisingOnlyToTranslationRetention': compare_sides(before, after, comparable),
                      'currentBaselineReproducibility': compare_sides(private_rows['advertising-only']['current'],
                          private_rows['translation-retention']['current'], comparable),
                      'sameSamplingBudgetDoesNotMeanSameAdvertisingExposure': True}
    return {'version': 1, 'scope': 'public-diagnostic-aggregate', 'automaticPromotion': False,
            'summarizerSha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'requestedPerModelPerMixture': 150, 'requestedByTask': TASK_COUNTS,
            'mixtures': summaries, 'mixtureComparison': comparison,
            'percentileMethod': 'nearest-rank', 'nativeSpeakerReviewCompleted': False,
            'rawTextIncluded': False, 'freeFormIssuesIncluded': False, 'privatePathsIncluded': False,
            'factChecksRecomputed': False, 'productionGateValidated': False,
            'limitations': ['Synthetic frozen holdout, diagnostic after first disclosure.',
                           'Automatic stored checker decisions are not independent manual fact labels.',
                           'Partial and recovered answers have no quality credit; all request denominators remain fixed.',
                           'This report does not merge answers across attempts or select the best retry.',
                           'Issue classes use a fixed string taxonomy, not an independent semantic review.',
                           'Failed earlier attempts and missing raw evidence must remain in the separate attempt ledger.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--advertising-only')
    parser.add_argument('--translation-retention')
    parser.add_argument('--output', help='Optional new JSON file; otherwise emit only the public aggregate to stdout')
    args = parser.parse_args()
    paths = {key: value for key, value in (('advertising-only', args.advertising_only),
              ('translation-retention', args.translation_retention)) if value}
    if not paths:
        parser.error('At least one paired report is required')
    try:
        result = summarize({key: read_report(path) for key, path in paths.items()})
        serialized = json.dumps(result, ensure_ascii=False, separators=(',', ':'), allow_nan=False) + '\n'
        if args.output:
            descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'w') as stream:
                stream.write(serialized)
                stream.flush()
                os.fsync(stream.fileno())
            print(json.dumps({'written': True, 'aggregateSha256': hashlib.sha256(serialized.encode()).hexdigest()}))
        else:
            print(serialized, end='')
        return 0
    except InvalidReport as error:
        print(json.dumps({'written': False, 'errorCode': str(error)}), file=sys.stderr)
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        print(json.dumps({'written': False, 'errorCode': 'unreadable-or-invalid-report'}), file=sys.stderr)
    return 2


if __name__ == '__main__':
    sys.exit(main())
