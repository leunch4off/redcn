import base64
import hashlib
import io
import os
from pathlib import Path
import stat
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'cli'))
import local_photo_ai
import photo_ai_settings
import photo_store
import vision_photo
from PIL import Image

URL = 'https://images.cdn-cian.ru/images/adapter-fixture.jpg'


class PhotoAIAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.environment = patch.dict(os.environ, {'REDCN_VISION_MODEL': '', 'REDCN_VISION_ENDPOINT': ''})
        self.environment.start()
        buffer = io.BytesIO()
        with Image.new('RGB', (96, 64), '#dedede') as image:
            image.save(buffer, 'PNG')
        self.content = buffer.getvalue()
        self.original = photo_store.attach_photo(self.directory, URL, 'data:image/png;base64,' + base64.b64encode(self.content).decode())
        self.digest = self.original['id']
        photo_ai_settings.save_config(self.directory, {'enabled': True, 'endpoint': 'http://127.0.0.1:11435'})

    def tearDown(self):
        self.environment.stop()
        self.temp.cleanup()

    def success(self, content, mime, *, model, **kwargs):
        digest = hashlib.sha256(content).hexdigest()
        normalized = local_photo_ai.normalize_inventory({'objects': [{'id': 'oven', 'visible': True,
            'confidence': .98, 'count': 1, 'countExact': True,
            'evidence': 'Духовой шкаф со стеклянной дверцей и ручкой'}], 'unknownIds': []}, digest, model)
        return {'version': 1, 'status': 'ok', 'provider': 'ollama', 'model': model,
            'engine': 'Ollama fixture', 'photoId': digest, 'originalId': digest, **normalized}

    def test_provider_success_preserves_original_bytes_hash_mime_evidence_and_private_cache(self):
        cancel = threading.Event()
        deadline = time.monotonic() + 10
        with patch.object(local_photo_ai, 'analyze_photo', side_effect=self.success) as provider, \
                patch.object(vision_photo, 'native_classify') as native:
            first = photo_store.analyze_photo(self.directory, self.digest, cancel=cancel, deadline=deadline)
            second = photo_store.analyze_photo(self.directory, self.digest)
        self.assertEqual(first['status'], 'review')
        self.assertEqual(first['provider'], 'ollama')
        self.assertEqual(first['originalId'], self.digest)
        self.assertEqual(first['photoId'], self.digest)
        self.assertTrue(first['candidates'][0]['autoEligible'])
        self.assertEqual(first['candidates'][0]['evidence'][0]['photoId'], self.digest)
        self.assertEqual(first['sourceUrl'], URL)
        self.assertFalse(first['cached']); self.assertTrue(second['cached'])
        self.assertEqual(provider.call_count, 1); native.assert_not_called()
        call = provider.call_args
        self.assertEqual(call.args, (self.content, 'image/png'))
        self.assertIs(call.kwargs['cancel'], cancel)
        self.assertEqual(call.kwargs['deadline'], deadline)
        self.assertEqual(call.kwargs['endpoint'], 'http://127.0.0.1:11435')
        self.assertEqual(call.kwargs['timeout'], 120)
        caches = list((self.directory / 'vision').glob('*.json'))
        self.assertEqual(len(caches), 1)
        self.assertEqual(stat.S_IMODE(caches[0].stat().st_mode), 0o600)
        self.assertEqual(photo_store.asset(self.directory, self.digest)[0], self.content)

    def test_model_endpoint_and_auto_apply_changes_invalidate_cached_analysis(self):
        with patch.object(local_photo_ai, 'analyze_photo', side_effect=self.success) as provider:
            for updates in ({}, {'endpoint': 'http://127.0.0.1:11436'},
                    {'model': 'qwen3-vl:another-local-tag'}, {'autoApply': False}):
                if updates:
                    photo_ai_settings.save_config(self.directory, updates)
                photo_store.analyze_photo(self.directory, self.digest)
        self.assertEqual(provider.call_count, 4)

    def test_wrong_original_or_corrupt_cache_is_recomputed(self):
        import json
        with patch.object(local_photo_ai, 'analyze_photo', side_effect=self.success) as provider:
            photo_store.analyze_photo(self.directory, self.digest)
            path = next((self.directory / 'vision').glob('*.json'))
            cached = json.loads(path.read_text()); cached['originalId'] = 'b' * 64
            path.write_text(json.dumps(cached))
            self.assertFalse(photo_store.analyze_photo(self.directory, self.digest)['cached'])
            path.write_text('invalid JSON')
            self.assertFalse(photo_store.analyze_photo(self.directory, self.digest)['cached'])
        self.assertEqual(provider.call_count, 3)

    def test_failed_model_and_failed_native_are_not_cached_and_never_auto_apply(self):
        failure = {'status': 'unavailable', 'message': 'Local model unavailable'}
        with patch.object(local_photo_ai, 'analyze_photo', return_value=failure) as provider, \
                patch.object(vision_photo, 'native_classify', side_effect=ValueError('Native unavailable')):
            for _ in range(2):
                result = photo_store.analyze_photo(self.directory, self.digest)
                self.assertEqual(result['status'], 'unavailable')
                self.assertFalse(result['autoApply'])
                self.assertEqual(result['candidates'], [])
                self.assertEqual(result['fallbackStatus'], 'unavailable')
        self.assertEqual(provider.call_count, 2)
        self.assertEqual(list((self.directory / 'vision').glob('*.json')), [])

    def test_apple_fallback_is_review_only_and_not_cached_as_model_success(self):
        failure = {'status': 'invalid', 'message': 'Model output invalid'}
        candidates = vision_photo.classifier_candidates([{'label': 'oven', 'confidence': .99, 'region': [0, 0, 1, 1]}])
        with patch.object(local_photo_ai, 'analyze_photo', return_value=failure) as provider, \
                patch.object(vision_photo, 'native_classify', return_value=(candidates, 'Apple Vision fixture')):
            for _ in range(2):
                result = photo_store.analyze_photo(self.directory, self.digest)
                self.assertEqual(result['provider'], 'apple-vision')
                self.assertEqual(result['status'], 'review')
                self.assertFalse(result['autoApply'])
                self.assertTrue(result['fallback'])
                self.assertTrue(all(not row.get('autoEligible') and row['count'] is None for row in result['candidates']))
        self.assertEqual(provider.call_count, 2)
        self.assertEqual(list((self.directory / 'vision').glob('*.json')), [])

    def test_disabled_config_uses_native_without_sending_to_provider(self):
        photo_ai_settings.save_config(self.directory, {'enabled': False})
        with patch.object(local_photo_ai, 'analyze_photo') as provider, \
                patch.object(vision_photo, 'native_classify', return_value=([], 'Native fixture')):
            result = photo_store.analyze_photo(self.directory, self.digest)
        provider.assert_not_called()
        self.assertEqual(result['provider'], 'apple-vision')
        self.assertFalse(result['autoApply'])

    def test_pre_cancel_rejects_even_a_cached_success_without_model_or_native_work(self):
        cancel = threading.Event()
        with patch.object(local_photo_ai, 'analyze_photo', side_effect=self.success) as provider, \
                patch.object(vision_photo, 'native_classify') as native:
            photo_store.analyze_photo(self.directory, self.digest)
            cancel.set()
            with self.assertRaisesRegex(ValueError, 'отмен'):
                photo_store.analyze_photo(self.directory, self.digest, cancel=cancel)
        self.assertEqual(provider.call_count, 1); native.assert_not_called()

    def test_expired_deadline_rejects_cached_work_and_adapter_does_not_launch_native(self):
        with patch.object(local_photo_ai, 'analyze_photo', side_effect=self.success):
            photo_store.analyze_photo(self.directory, self.digest)
            with self.assertRaisesRegex(ValueError, 'истек|лимит|время'):
                photo_store.analyze_photo(self.directory, self.digest, deadline=time.monotonic()-1)
        photo_ai_settings.save_config(self.directory, {'enabled': False})
        with Image.open(io.BytesIO(self.content)) as image, patch.object(vision_photo, 'native_classify') as native:
            with self.assertRaisesRegex(ValueError, 'истек|лимит|время'):
                vision_photo.analyze_image(image, self.directory, deadline=time.monotonic()-1)
        native.assert_not_called()

    def test_adapter_honors_pre_cancel_when_disabled_and_cancel_race_after_provider(self):
        cancel = threading.Event(); cancel.set()
        photo_ai_settings.save_config(self.directory, {'enabled': False})
        with Image.open(io.BytesIO(self.content)) as image, patch.object(vision_photo, 'native_classify') as native:
            with self.assertRaisesRegex(ValueError, 'отмен'):
                vision_photo.analyze_image(image, self.directory, cancel=cancel)
        native.assert_not_called()
        cancel.clear()
        photo_ai_settings.save_config(self.directory, {'enabled': True})
        def completes_after_cancel(*args, **kwargs):
            cancel.set(); return self.success(*args, **kwargs)
        with patch.object(local_photo_ai, 'analyze_photo', side_effect=completes_after_cancel):
            with self.assertRaisesRegex(ValueError, 'отмен'):
                photo_store.analyze_photo(self.directory, self.digest, cancel=cancel)

    def test_native_compilation_and_execution_timeouts_respect_remaining_deadline(self):
        compile_result = SimpleNamespace(returncode=0, stdout='')
        classify_result = SimpleNamespace(returncode=0, stdout='[]')
        actual_exists = Path.exists
        def exists(path):
            return True if str(path) == '/usr/bin/swiftc' else actual_exists(path)
        with Image.open(io.BytesIO(self.content)) as image, patch.object(vision_photo.sys, 'platform', 'darwin'), \
                patch.object(Path, 'exists', exists), \
                patch.object(vision_photo.subprocess, 'run', side_effect=[compile_result, classify_result]) as process:
            result = vision_photo.native_classify(image, self.directory, deadline=time.monotonic()+2)
        self.assertEqual(result[0], [])
        self.assertEqual(process.call_count, 2)
        self.assertTrue(all(0 < call.kwargs['timeout'] <= 2 for call in process.call_args_list))

    def test_native_compile_lock_wait_obeys_deadline_without_launching_a_process(self):
        lock = threading.Lock(); lock.acquire()
        done = threading.Event()
        failures = []
        actual_exists = Path.exists
        def exists(path):
            return True if str(path) == '/usr/bin/swiftc' else actual_exists(path)
        with Image.open(io.BytesIO(self.content)) as image, \
                patch.object(vision_photo.sys, 'platform', 'darwin'), \
                patch.object(Path, 'exists', exists), \
                patch.object(vision_photo, '_compile_lock', lock), \
                patch.object(vision_photo.subprocess, 'run', side_effect=ValueError('Unexpected process launch')) as process:
            def work():
                try:
                    vision_photo.native_classify(image, self.directory, deadline=time.monotonic()+.05)
                except ValueError as error:
                    failures.append(str(error))
                finally:
                    done.set()
            thread = threading.Thread(target=work, daemon=True)
            thread.start()
            try:
                finished_while_locked = done.wait(.25)
            finally:
                lock.release()
                thread.join(timeout=1)
            self.assertTrue(finished_while_locked, 'The compile lock ignored this request deadline')
            process.assert_not_called()
        self.assertTrue(failures)
        self.assertRegex(failures[0], 'истек|лимит|время')


if __name__ == '__main__':
    unittest.main()
