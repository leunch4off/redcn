"""Owned static Chrome UI for manual import failure fallback, with no imports."""
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import threading
import unittest

ROOT=Path(__file__).resolve().parents[1]
HARNESS='''<!doctype html><html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/web/import-errors.css"><style>*{box-sizing:border-box}body{margin:0;padding:12px;font:16px system-ui}main{max-width:850px;margin:auto}.btn{padding:10px;border:1px solid #ddd;border-radius:8px;background:white}textarea{width:100%;font:inherit}</style><main><div id="error"></div><label>Описание<textarea id="source"></textarea></label></main><script type="module">
import {setupImportErrors} from '/web/import-errors.mjs';
window.retryCalls=0;window.pasteCalls=0;window.importErrors=setupImportErrors({mount:document.getElementById('error'),getUrl:()=> 'https://www.avito.ru/moskva/kvartiry/test_123?context=tracking',onPaste:()=>{pasteCalls++;document.getElementById('source').focus();},onRetry:async()=>{retryCalls++;}});
importErrors.show({status:403,message:'HTTP Error 403: Forbidden'},{stage:'navigate'});window.importErrorsReady=true;
</script></html>'''

class ImportErrorsBrowserAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:raise unittest.SkipTest('Optional import-error acceptance needs Playwright and Chrome')
        cls.runtime=sync_playwright().start()
        try:cls.browser=cls.runtime.chromium.launch(channel='chrome',headless=True)
        except Exception as error:
            cls.runtime.stop();raise unittest.SkipTest('Optional Chrome unavailable: '+str(error).splitlines()[0])
        class Handler(SimpleHTTPRequestHandler):
            def __init__(self,*args,**kwargs):super().__init__(*args,directory=str(ROOT),**kwargs)
            def log_message(self,*args):pass
            def do_GET(self):
                if self.path=='/':
                    data=HARNESS.encode();self.send_response(200);self.send_header('Content-Type','text/html;charset=utf-8');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
                else:super().do_GET()
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start();cls.url='http://127.0.0.1:'+str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):cls.browser.close();cls.runtime.stop();cls.server.shutdown();cls.server.server_close();cls.thread.join()
    def setUp(self):
        self.context=self.browser.new_context(viewport={'width':393,'height':852});self.page=self.context.new_page();self.errors=[];self.page.on('pageerror',lambda error:self.errors.append(str(error)));self.page.goto(self.url);self.page.wait_for_function('window.importErrorsReady===true')
    def tearDown(self):self.context.close();self.assertEqual(self.errors,[])
    def test_blocked_avito_shows_exact_reason_and_manual_paste_without_retry(self):
        self.assertIn('Авито требует внимания',self.page.locator('#error').inner_text());self.assertIn('HTTP 403',self.page.locator('#error').inner_text())
        self.assertEqual(self.page.get_by_role('button',name='Повторить вручную').count(),0)
        self.page.get_by_role('button',name='Вставить текст вручную').click();self.assertEqual(self.page.evaluate('pasteCalls'),1);self.assertEqual(self.page.evaluate('document.activeElement.id'),'source')
        self.assertEqual(self.page.get_by_role('link',name='Открыть источник').get_attribute('href'),'https://www.avito.ru/moskva/kvartiry/test_123');self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'),394)
    def test_manual_retry_is_user_driven_and_clear_removes_old_error_and_untrusted_markup(self):
        self.page.evaluate("importErrors.show(new Error('HTTP Error 503: <img src=x onerror=window.injected=true>'))")
        self.assertEqual(self.page.evaluate('retryCalls'),0);self.page.get_by_role('button',name='Повторить вручную').click();self.assertEqual(self.page.evaluate('retryCalls'),1)
        self.assertEqual(self.page.locator('#error img').count(),0);self.assertIsNone(self.page.evaluate('window.injected'))
        self.page.evaluate('importErrors.clear()');self.assertFalse(self.page.locator('#error').is_visible());self.assertEqual(self.page.locator('#error').inner_text(),'')

if __name__=='__main__':unittest.main()
