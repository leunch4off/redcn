import copy
import hashlib
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'cli'))
import listing_photo_inventory as pipeline
import local_photo_ai as ai

MODEL = 'qwen3-vl:2b-instruct-q4_K_M'
CONFIG = {'enabled': True, 'model': MODEL, 'endpoint': 'http://127.0.0.1:11435', 'autoApply': True, 'maxSeconds': 600}


def card(count=3):
    urls = ['https://images.cdn-cian.ru/images/' + str(index) + '.jpg' for index in range(count)]
    media = [{'sourceUrl': url, 'id': hashlib.sha256(url.encode()).hexdigest(), 'kind': 'original'} for url in urls]
    return {'listing': {'images': urls}, 'fields': {'photos': urls}, 'media': media}


def observation(digest, feature='oven'):
    normalized = ai.normalize_inventory({'objects': [{'id': feature, 'visible': True,
        'confidence': .98, 'count': 1, 'countExact': True,
        'evidence': 'Духовой шкаф со стеклянной дверцей и ручкой'}], 'unknownIds': []}, digest, MODEL)
    return {'status': 'review', 'provider': 'ollama', 'model': MODEL,
        'engine': 'Ollama · fixture · локально', 'originalId': digest, **normalized}


def original(_, digest):
    return b'private image bytes', 'image/jpeg', {'id': digest, 'kind': 'original'}


class ListingInventoryTests(unittest.TestCase):
    def analyze(self, result, *, config=None, analysis=None, asset=original, **kwargs):
        with patch.object(pipeline, '_configuration', return_value=CONFIG if config is None else config), \
                patch.object(pipeline.photo_store, 'asset', side_effect=asset) as assets, \
                patch.object(pipeline.photo_store, 'analyze_photo', side_effect=analysis or (lambda _, digest, **options: observation(digest))) as analyze:
            output = pipeline.analyze_listing_photos('/unused-fixture', result, **kwargs)
        return output, analyze, assets

    def test_all_thirty_cached_originals_are_checked_once_with_progress_and_bounded_deadlines(self):
        result = card(30)
        before = copy.deepcopy(result)
        progress = []
        output, analyze, assets = self.analyze(result, report=lambda message, **fields: progress.append(fields))
        self.assertEqual(output['state'], 'complete')
        self.assertEqual((output['total'], output['checked'], output['failed'], output['skipped']), (30, 30, 0, 0))
        self.assertEqual(analyze.call_count, 30)
        self.assertEqual(assets.call_count, 30)
        self.assertEqual(output['features'], ['oven'])
        self.assertEqual(len(output['photoEvidence']), 30)
        self.assertTrue(all(row['count'] is None for row in output['photoEvidence']))
        self.assertTrue(all(row['status'] == 'auto-visible' for row in output['photoEvidence']))
        self.assertEqual(progress[0], {'stage': 'vision', 'completed': 0, 'total': 30})
        self.assertEqual(progress[-1], {'stage': 'vision', 'completed': 30, 'total': 30})
        self.assertTrue(all('deadline' in call.kwargs and 'cancel' in call.kwargs for call in analyze.call_args_list))
        self.assertEqual(result, before)
        self.assertNotIn('private image bytes', repr(output))

    def test_sha_duplicates_are_not_analyzed_twice_but_every_source_url_is_reported(self):
        result = card(3)
        result['media'][1]['id'] = result['media'][0]['id']
        output, analyze, _ = self.analyze(result)
        self.assertEqual(output['state'], 'complete')
        self.assertEqual((output['checked'], output['skipped']), (2, 1))
        self.assertEqual(analyze.call_count, 2)
        self.assertEqual(len(output['photos']), 3)
        self.assertEqual(output['photos'][1]['status'], 'duplicate')
        self.assertEqual(output['photos'][1]['duplicateOf'], result['media'][0]['sourceUrl'])

    def test_selected_order_and_exclusions_are_observed(self):
        result = card(3)
        urls = result['fields']['photos']
        result['fields']['photoEdits'] = {'excluded': [urls[1]], 'order': [urls[2], urls[0]]}
        output, analyze, _ = self.analyze(result)
        self.assertEqual([row['sourceUrl'] for row in output['photos']], [urls[2], urls[0]])
        self.assertEqual(output['totalAvailable'], 2)
        self.assertEqual(analyze.call_count, 2)

    def test_more_than_thirty_photos_is_explicitly_partial(self):
        output, analyze, _ = self.analyze(card(35))
        self.assertEqual(output['state'], 'partial')
        self.assertEqual((output['total'], output['totalAvailable'], output['coverageLimit']), (30, 35, 30))
        self.assertTrue(output['truncated'])
        self.assertEqual(analyze.call_count, 30)

    def test_disabled_or_missing_configuration_never_runs_apple_vision_thirty_times(self):
        for config in ({}, {**CONFIG, 'enabled': False}, {**CONFIG, 'model': ''}):
            output, analyze, assets = self.analyze(card(30), config=config)
            self.assertEqual(output['state'], 'disabled')
            self.assertEqual(output['skipped'], 30)
            analyze.assert_not_called(); assets.assert_not_called()

    def test_auto_apply_disabled_retains_review_candidates_but_publishes_no_features(self):
        output, analyze, _ = self.analyze(card(2), config={**CONFIG, 'autoApply': False})
        self.assertEqual(output['state'], 'complete')
        self.assertEqual(analyze.call_count, 2)
        self.assertEqual(output['features'], [])
        self.assertEqual(output['photoEvidence'], [])
        self.assertTrue(output['photos'][0]['candidates'][0]['autoEligible'])

    def test_empty_observation_remains_unknown_and_never_means_absent(self):
        def empty(_, digest, **kwargs):
            return {**observation(digest), 'candidates': [], 'unknownIds': ['oven', 'fridge']}
        output, _, _ = self.analyze(card(1), analysis=empty)
        self.assertEqual(output['state'], 'complete')
        self.assertEqual(output['features'], [])
        self.assertEqual(output['photos'][0]['unknownIds'], ['oven', 'fridge'])
        self.assertNotIn('absent', repr(output))

    def test_apple_classifier_and_fallback_are_review_only(self):
        def apple(_, digest, **kwargs):
            value = observation(digest)
            return {**value, 'provider': 'apple-vision', 'fallbackReason': 'unavailable'}
        output, _, _ = self.analyze(card(1), analysis=apple)
        self.assertEqual(output['state'], 'partial')
        self.assertEqual(output['checked'], 1)
        self.assertEqual(output['features'], [])
        self.assertFalse(output['photos'][0]['candidates'][0]['autoEligible'])

    def test_stale_auto_flags_wrong_hash_provider_model_negation_and_unknown_are_revalidated(self):
        def change_score(value):
            value['candidates'][0]['confidence'] = .5
        def change_hash(value):
            value['candidates'][0]['evidence'][0]['photoId'] = 'b' * 64
        def change_provider(value):
            value['candidates'][0]['evidence'][0]['provider'] = 'external'
        def change_model(value):
            value['model'] = 'different-model'
        def negate(value):
            value['candidates'][0]['evidence'][0]['text'] = 'Духовой шкаф не виден на фото'
        def unclear(value):
            value['unknownIds'].append('oven')
        for mutation in (change_score, change_hash, change_provider, change_model, negate, unclear):
            with self.subTest(mutation=mutation.__name__):
                def modified(_, digest, **kwargs):
                    value = observation(digest); mutation(value); return value
                output, _, _ = self.analyze(card(1), analysis=modified)
                self.assertEqual(output['features'], [])
                self.assertEqual(output['photoEvidence'], [])
                self.assertFalse(output['photos'][0]['candidates'][0]['autoEligible'])

    def test_missing_cached_original_wrong_kind_hash_mismatch_and_analysis_errors_are_partial(self):
        result = card(4)
        result['media'][0]['error'] = 'download failed'
        result['media'][1]['id'] = '../invalid'
        result['media'].pop()
        output, analyze, _ = self.analyze(result, asset=lambda _, digest: (b'', '', {'id': digest, 'kind': 'crop'}))
        self.assertEqual(output['state'], 'partial')
        self.assertEqual((output['skipped'], output['failed']), (3, 1))
        analyze.assert_not_called()
        output, _, _ = self.analyze(card(1), analysis=lambda *args, **kwargs: {**observation('b'*64)})
        self.assertEqual(output['failed'], 1)
        def broken(*args, **kwargs):
            raise ValueError('Failed private details should not be returned')
        output, _, _ = self.analyze(card(1), analysis=broken)
        self.assertEqual(output['failed'], 1)
        self.assertNotIn('private details', repr(output))

    def test_failure_states_do_not_publish_and_do_not_hide_remaining_photos(self):
        for state in ('unavailable', 'invalid', 'timeout', 'busy', 'cancelled'):
            with self.subTest(state=state):
                output, analyze, _ = self.analyze(card(3), analysis=lambda _, digest, **kwargs: {**observation(digest), 'status': state})
                self.assertEqual(output['state'], 'partial')
                self.assertEqual(output['failed'], 3)
                self.assertEqual(analyze.call_count, 3)
                self.assertEqual(output['features'], [])

    def test_overall_deadline_skips_remaining_and_per_photo_never_exceeds_120_seconds(self):
        with patch.object(pipeline.time, 'monotonic', side_effect=[100, 100, 100, 1000, 1000]):
            output, analyze, _ = self.analyze(card(3), max_seconds=600)
        self.assertEqual(analyze.call_count, 1)
        self.assertEqual(analyze.call_args.kwargs['deadline'], 220)
        self.assertEqual(output['state'], 'partial')
        self.assertEqual((output['checked'], output['skipped']), (1, 2))
        with patch.object(pipeline.time, 'monotonic', side_effect=[100, 100, 100]):
            _, analyze, _ = self.analyze(card(1), max_seconds=1800, config={**CONFIG, 'maxSeconds': 60})
        self.assertEqual(analyze.call_args.kwargs['deadline'], 160)

    def test_user_cancellation_raises_before_or_after_analysis_and_does_not_continue(self):
        cancel = threading.Event(); cancel.set()
        with self.assertRaisesRegex(ValueError, 'отменён'):
            self.analyze(card(3), cancel=cancel)
        cancel.clear()
        def cancel_during(_, digest, **kwargs):
            cancel.set(); return observation(digest)
        with self.assertRaisesRegex(ValueError, 'отменён'):
            self.analyze(card(3), analysis=cancel_during, cancel=cancel)

    def test_empty_album_and_legacy_single_argument_progress_are_valid(self):
        output, analyze, _ = self.analyze(card(0), report=lambda message: None)
        self.assertEqual(output['state'], 'complete')
        self.assertEqual(output['total'], 0)
        analyze.assert_not_called()


if __name__ == '__main__':
    unittest.main()
