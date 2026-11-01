import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'cli'))
from text_quality import (claims,language_check,money_roles,parse_card,polarity_issues,
    translation_checks)
from text_quality_probes import probes
from text_model_contract import inference_model, expected_parameters, BASE_MODEL
import local_content


class TextQualityTests(unittest.TestCase):
    def test_json_duplicate_keys_and_extra_fields_are_rejected(self):
        for text in ('{"title":"一", "body":"二", "body":"三"}',
            '{"title":"一","body":"二","phone":"123"}',
            '{"title":"一","body":null}'):
            with self.subTest(text=text),self.assertRaises(ValueError):parse_card(text)
        self.assertEqual(parse_card('```json\n{"title":"一","body":"二"}\n```')['body'],'二')

    def test_mixed_language_is_not_a_russian_translation(self):
        self.assertFalse(language_check('Аренда91200卢布，面积51.5平方米，位于16楼。','ru')['passed'])
        self.assertFalse(language_check('Rent and apartment with sofa 桌','zh')['passed'])
        self.assertTrue(language_check('租金91200卢布，面积51.5㎡，位于16楼。','zh')['passed'])
        self.assertTrue(language_check('Аренда91200 рублей, площадь51.5 м², 16 этаж.','ru')['passed'])

    def test_equipment_negation_cannot_move_to_other_item(self):
        issues=polarity_issues('Нет холодильника. Есть диван.','有冰箱，没有沙发。','ru','zh',require_mentions=True)
        self.assertTrue(any('fridge' in issue for issue in issues))
        self.assertTrue(any('sofa' in issue for issue in issues))

    def test_unknown_equipment_cannot_become_present_or_absent(self):
        for output in ('配有冰箱。','没有冰箱。'):
            self.assertTrue(polarity_issues('Наличие холодильника не подтверждено.',output,'ru','zh'))
        self.assertEqual(polarity_issues('Наличие холодильника не подтверждено.','冰箱配置尚未确认。','ru','zh'),[])

    def test_comma_with_new_predicate_starts_a_new_assertion(self):
        self.assertEqual(claims('配有冰箱，但没有电视。','zh'),{'fridge':{'present'},'tv':{'absent'}})
        self.assertEqual(claims('Есть холодильник, но нет телевизора.','ru'),{'fridge':{'present'},'tv':{'absent'}})
        self.assertEqual(claims('Холодильник есть, а дивана нет.','ru'),{'fridge':{'present'},'sofa':{'absent'}})
        self.assertEqual(claims('Есть холодильник и нет телевизора.','ru'),{'fridge':{'present'},'tv':{'absent'}})

    def test_list_inherits_absence_without_another_predicate(self):
        self.assertEqual(claims('Нет холодильника, дивана и кровати.','ru'),
            {'fridge':{'absent'},'sofa':{'absent'},'bed':{'absent'}})

    def test_oven_cabinet_does_not_create_a_wardrobe_claim(self):
        self.assertEqual(claims('Есть духовой шкаф.','ru'),{'oven':{'present'}})
        self.assertEqual(claims('Есть кухонные шкафы.','ru'),{'cabinetry':{'present'}})

    def test_identical_numbers_with_swapped_units_fail(self):
        result=translation_checks('Аренда88300 рублей, площадь47.2 м².','租金47.2卢布，面积88300㎡。','ru-zh')
        self.assertTrue(result['numbersPreserved']);self.assertFalse(result['unitsPreserved'])

    def test_identical_money_values_cannot_swap_rent_and_deposit(self):
        source='Аренда68000 рублей, залог34000 рублей.'
        output='月租34000卢布，押金68000卢布。'
        result=translation_checks(source,output,'ru-zh')
        self.assertTrue(result['numbersPreserved']);self.assertTrue(result['unitsPreserved'])
        self.assertFalse(result['moneyRolesPreserved'])

    def test_numeric_spelling_is_normalized_but_not_trusted_as_semantics(self):
        result=translation_checks('Аренда80000 рублей в месяц.','月租8万卢布。','ru-zh')
        self.assertTrue(result['passed']);self.assertFalse(result['automaticSemanticVerification'])

    def test_payment_period_cannot_change_or_disappear(self):
        source='月租91200卢布。'
        self.assertFalse(translation_checks(source,'Аренда91200 рублей в сутки.','zh-ru')['paymentPeriodsPreserved'])
        self.assertFalse(translation_checks(source,'Аренда91200 рублей.','zh-ru')['paymentPeriodsPreserved'])

    def test_common_uncertainty_synonyms_and_russian_ordinal_units_are_supported(self):
        for output in ('空调未被确认存在。','尚未证实存在空调。'):
            self.assertEqual(translation_checks('Наличие кондиционера не подтверждено.',output,'ru-zh')['issues'],[])
        self.assertEqual(translation_checks('月租91200卢布，面积51.5平方米，位于16楼。',
            'Аренда91200 рублей в месяц, площадь51.5 квадратных метров, квартира на16-м этаже.','zh-ru')['issues'],[])

    def test_changed_transport_mode_is_visible(self):
        result=translation_checks('До метро11 минут пешком. Автобус2 идёт до парка.',
            '步行11分钟即可到达地铁。地铁2号车将在停车处等你。','ru-zh')
        self.assertFalse(result['passed']);self.assertTrue(any('bus' in issue for issue in result['issues']))

    def test_absent_bed_does_not_make_only_sofa_absent(self):
        self.assertEqual(claims('Нет двуспальных кроватей, только диван.','ru'),
            {'bed':{'absent'},'sofa':{'present'}})

    def test_property_draft_cannot_reverse_absence(self):
        baseline={'titles':['莫斯科'],'body':'40㎡，月租80000卢布，未配空调。'}
        result=local_content.validate_draft({'title':'莫斯科40㎡','body':'40㎡，月租80000卢布，配有空调。'},baseline)
        self.assertTrue(any('aircon' in issue for issue in result))

    def test_chinese_body_does_not_approve_an_english_or_russian_title(self):
        baseline={'titles':['莫斯科40㎡'],'body':'月租8万卢布，配有沙发。'}
        fields={'price':'80000','area':'40'}
        body='莫斯科公寓，40㎡，月租80000卢布，配有沙发。'
        for title in ('A lovely flat','Красивая квартира'):
            issues=local_content.validate_draft({'title':title,'body':body},baseline,fields)
            self.assertTrue(any('Заголовок' in issue for issue in issues))
        fields['metro']='Прокшино'
        self.assertEqual(local_content.validate_draft({'title':'Прокшино｜公寓','body':body},baseline,fields),[])

    def test_all_eighteen_authored_reference_answers_pass_partial_checks(self):
        spec=importlib.util.spec_from_file_location('quality_benchmark',ROOT/'scripts/benchmark-local-text-quality.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        cases=probes();self.assertEqual(len(cases),18)
        for row in cases:
            answer=json.dumps(row['referenceAnswer'],ensure_ascii=False) if row['task']=='listing' else row['referenceAnswer']
            with self.subTest(probe=row['id']):self.assertTrue(module.assess(row,answer)['passed'])

    def test_argos_unsupported_directions_do_not_inflate_measured_denominator(self):
        spec=importlib.util.spec_from_file_location('quality_benchmark_summary',ROOT/'scripts/benchmark-local-text-quality.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        value=module.summary([{'task':'ru-zh','status':'measured','seconds':1,'checks':{'passed':True}},
            {'task':'zh-ru','status':'unsupported'}])
        self.assertEqual(value['all']['applicable'],1);self.assertEqual(value['zh-ru']['applicable'],0)

    def test_larger_base_is_an_explicit_inference_candidate_not_a_compatible_adapter(self):
        config={'model_type':'qwen2','hidden_size':1536,'num_hidden_layers':28,
            'num_attention_heads':12,'num_key_value_heads':2}
        self.assertEqual(inference_model(config),'mlx-community/Qwen2.5-1.5B-Instruct-4bit')
        with self.assertRaises(ValueError):expected_parameters(config)
        config['hidden_size']=1024
        with self.assertRaises(ValueError):inference_model(config)


if __name__=='__main__':unittest.main()
