"""Isolated mobile DOM acceptance; no import controller or user files."""
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import threading
import unittest

ROOT=Path(__file__).resolve().parents[1]
PAGE='''<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/web/dossier-transfer.css"><style>*{box-sizing:border-box}body{font:16px system-ui;margin:0;padding:12px}.btn{padding:10px;border:1px solid #ccd4c5;background:white;border-radius:8px}main{max-width:900px;margin:auto}</style><main id="mount"></main><script type="module">
import {setupDossierTransfer} from '/web/dossier-transfer.mjs';
window.testCardId='f'.repeat(32);window.testRequests=[];window.testDownloads=[];window.testRestores=[];window.testMessages=[];
window.testOptions={attachments:[{id:'a'.repeat(32),name:'floorplan.obj',category:'floorplan',shareable:true},{id:'b'.repeat(32),name:'contract.pdf',category:'contract',shareable:false}],exports:[{id:'c'.repeat(32),name:'current.html',requiresRegeneration:false},{id:'d'.repeat(32),name:'old.pdf',requiresRegeneration:true}]};
window.ui=setupDossierTransfer({mount:document.getElementById('mount'),getCardId:()=>testCardId,ensureCard:async()=>testCardId,api:async(path,data)=>{testRequests.push({path,data});if(path.startsWith('dossier-options/'))return testOptions;if(path==='dossier-import')return {result_id:'e'.repeat(32),restoredDesign:true,clientMaterialsRequireRegeneration:true};if(path==='photo-duplicates')return {groups:[],cardsChecked:2,truncated:false};if(path.startsWith('dossier-design/'))return {template:{version:1,preset:'minimal'}};throw Error('Unexpected route')},getBlob:async(path,data)=>{testRequests.push({path,data});return new Blob(['fixture-zip'],{type:'application/zip'})},downloadBlob:(blob,name)=>testDownloads.push({name,size:blob.size}),onRestored:async result=>{testRestores.push(result);window.testCardId=result.result_id},toast:message=>testMessages.push(message)});window.testReady=true;
</script>'''


class DossierTransferBrowser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:from playwright.sync_api import sync_playwright
        except ImportError:raise unittest.SkipTest('Optional dossier DOM acceptance requires Playwright and Chrome')
        cls.runtime=sync_playwright().start()
        try:cls.browser=cls.runtime.chromium.launch(channel='chrome',headless=True)
        except Exception as error:
            cls.runtime.stop();raise unittest.SkipTest('Optional Chrome unavailable: '+str(error).splitlines()[0])
        class Handler(SimpleHTTPRequestHandler):
            def __init__(self,*args,**kwargs):super().__init__(*args,directory=str(ROOT),**kwargs)
            def log_message(self,*args):pass
            def do_GET(self):
                if self.path=='/':
                    value=PAGE.encode();self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(value)));self.end_headers();self.wfile.write(value)
                else:super().do_GET()
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start();cls.url='http://127.0.0.1:'+str(cls.server.server_port)
    @classmethod
    def tearDownClass(cls):
        cls.browser.close();cls.runtime.stop();cls.server.shutdown();cls.server.server_close();cls.thread.join()
    def setUp(self):
        self.context=self.browser.new_context(viewport={'width':393,'height':852});self.page=self.context.new_page();self.errors=[];self.page.on('pageerror',lambda error:self.errors.append(str(error)));self.page.goto(self.url);self.page.wait_for_function('testReady===true');self.page.locator('summary').click()
    def tearDown(self):self.context.close();self.assertEqual(self.errors,[])
    def test_client_selection_starts_empty_and_excludes_contracts_and_uncertified_files(self):
        self.page.get_by_role('button',name='Выбрать файлы текущей квартиры').click()
        self.page.wait_for_selector('.dossier-transfer-choices input')
        boxes=self.page.locator('.dossier-transfer-choices input')
        self.assertEqual(boxes.count(),4);self.assertEqual(self.page.locator('.dossier-transfer-choices input:checked').count(),0)
        self.assertTrue(boxes.nth(1).is_disabled());self.assertTrue(boxes.nth(3).is_disabled())
        boxes.nth(0).check();boxes.nth(2).check();self.page.get_by_role('button',name='Скачать клиентский ZIP').click()
        self.page.wait_for_function('testDownloads.length===1')
        payload=self.page.evaluate("testRequests.find(row=>row.path.startsWith('dossier-client/')).data")
        self.assertEqual(payload,{'attachmentIds':['a'*32],'exportIds':['c'*32]})
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'),393)
    def test_private_restore_uses_an_explicit_file_and_opens_new_identity_without_reusing_selection(self):
        self.page.get_by_role('button',name='Выбрать файлы текущей квартиры').click();self.page.wait_for_selector('.dossier-transfer-choices input')
        self.page.locator('input[type=file]').set_input_files({'name':'private.zip','mimeType':'application/zip','buffer':b'fixture'})
        self.page.wait_for_function('testRestores.length===1')
        self.assertEqual(self.page.evaluate('testCardId'),'e'*32)
        self.assertTrue(self.page.get_by_role('button',name='Скачать клиентский ZIP').is_disabled())
        self.assertEqual(self.page.locator('.dossier-transfer-choices input').count(),0)
        self.page.get_by_role('button',name='Скачать восстановленный шаблон дизайна').click();self.page.wait_for_function('testDownloads.length===1')
        self.assertTrue(self.page.evaluate('testDownloads[0].name').endswith('.json'))
    def test_private_backup_and_duplicate_check_are_explicit_actions(self):
        self.assertEqual(self.page.evaluate('testRequests.length'),0)
        self.page.get_by_role('button',name='Скачать приватную ZIP-копию').click();self.page.wait_for_function('testDownloads.length===1')
        self.assertTrue(self.page.evaluate('testDownloads[0].name').startswith('redcn-private-'))
        self.page.get_by_role('button',name='Найти точные дубликаты фото между карточками').click();self.page.wait_for_function("testRequests.some(row=>row.path==='photo-duplicates')")
        self.assertIn('Ничего не удалено',self.page.locator('[role=status]').inner_text())


if __name__=='__main__':unittest.main()
