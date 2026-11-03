"""Two independent owners, real HTTP and generated data; no live cache/auth read."""
import base64
import http.cookiejar
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
import card_assets
import local_server
import phone_relay
import photo_store
from workspace_ownership import WorkspaceOwner,DesktopOwnerAccess,OwnershipError,owned_path


def unlink_during_lstat(target, replacement=None):
    """Model an inode unlinked after lookup but before stat sampled nlink."""
    original=Path.lstat
    calls=[]
    def raced(path,*args,**kwargs):
        value=original(path,*args,**kwargs)
        if path==target:
            calls.append(value.st_nlink)
            if len(calls)==1:
                target.unlink()
                if replacement is not None:replacement()
                fields=list(value);fields[3]=0
                return os.stat_result(fields)
        return value
    return raced,calls


class ScopeTests(unittest.TestCase):
    def test_private_owner_credential_is_persistent_and_not_a_workspace_public_id(self):
        with tempfile.TemporaryDirectory() as folder:
            scope=WorkspaceOwner(folder);first=DesktopOwnerAccess(scope)
            self.assertEqual(Path(folder).stat().st_mode&0o777,0o700)
            self.assertEqual((Path(folder)/first.filename).stat().st_mode&0o777,0o600)
            self.assertEqual(first.token,DesktopOwnerAccess(WorkspaceOwner(folder)).token)
            self.assertNotEqual(first.token,scope.owner_id)
            self.assertFalse(first.authorized(scope.owner_id));self.assertFalse(first.authorized(None))
            self.assertIn('; HttpOnly;',first.cookie(8765));self.assertIn('; SameSite=Strict;',first.cookie(8765))
            self.assertNotIn(first.token,first.cookie(8765));self.assertIn('Path=/owner/',first.cookie(8765))
            self.assertNotEqual(first.proof(8765,'a'*32),first.proof(8766,'a'*32))

    def test_missing_credential_is_not_created_by_open_existing(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(OwnershipError):DesktopOwnerAccess(WorkspaceOwner(folder),create=False)
            self.assertFalse((Path(folder)/DesktopOwnerAccess.filename).exists())

    def test_malformed_credentials_are_denied_without_comparison_exceptions(self):
        with tempfile.TemporaryDirectory() as folder:
            access=DesktopOwnerAccess(WorkspaceOwner(folder))
            for value in (None, {}, '', 'é'*43, 'a'*42, 'a'*1000):
                with self.subTest(value=type(value).__name__):
                    self.assertFalse(access.authorized(value))
                    self.assertFalse(access.authorized_cookie(value,8765))

    def test_credential_permissions_and_transplanted_links_fail_closed(self):
        with tempfile.TemporaryDirectory() as a,tempfile.TemporaryDirectory() as b:
            access=DesktopOwnerAccess(WorkspaceOwner(a));path=Path(a)/access.filename
            path.chmod(0o644)
            with self.assertRaises(OwnershipError):DesktopOwnerAccess(WorkspaceOwner(a))
            path.chmod(0o600);other=Path(b)/access.filename;other.symlink_to(path)
            with self.assertRaises(OwnershipError):WorkspaceOwner(b)

    def test_root_replacement_and_foreign_sqlite_link_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'owner';scope=WorkspaceOwner(root)
            root.rename(Path(folder)/'old-owner');root.mkdir()
            with self.assertRaises(OwnershipError):scope.check()
        with tempfile.TemporaryDirectory() as a,tempfile.TemporaryDirectory() as b:
            scope=WorkspaceOwner(a);foreign=Path(b)/'listings.sqlite3';foreign.write_bytes(b'foreign')
            (Path(a)/'listings.sqlite3').symlink_to(foreign)
            with self.assertRaises(OwnershipError):scope.check()

    def test_addressed_private_leaf_cannot_escape_through_symlink_or_hardlink(self):
        with tempfile.TemporaryDirectory() as a,tempfile.TemporaryDirectory() as b:
            root=Path(a);(root/'media').mkdir();foreign=Path(b)/'foreign';foreign.write_bytes(b'foreign')
            (root/'media'/'linked').symlink_to(foreign)
            with self.assertRaises(OwnershipError):owned_path(root,'media/linked')
            (root/'media'/'linked').unlink();os.link(foreign,root/'media'/'linked')
            with self.assertRaises(OwnershipError):owned_path(root,'media/linked')
            for path in ('../foreign','/tmp/foreign'):
                with self.assertRaises(OwnershipError):owned_path(root,path)

    def test_completed_sqlite_journal_can_vanish_during_metadata_check(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);scope=WorkspaceOwner(root)
            journal=root/'activity-log.sqlite3-journal';journal.write_bytes(b'synthetic journal')
            raced,calls=unlink_during_lstat(journal)
            with patch.object(Path,'lstat',raced):self.assertTrue(scope.check())
            self.assertFalse(journal.exists());self.assertEqual(len(calls),1)

    def test_zero_link_snapshot_rechecks_replacement_and_keeps_link_rejection(self):
        for kind in ('private-file','symlink','hardlink'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as folder:
                base=Path(folder);root=base/'workspace';scope=WorkspaceOwner(root)
                foreign=base/'foreign';foreign.write_bytes(b'other workspace fixture')
                journal=root/'activity-log.sqlite3-journal';journal.write_bytes(b'synthetic journal')
                def replace():
                    if kind=='private-file':journal.write_bytes(b'next private journal')
                    elif kind=='symlink':journal.symlink_to(foreign)
                    else:os.link(foreign,journal)
                raced,calls=unlink_during_lstat(journal,replace)
                with patch.object(Path,'lstat',raced):
                    if kind=='private-file':self.assertTrue(scope.check())
                    else:
                        with self.assertRaises(OwnershipError):scope.check()
                self.assertEqual(len(calls),2)
                self.assertEqual(foreign.read_bytes(),b'other workspace fixture')

    def test_zero_link_retry_is_bounded_and_unstable_metadata_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);scope=WorkspaceOwner(root)
            journal=root/'activity-log.sqlite3-journal';journal.write_bytes(b'synthetic journal')
            original=Path.lstat;calls=[]
            def unstable(path,*args,**kwargs):
                value=original(path,*args,**kwargs)
                if path==journal:
                    calls.append(True);fields=list(value);fields[3]=0
                    return os.stat_result(fields)
                return value
            with patch.object(Path,'lstat',unstable):
                with self.assertRaises(OwnershipError):scope.check()
            self.assertEqual(len(calls),2)


class TwoOwnerHTTP(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.owners=[];self.phones=[]
        for name in ('A','B'):
            directory=self.root/name
            server=local_server.create_server(directory,0)
            thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.01},daemon=True);thread.start()
            owner={'directory':directory,'server':server,'thread':thread,'base':'http://127.0.0.1:'+str(server.server_port),'token':server.desktop_access.token}
            owner['card']=local_server.save_result(directory,{'fields':{'price':'80000','photos':[]},'titles':['合成房源'+name],'body':'仅供测试 '+name,'tags':'#测试'})
            self.owners.append(owner)
        self.a,self.b=self.owners

    def tearDown(self):
        for server,thread in self.phones:
            server.shutdown();server.server_close();thread.join()
        for owner in self.owners:
            owner['server'].shutdown();owner['server'].server_close();owner['thread'].join()
        self.temp.cleanup()

    def call(self,owner,path,data=None,*,token=None,client=None,origin=None):
        headers={'Origin':origin or owner['base']}
        if token is not None:headers['X-Redcn-Token']=token
        if data is not None:headers['Content-Type']='application/json'
        request=urllib.request.Request(owner['base']+path,data=json.dumps(data).encode() if data is not None else None,headers=headers)
        try:
            with (client or urllib.request).open(request,timeout=10) if client else urllib.request.urlopen(request,timeout=10) as response:
                return response.status,response.read(),response.headers
        except urllib.error.HTTPError as error:
            return error.code,error.read(),error.headers

    def test_public_page_never_discloses_owner_token_and_exchange_is_scoped(self):
        for owner in self.owners:
            status,page,_=self.call(owner,'/')
            self.assertEqual(status,200);self.assertNotIn(owner['token'].encode(),page)
            self.assertIn(b'name="redcn-token" content=""',page)
            self.assertNotIn(b'token',self.call(owner,'/api/local/health')[1])
        for bad in (self.b['token'],'x'*43,None):
            status,body,_=self.call(self.a,'/api/local/desktop-access',{'token':bad})
            self.assertEqual(status,403);self.assertNotIn(self.a['token'].encode(),body)
        self.assertEqual(self.call(self.a,'/api/local/desktop-access',{'token':self.a['token']},origin='https://other.invalid')[0],403)
        jar=http.cookiejar.CookieJar();client=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        status,claim,headers=self.call(self.a,'/api/local/desktop-access',{'token':self.a['token']},client=client)
        self.assertEqual(status,200);self.assertIn('HttpOnly',headers['Set-Cookie'])
        self.assertNotIn(self.a['token'].encode(),headers['Set-Cookie'].encode())
        self.assertNotIn(self.a['token'].encode(),self.call(self.a,'/',client=client)[1])
        private=json.loads(claim)['sessionPath']
        self.assertEqual(json.loads(self.call(self.a,private,client=client)[1])['token'],self.a['token'])
        self.assertEqual(self.call(self.a,private)[0],403)
        cookie_value=headers['Set-Cookie'].split(';')[0].split('=',1)[1]
        self.assertEqual(self.call(self.a,'/api/local/cards',token=cookie_value)[0],403)
        self.assertNotIn(self.b['token'].encode(),self.call(self.b,'/',client=client)[1])

    def test_existing_server_proves_private_ownership_before_cli_opens_or_sends_a_secret(self):
        with patch.object(local_server.webbrowser,'open') as opened:
            self.assertEqual(local_server.open_existing(self.a['directory'],self.a['server'].server_port),0)
            opened.assert_called_once_with(self.a['server'].desktop_access.launch_url(self.a['server'].server_port))
        with self.assertRaises(OwnershipError):
            local_server.owner_launch_url(self.a['directory'],self.b['server'].server_port)
        self.assertEqual(self.call(self.a,'/api/local/owner-proof?challenge=invalid')[0],400)

    def test_private_cookie_and_session_path_survive_a_server_restart(self):
        jar=http.cookiejar.CookieJar();client=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        status,content,_=self.call(self.a,'/api/local/desktop-access',{'token':self.a['token']},client=client)
        self.assertEqual(status,200);path=json.loads(content)['sessionPath']
        owner=self.a;port=owner['server'].server_port
        owner['server'].shutdown();owner['server'].server_close();owner['thread'].join()
        server=local_server.create_server(owner['directory'],port)
        thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.01},daemon=True);thread.start()
        owner.update(server=server,thread=thread)
        self.assertEqual(server.desktop_access.token,owner['token'])
        self.assertEqual(json.loads(self.call(owner,path,client=client)[1])['token'],owner['token'])
        self.assertNotIn(owner['token'].encode(),self.call(owner,'/',client=client)[1])

    def test_wrong_owner_is_rejected_before_every_private_dispatch_family(self):
        paths=['cards','results/'+self.b['card'],'design','language','learning','workspace-backup.zip',
               'photo-duplicates','card-archive/'+self.b['card'],'import-diagnostics','phone-sessions']
        with patch.object(local_server.language_service,'dispatch') as language,patch.object(local_server.card_api,'dispatch') as cards:
            for path in paths:
                self.assertEqual(self.call(self.b,'/api/local/'+path,token=self.a['token'])[0],403,path)
            self.assertEqual(self.call(self.b,'/api/local/cards/'+self.b['card']+'/edit-text',{'part':'body','text':'foreign overwrite'},token=self.a['token'])[0],403)
            language.assert_not_called();cards.assert_not_called()
        self.assertEqual(local_server.read_result(self.b['directory'],self.b['card'])['body'],'仅供测试 B')

    def test_non_ascii_auth_header_returns_forbidden_without_closing_the_connection(self):
        status,body,_=self.call(self.a,'/api/local/cards',token='é'*43)
        self.assertEqual(status,403)
        self.assertIn('error',json.loads(body))

    def test_unlinked_sqlite_journal_does_not_revoke_valid_owner_http_access(self):
        journal=self.a['directory']/'activity-log.sqlite3-journal'
        journal.write_bytes(b'completed synthetic transaction')
        raced,calls=unlink_during_lstat(journal)
        with patch.object(Path,'lstat',raced):
            status,body,_=self.call(self.a,'/api/local/cards',token=self.a['token'])
        self.assertEqual(status,200,body.decode())
        self.assertEqual(len(calls),1)
        self.assertEqual(self.call(self.a,'/api/local/cards',token=self.b['token'])[0],403)
        self.assertEqual(self.call(self.a,'/api/local/cards')[0],403)

    def test_foreign_card_edit_attachment_and_media_ids_do_not_cross_workspaces(self):
        before=local_server.read_result(self.b['directory'],self.b['card'])
        self.assertEqual(self.call(self.a,'/api/local/cards/'+self.b['card']+'/edit-text',{'part':'body','text':'foreign overwrite'},token=self.a['token'])[0],404)
        content=b'private B attachment'
        item=card_assets.add_attachment(self.b['directory'],self.b['card'],{'category':'other','name':'private.txt','data':base64.b64encode(content).decode()})['attachments'][0]
        path='/api/local/card-attachments/'+self.a['card']+'/'+item['id']
        status,body,_=self.call(self.a,path,token=self.a['token'])
        self.assertIn(status,(400,404));self.assertNotIn(content,body)
        photo=photo_store._save(self.b['directory'],b'private B image','image/png',{'kind':'original'})
        status,body,_=self.call(self.a,'/api/local/media/'+photo['id'],token=self.a['token'])
        self.assertEqual(status,404);self.assertNotIn(b'private B image',body)
        self.assertEqual(local_server.read_result(self.b['directory'],self.b['card']),before)

    def test_same_id_has_owner_local_content_and_backup_cannot_collect_another_owner(self):
        same='c'*32
        for owner,marker in ((self.a,'A_ONLY'),(self.b,'B_ONLY')):
            local_server.save_result(owner['directory'],{'fields':{'price':'80000'},'titles':['测试'],'body':marker,'tags':'#测试'},same)
        for owner,marker in ((self.a,'A_ONLY'),(self.b,'B_ONLY')):
            status,body,_=self.call(owner,'/api/local/results/'+same,token=owner['token'])
            self.assertEqual(status,200);self.assertEqual(json.loads(body)['body'],marker)
        status,content,_=self.call(self.a,'/api/local/workspace-backup.zip',token=self.a['token'])
        self.assertEqual(status,200)
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            parts=[]
            for name in archive.namelist():
                value=archive.read(name)
                if name.endswith('.zip'):
                    with zipfile.ZipFile(io.BytesIO(value)) as card:
                        parts.extend(card.read(item) for item in card.namelist())
                else:parts.append(value)
            data=b'\n'.join(parts)
            self.assertIn(b'A_ONLY',data);self.assertNotIn(b'B_ONLY',data)
            self.assertNotIn(self.a['token'].encode(),data);self.assertFalse(any('desktop-owner' in name for name in archive.namelist()))

    def test_foreign_store_transplant_is_denied_before_private_lookup(self):
        database=self.a['directory']/'listings.sqlite3';database.unlink();database.symlink_to(self.b['directory']/'listings.sqlite3')
        status,body,_=self.call(self.a,'/api/local/cards',token=self.a['token'])
        self.assertEqual(status,403);self.assertEqual(json.loads(body)['code'],'WORKSPACE_OWNER_MISMATCH')
        self.assertNotIn(self.b['card'].encode(),body)

    def test_phone_cookie_cannot_be_used_in_another_owner_and_mismatched_controller_refused(self):
        phone=[]
        for owner in self.owners:
            server=phone_relay.create_phone_server(owner['directory'],owner['server'],0,'127.0.0.1',hosts={'127.0.0.1'})
            thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.01},daemon=True);thread.start()
            self.phones.append((server,thread));phone.append((server,'http://127.0.0.1:'+str(server.server_port)))
        code,_=phone[0][0].pairing.issue()
        request=urllib.request.Request(phone[0][1]+'/api/phone/pair',data=json.dumps({'code':code}).encode(),headers={'Content-Type':'application/json','Origin':phone[0][1]})
        with urllib.request.urlopen(request) as response:cookie=response.headers['Set-Cookie'].split(';')[0]
        request=urllib.request.Request(phone[1][1]+'/api/phone/state',headers={'Cookie':cookie,'Origin':phone[1][1]})
        with self.assertRaises(urllib.error.HTTPError) as caught:urllib.request.urlopen(request)
        self.assertEqual(caught.exception.code,401)
        with self.assertRaises(OwnershipError):phone_relay.create_phone_server(self.a['directory'],self.b['server'],0,'127.0.0.1',hosts={'127.0.0.1'})


if __name__=='__main__':unittest.main()
