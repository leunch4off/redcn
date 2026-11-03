"""393px read-only city source browser acceptance, without external requests."""
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import threading
import unittest

ROOT=Path(__file__).resolve().parents[1]
HARNESS='''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/web/city-pois.css"><style>*{box-sizing:border-box}body{margin:0;padding:12px;font:16px system-ui}input,select{padding:8px}</style><div id="mount"></div><script type="module">import{setupCityPois}from'/web/city-pois.mjs';window.ui=setupCityPois({mount:document.getElementById('mount'),getFields:()=>({address:'PRIVATE CLIENT ADDRESS',phone:'+79991234567'})});window.ready=true;</script></html>'''


class CityPoiBrowser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:from playwright.sync_api import sync_playwright
        except ImportError:raise unittest.SkipTest('Optional city reference acceptance needs Playwright/Chrome')
        cls.runtime=sync_playwright().start()
        try:cls.browser=cls.runtime.chromium.launch(channel='chrome',headless=True)
        except Exception as error:cls.runtime.stop();raise unittest.SkipTest(str(error).splitlines()[0])
        class Handler(SimpleHTTPRequestHandler):
            def __init__(self,*args,**kwargs):super().__init__(*args,directory=str(ROOT),**kwargs)
            def log_message(self,*args):pass
            def do_GET(self):
                if self.path=='/':
                    value=HARNESS.encode();self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.end_headers();self.wfile.write(value)
                else:super().do_GET()
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start();cls.url='http://127.0.0.1:'+str(cls.server.server_port)
    @classmethod
    def tearDownClass(cls):
        cls.browser.close();cls.runtime.stop();cls.server.shutdown();cls.server.server_close();cls.thread.join()
    def setUp(self):
        self.context=self.browser.new_context(viewport={'width':393,'height':852});self.page=self.context.new_page();self.errors=[];self.page.on('pageerror',lambda error:self.errors.append(str(error)));self.page.goto(self.url,wait_until='domcontentloaded');self.page.wait_for_function('window.ready===true')
    def tearDown(self):
        self.context.close();self.assertEqual(self.errors,[])
    def test_category_name_sources_and_unknown_accuracy_are_visible_without_phone_overflow(self):
        self.assertEqual(self.page.locator('.city-poi-card').count(),5)
        self.page.get_by_label('Категория городских мест',exact=True).select_option('park')
        self.assertEqual(self.page.locator('.city-poi-card').count(),2)
        self.page.get_by_label('Поиск городских мест',exact=True).fill('Академический')
        card=self.page.locator('.city-poi-card');self.assertEqual(card.count(),1);self.assertIn('историческая',card.inner_text())
        card.locator('summary').click();self.assertIn('2026-11-01',card.inner_text());self.assertIn('Точность в метрах не опубликована',card.inner_text())
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'),393)
    def test_chinese_search_maplinks_and_hostile_query_never_use_private_card_fields(self):
        self.page.get_by_label('Поиск городских мест',exact=True).fill('粤菜');self.assertEqual(self.page.locator('.city-poi-card').count(),1)
        hrefs=self.page.locator('.city-poi-links a').evaluate_all('nodes=>nodes.map(node=>node.href)')
        self.assertEqual(len(hrefs),3);self.assertTrue(all('PRIVATE'not in href and'7999'not in href and'origin='not in href for href in hrefs))
        self.page.get_by_label('Поиск городских мест',exact=True).fill('<img src=x onerror=alert(1)>')
        self.assertEqual(self.page.locator('.city-poi-card').count(),0);self.assertEqual(self.page.locator('img').count(),0)


if __name__=='__main__':unittest.main()
