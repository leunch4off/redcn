import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'cli'))
from rental_heldout_quality import assess, check_training_disjoint, generation_messages, load_cases, probes, variation_summary


class HeldoutRentalTests(unittest.TestCase):
    def setUp(self):
        self.corpus = load_cases(ROOT/'data/quality/rental-heldout-v26.json')

    def test_six_independent_real_fact_extracts_define_both_directions_and_three_styles(self):
        rows = probes(self.corpus)
        self.assertEqual(len(rows), 30)
        self.assertEqual(len({row['id'] for row in rows}), 30)
        for case in self.corpus['cases']:
            self.assertEqual([row['task'] for row in rows if row['caseId'] == case['id']], ['ru-zh', 'zh-ru', 'listing', 'listing', 'listing'])
        self.assertFalse(self.corpus['annotation']['nativeSpeakerReview'])
        self.assertFalse(self.corpus['annotation']['trainingUseAllowed'])

    def test_independently_authored_reference_pairs_pass_frozen_partial_checks(self):
        for probe in probes(self.corpus):
            if probe['task'] == 'listing':
                continue
            reference = probe['case']['referenceZh'] if probe['task'] == 'ru-zh' else probe['case']['sourceRu']
            with self.subTest(probe=probe['id']):
                self.assertTrue(assess(probe, reference)['passed'])

    def test_conflicted_area_is_not_silently_taught_as_a_generation_fact(self):
        case = self.corpus['cases'][1]
        self.assertTrue(case['conflicts'])
        self.assertNotIn('area', case['fields'])
        self.assertNotIn('60㎡', case['generationFactsZh'])
        self.assertNotIn('64㎡', case['generationFactsZh'])
        self.assertIn('60', case['sourceRu'])

    def test_exact_training_overlap_fails_without_claiming_semantic_independence(self):
        result = check_training_disjoint(self.corpus, {'train': [{'text': 'Unrelated authored training example'}]})
        self.assertFalse(result['semanticOverlapAutomaticallyExcluded'])
        with self.assertRaises(ValueError):
            check_training_disjoint(self.corpus, {'train': [{'text': self.corpus['cases'][0]['sourceRu']}]})

    def test_previous_model_outputs_are_supplied_only_as_avoidance_context(self):
        probe = next(row for row in probes(self.corpus) if row['task'] == 'listing')
        messages = generation_messages(probe, [{'title': '旧标题', 'body': '旧正文'}])
        payload = json.loads(messages[1]['content'])
        self.assertEqual(payload['recentTitlesToAvoid'], ['旧标题'])
        self.assertEqual(payload['previousTextToAvoid'], '旧正文')
        self.assertEqual(payload['verifiedText'], probe['case']['generationFactsZh'])

    def test_emoji_only_variants_are_not_originality(self):
        rows = [{'caseId': 'heldout-01', 'task': 'listing', 'status': 'measured',
                 'text': json.dumps({'title': icon+'同一标题', 'body': icon+'同一正文。'})}
                for icon in ('🏠', '✨', '📌')]
        result = variation_summary(rows)[0]
        self.assertEqual(result['uniqueNormalizedTitles'], 1)
        self.assertFalse(result['allThreeSurfacesDifferent'])

    def test_dataset_has_no_addresses_contacts_urls_or_raw_card_identifiers(self):
        encoded = json.dumps(self.corpus, ensure_ascii=False)
        for forbidden in ('Бориса Пастернака', 'http://', 'https://', 'phone', 'cardId', 'bodyZh'):
            self.assertNotIn(forbidden, encoded)


if __name__ == '__main__':
    unittest.main()
