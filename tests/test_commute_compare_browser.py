"""Isolated operator commute comparison and verification editor acceptance."""
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import unittest

ROOT=Path(__file__).resolve().parents[1]
HARNESS='''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/web/commute-compare.css"><link rel="stylesheet" href="/web/university-routes.css"><style>*{box-sizing:border-box}body{margin:0;padding:12px;font:16px system-ui;color:#263a2d}main{max-width:1000px;margin:auto}.btn{min-height:44px;padding:8px;white-space:normal}input,select,textarea{font:inherit}.muted{color:#687466;font-size:13px}</style><main><div id="compare"></div><div id="routes"></div></main><script type="module">
import {setupCommuteCompare} from '/web/commute-compare.mjs';
import {setupUniversityRoutes} from '/web/university-routes.mjs';
Date.now=()=>Date.parse('2026-11-01T09:00:00Z');
const route=(patch={})=>({name:'МГУ',campusId:'msu-main',time:'40–55',mode:'public',transport:'Автобус119',method:'manual-confirmed',checkedAt:'2026-10-31',accessMinutes:5,egressMinutes:7,transfers:1,...patch});
window.objects=[{id:'a',title:'Квартира A',housingType:'apartment',sourceDescription:'Рядом общежитие',district:'Гагаринский',price:'80000',deposit:'40000',commission:'50',universityRoutes:[route()]},{id:'b',title:'Квартира B',housingType:'apartment',district:'Гагаринский',price:'70000',deposit:'0',commission:'0',universityRoutes:[route({time:'35'})]},{id:'c',title:'Общежитие C',workflow:{housingType:'dormitory'},district:'Раменки',price:'20000',deposit:'0',commission:'0',universityRoutes:[route({time:'30'})]}];
window.opened=[];window.ctrl=setupCommuteCompare({mount:document.getElementById('compare'),getObjects:()=>window.objects,onOpen:id=>window.opened.push(id)});
window.savedRoutes=[];window.routeEditor=setupUniversityRoutes({mount:document.getElementById('routes'),getFields:()=>({universityRoutes:[route()]}),onChange:rows=>window.savedRoutes=rows});window.ready=true;
</script></html>'''


class CommuteCompareBrowserAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise unittest.SkipTest('Optional commute acceptance needs Playwright/Chrome')
        cls.runtime=sync_playwright().start()
        try:
            cls.browser=cls.runtime.chromium.launch(channel='chrome',headless=True)
        except Exception as error:
            cls.runtime.stop()
            raise unittest.SkipTest(str(error).splitlines()[0])
        class Handler(SimpleHTTPRequestHandler):
            def __init__(self,*args,**kwargs):
                super().__init__(*args,directory=str(ROOT),**kwargs)
            def log_message(self,*args):
                pass
            def do_GET(self):
                if self.path=='/':
                    value=HARNESS.encode()
                    self.send_response(200)
                    self.send_header('Content-Type','text/html; charset=utf-8')
                    self.send_header('Content-Length',str(len(value)))
                    self.end_headers()
                    self.wfile.write(value)
                else:
                    super().do_GET()
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()
        cls.url='http://127.0.0.1:'+str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.runtime.stop()
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.context=self.browser.new_context(viewport={'width':393,'height':852})
        self.page=self.context.new_page()
        self.errors=[]
        self.page.on('pageerror',lambda error:self.errors.append(str(error)))
        self.page.goto(self.url,wait_until='domcontentloaded')
        self.page.wait_for_function('window.ready===true')

    def tearDown(self):
        self.context.close()
        self.assertEqual(self.errors,[])

    def choose_campus(self):
        self.page.get_by_label('Вуз для сравнения',exact=True).select_option('msu')
        self.assertEqual(self.page.get_by_label('Конкретный кампус / корпус',exact=True).input_value(),'')
        self.page.get_by_label('Конкретный кампус / корпус',exact=True).select_option('msu-main')

    def test_real_table_filters_ranges_types_and_keeps_unknowns_separate_without_overflow(self):
        self.assertIn('Выберите конкретный корпус',self.page.locator('.commute-compare-status').inner_text())
        self.choose_campus()
        self.page.get_by_label('Максимальная дорога, минут',exact=True).fill('50')
        self.page.get_by_label('Показать отложенные и неподтверждённые записи',exact=True).uncheck()
        self.assertEqual(self.page.locator('#compare tbody tr').count(),2)
        self.assertNotIn('Квартира A',self.page.locator('#compare tbody').inner_text())
        self.page.get_by_label('Тип жилья',exact=True).select_option('dormitory')
        self.assertEqual(self.page.locator('#compare tbody tr').count(),1)
        self.assertIn('Общежитие C',self.page.locator('#compare tbody').inner_text())
        self.page.locator('#compare tbody').get_by_role('button',name='Открыть карточку').click()
        self.assertEqual(self.page.evaluate('window.opened'),['c'])
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'),394)

    def test_registry_shows_primary_source_and_never_selects_university_main_address_implicitly(self):
        self.page.get_by_label('Вуз для сравнения',exact=True).select_option('msu')
        self.page.locator('.commute-campus-registry > summary').click()
        self.page.get_by_label('Поиск корпуса в справочнике',exact=True).fill('Моховая')
        entries=self.page.locator('.commute-campus-row')
        self.assertEqual(entries.count(),2)
        self.assertIn('cpk.msu.ru',entries.first.inner_text())
        self.assertIn('2026-10-30',entries.first.inner_text())
        self.assertTrue(entries.first.locator('a').first.get_attribute('href').startswith('https://cpk.msu.ru/'))
        entries.first.get_by_role('button',name='Сравнить дорогу к этому адресу').click()
        self.assertNotEqual(self.page.get_by_label('Конкретный кампус / корпус',exact=True).input_value(),'msu-main')
        self.assertIn('на проверку',self.page.locator('.commute-compare-status').inner_text())

    def test_verification_editor_saves_segments_and_resets_old_receipt_when_building_changes(self):
        self.page.locator('.university-route-entry > summary').click()
        self.page.get_by_label('Способ проверки · маршрут 1',exact=True).select_option('estimate')
        self.page.get_by_label('Дата проверки · маршрут 1',exact=True).fill('2026-11-01')
        self.page.get_by_label('Пешком от дома до транспорта, мин · маршрут 1',exact=True).fill('0')
        self.page.get_by_label('Количество пересадок · маршрут 1',exact=True).fill('2')
        self.page.get_by_label('Участки маршрута · маршрут 1',exact=True).fill('walk | 5 | Дом | Остановка |\nbus | 20 | Остановка | Корпус | 119')
        saved=self.page.evaluate('window.savedRoutes[0]')
        self.assertEqual(saved['method'],'estimate')
        self.assertEqual(saved['checkedAt'],'2026-11-01')
        self.assertEqual(saved['accessMinutes'],0)
        self.assertEqual(saved['transfers'],2)
        self.assertEqual(saved['segments'][1]['transport'],'119')
        self.assertIn('Оценка',self.page.locator('.university-route-previews').inner_text())
        self.page.get_by_label('Кампус из справочника · маршрут 1',exact=True).select_option('msu-first')
        saved=self.page.evaluate('window.savedRoutes[0]')
        self.assertEqual(saved['campusId'],'msu-first')
        self.assertEqual(saved['checkedAt'],'')
        self.assertEqual(saved['method'],'unknown')
        self.assertEqual(saved['segments'],[])


if __name__=='__main__':
    unittest.main()
