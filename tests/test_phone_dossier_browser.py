"""Phone dossier controls against synthetic responses at iPhone 14 Pro width."""
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import threading
import unittest

ROOT=Path(__file__).resolve().parents[1]
source=(ROOT/'web/phone.mjs').read_text()
helpers=source[source.index('function phoneDossierAccess()'):source.index('function phoneInitializeCardTools()')]
PAGE='''<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/web/dossier-transfer.css"><style>*{box-sizing:border-box}body{font:16px system-ui;margin:0;padding:12px}.hidden{display:none!important}.btn{padding:10px;border:1px solid #bbb;background:white;border-radius:8px}</style><main id="phoneDossierMount"></main><script type="module">
import {setupDossierTransfer} from '/web/dossier-transfer.mjs';
import {withRequestDeadline} from '/web/request-deadline.mjs';
const ph=id=>document.getElementById(id);let phoneDossierUI=null,phoneDossierCard=null,phoneDossierRole=null;
const phoneState={paired:true,authEpoch:1,csrf:'synthetic-csrf',detailId:'a'.repeat(32),access:{role:'editor'}};
window.requests=[];window.downloads=[];window.messages=[];
const cardDownloadBlob=(blob,name)=>downloads.push({type:blob.type,name});const phoneToast=text=>messages.push(text);
const phoneAuth=value=>{phoneState.paired=value;phoneState.authEpoch++;};const phoneEnsurePresentationCard=async()=>phoneState.detailId;
const phoneRefreshCards=async()=>{};const phoneOpenCard=async id=>{phoneState.detailId=id;phoneInitializeDossierTransfer();};
window.fetch=async(path,options)=>{requests.push({path,method:options.method,headers:options.headers,body:options.body});const payload=path.includes('dossier-options/')?{attachments:[{id:'b'.repeat(32),name:'plan.obj',category:'floorplan',shareable:true}],exports:[]}:path.endsWith('dossier-import')?{result_id:'c'.repeat(32),clientMaterialsRequireRegeneration:true}:{};return new Response(path.includes('dossier-backup/')||path.includes('dossier-client/')?'SYNTHETIC ZIP':JSON.stringify(payload),{status:200,headers:{'Content-Type':path.includes('dossier-backup/')||path.includes('dossier-client/')?'application/zip':'application/json'}});};
''' + helpers + '''
window.changeRole=role=>{phoneState.access.role=role;phoneInitializeDossierTransfer();};window.currentCard=()=>phoneState.detailId;phoneInitializeDossierTransfer();window.phoneDossierReady=true;
</script>'''

class PhoneDossierBrowser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:from playwright.sync_api import sync_playwright
        except ImportError:raise unittest.SkipTest('Phone dossier native acceptance requires Playwright and Chrome')
        cls.runtime=sync_playwright().start()
        try:cls.browser=cls.runtime.chromium.launch(channel='chrome',headless=True)
        except Exception as error:
            cls.runtime.stop();raise unittest.SkipTest('Chrome unavailable: '+str(error).splitlines()[0])
        class Handler(SimpleHTTPRequestHandler):
            def __init__(self,*args,**kwargs):super().__init__(*args,directory=str(ROOT),**kwargs)
            def log_message(self,*args):pass
            def do_GET(self):
                if self.path=='/':
                    value=PAGE.encode();self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(value)));self.end_headers();self.wfile.write(value)
                else:super().do_GET()
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start();cls.url='http://127.0.0.1:'+str(cls.server.server_port)
    @classmethod
    def tearDownClass(cls):cls.browser.close();cls.runtime.stop();cls.server.shutdown();cls.server.server_close();cls.thread.join()
    def setUp(self):
        self.context=self.browser.new_context(viewport={'width':393,'height':852});self.page=self.context.new_page();self.errors=[];self.page.on('pageerror',lambda error:self.errors.append(str(error)));self.page.goto(self.url);self.page.wait_for_function('phoneDossierReady===true')
    def tearDown(self):self.context.close();self.assertEqual(self.errors,[])
    def test_editor_has_explicit_client_choice_without_private_controls_and_viewer_has_no_panel(self):
        self.page.locator('summary').click();self.assertFalse(self.page.get_by_role('button',name='Скачать приватную ZIP-копию').is_visible())
        self.page.get_by_role('button',name='Выбрать файлы текущей квартиры').click();self.page.wait_for_selector('.dossier-transfer-choices input')
        self.assertEqual(self.page.locator('input:checked').count(),0);self.page.get_by_role('button',name='Скачать клиентский ZIP').click();self.page.wait_for_function('downloads.length===1')
        request=self.page.evaluate("requests.find(row=>row.path.includes('dossier-client/'))")
        self.assertEqual(request['method'],'POST');self.assertEqual(request['headers']['X-Phone-CSRF'],'synthetic-csrf');self.assertIn('"attachmentIds":[]',request['body'])
        self.page.evaluate("changeRole('viewer')");self.assertFalse(self.page.locator('#phoneDossierMount').is_visible());self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'),393)
    def test_owner_restore_uses_selected_file_and_opens_fresh_server_card(self):
        self.page.evaluate("changeRole('owner')");self.page.locator('summary').click()
        self.assertTrue(self.page.get_by_role('button',name='Скачать приватную ZIP-копию').is_visible())
        self.page.locator('input[type=file]').set_input_files({'name':'synthetic.zip','mimeType':'application/zip','buffer':b'fixture'})
        self.page.wait_for_function("currentCard()==='c'.repeat(32)");self.assertEqual(self.page.locator('input:checked').count(),0)
        self.assertEqual(self.page.evaluate("requests.filter(row=>row.path.endsWith('dossier-import')).length"),1)

if __name__=='__main__':unittest.main()
