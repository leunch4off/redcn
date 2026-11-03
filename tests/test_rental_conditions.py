from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'cli'))
from rental_conditions import fee_claims, fee_issues, metro_proximity_issues


class RentalFeeConditionsTests(unittest.TestCase):
    def test_real_failed_model_answer_cannot_reverse_separate_fees(self):
        source = '月租80000卢布，押金80000卢布。水电费另付。'
        output = '水电费另付。租金80000卢布/月，包括水电费。'
        issues = fee_issues(source, output)
        self.assertIn('Изменено условие оплаты: water', issues)
        self.assertIn('Изменено условие оплаты: electricity', issues)

    def test_different_fee_categories_do_not_inherit_each_others_policy(self):
        text = '物业费包含，水电费另付，网费已包含。'
        self.assertEqual(fee_claims(text, 'zh'), {
            'building': {'included'}, 'water': {'separate'},
            'electricity': {'separate'}, 'internet': {'included'}})
        self.assertEqual(fee_issues(text, '网费已包含。水电费另付，物业费包含。'), [])

    def test_generic_included_utilities_do_not_make_water_free(self):
        self.assertEqual(fee_issues('公用事业费已包含，水费按表计费。',
                                   '公用事业费已包含，水费按表计费。'), [])
        self.assertTrue(fee_issues('公用事业费已包含，水费按表计费。', '水费已包含。'))

    def test_russian_chinese_translation_preserves_specific_fee_policies(self):
        source = 'Вода и электричество оплачиваются отдельно. Интернет включён.'
        target = '水电费另付。网费已包含。'
        self.assertEqual(fee_issues(source, target, 'ru', 'zh', require_mentions=True), [])
        self.assertEqual(fee_issues(target, source, 'zh', 'ru', require_mentions=True), [])
        self.assertTrue(fee_issues(source, '水电费已包含。网费另付。', 'ru', 'zh'))

    def test_absence_of_a_fee_claim_is_not_a_new_free_utility_promise(self):
        self.assertTrue(fee_issues('冰箱已配。', '水电费已包含，冰箱已配。'))
        self.assertEqual(fee_issues('水费另付。冰箱已配。', '冰箱已配。'), [])
        self.assertTrue(fee_issues('水费另付。冰箱已配。', '冰箱已配。', require_mentions=True))

    def test_uncertain_fee_cannot_be_turned_into_a_confirmed_price_policy(self):
        self.assertTrue(fee_issues('网费需确认。', '网费已包含。'))
        self.assertEqual(fee_issues('网费需确认。', '网费尚未确认。'), [])

    def test_no_extra_meter_charge_is_not_parsed_as_an_extra_charge(self):
        source = '公用事业费及网络已包含，无需另付表计费用。'
        self.assertEqual(fee_claims(source, 'zh'), {
            'utilities': {'included'}, 'internet': {'included'}, 'meters': {'included'}})
        self.assertTrue(fee_issues(source, '表计费用另付。'))
        self.assertEqual(fee_issues('Коммунальные услуги и интернет включены; дополнительных платежей по счётчикам нет.',
                                   source, 'ru', 'zh', require_mentions=True), [])

    def test_preview_routes_reject_fee_reversal_without_rewriting_the_candidate(self):
        from local_content import validate_draft
        from text_quality import translation_checks
        draft = {'title': '月租8万卢布', 'body': '包括水电费。月租80000卢布。'}
        issues = validate_draft(draft, {'body': '月租80000卢布。水电费另付。'}, {'price': 80000})
        self.assertIn('Изменено условие оплаты: water', issues)
        self.assertEqual(draft['body'], '包括水电费。月租80000卢布。')
        result = translation_checks('Вода оплачивается отдельно.', '水费已包含。', 'ru-zh')
        self.assertFalse(result['passed'])
        self.assertIn('Изменено условие оплаты: water', result['issues'])

    def test_uncertainty_synonym_false_flag_found_in_native_audit_is_fixed(self):
        from text_quality import translation_checks
        result = translation_checks('Маршрут не подтверждён.', '路线未被证实。', 'ru-zh')
        self.assertTrue(result['conditionalPreserved'])

    def test_literal_station_mention_cannot_become_near_or_at_the_door(self):
        source = '原文提及Рассказовка和Переделкино站。'
        fields = {'metro': 'Рассказовка'}
        for candidate in ('近地铁，适合日常通勤。', 'Рассказовка和Переделкино站附近。', '近拉斯卡佐夫卡站。', '近拉斯卡佐夫卡地铁站。', '出门就是地铁。', '地铁口公寓。'):
            with self.subTest(candidate=candidate):
                self.assertTrue(metro_proximity_issues(source, candidate, fields))
        self.assertEqual(metro_proximity_issues(source, source, fields), [])

    def test_confirmed_walking_control_supports_near_but_not_metro_entrance(self):
        source = '原文提及Рассказовка站。'
        self.assertEqual(metro_proximity_issues(source, '近地铁。', {'metro': 'Рассказовка', 'walk': '8'}), [])
        self.assertTrue(metro_proximity_issues(source, '地铁口。', {'walk': '8'}))
        for walk in ('', '0', '8 на автобусе', None):
            self.assertTrue(metro_proximity_issues(source, '近地铁。', {'walk': walk}))
        self.assertTrue(metro_proximity_issues(source, '近地铁。', {'walk': '8', 'sourceAudit': {'blockedFields': ['walk']}}))

    def test_other_nearby_amenities_are_not_blocked_by_the_metro_guard(self):
        self.assertEqual(metro_proximity_issues('附近有公园和商店。', '附近有公园和商店。'), [])
        self.assertEqual(metro_proximity_issues('Рядом с метро.', '近地铁。', source_language='ru'), [])
        self.assertEqual(metro_proximity_issues('步行8分钟到地铁站。', '近地铁。'), [])


if __name__ == '__main__':
    unittest.main()
