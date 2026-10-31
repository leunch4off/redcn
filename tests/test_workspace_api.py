"""HTTP authorization, ten real job hooks, and conflict-safe route edits."""
import io
import json
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch
import urllib.error
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'cli'))
import local_server
import phone_relay
import test_phone_relay as relay_acceptance


class WorkspaceHTTPTests(unittest.TestCase):
    setUp = relay_acceptance.PhoneRelayTests.setUp
    tearDown = relay_acceptance.PhoneRelayTests.tearDown
    request = relay_acceptance.PhoneRelayTests.request
    pair = relay_acceptance.PhoneRelayTests.pair
    add_card = relay_acceptance.PhoneRelayTests.add_card

    def wait_for(self, predicate):
        end = time.monotonic()+4
        while time.monotonic()<end:
            value=predicate()
            if value:return value
            time.sleep(.01)
        self.fail('Isolated job did not finish within its test budget')

    def test_new_routes_keep_phone_auth_and_csrf_boundaries(self):
        for path in ('listing-watch', 'listing-watch/items', 'import-diagnostics', 'import-diagnostics/tg.zip'):
            with self.assertRaises(urllib.error.HTTPError) as caught:self.request('/api/phone/'+path)
            self.assertEqual(caught.exception.code, 401)
        self.pair()
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request('/api/phone/import-diagnostics', {'action':'arm','target':10},headers={'X-Phone-CSRF':'wrong'})
        self.assertEqual(caught.exception.code,403)
        self.assertEqual(self.controller.import_diagnostics.status()['state'],'idle')
        self.assertFalse(self.request('/api/phone/listing-watch')['config']['enabled'])
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request('/api/phone/listing-watch/items?category=private')
        self.assertEqual(caught.exception.code,400)

    def test_ten_manual_jobs_finish_recording_but_eleventh_import_still_works(self):
        self.pair()
        self.request('/api/phone/import-diagnostics', {'action':'arm','target':10})
        with patch('node_runtime.resolve_node',side_effect=ValueError('isolated preflight fixture')):
            for n in range(10):
                result=self.request('/api/phone/import',{'url':'https://www.cian.ru/rent/flat/'+str(990000+n)+'/'})
                self.wait_for(lambda:self.controller.job_status(result['job_id']).get('state')=='error')
                self.wait_for(lambda:self.controller.import_diagnostics.status()['completed']==n+1)
            ready=self.wait_for(lambda:self.controller.import_diagnostics.status().get('bundleReady'))
            self.assertTrue(ready)
            status=self.request('/api/phone/import-diagnostics')
            self.assertFalse(status['enabled']);self.assertEqual(status['completed'],10)
            archive=self.request('/api/phone/import-diagnostics/tg.zip')
            with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
                self.assertIn('manifest.json',bundle.namelist())
                self.assertTrue(all(not name.startswith('/') and '..' not in name for name in bundle.namelist()))
            again=self.request('/api/phone/import',{'url':'https://www.cian.ru/rent/flat/999999/'})
            self.wait_for(lambda:self.controller.job_status(again['job_id']).get('state')=='error')
            self.assertEqual(self.controller.import_diagnostics.status()['reserved'],10)
        self.assertEqual(phone_relay.list_cards(self.directory)['cards'],[])

    def test_route_edit_preserves_text_fields_and_rejects_stale_version(self):
        self.pair();card_id=self.add_card(description='Личный текст',universities='МГУ | 35 | public |')
        before=phone_relay.public_card(self.directory,card_id)
        body={'routes':[{'name':'МГУ','time':'35','mode':'public','transport':'Автобус 119'}], 'expectedUpdated':before['updated']}
        after=self.request('/api/phone/cards/'+card_id+'/routes',body)
        self.assertEqual(after['body'],before['body']);self.assertEqual(after['titles'],before['titles'])
        self.assertEqual(after['fields']['description'],'Личный текст')
        self.assertEqual(after['fields']['universities'],before['fields']['universities'])
        self.assertEqual(after['fields']['universityRoutes'][0]['transport'],'Автобус 119')
        with self.assertRaises(urllib.error.HTTPError) as caught:self.request('/api/phone/cards/'+card_id+'/routes',body)
        self.assertEqual(caught.exception.code,409)
        cleared=self.request('/api/phone/cards/'+card_id+'/routes',{'routes':[],'expectedUpdated':after['updated']})
        self.assertEqual(cleared['fields']['universityRoutes'],[])

    def test_route_version_and_missing_card_fail_as_json_not_connection_error(self):
        self.pair();cid=self.add_card()
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request('/api/phone/cards/'+cid+'/routes',{'routes':[],'expectedUpdated':10**400})
        self.assertEqual(caught.exception.code,400)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request('/api/phone/cards/'+'f'*32+'/routes',{'routes':[],'expectedUpdated':0})
        self.assertEqual(caught.exception.code,404)


if __name__=='__main__':unittest.main()
