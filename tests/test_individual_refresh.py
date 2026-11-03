"""Body refresh is an explicit editorial operation, never model evidence."""
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'cli'))
import card_catalog
import individual_refresh as refresh
from publication_provenance import bind_manual, part_origin


FIELDS = {'city': 'Москва', 'price': '85000', 'deposit': '35000', 'area': '42',
          'floor': '2', 'floors': '12', 'layout': 'one',
          'features': ['chairs', 'fridge', 'oven'],
          'photos': ['https://example.invalid/private-photo.jpg']}
FIRST, SECOND = 'a'*32, 'b'*32


class IndividualRefreshTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.brief = refresh.build_brief(FIELDS)
        facts = cls.brief['facts']
        cls.title = '莫斯科'+cls.brief['layout']+'出租'
        cls.body = '\n'.join(facts['核心参数'])+'\n'+facts['其他核实资料']+'\n'+facts['已知费用与限制']+'\n'\
            +'室内物品包括：'+'、'.join(facts['核实物品清单'])+'。'
        assert refresh.validate_individual({'title': cls.title, 'body': cls.body}, cls.brief) == []

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.cache = self.directory/'cache'
        self.cache.mkdir(mode=0o700)
        self.database = self.cache/'listings.sqlite3'
        self.backups = self.directory/'private-backups'
        self.card = {'titles': [self.title, '备用标题'], 'body': '  旧模板\r\n原有正文必须原样留存。\n',
            'tags': '#原标签 #不得修改', 'fields': copy.deepcopy(FIELDS),
            'media': [{'id': 'c'*64, 'kind': 'original', 'sourceUrl': FIELDS['photos'][0]}],
            'photoInventory': {'total': 1, 'completed': 0, 'partial': True},
            'templateInfo': {'descriptionId': 'old-template', 'titleId': 'old-title',
                'tagId': 'old-tags', 'manualParts': ['title', 'tags'],
                'copyEngine': {'version': 1}, 'unrelated': {'keep': True}}}
        self.card = bind_manual(self.card, 'title')
        self.card = bind_manual(self.card, 'tags')
        with sqlite3.connect(self.database) as db:
            db.execute('CREATE TABLE results (id TEXT PRIMARY KEY, updated REAL, payload TEXT)')
            for identifier, stamp in ((FIRST, 100.0), (SECOND, 200.0)):
                db.execute('INSERT INTO results VALUES (?,?,?)',
                    (identifier, stamp, json.dumps(self.card, ensure_ascii=False, indent=2)))
            card_catalog.ensure_entries(db, self.cache)
            db.execute('INSERT INTO card_catalog_views VALUES (?,?,?)', (FIRST, 'owner', 123.0))

    def saved(self, identifier=FIRST):
        with sqlite3.connect(self.database) as db:
            return db.execute('SELECT updated,payload FROM results WHERE id=?', (identifier,)).fetchone()

    def alter(self, callback, identifier=FIRST, *, updated=None):
        stamp, payload = self.saved(identifier)
        card = json.loads(payload)
        callback(card)
        with sqlite3.connect(self.database) as db:
            db.execute('UPDATE results SET payload=?,updated=? WHERE id=?',
                       (json.dumps(card, ensure_ascii=False), stamp if updated is None else updated, identifier))

    def candidates(self, plan, identifiers=(FIRST,)):
        by_id = {row['id']: row for row in plan['rows']}
        return {'rows': [{'id': identifier, 'title': self.title, 'body': self.body,
            'review': {'actor': 'assistant_editorial_test', 'humanVerified': False,
                'reason': 'Independently read this old template and checked the replacement against current facts.',
                'oldBodyReviewed': True, 'oldBodySha256': by_id[identifier]['oldBodySha256'],
                'replacementBasis': 'legacy-template'}} for identifier in identifiers]}

    def prepared(self, identifiers=(FIRST,), **kwargs):
        plan = refresh.plan_refresh(self.cache, now=500)
        return refresh.prepare_refresh(self.cache, plan, self.candidates(plan, identifiers), now=501, **kwargs)

    def review(self, prepared):
        return {'version': 1, 'kind': 'individual-body-refresh-review',
            'preparedSha256': prepared['preparedSha256'], 'approved': True,
            'actor': 'assistant_application_review', 'humanVerified': False,
            'reason': 'Reviewed the exact before/after pair and the source facts for this application.',
            'rows': [{'id': row['id'], 'candidateSha256': row['candidateSha256']}
                     for row in prepared['rows'] if row['status'] == 'ready']}

    def apply(self, prepared, review=None):
        return refresh.apply_refresh(self.cache, prepared, self.review(prepared) if review is None else review,
                                     backup_directory=self.backups, now=600)

    def tables(self):
        with sqlite3.connect(self.database) as db:
            return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    def test_plan_and_prepare_are_read_only_and_bind_exact_old_bytes(self):
        original = self.database.read_bytes()
        tables = self.tables()
        plan = refresh.plan_refresh(self.cache, now=500)
        row = next(row for row in plan['rows'] if row['id'] == FIRST)
        self.assertEqual(row['payloadSha256'], hashlib.sha256(self.saved()[1].encode()).hexdigest())
        self.assertEqual(row['oldBody'], self.card['body'])
        self.assertEqual(row['manualParts'], ['title', 'tags'])
        self.assertEqual(row['bodyOrigin'], 'rules-unverified')
        prepared = refresh.prepare_refresh(self.cache, plan, self.candidates(plan), now=501)
        self.assertEqual(prepared['counts'], {'ready': 1, 'blocked': 0, 'skipped': 0})
        self.assertTrue(prepared['requiresSeparateReview'])
        self.assertEqual(self.database.read_bytes(), original)
        self.assertEqual(self.tables(), tables)
        self.assertFalse(self.backups.exists())

    def test_default_cli_only_plans_and_never_overwrites_a_private_receipt(self):
        target = self.directory/'plan.private.json'
        command = [sys.executable, str(ROOT/'scripts/refresh-individual-descriptions.py'),
                   '--cache', str(self.cache), '--out', str(target)]
        before = self.database.read_bytes()
        first = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(first.returncode, 0, first.stderr)
        value = json.loads(target.read_text())
        self.assertTrue(value['readOnly'])
        self.assertEqual(value['modelCalls'], 0)
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        original = target.read_bytes()
        self.assertEqual(subprocess.run(command, capture_output=True).returncode, 1)
        self.assertEqual(target.read_bytes(), original)
        self.assertEqual(self.database.read_bytes(), before)

    def test_missing_database_is_not_created(self):
        missing = self.directory/'missing'
        with self.assertRaisesRegex(ValueError, 'не найдено'):
            refresh.plan_refresh(missing)
        self.assertFalse(missing.exists())

    def test_declared_manual_body_always_skips_even_with_candidate_review(self):
        self.alter(lambda card: card['templateInfo']['manualParts'].append('body'))
        before = self.saved()
        prepared = self.prepared()
        self.assertEqual(prepared['counts']['skipped'], 1)
        self.assertEqual(prepared['rows'][0]['bodyOrigin'], 'manual')
        with self.assertRaisesRegex(ValueError, 'не выбраны'):
            self.apply(prepared)
        self.assertEqual(self.saved(), before)

    def test_edit_after_bound_generation_is_manual_without_manualparts_flag(self):
        from publication_provenance import bind_generation
        def edited(card):
            bound = bind_generation(card, {'part': 'body', 'generationMode': 'individual-model-prose',
                'modelAccepted': True, 'fallback': False}, parts=('body',))
            card.update(bound)
            card['body'] += '\nОператор внёс ручную правку.'
        self.alter(edited)
        self.assertEqual(self.prepared()['rows'][0]['bodyOrigin'], 'manual')
        self.assertEqual(self.prepared()['counts']['skipped'], 1)

    def test_legacy_explicit_manual_body_veto_preserves_title_only_scope(self):
        self.alter(lambda card: card.update(aiAdvertisement={'generationMode': 'manual', 'part': 'body'}))
        self.assertEqual(self.prepared()['counts']['skipped'], 1)
        self.alter(lambda card: card.update(aiAdvertisement={'generationMode': 'manual', 'part': 'title'}))
        self.assertEqual(self.prepared()['counts']['ready'], 1)

    def test_unknown_origin_needs_explicit_review_of_exact_old_body(self):
        self.alter(lambda card: card['templateInfo'].update(descriptionId=None, copyEngine=None))
        plan = refresh.plan_refresh(self.cache)
        cases = [({'oldBodyReviewed': False}, 'проверка точного старого'),
                 ({'oldBodySha256': '0'*64}, 'проверка точного старого'),
                 ({'replacementBasis': 'reviewed-rewrite'}, 'legacy-template'),
                 ({'humanVerified': True}, 'humanVerified=false')]
        for changes, fragment in cases:
            with self.subTest(changes=changes):
                candidates = self.candidates(plan)
                candidates['rows'][0]['review'].update(changes)
                result = refresh.prepare_refresh(self.cache, plan, candidates)
                self.assertEqual(result['counts']['blocked'], 1)
                self.assertIn(fragment, result['rows'][0]['reason'])

    def test_actual_saved_title_is_validated_not_candidate_reference_title(self):
        self.alter(lambda card: card['titles'].__setitem__(0, 'English publication title'))
        prepared = self.prepared()
        self.assertEqual(prepared['counts']['blocked'], 1)
        self.assertIn('Заголовок должен быть на китайском', prepared['rows'][0]['reason'])

    def test_conflicting_legacy_title_alias_cannot_hide_behind_valid_titles_array(self):
        self.alter(lambda card: card.update(title='Untranslated legacy title'))
        prepared = self.prepared()
        self.assertEqual(prepared['counts']['blocked'], 1)
        self.assertIn('Заголовок должен быть на китайском', prepared['rows'][0]['reason'])

    def test_missing_inventory_is_blocked_by_real_individual_validator(self):
        plan = refresh.plan_refresh(self.cache)
        candidates = self.candidates(plan)
        candidates['rows'][0]['body'] = self.body.replace('冰箱', '')
        prepared = refresh.prepare_refresh(self.cache, plan, candidates)
        self.assertEqual(prepared['counts']['blocked'], 1)
        self.assertIn('Пропущены подтверждённые предметы', prepared['rows'][0]['reason'])

    def test_applied_body_has_editorial_provenance_exact_backups_and_stable_catalog(self):
        before_stamp, before_payload = self.saved()
        before = json.loads(before_payload)
        other = self.saved(SECOND)
        with sqlite3.connect(self.database) as db:
            catalog = db.execute('SELECT * FROM card_catalog_state ORDER BY card_id').fetchall()
            views = db.execute('SELECT * FROM card_catalog_views').fetchall()
        prepared = self.prepared()
        receipt = self.apply(prepared)
        updated_stamp, updated_payload = self.saved()
        updated = json.loads(updated_payload)
        self.assertGreater(updated_stamp, before_stamp)
        self.assertEqual(updated['body'], self.body)
        self.assertEqual(updated['titles'], before['titles'])
        self.assertEqual(updated['tags'], before['tags'])
        self.assertEqual(updated['media'], before['media'])
        self.assertEqual(updated['photoInventory'], before['photoInventory'])
        self.assertEqual({key: value for key, value in updated['fields'].items() if key != 'aiGeneration'},
                         {key: value for key, value in before['fields'].items() if key != 'aiGeneration'})
        self.assertEqual(updated['templateInfo'], {key: value for key, value in before['templateInfo'].items()
                                                 if key not in ('descriptionId', 'copyEngine')})
        metadata = updated['aiAdvertisement']
        self.assertEqual(part_origin(updated, 'body'), 'assistant-reviewed')
        for key, value in {'generationMode': 'assistant-editorial', 'textSource': 'assistant-reviewed',
                           'modelAccepted': False, 'fallback': False, 'part': 'body'}.items():
            self.assertEqual(metadata[key], value)
            self.assertEqual(metadata['parts']['body'][key], value)
        for part in ('title', 'tags'):
            self.assertEqual(metadata['parts'][part], before['aiAdvertisement']['parts'][part])
        self.assertEqual(self.saved(SECOND), other)
        backup = Path(receipt['backupDirectory'])
        self.assertEqual((backup/(FIRST+'.payload.json')).read_bytes(), before_payload.encode())
        self.assertEqual((backup/(FIRST+'.body.txt')).read_bytes(), before['body'].encode())
        self.assertEqual(backup.stat().st_mode & 0o777, 0o700)
        self.assertTrue(all(path.stat().st_mode & 0o777 == 0o600 for path in backup.iterdir()))
        with sqlite3.connect(self.database) as db:
            after_catalog = db.execute('SELECT * FROM card_catalog_state ORDER BY card_id').fetchall()
            self.assertEqual(db.execute('SELECT * FROM card_catalog_views').fetchall(), views)
            revision = db.execute('SELECT previous_payload,previous_body,body,source FROM individual_body_revisions').fetchone()
        self.assertEqual(revision, (before_payload, before['body'], self.body, 'assistant-editorial'))
        self.assertEqual(after_catalog[0][:4], catalog[0][:4])
        self.assertIsNotNone(after_catalog[0][5])
        self.assertEqual(after_catalog[1], catalog[1])
        self.assertEqual(receipt['modelCalls'], 0)
        self.assertFalse(receipt['humanVerified'])
        with self.assertRaises(refresh.RefreshConflict):
            self.apply(prepared)
        self.assertEqual((backup/(FIRST+'.body.txt')).read_bytes(), before['body'].encode())

    def test_full_payload_cas_detects_unrelated_edit_even_with_identical_timestamp(self):
        prepared = self.prepared()
        self.alter(lambda card: card.update(tags='#новые_теги'))
        changed = self.saved()
        with self.assertRaises(refresh.RefreshConflict):
            self.apply(prepared)
        self.assertEqual(self.saved(), changed)
        self.assertNotIn('individual_body_revisions', self.tables())
        self.assertFalse(self.backups.exists())

    def test_timestamp_only_update_requires_new_prepare(self):
        prepared = self.prepared()
        with sqlite3.connect(self.database) as db:
            db.execute('UPDATE results SET updated=101 WHERE id=?', (FIRST,))
        with self.assertRaises(refresh.RefreshConflict):
            self.apply(prepared)
        self.assertEqual(json.loads(self.saved()[1])['body'], self.card['body'])

    def test_race_after_semantic_validation_aborts_entire_transaction(self):
        prepared = self.prepared((FIRST, SECOND))
        second = self.saved(SECOND)
        backup = refresh._backup
        def interleaved(*args, **kwargs):
            destination = backup(*args, **kwargs)
            self.alter(lambda card: card['fields'].update(deposit='36000'))
            return destination
        with patch.object(refresh, '_backup', side_effect=interleaved):
            with self.assertRaisesRegex(refresh.RefreshConflict, 'Параллельное'):
                self.apply(prepared)
        self.assertEqual(self.saved(SECOND), second)
        self.assertEqual(json.loads(self.saved()[1])['body'], self.card['body'])
        self.assertNotIn('individual_body_revisions', self.tables())
        self.assertEqual(len(list(self.backups.glob('body-refresh-*'))), 1)

    def test_second_write_failure_rolls_back_bodies_catalog_and_revision(self):
        prepared = self.prepared((FIRST, SECOND))
        originals = {identifier: self.saved(identifier) for identifier in (FIRST, SECOND)}
        with sqlite3.connect(self.database) as db:
            catalog = db.execute('SELECT * FROM card_catalog_state').fetchall()
        original_save = card_catalog.record_save
        def fail_second(db, directory, identifier, *args, **kwargs):
            if identifier == SECOND:
                raise sqlite3.OperationalError('synthetic disk failure during second card')
            return original_save(db, directory, identifier, *args, **kwargs)
        with patch.object(card_catalog, 'record_save', side_effect=fail_second):
            with self.assertRaises(sqlite3.OperationalError):
                self.apply(prepared)
        self.assertEqual({identifier: self.saved(identifier) for identifier in (FIRST, SECOND)}, originals)
        self.assertNotIn('individual_body_revisions', self.tables())
        with sqlite3.connect(self.database) as db:
            self.assertEqual(db.execute('SELECT * FROM card_catalog_state').fetchall(), catalog)

    def test_apply_rebuilds_and_revalidates_instead_of_trusting_old_pass(self):
        prepared = self.prepared()
        before = self.saved()
        with patch.object(refresh, 'validate_individual', return_value=['Новое несовпадение фактов']):
            with self.assertRaisesRegex(ValueError, 'Новое несовпадение'):
                self.apply(prepared)
        self.assertEqual(self.saved(), before)
        self.assertFalse(self.backups.exists())

    def test_changed_rules_or_tampered_receipts_cannot_apply(self):
        prepared = self.prepared()
        review = self.review(prepared)
        changed = copy.deepcopy(prepared)
        changed['rows'][0]['body'] += '\n虚构的内容。'
        with self.assertRaises(refresh.RefreshConflict):
            self.apply(changed, review)
        wrong_review = {**review, 'preparedSha256': '0'*64}
        with self.assertRaisesRegex(ValueError, 'именно этого'):
            self.apply(prepared, wrong_review)
        (self.cache/'phone-profile.json').write_text(json.dumps({'rules': {'changed': 'translation'}}))
        with self.assertRaisesRegex(refresh.RefreshConflict, 'правила'):
            self.apply(prepared, review)
        self.assertNotIn('individual_body_revisions', self.tables())

    def test_photo_reconciliation_is_explicit_pure_and_remains_under_payload_cas(self):
        prepared = self.prepared(reconcile_photo_journal=True)
        from photo_auto_policy import reconcile_saved_review_journal
        with patch('photo_auto_policy.reconcile_saved_review_journal', wraps=reconcile_saved_review_journal) as reconcile:
            self.apply(prepared)
        self.assertEqual(reconcile.call_count, 1)
        updated = json.loads(self.saved()[1])
        self.assertEqual(updated['photoInventory'], self.card['photoInventory'])
        self.assertEqual(updated['fields']['photos'], self.card['fields']['photos'])


if __name__ == '__main__':
    unittest.main()
