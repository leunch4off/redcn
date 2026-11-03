"""Desktop-visible pairing survives page reload, rotates and works in a fresh app."""
import json
from pathlib import Path
import sys
import tempfile
import urllib.error
import urllib.request
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
import phone_relay


class PairingCodeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.directory=Path(self.temp.name)
        self.manager=phone_relay.RelayManager(self.directory,object())
        self.manager.enable(0)

    def tearDown(self):
        self.manager.stop();self.temp.cleanup()

    def test_visible_code_retrievable_until_used_and_new_code_after_consumption(self):
        first=self.manager.status();again=self.manager.status()
        self.assertRegex(first['pairCode'],r'^[0-9]{8}$')
        self.assertEqual(first['pairCode'],again['pairCode']);self.assertEqual(first['pairUrls'],again['pairUrls'])
        code=first['pairCode'];token=self.manager.server.pairing.pair(code[:4]+' '+code[4:],'phone')
        self.assertTrue(self.manager.server.pairing.authorized(token))
        renewed=self.manager.status();self.assertNotEqual(renewed['pairUrls'],first['pairUrls'])
        with self.assertRaises(ValueError):self.manager.server.pairing.pair(code,'phone')
        self.assertTrue(self.manager.server.pairing.authorized(token))

    def test_short_code_and_qr_are_one_use_alternatives_no_plaintext_on_disk(self):
        status=self.manager.status();code=status['pairCode'];qr=status['pairUrls'][0].split('#')[1]
        disk=(self.directory/'phone-access.json').read_text()
        self.assertNotIn(code,disk);self.assertNotIn(qr,disk)
        self.manager.server.pairing.pair(qr,'qr-phone')
        with self.assertRaises(ValueError):self.manager.server.pairing.pair(code,'manual-phone')

    def test_expired_code_renews_without_revoking_existing_phone_sessions(self):
        state=self.manager.server.pairing;first=self.manager.status();token=state.pair(first['pairCode'],'phone')
        current=self.manager.status();state.data['pair']['expires']=0
        renewed=self.manager.status();self.assertNotEqual(renewed['pairUrls'],current['pairUrls'])
        self.assertTrue(state.authorized(token))
        with self.assertRaises(ValueError):state.pair(current['pairCode'],'phone')

    def test_manual_attempts_throttled_even_when_desktop_rotates_code(self):
        state=self.manager.server.pairing
        for _ in range(phone_relay.PAIR_ATTEMPTS_PER_PEER):
            with self.assertRaises(ValueError):state.pair('invalid','phone')
        self.manager.issue_pair()
        with self.assertRaises(phone_relay.PairingThrottled) as caught:state.pair(self.manager.status()['pairCode'],'phone')
        self.assertGreater(caught.exception.retry_after,0)
        state.pair(self.manager.status()['pairCode'],'another-phone')
        with patch.object(phone_relay.time,'time',return_value=phone_relay.time.time()+61):
            state.pair(self.manager.status()['pairCode'],'phone')

    def test_global_limit_bounds_unknown_peers(self):
        state=self.manager.server.pairing
        for number in range(phone_relay.PAIR_ATTEMPTS_TOTAL):
            with self.assertRaises(ValueError):state.pair('invalid',str(number))
        with self.assertRaises(phone_relay.PairingThrottled):state.pair('invalid','new-peer')
        self.assertEqual(len(state._pair_attempts),phone_relay.PAIR_ATTEMPTS_TOTAL)

    def test_lan_never_returns_code_and_http_throttle_has_retry_after(self):
        base='http://127.0.0.1:'+str(self.manager.server.server_port)
        with self.assertRaises(urllib.error.HTTPError) as denied:urllib.request.urlopen(base+'/api/phone/state')
        self.assertEqual(denied.exception.code,401)
        for number in range(phone_relay.PAIR_ATTEMPTS_PER_PEER+1):
            request=urllib.request.Request(base+'/api/phone/pair',json.dumps({'code':'invalid'}).encode(),headers={'Origin':base,'Content-Type':'application/json'})
            with self.assertRaises(urllib.error.HTTPError) as caught:urllib.request.urlopen(request)
            self.assertEqual(caught.exception.code,429 if number==phone_relay.PAIR_ATTEMPTS_PER_PEER else 400)
        self.assertGreater(int(caught.exception.headers['Retry-After']),0)
        self.assertNotIn(self.manager.status()['pairCode'],caught.exception.read().decode())

    def test_restart_requires_new_code_but_preserves_saved_phone_cookie(self):
        state=self.manager.server.pairing;token=state.pair(self.manager.status()['pairCode'],'phone')
        before=self.manager.status();restarted=phone_relay.PairingState(self.directory)
        self.assertTrue(restarted.authorized(token));new_qr,new_code,_=restarted.current()
        self.assertNotEqual(new_qr,before['pairUrls'][0].split('#')[1])
        self.assertRegex(new_code,r'^[0-9]{8}$')
        self.assertTrue(restarted.authorized(token))


if __name__=='__main__':unittest.main()
