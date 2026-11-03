"""Disposable native Chrome role-management fixture; never opens paired devices."""
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import threading
import unittest

ROOT=Path(__file__).resolve().parents[1]
HARNESS='''<!doctype html><html lang="ru"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/web/access-roles.css"><div id="roles"></div><script type="module">
import {setupAccessRoles} from '/web/access-roles.mjs';
window.calls=[];window.state={revision:1,sessions:[{id:'a'.repeat(64),role:'editor',label:'Телефон коллеги',expires:1900000000,created:1800000000}]};window.old=false;window.conflict=false;
window.panel=setupAccessRoles({mount:document.getElementById('roles'),api:async(path,data)=>{window.calls.push({path,data});if(window.old)throw Error('Не найдено');if(!data)return structuredClone(window.state);if(window.conflict)throw Error('Права устройств изменились. Обновите список');if(data.expectedRevision!==window.state.revision)throw Error('Версия изменилась');window.state.revision++;if(data.action==='revoke')window.state.sessions=[];else Object.assign(window.state.sessions[0],{role:data.role,label:data.label});return structuredClone(window.state);}});window.ready=true;
</script></html>'''


class AccessRolesBrowserAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:from playwright.sync_api import sync_playwright
        except ImportError:raise unittest.SkipTest('Optional role-management acceptance needs Playwright/Chrome')
        cls.runtime=sync_playwright().start()
        try:cls.browser=cls.runtime.chromium.launch(channel='chrome',headless=True)
        except Exception as error:cls.runtime.stop();raise unittest.SkipTest(str(error).splitlines()[0])
        class Handler(SimpleHTTPRequestHandler):
            def __init__(self,*args,**kwargs):super().__init__(*args,directory=str(ROOT),**kwargs)
            def log_message(self,*args):pass
            def do_GET(self):
                if self.path!='/':return super().do_GET()
                value=HARNESS.encode();self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(value)));self.end_headers();self.wfile.write(value)
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start();cls.url='http://127.0.0.1:'+str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):cls.browser.close();cls.runtime.stop();cls.server.shutdown();cls.server.server_close();cls.thread.join()

    def setUp(self):
        self.context=self.browser.new_context(viewport={'width':393,'height':852});self.page=self.context.new_page();self.errors=[];self.page.on('pageerror',lambda error:self.errors.append(str(error)));self.page.goto(self.url,wait_until='domcontentloaded');self.page.wait_for_function('window.ready === true')

    def tearDown(self):self.context.close();self.assertEqual(self.errors,[])

    def open(self):self.page.locator('summary').click();self.page.get_by_role('combobox').wait_for()

    def test_explicit_role_label_save_and_revocation_use_current_revision(self):
        self.open();self.page.get_by_role('combobox').select_option('viewer');self.page.get_by_role('textbox').fill('Второй iPhone');self.page.get_by_role('button',name='Сохранить права').click();self.page.wait_for_function("window.state.revision===2 && window.state.sessions[0].role==='viewer'");self.assertEqual(self.page.evaluate('window.calls.at(-1).data.expectedRevision'),1)
        self.page.get_by_role('button',name='Отключить устройство').click();self.page.wait_for_function('window.state.sessions.length===0');self.assertIn('Устройство отключено',self.page.get_by_role('status').inner_text());self.assertFalse(self.page.evaluate('document.documentElement.scrollWidth>innerWidth'))

    def test_conflict_requires_explicit_refresh_and_never_replays_write(self):
        self.open();self.page.evaluate('window.conflict=true');self.page.get_by_role('combobox').select_option('owner');self.page.get_by_role('button',name='Сохранить права').click();self.page.wait_for_function("document.querySelector('[role=status]').textContent.includes('Обновите список')");self.assertEqual(self.page.evaluate('window.calls.filter(row=>row.data).length'),1);self.assertEqual(self.page.evaluate('window.state.sessions[0].role'),'editor');self.assertEqual(self.page.get_by_role('combobox').count(),0)
        self.page.evaluate('window.conflict=false');self.page.get_by_role('button',name='Обновить список').click();self.page.get_by_role('combobox').wait_for();self.assertEqual(self.page.get_by_role('combobox').input_value(),'editor')

    def test_old_server_is_reported_unavailable_without_fabricated_session_grants(self):
        self.page.evaluate('window.old=true');self.page.locator('summary').click();self.page.wait_for_function("document.querySelector('[role=status]').textContent.includes('роли недоступны')");self.assertEqual(self.page.get_by_role('combobox').count(),0);self.assertEqual(self.page.evaluate('window.calls.filter(row=>row.data).length'),0)


if __name__=='__main__':unittest.main()
