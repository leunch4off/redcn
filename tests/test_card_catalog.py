"""Catalogue chronology, publication edits, reader isolation and portable state.

All databases and sessions belong to these disposable fixtures.
"""
import copy
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'cli'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import access_roles
import card_catalog
import language_service
import local_server
import phone_relay
import redcn
import workspace_backup
import test_phone_relay as phone_fixture


def card(title='房源', body='独立卧室。'):
    return {'fields': {'price': '80000', 'area': '40', 'photos': []},
            'titles': [title, '备选标题'], 'body': body, 'tags': '#莫斯科租房'}


class CardCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.one, self.two = 'a'*32, 'b'*32

    def tearDown(self):
        self.temp.cleanup()

    def save(self, identifier, value, at):
        with patch('local_server.time.time', return_value=at):
            return local_server.save_result(self.root, value, identifier)

    def state(self, identifier, viewer='owner'):
        with redcn.open_cache(self.root) as db:
            card_catalog.ensure_entries(db, self.root)
            return card_catalog.summaries(db, [identifier], viewer=viewer)[identifier]

    def raw(self, identifier):
        with redcn.open_cache(self.root) as db:
            return db.execute('SELECT rowid,updated,payload FROM results WHERE id=?', (identifier,)).fetchone()

    def test_edit_never_reorders_old_card_or_replaces_rowid(self):
        self.save(self.one, card(), 100)
        self.save(self.two, card('新房源'), 200)
        first = self.state(self.one)
        original_rowid = self.raw(self.one)[0]
        self.save(self.one, card(body='新描述：独立卧室。'), 300)
        changed = self.state(self.one)
        self.assertEqual(changed['catalogOrder'], first['catalogOrder'])
        self.assertEqual(changed['addedAt'], 100)
        self.assertTrue(changed['contentEdited'])
        self.assertEqual(changed['contentEditedAt'], 300)
        self.assertEqual(self.raw(self.one)[0], original_rowid)
        feed = phone_relay.list_cards(self.root)['cards']
        self.assertEqual([row['id'] for row in sorted(feed, key=lambda row: (-row['catalogOrder'], row['id']))], [self.two, self.one])

    def test_fields_photos_and_unused_alternatives_are_not_text_edits(self):
        initial = card(); self.save(self.one, initial, 100)
        changed = copy.deepcopy(initial)
        changed['fields']['price'] = '90000'
        changed['titles'].append('第三个备选')
        changed['mediaStatus'] = 'ready'
        changed['media'] = []
        self.save(self.one, changed, 200)
        self.assertFalse(self.state(self.one)['contentEdited'])
        with redcn.open_cache(self.root) as db:
            db.execute('UPDATE results SET updated=300,payload=? WHERE id=?', (json.dumps(card(body='照片准备后的文字')), self.one))
        feed = phone_relay.list_cards(self.root)['cards'][0]
        self.assertFalse(feed['contentEdited'], 'background preparation is not an operator publication edit')
        self.assertEqual(feed['catalogOrder'], 100)

    def test_language_save_preserves_cas_and_detects_real_publication_edits(self):
        original = card(); self.save(self.one, original, 100)
        raw = self.raw(self.one)[2]
        changed = card(body='经过编辑的描述。')
        language_service._save(self.root, self.one, raw, changed)
        self.assertTrue(self.state(self.one)['contentEdited'])
        expected = self.state(self.one)
        with self.assertRaises(language_service.LanguageConflict):
            language_service._save(self.root, self.one, raw, card(body='过期编辑'))
        self.assertEqual(self.state(self.one), expected)
        self.assertEqual(local_server.read_result(self.root, self.one)['body'], changed['body'])

    def test_viewed_is_persistent_personal_and_does_not_change_sync_cursor(self):
        self.save(self.one, card(), 100)
        raw = self.raw(self.one)
        before = phone_relay.list_cards(self.root)
        editor = card_catalog.viewer_scope('editor', 'fixture-session-a')
        another = card_catalog.viewer_scope('viewer', 'fixture-session-b')
        first = card_catalog.mark_viewed(self.root, self.one, viewer=editor)
        self.assertTrue(first['viewed'])
        self.assertEqual(card_catalog.mark_viewed(self.root, self.one, viewer=editor)['viewedAt'], first['viewedAt'])
        self.assertTrue(self.state(self.one, editor)['viewed'])
        self.assertFalse(self.state(self.one)['viewed'])
        self.assertFalse(self.state(self.one, another)['viewed'])
        card_catalog.mark_viewed(self.root, self.one)
        self.assertTrue(self.state(self.one)['viewed'])
        self.assertFalse(self.state(self.one)['contentEdited'])
        self.assertEqual(self.raw(self.one), raw)
        self.assertEqual(phone_relay.list_cards(self.root, before['cursor'])['cards'], [])

    def test_missing_demo_or_forged_scope_cannot_create_viewed_metadata(self):
        with self.assertRaisesRegex(ValueError, 'не найдена'):
            card_catalog.mark_viewed(self.root, self.one)
        value = card(); value['fields']['isDemo'] = True
        self.save(self.one, value, 100)
        with self.assertRaisesRegex(ValueError, 'не найдена'):
            card_catalog.mark_viewed(self.root, self.one)
        for scope in ('phone:raw-token', 'another-owner', '', None):
            with self.assertRaises(ValueError):
                card_catalog.mark_viewed(self.root, self.one, viewer=scope)
        with self.assertRaises(ValueError):
            card_catalog.viewer_scope('viewer')

    def test_migration_recovers_import_then_revision_without_fabricating_missing_dates(self):
        third = 'c'*32
        with redcn.open_cache(self.root) as db:
            db.execute('CREATE TABLE results(id TEXT PRIMARY KEY,updated REAL,payload TEXT)')
            for identifier, updated in ((self.one, 900), (self.two, 800), (third, 700)):
                db.execute('INSERT INTO results VALUES(?,?,?)', (identifier, updated, json.dumps(card())))
            db.execute('CREATE TABLE fact_revisions(result_id TEXT,revision INTEGER,stamp TEXT)')
            db.execute('INSERT INTO fact_revisions VALUES(?,?,?)', (self.one, 1, '1970-01-01T00:05:00+00:00'))
            db.execute('INSERT INTO fact_revisions VALUES(?,?,?)', (self.two, 1, '1970-01-01T00:03:20+00:00'))
        journal = self.root/'import-jobs'/'jobs.sqlite3'; journal.parent.mkdir()
        with sqlite3.connect(journal) as db:
            db.execute('CREATE TABLE jobs(result_id TEXT,kind TEXT,created REAL)')
            db.executemany('INSERT INTO jobs VALUES(?,?,?)', [(self.one, 'import', 100), (self.one, 'import', 500), (self.two, 'prepare', 50)])
        one, two, unknown = self.state(self.one), self.state(self.two), self.state(third)
        self.assertEqual((one['catalogOrder'], one['catalogOrigin']), (100, 'import-journal'))
        self.assertEqual((two['catalogOrder'], two['catalogOrigin']), (200, 'fact-revision'))
        self.assertEqual((unknown['catalogOrder'], unknown['addedAt'], unknown['catalogOrigin']), (700, None, 'legacy-snapshot'))
        with redcn.open_cache(self.root) as db:
            db.execute('UPDATE results SET updated=10000')
        self.assertEqual(self.state(third), unknown)

    def test_legacy_changed_tab_requires_two_linked_save_snapshots(self):
        with redcn.open_cache(self.root) as db:
            db.execute('CREATE TABLE results(id TEXT PRIMARY KEY,updated REAL,payload TEXT)')
            db.executemany('INSERT INTO results VALUES(?,?,?)', [(self.one, 100, json.dumps(card())), (self.two, 200, json.dumps(card()))])
            db.execute('CREATE TABLE history(id TEXT PRIMARY KEY,stamp REAL,payload TEXT,png BLOB)')
            for index, (identifier, kind, title) in enumerate([(self.one, 'save', '第一版'), (self.one, 'save', '第二版'), (self.two, 'generate', '第一版'), (self.two, 'save', '第二版')]):
                value = {'kind': kind, 'fields': {'resultId': identifier}, 'title': title, 'titles': ['unused'], 'body': '正文'}
                db.execute('INSERT INTO history VALUES(?,?,?,NULL)', (str(index), index+1, json.dumps(value)))
        self.assertTrue(self.state(self.one)['contentEdited'])
        self.assertEqual(self.state(self.one)['contentEditedAt'], 2)
        self.assertFalse(self.state(self.two)['contentEdited'])

    def test_workspace_metadata_remaps_and_preserves_catalogue_without_untrusted_ids(self):
        self.save(self.one, card(), 100)
        self.save(self.one, card(body='新的文本'), 200)
        card_catalog.mark_viewed(self.root, self.one)
        expected = self.state(self.one)
        metadata = workspace_backup._metadata(self.root)
        workspace_backup._validate_metadata_schema(metadata, {self.one})
        destination = self.root/'restore'; destination.mkdir()
        workspace_backup._restore_metadata(destination, metadata, {self.one: self.two})
        with redcn.open_cache(destination) as db:
            restored = card_catalog.summaries(db, [self.two])[self.two]
        self.assertEqual(restored, expected)
        with self.assertRaisesRegex(ValueError, 'вне комплекта'):
            workspace_backup._validate_metadata_schema(metadata, {self.two})

    def test_complete_workspace_round_trip_preserves_catalogue_with_new_card_ids(self):
        self.save(self.one, card(), 100)
        self.save(self.two, card('新房源'), 200)
        self.save(self.one, card(body='编辑后的文本'), 300)
        card_catalog.mark_viewed(self.root, self.one)
        expected = self.state(self.one)
        bundle = workspace_backup.export_workspace(self.root)
        destination = self.root/'complete-restore'
        receipt = workspace_backup.restore_workspace(bundle, destination)
        restored_ids = [row['result_id'] for row in receipt['cards']]
        self.assertNotIn(self.one, restored_ids)
        with redcn.open_cache(destination) as db:
            restored = list(card_catalog.summaries(db, restored_ids).values())
        self.assertIn(expected, restored)
        self.assertEqual(sorted(row['catalogOrder'] for row in restored), [100, 200])

    def test_paginated_snapshot_and_edit_delta_preserve_original_catalogue_order(self):
        identifiers = [format(number, '032x') for number in range(1, 206)]
        started = time.time()-1000
        for index, identifier in enumerate(identifiers):
            self.save(identifier, card('房源 '+str(index)), started+index)

        def complete_feed():
            rows, cursor, pages = [], 0, 0
            while True:
                page = phone_relay.list_cards(self.root, cursor)
                rows.extend(page['cards'])
                cursor = page['cursor']
                pages += 1
                if not page['hasMore']:
                    return rows, cursor, pages
                self.assertLess(pages, 3, 'The change cursor must advance across page boundaries')

        rows, cursor, pages = complete_feed()
        order = lambda cards: [row['id'] for row in sorted(cards, key=lambda row: (-row['catalogOrder'], row['id']))]
        self.assertEqual(pages, 2)
        self.assertEqual(order(rows), list(reversed(identifiers)))
        edited_id = identifiers[3]
        local_server.save_result(self.root, card(body='更新了早期房源的描述。'), edited_id)
        delta = phone_relay.list_cards(self.root, cursor)
        self.assertEqual([row['id'] for row in delta['cards']], [edited_id])
        self.assertTrue(delta['cards'][0]['contentEdited'])
        merged = {row['id']: row for row in rows}
        merged.update({row['id']: row for row in delta['cards']})
        self.assertEqual(order(merged.values()), list(reversed(identifiers)))
        reloaded, _, pages = complete_feed()
        self.assertEqual(pages, 2)
        self.assertEqual(order(reloaded), list(reversed(identifiers)))


class CatalogHTTPTests(unittest.TestCase):
    setUp = phone_fixture.PhoneRelayTests.setUp
    tearDown = phone_fixture.PhoneRelayTests.tearDown
    request = phone_fixture.PhoneRelayTests.request
    pair = phone_fixture.PhoneRelayTests.pair
    add_card = phone_fixture.PhoneRelayTests.add_card

    def test_view_acknowledgement_auth_csrf_and_viewer_isolation(self):
        identifier = self.add_card()
        path = '/api/phone/cards/'+identifier+'/viewed'
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request(path, {})
        self.assertEqual(caught.exception.code, 401)
        self.pair()
        snapshot = self.server.pairing.list_sessions()
        self.server.pairing.manage_session(snapshot['sessions'][0]['id'], role='viewer', expected_revision=snapshot['revision'])
        for body, headers, status in [({}, {'X-Phone-CSRF': 'wrong'}, 403), ({'viewer': 'owner'}, {}, 400)]:
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.request(path, body, headers=headers)
            self.assertEqual(caught.exception.code, status)
        self.assertTrue(self.request(path, {})['viewed'])
        self.assertTrue(self.request('/api/phone/cards')['cards'][0]['viewed'])
        self.assertFalse(phone_relay.list_cards(self.directory)['cards'][0]['viewed'])
        self.assertFalse(access_roles.phone_route_allowed('viewer', 'POST', 'cards/'+identifier+'/rewrite'))
        demo = self.add_card(isDemo=True)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request('/api/phone/cards/'+demo+'/viewed', {})
        self.assertEqual(caught.exception.code, 404)


if __name__ == '__main__':
    unittest.main()
