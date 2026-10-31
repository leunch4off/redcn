"""Provider-independent HTTPS origins preserve the phone trust boundary."""
from email.message import Message
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
import external_origin
import local_server
import phone_relay

FIRST='https://studio.tuna.am'
SECOND='https://office.ngrok-free.app'
PUBLIC=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('93.184.216.34',443))]


class PublicOriginValidation(unittest.TestCase):
    def test_origin_is_exact_https_public_domain_without_port_or_url_secrets(self):
        self.assertEqual(external_origin.normalize_external_origin('https://Studio.Tuna.Am/'),FIRST)
        self.assertEqual(external_origin.normalize_external_origin('https://xn--e1afmkfd.xn--p1ai'),'https://xn--e1afmkfd.xn--p1ai')
        for value in (None,'',' http://studio.tuna.am','https://studio.tuna.am ',
                'http://studio.tuna.am','https://studio.tuna.am:443','https://studio.tuna.am:8765',
                'https://u:p@studio.tuna.am','https://studio.tuna.am/phone/',
                'https://studio.tuna.am?token=private','https://studio.tuna.am#pair',
                'https://127.0.0.1','https://[::1]','https://192.168.1.64',
                'https://localhost','https://mac.local','https://x.internal',
                'https://x.invalid','https://x.test','https://example.com',
                'https://studio.tuna.am.evil.invalid','https://stüdio.tuna.am',
                'https://studio.tuna.am\\@127.0.0.1','https://studio.tuna.am\n'):
            with self.subTest(value=value),self.assertRaises(ValueError):
                external_origin.normalize_external_origin(value)

    def test_public_dns_validation_rejects_mixed_private_and_reserved_answers(self):
        with patch.object(external_origin.socket,'getaddrinfo',return_value=PUBLIC):
            self.assertEqual(external_origin.normalize_external_origin(FIRST,resolve=True),FIRST)
        for address in ('127.0.0.1','10.2.3.4','192.168.1.64','169.254.169.254','::1','fc00::1','192.0.2.1'):
            answers=PUBLIC+[(socket.AF_INET,socket.SOCK_STREAM,6,'',(address,443))]
            with self.subTest(address=address),patch.object(external_origin.socket,'getaddrinfo',return_value=answers),self.assertRaises(ValueError):
                external_origin.normalize_external_origin(FIRST,resolve=True)
        with patch.object(external_origin.socket,'getaddrinfo',side_effect=socket.gaierror()),self.assertRaises(ValueError):
            external_origin.normalize_external_origin(FIRST,resolve=True)

    def test_stuck_dns_has_deadline_and_cannot_create_unbounded_lookup_threads(self):
        entered=threading.Event();release=threading.Event();finished=threading.Event()
        def stuck(*_args,**_kwargs):
            entered.set();release.wait(2);finished.set();return PUBLIC
        with patch.object(external_origin.socket,'getaddrinfo',side_effect=stuck) as lookup:
            try:
                started=time.monotonic()
                with self.assertRaises(ValueError):external_origin.public_addresses('studio.tuna.am',timeout=.04)
                self.assertTrue(entered.is_set());self.assertLess(time.monotonic()-started,.5)
                with self.assertRaises(ValueError):external_origin.public_addresses('office.ngrok-free.app',timeout=.04)
                self.assertEqual(lookup.call_count,1)
            finally:
                release.set();self.assertTrue(finished.wait(1))


class CustomGatewayLifecycle(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.directory=Path(self.temp.name)
        self.controller=local_server.create_server(self.directory,0)
        self.manager=self.controller.phone_relay
        self.manager.enable(0)

    def tearDown(self):
        self.controller.server_close();self.temp.cleanup()

    def configure(self,url=FIRST):
        with patch.object(external_origin.socket,'getaddrinfo',return_value=PUBLIC):
            return self.manager.set_external_origin(url)

    def request(self,path,origin=FIRST,data=None,headers=None):
        base='http://127.0.0.1:'+str(self.manager.server.server_port)
        values={'Host':origin.removeprefix('https://'),'Origin':origin,**(headers or {})}
        if data is not None:values['Content-Type']='application/json'
        request=urllib.request.Request(base+path,headers=values,data=json.dumps(data).encode() if data is not None else None)
        try:
            with urllib.request.urlopen(request,timeout=5) as response:return response.status,response.read(),response.headers
        except urllib.error.HTTPError as error:return error.code,error.read(),error.headers

    def test_manual_mapping_stops_owned_tunnel_and_persists_private_config(self):
        with patch.object(self.manager.remote,'stop',wraps=self.manager.remote.stop) as stop:
            state=self.configure()
        self.assertTrue(stop.called)
        self.assertEqual(state['remote']['state'],'configured')
        self.assertEqual(state['remote']['provider'],'custom')
        self.assertEqual(state['urls'][0],FIRST+'/phone/')
        self.assertIn(FIRST,self.manager.server.external_origins)
        config=json.loads(self.manager.remote_preference.read_text())
        self.assertEqual(config,{'enabled':True,'provider':'custom','url':FIRST})
        self.assertEqual(self.manager.remote_preference.stat().st_mode&0o777,0o600)
        self.manager.stop()
        replacement=phone_relay.RelayManager(self.directory,self.controller)
        self.controller.phone_relay=self.manager=replacement
        with patch.object(replacement.remote,'start') as start:
            state=replacement.enable(0)
        start.assert_not_called()
        self.assertEqual(state['remote']['state'],'configured')
        self.assertIn(FIRST,replacement.server.external_origins)

    def test_switch_clear_and_late_callbacks_only_remove_their_own_origin(self):
        self.configure();self.configure(SECOND)
        self.assertEqual(self.manager.server.external_origins,{SECOND})
        self.manager._remove_remote(FIRST)
        self.assertEqual(self.manager.server.external_origins,{SECOND})
        self.manager._allow_remote('https://old-mapping.trycloudflare.com')
        self.assertEqual(self.manager.server.external_origins,{SECOND})
        self.manager._remove_remote(SECOND)
        self.assertEqual(self.manager.server.external_origins,{SECOND})
        self.manager.clear_external_origin()
        self.assertEqual(self.manager.server.external_origins,set())
        self.assertFalse(self.manager.remote_enabled)
        self.assertEqual(self.manager.remote_provider,'cloudflare')

    def test_cf_switch_removes_manual_origin_and_explicit_disable_revokes_sessions(self):
        self.configure();code,_,_=self.manager.server.pairing.current()
        token=self.manager.server.pairing.pair(code)
        with patch.object(self.manager.remote,'start') as start:
            state=self.manager.enable_remote()
        start.assert_called_once()
        self.assertNotIn(FIRST,self.manager.server.external_origins)
        self.assertEqual(state['remote']['provider'],'cloudflare')
        self.configure();self.manager.disable()
        self.assertFalse(phone_relay.PairingState(self.directory).authorized(token))
        self.assertFalse(json.loads(self.manager.remote_preference.read_text())['enabled'])

    def test_mapping_requires_pairing_secure_cookie_and_exact_host_origin(self):
        self.configure()
        self.assertEqual(self.request('/phone/')[0],200)
        self.assertEqual(self.request('/api/phone/cards')[0],401)
        code,_,_=self.manager.server.pairing.current()
        status,body,headers=self.request('/api/phone/pair',data={'code':code})
        self.assertEqual(status,200);self.assertIn('; Secure',headers['Set-Cookie'])
        cookie=headers['Set-Cookie'].split(';')[0]
        self.assertEqual(self.request('/api/phone/cards',headers={'Cookie':cookie})[0],200)
        self.assertEqual(self.request('/api/local/phone',headers={'Cookie':cookie})[0],404)
        self.assertEqual(self.request('/phone/',origin=SECOND)[0],403)
        self.assertEqual(self.request('/phone/',headers={'Origin':SECOND})[0],403)
        self.manager.disable_remote()
        self.assertEqual(self.request('/phone/')[0],403)

    def test_non_loopback_cannot_claim_external_host_even_with_forwarded_headers(self):
        self.configure();handler=object.__new__(self.manager.server.RequestHandlerClass)
        handler.headers=Message()
        for key,value in {'Host':FIRST.removeprefix('https://'),'Origin':FIRST,'X-Forwarded-For':'127.0.0.1','X-Forwarded-Proto':'https'}.items():handler.headers[key]=value
        handler.client_address=('192.168.1.20',12345);handler.server=self.manager.server;responses=[]
        handler.respond=lambda *args:responses.append(args)
        self.assertFalse(handler.permitted(auth=False));self.assertEqual(responses[0][0],403)

    def test_legacy_preference_keeps_cf_mode_and_invalid_update_does_not_break_mapping(self):
        self.configure()
        with self.assertRaises(ValueError):self.manager.set_external_origin('https://127.0.0.1')
        self.assertEqual(self.manager.server.external_origins,{FIRST})
        self.manager.stop();self.manager.remote_preference.write_text('{"enabled":true}')
        legacy=phone_relay.RelayManager(self.directory,self.controller)
        self.assertTrue(legacy.remote_enabled);self.assertEqual(legacy.remote_provider,'cloudflare')


if __name__=='__main__':unittest.main()
