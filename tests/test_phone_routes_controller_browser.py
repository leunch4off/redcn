"""Delayed module/save and conflict checks for the phone wrapper, with no live API."""
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import unittest


ROOT=Path(__file__).resolve().parents[1]
HARNESS='''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/web/university-routes.css"><style>*{box-sizing:border-box}body{margin:0;font:16px system-ui}#mount{padding:12px;max-width:900px;margin:auto}.btn{font:inherit;padding:10px;white-space:normal}input{font:inherit;width:100%;max-width:100%}</style><div id="mount"></div><script type="module">
import {setupPhoneUniversityRoutes} from '/web/phone-routes-controller.mjs';
const copy=value=>JSON.parse(JSON.stringify(value)),A='a'.repeat(32),B='b'.repeat(32);
window.A=A;window.B=B;window.backend={};window.calls=[];window.loads=0;window.saved=[];window.deferModule=false;window.deferSave=false;window.forceConflict=false;
window.backend[A]={id:A,updated:10,title:'用户标题A',body:'用户正文A',fields:{universityRoutes:[{name:'МГУ',transport:'261'}]}};window.backend[B]={id:B,updated:50,title:'用户标题B',body:'用户正文B',fields:{universityRoutes:[{name:'ВШЭ',transport:'119'}]}};window.current=copy(window.backend[A]);
const fakeModule={setupUniversityRoutes({mount,getFields,onChange}){let rows=copy(getFields().universityRoutes||[]);const input=document.createElement('input');input.setAttribute('aria-label','Вуз в тестовой форме');mount.replaceChildren(input);const paint=()=>input.value=rows[0]?.name||'';paint();input.oninput=()=>{rows=[{...(rows[0]||{}),name:input.value}];onChange(copy(rows));};return {value:()=>copy(rows),refresh(fields){rows=copy(fields.universityRoutes||[]);paint();},destroy(){mount.replaceChildren();}};}};
async function loadModule(){window.loads++;if(window.deferModule){window.deferModule=false;await new Promise(resolve=>window.releaseModule=resolve);}return fakeModule;}
async function api(path,data){window.calls.push({path,data:copy(data??null)});const parts=path.split('/'),id=parts[1];if(parts[2]==='routes'){
 if(window.forceConflict){window.forceConflict=false;throw Object.assign(Error('Версия карточки изменена'),{status:409});}
 if(window.deferSave){window.deferSave=false;await new Promise(resolve=>window.releaseSave=resolve);}
 if(data.expectedUpdated!==window.backend[id].updated)throw Object.assign(Error('Conflict'),{status:409});
 window.backend[id].updated++;window.backend[id].fields.universityRoutes=copy(data.routes);return copy(window.backend[id]);
 }return copy(window.backend[id]);}
window.ctrl=setupPhoneUniversityRoutes({mount:document.getElementById('mount'),getCard:()=>window.current,api,onSaved:card=>{window.saved.push(copy(card));window.current=copy(card);window.ctrl.cardChanged(card);},loadModule});
window.switchCard=id=>{window.current=copy(window.backend[id]);window.ctrl.cardChanged(window.current);};window.ready=true;
</script></html>'''


class PhoneRoutesControllerBrowserAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise unittest.SkipTest('Optional phone routes acceptance needs Playwright/Chrome')
        cls.runtime=sync_playwright().start()
        try:cls.browser=cls.runtime.chromium.launch(channel='chrome',headless=True)
        except Exception as error:
            cls.runtime.stop();raise unittest.SkipTest(str(error).splitlines()[0])
        class Handler(SimpleHTTPRequestHandler):
            def __init__(self,*args,**kwargs):super().__init__(*args,directory=str(ROOT),**kwargs)
            def log_message(self,*args):pass
            def do_GET(self):
                if self.path=='/':
                    value=HARNESS.encode();self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(value)));self.end_headers();self.wfile.write(value)
                else:super().do_GET()
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start();cls.url='http://127.0.0.1:'+str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close();cls.runtime.stop();cls.server.shutdown();cls.server.server_close();cls.thread.join()

    def setUp(self):
        self.context=self.browser.new_context(viewport={'width':393,'height':852},has_touch=True);self.page=self.context.new_page();self.errors=[];self.page.on('pageerror',lambda error:self.errors.append(str(error)))
        self.page.goto(self.url,wait_until='domcontentloaded');self.page.wait_for_function('window.ready===true')

    def tearDown(self):
        self.context.close();self.assertEqual(self.errors,[])

    def open(self):
        self.page.locator('.phone-route-controller > summary').click();self.page.get_by_label('Вуз в тестовой форме',exact=True).wait_for()

    def field(self):return self.page.get_by_label('Вуз в тестовой форме',exact=True)

    def test_lazy_module_switch_and_collapse_preserve_correct_draft(self):
        self.assertEqual(self.page.evaluate('window.loads'),0);self.assertEqual(self.page.evaluate('window.calls'),[])
        self.page.evaluate('window.deferModule=true');self.page.locator('.phone-route-controller > summary').click()
        self.page.wait_for_function("typeof window.releaseModule==='function'")
        self.page.evaluate('window.switchCard(window.B);window.releaseModule();void 0')
        self.field().wait_for();self.assertEqual(self.field().input_value(),'ВШЭ');self.assertEqual(self.page.evaluate('window.loads'),1)
        self.field().fill('РУДН');self.page.locator('.phone-route-controller > summary').click();self.page.locator('.phone-route-controller > summary').click()
        self.field().wait_for();self.assertEqual(self.field().input_value(),'РУДН');self.field().fill('МГИМО')
        self.page.get_by_role('button',name='Сохранить маршруты',exact=True).click();self.page.wait_for_function('window.saved.length===1')
        self.assertEqual(self.page.evaluate('window.saved[0].id'),'b'*32);self.assertEqual(self.page.evaluate('window.saved[0].fields.universityRoutes[0].name'),'МГИМО')
        self.page.evaluate('window.switchCard(window.A)');self.assertEqual(self.field().input_value(),'МГУ')
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'),393)

    def test_edits_during_save_keep_new_draft_and_advance_only_its_cas_version(self):
        self.open();self.field().fill('РУДН');self.page.evaluate('window.deferSave=true')
        self.page.get_by_role('button',name='Сохранить маршруты',exact=True).click();self.page.wait_for_function("typeof window.releaseSave==='function'")
        self.field().fill('МГИМО');self.page.evaluate('window.releaseSave();void 0');self.page.wait_for_function('window.saved.length===1')
        self.assertEqual(self.field().input_value(),'МГИМО');self.assertTrue(self.page.get_by_role('button',name='Сохранить маршруты',exact=True).is_enabled())
        self.page.get_by_role('button',name='Сохранить маршруты',exact=True).click();self.page.wait_for_function('window.saved.length===2')
        sent=self.page.evaluate("window.calls.filter(call=>call.path.endsWith('/routes')).map(call=>call.data)")
        self.assertEqual([row['expectedUpdated']for row in sent],[10,11]);self.assertEqual(sent[1]['routes'][0]['name'],'МГИМО')
        self.assertEqual(self.page.evaluate('window.current.body'),'用户正文A')

    def test_409_rebase_is_explicit_read_and_never_submits_draft_automatically(self):
        self.open();self.field().fill('РУДН');self.page.evaluate("window.forceConflict=true;window.backend[window.A].updated=12;window.backend[window.A].fields.universityRoutes=[{name:'ВШЭ'}]")
        self.page.get_by_role('button',name='Сохранить маршруты',exact=True).click()
        retry=self.page.get_by_role('button',name='Обновить версию, сохранив мой черновик',exact=True);retry.wait_for();self.assertEqual(self.field().input_value(),'РУДН')
        retry.click();self.page.wait_for_function("document.querySelector('.phone-route-controller p').textContent.includes('Новая версия получена')")
        self.assertEqual(self.field().input_value(),'РУДН');self.assertEqual(self.page.evaluate("window.calls.filter(call=>call.path.endsWith('/routes')).length"),1)
        self.page.get_by_role('button',name='Сохранить маршруты',exact=True).click()
        self.page.wait_for_function("window.calls.filter(call=>call.path.endsWith('/routes')).length===2")
        self.assertEqual(self.page.evaluate("window.calls.filter(call=>call.path.endsWith('/routes'))[1].data.expectedUpdated"),12)

    def test_auth_clear_blocks_a_late_save_callback(self):
        self.open();self.field().fill('РУДН');self.page.evaluate('window.deferSave=true')
        self.page.get_by_role('button',name='Сохранить маршруты',exact=True).click();self.page.wait_for_function("typeof window.releaseSave==='function'")
        self.page.evaluate('window.current=null;window.ctrl.clear();window.releaseSave();void 0')
        self.page.wait_for_function('window.backend[window.A].updated===11')
        self.assertEqual(self.page.evaluate('window.saved'),[]);self.assertFalse(self.page.locator('.phone-route-controller').evaluate('element=>element.open'))
        self.assertEqual(self.page.locator('.phone-route-controller input').count(),0)


if __name__=='__main__':unittest.main()
