"""Owned static Chrome fixture for private fact ledger; never starts imports."""
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import unittest


ROOT=Path(__file__).resolve().parents[1]
HARNESS='''<!doctype html><html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/web/fact-ledger.css"><style>*{box-sizing:border-box}body{margin:0;padding:12px;font:16px system-ui}main{max-width:900px;margin:auto}.btn{padding:10px;border:1px solid #ddd;border-radius:8px;background:white}#price{font:inherit;padding:10px;max-width:100%}</style><main><label>Аренда<input id="price"></label><button id="import">Новый снимок источника</button><div id="mount"></div></main><script type="module">
import {setupFactLedger} from '/web/fact-ledger.mjs';
const read=(key,fallback)=>JSON.parse(localStorage.getItem(key)||'null')??fallback;
const write=(key,value)=>{localStorage.setItem(key,JSON.stringify(value));return true;};
window.fields=read('fact-browser-state',{editorIdentity:'test-a',url:'https://www.cian.ru/rent/flat/1/',price:'80000',deposit:'40000',features:['fridge'],sourceAudit:{fields:[{field:'price',value:'80000',source:'JSON-LD',text:'80 000 ₽',sourceUrl:'https://www.cian.ru/rent/flat/1/'}],furniture:[{id:'fridge',label:'Холодильник',status:'present',matches:[{source:'Описание',text:'Есть холодильник'}]}]}});
const save=()=>write('fact-browser-state',fields);window.messages=[];window.serverCacheCalls=0;
window.ledger=setupFactLedger({mount:document.getElementById('mount'),read,write,getFields:()=>fields,onApplyField:(key,value)=>{fields[key]=String(value);document.getElementById('price').value=fields.price;save();},clearServerCache:async()=>{serverCacheCalls++;return {cleared:3,scope:'import-cache'};},toast:message=>messages.push(message)});
document.getElementById('price').value=fields.price;
document.getElementById('price').oninput=event=>{fields.price=event.target.value;save();};
document.getElementById('import').onclick=()=>{ledger.observe('import','Источник ЦИАН');save();};
window.factReady=true;
</script></html>'''


class FactLedgerBrowserAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise unittest.SkipTest('Optional fact-ledger acceptance needs Playwright and Chrome')
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
    def tearDownClass(cls):
        cls.browser.close();cls.runtime.stop();cls.server.shutdown();cls.server.server_close();cls.thread.join()

    def setUp(self):
        self.context=self.browser.new_context(viewport={'width':393,'height':852});self.page=self.context.new_page();self.errors=[];self.page.on('pageerror',lambda error:self.errors.append(str(error)))
        self.page.goto(self.url,wait_until='domcontentloaded');self.page.wait_for_function('window.factReady===true');self.page.locator('#mount>details>summary').click()

    def tearDown(self):
        self.context.close();self.assertEqual(self.errors,[])

    def test_two_source_snapshots_price_conditions_diff_author_and_reload(self):
        self.page.locator('#import').click();self.page.locator('#price').fill('85000');self.page.evaluate("fields.petPolicy='yes'");self.page.locator('#import').click()
        self.page.get_by_label('Версия фактов для сравнения',exact=True).select_option(index=1)
        text=self.page.locator('.fact-ledger-diff').inner_text();self.assertIn('80000 → 85000',text)
        self.assertIn('Источник ЦИАН',self.page.get_by_label('Версия фактов для сравнения').inner_text())
        self.page.reload();self.page.wait_for_function('window.factReady===true');self.page.locator('#mount>details>summary').click()
        self.assertEqual(self.page.get_by_label('Версия фактов для сравнения').locator('option').count(),2)
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'),394)

    def test_explicit_verification_is_dated_and_invalidated_on_change_and_conflict_restore_is_explicit(self):
        row=self.page.locator('tbody tr').filter(has=self.page.locator('th',has_text='Аренда')).first
        self.assertIn('Не подтверждено',row.inner_text());row.get_by_role('button',name='Подтвердить текущий факт').click();self.assertIn('Оператор Mac',row.inner_text())
        self.page.locator('#price').fill('90000');self.page.get_by_role('button',name='Обновить версии и сравнение').click()
        self.assertIn('Ручное значение отличается',self.page.locator('.fact-ledger-conflict').inner_text());self.assertIn('Не подтверждено',row.inner_text())
        self.assertEqual(self.page.locator('#price').input_value(),'90000')
        self.page.get_by_role('button',name='Принять Аренда: 80000',exact=True).click();self.assertEqual(self.page.locator('#price').input_value(),'80000')
        self.assertEqual(self.page.locator('a',has_text='Открыть источник').first.get_attribute('href'),'https://www.cian.ru/rent/flat/1/')

    def test_stale_confirmation_is_rejected_and_cache_clear_preserves_private_stores(self):
        row=self.page.locator('tbody tr').filter(has=self.page.locator('th',has_text='Аренда')).first
        self.page.locator('#price').fill('90000');row.get_by_role('button',name='Подтвердить текущий факт').click()
        self.assertIn('Факт изменился',self.page.evaluate('messages.at(-1)'))
        self.page.evaluate("localStorage.setItem('most-cache',JSON.stringify({fixture:{text:'cached'}}));localStorage.setItem('most-objects',JSON.stringify([{id:'keep'}]));localStorage.setItem('most-rules',JSON.stringify({Москва:'莫斯科'}));")
        self.page.get_by_text('Очистить только кеш импорта',exact=True).click()
        self.page.get_by_role('button',name='Очистить кеш этого браузера',exact=True).click();self.assertIn('Подтвердите',self.page.evaluate('messages.at(-1)'))
        self.page.get_by_label('Подтверждаю очистку только кеша импорта').check();self.page.get_by_role('button',name='Очистить кеш этого браузера',exact=True).click()
        self.assertEqual(self.page.evaluate("JSON.parse(localStorage.getItem('most-cache'))"),{})
        self.assertEqual(self.page.evaluate("JSON.parse(localStorage.getItem('most-objects'))[0].id"),'keep')
        self.assertEqual(self.page.evaluate("JSON.parse(localStorage.getItem('most-rules')).Москва"),'莫斯科')
        self.page.get_by_role('button',name='Очистить кеш исходных страниц на Mac',exact=True).click();self.assertEqual(self.page.evaluate('serverCacheCalls'),1)

    def test_service_confirmation_receipt_survives_reload_and_changed_scope_requires_new_confirmation(self):
        registration=self.page.get_by_label('Подтвердить услугу: Содействие регистрации по прибытии',exact=True)
        self.assertFalse(self.page.evaluate('ledger.checkServices().ready'))
        registration.check();self.assertTrue(self.page.evaluate('ledger.checkServices().ready'))
        receipt=self.page.evaluate('ledger.serviceReceipts().registration');self.assertEqual(receipt['actor'],'Оператор Mac');self.assertIn('T',receipt['at'])
        self.page.reload();self.page.wait_for_function('window.factReady===true');self.page.locator('#mount>details>summary').click();self.assertTrue(registration.is_checked())
        self.page.evaluate("fields.registrationPeople='2';ledger.refresh()")
        self.assertFalse(registration.is_checked());self.assertFalse(self.page.evaluate('ledger.checkServices().ready'))


if __name__=='__main__':unittest.main()
