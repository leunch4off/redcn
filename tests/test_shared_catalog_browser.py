"""RCN-056: two independently paired Chrome stores read one persisted catalogue.

Owned loopback fixture only. This proves server persistence and independent
authenticated browser contexts; it does not claim physical iOS/PWA acceptance.
"""
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'cli'))
import local_server
import phone_relay
import redcn


class SharedCatalogBrowserAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise unittest.SkipTest('Optional shared-catalogue acceptance needs Playwright/Chrome')
        cls.runtime = sync_playwright().start()
        try:
            cls.browser = cls.runtime.chromium.launch(channel='chrome', headless=True)
        except Exception as error:
            cls.runtime.stop()
            raise unittest.SkipTest(str(error).splitlines()[0])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.runtime.stop()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        # Seed durable server data before either HTTP server or browser exists.
        self.result = {
            'fields': {'price': '81000', 'pricePeriod': 'month', 'priceCurrency': 'RUB',
                       'deposit': '40500', 'area': '48.5', 'metro': 'Прокшино', 'photos': []},
            'titles': ['共享服务器房源'], 'body': '服务器保存的房源说明。',
            'tags': '#莫斯科租房', 'mediaStatus': 'ready',
        }
        self.card_id = local_server.save_result(self.directory, self.result)
        self.assertTrue((self.directory / 'listings.sqlite3').is_file())
        self.assertEqual(local_server.read_result(self.directory, self.card_id)['body'], self.result['body'])
        self.controller = local_server.create_server(self.directory, 0)
        self.phone = phone_relay.create_phone_server(
            self.directory, self.controller, 0, '127.0.0.1', hosts={'127.0.0.1'})
        self.thread = threading.Thread(target=self.phone.serve_forever, daemon=True)
        self.thread.start()
        self.base = 'http://127.0.0.1:' + str(self.phone.server_port)
        self.contexts = []
        self.errors = []
        self.external_requests = []

    def tearDown(self):
        for context in self.contexts:
            context.close()
        self.phone.shutdown()
        self.phone.server_close()
        self.thread.join()
        self.controller.server_close()
        self.temp.cleanup()
        self.assertEqual(self.external_requests, [], 'The fixture must not request a live source')
        self.assertEqual(self.errors, [], 'Built companion JavaScript errors')

    def new_client(self, marker, browser_body):
        context = self.browser.new_context(viewport={'width': 393, 'height': 852})
        self.contexts.append(context)
        own_host = urlsplit(self.base).netloc

        def only_owned_origin(route):
            if urlsplit(route.request.url).netloc != own_host:
                self.external_requests.append(route.request.url)
                route.abort()
            else:
                route.continue_()

        context.route('**/*', only_owned_origin)
        seed = {'marker': marker, 'objects': [{'id': self.card_id,
                                             'title': 'BROWSER-ONLY DECOY',
                                             'body': browser_body, 'output': browser_body}]}
        context.add_init_script('''(() => {
          const seed = ''' + json.dumps(seed, ensure_ascii=False) + ''';
          if (!localStorage.getItem('shared-catalogue-client')) {
            localStorage.setItem('shared-catalogue-client', seed.marker);
            localStorage.setItem('most-objects', JSON.stringify(seed.objects));
          }
        })();''')
        page = context.new_page()
        page.on('pageerror', lambda error: self.errors.append(str(error)))
        return context, page

    def pair_and_open(self, context, page):
        self.assertEqual(context.request.get(self.base + '/api/phone/cards').status, 401)
        self.assertEqual(context.request.get(self.base + '/api/phone/cards/' + self.card_id).status, 401)
        self.assertEqual(context.cookies(), [])
        code, _ = self.phone.pairing.issue()
        page.goto(self.base + '/phone/pair#' + code, wait_until='domcontentloaded')
        page.locator('#phoneWorkspace').wait_for(state='visible')
        page.locator('#phoneCards [data-card-id="' + self.card_id + '"]').click()
        page.locator('#phoneDetail').wait_for(state='visible')
        self.assertEqual(page.locator('#phoneBody').inner_text(), self.result['body'])
        self.assertEqual(page.locator('#phoneTitleCopy').inner_text(), self.result['titles'][0])
        self.assertNotIn('#', page.url, 'The single-use pairing fragment is removed')
        state = context.request.get(self.base + '/api/phone/state').json()
        self.assertTrue(state['paired'])
        token = next(cookie['value'] for cookie in context.cookies()
                     if cookie['name'] == phone_relay.COOKIE)
        return token, state['csrfToken']

    def test_two_fresh_credentials_ignore_distinct_local_libraries_and_read_server_updates(self):
        first_context, first_page = self.new_client('FIRST-CONTEXT', 'LOCAL-FIRST-ONLY')
        second_context, second_page = self.new_client('SECOND-CONTEXT', 'LOCAL-SECOND-ONLY')
        with patch.object(redcn, 'load_listing', side_effect=AssertionError('No source import in catalogue acceptance')) as importer:
            first_token, first_csrf = self.pair_and_open(first_context, first_page)
            # Pair the second context with a fresh one-use code; no cookies or
            # storage_state are copied from the first authorized context.
            second_token, second_csrf = self.pair_and_open(second_context, second_page)
            self.assertNotEqual(first_token, second_token)
            self.assertNotEqual(first_csrf, second_csrf)
            sessions = self.phone.pairing.list_sessions()['sessions']
            self.assertEqual(len(sessions), 2)

            self.result = {**self.result, 'body': '服务器已更新，两台设备读取同一保存版本。'}
            local_server.save_result(self.directory, self.result, self.card_id)
            for context, page, marker, decoy in (
                    (first_context, first_page, 'FIRST-CONTEXT', 'LOCAL-FIRST-ONLY'),
                    (second_context, second_page, 'SECOND-CONTEXT', 'LOCAL-SECOND-ONLY')):
                page.reload(wait_until='domcontentloaded')
                page.locator('#phoneWorkspace').wait_for(state='visible')
                page.locator('#phoneCards [data-card-id="' + self.card_id + '"]').click()
                page.wait_for_function('(body) => document.getElementById("phoneBody").textContent === body',
                                       arg=self.result['body'])
                self.assertEqual(context.request.get(self.base + '/api/phone/cards/' + self.card_id).json()['body'],
                                 self.result['body'])
                self.assertEqual(page.evaluate('localStorage.getItem("shared-catalogue-client")'), marker)
                self.assertEqual(page.evaluate('JSON.parse(localStorage.getItem("most-objects"))[0].body'), decoy)
                self.assertEqual(page.locator('#phoneCards .listing-card').count(), 1)
            importer.assert_not_called()


if __name__ == '__main__':
    unittest.main()
