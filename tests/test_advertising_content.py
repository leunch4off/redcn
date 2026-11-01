"""Advertising acceptance: real fact gates, durable history and safe imports."""
from concurrent.futures import ThreadPoolExecutor
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'cli'))
import advertising_styles as styles
import language_service
import local_content as content
import local_server
from learning_memory import LearningMemory
from local_text_model import clean_request
from rental_heldout_quality import check_training_disjoint, load_cases
from text_model_contract import load_dataset
from text_quality import LISTING_SYSTEM, parse_card, translation_checks


BASELINE = {'titles': ['莫斯科公寓'], 'body': (
    '莫斯科，40㎡，8/20层。月租80000卢布，押金40000卢布。'
    '水电另付。配有冰箱和书桌。禁止养宠物；房东不办理登记。'),
    'tags': '#莫斯科租房 #书桌'}
FIELDS = {'city': 'Москва', 'price': '80000', 'area': '40', 'floor': '8', 'floors': '20'}


class AdvertisingContentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def preview(self, generated, *, fields=None, extra=None, cancel=None):
        data = {'fields': copy.deepcopy(fields or FIELDS), 'styleMode': 'advertising', 'groupId': 'same-property', **(extra or {})}
        with patch.object(language_service, '_profile', return_value={}), \
                patch.object(language_service, 'node_language', return_value=copy.deepcopy(BASELINE)), \
                patch.object(content, '_model', return_value=generated) as model:
            result = content.generate_preview(self.directory, data, cancel=cancel)
        return result, model

    def test_model_prompt_is_grounded_and_complete_with_no_private_source_or_commission(self):
        fields = {**FIELDS, 'photos': ['PRIVATE_PHOTO'], 'source': 'PRIVATE_SOURCE',
                  'commission': 12345, 'smokingPolicy': 'no'}
        generated = {'status': 'review', 'text': json.dumps({'title': '莫斯科租赁信息', 'body': BASELINE['body']}),
                     'model': 'fixture-mlx', 'ai': {'generatedTokens': 42, 'tokensPerSecond': 11.2}}
        result, model = self.preview(generated, fields=fields)
        self.assertEqual(result['status'], 'review', result)
        self.assertFalse(result['fallback'])
        self.assertFalse(result['autoApply'])
        self.assertEqual(result['ai']['generatedTokens'], 42)
        request = model.call_args.args[0]
        self.assertEqual(clean_request(request)['seed'], request['seed'])
        payload = json.loads(request['messages'][1]['content'])
        self.assertIn(BASELINE['body'], payload['verifiedText'])
        self.assertNotIn('PRIVATE_', json.dumps(request))
        self.assertNotIn('12345', json.dumps(request))
        self.assertEqual(set(parse_card(generated['text'])), {'title', 'body'})
        memory = LearningMemory(self.directory)
        self.assertEqual(memory.status()['approvedExamples'], 0)
        self.assertEqual(memory.status()['counts']['pending'], 2)
        snapshot = memory.snapshot('generated-cannot-train-itself')
        self.assertEqual(snapshot['manifest']['trainExamples'], 0)
        self.assertEqual(snapshot['manifest']['validExamples'], 0)

    def test_negative_permissions_and_equipment_cannot_disappear_or_flip(self):
        for body in (BASELINE['body'].replace('禁止养宠物；房东不办理登记。', ''),
                     BASELINE['body'].replace('禁止养宠物', '可养宠物'),
                     BASELINE['body'].replace('房东不办理登记', '房东办理登记'),
                     BASELINE['body'].replace('配有冰箱和书桌。', '')):
            with self.subTest(body=body):
                self.assertTrue(content._advertising_issues({'title': '莫斯科租房资料', 'body': body}, BASELINE, FIELDS))

    def test_price_deposit_roles_and_monthly_period_cannot_be_swapped(self):
        for body in (BASELINE['body'].replace('月租80000卢布，押金40000卢布', '月租40000卢布，押金80000卢布'),
                     BASELINE['body'].replace('月租80000', '日租80000'),
                     BASELINE['body'].replace('月租80000', '价格80000')):
            with self.subTest(body=body):
                self.assertTrue(content._advertising_issues({'title': '莫斯科租房资料', 'body': body}, BASELINE, FIELDS))
        equivalent = BASELINE['body'].replace('月租80000卢布', '月租8W').replace('押金40000卢布', '押金40,000₽')
        self.assertEqual(content._advertising_issues({'title': '莫斯科租房资料', 'body': equivalent}, BASELINE, FIELDS), [])

    def test_commission_contacts_false_free_services_and_luxury_claims_are_rejected(self):
        for suffix in ('中介费一个月。', '佣金免收。', '微信号：external_identity',
                       '免费提供登记。', '全新装修，采光很好，拎包入住。', '配有洗碗机。'):
            with self.subTest(suffix=suffix):
                self.assertTrue(content._advertising_issues(
                    {'title': '莫斯科租房资料', 'body': BASELINE['body']+suffix}, BASELINE, FIELDS))

    def test_smoking_guests_and_subletting_restrictions_cannot_disappear(self):
        clauses = ('仅限不吸烟的租客。', '房东要求不接待访客或聚会。', '不得将公寓转租给他人。')
        for clause in clauses:
            baseline = {**BASELINE, 'body': BASELINE['body']+clause}
            with self.subTest(clause=clause):
                self.assertTrue(content._advertising_issues(
                    {'title': '莫斯科租房资料', 'body': BASELINE['body']}, baseline, FIELDS))

    def test_typed_negative_policies_survive_real_fallback_but_unchecked_boxes_are_not_bans(self):
        fields = {'price': '85000', 'area': '50', 'rooms': '2', 'petPolicy': 'no', 'childrenPolicy': 'no'}
        with patch.object(language_service, '_profile', return_value={}), \
                patch.object(content, '_model', return_value={'status': 'unavailable'}):
            result = content.generate_preview(self.directory, {'styleMode': 'advertising', 'fields': fields})
        self.assertEqual(result['status'], 'review', result)
        self.assertTrue(result['fallback'])
        self.assertIn('不接受宠物', result['body'])
        self.assertIn('不接受带孩子入住', result['body'])
        self.assertIn('提供办理协助', result['body'])
        original = {'body': '已确认资料。'}
        self.assertEqual(content._with_advertising_conditions(original, {'pets': False, 'children': False}), original)
        conditional = content._with_advertising_conditions(original, {'petPolicy': 'negotiable', 'childrenPolicy': 'conflict'})
        self.assertIn('宠物需事先协商', conditional['body'])
        self.assertIn('儿童入住条件待确认', conditional['body'])

    def test_confirmed_contract_terms_reach_the_fact_renderer(self):
        fields = {**FIELDS, 'leaseTerm': 'long', 'minMonths': '6', 'contractMonths': '12', 'prepayMonths': '2'}
        with patch.object(language_service, '_profile', return_value={}), \
                patch.object(language_service, 'node_language', return_value=BASELINE) as render, \
                patch.object(content, '_model', return_value={'status': 'unavailable'}):
            content.generate_preview(self.directory, {'fields': fields, 'styleMode': 'advertising'})
        forwarded = render.call_args_list[0].args[0]['fields']
        for key in ('leaseTerm', 'minMonths', 'contractMonths', 'prepayMonths'):
            self.assertEqual(forwarded.get(key), fields[key], key)

    def test_month_counts_and_occupancy_cannot_disappear_from_confirmed_copy(self):
        for clause in ('预付：2个月', '描述所列合同期限：12个月', '卡片所列最低租期：6个月',
                       '房源描述注明可入住3人。'):
            baseline = {**BASELINE, 'body': BASELINE['body']+'\n'+clause}
            with self.subTest(clause=clause):
                self.assertTrue(content._advertising_issues(
                    {'title': '莫斯科租房资料', 'body': BASELINE['body']}, baseline, FIELDS))

    def test_equipment_section_heading_does_not_claim_a_bed(self):
        baseline = {'titles': ['莫斯科公寓'],
                    'body': '莫斯科，40㎡。月租80000卢布。\n🛏️ 床与收纳\n✅ 书桌'}
        draft = {'title': '莫斯科租房资料', 'body': '莫斯科，40㎡。月租80000卢布。配有书桌。'}
        self.assertEqual(content._advertising_issues(draft, baseline, FIELDS), [])
        self.assertTrue(content._advertising_issues({**draft, 'body': draft['body']+'配有床。'}, baseline, FIELDS))

    def test_display_intro_does_not_turn_area_into_an_extra_rental_amount(self):
        baseline = {'titles': ['莫斯科公寓'],
                    'body': '莫斯科。先了解面积、租金与入住条件：40㎡的一居室。月租80000卢布。'}
        draft = {'title': '莫斯科租房资料', 'body': '莫斯科，40㎡的一居室。月租80000卢布。'}
        self.assertEqual(content._advertising_issues(draft, baseline, FIELDS), [])

    def test_durable_history_rejects_punctuation_repeats_after_recent_prompt_window(self):
        self.assertTrue(styles.claim_variant(self.directory, 'property', '莫斯科房源', '配有书桌。'))
        for index in range(40):
            self.assertTrue(styles.claim_variant(self.directory, 'property', '其他房源'+str(index), '另一份说明'+str(index)))
        self.assertFalse(styles.claim_variant(self.directory, 'property', '🏡莫斯科＿房源！', '配有书桌！'))
        self.assertTrue(styles.claim_variant(self.directory, 'other-property', '莫斯科房源', '配有书桌。'))
        self.assertFalse(styles.claim_variant(self.directory, 'property', '新的标题', '配有书桌。'))
        self.assertTrue(styles.claim_variant(self.directory, 'property', '新的标题', '新的说明'))

    def test_concurrent_claims_reserve_exactly_one_pair_and_rotation_is_atomic(self):
        LearningMemory(self.directory)
        barrier = threading.Barrier(8)
        def claim(_):
            barrier.wait()
            return styles.claim_variant(self.directory, 'same', '相同标题', '相同内容')
        with ThreadPoolExecutor(max_workers=8) as workers:
            results = list(workers.map(claim, range(8)))
        self.assertEqual(results.count(True), 1)
        with ThreadPoolExecutor(max_workers=8) as workers:
            rotations = list(workers.map(lambda _: styles.next_style(self.directory, 'same'), range(24)))
        self.assertEqual(sorted(row['sequence'] for row in rotations), list(range(24)))
        self.assertEqual(len({row['id'] for row in rotations}), 12)
        self.assertEqual(styles.next_style(self.directory, 'same')['sequence'], 24)

    def test_real_node_fallback_preserves_facts_and_returns_distinct_pending_drafts(self):
        fields = {'city': 'Москва', 'price': '85000', 'area': '50', 'rooms': '2', 'metro': 'Прокшино',
                  'walk': '9', 'floor': '8', 'floors': '20', 'features': ['fridge', 'washer', 'sofa']}
        baseline = language_service.node_language({'action': 'generate', 'fields': fields, 'rules': {},
            'prefs': {'benefits': '', 'contact': '', 'brandTags': ''}})
        rejected = {'status': 'review', 'model': 'fixture', 'text': '{"title":"错误","body":"月租1卢布，佣金一个月。"}',
                    'ai': {'generatedTokens': 10, 'tokensPerSecond': 8.4}}
        results = []
        with patch.object(language_service, '_profile', return_value={}), patch.object(content, '_model', return_value=rejected):
            for _ in range(3):
                results.append(content.generate_preview(self.directory, {'styleMode': 'advertising', 'fields': fields, 'groupId': 'node-property'}))
        self.assertEqual(len({content._content_fingerprint(row.get('body', '')) for row in results}), 3)
        self.assertEqual(len({content._content_fingerprint(row.get('title', '')) for row in results}), 3)
        for row in results:
            self.assertEqual(row['status'], 'review', row)
            self.assertTrue(row['fallback'])
            self.assertEqual(row['backend'], 'grounded-advertising')
            self.assertEqual(row['ai']['tokensPerSecond'], 8.4)
            self.assertFalse(row['autoApply'])
            self.assertEqual(content._advertising_issues(row, baseline, fields, max_body=600), [])
            self.assertNotIn('佣金', row['body'])
        self.assertEqual(LearningMemory(self.directory).status()['approvedExamples'], 0)

    def test_model_cancellation_does_not_claim_or_record_a_draft(self):
        cancel = threading.Event()
        def model(*args, **kwargs):
            cancel.set()
            return {'status': 'review', 'text': json.dumps({'title': '莫斯科房源信息', 'body': BASELINE['body']})}
        with patch.object(language_service, '_profile', return_value={}), \
                patch.object(language_service, 'node_language', return_value=BASELINE), \
                patch.object(content, '_model', side_effect=model):
            result = content.generate_preview(self.directory, {'styleMode': 'advertising', 'fields': FIELDS}, cancel=cancel)
        self.assertEqual(result['status'], 'cancelled')
        self.assertEqual(LearningMemory(self.directory).status()['counts']['pending'], 0)

    def test_auto_import_keeps_original_on_cancel_failure_or_blocked_output(self):
        original = {'fields': FIELDS, 'titles': ['原始标题'], 'body': '原始正文', 'tags': '#莫斯科租房'}
        cancel = threading.Event()
        cancel.set()
        with patch.object(content, 'generate_preview') as preview:
            self.assertIs(content.generate_import_advertisement(self.directory, original, cancel=cancel), original)
            preview.assert_not_called()
        cancel.clear()
        def cancelled(*args, **kwargs):
            cancel.set()
            return {'status': 'review', 'title': '新的', 'body': '新的', 'tags': ''}
        with patch.object(content, 'generate_preview', side_effect=cancelled):
            self.assertIs(content.generate_import_advertisement(self.directory, original, cancel=cancel), original)
        for mode in ({'status': 'blocked', 'warnings': ['fixture']}, OSError('fixture unavailable')):
            kwargs = {'side_effect': mode} if isinstance(mode, Exception) else {'return_value': mode}
            with patch.object(content, 'generate_preview', **kwargs):
                result = content.generate_import_advertisement(self.directory, original)
            for key in ('fields', 'titles', 'body', 'tags'):
                self.assertEqual(result[key], original[key])

    def test_import_completion_preserves_manual_text_saved_during_generation(self):
        original = {'fields': copy.deepcopy(FIELDS), 'titles': ['原始标题'], 'body': '原始正文',
                    'tags': '#莫斯科租房', 'media': []}
        result_id = local_server.save_result(self.directory, original)
        edited = {**original, 'body': '用户已确认的手动正文'}
        local_server.save_result(self.directory, edited, result_id)
        generated = {**original, 'body': '稍后完成的自动正文', 'aiAdvertisement': {'status': 'review'}}
        merged = local_server.merge_prepared_media(self.directory, result_id, original,
                    photo_baseline=original, photo_generated=generated)
        self.assertEqual(merged['body'], edited['body'])
        self.assertNotIn('aiAdvertisement', merged)
        self.assertEqual(local_server.read_result(self.directory, result_id)['body'], edited['body'])


class FictionalCorpusTests(unittest.TestCase):
    def test_corpus_rebuild_is_identical_disjoint_from_holdout_and_group_split(self):
        spec = importlib.util.spec_from_file_location('advertising_corpus_fixture', ROOT/'scripts/build-advertising-corpus.py')
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        path = ROOT/'data/learning/advertising-fictional-v27.json'
        self.assertEqual(builder.build(), json.loads(path.read_text()))
        corpus = load_dataset(path)
        groups = {name: {row['groupId'] for row in rows} for name, rows in corpus['splits'].items()}
        self.assertFalse(groups['train'] & groups['validation'])
        self.assertEqual({name: len(rows) for name, rows in corpus['splits'].items()}, {'train': 640, 'validation': 128})
        self.assertFalse(check_training_disjoint(load_cases(ROOT/'data/quality/rental-heldout-v26.json'), corpus)['exactTextOverlap'])
        self.assertFalse(corpus['provenance']['containsCopiedAdvertisements'])
        self.assertFalse(corpus['provenance']['nativeEditorAccepted'])

    def test_every_authored_target_matches_its_fact_message_and_runtime_shape(self):
        corpus = load_dataset(ROOT/'data/learning/advertising-fictional-v27.json')
        for rows in corpus['splits'].values():
            for row in rows:
                with self.subTest(row=row['id']):
                    system, request, answer = row['messages']
                    payload = json.loads(request['content'])
                    if system['content'] == LISTING_SYSTEM:
                        draft = parse_card(answer['content'])
                        self.assertEqual(set(draft), {'title', 'body'})
                        self.assertIn('style', payload)
                        self.assertIn('variation', payload)
                        baseline = {'titles': [], 'body': payload['verifiedText']}
                        self.assertEqual(content._advertising_issues(draft, baseline, {}), [])
                    else:
                        direction = 'ru-zh' if row['id'].endswith('ru-zh') else 'zh-ru'
                        self.assertTrue(translation_checks(payload['text'], answer['content'], direction)['passed'])


if __name__ == '__main__':
    unittest.main()
