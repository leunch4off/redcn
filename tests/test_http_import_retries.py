"""No external source requests: mock transports and owned loopback HTTP child."""
import base64
import contextlib
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import io
import json
from pathlib import Path
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
from unittest.mock import MagicMock,patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
import redcn

URL='https://www.cian.ru/rent/flat/123/'
AVITO='https://www.avito.ru/moskva/kvartiry/test_123'


class HTTPImportRetryTests(unittest.TestCase):
    def transport(self,mime='text/html',body=b'<p>Listing</p>'):
        opener=MagicMock();response=opener.open.return_value.__enter__.return_value
        response.headers.get_content_type.return_value=mime;response.read.return_value=body
        return opener,response,opener.open.return_value

    def test_transient_network_and_server_errors_retry_exactly_once(self):
        errors=[TimeoutError('temporary'),urllib.error.URLError(ConnectionResetError('reset'))]
        errors += [urllib.error.HTTPError(URL,status,'temporary',{},None) for status in (500,502,503,504)]
        for error in errors:
            with self.subTest(error=error):
                opener,response,good=self.transport();opener.open.side_effect=[error,good]
                events=[]
                with patch.object(redcn,'allowed_url',return_value=URL),patch.object(redcn.urllib.request,'build_opener',return_value=opener):
                    self.assertEqual(redcn._fetch_bytes_direct(URL,status=lambda message,**fields:events.append(fields))[1],'text/html')
                self.assertEqual(opener.open.call_count,2);self.assertEqual([row['httpAttempt'] for row in events],[1,2]);self.assertLessEqual(opener.open.call_args.kwargs['timeout'],15)

    def test_protection_permanent_status_and_tls_never_retry(self):
        errors=[urllib.error.HTTPError(URL,status,'blocked',{},None) for status in (401,403,404,429,501)]
        errors += [urllib.error.URLError(ssl.SSLCertVerificationError('bad certificate')),urllib.error.URLError(ssl.SSLError('handshake failed'))]
        for error in errors:
            with self.subTest(error=error):
                opener,_,_=self.transport();opener.open.side_effect=error
                with patch.object(redcn,'allowed_url',return_value=URL),patch.object(redcn.urllib.request,'build_opener',return_value=opener),self.assertRaises((OSError,urllib.error.URLError)):
                    redcn._fetch_bytes_direct(URL)
                self.assertEqual(opener.open.call_count,1)

    def test_mime_size_and_exhausted_budget_do_not_create_second_attempt(self):
        for mime,body in [('application/json',b'{}'),('text/html',b'x'*(redcn.MAX_HTML+1))]:
            opener,_,_=self.transport(mime,body)
            with patch.object(redcn,'allowed_url',return_value=URL),patch.object(redcn.urllib.request,'build_opener',return_value=opener),self.assertRaises(ValueError):redcn._fetch_bytes_direct(URL)
            self.assertEqual(opener.open.call_count,1)
        opener,_,_=self.transport();opener.open.side_effect=TimeoutError('too late')
        with patch.object(redcn,'allowed_url',return_value=URL),patch.object(redcn.urllib.request,'build_opener',return_value=opener),self.assertRaises(TimeoutError):redcn._fetch_bytes_direct(URL,timeout=.02)
        self.assertEqual(opener.open.call_count,1)

    def test_visible_challenge_is_checked_before_fast_structured_parser(self):
        markup='<div data-name="Description">Подтвердите, что вы не робот. Квартира 52 м², 80 000 ₽.</div>'
        with patch.object(redcn.subprocess,'run') as node,self.assertRaises(redcn.ChallengeError):redcn.parse_html(markup,URL)
        node.assert_not_called()

    def test_blocked_auto_import_does_not_replay_protection_through_browser(self):
        import browser_import
        for error in (redcn.ChallengeError('Проверка браузера'),urllib.error.HTTPError(URL,403,'Forbidden',{},None),urllib.error.HTTPError(URL,429,'Limit',{},None),urllib.error.URLError(ssl.SSLCertVerificationError('invalid'))):
            with self.subTest(error=error),tempfile.TemporaryDirectory() as folder:
                connection=redcn.open_cache(Path(folder))
                try:
                    with patch.object(redcn,'fetch_bytes',side_effect=error),patch.object(browser_import,'load_browser') as browser,self.assertRaisesRegex(redcn.ChallengeError,'вручную|вставьте'):
                        redcn.load_listing(URL,connection,loader='auto')
                    browser.assert_not_called();self.assertEqual(connection.execute('SELECT COUNT(*) FROM cache').fetchone()[0],0)
                finally:connection.close()

    def test_cian_and_avito_https_use_same_cli_import_boundary_and_fail_clearly(self):
        for url,fixture in (('https://www.cian.ru/rent/flat/300691182/','cian-54.html'),(AVITO,'avito-52.html')):
            with self.subTest(url=url),tempfile.TemporaryDirectory() as folder:
                raw=(Path(__file__).parent/'fixtures'/fixture).read_bytes()
                with patch.object(redcn,'fetch_bytes',return_value=(raw,'text/html')) as fetch,contextlib.redirect_stdout(io.StringIO()) as output,contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(redcn.main([url,'--loader','http','--cache-dir',folder,'--json']),0)
                result=json.loads(output.getvalue());self.assertEqual(result['fields']['url'],url);self.assertTrue(result['body']);fetch.assert_called_once()
                with patch.object(redcn,'fetch_bytes',side_effect=urllib.error.HTTPError(url,403,'Forbidden',{},None)),contextlib.redirect_stderr(io.StringIO()),self.assertRaisesRegex(redcn.ChallengeError,'HTTP 403'):
                    redcn.main([url,'--loader','http','--cache-dir',folder,'--refresh','--json'])

    def test_supervisor_rejects_unsafe_urls_before_process_and_cancel_before_cache(self):
        with patch.object(redcn.subprocess,'Popen') as spawn:
            for url in ('http://cian.ru/x','https://user:pass@cian.ru/x','https://cian.ru.evil.invalid/x'):
                with self.assertRaises(ValueError):redcn.fetch_bytes(url)
            spawn.assert_not_called()
        cancel=threading.Event();cancel.set()
        with patch.object(redcn.subprocess,'Popen') as spawn,self.assertRaisesRegex(ValueError,'отменён'):redcn.fetch_bytes(URL,cancel=cancel)
        spawn.assert_not_called()


class LoopbackHTTPWorkerTests(unittest.TestCase):
    def setUp(self):
        self.requests=0;self.slow=False;self.first503=False
        owner=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                owner.requests+=1
                if owner.first503 and owner.requests==1:self.send_response(503);self.end_headers();return
                self.send_response(200);self.send_header('Content-Type','text/html');self.send_header('Transfer-Encoding','chunked');self.end_headers()
                try:
                    for _ in range(20 if owner.slow else 2):
                        chunk=b'<p>owned loopback fixture</p>';self.wfile.write(('%x\r\n'%len(chunk)).encode()+chunk+b'\r\n');self.wfile.flush();time.sleep(.05 if owner.slow else .005)
                    self.wfile.write(b'0\r\n\r\n')
                except (BrokenPipeError,ConnectionResetError):pass
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.server.daemon_threads=True;self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.url='http://127.0.0.1:'+str(self.server.server_port)
        # The production parent still validates HTTPS CIAN/Avito. Only this test
        # child substitutes its owned loopback transport; no runtime bypass exists.
        code='import sys;sys.path.insert(0,'+repr(str(redcn.ROOT/'cli'))+');import redcn;redcn.allowed_url=lambda *args,**kwargs:'+repr(self.url)+';import http_fetch_worker;http_fetch_worker.main()'
        self.command=[sys.executable,'-c',code]

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join()

    def test_owned_http_worker_streams_body_and_reports_retry_without_external_requests(self):
        self.first503=True;events=[]
        with patch.object(redcn,'_http_worker_command',return_value=self.command):
            content,mime=redcn.fetch_bytes(URL,timeout=3,status=lambda message,**fields:events.append(fields))
        self.assertIn(b'owned loopback',content);self.assertEqual(mime,'text/html');self.assertEqual(self.requests,2);self.assertEqual([row['httpAttempt'] for row in events],[1,2])

    def test_trickle_body_obeys_total_parent_deadline_and_cancel_reaps_owned_process(self):
        self.slow=True;started=time.monotonic()
        with patch.object(redcn,'_http_worker_command',return_value=self.command),self.assertRaises(TimeoutError):redcn.fetch_bytes(URL,timeout=.2)
        self.assertLess(time.monotonic()-started,1.2)
        cancel=threading.Event();timer=threading.Timer(.1,cancel.set);timer.start();started=time.monotonic()
        try:
            with patch.object(redcn,'_http_worker_command',return_value=self.command),self.assertRaisesRegex(ValueError,'отменён'):redcn.fetch_bytes(URL,timeout=3,cancel=cancel)
            self.assertLess(time.monotonic()-started,1.2)
        finally:timer.cancel()


if __name__=='__main__':unittest.main()
