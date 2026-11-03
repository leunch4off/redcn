import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'cli'))
import photo_ai_settings as settings


class PhotoAISettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.environment = patch.dict(os.environ, {'REDCN_VISION_MODEL': '', 'REDCN_VISION_ENDPOINT': ''})
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.temp.cleanup()

    def test_default_is_disabled_and_returned_configuration_is_not_shared(self):
        first = settings.load_config(self.directory)
        self.assertFalse(first['enabled'])
        first['enabled'] = True
        self.assertFalse(settings.load_config(self.directory)['enabled'])
        self.assertFalse((self.directory / 'photo-ai.json').exists())

    def test_loopback_ipv4_ipv6_with_explicit_port_are_accepted_and_normalized(self):
        for endpoint in ('http://127.0.0.1:11434', 'http://127.0.0.1:11435/', 'http://[::1]:65535/'):
            value = settings.validate_config({**settings.DEFAULTS, 'endpoint': endpoint})
            self.assertEqual(value['endpoint'], endpoint.rstrip('/'))

    def test_remote_urls_proxy_targets_credentials_and_ambiguous_origins_are_rejected(self):
        invalid = ['https://127.0.0.1:11434', 'http://localhost:11434', 'http://192.168.1.1:11434',
            'http://0.0.0.0:11434', 'http://127.0.0.1.evil.test:11434',
            'http://user:secret@127.0.0.1:11434', 'http://@127.0.0.1:11434',
            'http://127.0.0.1:11434/api/chat', 'http://127.0.0.1:11434?url=https://evil.test',
            'http://127.0.0.1:11434#fragment', 'http://127.0.0.1', 'http://127.0.0.1:0',
            'http://127.0.0.1:65536', 'http://127.0.0.1\n:11434', 'file:///tmp/model', None, 3]
        for endpoint in invalid:
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                settings.validate_config({**settings.DEFAULTS, 'endpoint': endpoint})

    def test_model_must_be_explicit_local_identifier_and_cloud_or_url_is_rejected(self):
        for model in ('', 'qwen model', 'qwen3-vl:cloud', 'QWEN3-VL:Cloud',
                'http://evil.test/model', 'https://127.0.0.1/model', 'qwen\n:model',
                '-option', 'x' * 129, None, True):
            with self.subTest(model=model), self.assertRaises(ValueError):
                settings.validate_config({**settings.DEFAULTS, 'model': model})
        self.assertEqual(settings.validate_config({**settings.DEFAULTS, 'model': settings.DEFAULT_MODEL})['model'], settings.DEFAULT_MODEL)

    def test_types_bounds_and_unknown_secret_fields_are_rejected(self):
        cases = [{'enabled': 1}, {'enabled': 'true'}, {'autoApply': 0}, {'autoApply': 'false'},
            {'maxSeconds': 59}, {'maxSeconds': 1801}, {'maxSeconds': 60.0},
            {'maxSeconds': True}, {'maxSeconds': float('nan')}, {'apiKey': 'private'}]
        for updates in cases:
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                settings.validate_config({**settings.DEFAULTS, **updates})
        for seconds in (60, 1800):
            self.assertEqual(settings.validate_config({**settings.DEFAULTS, 'maxSeconds': seconds})['maxSeconds'], seconds)
        for value in ([], None, {}, {'enabled': True}):
            with self.assertRaises(ValueError):
                settings.validate_config(value)

    def test_legacy_environment_only_enables_valid_local_model_without_saved_file(self):
        with patch.dict(os.environ, {'REDCN_VISION_MODEL': settings.DEFAULT_MODEL,
                'REDCN_VISION_ENDPOINT': 'http://127.0.0.1:11435'}):
            config = settings.load_config(self.directory)
            self.assertTrue(config['enabled'])
            self.assertEqual(config['endpoint'], 'http://127.0.0.1:11435')
            settings.save_config(self.directory, {'enabled': False})
            self.assertFalse(settings.load_config(self.directory)['enabled'])
        for model, endpoint in (('qwen:cloud', 'http://127.0.0.1:11435'),
                (settings.DEFAULT_MODEL, 'https://remote.test')):
            with patch.dict(os.environ, {'REDCN_VISION_MODEL': model, 'REDCN_VISION_ENDPOINT': endpoint}):
                (self.directory / 'photo-ai.json').unlink(missing_ok=True)
                self.assertFalse(settings.load_config(self.directory)['enabled'])

    def test_private_atomic_save_merges_valid_updates_and_creates_no_secrets(self):
        nested = self.directory / 'new-private-settings'
        settings.save_config(nested, {'enabled': True, 'endpoint': 'http://127.0.0.1:11435'})
        config = settings.save_config(nested, {'autoApply': False})
        path = nested / 'photo-ai.json'
        self.assertTrue(config['enabled'])
        self.assertFalse(config['autoApply'])
        self.assertEqual(json.loads(path.read_text()), config)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(nested.stat().st_mode), 0o700)
        self.assertEqual([row.name for row in nested.iterdir()], ['photo-ai.json'])

    def test_interrupted_save_preserves_previous_file_and_removes_temporary_file(self):
        settings.save_config(self.directory, {'enabled': True})
        path = self.directory / 'photo-ai.json'
        previous = path.read_bytes()
        with patch.object(settings.json, 'dump', side_effect=OSError('simulated disk failure')):
            with self.assertRaises(OSError):
                settings.save_config(self.directory, {'enabled': False})
        self.assertEqual(path.read_bytes(), previous)
        self.assertEqual([row.name for row in self.directory.iterdir()], ['photo-ai.json'])

    def test_symlink_is_never_followed_even_when_legacy_environment_is_enabled(self):
        target = self.directory / 'target.json'
        target.write_text(json.dumps({**settings.DEFAULTS, 'enabled': True}))
        path = self.directory / 'photo-ai.json'
        path.symlink_to(target)
        previous = target.read_bytes()
        with patch.dict(os.environ, {'REDCN_VISION_MODEL': settings.DEFAULT_MODEL}):
            self.assertFalse(settings.load_config(self.directory)['enabled'])
        with self.assertRaises(ValueError):
            settings.save_config(self.directory, {'enabled': True})
        self.assertTrue(path.is_symlink())
        self.assertEqual(target.read_bytes(), previous)

    def test_bad_oversized_and_non_utf8_files_fail_closed_without_legacy_override(self):
        path = self.directory / 'photo-ai.json'
        for data in (b'not JSON', b'\xff\xfe', b'[]', b'x' * 16385,
                json.dumps({**settings.DEFAULTS, 'endpoint': 'http://remote.test:11434'}).encode()):
            path.write_bytes(data)
            with patch.dict(os.environ, {'REDCN_VISION_MODEL': settings.DEFAULT_MODEL}):
                self.assertFalse(settings.load_config(self.directory)['enabled'])
        for updates in ({}, [], None, {'apiKey': 'secret'}):
            with self.assertRaises(ValueError):
                settings.save_config(self.directory, updates)


if __name__ == '__main__':
    unittest.main()
