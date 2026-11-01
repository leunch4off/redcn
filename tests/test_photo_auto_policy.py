import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'cli'))
import photo_auto_policy as policy

DIGEST = 'a' * 64
URL = 'https://images.cdn-cian.ru/images/policy-fixture.jpg'


def observation(digest=DIGEST, score=.96, feature='sofa'):
    return {'status': 'review', 'provider': 'yolox-trained', 'model': policy.TRAINED_MODEL,
        'originalId': digest, 'sourceUrl': URL, 'unknownIds': [],
        'candidates': [{'id': feature, 'label': 'Диван', 'confidence': score,
            'count': 3, 'autoEligible': True, 'autoCountEligible': True,
            'evidence': [{'kind': 'object-detection', 'provider': 'yolox-trained',
                'model': policy.TRAINED_MODEL, 'source': 'local-photo',
                'photoId': digest, 'originalId': digest, 'confidence': score,
                'region': [.1, .2, .7, .6]}]}]}


class PhotoAutomaticReviewTests(unittest.TestCase):
    def test_enabled_adds_real_review_entry_without_confirming_detector_facts(self):
        raw = observation()
        before = copy.deepcopy(raw)
        result = policy.trained_policy(raw, requested=True)
        self.assertTrue(result['autoApply'])
        self.assertEqual(result['autoApplyMode'], 'suggestions-only')
        self.assertEqual(result['autoApplySummary']['automaticFacts'], 0)
        self.assertEqual(result['autoApplySummary']['acceptedClasses'], 0)
        self.assertEqual(result['autoApplySummary']['automaticSuggestions'], 1)
        candidate = result['candidates'][0]
        self.assertTrue(candidate['automaticSuggestion'])
        self.assertTrue(candidate['requiresConfirmation'])
        self.assertFalse(candidate['autoEligible'])
        self.assertFalse(candidate['autoCountEligible'])
        self.assertIsNone(candidate['count'])
        journal = policy.review_journal({}, [result], requested=True, now='2026-11-02T12:00:00+00:00')
        self.assertEqual((journal['added'], journal['pending']), (1, 1))
        entry = journal['entries'][0]
        self.assertEqual(entry['id'], candidate['reviewId'])
        self.assertEqual(entry['state'], 'pending')
        self.assertEqual(entry['originalId'], DIGEST)
        self.assertIsNone(entry['count'])
        self.assertEqual(entry['evidence'][0]['region'], [.1, .2, .7, .6])
        self.assertEqual(raw, before)

    def test_switch_off_does_not_add_entries_and_score_is_not_acceptance(self):
        for requested, score, expected in [(False, .99, 0), (True, .84, 0), (True, .85, 1)]:
            with self.subTest(requested=requested, score=score):
                result = policy.trained_policy(observation(score=score), requested=requested)
                journal = policy.review_journal({}, [result], requested=requested)
                self.assertEqual(journal['pending'], expected)
                self.assertFalse(result['candidates'][0]['autoEligible'])

    def test_wrong_model_original_region_unknown_and_unsupported_class_cannot_enter_queue(self):
        def wrong_original(row): row['candidates'][0]['evidence'][0]['originalId'] = 'b' * 64
        def wrong_source(row): row['candidates'][0]['evidence'][0]['source'] = 'copied-ad-example'
        def no_box(row): row['candidates'][0]['evidence'][0]['region'] = None
        def overflowing_box(row): row['candidates'][0]['evidence'][0]['region'] = [.8, .2, .4, .5]
        def false_score(row): row['candidates'][0]['confidence'] = True
        def conflicting_score(row): row['candidates'][0]['evidence'][0]['confidence'] = .95
        def unknown(row): row['unknownIds'] = ['sofa']
        def wrong_model(row): row['model'] = 'other-model'
        def unknown_class(row): row['candidates'][0]['id'] = 'brandName'
        for mutate in (wrong_original, wrong_source, no_box, overflowing_box, false_score,
                conflicting_score, unknown, wrong_model, unknown_class):
            with self.subTest(mutate=mutate.__name__):
                row = observation(); mutate(row)
                result = policy.trained_policy(row, requested=True)
                self.assertEqual(result['autoApplySummary']['automaticSuggestions'], 0)
                self.assertEqual(policy.review_journal({}, [result], requested=True)['entries'], [])

    def test_rescan_deduplicates_by_original_and_preserves_decisions(self):
        photo = policy.trained_policy(observation(), requested=True)
        first = policy.review_journal({}, [photo, photo], requested=True, now='first')
        fields = {'photoEdits': {'reviewJournal': first,
            'observations': {URL: {'analysis': {'originalId': DIGEST}, 'rejected': {'sofa': True}}}}}
        before = copy.deepcopy(fields)
        again = policy.review_journal(fields, [photo], requested=True, now='second')
        self.assertEqual(len(again['entries']), 1)
        self.assertEqual((again['added'], again['pending'], again['rejected']), (0, 0, 1))
        self.assertEqual(again['entries'][0]['createdAt'], 'first')
        self.assertEqual(again['entries'][0]['lastSeenAt'], 'second')
        self.assertEqual(fields, before)
        fields['photoEdits']['reviewJournal'] = again
        fields['photoEdits']['observations'][URL] = {'analysis': {'originalId': DIGEST}, 'confirmed': {'sofa': {'count': 2}}}
        confirmed = policy.review_journal(fields, [photo], requested=True)
        self.assertEqual(confirmed['entries'][0]['state'], 'confirmed')
        self.assertIsNone(confirmed['entries'][0]['count'])

    def test_changed_original_never_inherits_old_confirmation_and_cards_are_isolated(self):
        old = policy.trained_policy(observation(), requested=True)
        first = policy.review_journal({}, [old], requested=True)
        fields = {'photoEdits': {'reviewJournal': first,
            'observations': {URL: {'analysis': {'originalId': DIGEST}, 'confirmed': {'sofa': {}}}}}}
        replacement = policy.trained_policy(observation(digest='b' * 64), requested=True)
        journal = policy.review_journal(fields, [replacement], requested=True)
        fresh = next(row for row in journal['entries'] if row['originalId'] == 'b' * 64)
        self.assertEqual(fresh['state'], 'pending')
        self.assertEqual(policy.review_journal({}, [], requested=True)['entries'], [])
        self.assertEqual(policy.review_journal(fields, [replacement], requested=False)['added'], 0)

    def test_global_rejections_and_same_original_url_aliases_take_precedence(self):
        fields = {'photoRejectedFeatures': ['washer'], 'photoEdits': {'observations': {
            URL: {'analysis': {'originalId': DIGEST}, 'confirmed': {'washer': {}, 'sofa': {}}},
            URL + '?alias': {'analysis': {'originalId': DIGEST}, 'rejected': {'sofa': True}}}}}
        rejected, confirmed = policy.human_decisions(fields, URL, DIGEST)
        self.assertEqual(rejected, {'washer', 'sofa'})
        self.assertEqual(confirmed, set())
        rejected, confirmed = policy.human_decisions(fields, URL, 'b' * 64)
        self.assertEqual(rejected, {'washer'})
        self.assertEqual(confirmed, set())


if __name__ == '__main__':
    unittest.main()
