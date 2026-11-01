"""RCN177: role decisions on disposable fixtures, without reading live auth state."""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import urllib.error

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
sys.path.insert(0,str(Path(__file__).resolve().parent))
import access_roles
import card_assets
import phone_relay
import test_phone_relay as fixture


class PairingRoles(unittest.TestCase):
    def test_legacy_numeric_expiry_defaults_editor_and_never_grants_owner(self):
        with tempfile.TemporaryDirectory() as folder:
            token='x'*43;digest=hashlib.sha256(token.encode()).hexdigest()
            path=Path(folder)/'phone-access.json';path.write_text(json.dumps({'secret':'f'*64,'sessions':{digest:time.time()+300},'pair':None}))
            state=phone_relay.PairingState(folder)
            self.assertEqual(state.role(token),'editor');self.assertTrue(state.authorized(token))
            self.assertEqual(state.actor(token),'phone:'+digest)
            self.assertIsInstance(state.data['sessions'][digest],float)
            safe=json.dumps(state.list_sessions());self.assertNotIn(token,safe);self.assertNotIn('secret',safe);self.assertNotIn('csrf',safe)
            state.data['roles']={digest:['owner']};self.assertIsNone(state.role(token));self.assertEqual(state.list_sessions()['sessions'][0]['role'],'unknown')
            state.data['sessions'][digest]='invalid';self.assertFalse(state.authorized(token))

    def test_grants_persist_with_compare_and_swap_revocation_and_new_pair_editor(self):
        with tempfile.TemporaryDirectory() as folder:
            state=phone_relay.PairingState(folder);code,_=state.issue();token=state.pair(code)
            initial=state.list_sessions();sid=initial['sessions'][0]['id']
            self.assertEqual(state.role(token),'editor')
            grant=state.manage_session(sid,role='viewer',label='iPhone коллеги',expected_revision=initial['revision'])
            self.assertEqual(phone_relay.PairingState(folder).role(token),'viewer')
            self.assertEqual(grant['sessions'][0]['label'],'iPhone коллеги')
            with self.assertRaises(access_roles.AccessRoleConflict):state.manage_session(sid,role='owner',expected_revision=initial['revision'])
            self.assertEqual(state.role(token),'viewer')
            for role in ('admin',None,{'owner':True}):
                with self.assertRaises(ValueError):state.manage_session(sid,role=role,expected_revision=grant['revision'])
            with self.assertRaises(ValueError):state.manage_session(sid,role='owner',label='bad\nlabel',expected_revision=grant['revision'])
            state.manage_session(sid,role='owner',expected_revision=grant['revision']);self.assertEqual(state.role(token),'owner')
            code,_=state.issue();second=state.pair(code);self.assertEqual(state.role(second),'editor')
            revision=state.list_sessions()['revision'];state.manage_session(sid,revoke=True,expected_revision=revision)
            self.assertFalse(state.authorized(token));self.assertIsNone(state.actor(token));self.assertTrue(state.authorized(second))
            saved=json.loads((Path(folder)/'phone-access.json').read_text())
            self.assertTrue(all(type(value) in (int,float) for value in saved['sessions'].values()))
            self.assertNotIn(sid,saved['roles']);self.assertNotIn(sid,saved['devices'])

    def test_desktop_session_dispatch_validates_action_revision_and_exposes_no_secret(self):
        with tempfile.TemporaryDirectory() as folder:
            state=phone_relay.PairingState(folder);code,_=state.issue();state.pair(code)
            status,data,_,_=access_roles.dispatch_sessions(state);self.assertEqual(status,200)
            payload={'action':'set-role','sessionId':data['sessions'][0]['id'],'role':'viewer','expectedRevision':data['revision']}
            self.assertEqual(access_roles.dispatch_sessions(state,payload)[0],200)
            self.assertEqual(access_roles.dispatch_sessions(state,payload)[0],409)
            self.assertEqual(access_roles.dispatch_sessions(state,{**payload,'role':'owner','actor':'Mac owner'})[0],400)
            self.assertNotIn('secret',json.dumps(access_roles.dispatch_sessions(state)[1]))

    def test_route_matrix_fails_closed_while_content_editing_remains_available(self):
        cid='a'*32;asset='b'*64
        for role in ('owner','editor','viewer'):
            for path in ('state','cards','cards/'+cid,'cards/'+cid+'.zip','media/'+asset,'previews/'+asset,'exports/'+asset,'card-exports/'+cid):
                self.assertTrue(access_roles.phone_route_allowed(role,'GET',path),(role,path))
            self.assertTrue(access_roles.phone_route_allowed(role,'POST','logout'))
            for method,path in [('GET','phone-sessions'),('POST','phone-sessions'),('POST','unknown-future'),('GET','../design')]:self.assertFalse(access_roles.phone_route_allowed(role,method,path))
        for role in ('owner','editor'):
            for path in ('import','design','design-images','dictionary-reviews','card-export','cards/'+cid+'/routes'):
                self.assertTrue(access_roles.phone_route_allowed(role,'POST',path,{'action':'approve'}),(role,path))
        for path in ('design','import-diagnostics','photo-ai','dictionary-reviews','fact-revisions/'+cid,'jobs/'+cid):self.assertFalse(access_roles.phone_route_allowed('viewer','GET',path))
        for method,path in [('POST','import-diagnostics'),('POST','photo-ai'),('POST','listing-watch'),('POST','dossier-import'),('GET','dossier-backup/'+cid),('GET','card-archive/'+cid),('GET','card-attachments/'+cid+'/'+asset)]:
            self.assertFalse(access_roles.phone_route_allowed('editor',method,path));self.assertTrue(access_roles.phone_route_allowed('owner',method,path))
        for role in (None,'admin',{},[]):self.assertFalse(access_roles.phone_route_allowed(role,'GET','cards'))


class RoleHTTPBoundary(unittest.TestCase):
    setUp=fixture.PhoneRelayTests.setUp
    tearDown=fixture.PhoneRelayTests.tearDown
    request=fixture.PhoneRelayTests.request
    pair=fixture.PhoneRelayTests.pair
    add_card=fixture.PhoneRelayTests.add_card
    photo=fixture.PhoneRelayTests.photo

    def grant(self,role):
        state=self.server.pairing;snapshot=state.list_sessions()
        return state.manage_session(snapshot['sessions'][0]['id'],role=role,expected_revision=snapshot['revision'])

    def denied(self,path,data=None,status=403):
        with self.assertRaises(urllib.error.HTTPError) as caught:self.request('/api/phone/'+path,data)
        self.assertEqual(caught.exception.code,status)
        return json.loads(caught.exception.read())

    def test_editor_global_admin_denied_before_dispatch_and_cannot_upgrade_itself(self):
        self.pair();state=self.request('/api/phone/state');self.assertEqual(state['role'],'editor')
        self.assertFalse(state['capabilities']['manageSettings']);self.assertTrue(state['capabilities']['manageDesign'])
        with patch.object(self.controller,'workspace_api',return_value=None) as workspace,patch.object(self.controller,'start_import') as start:
            for path in ('photo-ai','import-diagnostics','listing-watch','listing-watch/check','phone-sessions','dossier-import'):
                self.assertEqual(self.denied(path,{'action':'arm','role':'owner'})['code'],'ROLE_FORBIDDEN')
            for path in ('import-diagnostics/tg.zip','card-archive/'+'a'*32,'dossier-backup/'+'a'*32):self.denied(path)
            workspace.assert_not_called();start.assert_not_called()
        self.assertEqual(self.request('/api/phone/state')['role'],'editor')

    def test_actor_is_verified_from_cookie_not_payload_and_revocation_applies_next_request(self):
        self.pair();digest=self.server.pairing.list_sessions()['sessions'][0]['id']
        with patch.object(self.controller,'workspace_api',return_value=(200,{'ok':True},'application/json',{})) as workspace:
            self.request('/api/phone/dictionary-reviews',{'action':'propose','actor':'Mac owner','role':'owner'})
            self.assertEqual(workspace.call_args.kwargs['actor'],'phone:'+digest)
            self.assertEqual(workspace.call_args.kwargs['namespace'],'phone')
        self.grant('viewer');self.assertEqual(self.request('/api/phone/state')['role'],'viewer')
        self.denied('import',{'url':fixture.URL})
        snapshot=self.server.pairing.list_sessions();self.server.pairing.manage_session(digest,revoke=True,expected_revision=snapshot['revision'])
        self.denied('cards',status=401)

    def test_viewer_reads_only_client_fields_and_not_notes_archives_jobs_or_mutations(self):
        cid=self.add_card(workflow={'housingType':'apartment','internalNote':'PRIVATE WORKFLOW'},factLedger={'price':'PRIVATE FACT'},source='PRIVATE RAW SOURCE',description='Клиентский факт')
        invalid=self.add_card(price='500',priceCurrency='USD');self.pair();self.grant('viewer')
        state=self.request('/api/phone/state');self.assertIsNone(state['activeJob'])
        card=self.request('/api/phone/cards/'+cid)
        self.assertEqual(card['body'],'正文');self.assertNotIn('workflow',card['fields']);self.assertNotIn('source',card['fields']);self.assertNotIn('factLedger',card['fields'])
        self.assertNotIn('PRIVATE',json.dumps(card));self.assertEqual([row['id'] for row in self.request('/api/phone/cards')['cards']],[cid])
        self.assertNotEqual(invalid,cid)
        with patch.object(self.controller,'workspace_api',return_value=None) as workspace,patch.object(self.controller,'prepare_card') as prepare:
            for path in ('design','photo-ai','import-diagnostics','dictionary-reviews','card-archive/'+cid,'dossier-options/'+cid,'card-attachments/'+cid+'/'+'a'*32,'jobs/'+'b'*32):self.denied(path)
            for path in ('import','design','card-export','cards/'+cid+'/prepare','cards/'+cid+'/routes','cards/'+cid+'/edit-text'):self.denied(path,{})
            workspace.assert_not_called();prepare.assert_not_called()

    def test_viewer_media_and_exports_must_belong_to_visible_current_policy_card(self):
        media=self.photo();cid=self.add_card([fixture.PHOTO]);self.pair();self.grant('viewer')
        self.assertTrue(access_roles.viewer_asset_allowed(self.directory,'media',media['id']))
        self.assertEqual(self.request('/api/phone/media/'+media['id']),fixture.photo_store.asset(self.directory,media['id'])[0])
        self.denied('media/'+'c'*64)
        exported=card_assets.create_export(self.directory,cid,'<!doctype html><html><body>CLIENT</body></html>','html')
        self.assertTrue(access_roles.viewer_asset_allowed(self.directory,'export',exported['id']))
        self.assertIn(b'CLIENT',self.request('/api/phone/exports/'+exported['id']))
        self.assertEqual(self.request('/api/phone/card-exports/'+cid)['exports'][0]['id'],exported['id'])
        with sqlite3.connect(self.directory/'card-assets'/'dossiers.sqlite3') as db:db.execute('DELETE FROM export_publication_policy WHERE export_id=?',(exported['id'],))
        self.denied('exports/'+exported['id']);self.assertEqual(self.request('/api/phone/card-exports/'+cid)['exports'],[])
        with sqlite3.connect(self.directory/'listings.sqlite3') as db:
            payload=json.loads(db.execute('SELECT payload FROM results WHERE id=?',(cid,)).fetchone()[0]);payload['fields']['isDemo']=True;db.execute('UPDATE results SET payload=? WHERE id=?',(json.dumps(payload),cid))
        self.denied('media/'+media['id'])

    def test_owner_admin_is_allowed_but_http_origin_csrf_and_unknown_role_still_reject(self):
        self.pair();self.grant('owner')
        with patch.object(self.controller,'workspace_api',return_value=(200,{'allowed':True},'application/json',{})) as workspace:
            self.assertTrue(self.request('/api/phone/import-diagnostics',{'action':'pause'})['allowed']);workspace.assert_called_once()
        for headers in ({'Origin':'https://evil.example'},{'X-Phone-CSRF':'wrong'}):
            with self.assertRaises(urllib.error.HTTPError) as caught:self.request('/api/phone/import-diagnostics',{'action':'pause'},headers=headers)
            self.assertEqual(caught.exception.code,403)
        digest=self.server.pairing.list_sessions()['sessions'][0]['id'];self.server.pairing.data['roles'][digest]='admin'
        self.denied('cards')


if __name__=='__main__':unittest.main()
