import importlib.util
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[1]/'scripts/benchmark-phone-designer.py'
SPEC = importlib.util.spec_from_file_location('phone_designer_benchmark', PATH)
benchmark = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(benchmark)


class PhoneDesignerBenchmarkTests(unittest.TestCase):
    def test_url_cannot_include_pairing_fragment_credentials_or_private_path(self):
        for value in ('http://example.com', 'https://user:password@example.com',
                'https://example.com/phone/pair#1234', 'https://example.com/api/phone/cards',
                'https://example.com/phone/?card=private'):
            with self.assertRaises(ValueError):
                benchmark.phone_url(value)
        self.assertEqual(benchmark.phone_url('https://example.com'), 'https://example.com/phone/')

    def test_every_api_write_is_blocked_and_unknown_private_get_is_synthetic_404(self):
        data = benchmark.fixtures(3)
        for path in ('/api/phone/pair', '/api/phone/import', '/api/phone/design', '/api/local/save'):
            self.assertEqual(benchmark.fixture_response('POST', path, data)[0], 403)
        self.assertEqual(benchmark.fixture_response('GET', '/api/phone/contracts', data)[0], 404)

    def test_media_and_card_are_generated_in_memory_and_include_no_real_source(self):
        data = benchmark.fixtures(19)
        status, mime, card = benchmark.fixture_response('GET', '/api/phone/cards/'+benchmark.CARD_ID, data)
        self.assertEqual(status, 200)
        self.assertEqual(len(card['media']), 19)
        self.assertTrue(all(row['sourceUrl'].startswith('https://fixture.invalid/') for row in card['media']))
        for row in card['media']:
            status, mime, content = benchmark.fixture_response('GET', row['url'], data)
            self.assertEqual((status, mime), (200, 'image/png'))
            self.assertTrue(content.startswith(b'\x89PNG\r\n\x1a\n'))

    def test_provider_warning_header_is_empty_by_default(self):
        for url in ('https://free-quail-5273.a.tuna.free/phone/',
                'https://brave-otter-4821.ru.tuna.am', 'https://example.com'):
            self.assertEqual(benchmark.provider_headers(url), {})
            self.assertEqual(benchmark.provider_headers(url, False), {})
        self.assertFalse(benchmark.arguments(['--url', 'https://example.com']).skip_tuna_warning)

    def test_opt_in_provider_warning_header_is_only_for_validated_tuna_origin(self):
        for url in ('https://free-quail-5273.a.tuna.free/phone/',
                'https://brave-otter-4821.ru.tuna.am', 'https://brave-otter-4821.ru.tuna.am:443/phone/'):
            self.assertEqual(benchmark.provider_headers(url, True), {'tuna-skip-browser-warning': '1'})
        self.assertTrue(benchmark.arguments(['--url', 'https://free-quail-5273.a.tuna.free',
            '--skip-tuna-warning']).skip_tuna_warning)

    def test_provider_warning_opt_in_rejects_other_hosts_and_nonpublic_urls(self):
        for url in ('https://example.com', 'https://tuna.am.evil.test',
                'https://eviltuna.free', 'https://tuna.free', 'https://tuna.am',
                'https://a.tuna.free:8443', 'http://a.tuna.free',
                'https://a.tuna.free/api/phone/state', 'https://a.tuna.free/phone/#pairing',
                'https://user:password@a.tuna.free'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                benchmark.provider_headers(url, True)


if __name__ == '__main__':
    unittest.main()
