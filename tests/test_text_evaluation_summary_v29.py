"""Public aggregation boundaries, using explicit fixtures rather than MLX."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/summarize-text-evaluation-v29.py'
spec = importlib.util.spec_from_file_location('text_evaluation_summary_v29', SCRIPT)
summary = importlib.util.module_from_spec(spec)
spec.loader.exec_module(summary)
SECRET = 'PRIVATE_FIXTURE_ANSWER_/Users/fixture/secret_flat'


def fixture(label='advertising-only', *, count=150, smoke=False, crashed=False):
    inputs = {'base': {'configSha256': 'b' * 64, 'artifacts': {'private-file-name': 'c' * 64}},
              'runtimeConfig': {'value': {'modelPath': '/Users/private/model'}},
              'current': {'path': '/Users/private/current', 'weightsSha256': 'd' * 64, 'receiptSha256': 'e' * 64},
              'candidate': {'path': '/Users/private/candidate', 'weightsSha256': ('f' if label == 'advertising-only' else 'a') * 64,
                            'receiptSha256': '7' * 64},
              'datasetSha256': summary.FROZEN_DATASET_SHAS[label], 'datasetPath': '/Users/private/dataset.json',
              'casesSha256': summary.FROZEN_CASES_SHA, 'checkers': {'private-file-name': '8' * 64},
              'rendererArtifacts': {'private-file-name': '9' * 64}, 'rendererRulesSha256': 'a' * 64,
              'advertisingBasisSha256': 'b' * 64, 'evaluatorSha256': 'c' * 64,
              'policy': {'seed': 20261102, 'listingMaxTokens': 768, 'translationMaxTokens': 512,
                         'listingTemperature': .75, 'translationTemperature': 0, 'topP': .9, 'probeSeconds': 55}}
    report = {'executionVersion': 1, 'modelProcessIsolation': True, 'inputs': inputs,
              'automaticPromotion': False, 'acceptanceScope': 'diagnostic-custom-heldout',
              'attemptOutcome': 'failed' if crashed else 'completed', 'inputsStillFresh': True,
              'fullGateEligible': count == 150 and not smoke and not crashed,
              'fullProbeSetRequested': count == 150, 'processAttempts': {}, 'recoveredAnswers': {},
              'elapsedSeconds': 15.0, 'decision': {'accepted': False, 'message': SECRET}}
    for side in ('current', 'candidate'):
        rows = []
        for index, identity in enumerate(summary.EXPECTED[:count]):
            task = summary.TASK_BY_ID[identity]
            checks = {'passed': True, 'issues': []}
            if task == 'listing':
                checks['jsonShapeCorrect'] = True
            else:
                checks.update(numbersPreserved=True, unitsPreserved=True, moneyRolesPreserved=True,
                              paymentPeriodsPreserved=True, conditionalPreserved=True, stationNamesPreserved=True)
            rows.append({'id': identity, 'caseId': identity[:10], 'task': task, 'backend': 'mlx', 'fallback': False,
                         'text': SECRET, 'checks': checks, 'status': 'measured', 'stopReason': 'eos', 'seconds': index + 1,
                         'generationTokens': 20, 'generationTokensMeasured': True, 'tokensPerSecond': 100.0,
                         'seed': 20261102 + index, 'maxTokens': 768 if task == 'listing' else 512,
                         'temperature': .75 if task == 'listing' else 0, 'topP': .9})
        terminal = {'version': 1, 'modelSide': side, 'inputsSha256': summary.digest(inputs),
                    'expectedIdsSha256': summary.digest(summary.EXPECTED[:count]), 'rowCount': count,
                    'rowsSha256': summary.digest(rows), 'complete': True, 'inputsStillFresh': True,
                    'nativeArm64': True, 'peakMemoryBytes': 1_000_000,
                    'environment': {'nativeArm64': True, 'packages': {'mlx': '0.32.4', 'mlx-lm': '0.30.6',
                        'transformers': '5.22.1', 'tokenizers': '0.23.3'}}}
        failed_side = crashed and side == 'candidate'
        report['processAttempts'][side] = {'admitted': not failed_side, 'returnCode': -11 if failed_side else 0,
                   'journalRows': count, 'journalError': None, 'terminal': terminal, 'elapsedSeconds': 6.0,
                   'stopReason': 'native-signal-11' if failed_side else 'finished',
                   'resources': {'samples': [{'physicalBytes': 8 * 1024**3, 'swapUsedBytes': 0},
                                             {'physicalBytes': 8 * 1024**3, 'swapUsedBytes': 123}],
                                 'peakProcessGroupRssBytes': 2_000_000, 'minimumAvailableEstimateBytes': 1_000_000}}
        report[side] = [] if smoke or failed_side else rows
        if smoke or failed_side:
            report['recoveredAnswers'][side] = rows
    return report


def rebind(report, side):
    rows = report[side] or report['recoveredAnswers'].get(side, [])
    terminal = report['processAttempts'][side]['terminal']
    terminal['inputsSha256'] = summary.digest(report['inputs'])
    terminal['rowsSha256'] = summary.digest(rows)


class TextSummaryTests(unittest.TestCase):
    def test_full_report_counts_privacy_and_measured_distributions(self):
        report = fixture()
        failed = report['candidate'][0]
        failed['checks'].update(passed=False, numbersPreserved=False, issues=['Числа перевода отличаются от оригинала ' + SECRET])
        partial = report['candidate'][1]
        partial.update(status='incomplete', stopReason='token-limit')
        rebind(report, 'candidate')
        result = summary.summarize({'advertising-only': (report, '1' * 64)})
        safe = json.dumps(result, ensure_ascii=False)
        for private in (SECRET, '/Users', 'private-file-name', 'heldout-01', 'Числа перевода отличаются'):
            self.assertNotIn(private, safe)
        measured = result['mixtures']['advertising-only']
        self.assertTrue(measured['completePairComparable'])
        self.assertEqual(measured['sides']['candidate']['all']['completedFactsPassed'], 148)
        self.assertEqual(measured['sides']['candidate']['all']['partialOutputsPassingOnlyFactChecks'], 1)
        self.assertEqual(measured['sides']['candidate']['tasks']['ru-zh']['requested'], 30)
        self.assertEqual(measured['sides']['candidate']['tasks']['listing']['requested'], 90)
        self.assertEqual(measured['sides']['candidate']['all']['seconds']['p95'], 143)
        comparisons = measured['currentToCandidate']['all']
        self.assertEqual(comparisons['completeAndFactCorrect']['regressed'], 2)
        self.assertEqual(comparisons['factsAmongBothCompleted']['nonComparable'], 1)
        self.assertEqual(comparisons['factsAmongBothCompleted']['regressed'], 1)
        self.assertFalse(result['productionGateValidated'])

    def test_smoke_full_length_and_crashed_full_side_never_credit_quality(self):
        for options in ({'count': 5, 'smoke': True}, {'count': 150, 'smoke': True}, {'crashed': True}):
            with self.subTest(options=options):
                result = summary.summarize({'advertising-only': (fixture(**options), '1' * 64)})['mixtures']['advertising-only']
                self.assertFalse(result['completePairComparable'])
                self.assertIsNone(result['sides']['candidate']['all']['completedFactsPassed'])
                self.assertEqual(result['sides']['candidate']['all']['requested'], 150)
                self.assertEqual(result['currentToCandidate']['all']['completeAndFactCorrect']['nonComparable'], 150)
                self.assertGreater(result['sides']['candidate']['all']['diagnosticCompletedFactChecksPassedObserved'], 0)

    def test_binding_and_regime_comparability(self):
        first, second = fixture(), fixture('translation-retention')
        result = summary.summarize({'advertising-only': (first, '1' * 64), 'translation-retention': (second, '2' * 64)})
        self.assertTrue(result['mixtureComparison']['completeComparisonEligible'])
        second['inputs']['checkers']['private-file-name'] = '0' * 64
        for side in ('current', 'candidate'):
            rebind(second, side)
        result = summary.summarize({'advertising-only': (first, '1' * 64), 'translation-retention': (second, '2' * 64)})
        self.assertFalse(result['mixtureComparison']['completeComparisonEligible'])
        self.assertFalse(result['mixtureComparison']['sameComparisonInputs']['checkers'])
        self.assertEqual(result['mixtureComparison']['candidateAdvertisingOnlyToTranslationRetention']['all']['completion']['nonComparable'], 150)

    def test_invalid_schema_order_metrics_hashes_and_sampling_fail_closed(self):
        mutations = [
            lambda r: r['candidate'].reverse(),
            lambda r: r['candidate'][0].update(id=r['candidate'][1]['id']),
            lambda r: r['candidate'][0].update(seconds=float('nan')),
            lambda r: r['candidate'][0].update(seconds=10**1000),
            lambda r: r['candidate'][0].update(generationTokens=True),
            lambda r: r['candidate'][0].update(seed=999),
            lambda r: r['candidate'][0].update(text='changed-after-terminal'),
            lambda r: r.update(fullGateEligible='true'),
            lambda r: r['candidate'][0]['checks'].update(passed=True, issues=['invalid']),
            lambda r: r['processAttempts']['candidate'].update(lastState=['invalid']),
            lambda r: r['processAttempts']['candidate']['terminal']['environment']['packages'].update(mlx='/private/path'),
            lambda r: r['inputs'].update(casesSha256='0' * 64),
            lambda r: r.update(decision=['invalid']),
            lambda r: r['candidate'][0].update(status='measured', stopReason='memory-error'),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                report = fixture()
                mutate(report)
                with self.assertRaises(summary.InvalidReport):
                    summary.summarize({'advertising-only': (report, '1' * 64)})

    def test_cli_is_standalone_readonly_and_rejects_duplicate_json_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source, target = folder / 'private.json', folder / 'public.json'
            raw = json.dumps(fixture()).encode()
            source.write_bytes(raw)
            output = subprocess.run([sys.executable, str(SCRIPT), '--advertising-only', str(source), '--output', str(target)],
                                    capture_output=True, text=True, timeout=10, cwd=directory)
            self.assertEqual(output.returncode, 0, output.stderr)
            self.assertEqual(source.read_bytes(), raw)
            self.assertNotIn(SECRET, target.read_text())
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(output.stdout)['written'], True)
            again = subprocess.run([sys.executable, str(SCRIPT), '--advertising-only', str(source), '--output', str(target)],
                                   capture_output=True, text=True, timeout=10, cwd=directory)
            self.assertEqual(again.returncode, 2)
            self.assertNotIn(directory, again.stderr)
            source.write_text('{"a":1,"a":2}')
            with self.assertRaises(summary.InvalidReport):
                summary.read_report(source)
            source.write_text('{"a":NaN}')
            with self.assertRaises(summary.InvalidReport):
                summary.read_report(source)


if __name__ == '__main__':
    unittest.main()
