"""Real editor controls: source-aware reuse, expiry and edits during a request."""
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import unittest

ROOT=Path(__file__).resolve().parents[1]
HARNESS='''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/web/university-routes.css"><style>*{box-sizing:border-box}body{margin:0;padding:12px;font:16px system-ui}.btn{min-height:44px;white-space:normal}input,select,textarea{font:inherit}</style><div id="mount"></div><script type="module">
import {setupUniversityRoutes} from '/web/university-routes.mjs';
import {createUniversityRouteCacheCallbacks} from '/web/university-route-cache-client.mjs';
window.calls=[];window.changed=[];window.defer=false;window.response={hit:false,reason:'expired'};
window.fields={latitude:55.7,longitude:37.5,address:'Москва, тестовая улица 1',universityRoutes:[{name:'МГУ',campusId:'msu-physics',mode:'public',method:'manual-confirmed',checkedAt:'2026-11-01',time:'40–55'}]};
const api=async(path,data)=>{window.calls.push({path,data});if(window.defer){window.defer=false;await new Promise(resolve=>window.release=resolve);}return path.endsWith('/remember')?{cached:true}:window.response;};
window.editor=setupUniversityRoutes({mount:document.getElementById('mount'),getFields:()=>window.fields,onChange:rows=>window.changed.push(rows),...createUniversityRouteCacheCallbacks(api)});window.ready=true;
</script></html>'''


class UniversityRouteCacheBrowser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise unittest.SkipTest('Optional route-cache UI acceptance needs Playwright/Chrome')
        cls.runtime=sync_playwright().start()
        try:
            cls.browser=cls.runtime.chromium.launch(channel='chrome',headless=True)
        except Exception as error:
            cls.runtime.stop();raise unittest.SkipTest(str(error).splitlines()[0])
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
        self.context=self.browser.new_context(viewport={'width':393,'height':852});self.page=self.context.new_page();self.errors=[];self.page.on('pageerror',lambda error:self.errors.append(str(error)))
        self.page.goto(self.url,wait_until='domcontentloaded');self.page.wait_for_function('window.ready===true');self.page.locator('.university-route-entry > summary').click()

    def tearDown(self):
        self.context.close();self.assertEqual(self.errors,[])

    def lookup(self):
        self.page.get_by_role('button',name='Взять недавний проверенный маршрут',exact=True).click()

    def test_expired_route_is_visible_and_never_replaces_fields(self):
        self.lookup();self.page.get_by_text('Срок кеша истёк. Проверьте маршрут в картах.',exact=True).wait_for()
        self.assertEqual(self.page.evaluate('window.changed'),[])
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'),393)

    def test_remember_and_hit_keep_original_date_and_only_change_route_draft(self):
        self.page.get_by_role('button',name='Запомнить проверенный маршрут на 1 час',exact=True).click()
        self.page.get_by_text('Маршрут сохранён на 1 час; дата проверки и источник сохраняются.',exact=True).wait_for()
        self.assertEqual(self.page.evaluate('Object.keys(window.calls[0].data).sort()'),['origin','route'])
        self.page.evaluate("window.response={hit:true,route:{...window.fields.universityRoutes[0],time:'35',transport:'Проверенная заметка'}}")
        self.lookup();self.page.wait_for_function('window.changed.length===1')
        self.assertEqual(self.page.evaluate('window.changed[0][0].time'),'35');self.assertEqual(self.page.evaluate('window.changed[0][0].checkedAt'),'2026-11-01')
        self.assertEqual(self.page.evaluate('window.fields.universityRoutes[0].time'),'40–55')

    def test_late_cache_response_cannot_overwrite_operator_edits(self):
        self.page.evaluate("window.defer=true;window.response={hit:true,route:{...window.fields.universityRoutes[0],time:'35'}}")
        self.lookup();self.page.wait_for_function("typeof window.release==='function'")
        self.page.get_by_label('Подтверждённое время, мин · маршрут 1',exact=True).fill('60')
        self.page.evaluate('window.release();void 0')
        self.page.get_by_text('Поля изменились во время запроса. Результат кеша не применён.',exact=True).wait_for()
        self.assertEqual(self.page.evaluate('window.editor.value()[0].time'),'60')

    def test_new_route_campus_then_mode_enables_cache_without_rerender_and_pending_keeps_unknown_disabled(self):
        self.page.get_by_role('button',name='Убрать маршрут',exact=True).click()
        self.page.get_by_role('button',name='Добавить вуз / маршрут',exact=True).click()
        lookup=self.page.get_by_role('button',name='Взять недавний проверенный маршрут',exact=True)
        self.assertTrue(lookup.is_disabled())
        self.page.get_by_label('Кампус из справочника · маршрут 1',exact=True).select_option('msu-physics')
        self.page.locator('.university-route-entry > summary').click()
        mode=self.page.get_by_label('Способ поездки · маршрут 1',exact=True)
        self.assertTrue(lookup.is_disabled());mode.select_option('public');self.assertTrue(lookup.is_enabled())
        self.page.evaluate('window.defer=true');lookup.click();self.page.wait_for_function("typeof window.release==='function'")
        mode.select_option('unknown');self.assertTrue(lookup.is_disabled())
        self.page.evaluate('window.release();void 0')
        self.page.get_by_text('Поля изменились во время запроса. Результат кеша не применён.',exact=True).wait_for()
        self.assertTrue(lookup.is_disabled())

    def test_late_lookup_after_removing_earlier_row_updates_current_row_without_duplication(self):
        self.page.evaluate("window.second={...window.fields.universityRoutes[0],campusId:'msu-sternberg'};window.editor.refresh({...window.fields,universityRoutes:[window.fields.universityRoutes[0],window.second]});window.defer=true;window.response={hit:true,route:{...window.second,time:'35'}}")
        entries=self.page.locator('.university-route-entry')
        entries.nth(1).locator(':scope > summary').click()
        entries.nth(1).get_by_role('button',name='Взять недавний проверенный маршрут',exact=True).click()
        self.page.wait_for_function("typeof window.release==='function'")
        entries.nth(0).locator(':scope > summary').click();entries.nth(0).get_by_role('button',name='Убрать маршрут',exact=True).click()
        self.page.evaluate('window.release();void 0')
        self.page.wait_for_function("window.editor.value().length===1&&window.editor.value()[0].time==='35'")
        self.assertEqual(self.page.evaluate('window.editor.value()[0].campusId'),'msu-sternberg')


if __name__=='__main__':unittest.main()
