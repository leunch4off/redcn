"""Owned loopback editor integration; no live Mac, source or private cache."""
import hashlib
import http.cookiejar
import json
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
import activity_log
import fact_ledger
import language_service
import local_server
import phone_relay
import redcn
import test_local as desktop_fixture
import test_phone_relay as phone_fixture
import test_language_service as language_fixture


URL='https://www.cian.ru/rent/flat/300691182/'


class WorkspaceDesktopBoundaries(unittest.TestCase):
    setUp=desktop_fixture.LocalServerTests.setUp
    tearDown=desktop_fixture.LocalServerTests.tearDown
    request=desktop_fixture.LocalServerTests.request

    def wait_for(self,predicate):
        deadline=time.monotonic()+4
        while time.monotonic()<deadline:
            value=predicate()
            if value:return value
            time.sleep(.01)
        self.fail('Owned fixture job did not finish within its test budget')

    def denied(self,path,data=None,*,status,extra=None,token=True):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request('/api/local/'+path,data,extra=extra,token=token)
        self.assertEqual(caught.exception.code,status)
        return json.loads(caught.exception.read())

    def test_live_manager_pairing_is_required_and_owner_token_guards_get_post(self):
        self.denied('phone-sessions',status=403,token=False)
        self.denied('phone-sessions',{'action':'revoke'},status=403,token=False)
        self.denied('phone-sessions',status=409)
        self.denied('phone-sessions',{'action':'revoke'},status=409)
        with patch.object(phone_relay,'lan_names',return_value={'127.0.0.1'}):
            self.server.phone_relay.enable(0)
        pairing=self.server.phone_relay.server.pairing
        code,_=pairing.issue();phone_token=pairing.pair(code)
        # A new PairingState here would retain stale in-memory authorization.
        # The handlers must use this exact active manager-owned state.
        with patch.object(phone_relay,'PairingState',side_effect=AssertionError('must use active pairing')) as constructor:
            snapshot=self.request('/api/local/phone-sessions')
            session=snapshot['sessions'][0]
            body={'action':'set-role','sessionId':session['id'],'role':'viewer','label':'Тестовый телефон','expectedRevision':snapshot['revision']}
            self.denied('phone-sessions',body,status=403,extra={'X-Redcn-Token':'wrong'})
            self.denied('phone-sessions',status=403,extra={'Origin':'https://foreign.invalid'})
            changed=self.request('/api/local/phone-sessions',body)
            self.assertEqual(pairing.role(phone_token),'viewer')
            self.assertEqual(changed['sessions'][0]['label'],'Тестовый телефон')
            self.denied('phone-sessions',{**body,'role':'owner'},status=409)
            self.assertEqual(pairing.role(phone_token),'viewer')
            revoked=self.request('/api/local/phone-sessions',{'action':'revoke','sessionId':session['id'],'expectedRevision':changed['revision']})
            self.assertFalse(pairing.authorized(phone_token));self.assertEqual(revoked['sessions'],[])
            self.assertGreater(revoked['revision'],changed['revision'])
            constructor.assert_not_called()
        serialized=json.dumps(snapshot)
        self.assertNotIn(phone_token,serialized);self.assertNotIn('secret',serialized);self.assertNotIn('csrf',serialized)

    def test_import_cache_requires_owner_and_preserves_cache_during_active_job(self):
        card_id=local_server.save_result(self.directory,{'fields':{'price':'80000','source':'PRIVATE_SOURCE'},'body':'正文','titles':['标题'],'tags':''})
        with redcn.open_cache(self.directory) as connection:
            connection.execute('INSERT INTO cache VALUES (?,?,?)',(URL,time.time(),'CACHE_FIXTURE'))
        def cache_count():
            with redcn.open_cache(self.directory) as connection:return connection.execute('SELECT COUNT(*) FROM cache').fetchone()[0]
        self.denied('import-cache',{'confirm':'clear-import-cache'},status=403,token=False)
        self.denied('import-cache',{'confirm':'wrong'},status=400)
        self.assertEqual(cache_count(),1)
        entered,release=threading.Event(),threading.Event()
        def blocked_listing(*args,**kwargs):
            entered.set()
            if not release.wait(3):raise AssertionError('Owned fixture was not released')
            raise ValueError('isolated fixture stop')
        before=local_server.read_result(self.directory,card_id)
        try:
            with patch('node_runtime.resolve_node',return_value='isolated-node'),patch.object(local_server.photo_store,'pillow'),patch.object(redcn,'load_listing',side_effect=blocked_listing):
                started=self.request('/api/local/import',{'url':URL})
                self.assertTrue(entered.wait(1))
                self.denied('import-cache',{'confirm':'clear-import-cache'},status=409)
                self.assertEqual(cache_count(),1);self.assertEqual(local_server.read_result(self.directory,card_id),before)
                release.set()
                self.wait_for(lambda:self.server.job_status(started['job_id']).get('state')=='error')
                self.wait_for(lambda:activity_log.list_attempts(self.directory,kind='import')['items'])
        finally:release.set()
        cleared=self.request('/api/local/import-cache',{'confirm':'clear-import-cache'})
        self.assertEqual(cleared,{'cleared':1,'scope':'import-cache'});self.assertEqual(cache_count(),0)
        self.assertEqual(local_server.read_result(self.directory,card_id),before)
        self.assertEqual(self.server.import_diagnostics.status()['state'],'idle')

    def test_terminal_manual_preflight_failure_records_once_without_arming_diagnostics(self):
        recorded=threading.Event();original=activity_log.record_import_attempt
        def receipt(*args,**kwargs):
            result=original(*args,**kwargs);recorded.set();return result
        before=self.server.import_diagnostics.status()
        with patch.object(self.server.import_diagnostics,'arm',wraps=self.server.import_diagnostics.arm) as arm,patch('node_runtime.resolve_node',side_effect=ValueError('PRIVATE_SECRET preflight fixture')),patch.object(redcn,'load_listing') as listing,patch.object(activity_log,'record_import_attempt',side_effect=receipt) as record:
            started=self.request('/api/local/import',{'url':URL+'?private=PRIVATE_QUERY'})
            self.wait_for(lambda:self.server.job_status(started['job_id']).get('state')=='error')
            self.assertTrue(recorded.wait(2))
            self.assertEqual(record.call_count,1);arm.assert_not_called();listing.assert_not_called()
        rows=activity_log.list_attempts(self.directory,kind='import')['items']
        self.assertEqual(len(rows),1)
        row=rows[0];self.assertEqual(row['outcome'],'error');self.assertEqual(row['source'],'cian')
        self.assertIn('preflight',row['stages']);self.assertGreaterEqual(row['stages']['preflight'],0)
        self.assertNotIn('PRIVATE_',json.dumps(rows));self.assertNotIn('https://',json.dumps(rows))
        after=self.server.import_diagnostics.status()
        self.assertEqual(after['state'],before['state']);self.assertEqual(after['reserved'],before['reserved'])


class WorkspacePhoneAttribution(unittest.TestCase):
    setUp=phone_fixture.PhoneRelayTests.setUp
    tearDown=phone_fixture.PhoneRelayTests.tearDown
    request=phone_fixture.PhoneRelayTests.request
    pair=phone_fixture.PhoneRelayTests.pair
    add_card=phone_fixture.PhoneRelayTests.add_card
    wait_for=WorkspaceDesktopBoundaries.wait_for

    def two_devices(self):
        devices=[]
        for _ in range(2):
            self.jar=http.cookiejar.CookieJar()
            self.client=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
            self.pair()
            cookie=next(iter(self.jar)).value
            devices.append({'client':self.client,'csrf':self.csrf,'actor':'phone:'+hashlib.sha256(cookie.encode()).hexdigest()})
        self.assertNotEqual(devices[0]['actor'],devices[1]['actor'])
        return devices

    def as_device(self,device,path,data=None):
        return self.request('/api/phone/'+path,data,client=device['client'],headers={'X-Phone-CSRF':device['csrf']})

    def test_two_cookie_route_changes_record_distinct_verified_authors(self):
        first,second=self.two_devices();card_id=self.add_card()
        card=self.as_device(first,'cards/'+card_id)
        a=self.as_device(first,'cards/'+card_id+'/routes',{'routes':[{'name':'МГУ','time':'30','mode':'public','transport':'Автобус 119'}],'expectedUpdated':card['updated']})
        self.as_device(second,'cards/'+card_id+'/routes',{'routes':[{'name':'МГУ','time':'40','mode':'public','transport':'Метро'}],'expectedUpdated':a['updated']})
        rows=fact_ledger.list_fact_revisions(self.directory,card_id)
        self.assertEqual([row['actor'] for row in rows[:2]],[second['actor'],first['actor']])
        self.assertEqual(rows[0]['fields']['universityRoutes'][0]['time'],'40')
        self.assertEqual(rows[1]['fields']['universityRoutes'][0]['time'],'30')
        before=len(rows)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.as_device(first,'cards/'+card_id+'/routes',{'routes':[],'expectedUpdated':a['updated']})
        self.assertEqual(caught.exception.code,409)
        self.assertEqual(len(fact_ledger.list_fact_revisions(self.directory,card_id)),before)

    def test_two_cookie_text_edit_and_rewrite_forward_verified_actor_to_save_receipts(self):
        first,second=self.two_devices();card_id=self.add_card()
        with patch.object(fact_ledger,'record_fact_revision',wraps=fact_ledger.record_fact_revision) as receipt,patch.object(language_service,'node_language',side_effect=language_fixture.LanguageUpdates.render):
            self.as_device(first,'cards/'+card_id+'/edit-text',{'part':'body','text':'第一部正文','actor':'forged-owner'})
            self.as_device(second,'cards/'+card_id+'/edit-text',{'part':'tags','text':'#第二部','actor':'forged-owner'})
            self.as_device(second,'cards/'+card_id+'/rewrite',{'part':'title','actor':'forged-owner'})
        self.assertEqual([call.kwargs['actor'] for call in receipt.call_args_list],[first['actor'],second['actor'],second['actor']])
        saved=local_server.read_result(self.directory,card_id)
        self.assertEqual(saved['body'],'第一部正文');self.assertEqual(saved['tags'],'#第二部')
        self.assertEqual(saved['titles'][0],'标题1')

    def test_two_cookie_synthetic_imports_use_trusted_actor_outside_listing_fields(self):
        first,second=self.two_devices()
        def listing(url,*args,**kwargs):
            return {'url':url,'text':'Изолированная квартира 52 м². Аренда 80 000 ₽.','fields':{'price':'80000','area':'52'},'images':[],'cached':False,'loader':'http'}
        def generated(text,fields,*args):
            return {'fields':{**fields,'photos':[],'features':[]},'titles':['测试房源'],'body':'正文','tags':'#莫斯科'}
        def media(directory,result,*args):
            result['mediaStatus']='ready';result['media']=[]
        with patch('node_runtime.resolve_node',return_value='isolated-node'),patch.object(local_server.photo_store,'pillow'),patch.object(redcn,'load_listing',side_effect=listing),patch.object(redcn,'generate',side_effect=generated),patch.object(phone_relay,'cache_photos',side_effect=media),patch('photo_generation.photo_revision',return_value=None):
            cards=[]
            for device in (first,second):
                started=self.as_device(device,'import',{'url':URL,'actor':'forged-owner'})
                job=self.wait_for(lambda:self.controller.job_status(started['job_id']) if self.controller.job_status(started['job_id']).get('state')!='running' else None)
                self.assertEqual(job['state'],'done',job);cards.append(job['result_id'])
                revision=fact_ledger.list_fact_revisions(self.directory,job['result_id'])[0]
                self.assertEqual(revision['actor'],device['actor']);self.assertEqual(revision['kind'],'import')
                fields=local_server.read_result(self.directory,job['result_id'])['fields']
                self.assertNotIn('actor',fields);self.assertNotIn('forged-owner',json.dumps(fields))
            self.assertNotEqual(*cards)
        self.assertEqual(self.controller.import_diagnostics.status()['state'],'idle')
        for actor in ('',[],False,'x'*101):
            with self.assertRaises(ValueError):self.controller.start_import({'url':URL},actor=actor)


if __name__=='__main__':unittest.main()
