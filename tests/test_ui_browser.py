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
from browser_text_jobs_fixture import MockTextJobs, accepted_draft


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
        # Authored HTTP fixture tests the async editor contract, not model quality.
        fixture_body = '两间独立房间，月租80,000卢布。西南站步行7分钟。有书桌。房东不提供登记；不可养宠物。'
        def draft(request, ordinal):
            return {**accepted_draft(request, ordinal), 'title': '西南站两居租房',
                    'body': fixture_body, 'tags': '#两居室租房 #地铁租房', 'inventoryCount': 1}
        jobs = MockTextJobs(self.page, 'local', result_factory=draft, hold=True)
        self.page.add_init_script("localStorage.setItem('most-rules-version','3');localStorage.setItem('most-rules',JSON.stringify({'рабочий стол':'自定义书桌'}));")
        self.page.goto(self.server.desktop_access.launch_url(self.server.server_port))
        source = '2-комнатная квартира. Две изолированные комнаты. Цена 80 000 руб. Метро Юго-Западная — 7 минут пешком. Есть рабочий стол. Без животных. Регистрацию не делаем.'
        self.page.locator('#source').fill(source)
        self.page.locator('#extract').click()
        with self.page.expect_response(lambda response: response.url.endswith('/api/local/text-jobs')
                                       and response.request.method == 'POST') as queued:
            self.page.locator('#generate').click()
        self.assertEqual(queued.value.status, 202)
        self.assertEqual(len(jobs.requests), 1)
        request = jobs.requests[0]
        self.assertEqual(request['action'], 'preview')
        self.assertEqual(request['part'], 'all')
        fields = request['fields']
        self.assertEqual(fields['sourceDescription'], source)
        self.assertEqual(fields['price'], '80000')
        self.assertEqual(fields['metro'], 'Юго-Западная')
        self.assertEqual(fields['walk'], '7')
        self.assertEqual(fields['petPolicy'], 'no')
        self.assertFalse(fields['pets'])
        self.assertEqual(fields['registrationPolicy'], 'not_available')
        self.assertEqual(fields['deposit'], '', 'The source does not specify a deposit')
        self.assertEqual(self.page.locator('#output').input_value(), '')
        self.assertTrue(self.page.locator('#generate').is_disabled())
        jobs.release_all()
        self.page.wait_for_function("expected => document.querySelector('#output').value === expected", arg=fixture_body)
        self.page.wait_for_function("!document.querySelector('#generate').disabled")
        self.assertEqual(self.page.locator('#outputTitle').input_value(), '西南站两居租房')
        self.assertEqual(len(jobs.requests), 1)
        self.assertFalse(self.page.locator('#registration').is_disabled())
        rules = self.page.evaluate("JSON.parse(localStorage.getItem('most-rules'))")
        self.assertEqual(rules['рабочий стол'], '自定义书桌')
        self.assertNotIn('вытяжка', rules, 'Built-in translations are no longer duplicated in personal rules')
        self.assertEqual(self.page.evaluate("JSON.parse(localStorage.getItem('most-rules-version'))"), 5)
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
            self.page.goto(self.server.desktop_access.launch_url(self.server.server_port))
            payload = {'fields':{'price':'80000','photos':[image_url],'features':[]},'titles':['测试'],'body':'测试','tags':'#莫斯科租房'}
            self.page.locator('#cliFile').set_input_files({'name':'acceptance.json','mimeType':'application/json','buffer':json.dumps(payload).encode()})
            row = self.page.locator('#photoObservations label').first
            row.wait_for()
            checkbox = row.locator('input[type=checkbox]')
            row.locator('input[type=number]').fill('2')
            checkbox.check()
            self.page.wait_for_function("document.getElementById('feature-chairs').checked&&document.getElementById('feature-qty-chairs').value==='2'")
            self.assertTrue(self.page.locator('#feature-chairs').is_checked())
            self.assertEqual(self.page.locator('#feature-qty-chairs').input_value(), '2')
            checkbox.uncheck()
            self.page.wait_for_function("!document.getElementById('feature-chairs').checked&&document.getElementById('feature-qty-chairs').value===''")
            self.assertFalse(self.page.locator('#feature-chairs').is_checked())
            self.assertEqual(self.page.locator('#feature-qty-chairs').input_value(), '')
            self.page.locator('#feature-chairs').check()
            self.page.locator('#feature-qty-chairs').fill('5')
            checkbox.check()
            self.page.wait_for_function("JSON.parse(localStorage.getItem('most-draft'))?.fields?.photoEvidence?.some(row=>row.id==='chairs')&&document.getElementById('feature-qty-chairs').value==='5'")
            self.assertEqual(self.page.locator('#feature-qty-chairs').input_value(), '5')
            checkbox.uncheck()
            self.page.wait_for_function("JSON.parse(localStorage.getItem('most-draft'))?.fields?.photoEvidence?.length===0&&document.getElementById('feature-chairs').checked&&document.getElementById('feature-qty-chairs').value==='5'")
            self.assertTrue(self.page.locator('#feature-chairs').is_checked())
            self.assertEqual(self.page.locator('#feature-qty-chairs').input_value(), '5')
            draft = self.page.evaluate("JSON.parse(localStorage.getItem('most-draft'))")
            self.assertEqual(draft['fields']['photoEvidence'], [])

    def test_real_chrome_scroll_loads_gallery_and_reaches_bottom(self):
        import browser_import
        # Use the fresh blank page: set_content preserves a live app's pending
        # bootstrap callbacks, which would then render into this source fixture.
        self.page.set_content('''<main><h1>Сдаётся 2-комнатная квартира 54 м²</h1>
            <div data-name="PriceInfo">80 000 ₽ в месяц</div>
            <div data-name="Description">Две изолированные комнаты</div>
            <div style="height:2400px"></div><div data-name="OfferGallery"><img id="lazy"></div></main>
            <script>addEventListener('scroll',()=>{if(scrollY>1000)document.querySelector('#lazy').setAttribute('data-original','https://images.cdn-cian.ru/images/lazy-room.jpg');});</script>''')
        first = self.page.evaluate(browser_import.SNAPSHOT)
        self.assertNotIn('https://images.cdn-cian.ru/images/lazy-room.jpg', first['images'])
        snapshot, capture = browser_import.capture_scrolled(self.page, first, time.monotonic()+12, lambda url, **kwargs: url)
        self.assertTrue(capture['pageWalkCompleted'])
        self.assertFalse(capture['completed'])
        self.assertTrue(capture['bounded'])
        self.assertEqual(capture['galleryCoverage']['state'], 'unknown')
        self.assertIsNone(capture['galleryCoverage']['expected'])
        self.assertEqual(capture['galleryCoverage']['observed'], 1)
        self.assertGreater(capture['scrolls'], 1)
        self.assertIn('https://images.cdn-cian.ru/images/lazy-room.jpg', snapshot['images'])
        self.assertEqual(self.page.evaluate('scrollY'), 0)
        # An explicit total changes the album conclusion, while the completed
        # page walk and the original lazy-loading checks remain independent.
        self.page.locator('[data-name="OfferGallery"]').evaluate('(e)=>e.setAttribute("data-photo-count","1")')
        counted = self.page.evaluate(browser_import.SNAPSHOT)
        observed, complete = browser_import.capture_scrolled(self.page, counted, time.monotonic()+12, lambda url, **kwargs: url)
        self.assertTrue(complete['pageWalkCompleted']);self.assertTrue(complete['completed'])
        self.assertFalse(complete['bounded'])
        self.assertEqual(complete['galleryCoverage']['state'], 'complete')
        self.assertEqual(complete['galleryCoverage']['missing'], 0)
        self.assertEqual(observed['images'], snapshot['images'])
        self.assertEqual(self.page.evaluate('scrollY'), 0)

    def test_furniture_photos_follow_scroll_and_real_import_clears_example(self):
        from PIL import Image
        self.page.set_viewport_size({'width':1440, 'height':1000})
        self.page.add_init_script("localStorage.setItem('most-photo-analyze','false');")
        images = {}
        for index, color in enumerate(['#e5b585', '#81aab9', '#93af80'], 1):
            stream = io.BytesIO()
            Image.new('RGB', (320, 220), color).save(stream, 'PNG')
            url = f'https://images.cdn-cian.ru/images/furniture-follow-{index}.jpg'
            images[url] = photo_store.attach_photo(self.storage.name, url, 'data:image/png;base64,' + base64.b64encode(stream.getvalue()).decode())
        with patch.object(photo_store, 'load_photo', side_effect=lambda directory, url, *args, **kwargs: images[url]):
            self.page.goto(self.server.desktop_access.launch_url(self.server.server_port))
            self.page.locator('#demo').click()
            self.assertTrue(self.page.locator('#demoNotice').is_visible())
            payload = {'fields':{'price':'80000','photos':list(images),'features':[]},'titles':['真实房源'],'body':'真实房源说明','tags':'#莫斯科租房'}
            self.page.locator('#cliFile').set_input_files({'name':'real-listing.json','mimeType':'application/json','buffer':json.dumps(payload).encode()})
            self.page.wait_for_function("document.getElementById('photoStatus').textContent.startsWith('3 / 3')")
            self.assertFalse(self.page.locator('#demoNotice').is_visible())
            self.assertNotIn('демонстрационные данные', self.page.locator('#toast').inner_text())
            self.assertEqual(self.page.locator('#output').input_value(), '真实房源说明')
            self.page.evaluate("window.scrollTo(0,document.getElementById('furnitureSection').getBoundingClientRect().top+scrollY-100)")
            dock = self.page.locator('#furniturePhotoDock')
            dock.wait_for(state='visible')
            self.page.wait_for_function("document.getElementById('furniturePhotoImage').naturalWidth>0")
            dock_box = dock.bounding_box()
            furniture_box = self.page.locator('#featureGrid').bounding_box()
            self.assertGreater(dock_box['x'], furniture_box['x']+furniture_box['width'])
            self.assertGreaterEqual(dock_box['y'], 0)
            self.assertLess(dock_box['y'], 150)
            first_counter = self.page.locator('#furniturePhotoCounter').inner_text()
            first_image = self.page.locator('#furniturePhotoImage').get_attribute('src')
            self.assertTrue(self.page.locator('#furniturePhotoAuto').is_checked())
            # The catalog can grow without changing how scrolling distributes
            # photos over its full height. Cross a real photo boundary instead
            # of assuming every fixed 240 px reaches the next segment.
            scroll = self.page.evaluate("""() => {
                const section=document.getElementById('furnitureSection').getBoundingClientRect();
                const [number,count]=document.getElementById('furniturePhotoCounter').textContent.split('/').map(Number);
                const distance=Math.max(1,section.height-innerHeight*.35),next=number;
                if(next>=count)throw Error('Fixture must start before the final photo');
                const targetTop=innerHeight*.25-distance*(next+.15)/count;
                const before=scrollY,delta=section.top-targetTop;
                window.scrollBy(0,delta);
                return {before,delta,sectionHeight:section.height,sectionTop:section.top,number,count};
            }""")
            self.assertGreater(scroll['delta'], 0, scroll)
            self.page.wait_for_function('before => scrollY>before', arg=scroll['before'])
            self.page.wait_for_function("old => document.getElementById('furniturePhotoCounter').textContent!==old", arg=first_counter)
            self.assertNotEqual(self.page.locator('#furniturePhotoImage').get_attribute('src'), first_image)
            self.page.locator('#furniturePhotoAuto').uncheck()
            before_counter = self.page.locator('#furniturePhotoCounter').inner_text()
            before_image = self.page.locator('#furniturePhotoImage').get_attribute('src')
            self.page.locator('#furniturePhotoNext').click()
            self.assertNotEqual(self.page.locator('#furniturePhotoCounter').inner_text(), before_counter)
            self.assertNotEqual(self.page.locator('#furniturePhotoImage').get_attribute('src'), before_image)
            selected_counter = self.page.locator('#furniturePhotoCounter').inner_text()
            self.page.evaluate('window.scrollBy(0,160)')
            self.page.wait_for_function("!document.getElementById('furniturePhotoDock').classList.contains('hidden')")
            self.assertEqual(self.page.locator('#furniturePhotoCounter').inner_text(), selected_counter)
            self.page.set_viewport_size({'width':900, 'height':1000})
            self.page.evaluate("window.scrollTo(0,document.getElementById('furnitureSection').getBoundingClientRect().top+scrollY-30)")
            self.page.wait_for_function('document.documentElement.scrollWidth<=innerWidth+1')
            self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'), 901)


if __name__ == '__main__':
    unittest.main()
