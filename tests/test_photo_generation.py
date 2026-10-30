"""Photo revision contracts and conflict-safe persistence, with isolated data."""
import base64
import copy
import hashlib
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'cli'))
import card_assets
import listing_photo_inventory
import local_photo_ai
import local_server
import photo_ai_settings
import photo_generation
import redcn
import test_phone_relay as relay_acceptance

PHOTO = 'https://images.cdn-cian.ru/images/generation-fixture.jpg'
OTHER_PHOTO = 'https://images.cdn-cian.ru/images/generation-other.jpg'
DIGEST = hashlib.sha256(b'isolated fixture photograph').hexdigest()
MODEL = photo_ai_settings.DEFAULT_MODEL


def card(**fields):
    return {'fields': {'price': '80000', 'area': '54', 'rooms': '2',
        'city': 'Москва', 'photos': [PHOTO], 'features': [], 'source': 'Квартира 54 м² за 80 000 рублей.', **fields},
        'body': '原始正文', 'titles': ['原始标题'], 'tags': '#原始',
        'listing': {'images': [PHOTO], 'text': 'Квартира 54 м² за 80 000 рублей.'},
        'media': [{'id': DIGEST, 'sourceUrl': PHOTO, 'kind': 'original'}],
        'mediaStatus': 'ready'}


def inventory(features=('fridge', 'sofa', 'bedAny'), state='complete'):
    details = {'fridge': 'Холодильник с двумя дверцами и вертикальной ручкой',
        'sofa': 'Диван с подлокотниками и двумя подушками',
        'bedAny': 'Кровать с мягким изголовьем у стены',
        'oven': 'Духовой шкаф со стеклянной дверцей и ручкой'}
    normalized = local_photo_ai.normalize_inventory({'objects': [{'id': feature, 'visible': True,
        'confidence': .98, 'count': 1, 'countExact': True, 'evidence': details[feature]} for feature in features],
        'unknownIds': []}, DIGEST, MODEL)
    evidence = [{'id': row['id'], 'status': 'auto-visible', 'autoEligible': True,
        'photoId': DIGEST, 'originalId': DIGEST, 'sourceUrl': PHOTO,
        'confidence': row['confidence'], 'count': None, 'evidence': row['evidence'],
        'engine': 'Ollama fixture'} for row in normalized['candidates']]
    return {'version': 1, 'state': state, 'total': 1, 'checked': 1 if state != 'disabled' else 0,
        'failed': 0, 'skipped': 0, 'features': list(features), 'photoEvidence': evidence,
        'photos': [{'sourceUrl': PHOTO, 'originalId': DIGEST, 'status': 'review',
            'provider': 'ollama', 'model': MODEL, 'engine': 'Ollama fixture',
            'candidates': normalized['candidates'], 'unknownIds': normalized['unknownIds']}]}


def suggested(fields=None):
    return {'fields': copy.deepcopy(fields or card()['fields']), 'body': '照片可见：冰箱、沙发、床',
        'titles': ['更新标题'], 'tags': '#更新', 'tagSuggestions': ['#家具'],
        'translationWarnings': [], 'warnings': []}


class PhotoGenerationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.environment = patch.dict(os.environ, {'REDCN_VISION_MODEL': '', 'REDCN_VISION_ENDPOINT': ''})
        self.environment.start()
        photo_ai_settings.save_config(self.directory, {'enabled': True, 'endpoint': 'http://127.0.0.1:11435'})

    def tearDown(self):
        self.environment.stop()
        self.temp.cleanup()

    def revision(self, result, observations=None, generator=None, **kwargs):
        observed = inventory() if observations is None else observations
        generate = generator or (lambda text, fields, rules, prefs: suggested(fields))
        with patch.object(listing_photo_inventory, 'analyze_listing_photos', return_value=observed) as analyze, \
                patch.object(redcn, 'generate', side_effect=generate) as make:
            revision = photo_generation.photo_revision(self.directory, result, **kwargs)
        return revision, make, analyze

    def test_photo_only_equipment_uses_real_node_once_and_is_qualified_as_visible(self):
        result = card()
        baseline = copy.deepcopy(result['fields'])
        real_generate = redcn.generate
        with patch.object(listing_photo_inventory, 'analyze_listing_photos', return_value=inventory()), \
                patch.object(redcn, 'generate', wraps=real_generate) as make:
            revision = photo_generation.photo_revision(self.directory, result)
        self.assertEqual(make.call_count, 1)
        self.assertTrue({'fridge', 'sofa', 'bedAny'} <= set(revision['fields']['features']))
        self.assertNotIn('bed', revision['fields']['features'])
        self.assertIn('照片可见', revision['body'])
        self.assertIn('冰箱', revision['body']); self.assertIn('沙发', revision['body'])
        self.assertNotIn('双人床', revision['body'])
        self.assertEqual(result['fields'], baseline)
        self.assertEqual(revision['fields']['photoAutoFeatures'], ['bedAny', 'fridge', 'sofa'])
        self.assertTrue(all(row['count'] is None for row in revision['fields']['photoEvidence']))

    def test_generation_receives_rules_preferences_source_and_one_album_inventory(self):
        result = card()
        progress = []
        rules, prefs = {'МГУ': '莫斯科国立大学'}, {'contact': ''}
        revision, make, analyze = self.revision(result, text='Оригинальный источник', rules=rules, prefs=prefs,
            report=lambda message, **fields: progress.append(fields))
        self.assertEqual(make.call_count, 1); self.assertEqual(analyze.call_count, 1)
        self.assertEqual(make.call_args.args[0], 'Оригинальный источник')
        self.assertEqual(make.call_args.args[2:], (rules, prefs))
        fields = make.call_args.args[1]
        self.assertEqual(fields['photoAutoFeatures'], ['bedAny', 'fridge', 'sofa'])
        self.assertEqual(fields['photoEdits']['observations'][PHOTO]['analysis']['originalId'], DIGEST)
        self.assertEqual(progress[-1]['stage'], 'generate')
        self.assertEqual(analyze.call_args.kwargs['max_seconds'], 600)
        self.assertIsNotNone(revision)

    def test_source_positives_and_manual_confirmations_survive_missed_repeat_detection(self):
        human = {'id': 'bedAny', 'status': 'confirmed', 'count': 1, 'photoId': DIGEST}
        result = card(features=['fridge', 'sofa', 'bedAny', 'desk'],
            photoAutoFeatures=['fridge', 'sofa', 'bedAny'], photoEvidence=[human],
            sourceAudit={'furniture': [{'id': 'fridge', 'status': 'present'}, {'id': 'sofa', 'status': 'unknown'}]})
        _, make, _ = self.revision(result, inventory(features=()))
        fields = make.call_args.args[1]
        self.assertEqual(set(fields['features']), {'fridge', 'bedAny', 'desk'})
        self.assertEqual(fields['photoEvidence'], [human])
        self.assertEqual(fields['photoAutoFeatures'], [])
        # A second empty scan has no reason to regenerate; the source and
        # confirmed features remain in the first generated fields.
        repeated = {'fields': copy.deepcopy(fields)}
        revision, make, _ = self.revision(repeated, inventory(features=()))
        self.assertIsNotNone(revision); make.assert_not_called()
        self.assertEqual(set(revision['fields']['features']), {'fridge', 'bedAny', 'desk'})

    def test_human_rejections_and_confirmed_photo_controls_survive_same_original_only(self):
        controls = {'analysis': {'originalId': DIGEST},
            'confirmed': {'bedAny': {'count': 1}}, 'rejected': {'fridge': True}}
        fields = {'photoRejectedFeatures': ['fridge'], 'photoEdits': {'crops': {PHOTO: {'bottom': 12}},
            'observations': {PHOTO: controls}},
            'photoEvidence': [{'id': 'bedAny', 'status': 'confirmed', 'count': 1, 'photoId': DIGEST}]}
        _, make, _ = self.revision(card(**fields))
        revised = make.call_args.args[1]
        self.assertEqual(revised['photoRejectedFeatures'], ['fridge'])
        self.assertEqual(revised['photoEdits']['crops'], fields['photoEdits']['crops'])
        observed = revised['photoEdits']['observations'][PHOTO]
        self.assertEqual(observed['confirmed'], controls['confirmed'])
        self.assertEqual(observed['rejected'], controls['rejected'])
        self.assertEqual(revised['photoEvidence'][0]['status'], 'confirmed')
        controls['analysis']['originalId'] = 'b' * 64
        _, make, _ = self.revision(card(**fields))
        observed = make.call_args.args[1]['photoEdits']['observations'][PHOTO]
        self.assertEqual(observed['confirmed'], {}); self.assertEqual(observed['rejected'], {})

    def test_disabled_and_empty_first_scan_do_not_make_extra_node_calls(self):
        for observed in (inventory(state='disabled'), inventory(features=())):
            result = card(); before = copy.deepcopy(result['fields'])
            revision, make, _ = self.revision(result, observed)
            if observed['state'] == 'disabled':
                self.assertIsNone(revision)
            else:
                self.assertIsNotNone(revision)
                for key in ('body', 'titles', 'tags'):
                    self.assertEqual(revision[key], result[key])
            make.assert_not_called()
            self.assertEqual(result['fields'], before)
            self.assertEqual(result['photoInventory'], observed)

    def test_review_only_findings_are_saved_for_controls_without_rewriting_text_or_calling_node(self):
        photo_ai_settings.save_config(self.directory, {'autoApply': False})
        observations = inventory()
        observations.update(features=[], photoEvidence=[])
        result = card(); baseline = copy.deepcopy(result)
        card_id = local_server.save_result(self.directory, baseline)
        revision, make, _ = self.revision(result, observations)
        make.assert_not_called()
        self.assertTrue(revision['fields']['photoEdits']['observations'][PHOTO]['analysis']['candidates'])
        self.assertFalse(revision['fields']['photoEdits']['observations'][PHOTO]['analysis']['autoApply'])
        saved = local_server.merge_prepared_media(self.directory, card_id, result,
            photo_baseline=baseline, photo_generated=revision)
        self.assertEqual(saved['body'], baseline['body'])
        self.assertEqual(saved['fields']['features'], [])
        self.assertTrue(saved['fields']['photoEdits']['observations'][PHOTO]['analysis']['candidates'])

    def test_generator_failure_keeps_inventory_partial_and_does_not_publish_a_half_revision(self):
        result = card(); before = copy.deepcopy(result['fields'])
        def fails(*args):
            raise ValueError('Isolated generator failure')
        revision, make, _ = self.revision(result, generator=fails)
        self.assertIsNone(revision); self.assertEqual(make.call_count, 1)
        self.assertEqual(result['photoInventory']['state'], 'partial')
        self.assertFalse(result['photoInventory']['textUpdated'])
        self.assertEqual(result['fields'], before)

    def test_cancel_after_inventory_or_generation_never_returns_a_revision(self):
        cancel = threading.Event(); cancel.set()
        with self.assertRaisesRegex(ValueError, 'отмен'):
            self.revision(card(), cancel=cancel)
        cancel.clear()
        def cancels(text, fields, rules, prefs):
            cancel.set(); return suggested(fields)
        with self.assertRaisesRegex(ValueError, 'отмен'):
            self.revision(card(), generator=cancels, cancel=cancel)

    def prepared(self, baseline):
        prepared = copy.deepcopy(baseline)
        prepared.update(media=[{'id': DIGEST, 'sourceUrl': PHOTO, 'kind': 'original', 'prepared': True}],
            mediaStatus='ready', photoProcessing={'checked': 1}, photoInventory=inventory())
        generated = suggested({**baseline['fields'], 'features': ['fridge', 'sofa', 'bedAny']})
        return prepared, generated

    def test_unchanged_card_gets_generated_features_and_text_once_with_monotonic_db_stamp(self):
        baseline = card(); card_id = local_server.save_result(self.directory, baseline)
        with sqlite3.connect(self.directory / 'listings.sqlite3') as db:
            stamp = db.execute('SELECT updated FROM results WHERE id=?', (card_id,)).fetchone()[0]
        prepared, generated = self.prepared(baseline)
        saved = local_server.merge_prepared_media(self.directory, card_id, prepared,
            photo_baseline=baseline, photo_generated=generated)
        self.assertEqual(saved['body'], generated['body'])
        self.assertEqual(saved['fields']['features'], ['fridge', 'sofa', 'bedAny'])
        self.assertEqual(saved['media'], prepared['media'])
        self.assertTrue(saved['photoInventory']['textUpdated'])
        self.assertEqual(local_server.read_result(self.directory, card_id), saved)
        with sqlite3.connect(self.directory / 'listings.sqlite3') as db:
            self.assertGreater(db.execute('SELECT updated FROM results WHERE id=?', (card_id,)).fetchone()[0], stamp)

    def test_concurrent_body_title_tag_and_field_edits_are_never_overwritten(self):
        mutations = [lambda value: value.update(body='操作员手动正文'),
            lambda value: value.update(titles=['操作员标题']), lambda value: value.update(tags='#人工'),
            lambda value: value['fields'].update(price='90000'),
            lambda value: value['fields'].update(photoEdits={'crops': {PHOTO: {'bottom': 7}}})]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                baseline = card(); card_id = local_server.save_result(self.directory, baseline)
                prepared, generated = self.prepared(baseline)
                concurrent = copy.deepcopy(baseline); mutate(concurrent)
                local_server.save_result(self.directory, concurrent, card_id)
                saved = local_server.merge_prepared_media(self.directory, card_id, prepared,
                    photo_baseline=baseline, photo_generated=generated)
                for key in ('fields', 'body', 'titles', 'tags'):
                    self.assertEqual(saved[key], concurrent[key])
                self.assertFalse(saved['photoInventory']['textUpdated'])
                self.assertEqual(saved['media'], prepared['media'])
                self.assertTrue(saved['photoInventory']['photoEvidence'])

    def test_changed_selected_photos_roll_back_the_whole_merge(self):
        for edits in ({'photos': [OTHER_PHOTO]}, {'photoEdits': {'excluded': [PHOTO]}}):
            baseline = card(); card_id = local_server.save_result(self.directory, baseline)
            prepared, generated = self.prepared(baseline)
            concurrent = copy.deepcopy(baseline); concurrent['fields'].update(edits)
            local_server.save_result(self.directory, concurrent, card_id)
            with self.assertRaisesRegex(ValueError, 'Фотографии карточки изменились'):
                local_server.merge_prepared_media(self.directory, card_id, prepared,
                    photo_baseline=baseline, photo_generated=generated)
            self.assertEqual(local_server.read_result(self.directory, card_id), concurrent)

    def test_attachment_added_during_analysis_and_unrelated_fields_are_preserved(self):
        baseline = {**card(), 'privateNotes': 'Keep this note'}
        card_id = local_server.save_result(self.directory, baseline)
        prepared, generated = self.prepared(baseline)
        content = b'Internal contract fixture. No real person.'
        before = card_assets.add_attachment(self.directory, card_id, {'category': 'contract',
            'name': 'contract-fixture.txt', 'data': base64.b64encode(content).decode()})
        saved = local_server.merge_prepared_media(self.directory, card_id, prepared,
            photo_baseline=baseline, photo_generated=generated)
        self.assertEqual(card_assets.archive(self.directory, card_id), before)
        attachment_id = before['attachments'][0]['id']
        self.assertEqual(card_assets.attachment_asset(self.directory, card_id, attachment_id)[0], content)
        self.assertEqual(saved['privateNotes'], baseline['privateNotes'])
        self.assertEqual(saved['body'], generated['body'])

    def test_plain_media_merge_preserves_text_when_no_revision_is_available(self):
        baseline = card(); card_id = local_server.save_result(self.directory, baseline)
        prepared, _ = self.prepared(baseline)
        prepared['photoInventory'] = inventory(state='disabled')
        saved = local_server.merge_prepared_media(self.directory, card_id, prepared,
            photo_baseline=baseline, photo_generated=None)
        for key in ('fields', 'body', 'titles', 'tags'):
            self.assertEqual(saved[key], baseline[key])


class PhotoAIHTTPBoundaryTests(unittest.TestCase):
    request = relay_acceptance.PhoneRelayTests.request
    pair = relay_acceptance.PhoneRelayTests.pair
    add_card = relay_acceptance.PhoneRelayTests.add_card

    def setUp(self):
        self.environment = patch.dict(os.environ, {'REDCN_VISION_MODEL': '', 'REDCN_VISION_ENDPOINT': ''})
        self.environment.start()
        relay_acceptance.PhoneRelayTests.setUp(self)

    def tearDown(self):
        try:
            relay_acceptance.PhoneRelayTests.tearDown(self)
        finally:
            self.environment.stop()

    def test_unpaired_clients_cannot_read_config_or_start_album_analysis(self):
        with patch.object(self.controller, 'prepare_card') as prepare:
            for path, data in (('/api/phone/photo-ai', None),
                    ('/api/phone/photo-ai', {'enabled': True}),
                    ('/api/phone/cards/' + 'a'*32 + '/analyze', {})):
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    self.request(path, data)
                self.assertEqual(caught.exception.code, 401)
            prepare.assert_not_called()

    def test_config_and_analyze_require_csrf_same_origin_and_valid_local_model_config(self):
        self.pair(); card_id = self.add_card()
        with patch.object(self.controller, 'prepare_card', return_value='b'*32) as prepare:
            for headers in ({'X-Phone-CSRF': 'wrong'}, {'Origin': 'https://foreign.example'}):
                for path, data in (('/api/phone/photo-ai', {'enabled': True}),
                        ('/api/phone/cards/' + card_id + '/analyze', {})):
                    with self.assertRaises(urllib.error.HTTPError) as caught:
                        self.request(path, data, headers=headers)
                    self.assertEqual(caught.exception.code, 403)
            prepare.assert_not_called()
            self.assertFalse(self.request('/api/phone/photo-ai')['enabled'])
            config = self.request('/api/phone/photo-ai', {'enabled': True, 'endpoint': 'http://127.0.0.1:11435'})
            self.assertTrue(config['enabled'])
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.request('/api/phone/photo-ai', {'endpoint': 'https://remote.example'})
            self.assertEqual(caught.exception.code, 400)
            self.assertEqual(photo_ai_settings.load_config(self.directory), config)
            response = self.request('/api/phone/cards/' + card_id + '/analyze', {})
            self.assertEqual(response, {'job_id': 'b'*32})
            prepare.assert_called_once_with(card_id, analyze=True)


if __name__ == '__main__':
    unittest.main()
