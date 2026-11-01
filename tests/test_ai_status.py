import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'cli'))
from ai_status import inspect_ai, russian_status, server_status
from photo_ai_settings import DEFAULTS, load_config


class AIStatusTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_missing_workspace_is_not_created(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'absent'
            result = inspect_ai(root)
            self.assertFalse(root.exists())
            self.assertFalse(result['text']['enabled'])
            self.assertFalse(result['photo']['automaticApplication'])
            self.assertEqual(result['processActivity'], 'not-inspected')

    def test_enabled_does_not_claim_running_or_quality(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            model = root / 'tools/text-ai/model'
            model.mkdir(parents=True)
            (model / 'config.json').write_text('{}')
            (root / 'text-ai.json').write_text(json.dumps({'enabled': True}))
            (root / 'photo-ai.json').write_text(json.dumps({**DEFAULTS, 'enabled': True, 'autoApply': False, 'model': 'redcn-furniture-v1'}))
            result = inspect_ai(root)
            self.assertTrue(result['text']['enabled'])
            self.assertTrue(result['text']['modelConfigPresent'])
            self.assertFalse(result['text']['runtimePresent'])
            self.assertFalse(result['photo']['automaticApplication'])
            self.assertFalse(result['idleTraining']['phaseIsLiveProcessProof'])
            self.assertIn('ВКЛЮЧЕНО', russian_status(result))
            self.assertFalse((root / 'learning').exists())

    def test_status_matches_environment_fallback_without_creating_config(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'REDCN_VISION_MODEL': 'fixture-vision:1b'}):
            root = Path(folder) / 'absent'
            result = inspect_ai(root)['photo']
            runtime = load_config(root)
            self.assertEqual(result['enabled'], runtime['enabled'])
            self.assertEqual(result['model'], runtime['model'])
            self.assertEqual(result['autoApplyConfigured'], runtime['autoApply'])
            self.assertEqual(result['configurationSource'], 'environment')
            self.assertFalse(root.exists())

    def test_trained_detector_requires_review_even_when_auto_apply_flag_is_true(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'photo-ai.json').write_text(json.dumps({**DEFAULTS, 'enabled': True,
                'autoApply': True, 'model': 'redcn-furniture-v1'}))
            result = inspect_ai(root)['photo']
            self.assertTrue(result['enabled'])
            self.assertTrue(result['autoApplyConfigured'])
            self.assertFalse(result['modelAllowsAutomaticApplication'])
            self.assertFalse(result['automaticApplication'])
            self.assertTrue(result['automaticReviewCapture'])
            self.assertEqual(result['effectiveMode'], 'suggestions-only')
            summary = russian_status(inspect_ai(root))
            self.assertIn('Автодобавление в журнал проверки: ВКЛЮЧЕНО', summary)
            self.assertIn('Автоподтверждение для описания: ВЫКЛЮЧЕНО — модель ещё не прошла приёмку', summary)

    def test_partial_or_unsafe_photo_config_is_reported_as_error_not_enabled(self):
        for value in ({'enabled': True}, {**DEFAULTS, 'enabled': True, 'endpoint': 'https://remote.invalid'}):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                (root / 'photo-ai.json').write_text(json.dumps(value))
                result = inspect_ai(root)
                self.assertFalse(load_config(root)['enabled'])
                self.assertIsNone(result['photo']['enabled'])
                self.assertIsNone(result['photo']['automaticApplication'])
                self.assertTrue(result['warnings'])

    def test_adapter_switch_without_a_selected_path_does_not_claim_a_selection(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'text-ai.json').write_text('{"enabled": true, "useCandidateAdapter": true}')
            self.assertFalse(inspect_ai(root)['text']['candidateAdapterSelected'])

    def test_bad_or_linked_config_is_not_mistaken_for_disabled(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'text-ai.json').write_text('broken')
            target = root / 'private.json'
            target.write_text('{"enabled":true,"secret":"DO_NOT_OUTPUT"}')
            (root / 'photo-ai.json').symlink_to(target)
            result = inspect_ai(root)
            self.assertIsNone(result['text']['enabled'])
            self.assertIsNone(result['photo']['enabled'])
            self.assertNotIn('DO_NOT_OUTPUT', json.dumps(result))

    def test_scheduler_state_is_recorded_not_live_activity(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'learning').mkdir()
            (root / 'learning/scheduler-state.json').write_text('{"phase":"training","sampleHashes":["secret"]}')
            result = inspect_ai(root)
            self.assertEqual(result['idleTraining']['recordedPhase'], 'training')
            self.assertFalse(result['idleTraining']['phaseIsLiveProcessProof'])
            self.assertNotIn('secret', json.dumps(result))

    def test_auto_accept_flag_reports_configuration_without_claiming_live_quality(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'learning').mkdir()
            (root/'learning/scheduler.json').write_text('{"enabled":true,"automaticPromotion":true}')
            before = set(root.rglob('*'))
            result = inspect_ai(root)
            self.assertTrue(result['idleTraining']['automaticPromotion'])
            self.assertFalse(result['idleTraining']['phaseIsLiveProcessProof'])
            self.assertIn('Автоматическое принятие новых весов: ВКЛЮЧЕНО', russian_status(result))
            self.assertEqual(before, set(root.rglob('*')))

    def test_health_filters_fields_and_has_bounded_loopback_request(self):
        calls = []
        def opener(url, timeout):
            calls.append((url, timeout))
            return io.BytesIO(b'{"app":"redcn","release":"v25","activeJobs":0,"secret":"omitted"}')
        value = server_status(opener=opener)
        self.assertEqual(value, {'reachable': True, 'release': 'v25', 'activeJobs': 0})
        self.assertEqual(calls, [('http://127.0.0.1:8765/api/local/health', 2)])
        self.assertNotIn('omitted', json.dumps(value))

    def test_other_service_and_timeout_remain_unknown(self):
        self.assertFalse(server_status(opener=lambda *a, **k: io.BytesIO(b'{"app":"other"}'))['reachable'])
        with self.assertRaises(ValueError):
            server_status(port=0)


if __name__ == '__main__':
    unittest.main()
