"""Prevent a repeat of the training/inference distribution contract mismatch."""
from collections import defaultdict
import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'cli'))
from advertising_styles import ADVERTISING_SYSTEM, STYLES
from local_content import advertising_baseline, advertising_messages, _advertising_issues, _content_fingerprint
from rental_heldout_quality import check_training_disjoint, load_cases
from text_model_contract import load_dataset
from text_quality import parse_card, translation_checks

SPEC = importlib.util.spec_from_file_location('aligned_advertising_builder', ROOT/'scripts/build-aligned-advertising-corpus.py')
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


class AlignedAdvertisingCorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load_dataset(ROOT/'data/learning/advertising-production-v27b.json')
        cls.rendered = {}
        for rows in cls.data['splits'].values():
            for row in rows:
                if row['task'] == 'advertising' and row['groupId'] not in cls.rendered:
                    cls.rendered[row['groupId']] = advertising_baseline(row['verifiedFields'])

    def test_checked_in_dataset_is_reproducible_and_prior_experiment_is_preserved(self):
        self.assertEqual(builder.build(), self.data)
        previous = load_dataset(ROOT/'data/learning/advertising-fictional-v27.json')
        self.assertEqual(len(previous['splits']['train']), 640)
        self.assertFalse(self.data['provenance']['supersedesExperimentData'])
        self.assertFalse(self.data['provenance']['containsModelGeneratedTargets'])

    def test_all_advertising_messages_use_complete_production_facts_and_contract(self):
        for rows in self.data['splits'].values():
            for row in rows:
                if row['task'] != 'advertising':
                    continue
                with self.subTest(record=row['id']):
                    fields, baseline = self.rendered[row['groupId']]
                    payload = json.loads(row['messages'][1]['content'])
                    reference = '\n'.join([baseline['titles'][0], baseline['body']]).strip()
                    self.assertEqual(payload['verifiedText'], reference)
                    self.assertEqual(row['messages'][0]['content'], ADVERTISING_SYSTEM)
                    expected = advertising_messages(reference, payload['style'], seed=payload['variation'],
                        previous_body=payload['previousTextToAvoid'], previous_title=payload['previousTitleToAvoid'],
                        recent_titles=payload['recentTitlesToAvoid'])
                    self.assertEqual(row['messages'][:-1], expected)
                    target = parse_card(row['messages'][-1]['content'])
                    self.assertEqual(_advertising_issues(target, baseline, fields), [])
                    self.assertNotIn('工作学习更方便', target['body'])
                    if row['historyPresent']:
                        self.assertTrue(payload['previousTextToAvoid'])
                        self.assertTrue(payload['previousTitleToAvoid'])
                        self.assertNotEqual(_content_fingerprint(target['title']),
                                            _content_fingerprint(payload['previousTitleToAvoid']))
                        self.assertNotEqual(_content_fingerprint(target['body']),
                                            _content_fingerprint(payload['previousTextToAvoid']))

    def test_coverage_has_all_styles_sparse_facts_and_independent_station_policies(self):
        for split, rows in self.data['splits'].items():
            ads = [row for row in rows if row['task'] == 'advertising']
            self.assertEqual({row['styleId'] for row in ads}, {style[0] for style in STYLES}, split)
            self.assertEqual(sum(row['historyPresent'] for row in ads), len(ads)//2)
            self.assertTrue(any(not row['verifiedFields'].get('features') for row in ads))
            self.assertTrue(any(not row['verifiedFields'].get('walk') for row in ads))
        combinations = defaultdict(set)
        for row in self.data['splits']['train']:
            if row['task'] == 'advertising' and row['verifiedFields'].get('metro'):
                fields = row['verifiedFields']
                combinations[fields['metro']].add((fields['rooms'], fields['petPolicy'], fields['utilities']))
        self.assertTrue(any(len(values) >= 3 for values in combinations.values()))
        first = self.data['splits']['validation'][:32]
        self.assertEqual(len({row['groupId'] for row in first}), 8)
        self.assertEqual({row['task'] for row in first}, {'advertising', 'translation-ru-zh', 'translation-zh-ru'})
        self.assertEqual({row['styleId'] for row in first if row['task'] == 'advertising'}, {style[0] for style in STYLES})

    def test_authored_new_translations_keep_negations_conditions_and_money(self):
        for rows in self.data['splits'].values():
            for row in rows:
                if not row['id'].startswith('fictional-production-') or not row['task'].startswith('translation-'):
                    continue
                with self.subTest(record=row['id']):
                    source = json.loads(row['messages'][1]['content'])['text']
                    result = translation_checks(source, row['messages'][-1]['content'], row['task'].removeprefix('translation-'))
                    self.assertTrue(result['passed'], result)

    def test_translation_replay_keeps_original_splits_and_frozen_heldout_is_disjoint(self):
        replay = builder.balanced_translation_replay()
        for split in ('train', 'validation'):
            actual = {row['id']: row for row in self.data['splits'][split]}
            for row in replay[split]:
                self.assertEqual(actual[row['id']], row)
        self.assertFalse(check_training_disjoint(
            load_cases(ROOT/'data/quality/rental-heldout-v26.json'), self.data)['exactTextOverlap'])


if __name__ == '__main__':
    unittest.main()
