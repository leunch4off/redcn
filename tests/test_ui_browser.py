"""Optional real Chrome acceptance; uses owned headless context and isolated storage."""
import base64
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'cli'))
import local_server
import photo_store


class BrowserAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise unittest.SkipTest('Optional browser acceptance needs Playwright and Chrome')
        cls.runtime = sync_playwright().start()
        try:
            cls.browser = cls.runtime.chromium.launch(channel='chrome', headless=True)
        except Exception as error:
            cls.runtime.stop()
            raise unittest.SkipTest('Optional Chrome unavailable: ' + str(error).splitlines()[0])
        cls.storage = tempfile.TemporaryDirectory()
        cls.server = local_server.create_server(cls.storage.name, 0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = 'http://127.0.0.1:' + str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.runtime.stop()
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.storage.cleanup()

    def setUp(self):
        self.context = self.browser.new_context()
        self.page = self.context.new_page()
        self.errors = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))

    def tearDown(self):
        self.context.close()
        self.assertEqual(self.errors, [], 'Browser JavaScript errors')

    def test_existing_dictionary_and_generation_geo(self):
        self.page.add_init_script("localStorage.setItem('most-rules-version','3');localStorage.setItem('most-rules',JSON.stringify({'рабочий стол':'自定义书桌'}));")
        self.page.goto(self.url)
        self.page.locator('#source').fill('2-комнатная квартира. Две изолированные комнаты. Цена 80 000 руб. Метро Юго-Западная — 7 минут пешком. Есть рабочий стол. Без животных. Регистрацию не делаем.')
        self.page.locator('#extract').click()
        self.page.locator('#generate').click()
        body = self.page.locator('#output').input_value()
        self.assertIn('抵达登记（临时居住登记）', body)
        self.assertNotIn('宠物', body)
        self.assertIn('40,000', body)
        self.assertIn('20,000', body)
        self.assertFalse(self.page.locator('#registration').is_disabled())
        rules = self.page.evaluate("JSON.parse(localStorage.getItem('most-rules'))")
        self.assertEqual(rules['рабочий стол'], '自定义书桌')
        self.assertIn('вытяжка', rules)
        self.page.locator('[data-tab="knowledge"]').click()
        self.page.locator('#geoSearch').fill('Юго-Западная')
        self.assertIn('西南', self.page.locator('#geoResults').inner_text())
        self.assertIn('5', self.page.locator('#geoSummary').inner_text())

    def test_photo_confirmation_uncheck_and_count_restore(self):
        from PIL import Image
        stream = io.BytesIO()
        Image.new('RGB', (128, 128), '#eeeeee').save(stream, 'PNG')
        image_url = 'https://images.cdn-cian.ru/images/acceptance-room.jpg'
        image = photo_store.attach_photo(self.storage.name, image_url, 'data:image/png;base64,' + base64.b64encode(stream.getvalue()).decode())
        analysis = {'version':1, 'status':'review', 'engine':'Acceptance fixture', 'originalId':image['id'], 'sourceUrl':image_url, 'candidates':[{'id':'chairs','label':'Стулья','confidence':.8,'count':None,'evidence':[{'text':'Два видимых стула'}]}]}
        with patch.object(photo_store, 'load_photo', return_value=image), patch.object(photo_store, 'analyze_photo', return_value=analysis):
            self.page.goto(self.url)
            payload = {'fields':{'price':'80000','photos':[image_url],'features':[]},'titles':['测试'],'body':'测试','tags':'#莫斯科租房'}
            self.page.locator('#cliFile').set_input_files({'name':'acceptance.json','mimeType':'application/json','buffer':json.dumps(payload).encode()})
            row = self.page.locator('#photoObservations label').first
            row.wait_for()
            checkbox = row.locator('input[type=checkbox]')
            row.locator('input[type=number]').fill('2')
            checkbox.check()
            self.assertTrue(self.page.locator('#feature-chairs').is_checked())
            self.assertEqual(self.page.locator('#feature-qty-chairs').input_value(), '2')
            checkbox.uncheck()
            self.assertFalse(self.page.locator('#feature-chairs').is_checked())
            self.assertEqual(self.page.locator('#feature-qty-chairs').input_value(), '')
            self.page.locator('#feature-chairs').check()
            self.page.locator('#feature-qty-chairs').fill('5')
            checkbox.check()
            self.assertEqual(self.page.locator('#feature-qty-chairs').input_value(), '5')
            checkbox.uncheck()
            self.assertTrue(self.page.locator('#feature-chairs').is_checked())
            self.assertEqual(self.page.locator('#feature-qty-chairs').input_value(), '5')
            draft = self.page.evaluate("JSON.parse(localStorage.getItem('most-draft'))")
            self.assertEqual(draft['fields']['photoEvidence'], [])

    def test_real_chrome_scroll_loads_gallery_and_reaches_bottom(self):
        import browser_import
        self.page.goto(self.url)
        self.page.set_content('''<main><h1>Сдаётся 2-комнатная квартира 54 м²</h1>
            <div data-name="PriceInfo">80 000 ₽ в месяц</div>
            <div data-name="Description">Две изолированные комнаты</div>
            <div style="height:2400px"></div><div data-name="OfferGallery"><img id="lazy"></div></main>
            <script>addEventListener('scroll',()=>{if(scrollY>1000)document.querySelector('#lazy').setAttribute('data-original','https://images.cdn-cian.ru/images/lazy-room.jpg');});</script>''')
        first = self.page.evaluate(browser_import.SNAPSHOT)
        snapshot, capture = browser_import.capture_scrolled(self.page, first, time.monotonic()+12, lambda url, **kwargs: url)
        self.assertTrue(capture['completed'])
        self.assertFalse(capture['bounded'])
        self.assertGreater(capture['scrolls'], 1)
        self.assertIn('https://images.cdn-cian.ru/images/lazy-room.jpg', snapshot['images'])
        self.assertEqual(self.page.evaluate('scrollY'), 0)


if __name__ == '__main__':
    unittest.main()
