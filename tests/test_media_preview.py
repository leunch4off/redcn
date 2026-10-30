import base64
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'cli'))
import media_preview
import photo_store
from PIL import Image


class MediaPreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def attach(self, size, mode='RGB'):
        image = Image.new(mode, size, (120, 160, 180, 0) if mode == 'RGBA' else '#91aab0')
        output = io.BytesIO()
        image.save(output, 'PNG')
        image.close()
        raw = output.getvalue()
        item = photo_store.attach_photo(self.directory, 'https://images.cdn-cian.ru/images/preview-test.jpg', 'data:image/png;base64,' + base64.b64encode(raw).decode())
        return item, raw

    def test_landscape_portrait_and_small_photo_preserve_proportions_and_originals(self):
        for size, expected in (((2400, 1600), (480, 320)), ((1000, 3000), (120, 360)), ((80, 100), (80, 100))):
            item, original = self.attach(size)
            content, mime, meta = media_preview.preview(self.directory, item['id'])
            self.assertEqual(mime, 'image/jpeg')
            self.assertLessEqual(len(content), media_preview.MAX_PREVIEW_BYTES)
            with Image.open(io.BytesIO(content)) as image:
                self.assertEqual(image.size, expected)
            self.assertEqual((meta['width'], meta['height']), expected)
            self.assertEqual(photo_store.asset(self.directory, item['id'])[0], original)

    def test_cached_cover_does_not_reread_or_decode_original(self):
        item, _ = self.attach((2500, 2000))
        first = media_preview.preview(self.directory, item['id'])
        with patch.object(photo_store, 'asset', side_effect=AssertionError('Do not rehash full originals')), patch.object(photo_store, 'open_photo', side_effect=AssertionError('Do not decode full originals')):
            self.assertEqual(media_preview.preview(self.directory, item['id']), first)

    def test_modified_original_cannot_reuse_old_cover_and_corrupted_derivative_rebuilds(self):
        item, raw = self.attach((1200, 900))
        first = media_preview.preview(self.directory, item['id'])
        cached = next((self.directory / 'media-previews').glob('*.jpg'))
        cached.write_bytes(b'broken preview')
        self.assertEqual(media_preview.preview(self.directory, item['id']), first)
        (self.directory / 'media' / item['id']).write_bytes(b'broken original')
        with self.assertRaisesRegex(ValueError, 'повреждён'):
            media_preview.preview(self.directory, item['id'])

    def test_transparent_png_is_rendered_over_white(self):
        item, _ = self.attach((800, 600), 'RGBA')
        content, _, _ = media_preview.preview(self.directory, item['id'])
        with Image.open(io.BytesIO(content)) as image:
            self.assertEqual(image.getpixel((100, 100)), (255, 255, 255))

    def test_preview_rejects_invalid_ids_width_and_symlinks(self):
        for digest in ('../secret', 'a' * 63, None):
            with self.assertRaises(ValueError):
                media_preview.preview(self.directory, digest)
        item, _ = self.attach((1000, 1000))
        for width in (True, 0, 641, '480'):
            with self.assertRaises(ValueError):
                media_preview.preview(self.directory, item['id'], width)
        (self.directory / 'media-previews').symlink_to(self.directory / 'media', target_is_directory=True)
        with self.assertRaises(ValueError):
            media_preview.preview(self.directory, item['id'])


if __name__ == '__main__':
    unittest.main()
