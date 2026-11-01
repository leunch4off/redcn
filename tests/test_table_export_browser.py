"""Static operator export fixture with an in-memory XLSX endpoint, no live cache."""
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import sys
import threading
import unittest
from zipfile import ZipFile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'cli'))
from table_export import export_table

HARNESS='''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/web/table-export.css"><style>*{box-sizing:border-box}body{margin:0;padding:12px;font:16px system-ui;color:#243b2d}main{max-width:1100px;margin:auto;min-width:0}.btn{padding:9px;border:1px solid #d7dfd5;border-radius:7px;background:white}input,select{padding:8px;font:inherit;border:1px solid #d7dfd5;border-radius:6px}.muted{color:#687466}</style><main><div id="mount"></div></main><script type="module">
import {setupTableExport} from '/web/table-export.mjs';
const cards=[{id:'a',title:'=SUM(1) 房源 +7 (999) 123-45-67',url:'https://www.cian.ru/rent/flat/123/?auth=PRIVATE_QUERY',district:'Лианозово',rooms:'2',price:'80000',pricePeriod:'month',priceCurrency:'RUB',deposit:'40000',commission:'50',source:'PRIVATE_HTML',notes:'PRIVATE_NOTE',workflow:{status:'ready'}},{id:'b',title:'Другая квартира',district:'Хамовники',rooms:'1',price:'50000',deposit:0,commission:0},{id:'daily',title:'Посуточная',district:'Лианозово',rooms:'2',price:3500,pricePeriod:'day',priceCurrency:'RUB',deposit:0,commission:0}];
setupTableExport({mount:document.getElementById('mount'),getObjects:()=>cards,getXlsx:async rows=>{const result=await fetch('/table-export',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({format:'xlsx',rows})});if(!result.ok)throw Error('Ошибка XLSX');return result.blob();},toast:text=>window.tableMessage=text});window.tableReady=true;
</script></html>'''


class TableExportBrowserAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise unittest.SkipTest('Optional table export acceptance needs Playwright and Chrome')
        cls.runtime=sync_playwright().start()
        try:
            cls.browser=cls.runtime.chromium.launch(channel='chrome',headless=True)
        except Exception as error:
            cls.runtime.stop()
            raise unittest.SkipTest('Optional Chrome unavailable: '+str(error).splitlines()[0])

        class Handler(SimpleHTTPRequestHandler):
            def __init__(self,*args,**kwargs):super().__init__(*args,directory=str(ROOT),**kwargs)
            def log_message(self,*args):pass
            def do_GET(self):
                if self.path=='/':
                    body=HARNESS.encode();self.send_response(200);self.send_header('Content-Type','text/html;charset=utf-8');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
                else:super().do_GET()
            def do_POST(self):
                if self.path!='/table-export':self.send_error(404);return
                data=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                content,mime,name=export_table(data);self.send_response(200);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(content)));self.end_headers();self.wfile.write(content)
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start();cls.url='http://127.0.0.1:'+str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close();cls.runtime.stop();cls.server.shutdown();cls.server.server_close();cls.thread.join()

    def setUp(self):
        self.context=self.browser.new_context(viewport={'width':393,'height':852},accept_downloads=True);self.page=self.context.new_page();self.errors=[];self.page.on('pageerror',lambda error:self.errors.append(str(error)));self.page.goto(self.url,wait_until='domcontentloaded');self.page.wait_for_function('window.tableReady===true');self.page.locator('summary').click()

    def tearDown(self):
        self.context.close();self.assertEqual(self.errors,[])

    def filters(self):
        self.page.get_by_label('Аренда от, ₽ / месяц',exact=True).fill('70000');self.page.get_by_label('Аренда до, ₽ / месяц',exact=True).fill('90000');self.page.get_by_label('Район содержит',exact=True).fill('Лиан');self.page.get_by_label('Комнат · точно',exact=True).fill('2');self.page.get_by_label('Рабочий статус выгрузки',exact=True).select_option('ready')

    def test_filters_preview_and_csv_download_use_only_matching_safe_rows(self):
        self.filters();self.assertIn('Выбрано 1 из 3',self.page.locator('[role=status]').inner_text())
        with self.page.expect_download() as event:self.page.get_by_role('button',name='Скачать внутренний CSV',exact=True).click()
        content=Path(event.value.path()).read_text(encoding='utf-8-sig')
        self.assertIn("'=SUM(1)",content);self.assertIn('телефон скрыт',content);self.assertIn('160000',content);self.assertNotIn('PRIVATE',content);self.assertNotIn('Другая квартира',content);self.assertNotIn('Посуточная',content)
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'),394)

    def test_xlsx_download_is_real_office_zip_with_unicode_and_separate_payment_columns(self):
        self.filters()
        with self.page.expect_download() as event:self.page.get_by_role('button',name='Скачать внутренний XLSX',exact=True).click()
        content=Path(event.value.path()).read_bytes();self.assertTrue(content.startswith(b'PK'))
        with ZipFile(io.BytesIO(content)) as archive:
            text=archive.read('xl/worksheets/sheet1.xml').decode()
        self.assertIn('Комиссия, %',text);self.assertIn('Валюта залога',text);self.assertIn('160000.00',text);self.assertNotIn('PRIVATE',text);self.assertNotIn('<f>',text);self.assertIn('телефон скрыт',text)


if __name__=='__main__':unittest.main()
