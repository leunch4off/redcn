import importlib.util
import base64
import json
from pathlib import Path
import re
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch, MagicMock
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'cli'))
import redcn
import browser_import
import local_server

URL = 'https://www.cian.ru/rent/flat/300691182/'
TEXT = '1-комнатная квартира, 38 м². Аренда 70 000 ₽. Залог 35 000 ₽. Метро Прокшино, 12 минут пешком. Холодильник и стиральная машина.'


class ImportTests(unittest.TestCase):
    def test_raw_search_parameters_are_not_html_entities(self):
        url = 'https://www.cian.ru/cat.php?deal_type=rent&region=1&resume=1'
        self.assertEqual(redcn.normalize_url(url), url)
        self.assertEqual(redcn.normalize_url(url.replace('&', '&amp;')), url)
        self.assertEqual(redcn.normalize_url(url.replace('&', '&#38;')), url)

    def test_markdown_and_pasted_error_cleanup(self):
        for value in [f'[{URL}]({URL})', URL+'Ошибка: <urlopen error timed out>', '<'+URL+'>', URL+'\nОшибка: timed out']:
            self.assertEqual(redcn.normalize_url(value), URL)
        for value in ['https://cian.ru.evil.org/1', 'http://cian.ru/1', 'https://user:pass@cian.ru/1']:
            with self.assertRaises(ValueError): redcn.normalize_url(value)

    def test_challenge_script_name_is_not_a_challenge(self):
        result = redcn.parse_html('<script src="captcha.js"></script><p>'+TEXT+'</p>', URL)
        self.assertIn('70 000',result['text'])

    def test_auto_falls_back_once_and_caches_browser_result(self):
        with tempfile.TemporaryDirectory() as folder:
            conn = redcn.open_cache(Path(folder))
            with patch.object(redcn,'fetch_bytes',side_effect=TimeoutError('TLS timeout')) as http, patch.object(browser_import,'load_browser',return_value={'url':URL,'text':TEXT,'images':[],'loader':'chrome'}) as browser:
                first = redcn.load_listing(URL,conn)
                second = redcn.load_listing(URL,conn)
                self.assertEqual(http.call_count,1);self.assertEqual(browser.call_count,1)
                self.assertFalse(first['cached']);self.assertTrue(second['cached'])
            conn.close()

    def test_http_mode_does_not_open_browser_on_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            conn = redcn.open_cache(Path(folder))
            with patch.object(redcn,'fetch_bytes',side_effect=TimeoutError('TLS timeout')),patch.object(browser_import,'load_browser') as browser:
                with self.assertRaises(TimeoutError): redcn.load_listing(URL,conn,loader='http')
                browser.assert_not_called()
            conn.close()

    def test_readiness_rejects_captcha_and_removed_listing(self):
        base = {'text':TEXT,'body':TEXT,'title':'Аренда квартиры'}
        self.assertEqual(browser_import.listing_ready(base),(True,False,False))
        self.assertFalse(browser_import.listing_ready({**base,'body':'Подтвердите, что вы не робот'})[0])
        self.assertFalse(browser_import.listing_ready({**base,'title':'Объявление снято'})[0])
        self.assertFalse(browser_import.listing_ready({**base,'text':'Недостаточно текста'})[0])

    def test_visible_chrome_closes_after_stable_read(self):
        page, context = MagicMock(), MagicMock()
        page.url=URL;page.is_closed.return_value=False
        snapshot={'text':TEXT,'fallback':TEXT,'body':TEXT,'title':'Аренда квартиры','metadata':'','images':[]}
        page.evaluate.side_effect=lambda script,*_args: {'top':100,'height':900,'bottom':True} if script==browser_import.SCROLL else snapshot;context.pages=[page];context.new_page.return_value=page
        pw=MagicMock();pw.chromium.launch_persistent_context.return_value=context
        api=MagicMock();api.sync_playwright.return_value.__enter__.return_value=pw
        api.Error=type('FakeBrowserError',(Exception,),{});api.TimeoutError=type('FakeBrowserTimeout',(api.Error,),{})
        with tempfile.TemporaryDirectory() as folder,patch.dict(sys.modules,{'playwright':MagicMock(),'playwright.sync_api':api}):
            result=browser_import.load_browser(URL,Path(folder),redcn.parse_html,lambda value,**_:value)
            self.assertEqual(result['loader'],'chrome');context.close.assert_called_once()
            self.assertFalse(pw.chromium.launch_persistent_context.call_args.kwargs['headless'])
            self.assertNotIn('args',pw.chromium.launch_persistent_context.call_args.kwargs)

    def test_cancel_closes_chrome(self):
        page,context,pw,api=MagicMock(),MagicMock(),MagicMock(),MagicMock()
        page.url=URL;context.pages=[page];pw.chromium.launch_persistent_context.return_value=context
        api.sync_playwright.return_value.__enter__.return_value=pw
        api.Error=type('FakeBrowserError',(Exception,),{});api.TimeoutError=type('FakeBrowserTimeout',(api.Error,),{})
        event=threading.Event();event.set()
        with tempfile.TemporaryDirectory() as folder,patch.dict(sys.modules,{'playwright':MagicMock(),'playwright.sync_api':api}):
            with self.assertRaisesRegex(ValueError,'отменён'): browser_import.load_browser(URL,Path(folder),redcn.parse_html,lambda value,**_:value,cancel=event)
            context.close.assert_called_once()

    def test_pending_blank_navigation_is_waited_for_not_rejected_as_bad_url(self):
        page,context,pw,api=MagicMock(),MagicMock(),MagicMock(),MagicMock()
        page.url='about:blank';page.is_closed.return_value=False;context.pages=[page];context.new_page.return_value=page
        page.wait_for_timeout.side_effect=lambda _:setattr(page,'url',URL)
        page.evaluate.side_effect=lambda script,*_args: {'top':100,'height':900,'bottom':True} if script==browser_import.SCROLL else {'text':TEXT,'fallback':TEXT,'body':TEXT,'title':'Аренда квартиры','metadata':'','images':[]}
        pw.chromium.launch_persistent_context.return_value=context
        api.sync_playwright.return_value.__enter__.return_value=pw
        api.Error=type('FakeBrowserError',(Exception,),{});api.TimeoutError=type('FakeBrowserTimeout',(api.Error,),{})
        validate=MagicMock(side_effect=lambda value,**_:value)
        with tempfile.TemporaryDirectory() as folder,patch.dict(sys.modules,{'playwright':MagicMock(),'playwright.sync_api':api}):
            result=browser_import.load_browser(URL,Path(folder),redcn.parse_html,validate,channel='chromium')
            self.assertEqual(result['browser_channel'],'chromium')
            self.assertFalse(any(call.args[0]=='about:blank' for call in validate.call_args_list))
            self.assertIsNone(pw.chromium.launch_persistent_context.call_args.kwargs['channel'])


class LocalServerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.directory=Path(self.temp.name)
        self.server=local_server.create_server(self.directory,0)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base=f'http://127.0.0.1:{self.server.server_port}'
        self.page=urllib.request.urlopen(self.base+'/').read().decode()
        self.token=re.search(r'name="redcn-token" content="([^"]+)"',self.page)[1]

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.temp.cleanup()

    def request(self,path,data=None,extra=None,token=True):
        headers={'Content-Type':'application/json',**({'X-Redcn-Token':self.token} if token else {}),**(extra or {})}
        req=urllib.request.Request(self.base+path,data=json.dumps(data).encode() if data is not None else None,headers=headers)
        with urllib.request.urlopen(req) as response:return json.loads(response.read())

    def test_loopback_meta_and_cross_origin_rejection(self):
        self.assertIn('content="local"',self.page);self.assertIn('<base href="/">',self.page)
        for extra,token in [({'Origin':'https://evil.example'},True),({'Host':'evil.example'},True),({},False)]:
            with self.assertRaises(urllib.error.HTTPError) as caught: self.request('/api/local/import',{'url':URL},extra,token)
            self.assertEqual(caught.exception.code,403)

    def test_repeat_start_reuses_running_server_and_saved_link(self):
        with patch.object(local_server.webbrowser,'open') as opened,patch.object(local_server,'create_server',wraps=local_server.create_server) as create:
            self.assertEqual(local_server.serve(self.directory,self.server.server_port,result_id='a'*32),0)
            opened.assert_called_once_with(self.base+'/listing/'+'a'*32)
            self.assertEqual(create.call_count,1)
        self.assertFalse(local_server.matching_server(self.directory/'different-data',self.server.server_port,'chrome'))
        self.assertFalse(local_server.matching_server(self.directory,self.server.server_port,'chromium'))

    def test_foreign_listener_is_kept_and_next_port_is_used(self):
        with socket.socket() as occupied:
            occupied.bind(('127.0.0.1',0));occupied.listen()
            port=occupied.getsockname()[1]
            with patch.object(local_server,'matching_server',return_value=False):
                server,selected=local_server.bind_or_reuse(self.directory,port)
            try:
                self.assertGreater(selected,port)
                self.assertEqual(occupied.getsockname()[1],port)
            finally: server.server_close()

    def test_health_needs_correct_origin_but_never_exposes_token_or_directory(self):
        health=self.request('/api/local/health',token=False)
        self.assertEqual(health['app'],'redcn');self.assertEqual(len(health['storage']),64)
        self.assertNotIn(self.token,json.dumps(health));self.assertNotIn(str(self.directory),json.dumps(health))
        with self.assertRaises(urllib.error.HTTPError) as caught: self.request('/api/local/health',extra={'Origin':'https://evil.example'},token=False)
        self.assertEqual(caught.exception.code,403)

    def test_job_to_durable_link_and_saved_word_notes(self):
        with patch.object(redcn,'load_listing',return_value={'url':URL,'text':TEXT,'images':[],'cached':False}):
            start=self.request('/api/local/import',{'url':URL,'data':{'phraseNotes':'家具齐全 | Вся мебель | 家具配备齐全'}})
            for _ in range(100):
                job=self.request('/api/local/jobs/'+start['job_id'])
                if job['state']!='running': break
                time.sleep(.01)
        self.assertEqual(job['state'],'done',job)
        result=self.request('/api/local/results/'+job['result_id'])
        self.assertEqual(result['fields']['price'],'70000');self.assertIn('家具齐全',result['fields']['phraseNotes'])
        result['body']='Моя правка';saved=self.request('/api/local/save',{'result_id':job['result_id'],'result':result})
        self.assertEqual(local_server.read_result(self.directory,saved['result_id'])['body'],'Моя правка')
        page=urllib.request.urlopen(self.base+'/listing/'+saved['result_id']).read().decode()
        self.assertIn('content="local"',page)

    def test_invalid_import_and_result_are_rejected(self):
        for path,data in [('/api/local/import',{'url':URL,'timeout':99999}),('/api/local/import',{'url':'https://localhost/'}),('/api/local/save',{'result':{}})]:
            with self.assertRaises(urllib.error.HTTPError) as caught: self.request(path,data)
            self.assertEqual(caught.exception.code,400)

    def test_failed_job_never_creates_result(self):
        with patch.object(redcn,'load_listing',side_effect=ValueError('Импорт отменён')):
            start=self.request('/api/local/import',{'url':URL})
            for _ in range(100):
                job=self.request('/api/local/jobs/'+start['job_id'])
                if job['state']!='running': break
                time.sleep(.01)
        self.assertEqual(job['state'],'error');self.assertNotIn('result_id',job)

    def test_history_persists_edited_fields_and_png_after_successful_action(self):
        png=b'\x89PNG\r\n\x1a\nfixture'
        entry={'version':1,'id':'event-12345678','kind':'copy-all','at':'2026-10-30T00:00:00Z','title':'标题','body':'正文已编辑','tags':'#莫斯科','fields':{'price':'70000','featureCounts':{'slidingWardrobe':'2'}},'png':'data:image/png;base64,'+base64.b64encode(png).decode()}
        self.request('/api/local/history',{'entry':entry})
        listed=self.request('/api/local/history')['entries'];self.assertEqual(len(listed),1);self.assertTrue(listed[0]['hasSnapshot'])
        saved=self.request('/api/local/history/event-12345678')['entry'];self.assertEqual(saved['body'],'正文已编辑');self.assertEqual(saved['fields']['featureCounts']['slidingWardrobe'],'2');self.assertNotIn('png',saved)
        self.assertEqual(local_server.history_rows(self.directory,'event-12345678',image=True),png)

    def test_history_rejects_non_png_and_unauthenticated_writes(self):
        entry={'version':1,'id':'event-12345678','kind':'save','at':'2026-10-30T00:00:00Z','title':'标题','body':'正文','tags':'','fields':{},'png':'data:image/svg+xml;base64,AAAA'}
        with self.assertRaises(urllib.error.HTTPError) as caught:self.request('/api/local/history',{'entry':entry})
        self.assertEqual(caught.exception.code,400)
        with self.assertRaises(urllib.error.HTTPError) as caught:self.request('/api/local/history',{'entry':entry},token=False)
        self.assertEqual(caught.exception.code,403)

    def test_real_html_fixture_uses_shared_analyzer_in_python(self):
        path=Path(__file__).parent/'fixtures/cian-54.html'
        listing=redcn.parse_html(path.read_text(),URL)
        self.assertEqual(listing['fields']['price'],'70000');self.assertEqual(listing['fields']['deposit'],'140000');self.assertEqual(listing['fields']['registrationPolicy'],'not_available')
        self.assertIn('tvStand',listing['fields']['features']);self.assertNotIn('tv',listing['fields']['features']);self.assertEqual(len(listing['images']),6)


if __name__=='__main__': unittest.main()
