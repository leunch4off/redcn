import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'cli'))
from text_quality import (claims,deposit_returns,folding_sofa_claims,language_check,
    lease_terms,money_roles,parse_card,polarity_issues,translation_checks,utility_payers)
from text_quality_probes import probes
from text_model_contract import inference_model, expected_parameters, BASE_MODEL
import local_content


class TextQualityTests(unittest.TestCase):
    def test_tv_stand_is_not_evidence_of_a_television(self):
        self.assertEqual(claims('Есть ТВ-тумба.', 'ru'), {'tvStand': {'present'}})
        self.assertEqual(claims('Есть тумба под телевизор.', 'ru'), {'tvStand': {'present'}})
        for word in ('电视柜', '电视机柜', '电视架'):
            self.assertEqual(claims('配有'+word+'。', 'zh'), {'tvStand': {'present'}})
        self.assertEqual(claims('配有电视和电视柜。', 'zh'), {'tv': {'present'}, 'tvStand': {'present'}})
        self.assertTrue(polarity_issues('Есть ТВ-тумба.', '配有电视。', 'ru', 'zh', require_mentions=True))

    def test_isolated_rooms_do_not_create_a_bathtub(self):
        self.assertNotIn('bathtub', claims('Есть 2 изолированные комнаты.', 'ru'))
        self.assertIn('bathtub', claims('Есть ванна.', 'ru'))

    def test_registration_refusal_and_induction_hob_are_recognized(self):
        self.assertEqual(claims('Владелец не оформляет временную регистрацию.', 'ru'), {'registration': {'absent'}})
        self.assertEqual(claims('Есть индукционная плита.', 'ru'), {'hob': {'present'}})

    def test_negative_list_suffix_does_not_cross_a_positive_or_contrast(self):
        self.assertEqual(claims('Холодильника, обеденного стола и стульев нет в комплектации.', 'ru'),
            {'fridge': {'absent'}, 'table': {'absent'}, 'chairs': {'absent'}})
        self.assertEqual(claims('Холодильник есть, но стульев нет.', 'ru'),
            {'fridge': {'present'}, 'chairs': {'absent'}})

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

    def test_utility_payers_are_bound_to_each_expense_in_both_directions(self):
        pairs=[
            ('Отопление оплачивает владелец, воду — арендатор.', '供暖费由房东支付，水费由租客支付。'),
            ('За электричество платит наниматель, а за отопление — владелец.', '电费由房客支付，供暖费由业主承担。'),
            ('Вода оплачивается жильцом; интернет — собственником.', '水费由租客缴纳；网费由房东支付。'),
            ('Вода за счёт собственника. Интернет оплачивает жилец.', '水费由房东承担。网费由租客支付。'),
        ]
        for ru,zh in pairs:
            for source,output,direction in [(ru,zh,'ru-zh'),(zh,ru,'zh-ru')]:
                with self.subTest(source=source,output=output):
                    self.assertTrue(translation_checks(source,output,direction)['passed'])

    def test_payer_swap_omission_and_fluent_unrelated_words_do_not_pass(self):
        source='水费由租客支付，供暖费由房东支付。'
        for output in ('Воду оплачивает собственник, отопление — жилец.',
                       'Воду оплачивает жилец.',
                       'Кухня отвечает за воду, стол — за отопление.'):
            with self.subTest(output=output):
                check=translation_checks(source,output,'zh-ru')
                self.assertFalse(check['passed']);self.assertFalse(check['utilityPayersPreserved'])
        self.assertFalse(translation_checks('Воду оплачивает жилец, отопление — собственник.',
            '水费由租客支付，供暖费由租客承担。','ru-zh')['utilityPayersPreserved'])
        self.assertEqual(utility_payers('Арендатор осмотрел воду и отопление с собственником.','ru'),{})

    def test_payment_recipient_is_not_payer_and_explicit_exclusion_is_preserved(self):
        self.assertEqual(utility_payers('水费由租客支付给房东。','zh'),{'water':{'tenant'}})
        check=translation_checks('Жилец не оплачивает воду.','水费不由租客支付。','ru-zh')
        self.assertTrue(check['passed'])
        self.assertFalse(translation_checks('Жилец не оплачивает воду.','水费由租客支付。','ru-zh')['utilityPayersPreserved'])

    def test_deposit_refund_conditions_and_timing_survive_correct_translation(self):
        pairs=[
            ('Депозит вернут после освобождения квартиры при условии соблюдения договора.',
             '如果遵守合同，搬出后押金返还。'),
            ('Если договор не нарушен, после выезда возвращают залог.',
             '押金在退租后退回，前提是遵守合同。'),
            ('Депозит возвращается после выезда при отсутствии повреждений.',
             '无损坏的情况下，退房后退还押金。'),
            ('Залог невозвратный.', '押金不退。'),
        ]
        for ru,zh in pairs:
            for source,output,direction in [(ru,zh,'ru-zh'),(zh,ru,'zh-ru')]:
                with self.subTest(source=source,output=output):
                    self.assertTrue(translation_checks(source,output,direction)['passed'])

    def test_refund_cannot_lose_its_object_condition_or_time_order(self):
        source='Депозит вернут после выезда при соблюдении договора.'
        for output in ('回来以后再回来。', '退房后退还押金。', '遵守合同的情况下退还押金。',
                       '遵守合同的情况下，退房前退还押金。', '退还押金后，需要遵守合同。',
                       '没有损坏的情况下，退房后退还押金。'):
            with self.subTest(output=output):
                check=translation_checks(source,output,'ru-zh')
                self.assertFalse(check['passed']);self.assertFalse(check['depositReturnPreserved'])
        self.assertFalse(translation_checks('Депозит возвращается после выезда.',
            '如果遵守合同，退房后退还押金。','ru-zh')['depositReturnPreserved'])
        self.assertEqual(deposit_returns('Возврат ключей после выезда, депозит внесён.','ru'),set())

    def test_sofa_bed_and_folding_qualification_are_not_an_additional_bed(self):
        pairs=[
            ('В комнате раскладной диван.', '房间配有可展开作睡床的沙发。'),
            ('Есть диван-кровать.', '配有沙发床。'),
            ('Диван раскладывается.', '沙发可以展开。'),
            ('Диван не раскладывается.', '沙发不可折叠。'),
            ('Есть раскладной диван и отдельная кровать.', '配有可折叠沙发和单独的床。'),
        ]
        for ru,zh in pairs:
            for source,output,direction in [(ru,zh,'ru-zh'),(zh,ru,'zh-ru')]:
                with self.subTest(source=source,output=output):
                    self.assertTrue(translation_checks(source,output,direction)['passed'])
        self.assertNotIn('bed',claims('房间配有可展开作睡床的沙发。','zh'))
        self.assertIn('bed',claims('配有沙发床和一张床。','zh'))
        self.assertEqual(folding_sofa_claims('Есть раскладной стол и обычный диван.','ru'),set())

    def test_folding_cannot_disappear_be_added_or_move_to_another_object(self):
        for output in ('有沙发。','有布艺沙发。','有折叠桌和沙发。'):
            with self.subTest(output=output):
                check=translation_checks('Есть раскладной диван.',output,'ru-zh')
                self.assertFalse(check['passed']);self.assertFalse(check['sofaFoldingPreserved'])
        self.assertFalse(translation_checks('Есть диван.','配有沙发床。','ru-zh')['sofaFoldingPreserved'])
        self.assertFalse(translation_checks('Диван не раскладывается.','沙发可以展开。','ru-zh')['sofaFoldingPreserved'])

    def test_minimum_lease_is_not_a_rent_amount(self):
        pairs=[
            ('Минимальный срок аренды — 9 месяцев.', '最短租期为9个月。'),
            ('Срок аренды не менее 8 месяцев.', '租期至少8个月。'),
            ('Максимальный срок аренды — 2 года.', '最长租期为2年。'),
            ('Срок аренды — 6 месяцев; аренда — 54000 рублей, залог — 27000 рублей.',
             '租期为6个月；租金54000卢布，押金27000卢布。'),
        ]
        for ru,zh in pairs:
            for source,output,direction in [(ru,zh,'ru-zh'),(zh,ru,'zh-ru')]:
                with self.subTest(source=source,output=output):
                    check=translation_checks(source,output,direction)
                    self.assertTrue(check['moneyRolesPreserved']);self.assertTrue(check['passed'])
        self.assertEqual(money_roles('Минимальный срок аренды — 9 месяцев.'),{})

    def test_minimum_or_maximum_lease_bound_cannot_turn_into_fixed_term(self):
        source='Минимальный срок аренды — 9 месяцев.'
        for output in ('租期为9个月。','最长租期为9个月。','最短租期为9年。'):
            with self.subTest(output=output):
                check=translation_checks(source,output,'ru-zh')
                self.assertFalse(check['passed']);self.assertFalse(check['leaseTermPreserved'])
        self.assertEqual(lease_terms('Залог вносится за 2 месяца.','ru'),{})
        self.assertTrue(translation_checks('Залог вносится за 2 месяца.','押金为2个月。','ru-zh')['moneyRolesPreserved'])

    def test_common_uncertainty_synonyms_and_russian_ordinal_units_are_supported(self):
        for output in ('空调未被确认存在。','尚未证实存在空调。'):
            self.assertEqual(translation_checks('Наличие кондиционера не подтверждено.',output,'ru-zh')['issues'],[])
        self.assertEqual(translation_checks('月租91200卢布，面积51.5平方米，位于16楼。',
            'Аренда91200 рублей в месяц, площадь51.5 квадратных метров, квартира на16-м этаже.','zh-ru')['issues'],[])

    def test_confirmation_adverbs_do_not_turn_unknown_equipment_into_presence(self):
        for zh in ('电视配置需进一步核实。','电视是否配备需要再次确认。','电视配置需另行确认。'):
            ru='Наличие телевизора нужно уточнить.'
            with self.subTest(zh=zh):
                self.assertTrue(translation_checks(ru,zh,'ru-zh')['passed'])
                self.assertTrue(translation_checks(zh,ru,'zh-ru')['passed'])
                self.assertFalse(translation_checks(ru,'配有电视。','ru-zh')['passed'])

    def test_equipment_exception_reverses_only_the_excluded_item(self):
        for ru,zh in [('Нет мебели, кроме шкафа.','除衣柜外，没有其他家具。'),
                      ('Есть всё, кроме холодильника.','除了冰箱之外，其他都有。')]:
            with self.subTest(ru=ru):
                self.assertTrue(translation_checks(ru,zh,'ru-zh')['passed'])
                self.assertTrue(translation_checks(zh,ru,'zh-ru')['passed'])
        self.assertEqual(claims('Кроме шкафа, нет холодильника.','ru').get('fridge'),{'absent'})
        self.assertTrue(polarity_issues('Нет мебели, кроме шкафа.','没有衣柜。','ru','zh',require_mentions=True))

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
