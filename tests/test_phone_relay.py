import base64
import http.cookiejar
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
import local_server
import phone_relay
import photo_store
import redcn

URL='https://www.cian.ru/rent/flat/300691182/'
PHOTO='https://images.cdn-cian.ru/images/phone-test.jpg'


class PhoneRelayTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.directory=Path(self.temp.name)
        self.controller=local_server.create_server(self.directory,0)
        self.server=phone_relay.create_phone_server(self.directory,self.controller,0,'127.0.0.1',hosts={'127.0.0.1'})
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base='http://127.0.0.1:'+str(self.server.server_port)
        self.jar=http.cookiejar.CookieJar();self.client=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.csrf=None;self.controller_thread=None

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join()
        if self.controller_thread:self.controller.shutdown();self.controller_thread.join()
        self.controller.server_close();self.temp.cleanup()

    def request(self,path,data=None,headers=None,client=None):
        fields={'Origin':self.base}
        if data is not None:fields['Content-Type']='application/json';fields['X-Phone-CSRF']=self.csrf or ''
        fields.update(headers or {})
        request=urllib.request.Request(self.base+path,json.dumps(data).encode() if data is not None else None,headers=fields)
        with (client or self.client).open(request) as response:
            content=response.read()
            return json.loads(content) if response.headers.get_content_type()=='application/json' else content

    def pair(self):
        code,_=self.server.pairing.issue()
        result=self.request('/api/phone/pair',{'code':code});self.csrf=result['csrfToken'];return code

    def add_card(self,photos=None,**fields):
        return local_server.save_result(self.directory,{'fields':{'price':'80000','photos':photos or [],**fields},'titles':['真实房源'],'body':'正文','tags':'#莫斯科'})

    def photo(self):
        from PIL import Image
        stream=io.BytesIO();Image.new('RGB',(100,80),'#aaaaaa').save(stream,'PNG')
        return photo_store.attach_photo(self.directory,PHOTO,'data:image/png;base64,'+base64.b64encode(stream.getvalue()).decode())

    def test_unpaired_phone_cannot_read_cards_media_state_or_local_editor_token(self):
        result=self.add_card()
        for path in ['/api/phone/cards','/api/phone/cards/'+result,'/api/phone/state','/api/phone/media/'+'a'*64,'/api/local/health']:
            with self.assertRaises(urllib.error.HTTPError) as caught:self.request(path)
            self.assertEqual(caught.exception.code,401)
        with self.assertRaises(urllib.error.HTTPError) as caught:self.request('/api/phone/pair',{'code':'x'*43},headers={'Host':'evil.example:'+str(self.server.server_port)})
        self.assertEqual(caught.exception.code,403)

    def test_pairing_is_one_use_cookie_persistent_and_csrf_origin_checked(self):
        code=self.pair();self.assertTrue(self.request('/api/phone/state')['paired'])
        self.assertTrue(any(cookie.has_nonstandard_attr('HttpOnly') for cookie in self.jar))
        self.assertTrue(phone_relay.PairingState(self.directory).authorized(next(iter(self.jar)).value))
        with self.assertRaises(urllib.error.HTTPError):self.request('/api/phone/pair',{'code':code})
        for headers in [{'Origin':'https://evil.example'},{'X-Phone-CSRF':'wrong'}]:
            with self.assertRaises(urllib.error.HTTPError) as caught:self.request('/api/phone/import',{'url':URL},headers=headers)
            self.assertEqual(caught.exception.code,403)
        self.assertEqual((self.directory/'phone-access.json').stat().st_mode&0o777,0o600)

    def test_expired_pair_link_cannot_be_used_and_logout_revokes_cookie(self):
        code,_=self.server.pairing.issue();self.server.pairing.data['pair']['expires']=time.time()-1
        with self.assertRaises(urllib.error.HTTPError):self.request('/api/phone/pair',{'code':code})
        self.pair();self.request('/api/phone/logout',{})
        with self.assertRaises(urllib.error.HTTPError) as caught:self.request('/api/phone/cards')
        self.assertEqual(caught.exception.code,401)

    def test_phone_reads_saved_card_and_image_bytes_download_zip(self):
        asset=self.photo();result_id=self.add_card([PHOTO]);self.pair()
        feed=self.request('/api/phone/cards');self.assertEqual(feed['cards'][0]['id'],result_id);self.assertEqual(feed['cards'][0]['cachedPhotoCount'],1)
        card=self.request('/api/phone/cards/'+result_id);self.assertEqual(card['body'],'正文');self.assertEqual(card['media'][0]['id'],asset['id'])
        self.assertEqual(self.request(card['media'][0]['downloadUrl']),photo_store.asset(self.directory,asset['id'])[0])
        archive=zipfile.ZipFile(io.BytesIO(self.request('/api/phone/cards/'+result_id+'.zip')))
        self.assertEqual(archive.read('post.txt').decode(),'真实房源\n\n正文\n\n#莫斯科');self.assertIn('01.png',archive.namelist())
        self.assertEqual(self.request('/api/phone/cards?after='+str(feed['cursor']))['cards'],[])
        self.assertNotIn('redcn-token',json.dumps(card))

    def test_demo_cards_hidden_and_excluded_photos_not_relayed(self):
        self.photo();self.add_card([PHOTO],isDemo=True);normal=self.add_card([PHOTO],photoEdits={'excluded':[PHOTO]});self.pair()
        cards=self.request('/api/phone/cards')['cards'];self.assertEqual(len(cards),1);self.assertEqual(cards[0]['photoCount'],0)
        self.assertEqual(self.request('/api/phone/cards/'+normal)['media'],[])

    def test_phone_import_uses_mac_queue_and_caches_photo_before_job_done(self):
        asset=self.photo();self.pair()
        with patch.object(redcn,'load_listing',return_value={'url':URL,'text':'Квартира 54 м². Аренда 80 000 руб.','images':[PHOTO]}),patch.object(redcn,'generate',return_value={'fields':{'photos':[PHOTO],'price':'80000'},'titles':['标题'],'body':'正文','tags':'#租房'}),patch.object(photo_store,'load_photo',return_value=asset) as load,patch.object(photo_store,'auto_crop_photo',return_value={'autoStatus':'unchanged'}):
            response=self.request('/api/phone/import',{'url':URL})
            for _ in range(100):
                job=self.request('/api/phone/jobs/'+response['job_id'])
                if job['state']!='running':break
                time.sleep(.01)
            self.assertEqual(job['state'],'done');load.assert_called_once_with(self.directory,PHOTO)
            self.assertEqual(self.request('/api/phone/cards/'+job['result_id'])['mediaStatus'],'ready')
        for url in ['http://127.0.0.1/','https://cian.ru.evil.org/']:
            with self.assertRaises(urllib.error.HTTPError):self.request('/api/phone/import',{'url':url})

    def test_prepare_old_card_downloads_on_mac_and_queue_serializes(self):
        asset=self.photo();result_id=self.add_card([PHOTO]);self.pair()
        with patch.object(photo_store,'load_photo',return_value=asset),patch.object(photo_store,'auto_crop_photo',return_value={'autoStatus':'unchanged'}):
            response=self.request('/api/phone/cards/'+result_id+'/prepare',{})
            for _ in range(100):
                job=self.request('/api/phone/jobs/'+response['job_id'])
                if job['state']!='running':break
                time.sleep(.01)
        self.assertEqual(job['state'],'done')
        entered,release=threading.Event(),threading.Event()
        def listing(*args,**kwargs):entered.set();release.wait(2);raise ValueError('fixture stop')
        with patch.object(redcn,'load_listing',side_effect=listing):
            self.request('/api/phone/import',{'url':URL});self.assertTrue(entered.wait(1))
            with self.assertRaises(urllib.error.HTTPError):self.request('/api/phone/import',{'url':URL})
            release.set()

    def test_running_desktop_can_enable_phone_without_a_second_editor(self):
        import socket
        self.controller_thread=threading.Thread(target=self.controller.serve_forever,daemon=True);self.controller_thread.start()
        with socket.socket() as available:available.bind(('127.0.0.1',0));port=available.getsockname()[1]
        result=local_server.enable_existing_phone(self.controller.server_port,port)
        self.assertTrue(result['enabled']);self.assertEqual(result['port'],port);self.assertTrue(result['pairUrls'])
        self.assertTrue(self.controller.phone_relay.status()['enabled'])

    def test_polling_does_not_read_or_hash_full_photo_binaries(self):
        self.photo();self.add_card([PHOTO]);self.pair()
        with patch.object(photo_store,'asset',side_effect=AssertionError('Feed must not read image bytes')):
            cards=self.request('/api/phone/cards')['cards']
        self.assertEqual(cards[0]['cachedPhotoCount'],1)

    def test_prepare_completion_keeps_newer_manual_text_and_price(self):
        asset=self.photo();result_id=self.add_card([PHOTO]);self.pair()
        entered,release=threading.Event(),threading.Event()
        def load(*args,**kwargs):entered.set();release.wait(2);return asset
        with patch.object(photo_store,'load_photo',side_effect=load),patch.object(photo_store,'auto_crop_photo',return_value={'autoStatus':'unchanged'}):
            response=self.request('/api/phone/cards/'+result_id+'/prepare',{});self.assertTrue(entered.wait(1))
            current=local_server.read_result(self.directory,result_id);current['body']='Новый текст';current['fields']['price']='81000'
            local_server.save_result(self.directory,current,result_id);release.set()
            for _ in range(100):
                job=self.request('/api/phone/jobs/'+response['job_id'])
                if job['state']!='running':break
                time.sleep(.01)
        card=self.request('/api/phone/cards/'+result_id)
        self.assertEqual(job['state'],'done');self.assertEqual(card['body'],'Новый текст');self.assertEqual(card['fields']['price'],'81000');self.assertEqual(card['mediaStatus'],'ready')

    def test_normal_server_stop_preserves_session_but_explicit_disable_revokes(self):
        self.pair();token=next(iter(self.jar)).value
        self.controller.phone_relay.server=self.server;self.controller.phone_relay.thread=self.thread
        self.controller.server_close()
        self.assertTrue(phone_relay.PairingState(self.directory).authorized(token))
        state=phone_relay.PairingState(self.directory);state.revoke()
        self.assertFalse(phone_relay.PairingState(self.directory).authorized(token))

    def test_compound_cursor_does_not_skip_legacy_equal_timestamps(self):
        stamp=time.time()-10
        with redcn.open_cache(self.directory) as db:
            db.execute('CREATE TABLE IF NOT EXISTS results (id TEXT PRIMARY KEY, updated REAL, payload TEXT)')
            for n in range(205):db.execute('INSERT INTO results VALUES (?,?,?)',(f'{n:032x}',stamp,json.dumps({'fields':{},'titles':['房源']})))
        first=phone_relay.list_cards(self.directory);self.assertEqual(len(first['cards']),200);self.assertTrue(first['hasMore'])
        second=phone_relay.list_cards(self.directory,first['cursor']);self.assertEqual(len(second['cards']),5)
        self.assertEqual(len({row['id'] for row in first['cards']+second['cards']}),205)
        self.assertEqual(phone_relay.list_cards(self.directory,stamp)['cards'],[])

    def test_crop_zip_never_silently_substitutes_an_original(self):
        from PIL import Image
        original=self.photo();second=io.BytesIO();Image.new('RGB',(100,80),'#dd6633').save(second,'PNG')
        other=PHOTO.replace('phone-test','phone-other');photo_store.attach_photo(self.directory,other,'data:image/png;base64,'+base64.b64encode(second.getvalue()).decode())
        result_id=self.add_card([PHOTO,other]);self.pair()
        with self.assertRaises(urllib.error.HTTPError):self.request('/api/phone/cards/'+result_id+'.zip?variant=crop')
        photo_store.crop_photo(self.directory,original['id'],{'bottom':10})
        archive=zipfile.ZipFile(io.BytesIO(self.request('/api/phone/cards/'+result_id+'.zip?variant=crop')))
        manifest=json.loads(archive.read('manifest.json'));self.assertEqual(len(manifest),1);self.assertEqual(manifest[0]['kind'],'crop')

    def test_mac_profile_persists_and_phone_cannot_override_it(self):
        profile={'rules':{'Юго-Западная':'自定义站'},'prefs':{'contact':'Studio'},'data':{'style':'compact','pickup':True}}
        phone_relay.save_profile(self.directory,profile);self.assertEqual(phone_relay.load_profile(self.directory),profile)
        self.pair()
        with patch.object(self.controller,'start_import',return_value='a'*32) as start:
            self.request('/api/phone/import',{'url':URL,'rules':{'Юго-Западная':'evil'},'data':{'price':'1'}})
            passed=start.call_args.args[0];self.assertEqual(passed['rules'],profile['rules']);self.assertEqual(passed['data'],profile['data'])
        with self.assertRaises(ValueError):phone_relay.save_profile(self.directory,{'data':{'price':'1'}})
        self.assertEqual((self.directory/'phone-profile.json').stat().st_mode&0o777,0o600)

    def test_sessions_revoked_on_disabled_relay(self):
        self.pair();token=next(iter(self.jar)).value;self.server.pairing.revoke()
        self.assertFalse(phone_relay.PairingState(self.directory).authorized(token))


if __name__=='__main__':unittest.main()
