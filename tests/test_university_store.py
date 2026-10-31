"""Source-owned campus catalogue, atomic refresh and authenticated lookup."""
import copy
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'cli'))
import language_service
import university_store as store
import test_card_api as route_fixture


def fixture():
    source = {'id': 'official-one', 'url': 'https://example.edu.cn/admissions',
              'title': 'Official admissions and address', 'status': 'verified',
              'verifiedAt': '2026-10-30', 'language': 'zh', 'publisher': 'University',
              'evidenceType': 'official-university'}
    first = {'id': 'one', 'ru': 'Первый университет', 'zh': '第一大学',
        'aliases': ['ПУ', 'First Uni'], 'region': 'Москва', 'city': 'Москва',
        'sourceIds': ['official-one'], 'translationStatus': 'official',
        'chinaEvidence': {'type': 'official-chinese-page', 'sourceIds': ['official-one']}}
    second = {'id': 'two', 'ru': 'Второй университет', 'zh': '第二大学',
        'aliases': ['ВУ'], 'region': 'Московская область', 'city': 'Долгопрудный',
        'sourceIds': ['official-one'], 'translationStatus': 'editorial',
        'chinaEvidence': {'type': 'official-international-admissions', 'sourceIds': ['official-one']}}
    campus = {'id': 'one-campus', 'universityId': 'one', 'ru': 'Первый университет — корпус на Ёлочной',
        'zh': '第一大学教学楼', 'aliases': ['Ёлочный корпус'], 'region': 'Москва', 'city': 'Москва',
        'addressRu': 'Москва, Ёлочная улица, 1', 'sourceIds': ['official-one'],
        'translationStatus': 'editorial', 'addressStatus': 'official'}
    return {'version': 1, 'asOf': '2026-10-30', 'coverage': {'exhaustive': False},
            'note': 'Primary sources, without a popularity ranking.',
            'universities': [first, second], 'campuses': [campus], 'sources': [source]}


class UniversityStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.directory = self.root/'cache'
        self.catalog = self.root/'source.json'
        self.value = fixture()
        self.write()

    def tearDown(self):
        self.temp.cleanup()

    def write(self):
        self.catalog.write_text(json.dumps(self.value, ensure_ascii=False), encoding='utf-8')

    def test_missing_resource_does_not_create_database_or_cache_directory(self):
        self.assertFalse(self.directory.exists())
        stats = store.stats(self.directory, self.root/'missing.json')
        self.assertFalse(stats['available'])
        self.assertFalse(self.directory.exists())

    def test_lazy_refresh_is_idempotent_and_counts_actual_records_and_evidence(self):
        self.assertFalse(self.directory.exists())
        first = store.stats(self.directory, self.catalog)
        second = store.stats(self.directory, self.catalog)
        self.assertEqual(first, second)
        self.assertEqual(first['universities'], 2)
        self.assertEqual(first['campuses'], 1)
        self.assertEqual(first['sources'], 1)
        self.assertEqual(first['regions']['Московская область']['universities'], 1)
        self.assertEqual(first['chinaEvidence'], {'official-chinese-page': 1, 'official-international-admissions': 1})
        self.assertFalse(first['exhaustive'])
        self.assertEqual(first['coverage'], {'exhaustive': False})
        self.assertEqual((self.directory/store.DATABASE).stat().st_mode & 0o777, 0o600)

    def test_alias_chinese_parent_and_yo_search_preserve_provenance(self):
        first = store.search(self.directory, 'пу', catalog_path=self.catalog)
        self.assertEqual(first['total'], 2)
        self.assertEqual(first['entries'][0]['id'], 'one')
        self.assertTrue(first['entries'][0]['reviewed'])
        self.assertEqual(first['entries'][0]['sourceUrl'], 'https://example.edu.cn/admissions')
        campus = store.search(self.directory, 'елочный', catalog_path=self.catalog)['entries'][0]
        self.assertEqual(campus['kind'], 'campus')
        self.assertEqual(campus['parentZh'], '第一大学')
        self.assertEqual(campus['addressStatus'], 'official')
        self.assertFalse(campus['reviewed'], 'Official address is not a reviewed Chinese campus translation')
        self.assertEqual(store.search(self.directory, '第二大学', catalog_path=self.catalog)['total'], 1)
        self.assertEqual(store.search(self.directory, 'пу', kind='campus', catalog_path=self.catalog)['total'], 1)

    def test_refresh_replaces_only_owned_rows_preserving_cards_and_unrelated_tables(self):
        first = store.stats(self.directory, self.catalog)
        card_file = self.directory/'listings.sqlite3'
        with sqlite3.connect(card_file) as connection:
            connection.execute('CREATE TABLE results (id TEXT PRIMARY KEY,payload TEXT)')
            connection.execute('INSERT INTO results VALUES (?,?)', ('keep', 'private card'))
        with sqlite3.connect(self.directory/store.DATABASE) as connection:
            connection.execute('CREATE TABLE local_notes (value TEXT)')
            connection.execute('INSERT INTO local_notes VALUES (?)', ('keep note',))
        before = card_file.read_bytes()
        self.value['universities'] = self.value['universities'][:1]
        self.value['campuses'] = []
        self.write()
        after = store.stats(self.directory, self.catalog)
        self.assertNotEqual(first['catalogSha256'], after['catalogSha256'])
        self.assertEqual((after['universities'], after['campuses']), (1, 0))
        self.assertEqual(card_file.read_bytes(), before)
        with sqlite3.connect(self.directory/store.DATABASE) as connection:
            self.assertEqual(connection.execute('SELECT value FROM local_notes').fetchone()[0], 'keep note')

    def test_invalid_foreign_references_duplicates_and_urls_cannot_destroy_existing_catalog(self):
        expected = store.stats(self.directory, self.catalog)
        original = copy.deepcopy(self.value)
        mutations = [lambda value: value['campuses'][0].update(universityId='missing'),
            lambda value: value['universities'].append(value['universities'][0]),
            lambda value: value['sources'][0].update(url='javascript:alert(1)'),
            lambda value: value['universities'][0].update(sourceIds=['missing']),
            lambda value: value['universities'][0].update(region='Санкт-Петербург')]
        for mutate in mutations:
            self.value = copy.deepcopy(original)
            mutate(self.value)
            self.write()
            with self.assertRaises(ValueError):
                store.stats(self.directory, self.catalog)
            with sqlite3.connect(self.directory/store.DATABASE) as connection:
                self.assertEqual(connection.execute("SELECT value FROM catalog_meta WHERE key='sha256'").fetchone()[0], expected['catalogSha256'])
                self.assertEqual(connection.execute('SELECT COUNT(*) FROM universities').fetchone()[0], 2)

    def test_pagination_query_bounds_and_sql_literal_search(self):
        first = store.search(self.directory, 'университет', limit=1, catalog_path=self.catalog)
        second = store.search(self.directory, 'университет', limit=1, offset=1, catalog_path=self.catalog)
        self.assertEqual(first['total'], 3)
        self.assertNotEqual(first['entries'][0]['id'], second['entries'][0]['id'])
        self.assertEqual(store.search(self.directory, "' OR 1=1 --", catalog_path=self.catalog)['total'], 0)
        for options in ({'query': ''}, {'query': 'x'*201}, {'query': 'x', 'limit': True},
                        {'query': 'x', 'limit': 101}, {'query': 'x', 'offset': -1}, {'query': 'x', 'kind': 'unknown'}):
            with self.assertRaises(ValueError):
                store.search(self.directory, catalog_path=self.catalog, **options)

    def test_language_dispatch_stats_and_search_share_sqlite_without_node(self):
        with patch.object(store, 'CATALOG', self.catalog), patch.object(language_service, 'node_language') as node:
            response = language_service.dispatch(self.directory, 'universities')
            self.assertEqual(response[0], 200)
            self.assertEqual(response[1]['universities'], 2)
            response = language_service.dispatch(self.directory, 'universities/search', {'query': 'пу', 'kind': 'campus'})
            self.assertEqual(response[0], 200)
            self.assertEqual(response[1]['entries'][0]['universityId'], 'one')
            result = language_service.search(self.directory, {'query': 'пу', 'scope': 'universities'})
            self.assertEqual(result['total'], 2)
            node.assert_not_called()

    def test_repository_catalog_imports_with_valid_campus_parents_and_sources(self):
        if not store.CATALOG.exists():
            self.skipTest('University research resource is not present yet')
        catalog = json.loads(store.CATALOG.read_text(encoding='utf-8'))
        result = store.stats(self.directory)
        self.assertTrue(result['available'])
        self.assertGreater(result['universities'], 0)
        self.assertEqual(result['universities'], len(catalog['universities']))
        self.assertEqual(result['campuses'], len(catalog['campuses']))
        first = catalog['universities'][0]
        matches = store.search(self.directory, first['ru'])
        found = [row for row in matches['entries'] if row['id']==first['id']]
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0]['sources'])
        with sqlite3.connect(self.directory/store.DATABASE) as connection:
            self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(), [])


class UniversityRouteAuthentication(unittest.TestCase):
    setUp = route_fixture.CardDocumentRoutes.setUp
    tearDown = route_fixture.CardDocumentRoutes.tearDown
    request = route_fixture.CardDocumentRoutes.request

    def test_university_routes_enforce_existing_auth_before_lazy_database_creation(self):
        source = self.directory/'university-source.json'
        source.write_text(json.dumps(fixture(), ensure_ascii=False), encoding='utf-8')
        with patch.object(store, 'CATALOG', source):
            self.assertEqual(self.request('universities', auth=False)[0], 401)
            self.assertEqual(self.request('universities/search', {'query': 'пу'}, auth=False)[0], 401)
            self.assertEqual(self.request('universities/search', {'query': 'пу'}, csrf=False)[0], 403)
            base = 'http://127.0.0.1:'+str(self.desktop.server_port)
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(base+'/api/local/universities')
            self.assertEqual(caught.exception.code, 403)
            self.assertFalse((self.directory/store.DATABASE).exists())
            status, body, _ = self.request('universities')
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)['universities'], 2)
            status, body, _ = self.request('universities/search', {'query': 'пу', 'kind': 'campus'})
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)['entries'][0]['parentRu'], 'Первый университет')


if __name__=='__main__':
    unittest.main()
