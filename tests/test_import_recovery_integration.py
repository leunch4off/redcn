"""Real loopback/cache/controller replay; no remote listing or model is used."""
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
import local_server
import phone_relay
import redcn
from source_adapters import adapter_for
from photo_ai_settings import DEFAULTS

URL='https://www.cian.ru/rent/flat/123456789/'


class ImportRecoveryIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        (self.root/'photo-ai.json').write_text(json.dumps(DEFAULTS))
        (self.root/'text-ai.json').write_text(json.dumps({'enabled':False,'advertisingEnabled':False}))
        listing={'url':URL,'text':'Сдаётся квартира. 2 комнаты. Площадь 52 м². 80000 руб. в месяц.',
                 'fields':{'price':'80000','area':'52','rooms':'2','layout':'two','features':[]},
                 'images':[],'parserVersion':adapter_for(URL)['parserVersion']}
        with redcn.open_cache(self.root) as db:db.execute('INSERT INTO cache VALUES (?,?,?)',(URL,time.time(),json.dumps(listing)))
        self.start()
    def start(self):
        self.server=local_server.create_server(self.root,0)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base='http://127.0.0.1:'+str(self.server.server_port)
    def stop(self):self.server.shutdown();self.server.server_close();self.thread.join(3)
    def tearDown(self):self.stop();self.temp.cleanup()
    def request(self,path,data=None,authenticated=True):
        headers={'X-Redcn-Token':self.server.desktop_access.token} if authenticated else {}
        if data is not None:headers['Content-Type']='application/json'
        request=urllib.request.Request(self.base+'/api/local/'+path,headers=headers,data=json.dumps(data).encode() if data is not None else None)
        try:
            with urllib.request.urlopen(request,timeout=15) as response:return response.status,json.loads(response.read())
        except urllib.error.HTTPError as error:return error.code,json.loads(error.read())
    def completed(self,job_id):
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            status,job=self.request('jobs/'+job_id)
            self.assertEqual(status,200)
            if job['state']!='running':return job
            time.sleep(.02)
        self.fail('Isolated cached import did not complete')

    def test_identical_request_across_restart_keeps_one_result_and_manual_revision(self):
        data={'url':URL,'loader':'http','requestId':'integration-request-0001'}
        status,start=self.request('import',data);self.assertEqual(status,202,start)
        job=self.completed(start['job_id']);self.assertEqual(job['state'],'done',job)
        card_id=job['result_id'];card=local_server.read_result(self.root,card_id);card['body']='手动修改不会被重试覆盖。'
        local_server.save_result(self.root,card,card_id)
        self.stop();self.start()
        status,replayed=self.request('import',data);self.assertEqual(status,202,replayed)
        self.assertEqual(replayed['job_id'],start['job_id'])
        restored=self.completed(replayed['job_id']);self.assertEqual(restored['result_id'],card_id)
        self.assertEqual(local_server.read_result(self.root,card_id)['body'],card['body'])
        with sqlite3.connect(self.root/'listings.sqlite3') as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM results').fetchone()[0],1)
        status,conflict=self.request('import',{**data,'refresh':True});self.assertEqual(status,409)
        self.assertEqual(conflict['code'],'IMPORT_REQUEST_CONFLICT')

    def test_recovered_partial_job_is_terminal_and_preserves_edited_text(self):
        self.stop()
        from import_job_journal import ImportJobJournal
        old=ImportJobJournal(self.root)
        row=old.reserve({'url':URL},actor='Mac owner',request_id='interrupted-request-0001')
        local_server.save_result(self.root,{'fields':{'price':'80000'},'titles':['标题'],'body':'手工修改','tags':'','mediaStatus':'loading'},row['resultId'])
        old.progress(row['jobId'],'vision');old.close()
        self.start()
        status,recovered=self.request('jobs/'+row['jobId']);self.assertEqual(status,200)
        self.assertEqual(recovered['state'],'interrupted');self.assertTrue(recovered['recovery']['available'])
        self.assertEqual(recovered['result_id'],row['resultId'])
        local_server.mark_media_interrupted(self.root,row['resultId'],cancelled=True)
        value=local_server.read_result(self.root,row['resultId'])
        self.assertEqual(value['body'],'手工修改');self.assertEqual(value['mediaStatus'],'cancelled')

    def test_release_diagnostics_requires_owner_and_preview_is_not_an_export_side_effect(self):
        self.assertEqual(self.request('release-diagnostics',authenticated=False)[0],403)
        self.assertEqual(self.request('release-diagnostics',{'action':'preview'},authenticated=False)[0],403)
        status,value=self.request('release-diagnostics',{'action':'status','clientBuild':'0'*64})
        self.assertEqual(status,200,value);self.assertTrue(value['readOnly']);self.assertEqual(value['serverRelease'],'v32')
        self.assertFalse((self.root/'import-diagnostics/tg.zip').exists())

    def test_phone_retry_uses_original_intent_after_mac_profile_changes(self):
        phone=phone_relay.create_phone_server(self.root,self.server,0,'127.0.0.1',hosts={'127.0.0.1'})
        thread=threading.Thread(target=phone.serve_forever,daemon=True);thread.start()
        base='http://127.0.0.1:'+str(phone.server_port)+'/api/phone/'
        cookie=csrf=None
        def call(path,data):
            headers={'Content-Type':'application/json'}
            if cookie:headers.update(Cookie=cookie,**{'X-Phone-CSRF':csrf})
            request=urllib.request.Request(base+path,headers=headers,data=json.dumps(data).encode())
            try:
                with urllib.request.urlopen(request,timeout=15) as response:return response.status,json.loads(response.read()),response.headers
            except urllib.error.HTTPError as error:return error.code,json.loads(error.read()),error.headers
        try:
            code,_=phone.pairing.issue();status,paired,headers=call('pair',{'code':code})
            self.assertEqual(status,200);cookie=headers['Set-Cookie'].split(';')[0];csrf=paired['csrfToken']
            data={'url':URL,'loader':'http','requestId':'phone-recovery-request-0001'}
            status,first,_=call('import',data);self.assertEqual(status,202,first)
            job=self.completed(first['job_id']);self.assertEqual(job['state'],'done',job)
            card_id=job['result_id'];card=local_server.read_result(self.root,card_id);card['body']='手机重试保持人工修改。'
            local_server.save_result(self.root,card,card_id)
            phone_relay.save_profile(self.root,{'rules':{'Тестовая':'测试站'},'prefs':{'brandTags':'工作室'},'data':{}})
            status,again,_=call('import',data);self.assertEqual(status,202,again)
            self.assertEqual(again['job_id'],first['job_id'])
            self.assertEqual(local_server.read_result(self.root,card_id)['body'],card['body'])
            with sqlite3.connect(self.root/'listings.sqlite3') as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM results').fetchone()[0],1)
            status,conflict,_=call('import',{**data,'loader':'chrome'})
            self.assertEqual(status,409);self.assertEqual(conflict['code'],'IMPORT_REQUEST_CONFLICT')
        finally:phone.shutdown();phone.server_close();thread.join(3)


if __name__=='__main__':unittest.main()
