import copy
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'cli'))
from photo_teacher_review import make_review, merge_reviews, valid_review

SHA = 'a' * 64
URL = 'https://images.cdn-cian.ru/images/test-1.jpg'


class AssistantReviewTests(unittest.TestCase):
    def fixture(self):
        review = make_review(SHA, ['washer', 'sofa'], dataset_id='b' * 64,
                             reviewed_at='2026-11-03T00:00:00Z')
        result = {'fields': {'features': ['tv'], 'photos': [URL], 'featureCounts': {'tv': 1}},
                  'body': 'Existing operator text', 'media': [{'id': SHA, 'sourceUrl': URL, 'kind': 'original'}]}
        return result, [{'sourceUrl': URL, 'review': review}]

    def test_positive_witness_is_not_human_label_or_model_score(self):
        result, reviews = self.fixture()
        original = copy.deepcopy(result)
        output, summary = merge_reviews(result, reviews)
        self.assertEqual(result, original)
        self.assertEqual(output['body'], result['body'])
        self.assertEqual(output['fields']['features'], ['sofa', 'tv', 'washer'])
        self.assertEqual(output['fields']['featureCounts'], {'tv': 1})
        observation = output['fields']['photoEdits']['observations'][URL]
        self.assertEqual(observation['confirmed'], {})
        self.assertEqual(summary['addedFeatures'], ['sofa', 'washer'])
        self.assertTrue(all(row['status'] == 'assistant-reviewed' and row['humanVerified'] is False
                            and 'confidence' not in row and row['count'] is None
                            for row in output['fields']['photoEvidence']))

    def test_changed_original_and_exclusion_invalidate_review(self):
        result, reviews = self.fixture()
        result['media'][0]['id'] = 'c' * 64
        output, summary = merge_reviews(result, reviews)
        self.assertEqual(output['fields']['features'], ['tv'])
        self.assertEqual(summary['reviewedPhotos'], 0)
        result['media'][0]['id'] = SHA
        result['fields']['photoEdits'] = {'excluded': [URL]}
        self.assertEqual(merge_reviews(result, reviews)[1]['reviewedPhotos'], 0)

    def test_manual_rejection_and_source_conflict_are_not_overwritten(self):
        result, reviews = self.fixture()
        result['fields']['photoRejectedFeatures'] = ['sofa']
        result['fields']['sourceAudit'] = {'furniture': [{'id': 'washer', 'status': 'absent'}]}
        output, summary = merge_reviews(result, reviews)
        self.assertEqual(output['fields']['features'], ['tv'])
        self.assertEqual(len(summary['conflicts']), 2)
        self.assertEqual(output['fields']['photoEvidence'], [])

    def test_changed_bytes_do_not_rebind_old_photo_decisions_or_counts(self):
        result, reviews = self.fixture()
        old_sha = 'c' * 64
        old = {'analysis': {'originalId': old_sha, 'candidates': [{'id': 'washer'}, {'id': 'sofa'}]},
               'confirmed': {'washer': {'count': 2}}, 'rejected': {'sofa': True}}
        journal = {'version': 1, 'entries': [{'originalId': old_sha, 'featureId': 'washer', 'state': 'confirmed'}]}
        result['fields']['photoEdits'] = {'observations': {URL: old}, 'reviewJournal': journal}
        original = copy.deepcopy(result)
        output, summary = merge_reviews(result, reviews)
        observation = output['fields']['photoEdits']['observations'][URL]
        self.assertEqual(observation['analysis']['originalId'], SHA)
        self.assertEqual(observation['confirmed'], {})
        self.assertEqual(observation['rejected'], {})
        self.assertEqual(output['fields']['featureCounts'], {'tv': 1})
        self.assertIn('tv', output['fields']['features'])
        self.assertEqual(output['fields']['photoEdits']['reviewJournal'], journal)
        self.assertEqual(sorted(summary['addedFeatures']), ['sofa', 'washer'])
        self.assertTrue(all(row['humanVerified'] is False and row['count'] is None
                            for row in output['fields']['photoEvidence']))
        self.assertEqual(result, original)

    def test_same_original_keeps_operator_confirmation_and_rejection(self):
        result, reviews = self.fixture()
        result['fields']['photoEdits'] = {'observations': {URL: {
            'analysis': {'originalId': SHA, 'candidates': [{'id': 'washer'}, {'id': 'sofa'}]},
            'confirmed': {'washer': {'count': 2}}, 'rejected': {'sofa': True}}}}
        output, summary = merge_reviews(result, reviews)
        observation = output['fields']['photoEdits']['observations'][URL]
        self.assertEqual(observation['confirmed'], {'washer': {'count': 2}})
        self.assertEqual(observation['rejected'], {'sofa': True})
        self.assertEqual(summary['addedFeatures'], ['washer'])
        self.assertEqual(summary['conflicts'][0]['reason'], 'operator-rejected')

    def test_idempotent_and_no_fabricated_room_or_bed_size(self):
        result, reviews = self.fixture()
        first, _ = merge_reviews(result, reviews)
        second, summary = merge_reviews(first, reviews)
        self.assertEqual(first['fields'], second['fields'])
        self.assertEqual(summary['addedFeatures'], [])
        for value in ('internet', 'bed', 'roomFurniture', 'closet'):
            with self.assertRaises(ValueError):
                make_review(SHA, [value], dataset_id='b' * 64, reviewed_at='2026-11-03T00:00:00Z')
        self.assertFalse(valid_review({**reviews[0]['review'], 'humanVerified': True}))


if __name__ == '__main__':
    unittest.main()
