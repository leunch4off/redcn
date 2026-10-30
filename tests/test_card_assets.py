import base64
import io
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'cli'))
import card_assets
import local_server

HTML = '<!doctype html><html lang="ru"><head><meta charset="utf-8"><style>@page{size:A4;margin:16mm}body{font-family:Arial,sans-serif}h1{color:#c0492f}</style></head><body><main><h1>Москва · 莫斯科</h1><p>80 000 ₽</p><a href="https://yandex.ru/maps/">Карта</a></main></body></html>'


class CardAssetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.result_id = local_server.save_result(self.directory, {'fields': {'price': '80000'}, 'titles': ['公寓'], 'body': '正文', 'tags': '#莫斯科'})

    def tearDown(self):
        self.temp.cleanup()

    def attachment(self, content=b'%PDF-1.4\nprivate-contract\n%%EOF', **values):
        return card_assets.add_attachment(self.directory, self.result_id, {'name': 'Договор.pdf', 'category': 'contract', 'data': base64.b64encode(content).decode(), **values})

    def png(self):
        from PIL import Image
        stream = io.BytesIO()
        Image.new('RGB', (120, 160), '#b33224').save(stream, 'PNG')
        return stream.getvalue()

    def test_template_survives_reload_and_does_not_change_card(self):
        template = {'version': 1, 'preset': 'editorial', 'accent': '#d05031', 'paper': 'A4', 'blocks': [{'id': 'title', 'enabled': True}], 'intro': {'headline': '新家'}, 'outro': {'showMap': True}}
        before = local_server.read_result(self.directory, self.result_id)
        self.assertEqual(card_assets.load_design(self.directory)['template'], None)
        self.assertEqual(card_assets.save_design(self.directory, template)['template'], template)
        self.assertEqual(card_assets.load_design(str(self.directory))['template'], template)
        self.assertEqual(local_server.read_result(self.directory, self.result_id), before)
        for bad in [None, {'version': 2}, {'script': '<script>'}, {'intro': []}, {'blocks': [{'id': 'a', 'enabled': True}, {'id': 'a', 'enabled': False}]}]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                card_assets.save_design(self.directory, bad)

    def test_status_and_notes_persist_separately_from_editable_result(self):
        self.assertEqual(card_assets.archive(self.directory, self.result_id)['status'], 'active')
        result = card_assets.update_archive(self.directory, self.result_id, {'status': 'rented', 'notes': 'Договор до 01.05'})
        self.assertEqual(result['status'], 'rented')
        card = local_server.read_result(self.directory, self.result_id)
        card['body'] = 'Новый текст'
        local_server.save_result(self.directory, card, self.result_id)
        self.assertEqual(card_assets.archive(self.directory, self.result_id)['notes'], 'Договор до 01.05')
        self.assertEqual(card_assets.archive(self.directory, self.result_id)['status'], 'rented')
        with self.assertRaises(ValueError):
            card_assets.update_archive(self.directory, self.result_id, {'status': 'published'})
        with self.assertRaises(ValueError):
            card_assets.update_archive(self.directory, self.result_id, {'notes': 'x' * 20001})

    def test_attachment_roundtrip_private_permissions_and_removal(self):
        content = b'%PDF-1.4\nprivate-contract\n%%EOF'
        row = self.attachment(content)['attachments'][0]
        self.assertEqual(row['category'], 'contract')
        self.assertEqual(row['bytes'], len(content))
        self.assertEqual(card_assets.attachment_asset(self.directory, self.result_id, row['id']), (content, 'application/pdf', 'Договор.pdf'))
        files = list((self.directory / 'card-assets').iterdir())
        self.assertTrue(all(path.stat().st_mode & 0o777 == 0o600 for path in files))
        self.assertEqual((self.directory / 'card-assets').stat().st_mode & 0o777, 0o700)
        self.assertEqual(card_assets.remove_attachment(self.directory, self.result_id, row['id'])['attachments'], [])
        with self.assertRaises(ValueError):
            card_assets.attachment_asset(self.directory, self.result_id, row['id'])

    def test_duplicate_attachment_content_remains_until_final_reference_removed(self):
        first = self.attachment()['attachments'][0]
        second = self.attachment(name='Копия.pdf')['attachments'][-1]
        card_assets.remove_attachment(self.directory, self.result_id, first['id'])
        self.assertTrue(card_assets.attachment_asset(self.directory, self.result_id, second['id'])[0].startswith(b'%PDF-'))
        card_assets.remove_attachment(self.directory, self.result_id, second['id'])
        self.assertEqual(len(list((self.directory / 'card-assets').glob('[a-f0-9]' * 64))), 0)

    def test_file_names_formats_categories_and_size_are_checked(self):
        for name in ['../secret.pdf', 'folder/a.pdf', 'a\\b.pdf', 'x\n.pdf', 'a".pdf', 'run.html', 'run.js']:
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.attachment(name=name)
        for values in [{'category': 'public'}, {'data': '!!!'}, {'data': base64.b64encode(b'not pdf').decode()}]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.attachment(**values)
        with patch.object(card_assets, 'MAX_ATTACHMENT', 8):
            with self.assertRaises(ValueError):
                self.attachment()
        self.attachment(b'v 1 2 3\nv 0 0 0\n', name='scan.obj', category='lidar')
        with self.assertRaises(ValueError):
            self.attachment(b'not-glb', name='scan.glb', category='lidar')

    def test_missing_card_or_cross_card_attachment_lookup_is_rejected(self):
        with self.assertRaises(ValueError):
            card_assets.archive(self.directory, 'a' * 32)
        with self.assertRaises(ValueError):
            card_assets.add_attachment(self.directory, 'a' * 32, {})
        row = self.attachment()['attachments'][0]
        other = local_server.save_result(self.directory, {'fields': {}, 'titles': ['other'], 'body': '', 'tags': ''})
        with self.assertRaises(ValueError):
            card_assets.attachment_asset(self.directory, other, row['id'])
        with self.assertRaises(ValueError):
            card_assets.remove_attachment(self.directory, other, row['id'])

    def test_symlink_storage_and_digest_tampering_are_rejected(self):
        row = self.attachment()['attachments'][0]
        with sqlite3.connect(self.directory / 'card-assets' / 'dossiers.sqlite3') as connection:
            digest = connection.execute('SELECT digest FROM attachments WHERE id=?', (row['id'],)).fetchone()[0]
        path = self.directory / 'card-assets' / digest
        path.write_bytes(b'changed')
        with self.assertRaises(ValueError):
            card_assets.attachment_asset(self.directory, self.result_id, row['id'])
        path.unlink()
        path.symlink_to(self.directory / 'listings.sqlite3')
        with self.assertRaises(ValueError):
            card_assets.attachment_asset(self.directory, self.result_id, row['id'])
        with self.assertRaises(ValueError):
            self.attachment()

    def test_static_html_export_preserves_bytes_and_excludes_confidential_attachments(self):
        self.attachment()
        result = card_assets.create_export(self.directory, self.result_id, HTML, 'html')
        content, mime, name = card_assets.export_asset(self.directory, result['id'])
        self.assertEqual(content, HTML.encode())
        self.assertEqual(mime, 'text/html; charset=utf-8')
        self.assertTrue(name.endswith('.html'))
        self.assertNotIn(b'private-contract', content)
        self.assertEqual(card_assets.list_exports(self.directory, self.result_id)[0]['id'], result['id'])
        self.assertEqual(len(card_assets.archive(self.directory, self.result_id)['attachments']), 1)

    def test_html_rejects_scripts_external_resources_and_file_access(self):
        bad_fragments = ['<script>alert(1)</script>', '<iframe src="https://example.com"></iframe>', '<img src="file:///etc/passwd">', '<img src="https://example.com/a.png">', '<p onclick="alert(1)">x</p>', '<style>@import "https://example.com/a";</style>', '<style>p{background:url(https://example.com/)}</style>', '<style>p{background:u\\72l(https://example.com/)}</style>', '<link rel="stylesheet" href="https://example.com/">', '<meta http-equiv="refresh" content="0;url=https://example.com">', '<object data="file:///etc/passwd"></object>', '<style/>@import "https://example.com";']
        for fragment in bad_fragments:
            with self.subTest(fragment=fragment), self.assertRaises(ValueError):
                card_assets.validate_html(HTML.replace('</main>', fragment + '</main>'))
        with self.assertRaises(ValueError):
            card_assets.validate_html('<p>fragment</p>')
        with patch.object(card_assets, 'MAX_HTML', 10):
            with self.assertRaises(ValueError):
                card_assets.validate_html(HTML)

    def test_embedded_photo_static_html_and_png_intro_outro_exports(self):
        png = self.png()
        data = 'data:image/png;base64,' + base64.b64encode(png).decode()
        html = HTML.replace('</main>', '<img alt="Фото" src="' + data + '"></main>')
        card_assets.validate_html(html)
        for kind in ('intro', 'outro'):
            row = card_assets.create_image_export(self.directory, self.result_id, data, kind)
            content, mime, name = card_assets.export_asset(self.directory, row['id'])
            self.assertEqual(content, png)
            self.assertEqual(mime, 'image/png')
            self.assertTrue(name.endswith('-' + kind + '.png'))
        self.assertEqual(len(card_assets.list_exports(self.directory, self.result_id)), 2)
        self.assertEqual(card_assets.archive(self.directory, self.result_id)['attachments'], [])
        with self.assertRaises(ValueError):
            card_assets.create_image_export(self.directory, self.result_id, 'data:image/png;base64,YnJva2Vu', 'intro')

    @unittest.skipUnless(importlib.util.find_spec('playwright') and Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome').exists(), 'Real Mac Chrome + Playwright required')
    def test_real_mac_chrome_pdf_from_static_unicode_html_and_embedded_photo(self):
        data = 'data:image/png;base64,' + base64.b64encode(self.png()).decode()
        html = HTML.replace('</main>', '<img width="120" height="160" alt="Apartment" src="' + data + '"></main>')
        result = card_assets.create_export(self.directory, self.result_id, html, 'pdf')
        content, mime, name = card_assets.export_asset(self.directory, result['id'])
        self.assertEqual(mime, 'application/pdf')
        self.assertTrue(content.startswith(b'%PDF-'))
        self.assertGreater(len(content), 5000)
        self.assertIn(b'/Type /Page', content)
        self.assertTrue(name.endswith('.pdf'))

    def test_phone_can_read_contract_while_pdf_renderer_is_busy(self):
        attachment = self.attachment()['attachments'][0]
        entered, release = threading.Event(), threading.Event()
        def slow_render(*_args):
            entered.set()
            if not release.wait(5):
                raise ValueError('Test renderer was not released')
            return b'%PDF-1.7\nconcurrent-render\n%%EOF'
        with ThreadPoolExecutor(max_workers=2) as executor, patch.object(card_assets, '_render_pdf', side_effect=slow_render):
            pdf = executor.submit(card_assets.create_export, self.directory, self.result_id, HTML, 'pdf')
            self.assertTrue(entered.wait(2))
            document = executor.submit(card_assets.attachment_asset, self.directory, self.result_id, attachment['id'])
            try:
                content, mime, _ = document.result(timeout=1)
                self.assertIn(b'private-contract', content)
                self.assertEqual(mime, 'application/pdf')
            finally:
                release.set()
            created = pdf.result(timeout=2)
        self.assertTrue(card_assets.export_asset(self.directory, created['id'])[0].startswith(b'%PDF-'))
        self.assertEqual(len(card_assets.archive(self.directory, self.result_id)['attachments']), 1)

    def test_pdf_bytes_saved_from_browser_render_and_errors_do_not_create_exports(self):
        with patch.object(card_assets, '_render_pdf', return_value=b'%PDF-1.7\nfake-test-render\n%%EOF') as render:
            result = card_assets.create_export(self.directory, self.result_id, HTML, 'pdf')
            render.assert_called_once_with(HTML, 'chrome')
        self.assertEqual(card_assets.export_asset(self.directory, result['id'])[0], b'%PDF-1.7\nfake-test-render\n%%EOF')
        with patch.object(card_assets, '_render_pdf', side_effect=ValueError('Chrome unavailable')):
            with self.assertRaises(ValueError):
                card_assets.create_export(self.directory, self.result_id, HTML, 'pdf')
        self.assertEqual(len(card_assets.list_exports(self.directory, self.result_id)), 1)


if __name__ == '__main__':
    unittest.main()
