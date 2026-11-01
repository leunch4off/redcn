"""Explicit cloud setup/recovery consent, with generated UI-only API responses."""
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import threading
import unittest
ROOT=Path(__file__).resolve().parents[1]
HARNESS='''<!doctype html><html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>*{box-sizing:border-box}body{font:16px system-ui;margin:10px}button,input,select{font:inherit;max-width:100%;min-height:44px}button{white-space:normal}label{display:block}</style><div id="mount"></div><script type="module">
import{setupCloudBackup}from'/web/cloud-backup.mjs';window.calls=[];window.downloads=[];let state={version:1,configured:false,enabled:false};
window.api=async(path,data)=>{window.calls.push({path,data});if(path==='cloud-backup'){if(data?.action==='configure')state={version:1,configured:true,enabled:false,recoveryAcknowledged:false};if(data?.action==='enable')state={...state,enabled:data.enabled,recoveryAcknowledged:data.recoveryAcknowledged};return state;}if(path==='cloud-backup/upload')return{stored:true,id:'a'.repeat(32)};if(path==='cloud-backup/list')return{items:[{id:'a'.repeat(32),bytes:31287,uploaded:'2026-11-01T18:00:00Z'}]};if(path==='cloud-backup/restore')return{cards:[{}],devicesMustPairAgain:true};throw Error('Unexpected API');};window.ui=setupCloudBackup({mount:document.getElementById('mount'),api:window.api,getBlob:async()=>new Blob(['synthetic-recovery-only']),downloadBlob:(blob,name)=>window.downloads.push(name)});window.ready=true;</script></html>'''
class CloudBackupBrowserTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  try:from playwright.sync_api import sync_playwright
  except ImportError:raise unittest.SkipTest('Cloud backup UI acceptance needs optional Playwright/Chrome')
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
 def tearDownClass(cls):cls.browser.close();cls.runtime.stop();cls.server.shutdown();cls.server.server_close();cls.thread.join()
 def setUp(self):
  self.context=self.browser.new_context(viewport={'width':393,'height':852});self.page=self.context.new_page();self.errors=[];self.page.on('pageerror',lambda e:self.errors.append(str(e)));self.page.goto(self.url,wait_until='domcontentloaded');self.page.wait_for_function('window.ready===true')
 def tearDown(self):self.context.close();self.assertEqual(self.errors,[])
 def test_operator_configures_clears_passwords_saves_recovery_optin_and_restores_new_folder(self):
  self.page.locator('summary').click();self.page.get_by_role('button',name='Подключить приватное облако',exact=True).wait_for()
  self.assertTrue(self.page.get_by_role('button',name='Скопировать базу в облако сейчас',exact=True).is_disabled())
  self.page.get_by_label('Сервисный доступ приватного облака').fill('synthetic-service-only');self.page.get_by_label('Ключ первичного подключения облака').fill('synthetic-registration-only')
  self.page.get_by_role('button',name='Подключить приватное облако',exact=True).click();self.page.wait_for_function("document.querySelector('[role=status]').textContent.includes('Сохраните ключ')")
  self.assertEqual(self.page.get_by_label('Сервисный доступ приватного облака').input_value(),'');self.assertEqual(self.page.get_by_label('Ключ первичного подключения облака').input_value(),'')
  self.page.get_by_role('button',name='Скачать отдельный ключ восстановления',exact=True).click();self.page.wait_for_function('window.downloads.length===1')
  self.page.get_by_label('Я сохранил отдельный ключ восстановления вне этого Mac').check();self.page.get_by_label('Разрешаю копирование приватной базы в это облако').check();self.page.get_by_role('button',name='Сохранить разрешение',exact=True).click()
  self.page.wait_for_function("window.calls.some(r=>r.data?.action==='enable'&&r.data.enabled===true&&r.data.recoveryAcknowledged===true)")
  self.page.get_by_role('button',name='Скопировать базу в облако сейчас',exact=True).click();self.page.wait_for_function("window.calls.some(r=>r.path==='cloud-backup/upload')")
  self.page.get_by_role('button',name='Показать облачные копии',exact=True).click();self.page.get_by_label('Выбранная облачная копия').select_option('a'*32);self.page.get_by_label('Новая папка восстановления облачной копии').fill('/tmp/redcn-generated-new-workspace')
  self.page.get_by_role('button',name='Восстановить в новую папку',exact=True).click();self.page.wait_for_function("document.querySelector('[role=status]').textContent.includes('Восстановлено карточек: 1')")
  self.assertEqual(self.page.evaluate('localStorage.length'),0);self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'),393)
if __name__=='__main__':unittest.main()
