"""The new curriculum must teach complete facts without leaking test answers."""
from collections import Counter
import copy
from decimal import Decimal
import importlib.util
import json
import re
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'cli'))
from advertising_styles import ADVERTISING_SYSTEM, STYLES
from local_content import advertising_messages, _advertising_issues, _content_fingerprint
from rental_heldout_quality import check_training_disjoint, load_cases
from text_model_contract import load_dataset, file_sha
from text_quality import claims, parse_card, translation_checks
from rental_conditions import fee_claims

SPEC = importlib.util.spec_from_file_location('factual_curriculum_v28', ROOT / 'scripts/build-factual-curriculum-v28.py')
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


class FactualCurriculumTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load_dataset(ROOT / 'data/learning/factual-curriculum-v28.json')
        cls.bases = {}
        for rows in cls.data['splits'].values():
            for row in rows:
                if row['task'] == 'advertising' and row['groupId'] not in cls.bases:
                    cls.bases[row['groupId']] = builder.production_basis(
                        row['verifiedFields'], row['additionalVerifiedText'])

    def test_reproducibility_and_preserved_experiments(self):
        self.assertEqual(file_sha(ROOT/'data/learning/factual-curriculum-v28.json'),
            'e5bcb3ee99e654e9cfb284d2f8fc4ae9fff29c820219ab35fa3b42f987e71b27')
        rebuilt=builder.build()
        current_contract=file_sha(ROOT/'cli/local_content.py')
        self.assertEqual(rebuilt['provenance']['sourceContractSha256'],current_contract)
        # Frozen provenance describes the validator that authored v28. A
        # current rebuild records the current checker without relabelling the
        # historical corpus. Every row and every other field must match.
        expected=copy.deepcopy(self.data)
        expected['provenance']['sourceContractSha256']=current_contract
        self.assertEqual(rebuilt,expected)
        for name, count in (('advertising-fictional-v27.json', 640),
                            ('advertising-production-v27b.json', 200)):
            self.assertEqual(len(load_dataset(ROOT / 'data/learning' / name)['splits']['train']), count)
        provenance = self.data['provenance']
        for key in ('containsUserListings', 'containsCopiedAdvertisements', 'containsHeldoutCases',
                    'containsModelGeneratedTargets', 'nativeEditorAccepted', 'supersedesExperimentData'):
            self.assertIs(provenance[key], False, key)
        self.assertLess((ROOT / 'data/learning/factual-curriculum-v28.json').stat().st_size, 8_000_000)

    def test_targets_keep_every_fact_and_use_shared_production_messages(self):
        for rows in self.data['splits'].values():
            for row in rows:
                if row['task'] != 'advertising':
                    continue
                with self.subTest(row=row['id']):
                    fields, baseline = self.bases[row['groupId']]
                    request = json.loads(row['messages'][1]['content'])
                    reference = '\n'.join([baseline['titles'][0], baseline['body']]).strip()
                    self.assertEqual(request['verifiedText'], reference)
                    self.assertEqual(row['messages'][0]['content'], ADVERTISING_SYSTEM)
                    self.assertEqual(row['messages'][:-1], advertising_messages(reference, request['style'],
                        seed=request['variation'], previous_body=request['previousTextToAvoid'],
                        previous_title=request['previousTitleToAvoid'], recent_titles=request['recentTitlesToAvoid']))
                    answer = parse_card(row['messages'][-1]['content'])
                    self.assertEqual(_advertising_issues(answer, baseline, fields), [])
                    self.assertTrue(request['previousTextToAvoid'])
                    self.assertLessEqual(len(request['previousTextToAvoid']), 240)
                    self.assertTrue(request['previousTitleToAvoid'])
                    self.assertNotEqual(_content_fingerprint(answer['title']),
                                        _content_fingerprint(request['previousTitleToAvoid']))
                    self.assertNotEqual(_content_fingerprint(answer['body']),
                                        _content_fingerprint(request['previousTextToAvoid']))
                    # A JSON-escaped newline decodes to a newline, never a
                    # literal backslash-n taught inside the final body.
                    self.assertNotIn('\\n', answer['body'])

    def test_translations_keep_names_negations_money_and_equipment(self):
        for rows in self.data['splits'].values():
            for row in rows:
                if not row['task'].startswith('translation-'):
                    continue
                with self.subTest(row=row['id']):
                    source = json.loads(row['messages'][1]['content'])['text']
                    answer = row['messages'][-1]['content']
                    checks = translation_checks(source, answer, row['task'].removeprefix('translation-'))
                    self.assertTrue(checks['passed'], checks)
                    for name in builder.STATIONS:
                        self.assertEqual(name in source, name in answer)

    def test_displayed_wan_amounts_are_exact_and_property_conditions_are_consistent(self):
        for rows in self.data['splits'].values():
            indexed = {}
            for row in rows:
                indexed.setdefault(row['groupId'], []).append(row)
                if row['task'] == 'advertising':
                    body = parse_card(row['messages'][-1]['content'])['body']
                    for wan, rub in re.findall(r'(\d+(?:\.\d+)?)W[^\n（]*（([\d,]+)₽）', body):
                        self.assertEqual(Decimal(wan) * 10000, Decimal(rub.replace(',', '')), row['id'])
            for group_id, variants in indexed.items():
                ad = next(row for row in variants if row['task'] == 'advertising')
                translation = next(row for row in variants if row['task'] == 'translation-ru-zh')
                fields, baseline = self.bases[group_id]
                zh = translation['messages'][-1]['content']
                expected = {'no': {'absent'}, 'yes': {'present'},
                            'negotiable': {'unknown'}, 'conflict': {'unknown'}}
                observed = claims(zh, 'zh')
                self.assertEqual(observed['pets'], expected[fields['petPolicy']], group_id)
                self.assertEqual(observed['children'], expected[fields['childrenPolicy']], group_id)
                self.assertEqual(fee_claims(zh, 'zh'), fee_claims(baseline['body'], 'zh'), group_id)
                for station in builder.STATIONS:
                    self.assertEqual(station in baseline['body'], station in zh, group_id)
                if fields.get('walk'):
                    self.assertIn('步行' + fields['walk'] + '分钟到' + fields['metro'], zh, group_id)

    def test_train_validation_and_unchanged_frozen_benchmark_are_separate(self):
        train = self.data['splits']['train']
        validation = self.data['splits']['validation']
        self.assertEqual(len(train), 600)
        self.assertEqual(len(validation), 120)
        self.assertEqual(len({row['groupId'] for row in train}), 120)
        self.assertEqual(len({row['groupId'] for row in validation}), 24)
        self.assertFalse({row['groupId'] for row in train} & {row['groupId'] for row in validation})
        independent = check_training_disjoint(load_cases(ROOT / 'data/quality/rental-heldout-v26.json'), self.data)
        self.assertFalse(independent['exactTextOverlap'])

    def test_curriculum_coverage_and_bounded_validation_mix(self):
        for split, rows in self.data['splits'].items():
            ads = [row for row in rows if row['task'] == 'advertising']
            self.assertEqual({row['styleId'] for row in ads}, {style[0] for style in STYLES})
            self.assertTrue(all(row['historyPresent'] for row in ads))
            self.assertTrue(any(row['sparse'] for row in ads))
            self.assertTrue(any(not row['sparse'] for row in ads))
            self.assertTrue(any('▪️' in row['additionalVerifiedText'] for row in ads))
            self.assertTrue(all('未配备' in row['additionalVerifiedText'] and
                                '尚未确认' in row['additionalVerifiedText'] for row in ads))
            self.assertTrue(any(not row['verifiedFields'].get('area') for row in ads))
            self.assertTrue(any(not row['verifiedFields'].get('rooms') for row in ads))
            for row in ads:
                if row['verifiedFields'].get('layout') == 'studio':
                    self.assertNotIn('0居室', row['messages'][-1]['content'])
        first = self.data['splits']['validation'][:32]
        self.assertEqual(self.data['provenance']['trainingHistoryCharacters'], 240)
        self.assertEqual(self.data['provenance']['productionHistoryMaximumCharacters'], 300)
        self.assertEqual(len({row['groupId'] for row in first}), 8)
        self.assertEqual(Counter(row['task'] for row in first),
                         {'advertising': 16, 'translation-ru-zh': 8, 'translation-zh-ru': 8})
        self.assertEqual({row['sparse'] for row in first if row['task'] == 'advertising'}, {True, False})


if __name__ == '__main__':
    unittest.main()
