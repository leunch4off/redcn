import base64
from email.message import Message
import io
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'cli'))
import listing_map

try:
    from PIL import Image
except ImportError:
    Image = None


def tile_bytes(color=(130, 180, 140)):
    output = io.BytesIO()
    Image.new('RGB', (256, 256), color).save(output, 'PNG')
    return output.getvalue()


class MapMathTests(unittest.TestCase):
    def test_known_mercator_center_and_moscow(self):
        self.assertEqual(listing_map.project(0, 0, 12), (524288.0, 524288.0))
        x, y = listing_map.project(55.75, 37.62, 15)
        self.assertTrue(5_000_000 < x < 5_100_000)
        self.assertTrue(2_600_000 < y < 2_700_000)

    def test_coordinate_validation(self):
        self.assertEqual(listing_map.coordinates('55.75', '37.62'), (55.75, 37.62, 15))
        for bad in (True, None, '', 'NaN', float('inf'), 90, -90, [], {}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                listing_map.coordinates(bad, 37.6)
        for bad in (True, 12.5, '15', 11, 18):
            with self.subTest(zoom=bad), self.assertRaises(ValueError):
                listing_map.coordinates(55.75, 37.6, bad)
        with self.assertRaises(ValueError):
            listing_map.coordinates(55, 181)

    def test_http_cache_ttl_honors_headers(self):
        self.assertEqual(listing_map._expiry({}, 100), 100 + 604800)
        self.assertEqual(listing_map._expiry({'Cache-Control': 'public, max-age=3600', 'Age': '60'}, 100), 3640)
        self.assertEqual(listing_map._expiry({'Cache-Control': 'no-store'}, 100), 100)
        self.assertEqual(listing_map._expiry({'Cache-Control': 'no-cache, max-age=3600'}, 100), 100)


@unittest.skipUnless(Image, 'Pillow not installed')
class MapRasterTests(unittest.TestCase):
    def test_real_coordinate_viewport_bounded_marker_and_attribution(self):
        calls = []
        def tile(directory, zoom, x, y, opener):
            calls.append((zoom, x, y))
            return tile_bytes()
        with tempfile.TemporaryDirectory() as temp, patch.object(listing_map, '_tile', side_effect=tile):
            result = listing_map.map_image(temp, 55.75, 37.62)
        self.assertLessEqual(len(calls), 9)
        self.assertEqual(len(calls), len(set(calls)))
        self.assertIn('OpenStreetMap', result['attribution'])
        self.assertIn('mlat=55.7500000', result['sourceUrl'])
        image = Image.open(io.BytesIO(base64.b64decode(result['dataUrl'].split(',')[1])))
        self.assertEqual(image.size, (768, 512))
        self.assertEqual(image.getpixel((384, 256)), (255, 255, 255))
        self.assertEqual(image.getpixel((397, 256)), (189, 79, 53))
        self.assertEqual(image.getpixel((20, 485)), (255, 255, 255))
        self.assertNotEqual(image.crop((10, 488, 500, 510)).getextrema(), ((255, 255),) * 3)

    def test_date_line_wrap_and_poles_never_fetch_invalid_tiles(self):
        for lat, lon in ((0, 180), (0, -180), (85.0511287798066, 0), (-85.0511287798066, 0)):
            calls = []
            def tile(directory, zoom, x, y, opener):
                calls.append((x, y))
                return tile_bytes()
            with tempfile.TemporaryDirectory() as temp, patch.object(listing_map, '_tile', side_effect=tile):
                listing_map.map_image(temp, lat, lon, 12)
            self.assertLessEqual(len(calls), 9)
            self.assertTrue(all(0 <= x < 4096 and 0 <= y < 4096 for x, y in calls))

    def test_fresh_cache_then_conditional_revalidation(self):
        content = tile_bytes()
        headers = Message()
        headers['Content-Type'] = 'image/png'
        headers['Cache-Control'] = 'max-age=3600'
        headers['ETag'] = '"tile-v1"'
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, limit): return content
        response = Response()
        response.headers = headers
        class Opener:
            def __init__(self): self.requests = []
            def open(self, request, timeout):
                self.requests.append(request)
                if len(self.requests) == 1: return response
                unchanged = Message()
                unchanged['Cache-Control'] = 'max-age=7200'
                raise urllib.error.HTTPError(request.full_url, 304, 'Not Modified', unchanged, None)
        opener = Opener()
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(listing_map._tile(temp, 15, 1, 2, opener), content)
            self.assertEqual(listing_map._tile(temp, 15, 1, 2, opener), content)
            self.assertEqual(len(opener.requests), 1)
            meta = Path(temp) / 'map-tiles/15/1/2.json'
            saved = json.loads(meta.read_text())
            saved['expires'] = time.time() - 1
            meta.write_text(json.dumps(saved))
            self.assertEqual(listing_map._tile(temp, 15, 1, 2, opener), content)
            self.assertEqual(len(opener.requests), 2)
            self.assertEqual(opener.requests[1].get_header('If-none-match'), '"tile-v1"')
            self.assertIn('MOST-Redcn', opener.requests[0].get_header('User-agent'))
            self.assertIsNone(opener.requests[0].get_header('Cache-control'))
            self.assertGreater(json.loads(meta.read_text())['expires'], time.time() + 7000)

    def test_network_error_has_no_fabricated_fallback_map(self):
        class Opener:
            def open(self, *args, **kwargs):
                raise urllib.error.URLError('offline')
        with tempfile.TemporaryDirectory() as temp, patch.object(listing_map, '_opener', return_value=Opener()):
            with self.assertRaisesRegex(ValueError, 'интернет'):
                listing_map.map_image(temp, 55.75, 37.62)


if __name__ == '__main__':
    unittest.main()
