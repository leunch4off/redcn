"""Operator chooses a synthetic own browser; OS delivery is stubbed, IDB is real."""
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import threading
import unittest
ROOT=Path(__file__).resolve().parents[1]
HARNESS='''<!doctype html><html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>*{box-sizing:border-box}body{font:16px system-ui;margin:10px}button,input{font:inherit;max-width:100%;min-height:44px}button{white-space:normal}input{display:block}</style><div id="mount"></div><script type="module">
import{setupDeviceNotifications,createDeviceDeliveryStore}from'/web/device-notifications.mjs';
window.permissionRequests=0;window.shown=[];window.apiCalls=[];
window.Notification=class{static permission='default';static async requestPermission(){window.permissionRequests++;this.permission='granted';return'granted';}constructor(title,options){window.shown.push({title,tag:options.tag});}close(){}};
window.api=async path=>{window.apiCalls.push(path);if(path==='device-delivery/context')return{version:1,workspaceId:'a'.repeat(64),channel:'this-browser'};if(path==='listing-watch/notifications')return{items:[{notificationId:'b'.repeat(32),title:'合成新房源',read:false,created:123}]};throw Error('Unexpected API');};
window.ui=setupDeviceNotifications({mount:document.getElementById('mount'),api:window.api});window.createStore=createDeviceDeliveryStore;window.ready=true;</script></html>'''
class DeviceNotificationsBrowserTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  try:from playwright.sync_api import sync_playwright
  except ImportError:raise unittest.SkipTest('Browser notification acceptance needs optional Playwright/Chrome')
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
 def test_operator_selects_own_browser_permission_and_duplicate_and_disable_are_enforced(self):
  self.assertEqual(self.page.evaluate('window.permissionRequests'),0);self.assertEqual(self.page.evaluate('window.shown.length'),0)
  self.page.locator('summary').click();self.page.get_by_label('Название получателя уведомлений').fill('Мой тестовый телефон')
  self.page.get_by_role('button',name='Выбрать этот браузер и включить',exact=True).click();self.page.wait_for_function('window.shown.length===1')
  self.assertEqual(self.page.evaluate('window.permissionRequests'),1)
  self.page.get_by_role('button',name='Выбрать этот браузер и включить',exact=True).click();self.page.wait_for_function('window.permissionRequests===2')
  self.page.wait_for_function("document.querySelector('[role=status]').textContent.includes('Получатель')")
  self.assertEqual(self.page.evaluate('window.shown.length'),1)
  self.page.get_by_role('button',name='Выключить доставку',exact=True).click();self.assertIn('выключена',self.page.get_by_role('status').inner_text())
  setting=self.page.evaluate("async()=>{const s=window.createStore();try{return await s.setting('a'.repeat(64));}finally{s.close();}}")
  self.assertFalse(setting['enabled']);self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'),393)
 def test_real_indexeddb_transaction_has_one_claim_across_concurrent_stores_and_owner_scopes(self):
  result=self.page.evaluate("""async()=>{const a=window.createStore(),b=window.createStore();try{const rows=await Promise.all([a.claim('a'.repeat(64),'c'.repeat(32),1),b.claim('a'.repeat(64),'c'.repeat(32),1)]);const other=await b.claim('d'.repeat(64),'c'.repeat(32),1);return{claims:rows.filter(Boolean).length,other:!!other};}finally{a.close();b.close();}}""")
  self.assertEqual(result,{'claims':1,'other':True})
if __name__=='__main__':unittest.main()
