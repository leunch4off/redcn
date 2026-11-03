"""Acceptance must survive forged scores, stale artifacts and interrupted writes."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'cli'))
import learning_scheduler
import model_promotion as promotion
from rental_heldout_quality import load_cases
from advertising_evaluation import prepare_probes, request_for
from advertising_styles import ADVERTISING_SYSTEM
from local_content import advertising_messages
from text_model_contract import EXPECTED_TRAINABLE, INFERENCE_TOP_P, file_sha, sampling_parameters


def fixture_rows(corpus, prepared=None):
    rows = []
    for index, probe in enumerate(prepared or prepare_probes(corpus)):
        case = probe['case']
        if probe['task'] == 'listing':
            variant = probe['variant']-1
            text = json.dumps({'title': '莫斯科'+case['observedStations'][0]['aliases'][0]+'租房'+
                               ('预算资料', '房源笔记', '配置说明')[variant],
                               'body': ('已确认信息：', '房源详情：', '租赁资料：')[variant]+
                               probe['baseline']['body']}, ensure_ascii=False)
        else:
            text = case['referenceZh'] if probe['task'] == 'ru-zh' else case['sourceRu']
        rows.append({'id': probe['id'], 'caseId': probe['caseId'], 'task': probe['task'],
                     'status': 'measured', 'text': text, 'seconds': 1.2, 'seed': promotion.POLICY['seed']+index,
                     'backend': 'mlx', 'fallback': False,
                     'topP': promotion.POLICY['topP'],
                     'maxTokens': promotion.POLICY['listingMaxTokens' if probe['task'] == 'listing' else 'translationMaxTokens'],
                     'temperature': promotion.POLICY['listingTemperature' if probe['task'] == 'listing' else 'translationTemperature']})
    return rows


class PromotionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.corpus = load_cases(promotion.CASES)
        cls.prepared = prepare_probes(cls.corpus)
        cls.fixture = fixture_rows(cls.corpus, cls.prepared)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cache = Path(self.temp.name)
        self.model = self.cache/'tools/text-ai/model'
        self.model.mkdir(parents=True)
        (self.model/'config.json').write_text('{}')
        (self.model/'model.safetensors').write_bytes(b'test-frozen-base')
        self.dataset = self.cache/'training.json'
        def record(i):
            return {'id': 'fiction-'+str(i), 'groupId': 'fiction-group-'+str(i),
                    'status': 'project-authored', 'source': 'synthetic-test',
                    'messages': [{'role': 'user', 'content': 'Стол '+str(i)},
                                 {'role': 'assistant', 'content': '桌子 '+str(i)}]}
        self.dataset.write_text(json.dumps({'version': 1, 'splits': {
            'train': [record(1), record(2)], 'validation': [record(3), record(4)]}}))
        self.previous = self.adapter('previous')
        self.candidate = self.adapter('candidate')
        self.config = {'enabled': True, 'modelPath': str(self.model), 'adapterPath': str(self.previous),
                       'useCandidateAdapter': True, 'maxSeconds': 55, 'advertisingEnabled': True}
        (self.cache/'text-ai.json').write_text(json.dumps(self.config))
        # Real Node rendering is checked once for this immutable corpus; the
        # acceptance tests below vary reports/artifacts rather than Node code.
        cached = patch.object(promotion, 'prepare_probes', return_value=copy.deepcopy(self.prepared))
        cached.start()
        self.addCleanup(cached.stop)
        self.rows = copy.deepcopy(self.fixture)
        self.baseline = copy.deepcopy(self.rows)
        self.baseline[0]['text'] = 'Неверные и неполные данные'

    def adapter(self, name):
        path = self.cache/'tools/text-ai/runs'/name
        path.mkdir(parents=True)
        (path/'adapters.safetensors').write_bytes(name.encode())
        (path/'adapter_config.json').write_text('{"fine_tune_type":"lora"}')
        (path/'trainreceipt.json').write_text(json.dumps({
            'status': 'trained-unaccepted', 'trainableParameters': EXPECTED_TRAINABLE,
            'stepsCompleted': 20, 'adapterSha256': file_sha(path/'adapters.safetensors'),
            'datasetSha256': file_sha(self.dataset), 'baseConfigSha256': file_sha(self.model/'config.json'),
            'baseArtifacts': {'model.safetensors': file_sha(self.model/'model.safetensors')}}))
        return path

    def report(self):
        report = {'version': promotion.REPORT_VERSION, 'finishedAt': time.time(),
                  'inputs': promotion.capture_inputs(self.cache, self.candidate, self.dataset),
                  'current': copy.deepcopy(self.baseline), 'candidate': copy.deepcopy(self.rows),
                  'decision': {'accepted': True}}
        path = self.cache/'paired-report.json'
        path.write_text(json.dumps(report, ensure_ascii=False))
        return path, report

    def test_real_checkers_require_all_facts_and_strict_improvement(self):
        result = promotion.compare_rows(self.corpus, self.baseline, self.rows)
        self.assertTrue(result['accepted'], result)
        self.assertEqual(result['candidatePassed'], 30)
        self.assertEqual(result['candidateDistinctCases'], 6)
        tied = promotion.compare_rows(self.corpus, self.rows, self.rows)
        self.assertFalse(tied['accepted'])
        self.assertEqual(tied['reason'], 'no-measured-improvement')
        self.assertFalse(result['nativeSpeakerReviewCompleted'])

    def test_listing_probes_use_shared_production_prompt_and_keep_all_heldout_facts(self):
        listings = [row for row in self.prepared if row['task'] == 'listing']
        self.assertEqual(len(listings), 18)
        self.assertEqual(len(self.prepared)-len(listings), 12)
        self.assertEqual(promotion.POLICY['name'], 'paired-independent-advertising-v2')
        for probe in listings:
            self.assertIn(probe['case']['generationFactsZh'], probe['reference'])
            request = request_for(probe, [], 42, promotion.POLICY)
            self.assertEqual(request['messages'], advertising_messages(probe['reference'], probe['style'], seed=42,
                previous_body=probe['baseline']['body'], previous_title=probe['baseline']['titles'][0]))
            self.assertEqual(request['messages'][0]['content'], ADVERTISING_SYSTEM)
            self.assertEqual(request['maxTokens'], 768)
            self.assertEqual(request['temperature'], .75)
            self.assertEqual(request['topP'], INFERENCE_TOP_P)

    def test_all_probes_bind_the_actual_production_sampling_contract(self):
        for probe in self.prepared:
            request = request_for(probe, [], 42, promotion.POLICY)
            self.assertEqual(sampling_parameters(request['temperature'], top_p=request['topP']),
                {'temp': request['temperature'], 'top_p': .9})
        for value in (None, True, 0, .8, 1, float('nan')):
            with self.assertRaises(ValueError):
                sampling_parameters(.75, top_p=value)
        rows = copy.deepcopy(self.rows)
        for row in rows:
            row.pop('topP')
        self.assertFalse(promotion.compare_rows(self.corpus, self.baseline, rows)['accepted'])

    def test_fallback_or_legacy_inference_parameters_never_count_as_measured_model_probes(self):
        for changes in ({'fallback': True}, {'backend': 'grounded-advertising'},
                        {'maxTokens': 512}, {'temperature': .35}, {'topP': 1}, {'topP': None}):
            rows = copy.deepcopy(self.rows)
            rows[2].update(changes)
            self.assertFalse(promotion.compare_rows(self.corpus, self.baseline, rows)['accepted'])

    def test_production_gate_rejects_missing_or_reversed_independent_condition(self):
        rows = copy.deepcopy(self.rows)
        draft = json.loads(rows[2]['text'])
        draft['body'] = draft['body'].replace('禁止吸烟和转租。', '')
        rows[2]['text'] = json.dumps(draft, ensure_ascii=False)
        self.assertFalse(promotion.compare_rows(self.corpus, self.baseline, rows)['accepted'])

    def test_forged_passed_scores_do_not_hide_price_or_commission_errors(self):
        for text in ('{"title":"莫斯科租房","body":"租金1卢布，中介费一个月。"}',
                     '{"title":"房源","body":"免费落地签，所有宠物均可入住。"}'):
            rows = copy.deepcopy(self.rows)
            rows[2].update(text=text, checks={'passed': True})
            result = promotion.compare_rows(self.corpus, self.baseline, rows)
            self.assertFalse(result['accepted'])
            self.assertIn(rows[2]['id'], result['candidateFailures'])
            self.assertIn(rows[2]['id'], result['regressions'])

    def test_missing_duplicate_timed_out_or_different_seed_probe_blocks(self):
        for mutate in (lambda rows: rows.pop(), lambda rows: rows.__setitem__(0, rows[1]),
                       lambda rows: rows[0].update(status='incomplete'),
                       lambda rows: rows[0].update(seconds=float('nan')),
                       lambda rows: rows[0].update(seed=0)):
            rows = copy.deepcopy(self.rows)
            mutate(rows)
            self.assertFalse(promotion.compare_rows(self.corpus, self.baseline, rows)['accepted'])

    def test_punctuation_only_variations_do_not_qualify(self):
        rows = copy.deepcopy(self.rows)
        card = json.loads(rows[2]['text'])
        for index, suffix in ((3, '!'), (4, '✅')):
            rows[index]['text'] = json.dumps({'title': card['title']+suffix, 'body': card['body']+suffix})
        result = promotion.compare_rows(self.corpus, self.baseline, rows)
        self.assertFalse(result['accepted'])
        self.assertEqual(result['reason'], 'repeated-variants')

    def test_atomic_selection_keeps_training_receipt_and_restores_previous_config(self):
        path, _ = self.report()
        receipt = (self.candidate/'trainreceipt.json').read_bytes()
        self.assertTrue(promotion.promote(self.cache, path)['selected'])
        self.assertEqual((self.candidate/'trainreceipt.json').read_bytes(), receipt)
        selected = json.loads((self.cache/'text-ai.json').read_text())
        self.assertEqual(selected['adapterPath'], str(self.candidate.resolve()))
        self.assertTrue(selected['advertisingEnabled'])
        self.assertTrue(promotion.public_status(self.cache)['rollbackAvailable'])
        self.assertNotIn(str(self.cache), json.dumps(promotion.public_status(self.cache)))
        self.assertTrue(promotion.rollback(self.cache)['rolledBack'])
        self.assertEqual(json.loads((self.cache/'text-ai.json').read_text()), self.config)
        self.assertFalse(promotion.public_status(self.cache)['rollbackAvailable'])

    def test_changed_runtime_settings_weights_data_or_report_age_block_selection(self):
        path, report = self.report()
        for target, changed in ((self.cache/'text-ai.json', json.dumps({**self.config, 'maxSeconds': 20}).encode()),
                                (self.candidate/'adapters.safetensors', b'changed'),
                                (self.model/'model.safetensors', b'changed'),
                                (self.dataset, b'{}')):
            previous = target.read_bytes()
            target.write_bytes(changed)
            with self.assertRaises(ValueError):
                promotion.promote(self.cache, path)
            target.write_bytes(previous)
        with self.assertRaises(ValueError):
            promotion.validate_report(self.cache, path, now=report['finishedAt']+86401)
        self.assertEqual(json.loads((self.cache/'text-ai.json').read_text()), self.config)

    def test_renderer_or_active_dictionary_change_invalidates_previous_acceptance_report(self):
        path, _ = self.report()
        actual = promotion.renderer_artifacts()
        with patch.object(promotion, 'renderer_artifacts', return_value={**actual, 'web/engine.mjs': 'changed'}):
            with self.assertRaises(ValueError):
                promotion.promote(self.cache, path)
        (self.cache/'phone-profile.json').write_text(json.dumps({'rules': {'Лианозово': '新的名字'}}))
        with self.assertRaises(ValueError):
            promotion.promote(self.cache, path)
        self.assertEqual(json.loads((self.cache/'text-ai.json').read_text()), self.config)

    def test_auto_toggle_is_enforced_and_does_not_assert_acceptance(self):
        path, _ = self.report()
        with self.assertRaises(ValueError):
            promotion.promote(self.cache, path, automatic=True)
        learning_scheduler.save_config(self.cache, {'automaticPromotion': True})
        status = learning_scheduler.status(self.cache)
        self.assertTrue(status['automaticPromotion'])
        self.assertEqual(status['promotion']['state'], 'not-evaluated')
        self.assertTrue(promotion.promote(self.cache, path, automatic=True)['selected'])

    def test_cli_evaluation_is_visible_without_selecting_new_weights_or_exposing_paths(self):
        path, _ = self.report()
        promotion.record_evaluation(self.cache, path)
        status = promotion.public_status(self.cache)
        self.assertEqual(status['latestEvaluation']['candidatePassed'], 30)
        self.assertEqual(status['latestEvaluation']['stepsCompleted'], 20)
        self.assertFalse(status['rollbackAvailable'])
        self.assertEqual(status['state'], 'not-evaluated')
        self.assertNotIn(str(self.cache), json.dumps(status))
        self.assertEqual(json.loads((self.cache/'text-ai.json').read_text()), self.config)

    def test_crash_after_atomic_settings_switch_keeps_a_usable_rollback(self):
        path, _ = self.report()
        actual_write = learning_scheduler._write
        def fail_final(path, value):
            if Path(path).name == 'model-promotion.json' and value.get('state') == 'accepted':
                raise OSError('simulated interruption')
            return actual_write(path, value)
        with patch.object(learning_scheduler, '_write', side_effect=fail_final), self.assertRaises(OSError):
            promotion.promote(self.cache, path)
        self.assertTrue(promotion.public_status(self.cache)['rollbackAvailable'])
        self.assertTrue(promotion.rollback(self.cache)['rolledBack'])

    def test_rollback_does_not_overwrite_a_subsequent_manual_setting(self):
        path, _ = self.report()
        promotion.promote(self.cache, path)
        active = json.loads((self.cache/'text-ai.json').read_text())
        active['enabled'] = False
        (self.cache/'text-ai.json').write_text(json.dumps(active))
        with self.assertRaises(ValueError):
            promotion.rollback(self.cache)
        self.assertEqual(json.loads((self.cache/'text-ai.json').read_text()), active)

    def test_promotion_cannot_overlap_inference_or_training(self):
        path, _ = self.report()
        with learning_scheduler.model_resource(self.cache, kind='background'):
            with self.assertRaises(learning_scheduler.ModelBusy):
                promotion.promote(self.cache, path)
        self.assertEqual(json.loads((self.cache/'text-ai.json').read_text()), self.config)


if __name__ == '__main__':
    unittest.main()
