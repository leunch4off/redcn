"""Live free-prose contract, separate from frozen advertising corpora."""
import copy
import json
import re
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'cli'))
import individual_copy as individual
import language_service
import local_server
from learning_memory import LearningMemory
from local_text_model import clean_request


FIELDS = {'city': 'Москва', 'price': '85000', 'deposit': '35000', 'area': '42',
    'floor': '2', 'floors': '12', 'layout': 'one', 'features': ['chairs', 'fridge', 'oven']}


def complete(brief):
    facts = brief['facts']
    return {'title': '莫斯科'+brief['layout']+'出租',
        'body': '\n'.join(facts['核心参数'])+'\n'+facts['其他核实资料']+'\n'+facts['已知费用与限制']+'\n'
            +'核实的室内物品包括：'+'、'.join(facts['核实物品清单'])+'。'}


def model_text(draft):
    return '标题：'+draft['title']+'\n正文：\n'+draft['body']


class IndividualContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.brief = individual.build_brief(FIELDS)

    def test_unknown_fields_gain_explicit_source_restrictions_without_losing_the_adult_scope(self):
        fields = {**FIELDS, 'deposit': '', 'utilities': 'unknown', 'petPolicy': 'unknown',
            'sourceDescription': 'К проживанию рассматриваются не более двух взрослых человек. С животными к сожалению не рассматриваем.\nОплата ЖКХ\nвключена (без счётчиков)\nЗалог\nнет'}
        brief = individual.build_brief(fields)
        self.assertEqual(brief['fields']['petPolicy'], 'no')
        self.assertEqual(brief['fields']['utilities'], 'meters')
        self.assertEqual(brief['fields']['deposit'], '0')
        self.assertNotIn('childrenPolicy', brief['fields'])
        self.assertIn('最多2名成人入住。', brief['facts']['已知费用与限制'])
        draft = complete(brief)
        self.assertEqual(individual.validate_individual(draft, brief), [])
        draft['body'] = draft['body'].replace('最多2名成人入住。', '最多2人入住。')
        self.assertTrue(any('числа жильцов' in issue for issue in individual.validate_individual(draft, brief)))

    def test_full_valid_body_and_optional_secondary_detail_can_be_retained(self):
        self.assertEqual(individual.validate_individual(complete(self.brief), self.brief), [])
        brief = individual.build_brief({**FIELDS, 'kitchen': '11', 'living': '21'})
        draft = complete(brief)
        draft['body'] = '\n'.join(line for line in draft['body'].splitlines() if '11㎡' not in line and '21㎡' not in line)
        self.assertEqual(individual.validate_individual(draft, brief), [])
        draft['body'] += '\n厨房21㎡。'
        self.assertTrue(any('площадь помещения' in issue for issue in individual.validate_individual(draft, brief)))

    def test_missing_city_is_not_filled_by_the_legacy_moscow_default(self):
        fields = {key: value for key, value in FIELDS.items() if key != 'city'}
        brief = individual.build_brief(fields)
        self.assertFalse(any(line.startswith('城市：') for line in brief['facts']['核心参数']))
        self.assertNotIn('莫斯科', brief['baseline']['body'])
        self.assertNotIn('城市：莫斯科', individual.generation_request(brief)['messages'][1]['content'])
        draft = complete(brief)
        draft['title'] = draft['title'].replace('莫斯科', '')
        self.assertEqual(individual.validate_individual(draft, brief), [])
        self.assertTrue(any('Город Москва' in issue for issue in individual.validate_individual(
            {**draft, 'body': '莫斯科。'+draft['body']}, brief)))

    def test_material_source_facts_and_no_daily_rent_survive_the_writer_prompt(self):
        source = 'Комнаты смежные. Дом включен в программу реновации и будет снесен. Субаренда, пересдача в том числе посуточно исключена.'
        brief = individual.build_brief({**FIELDS, 'sourceDescription': source})
        context = individual.generation_request(brief)['messages'][1]['content']
        for clause in ('房间相连。', '该楼已纳入城市更新计划，将被拆除。', '不接受按日短租'):
            self.assertIn(clause, context)
        good = complete(brief)
        self.assertEqual(individual.validate_individual(good, brief), [])
        for clause, fragment in [('房间相连。', 'смежные'), ('该楼已纳入城市更新计划，将被拆除。', 'сносе')]:
            bad = {**good, 'body': good['body'].replace(clause, '')}
            self.assertTrue(any(fragment in issue for issue in individual.validate_individual(bad, brief)))
        with self.assertRaisesRegex(ValueError, 'смежные'):
            individual.build_brief({**FIELDS, 'separate': True, 'sourceDescription': source})

    def test_long_full_inventory_is_not_rejected_by_legacy_280_limit(self):
        features = ['bedAny', 'mattress', 'wardrobe', 'nightstands', 'desk', 'dresser', 'mirror',
            'curtains', 'sofa', 'armchair', 'coffeeTable', 'cabinet', 'tvStand', 'tv', 'computer',
            'monitor', 'speakers', 'router', 'projector', 'piano', 'ac', 'fridge', 'stove', 'oven',
            'hood', 'sink', 'microwave', 'dishwasher', 'kettle', 'table', 'chairs', 'washer',
            'bath', 'shower', 'washbasin', 'closet', 'pantry', 'shelves', 'iron', 'vacuum']
        brief = individual.build_brief({**FIELDS, 'features': features})
        draft = complete(brief)
        draft['body'] += '\n关于这套房子，可以先从基本参数了解，再分别核对各项配置。刊登资料给出了月租、押金、面积与楼层，以下按这些信息说明。房源的价格和押金分别列出，便于在考虑租住安排时核对。面积是整套住房的总面积；楼层标示同时列出所在层与楼栋总层数。选择时可以对照自己的预算与所需物品逐项看，不需要把未知的条件当成已经确定的承诺。'
        self.assertGreater(len(re.findall(r'[\u3400-\u9fff]', draft['body'])), 280)
        self.assertEqual(len(brief['inventory']), len(features))
        self.assertEqual(individual.validate_individual(draft, brief), [])
        draft['body'] = draft['body'].replace('洗碗机', '')
        self.assertTrue(any('Пропущены подтверждённые предметы' in issue for issue in individual.validate_individual(draft, brief)))

    def test_proposals_rejected_features_and_private_evidence_do_not_enter_prompt(self):
        brief = individual.build_brief({**FIELDS, 'url': 'https://example.test/private-property',
            'editorIdentity': 'private-operator-identity', 'photoRejectedFeatures': ['fridge'],
            'photoEvidence': [{'id': 'dishwasher', 'status': 'proposal', 'sourceUrl': 'https://example.test/private.jpg',
                'evidence': 'private observation note'}],
            'sourceDescription': 'В квартире есть стол. https://example.test/contact'} )
        self.assertEqual({row['id'] for row in brief['inventory']}, {'chairs', 'oven'})
        request = clean_request(individual.generation_request(brief))
        text = json.dumps(request, ensure_ascii=False)
        for secret in ('example.test', 'private-operator-identity', 'private observation note', 'sourceUrl', 'photoEvidence'):
            self.assertNotIn(secret, text)
        rewritten = individual.generation_request(brief,
            previous_title='旧模板承诺免费服务', previous_body='旧模板臆造地暖与全套设备。')
        self.assertNotIn('旧模板', json.dumps(rewritten, ensure_ascii=False))

    def test_category_counts_cannot_borrow_floor_number_or_drop_minimum(self):
        unknown = complete(self.brief); unknown['body'] += '配有2把餐椅。'
        self.assertTrue(any('не подтверждено' in issue for issue in individual.validate_individual(unknown, self.brief)))
        counted = individual.build_brief({**FIELDS, 'featureCounts': {'chairs': '2'},
            'sourceAudit': {'furniture': [{'id': 'chairs', 'status': 'present', 'count': '2', 'countKind': 'minimum'}]}})
        draft = complete(counted)
        self.assertEqual(individual.validate_individual(draft, counted), [])
        draft['body'] = draft['body'].replace('至少2件', '2件')
        self.assertTrue(any('нижняя граница' in issue for issue in individual.validate_individual(draft, counted)))
        unknown['body'] = unknown['body'].replace('2把', '两把')
        self.assertTrue(any('не подтверждено' in issue for issue in individual.validate_individual(unknown, self.brief)))

    def test_bedside_table_is_not_bed_and_tv_stand_is_not_tv(self):
        brief = individual.build_brief({**FIELDS, 'features': ['nightstands', 'tvStand', 'chairs']})
        self.assertEqual(individual.validate_individual(complete(brief), brief), [])
        draft = complete(brief); draft['body'] += '配有电视和双人床。'
        self.assertTrue(any('Не подтвержден' in issue for issue in individual.validate_individual(draft, brief)))

    def test_fee_and_restriction_must_be_retained_not_invented(self):
        brief = individual.build_brief({**FIELDS, 'petPolicy': 'no', 'utilities': 'meters', 'prepayMonths': '1'})
        draft = complete(brief)
        self.assertEqual(individual.validate_individual(draft, brief), [])
        for forbidden in ('不接受宠物。', '物业费已包含，仅水电按表计费。', '预付：1个月'):
            changed = {**draft, 'body': draft['body'].replace(forbidden, '')}
            self.assertTrue(individual.validate_individual(changed, brief), forbidden)
        changed = {**draft, 'body': draft['body']+'可提供机场接送服务。'}
        self.assertTrue(any('трансфера' in issue for issue in individual.validate_individual(changed, brief)))

    def test_natural_rent_and_floor_prose_preserves_roles_without_fixed_template(self):
        brief = individual.build_brief({**FIELDS, 'layout': 'euro2'})
        # Independently written Chinese, not the baseline renderer's format.
        template = ('这套位于莫斯科的一室一厅面积42㎡，%s。%s，押金35000₽。'
                    '室内已确认的配置包括餐椅、冰箱、烤箱，其他租住条件可进一步确认。')
        for floor in ('在第2层，楼栋共12层', '位于2层/12层', '楼层：2/共12层', '第2楼，总高12层'):
            for rent in ('租金每月85000₽', '月租金：85,000卢布', '每月租金为8.5万卢布'):
                with self.subTest(floor=floor, rent=rent):
                    draft = {'title': '莫斯科42㎡一室一厅出租', 'body': template % (floor, rent)}
                    self.assertEqual(individual.validate_individual(draft, brief), [])

    def test_existing_number_cannot_change_role_or_hide_conflicting_rent_and_floor(self):
        brief = individual.build_brief({**FIELDS, 'layout': 'euro2'})
        body = ('这套位于莫斯科的一室一厅面积42㎡，位于2层/12层。月租金85000₽，押金35000₽。'
                '室内已确认的配置包括餐椅、冰箱、烤箱，其他租住条件可进一步确认。')
        for changed, reason in (
                (body.replace('2层/12层', '12层/2层'), 'этаж'),
                (body.replace('2层/12层', '2层/2层'), 'этаж'),
                (body.replace('位于2层/12层', '12层楼栋，房间2间'), 'этаж'),
                (body+'另有说明：楼层12/2层。', 'этаж'),
                (body+'租金每月35000₽。', 'месячная аренда'),
                (body+'押金85000₽。', 'сумма залога'),
                (body.replace('月租金85000₽，押金35000₽', '月租金35000₽，押金85000₽'), 'месячная аренда')):
            with self.subTest(reason=reason, body=changed):
                issues = individual.validate_individual({'title': '莫斯科42㎡一室一厅出租', 'body': changed}, brief)
                self.assertTrue(any(reason in issue for issue in issues), issues)
        free_brief = individual.build_brief({**FIELDS, 'layout': 'euro2', 'deposit': '0'})
        contradictory = body.replace('押金35000₽', '无需押金，押金85000₽')
        self.assertTrue(any('отсутствие залога' in issue for issue in individual.validate_individual(
            {'title': '莫斯科42㎡一室一厅出租', 'body': contradictory}, free_brief)))

    def test_payment_idiom_must_match_cash_deposit_and_independent_prepayment(self):
        brief = individual.build_brief({**FIELDS, 'deposit': '85000', 'prepayMonths': '1',
            'leaseTerm': 'long', 'minMonths': '12'})
        draft = complete(brief)
        draft['body'] = draft['body'].replace('最低租期', '租期最低')
        self.assertEqual(individual.validate_individual(draft, brief), [])
        for correct in ('押一付一。', '押1付1。'):
            self.assertEqual(individual.validate_individual({**draft, 'body': draft['body']+correct}, brief), [])
        for wrong, reason in (('押一付三。', 'предоплаты'), ('押二付一。', 'залога')):
            issues = individual.validate_individual({**draft, 'body': draft['body']+wrong}, brief)
            self.assertTrue(any(reason in issue for issue in issues), issues)
        # Real failed-output pattern: the right cash amount and explicit prepay
        # later in the paragraph do not cancel a contradictory Chinese idiom.
        unequal = individual.build_brief({**FIELDS, 'price': '73000', 'deposit': '75000', 'prepayMonths': '1'})
        contradicted = complete(unequal); contradicted['body'] += '押一付三。'
        issues = individual.validate_individual(contradicted, unequal)
        self.assertTrue(any('залога' in issue for issue in issues), issues)
        self.assertTrue(any('предоплаты' in issue for issue in issues), issues)
        unknown = individual.build_brief({**FIELDS, 'deposit': '85000'})
        unsupported = complete(unknown); unsupported['body'] += '押一付一。'
        self.assertTrue(any('предоплаты' in issue for issue in individual.validate_individual(unsupported, unknown)))

    def test_route_numbers_remain_attached_to_their_station_and_transport_mode(self):
        brief = individual.build_brief({**FIELDS, 'metro': 'Новаторская', 'walk': '16',
            'transit': 'Новаторская | 16 | walk\nЮго-Западная | 10 | bus'})
        draft = complete(brief)
        self.assertIn('革新家', brief['baseline']['body'])
        self.assertIn('西南', brief['baseline']['body'])
        for valid in ('步行16分钟达革新家，公交10分钟到西南。',
                      '革新家站步行约16分钟，西南站公共交通约10分钟。',
                      '革新家｜步行约16分钟\n西南｜公共交通约10分钟',
                      '西南｜公共交通约10分钟  \r\n革新家｜步行约16分钟'):
            self.assertEqual(individual.validate_individual({**draft, 'body': draft['body']+valid}, brief), [])
        for wrong in ('步行10分钟到革新家。', '革新家站步行约10分钟。',
                      '公交16分钟到革新家。', '步行16分钟到西南。'):
            with self.subTest(wrong=wrong):
                issues = individual.validate_individual({**draft, 'body': draft['body']+wrong}, brief)
                self.assertTrue(any('маршрута до станции' in issue for issue in issues), issues)

    def test_raw_source_restrictions_are_not_lost_when_old_fields_omit_them(self):
        brief = individual.build_brief({**FIELDS, 'sourceDescription': 'Без животных. Регистрацию не предоставляем. Предоплата 2 месяца.'})
        self.assertEqual(brief['fields']['petPolicy'], 'no')
        self.assertEqual(brief['fields']['prepayMonths'], '2')
        self.assertIn('宠物', brief['facts']['已知费用与限制'])
        # Source parser's documented patterns are used, not broad semantic guesses.
        draft = complete(brief)
        self.assertEqual(individual.validate_individual(draft, brief), [])

    def test_explicit_source_smoking_and_occupancy_limits_survive_the_whole_brief(self):
        brief = individual.build_brief({**FIELDS, 'sourceDescription':
            'Курение в квартире категорически запрещено. Рассмотрим к проживанию 1–2х человек.'})
        self.assertEqual(brief['fields']['smokingPolicy'], 'no')
        self.assertEqual(brief['sourceRestrictions'], [
            {'kind': 'occupancy-range', 'minimum': 1, 'maximum': 2, 'text': '入住人数：1–2人。'}])
        draft = complete(brief)
        self.assertEqual(individual.validate_individual(draft, brief), [])
        missing = {**draft, 'body': draft['body'].replace('入住人数：1–2人。', '')}
        self.assertTrue(any('числа жильцов' in issue for issue in individual.validate_individual(missing, brief)))
        wrong = {**draft, 'body': draft['body'].replace('入住人数：1–2人。', '入住人数：1–3人。')}
        self.assertTrue(individual.validate_individual(wrong, brief))

    def test_conflicting_source_utility_costs_do_not_become_an_inclusive_advertisement(self):
        with self.assertRaisesRegex(ValueError, 'коммунальн'):
            individual.build_brief({**FIELDS, 'utilities': 'meters', 'sourceDescription':
                'Дополнительно коммунальные платежи по квитанции (7-9т.р в месяц) + интернет.'})

    def test_internet_payment_is_a_required_cost_not_invented_equipment(self):
        brief = individual.build_brief({**FIELDS, 'utilities': 'meters', 'sourceDescription':
            'Оплата за месяц + счётчики и интернет.'})
        self.assertIn({'kind': 'internet-extra', 'text': '网络费用另付。'}, brief['sourceRestrictions'])
        draft = complete(brief)
        self.assertEqual(individual.validate_individual(draft, brief), [])
        missing = {**draft, 'body': draft['body'].replace('网络费用另付。', '')}
        self.assertTrue(any('оплата интернета' in issue for issue in individual.validate_individual(missing, brief)))
        contradictory = {**draft, 'body': draft['body']+'网络费用已包含。'}
        self.assertTrue(any('ошибочно названа' in issue for issue in individual.validate_individual(contradictory, brief)))

    def test_including_a_connection_in_an_equipment_list_does_not_include_its_bill(self):
        from rental_conditions import fee_claims
        self.assertEqual(fee_claims('家电与其他配置包括空调、冰箱、洗手盆和网络。', 'zh'), {})
        self.assertEqual(fee_claims('已有配置包含网络。', 'zh'), {})
        for clause in ('租金包括网络。', '费用包含网络。', '网络费用已包含。', '租金包含配置与网络。'):
            self.assertEqual(fee_claims(clause, 'zh').get('internet'), {'included'}, clause)
        brief = individual.build_brief({**FIELDS, 'features': [*FIELDS['features'], 'internet']})
        draft = complete(brief)
        draft['body'] += '\n家电与其他配置包括冰箱、烤箱和网络。'
        self.assertEqual(individual.validate_individual(draft, brief), [])
        draft['body'] += '\n网络费用已包含。'
        self.assertTrue(any('условие оплаты' in issue for issue in individual.validate_individual(draft, brief)))

    def test_negated_source_quality_is_not_permission_for_advertising_claim(self):
        for source, claim in [('Это не дизайнерский ремонт.', '房屋是精装修。'),
                              ('Квартира не впервые сдаётся. Ранее сдавалась.', '首次出租。')]:
            brief = individual.build_brief({**FIELDS, 'sourceDescription': source})
            draft = complete(brief); draft['body'] += claim
            self.assertTrue(any('Не подтверждено рекламное утверждение' in issue for issue in individual.validate_individual(draft, brief)))

    def test_short_source_renovation_enum_is_resolved_before_chinese_writing(self):
        brief = individual.build_brief({**FIELDS, 'renovation': 'Дизайнерский'})
        context = individual.generation_request(brief)['messages'][1]['content']
        self.assertIn('装修：设计感装修', context)
        self.assertNotIn('Дизайнерский', context)
        draft = complete(brief)
        self.assertTrue(any('по-китайски' in issue for issue in individual.validate_individual(draft, brief)))
        draft['body'] = draft['body'].replace('Дизайнерский', '设计感装修')
        self.assertEqual(individual.validate_individual(draft, brief), [])
        damaged = individual.build_brief({**FIELDS, 'renovation': 'Ди\ufffd\ufffdайнерский'})
        context = individual.generation_request(damaged)['messages'][1]['content']
        self.assertNotIn('айнерский', context)
        self.assertNotIn('设计感装修', context, 'Lost source bytes do not establish a repair category')

    def test_inventory_does_not_prove_blanket_photo_provenance_or_exhaustiveness(self):
        draft = complete(self.brief)
        for claim, reason in (
                ('所有物品均可见照片，具体状态以实拍为准。', 'подтверждены фотографиями'),
                ('餐椅、冰箱、烤箱为实际配置，照片可见。', 'неподтверждённое фото'),
                ('冰箱（照片可见）。', 'неподтверждённое фото'),
                ('家具仅包含：餐椅、冰箱、烤箱。', 'отсутствие остальной'),
                ('只有餐椅、冰箱、烤箱。', 'отсутствие остальной')):
            issues = individual.validate_individual({**draft, 'body': draft['body']+claim}, self.brief)
            self.assertTrue(any(reason in issue for issue in issues), issues)
        scoped = {**draft, 'body': draft['body']+'已确认的物品仅包括餐椅、冰箱、烤箱。'}
        self.assertEqual(individual.validate_individual(scoped, self.brief), [])
        # Upstream evidence validation owns photoVisible; this verifies that the
        # guard distinguishes a genuinely all-visual list from a mixed one.
        all_visual = copy.deepcopy(self.brief)
        for row in all_visual['inventory']:
            row['photoVisible'] = True
        visual = {**draft, 'body': draft['body']+'上述物品均可在照片中看到。'}
        self.assertEqual(individual.validate_individual(visual, all_visual), [])
        all_visual['inventory'][0]['photoVisible'] = False
        self.assertTrue(any('подтверждены фотографиями' in issue for issue in individual.validate_individual(visual, all_visual)))
        mixed = copy.deepcopy(self.brief)
        next(row for row in mixed['inventory'] if row['id'] == 'chairs')['photoVisible'] = True
        scoped_photo = {**draft, 'body': draft['body']+'物品包括餐椅（照片可见）、冰箱、烤箱。'}
        self.assertEqual(individual.validate_individual(scoped_photo, mixed), [])

    def test_inventory_category_is_distinct_from_an_unverified_room_assignment(self):
        draft = complete(self.brief)
        self.assertEqual(individual.validate_individual(
            {**draft, 'body': draft['body']+'厨房电器：冰箱、烤箱。'}, self.brief), [])
        for claim in ('厨房配备冰箱与烤箱。', '客厅有餐椅。', '卧室里放有冰箱。'):
            issues = individual.validate_individual({**draft, 'body': draft['body']+claim}, self.brief)
            self.assertTrue(any('расположение предметов' in issue for issue in issues), issues)
        with_kitchen = individual.build_brief({**FIELDS, 'kitchen': '11'})
        draft = complete(with_kitchen)
        misplaced = {**draft, 'body': draft['body']+'厨房面积11㎡，配备餐椅、冰箱、烤箱。'}
        self.assertTrue(any('расположение предметов' in issue for issue in individual.validate_individual(misplaced, with_kitchen)))
        apartment_scope = {**draft, 'body': draft['body']+'厨房面积11㎡。室内配备餐椅、冰箱、烤箱。'}
        self.assertEqual(individual.validate_individual(apartment_scope, with_kitchen), [])

    def test_full_furnishing_in_a_title_is_not_proved_by_an_item_inventory(self):
        draft = complete(self.brief)
        for title in ('莫斯科公寓，带全配家具家电', '莫斯科公寓，家具家电全配',
                      '莫斯科公寓，含完整配置', '莫斯科公寓，配置完整', '莫斯科公寓，全套设备'):
            with self.subTest(title=title):
                issues = individual.validate_individual({**draft, 'title': title}, self.brief)
                self.assertTrue(any('полную комплектацию' in issue for issue in issues), issues)
        scoped = {**draft, 'body': draft['body']+'物品清单不代表全配家具家电。'}
        self.assertEqual(individual.validate_individual(scoped, self.brief), [])

    def test_room_label_and_bathroom_introduction_cannot_locate_unassigned_items(self):
        features = FIELDS['features']+['bath', 'washbasin', 'shelves', 'dryingRack']
        brief = individual.build_brief({**FIELDS, 'features': features, 'bathroom': '1 раздельный'})
        draft = complete(brief)
        self.assertEqual(individual.validate_individual(draft, brief), [])
        for claim in ('浴室：浴缸、洗手盆、置物架、晾衣架。',
                      '卫浴：1处，厕所与浴室分开，含浴缸、洗手盆、置物架、晾衣架。',
                      '配备独立卫浴，含浴缸、洗手盆、置物架、晾衣架，厕所与浴室分开。'):
            with self.subTest(claim=claim):
                issues = individual.validate_individual({**draft, 'body': draft['body']+claim}, brief)
                self.assertTrue(any('расположение предметов' in issue for issue in issues), issues)
        apartment_scope = {**draft, 'body': draft['body']+
            '卫浴：1处，厕所与浴室分开，室内配置：浴缸、洗手盆、置物架、晾衣架。'}
        self.assertEqual(individual.validate_individual(apartment_scope, brief), [])
        combined = individual.build_brief({**FIELDS, 'features': features, 'bathroom': '1 совмещенный'})
        combined_draft = complete(combined)
        combined_draft['body'] += '卫生间与浴室合一。厨房电器：冰箱、烤箱。'
        self.assertEqual(individual.validate_individual(combined_draft, combined), [])

    def test_light_quality_synonyms_need_affirmative_source_evidence(self):
        for source in ('Окна выходят во двор.', 'Квартира не светлая.', '采光不足。'):
            brief = individual.build_brief({**FIELDS, 'sourceDescription': source})
            for claim in ('采光良好。', '明亮通透。'):
                draft = complete(brief); draft['body'] += claim
                issues = individual.validate_individual(draft, brief)
                self.assertTrue(any('Не подтверждено рекламное утверждение' in issue for issue in issues), issues)
        affirmed = individual.build_brief({**FIELDS, 'sourceDescription': 'Светлая квартира.'})
        draft = complete(affirmed); draft['body'] += '采光良好。'
        self.assertEqual(individual.validate_individual(draft, affirmed), [])

    def test_plain_protocol_and_legacy_strict_json_parse_without_repair(self):
        draft = complete(self.brief)
        self.assertEqual(individual.parse_individual_text(model_text(draft)), draft)
        self.assertEqual(individual.parse_individual_text('标题：'+draft['title']+'\n\n'+draft['body']), draft)
        self.assertEqual(individual.parse_individual_text(json.dumps(draft, ensure_ascii=False)), draft)
        for text in ('正文：只有正文', '标题：只有标题', '标题：空正文\n正文：',
                     model_text(draft)+'\n标题：重复', '{"title":"a","body":"x","body":"y"}', '{"title":"a","body":"'):
            with self.assertRaises(ValueError): individual.parse_individual_text(text)
        with_contact = individual.parse_individual_text(model_text({**draft, 'body': draft['body']+' https://example.test'}))
        self.assertIn('https://', with_contact['body'])
        self.assertTrue(individual.validate_individual(with_contact, self.brief))

    def test_retry_feedback_stays_chinese_and_prompt_is_advertising_only(self):
        request = individual.generation_request(self.brief, problems=['Не сохранена сумма залога с правильным назначением', 'Пропущены подтверждённые предметы: 烤箱', 'В черновике пропущена локация Москва'])
        self.assertEqual(request['purpose'], 'advertising')
        content = '\n'.join(message['content'] for message in request['messages'])
        self.assertIn('押金', content)
        self.assertNotIn('Не сохранена', content)
        feedback = request['messages'][-1]['content']
        self.assertIn('城市：莫斯科', feedback)
        self.assertIn('户型：', feedback)
        self.assertIn('正文明确写出核心参数中的城市', feedback)
        self.assertEqual(clean_request(request)['maxPromptTokens'], 4096)

    def test_instruction_echo_and_repeated_prose_are_rejected_without_repair(self):
        draft = complete(self.brief)
        for tail in ('未知条件不承诺，已知不利条件不遗漏。', '房源资料清楚，欢迎了解这套公寓。'*6,
                     '餐椅、餐椅、餐椅、餐椅、餐椅。'):
            self.assertTrue(individual.validate_individual({**draft, 'body': draft['body']+tail}, self.brief))


class IndividualPreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.directory = Path(self.temp.name)
        self.brief = individual.build_brief(FIELDS); self.draft = complete(self.brief)

    def tearDown(self): self.temp.cleanup()

    def preview(self, result, *, part='body', prior=None):
        with patch.object(individual, 'build_brief', return_value=self.brief), \
             patch.object(language_service, '_profile', return_value={}), \
             patch.object(individual, '_model', return_value=result) as model:
            value = individual.generate_preview(self.directory, {'fields': FIELDS, 'part': part, 'groupId': 'isolated-test',
                'output': prior or {'title': self.draft['title'], 'body': '旧的手工说明'}})
        return value, model

    def test_body_only_accepts_same_title_and_records_only_pending_body(self):
        value, model = self.preview({'status': 'review', 'text': model_text(self.draft)})
        self.assertEqual(value['status'], 'review'); self.assertTrue(value['modelAccepted'])
        self.assertEqual(model.call_args.args[0]['purpose'], 'advertising')
        status = LearningMemory(self.directory).status()
        self.assertEqual(status['approvedExamples'], 0)
        self.assertEqual(status['counts'].get('pending'), 1)
        duplicate, _ = self.preview({'status': 'review', 'text': model_text(self.draft)})
        self.assertEqual(duplicate['status'], 'blocked')

    def test_failure_never_claims_template_success_or_changes_current_text(self):
        for response in ({'status': 'unavailable', 'message': 'Model unavailable'},
                         {'status': 'review', 'text': model_text({**self.draft, 'body': self.draft['body']+'免费接送。'})},
                         {'status': 'review', 'text': model_text(self.draft), 'stopReason': 'length'}):
            value, _ = self.preview(response)
            self.assertNotEqual(value['status'], 'review'); self.assertFalse(value['fallback'])
            self.assertFalse(value['modelAccepted']); self.assertNotIn('body', value)

    def test_phone_rewrite_preserves_other_manual_parts_and_full_payload_cas(self):
        original = {'fields': copy.deepcopy(FIELDS), 'titles': ['手工标题'], 'body': '手工说明', 'tags': '#手工标签'}
        card_id = local_server.save_result(self.directory, original)
        accepted = {**self.draft, 'tags': '#新标签', 'status': 'review', 'modelAccepted': True,
            'generationMode': 'individual-model-prose', 'fallback': False}
        with patch.object(individual, 'generate_preview', return_value=accepted):
            value = language_service.rewrite(self.directory, card_id, {'part': 'body', 'mode': 'individual'})
        self.assertEqual(value['body'], self.draft['body']); self.assertEqual(value['titles'], original['titles'])
        self.assertEqual(value['tags'], original['tags'])
        self.assertEqual(value['aiAdvertisement']['generationMode'], 'individual-model-prose')
        def concurrent(*args, **kwargs):
            updated = local_server.read_result(self.directory, card_id); updated['body'] = '更晚的手工说明'
            local_server.save_result(self.directory, updated, card_id)
            return accepted
        with patch.object(individual, 'generate_preview', side_effect=concurrent):
            with self.assertRaises(language_service.LanguageConflict):
                language_service.rewrite(self.directory, card_id, {'part': 'body', 'mode': 'individual'})
        self.assertEqual(local_server.read_result(self.directory, card_id)['body'], '更晚的手工说明')


if __name__ == '__main__': unittest.main()
