"""Exact destination/provider isolation and real expiry of checked route notes."""
import copy
from datetime import datetime, timezone
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import json

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'cli'))
import university_route_cache as cache
import access_roles
import test_card_api as route_fixture


class UniversityRouteCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.now = datetime(2026, 11, 1, 12, tzinfo=timezone.utc).timestamp()
        self.data = {'origin': {'latitude': 55.7, 'longitude': 37.5, 'address': 'Москва, улица А, 1'},
            'route': {'name': 'МГУ', 'campusId': 'msu-physics', 'mode': 'public', 'time': '40–55',
                'method': 'manual-confirmed', 'checkedAt': '2026-11-01', 'transport': 'Проверено оператором'}}

    def tearDown(self):
        self.temp.cleanup()

    def test_reuse_preserves_original_date_and_source_and_expires_exactly_at_ttl(self):
        self.assertFalse(cache.cache(self.directory, 'lookup', self.data, now=self.now)['hit'])
        result = cache.cache(self.directory, 'remember', self.data, now=self.now)
        self.assertEqual(result['expiresAt'], self.now+cache.TTL_SECONDS)
        reused = cache.cache(self.directory, 'lookup', self.data, now=self.now+cache.TTL_SECONDS-1)
        self.assertTrue(reused['hit'])
        self.assertEqual(reused['route']['checkedAt'], '2026-11-01')
        self.assertEqual(reused['route']['method'], 'manual-confirmed')
        expired = cache.cache(self.directory, 'lookup', self.data, now=self.now+cache.TTL_SECONDS)
        self.assertEqual(expired['reason'], 'expired')
        self.assertNotIn('route', expired)
        self.assertEqual((self.directory/cache.DATABASE).stat().st_mode & 0o777, 0o600)

    def test_changed_origin_campus_mode_or_explicit_point_never_reuses_other_journey(self):
        cache.cache(self.directory, 'remember', self.data, now=self.now)
        for change in (lambda v: v['origin'].update(latitude=55.700001),
                       lambda v: v['origin'].update(address='Другой адрес'),
                       lambda v: v['route'].update(campusId='msu-sternberg'),
                       lambda v: v['route'].update(mode='bus'),
                       lambda v: v['route'].update(latitude=55.701, longitude=37.531)):
            value = copy.deepcopy(self.data)
            change(value)
            with self.subTest(value=value):
                self.assertFalse(cache.cache(self.directory, 'lookup', value, now=self.now+1)['hit'])

    def test_provider_identity_is_isolated_and_never_claimed_for_manual_notes(self):
        provider = copy.deepcopy(self.data)
        provider['route'].update(method='provider', sourceUrl='https://yandex.ru/maps/?rtext=55.7,37.5~55.699876,37.529543')
        cache.cache(self.directory, 'remember', provider, now=self.now)
        self.assertFalse(cache.cache(self.directory, 'lookup', self.data, now=self.now+1)['hit'])
        self.assertTrue(cache.cache(self.directory, 'lookup', provider, now=self.now+1)['hit'])
        provider['route']['sourceUrl']='https://www.google.com/maps/dir/'
        self.assertFalse(cache.cache(self.directory, 'lookup', provider, now=self.now+1)['hit'])

    def test_unverified_stale_future_mismatched_address_partial_points_do_not_enter_cache(self):
        for change in (lambda v: v['route'].update(method='unknown'),
                       lambda v: v['route'].update(method='estimate'),
                       lambda v: v['route'].update(checkedAt='2026-10-20'),
                       lambda v: v['route'].update(checkedAt='2026-11-03'),
                       lambda v: v['route'].update(address='Москва, Авиамоторная 8А'),
                       lambda v: v['route'].update(campusId=''),
                       lambda v: v['route'].update(method='provider', sourceUrl=''),
                       lambda v: v['origin'].update(longitude='')):
            value = copy.deepcopy(self.data)
            change(value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                cache.cache(self.directory, 'remember', value, now=self.now)

    def test_clock_rollback_is_miss_and_dispatch_never_touches_import_or_card_database(self):
        cache.cache(self.directory, 'remember', self.data, now=self.now)
        self.assertEqual(cache.cache(self.directory, 'lookup', self.data, now=self.now-1)['reason'], 'expired')
        self.assertIsNone(cache.dispatch(self.directory, 'unrelated', {}))
        self.assertEqual({p.name for p in self.directory.iterdir()}, {cache.DATABASE})

    def test_endpoint_role_matrix_allows_editors_while_rejecting_viewers_and_unknown_actions(self):
        for action in ('lookup','remember'):
            path=cache.PREFIX+action
            for role in ('owner','editor'):
                self.assertTrue(access_roles.phone_route_allowed(role,'POST',path))
            self.assertFalse(access_roles.phone_route_allowed('viewer','POST',path))
            self.assertFalse(access_roles.phone_route_allowed('owner','GET',path))
        self.assertFalse(access_roles.phone_route_allowed('owner','POST',cache.PREFIX+'reset'))


class UniversityRouteCacheHTTP(unittest.TestCase):
    setUp=route_fixture.CardDocumentRoutes.setUp
    tearDown=route_fixture.CardDocumentRoutes.tearDown
    request=route_fixture.CardDocumentRoutes.request

    def test_authenticated_mounted_endpoint_reuses_and_expires_without_changing_saved_card(self):
        data={'origin':{'latitude':55.7,'longitude':37.5,'address':'Москва, тестовый адрес'},'route':{'name':'МГУ','campusId':'msu-physics','mode':'public','method':'manual-confirmed','checkedAt':'2026-11-01','time':'40–55'}}
        path=cache.PREFIX+'remember'
        self.assertEqual(self.request(path,data,auth=False)[0],401)
        self.assertEqual(self.request(path,data,csrf=False)[0],403)
        self.assertFalse((self.directory/cache.DATABASE).exists())
        original=cache.cache
        clock=[datetime(2026,11,1,12,tzinfo=timezone.utc).timestamp()]
        card_before=(self.directory/'listings.sqlite3').read_bytes()
        with patch.object(cache,'cache',side_effect=lambda directory,action,payload:original(directory,action,payload,now=clock[0])):
            status,body,_=self.request(path,data)
            self.assertEqual(status,200,body)
            status,body,_=self.request(cache.PREFIX+'lookup',data)
            self.assertEqual(status,200,body)
            self.assertTrue(json.loads(body)['hit'])
            clock[0]+=cache.TTL_SECONDS
            status,body,_=self.request(cache.PREFIX+'lookup',data)
            self.assertEqual(json.loads(body)['reason'],'expired')
        self.assertEqual((self.directory/'listings.sqlite3').read_bytes(),card_before)
        state=self.phone.pairing;grant=state.list_sessions()
        state.manage_session(grant['sessions'][0]['id'],role='viewer',expected_revision=grant['revision'])
        self.assertEqual(self.request(cache.PREFIX+'lookup',data)[0],403)


if __name__=='__main__':
    unittest.main()
