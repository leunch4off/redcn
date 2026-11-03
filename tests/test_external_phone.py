"""Exact tunnel-origin allowlist, protected previews and compressed delivery."""
import gzip
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
import local_server
import phone_relay


class ExternalPhoneBoundary(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.controller=local_server.create_server(Path(self.temp.name),0)
        self.server=phone_relay.create_phone_server(self.temp.name,self.controller,0,'127.0.0.1',hosts={'127.0.0.1'})
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base='http://127.0.0.1:'+str(self.server.server_port);self.origin='https://test-approved.trycloudflare.com'
        self.server.external_origins.add(self.origin)

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.controller.server_close();self.temp.cleanup()

    def request(self,path,data=None,headers=None):
        values={'Host':self.origin.removeprefix('https://'),'Origin':self.origin,**(headers or {})}
        if data is not None:values['Content-Type']='application/json'
        request=urllib.request.Request(self.base+path,headers=values,data=json.dumps(data).encode() if data is not None else None)
        try:
            with urllib.request.urlopen(request,timeout=5) as response:return response.status,response.read(),response.headers
        except urllib.error.HTTPError as error:return error.code,error.read(),error.headers

    def test_exact_https_origin_secure_cookie_and_private_routes(self):
        self.assertEqual(self.request('/phone/')[0],200)
        for path in ('/api/phone/cards','/api/phone/design','/api/phone/previews/'+'a'*64,'/api/local/phone'):
            self.assertEqual(self.request(path)[0],401)
        code,_=self.server.pairing.issue()
        status,body,headers=self.request('/api/phone/pair',{'code':code})
        self.assertEqual(status,200);self.assertIn('; Secure',headers['Set-Cookie'])
        cookie=headers['Set-Cookie'].split(';')[0]
        self.assertEqual(self.request('/api/phone/cards',headers={'Cookie':cookie})[0],200)
        self.assertEqual(self.request('/api/phone/cards',headers={'Cookie':cookie,'Origin':'https://evil.test'})[0],403)
        self.assertEqual(self.request('/phone/',headers={'Host':'unapproved.trycloudflare.com'})[0],403)
        self.assertEqual(self.request('/phone/',headers={'Origin':self.origin.replace('https:','http:')})[0],403)
        self.server.external_origins.clear()
        self.assertEqual(self.request('/api/phone/cards',headers={'Cookie':cookie})[0],403)

    def test_gzip_shell_is_identical_and_anonymous_cache_stays_disabled(self):
        status,raw,_=self.request('/phone/')
        status,compressed,headers=self.request('/phone/',headers={'Accept-Encoding':'gzip'})
        self.assertEqual(status,200);self.assertEqual(gzip.decompress(compressed),raw)
        self.assertLess(len(compressed),len(raw)/2)
        self.assertEqual(headers['Content-Encoding'],'gzip');self.assertEqual(headers['Cache-Control'],'no-store')
        self.assertIn('Accept-Encoding',headers['Vary'])

    def test_explicit_remote_stop_removes_origin_and_disable_revokes_preference(self):
        manager=self.controller.phone_relay
        with patch.object(manager.remote,'start',return_value={'state':'starting'}),patch.object(manager.remote,'stop'):
            manager.enable(0)
            manager.enable_remote();self.assertTrue(manager.remote_enabled)
            manager._allow_remote(self.origin);self.assertIn(self.origin,manager.server.external_origins)
            manager._remove_remote(self.origin);self.assertNotIn(self.origin,manager.server.external_origins)
            with self.assertRaises(ValueError):manager._allow_remote('https://evil.test')
            manager.disable_remote();self.assertFalse(manager.remote_enabled)


if __name__=='__main__':unittest.main()
