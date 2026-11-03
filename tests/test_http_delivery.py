import gzip
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'cli'))
import http_delivery


class HttpDeliveryTests(unittest.TestCase):
    def setUp(self):
        http_delivery._cache.clear()

    def test_encoding_negotiation_respects_q_zero_and_exact_tokens(self):
        for value in ('gzip', 'br, gzip', 'GZIP; q=0.8', '*;q=1', 'br;q=0, gzip;q=1.000'):
            self.assertTrue(http_delivery.accepts_gzip(value), value)
        for value in ('', None, 'no-gzip', 'gzip;q=0', 'gzip;q=0, *;q=1', 'gzip;q=2', 'gzip;q=oops', 'gzip;q=-1', 'br'):
            self.assertFalse(http_delivery.accepts_gzip(value), value)

    def test_large_unicode_html_and_json_keep_exact_bytes(self):
        body = ('中文 · Москва <h1>Карточка</h1>' * 14000).encode()
        for mime in ('text/html; charset=utf-8', 'application/json', 'text/javascript', 'application/manifest+json'):
            compressed, headers = http_delivery.response_bytes(body, mime, 'br,gzip')
            self.assertEqual(gzip.decompress(compressed), body)
            self.assertLess(len(compressed), len(body) // 10)
            self.assertEqual(headers, {'Vary': 'Accept-Encoding', 'Content-Encoding': 'gzip'})
        self.assertEqual(http_delivery._cache.info()['entries'], 1)

    def test_unaccepted_small_and_binary_responses_remain_original_bytes(self):
        body = b'hello' * 1000
        for encoding in ('', 'gzip;q=0', 'no-gzip'):
            output, headers = http_delivery.response_bytes(body, 'text/html', encoding)
            self.assertEqual(output, body)
            self.assertNotIn('Content-Encoding', headers)
        for mime in ('image/jpeg', 'image/png', 'application/pdf', 'application/zip', 'application/octet-stream'):
            self.assertEqual(http_delivery.response_bytes(body, mime, 'gzip'), (body, {}))
        self.assertEqual(http_delivery.response_bytes(b'hello', 'text/html', 'gzip'), (b'hello', {'Vary': 'Accept-Encoding'}))
        random = os.urandom(4096)
        self.assertEqual(http_delivery.response_bytes(random, 'text/plain', 'gzip')[0], random)

    def test_cache_reuses_compression_and_evicts_old_responses(self):
        cache = http_delivery.ResponseCompressionCache(max_entries=2, max_bytes=400)
        real_compress = gzip.compress
        with patch.object(http_delivery.gzip, 'compress', wraps=real_compress) as compress:
            first = cache.compress(b'a' * 5000)
            self.assertEqual(cache.compress(b'a' * 5000), first)
            self.assertEqual(compress.call_count, 1)
            cache.compress(b'b' * 5000)
            cache.compress(b'c' * 5000)
            self.assertEqual(cache.info()['entries'], 2)
            self.assertLessEqual(cache.info()['bytes'], 400)
            cache.compress(b'a' * 5000)
            self.assertEqual(compress.call_count, 4)


if __name__ == '__main__':
    unittest.main()
