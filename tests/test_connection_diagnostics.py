"""Anonymous diagnostics must remain bounded and confined to the local API."""
import json
from pathlib import Path
import re
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'cli'))
import connection_diagnostics
import local_server


ORIGIN = 'https://test-diagnostics.trycloudflare.com'


class DiagnosticsChecks(unittest.TestCase):
    def wait(self, manager):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            result = manager.status()
            if result['state'] == 'done':
                return result
            time.sleep(.005)
        self.fail('Diagnostics did not finish within its bounded timeout')

    def test_only_owned_ready_quick_origin_is_probed_without_private_fields(self):
        requests = []
        manager = connection_diagnostics.ConnectionDiagnostics(
            fetch=lambda url, timeout, **kwargs: requests.append(url) or 'HTTP 200',
            resolve=lambda *a, **k: [(None, None, None, None, ('1.1.1.1', 443))])
        manager.start({'enabled':True, 'port':8767, 'pairCode':'PRIVATE',
            'remote':{'state':'ready', 'url':ORIGIN}})
        report = self.wait(manager)
        self.assertEqual(requests, ['http://127.0.0.1:8767/phone/', ORIGIN + '/phone/'])
        self.assertTrue(all(step['state'] == 'ok' for step in report['steps']))
        self.assertNotIn('PRIVATE', json.dumps(report))
        for origin in ('https://evil.test', ORIGIN + '/private', ORIGIN + '.evil.test'):
            requests.clear()
            manager.start({'enabled':False, 'remote':{'state':'ready', 'url':origin}})
            report = self.wait(manager)
            self.assertEqual(requests, [])
            self.assertIsNone(report['origin'])

    def test_private_dns_answer_never_reaches_https_fetch(self):
        requests = []
        manager = connection_diagnostics.ConnectionDiagnostics(
            fetch=lambda url, timeout, **kwargs: requests.append(url) or 'HTTP 200',
            resolve=lambda *a, **k: [(None, None, None, None, ('127.0.0.1', 443))])
        manager.start({'enabled':False, 'remote':{'state':'ready', 'url':ORIGIN}})
        result = self.wait(manager)
        self.assertEqual(requests, [])
        self.assertEqual(next(s for s in result['steps'] if s['id'] == 'dns')['state'], 'error')

    def test_configured_custom_origin_is_checked_as_an_address_not_owned_process(self):
        requests = []
        manager = connection_diagnostics.ConnectionDiagnostics(
            fetch=lambda url, timeout, **kwargs: requests.append((url, kwargs)) or 'HTTP 200',
            resolve=lambda *a, **k: [(None, None, None, None, ('1.1.1.1', 443))])
        manager.start({'enabled':False, 'remote':{'provider':'custom',
            'state':'configured', 'url':'https://studio.tuna.am'}})
        result = self.wait(manager)
        self.assertEqual(requests, [('https://studio.tuna.am/phone/', {'addresses':['1.1.1.1']})])
        self.assertEqual(next(s for s in result['steps'] if s['id'] == 'tunnel')['label'], 'Адрес стороннего шлюза')

    def test_slow_dns_has_deadline_and_repeated_checks_do_not_spawn_resolvers(self):
        release = threading.Event()
        calls = []
        def resolve(*_a, **_k):
            calls.append(1)
            release.wait(2)
            return []
        manager = connection_diagnostics.ConnectionDiagnostics(resolve=resolve, dns_timeout=.03)
        snapshot = {'enabled':False, 'remote':{'state':'ready', 'url':ORIGIN}}
        try:
            for _ in range(3):
                started = time.monotonic()
                manager.start(snapshot)
                result = self.wait(manager)
                self.assertLess(time.monotonic() - started, .5)
                self.assertEqual(next(s for s in result['steps'] if s['id'] == 'dns')['state'], 'error')
                self.assertEqual(next(s for s in result['steps'] if s['id'] == 'https')['state'], 'skipped')
            self.assertEqual(len(calls), 1)
        finally:
            release.set()

    def test_fetch_rejects_redirect_and_wrong_shell(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_GET(self):
                if self.path == '/redirect':
                    self.send_response(302); self.send_header('Location', '/wrong'); self.end_headers()
                else:
                    self.send_response(200); self.end_headers(); self.wfile.write(b'not redcn')
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        base = 'http://127.0.0.1:' + str(server.server_port)
        try:
            with self.assertRaises(urllib.error.HTTPError) as caught:
                connection_diagnostics.fetch_shell(base + '/redirect', 1)
            self.assertEqual(caught.exception.code, 302)
            with self.assertRaises(ValueError):
                connection_diagnostics.fetch_shell(base + '/wrong', 1)
        finally:
            server.shutdown(); server.server_close(); thread.join()


class DiagnosticsLocalApi(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.server = local_server.create_server(Path(self.temp.name), 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.base = 'http://127.0.0.1:' + str(self.server.server_port)
        with urllib.request.urlopen(self.base) as response:
            self.token = re.search(r'name="redcn-token" content="([^"]+)"', response.read().decode())[1]

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(); self.temp.cleanup()

    def call(self, data=None, token=True, origin=None):
        headers = {'Content-Type':'application/json'}
        if token: headers['X-Redcn-Token'] = self.token
        if origin: headers['Origin'] = origin
        req = urllib.request.Request(self.base + '/api/local/phone',
            json.dumps(data).encode() if data is not None else None, headers)
        try:
            with urllib.request.urlopen(req) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def test_check_requires_desktop_token_and_exact_origin_and_does_not_enable_access(self):
        self.assertEqual(self.call({'action':'diagnose'}, token=False)[0], 403)
        self.assertEqual(self.call({'action':'diagnose'}, origin='https://evil.test')[0], 403)
        status, value = self.call({'action':'diagnose', 'url':'https://evil.test'})
        self.assertEqual(status, 200)
        self.assertFalse(value['enabled'])
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            status, value = self.call()
            if value['diagnostics']['state'] == 'done': break
            time.sleep(.005)
        self.assertIsNone(value['diagnostics']['origin'])
        self.assertEqual(value['diagnostics']['state'], 'done')

    def test_custom_gateway_actions_use_protected_local_api_and_normalized_url(self):
        self.server.phone_relay.enable(0)
        with patch('external_origin.public_addresses', return_value=('1.1.1.1',)):
            status, value = self.call({'action':'external-set', 'url':'https://Studio.Tuna.Am/'})
        self.assertEqual(status, 200)
        self.assertEqual(value['remote']['state'], 'configured')
        self.assertEqual(value['remote']['url'], 'https://studio.tuna.am')
        self.assertEqual(self.call({'action':'external-clear'}, token=False)[0], 403)
        self.assertEqual(self.call({'action':'external-clear'})[1]['remote']['provider'], 'cloudflare')


if __name__ == '__main__': unittest.main()
