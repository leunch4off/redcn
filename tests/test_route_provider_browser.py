"""Shared desktop/phone route controls: real DOM, synthetic API, no live source."""
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import threading,unittest
ROOT=Path(__file__).resolve().parents[1]
HARNESS='''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/web/university-routes.css"><style>*{box-sizing:border-box}body{margin:0;padding:12px;font:16px system-ui}.btn{min-height:44px;white-space:normal}input,select,textarea{font:inherit}</style><div id="mount"></div><script type="module">
import {setupUniversityRoutes} from '/web/university-routes.mjs';import {createUniversityRouteCacheCallbacks} from '/web/university-route-cache-client.mjs';
window.calls=[];window.changed=[];window.defer=false;window.fail=false;window.fields={latitude:55.7,longitude:37.5,address:'Тестовый адрес',photos:['private-photo'],universityRoutes:[{name:'Тестовый корпус',latitude:55.71,longitude:37.51,mode:'walk',method:'manual-confirmed',checkedAt:'2026-11-01',time:'40'}]};
const api=async(path,data)=>{calls.push({path,data});if(window.defer){window.defer=false;await new Promise(resolve=>window.release=resolve);}if(window.fail)throw Error('Тестовый отказ сервиса');const stamp=Date.now();return {provider:'valhalla-fossgis-demo',calculatedAt:new Date(stamp).toISOString(),expiresAt:new Date(stamp+600000).toISOString(),durationSeconds:700,distanceKm:1.1,time:'12',routeDistanceKm:1.1,checkedAt:'2026-11-01',sourceUrl:'https://valhalla1.openstreetmap.de/route',origin:data.origin,destination:data.destination,mode:data.mode,useEntrances:data.useEntrances,travelTimeKind:'network-estimate',responseSha256:'a'.repeat(64)};};
window.editor=setupUniversityRoutes({mount:document.getElementById('mount'),getFields:()=>fields,onChange:rows=>changed.push(rows),...createUniversityRouteCacheCallbacks(api)});window.ready=true;
</script></html>'''

class RouteProviderBrowser(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  try:from playwright.sync_api import sync_playwright
  except ImportError:raise unittest.SkipTest('Route provider UI needs optional Chrome/Playwright')
  cls.runtime=sync_playwright().start()
  try:cls.browser=cls.runtime.chromium.launch(channel='chrome',headless=True)
  except Exception as error:cls.runtime.stop();raise unittest.SkipTest(str(error).splitlines()[0])
  class Handler(SimpleHTTPRequestHandler):
   def __init__(self,*a,**k):super().__init__(*a,directory=str(ROOT),**k)
   def log_message(self,*a):pass
   def do_GET(self):
    if self.path=='/':self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.end_headers();self.wfile.write(HARNESS.encode())
    else:super().do_GET()
  cls.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start();cls.url='http://127.0.0.1:'+str(cls.server.server_port)
 @classmethod
 def tearDownClass(cls):cls.browser.close();cls.runtime.stop();cls.server.shutdown();cls.server.server_close();cls.thread.join()
 def setUp(self):
  self.context=self.browser.new_context(viewport={'width':393,'height':852});self.page=self.context.new_page();self.errors=[];self.page.on('pageerror',lambda e:self.errors.append(str(e)));self.page.goto(self.url,wait_until='domcontentloaded');self.page.wait_for_function('window.ready===true');self.open_controls()
 def tearDown(self):self.context.close();self.assertEqual(self.errors,[])
 def open_controls(self):self.page.locator('.university-route-entry > summary').click();self.page.locator('.university-route-provider > summary').click()
 def calculate(self):self.page.get_by_role('button',name='Рассчитать выбранный пеший / автомобильный маршрут',exact=True).click()
 def test_explicit_calculation_applies_dated_estimate_and_sends_no_private_fields(self):
  self.calculate();self.page.wait_for_function("editor.value()[0].method==='provider'");row=self.page.evaluate('editor.value()[0]');self.assertEqual(row['time'],'12');self.assertEqual(row['originPoint']['latitude'],55.7);self.assertEqual(row['calculation']['travelTimeKind'],'network-estimate');self.assertNotIn('private',self.page.evaluate('JSON.stringify(calls)'));self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'),393)
 def test_late_response_cannot_overwrite_changed_point(self):
  self.page.evaluate('window.defer=true');self.calculate();self.page.wait_for_function("typeof window.release==='function'");self.page.get_by_label('Начало · Широта · маршрут 1',exact=True).fill('55.72');self.page.evaluate('release();void 0');self.page.get_by_text('Точки или поля изменились: результат не применён.',exact=True).wait_for();self.assertEqual(self.page.evaluate('editor.value()[0].originPoint.latitude'),55.72);self.assertEqual(self.page.evaluate('editor.value()[0].method'),'unknown')
 def test_changed_destination_invalidates_old_computed_time(self):
  self.calculate();self.page.wait_for_function("editor.value()[0].method==='provider'");self.open_controls();self.page.get_by_label('Назначение · Широта · маршрут 1',exact=True).fill('55.73');row=self.page.evaluate('editor.value()[0]');self.assertIsNone(row['calculation']);self.assertEqual(row['time'],'');self.assertEqual(row['sourceUrl'],'')
 def test_failed_provider_keeps_prior_manual_route(self):
  self.page.evaluate('window.fail=true');self.calculate();self.page.get_by_text('Тестовый отказ сервиса',exact=True).wait_for();self.assertEqual(self.page.evaluate('editor.value()[0].time'),'40');self.assertEqual(self.page.evaluate('editor.value()[0].method'),'manual-confirmed')

if __name__=='__main__':unittest.main()
