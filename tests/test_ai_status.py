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
from text_model_contract import ADVERTISING_MODEL, BASE_MODEL


TEXT_CONFIG = {'model_type': 'qwen2', 'hidden_size': 896, 'num_hidden_layers': 24,
               'num_attention_heads': 14, 'num_key_value_heads': 2}
ADVERTISING_CONFIG = {'model_type': 'qwen3', 'hidden_size': 2560, 'num_hidden_layers': 36,
                      'num_attention_heads': 32, 'num_key_value_heads': 8,
                      'head_dim': 128, 'vocab_size': 151936, 'tie_word_embeddings': True,
                      'rope_theta': 5000000, 'max_position_embeddings': 262144,
                      'quantization': {'bits': 4, 'group_size': 64}}


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

    def test_world_detector_never_claims_automatic_facts_from_auto_apply_flag(self):
        for enabled,auto_apply in ((True,True),(True,False),(False,True)):
            with self.subTest(enabled=enabled,auto_apply=auto_apply), tempfile.TemporaryDirectory() as folder:
                root=Path(folder)
                (root/'photo-ai.json').write_text(json.dumps({**DEFAULTS,'enabled':enabled,
                    'autoApply':auto_apply,'model':'redcn-furniture-world-v2'}))
                before=set(root.rglob('*'))
                value=inspect_ai(root);photo=value['photo']
                self.assertEqual(photo['provider'],'onnx-world')
                self.assertTrue(photo['reviewRequired'])
                self.assertEqual(photo['acceptedClasses'],0)
                self.assertEqual(photo['automaticFacts'],0)
                self.assertFalse(photo['modelAllowsAutomaticApplication'])
                self.assertFalse(photo['automaticApplication'])
                self.assertEqual(photo['automaticReviewCapture'],enabled and auto_apply)
                self.assertEqual(photo['effectiveMode'],'suggestions-only' if enabled and auto_apply else 'off')
                self.assertIn('YOLO-World v2 · локальные предложения',russian_status(value))
                self.assertIn('Категорий для автоподтверждения: 0',russian_status(value))
                self.assertEqual(before,set(root.rglob('*')))

    def test_adapter_switch_without_a_selected_path_does_not_claim_a_selection(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'text-ai.json').write_text('{"enabled": true, "useCandidateAdapter": true}')
            self.assertFalse(inspect_ai(root)['text']['candidateAdapterSelected'])

    def test_separate_advertising_profile_reports_its_architecture_and_runtime_read_only(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            model, advertising = root/'translation', root/'advertising'
            model.mkdir(); advertising.mkdir()
            (model/'config.json').write_text(json.dumps(TEXT_CONFIG))
            (advertising/'config.json').write_text(json.dumps(ADVERTISING_CONFIG))
            (advertising/'model.safetensors').write_bytes(b'not-real-weights-do-not-load')
            runtime = root/'advertising-python'
            runtime.write_text('not-an-executable')
            (root/'text-ai.json').write_text(json.dumps({'enabled': True, 'advertisingEnabled': True,
                'modelPath': str(model), 'pythonPath': str(root/'missing-translation-python'),
                'advertisingModelPath': str(advertising), 'advertisingPythonPath': str(runtime),
                'useCandidateAdapter': True, 'adapterPath': str(root/'translation-adapter')}))
            before = {str(path.relative_to(root)): path.read_bytes() for path in root.rglob('*') if path.is_file()}
            with patch('subprocess.Popen', side_effect=AssertionError('Status must not launch any worker')):
                result = inspect_ai(root)
            text, profile = result['text'], result['text']['advertising']
            self.assertEqual(text['model'], BASE_MODEL)
            self.assertFalse(text['runtimePresent'])
            self.assertTrue(text['candidateAdapterSelected'])
            self.assertEqual(profile['model'], ADVERTISING_MODEL)
            self.assertTrue(profile['modelSupported'])
            self.assertTrue(profile['modelConfigPresent'])
            self.assertTrue(profile['runtimePresent'])
            self.assertTrue(profile['separateProfile'])
            self.assertTrue(profile['automaticGenerationEnabled'])
            self.assertFalse(profile['candidateAdapterSelected'])
            self.assertFalse(profile['weightsTrainingInspected'])
            self.assertEqual(result['processActivity'], 'not-inspected')
            summary = russian_status(result)
            self.assertIn('Рекламный ИИ: ВКЛЮЧЕНО · '+ADVERTISING_MODEL+' · отдельный профиль', summary)
            self.assertIn('Выбор рекламной модели не означает обучение новых весов.', summary)
            self.assertNotIn(str(root), json.dumps(result))
            self.assertEqual(before, {str(path.relative_to(root)): path.read_bytes() for path in root.rglob('*') if path.is_file()})

    def test_shared_advertising_profile_uses_text_runtime_and_adapter_selection(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            model = root/'model'; model.mkdir()
            (model/'config.json').write_text(json.dumps(TEXT_CONFIG))
            runtime = root/'python'; runtime.write_text('placeholder')
            (root/'text-ai.json').write_text(json.dumps({'enabled': True,
                'modelPath': str(model), 'pythonPath': str(runtime),
                'advertisingPythonPath': str(root/'ignored-without-separate-model'),
                'useCandidateAdapter': True, 'adapterPath': str(root/'selected-adapter')}))
            profile = inspect_ai(root)['text']['advertising']
            self.assertFalse(profile['separateProfile'])
            self.assertEqual(profile['model'], BASE_MODEL)
            self.assertTrue(profile['runtimePresent'])
            self.assertTrue(profile['candidateAdapterSelected'])
            self.assertFalse(profile['automaticGenerationEnabled'])
            self.assertEqual(profile['modelSource'], 'shared-text-profile')

    def test_bad_separate_architecture_cannot_be_mislabeled_as_the_base_model(self):
        for content in ('broken', '{}', json.dumps({**ADVERTISING_CONFIG, 'rope_theta': 10000})):
            with self.subTest(content=content), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                model, advertising = root/'model', root/'advertising'
                model.mkdir(); advertising.mkdir()
                (model/'config.json').write_text(json.dumps(TEXT_CONFIG))
                (advertising/'config.json').write_text(content)
                (root/'text-ai.json').write_text(json.dumps({'enabled': True,
                    'modelPath': str(model), 'advertisingModelPath': str(advertising)}))
                result = inspect_ai(root)
                profile = result['text']['advertising']
                self.assertEqual(result['text']['model'], BASE_MODEL)
                self.assertTrue(profile['separateProfile'])
                self.assertTrue(profile['modelConfigPresent'])
                self.assertFalse(profile['modelSupported'])
                self.assertIsNone(profile['model'])
                self.assertFalse(profile['runtimePresent'])
                self.assertTrue(result['warnings'])
                self.assertIn('Рекламный ИИ: ВКЛЮЧЕНО · модель не определена', russian_status(result))

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
