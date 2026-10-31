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

    def test_invalid_style_and_pin_never_fetch_tiles(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(listing_map, '_opener') as opener:
            for style in ('unknown', None, [], '#ffffff', True):
                with self.subTest(style=style), self.assertRaisesRegex(ValueError, 'Стиль карты'):
                    listing_map.map_image(temp, 55.75, 37.62, style=style)
            for pin in ('red', '#fff', '#12345678', 'url(https://example.test)', True, {}):
                with self.subTest(pin=pin), self.assertRaisesRegex(ValueError, 'Цвет точки'):
                    listing_map.map_image(temp, 55.75, 37.62, style='navy', pin_color=pin)
            opener.assert_not_called()


@unittest.skipUnless(Image, 'Pillow not installed')
class MapRasterTests(unittest.TestCase):
    def test_palette_preserves_source_pixels_and_distinguishes_real_land_features(self):
        source = Image.new('RGB', (6, 1))
        source.putdata([(242,239,233),(217,208,201),(255,255,255),
                        (170,211,223),(173,209,158),(60,60,60)])
        before = source.tobytes()
        navy = listing_map._theme_map(source, 'navy')
        gold = listing_map._theme_map(source, 'gold')
        self.assertEqual(source.tobytes(), before)
        self.assertEqual(navy.size, source.size)
        self.assertEqual(len(navy.getcolors()), 6)
        self.assertEqual(len(gold.getcolors()), 6)
        self.assertEqual(navy.getpixel((0,0)), (8,31,61))
        self.assertEqual(navy.getpixel((1,0)), (24,59,98))
        self.assertGreater(sum(navy.getpixel((2,0))), sum(navy.getpixel((0,0))))
        self.assertNotEqual(navy.tobytes(), gold.tobytes())
        self.assertIs(listing_map._theme_map(source, 'classic'), source)

    def test_theme_changes_reuse_tiles_coordinates_and_never_mutate_source_cache(self):
        import hashlib
        content = tile_bytes((242,239,233))
        headers = Message()
        headers['Content-Type'] = 'image/png'
        headers['Cache-Control'] = 'max-age=3600'
        class Response:
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def read(self,limit): return content
        response = Response();response.headers = headers
        class Opener:
            def __init__(self): self.requests=[]
            def open(self,request,timeout): self.requests.append(request.full_url);return response
        opener=Opener()
        def image(result):return Image.open(io.BytesIO(base64.b64decode(result['dataUrl'].split(',')[1])))
        with tempfile.TemporaryDirectory() as temp, patch.object(listing_map,'_opener',return_value=opener):
            classic=listing_map.map_image(temp,55.75,37.62)
            cached={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(temp).rglob('*.png')}
            requests=len(opener.requests)
            navy=listing_map.map_image(temp,55.75,37.62,style='navy')
            gold=listing_map.map_image(temp,55.75,37.62,style='gold')
            custom=listing_map.map_image(temp,55.75,37.62,style='navy',pin_color='#23aabc')
            self.assertEqual(len(opener.requests),requests)
            self.assertLessEqual(requests,9)
            self.assertEqual(cached,{p:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(temp).rglob('*.png')})
        for result in (navy,gold,custom):
            self.assertEqual(result['sourceUrl'],classic['sourceUrl'])
            self.assertEqual((result['latitude'],result['longitude'],result['zoom']),(55.75,37.62,15))
            self.assertEqual(result['labelLanguage'],'source')
            self.assertEqual(result['attributionZh'],'地图数据 © OpenStreetMap 贡献者')
            self.assertIn('OpenStreetMap',result['attribution'])
            self.assertEqual(image(result).size,(768,512))
        self.assertEqual(image(navy).getpixel((397,256)),(243,77,112))
        self.assertEqual(image(gold).getpixel((397,256)),(183,150,101))
        self.assertEqual(image(custom).getpixel((397,256)),(35,170,188))
        self.assertEqual(image(navy).getpixel((20,485)),(6,23,47))
        self.assertNotEqual(image(navy).crop((10,488,500,510)).getextrema(),((6,6),(23,23),(47,47)))

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
