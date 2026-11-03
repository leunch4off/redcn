import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'cli'))
import city_poi_store as store
import language_service
import access_roles
import test_card_api as route_fixture


class CityPoiTests(unittest.TestCase):
    def test_readonly_search_supports_categories_aliases_and_source_provenance_without_creating_user_cache(self):
        with tempfile.TemporaryDirectory() as root:
            directory=Path(root)/'untouched'
            result=store.search(directory,'Миклухо')
            self.assertEqual(result['entries'][0]['id'],'korshop-belyaevo');self.assertFalse(result['entries'][0]['reviewed'])
            self.assertEqual(store.search(directory,'Савёловский')['total'],1)
            self.assertEqual(store.search(directory,category='park')['total'],2)
            self.assertEqual(store.stats(directory)['places'],5)
            self.assertFalse(directory.exists())

    def test_sources_unknown_accuracy_and_dates_validate_before_catalog_is_returned(self):
        original=store.read_catalog()
        mutations=[lambda value:value['places'][0].update(latitude=True),lambda value:value['places'][0].update(sourceIds=['missing']),lambda value:value['places'][0]['coordinateEvidence'].update(accuracyMeters=1),lambda value:value['places'][0]['coordinateEvidence'].update(scope='verified-accessible-entrance'),lambda value:value['sources'][0].update(verifiedAt='2099-01-01'),lambda value:value['sources'][0].update(url='https://user:pass@example.org'),lambda value:value['places'][0].update(openingStatus='open-now')]
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'source.json'
            for change in mutations:
                value=copy.deepcopy(original);change(value);path.write_text(json.dumps(value))
                with self.subTest(value=value['places'][0]),self.assertRaises(ValueError):store.read_catalog(path)

    def test_node_python_language_search_and_specific_endpoints_share_source_records_without_model_calls(self):
        result=store.search(None,'Беляево')
        output=json.loads(subprocess.check_output(['node','cli/language.mjs'],cwd=ROOT,input=json.dumps({'action':'search','scope':'city-pois','query':'Беляево'}).encode()))
        self.assertEqual([row['id']for row in output['entries']],[row['id']for row in result['entries']])
        with tempfile.TemporaryDirectory() as root,patch.object(language_service,'node_language') as node:
            self.assertEqual(language_service.dispatch(root,'city-pois')[1]['places'],5)
            self.assertEqual(language_service.dispatch(root,'city-pois/search',{'query':'粤菜'})[1]['entries'][0]['id'],'chinagramota-sretenka')
            self.assertEqual(language_service.search(root,{'query':'Беляево','scope':'city-pois'})['total'],1)
            node.assert_not_called()

    def test_invalid_search_and_role_matrix_fail_closed(self):
        for options in ({'query':{}},{'category':[]},{'category':'unknown'},{'limit':True},{'limit':101},{'offset':-1}):
            with self.subTest(options=options),self.assertRaises(ValueError):store.search(None,**options)
        for role in ('editor','owner'):
            self.assertTrue(access_roles.phone_route_allowed(role,'GET','city-pois'))
            self.assertTrue(access_roles.phone_route_allowed(role,'POST','city-pois/search'))
        self.assertFalse(access_roles.phone_route_allowed('viewer','GET','city-pois'))
        self.assertFalse(access_roles.phone_route_allowed('editor','POST','city-pois/modify'))


class CityPoiHTTP(unittest.TestCase):
    setUp=route_fixture.CardDocumentRoutes.setUp
    tearDown=route_fixture.CardDocumentRoutes.tearDown
    request=route_fixture.CardDocumentRoutes.request
    def test_mounted_read_search_require_session_csrf_and_do_not_change_cards(self):
        before=(self.directory/'listings.sqlite3').read_bytes()
        self.assertEqual(self.request('city-pois',auth=False)[0],401)
        self.assertEqual(self.request('city-pois/search',{'query':'КорШоп'},csrf=False)[0],403)
        status,body,_=self.request('city-pois');self.assertEqual(status,200,body);self.assertEqual(json.loads(body)['places'],5)
        status,body,_=self.request('city-pois/search',{'category':'park'});self.assertEqual(status,200,body);self.assertEqual(json.loads(body)['total'],2)
        self.assertEqual((self.directory/'listings.sqlite3').read_bytes(),before)


if __name__=='__main__':unittest.main()
