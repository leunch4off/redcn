#!/usr/bin/env python3
"""Build reproducible fictional rental SFT pairs, with disjoint property groups.

The donor advertisements informed only structural choices. No copied donor text,
user property, identity, phone number or private photograph is in this corpus.
Held-out real-source acceptance cases are never imported by this script.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import random
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'cli'))
from advertising_styles import STYLES
from text_quality import LISTING_SYSTEM, TRANSLATION_SYSTEM
from text_model_contract import load_dataset


EQUIPMENT = (('冰箱', 'холодильник'), ('洗衣机', 'стиральная машина'),
             ('烤箱', 'духовой шкаф'), ('微波炉', 'микроволновая печь'),
             ('洗碗机', 'посудомоечная машина'), ('空调', 'кондиционер'),
             ('沙发', 'диван'), ('衣柜', 'шкаф'), ('书桌', 'письменный стол'),
             ('电视', 'телевизор'), ('双人床', 'двуспальная кровать'))
# Synthetic station labels avoid teaching unreviewed translations of real names.
STATIONS = ('松林', '青园', '白桦', '河岸', '新城', '湖畔', '星河', '南园')
LAYOUTS = (('开间', 'Студия'), ('一居室', 'Однокомнатная квартира'),
           ('独立两居', 'Двухкомнатная квартира с изолированными комнатами'),
           ('三居室', 'Трёхкомнатная квартира'))
POLICIES = ('禁止养宠物；登记协助费用待确认。',
            '可养宠物；登记协助免费。',
            '宠物需协商；房东不办理登记，工作室可协助，费用待确认。',
            '宠物需协商；登记条件待确认。')
CTA = ('欢迎了解详细信息。', '感兴趣可以进一步了解。', '看房安排可再沟通。',
       '需要更多信息可以询问。', '租住细节可继续沟通。', '欢迎咨询这套房源。')


def property_facts(index, rng):
    layout, layout_ru = LAYOUTS[index % len(LAYOUTS)]
    area = 26 + (index * 13) % 78 + (0.5 if index % 7 == 0 else 0)
    rent = 37000 + ((index * 2750) % 101000)
    deposit = rent if index % 3 else int(rent / 2)
    floors = 12 + index % 17
    floor = 1 + (index * 7) % floors
    walk = 3 + index % 19
    equipment = rng.sample(EQUIPMENT, 3 + index % 3)
    station = STATIONS[index % len(STATIONS)]
    utilities = '水电另付' if index % 2 else '水电已包含在租金内'
    policy = POLICIES[index % len(POLICIES)]
    a = format(area, 'g')
    facts = (f'莫斯科，{layout}。面积{a}㎡，{floor}/{floors}层。'
             f'月租{rent}卢布，押金{deposit}卢布；{utilities}。'
             f'{station}站，步行{walk}分钟。'
             f'已确认配置：{"、".join(zh for zh, _ in equipment)}。{policy}')
    return dict(layout=layout, layout_ru=layout_ru, area=a, rent=rent, deposit=deposit,
                floor=floor, floors=floors, walk=walk, equipment=equipment,
                station=station, utilities=utilities, policy=policy, facts=facts)


def render(f, variant):
    space = f'{f["area"]}㎡｜{f["floor"]}/{f["floors"]}层｜{f["layout"]}'
    money = f'月租{f["rent"]}卢布，押金{f["deposit"]}卢布；{f["utilities"]}。'
    transit = f'{f["station"]}站，步行{f["walk"]}分钟。'
    equipment = '、'.join(zh for zh, _ in f['equipment']) + '。'
    titles = (
        f'莫斯科{f["layout"]}｜月租{f["rent"]}卢布',
        f'莫斯科{f["station"]}站房源｜{f["layout"]}',
        f'莫斯科{f["area"]}㎡{f["layout"]}｜空间配置',
        f'莫斯科{f["layout"]}｜{f["equipment"][0][0]}与日常配置',
        f'莫斯科{f["layout"]}｜租金与入住条件',
        f'莫斯科房源笔记｜{f["layout"]}·{f["area"]}㎡',
    )
    sections = (
        [f'💰{money}', f'🏠{space}', f'🚇{transit}', f'配置：{equipment}', f['policy']],
        [f'出行信息｜{transit}', f'空间｜{space}', f'日常配置｜{equipment}', money, f['policy']],
        [f'空间先看这里：{space}', f'屋内配置有{equipment}', transit, money, f['policy']],
        [f'室内已配：{equipment}', f'🏠{space}', f'💰{money}', transit, f['policy']],
        [f'费用说明：{money}', f'入住条件：{f["policy"]}', space, transit, f'已有配置：{equipment}'],
        [f'这套莫斯科房源的已知信息：{space}', transit, f'配置清单：{equipment}', money, f['policy']],
    )
    return {'title': titles[variant], 'body': '\n'.join([*sections[variant], CTA[variant]])}


def build():
    rng = random.Random(20261102)
    result = {'version': 1, 'provenance': {
        'kind': 'project-authored-fictional', 'seed': 20261102,
        'purpose': 'Factual Chinese advertising structures and faithful compact translation',
        'containsUserListings': False, 'containsCopiedAdvertisements': False,
        'containsHeldoutCases': False, 'stationNames': 'fictional; not metro dictionary entries',
        'nativeEditorAccepted': False}, 'splits': {'train': [], 'validation': []}}
    for split, indices in [('train', range(80)), ('validation', range(1000, 1016))]:
        for index in indices:
            f = property_facts(index, rng)
            group = f'fictional-v27-property-{index:04d}'
            for variant in range(6):
                output = render(f, variant)
                if len(re.findall(r'[\u3400-\u9fff]', output['body'])) > 150:
                    raise ValueError('Corpus description exceeds the inference contract')
                payload = {'verifiedText': f['facts'], 'style': STYLES[variant][1],
                           'recentTitlesToAvoid': [], 'previousTextToAvoid': '',
                           'variation': f'fiction-{index}-{variant}'}
                result['splits'][split].append({'id': f'{group}-style-{variant}', 'groupId': group,
                    'status': 'project-authored', 'source': 'redcn-fictional-advertising-v27',
                    'messages': [{'role': 'system', 'content': LISTING_SYSTEM},
                                 {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False, separators=(',', ':'))},
                                 {'role': 'assistant', 'content': json.dumps(output, ensure_ascii=False, separators=(',', ':'))}]})
            ru = (f'{f["layout_ru"]} в Москве. Площадь {f["area"]} м². Аренда {f["rent"]} рублей в месяц, '
                  f'залог {f["deposit"]} рублей. До метро пешком {f["walk"]} минут. '
                  + ('Вода и электричество оплачиваются отдельно.' if index % 2 else 'Вода и электричество включены в аренду.'))
            zh = (f'莫斯科{f["layout"]}。面积{f["area"]}㎡。月租{f["rent"]}卢布，押金{f["deposit"]}卢布。'
                  f'步行到地铁{f["walk"]}分钟。{f["utilities"]}。')
            for direction, language, source, target in [('ru-zh', 'Simplified Chinese', ru, zh), ('zh-ru', 'Russian', zh, ru)]:
                result['splits'][split].append({'id': f'{group}-{direction}', 'groupId': group,
                    'status': 'project-authored', 'source': 'redcn-fictional-advertising-v27',
                    'messages': [{'role': 'system', 'content': TRANSLATION_SYSTEM.format(language=language)},
                                 {'role': 'user', 'content': json.dumps({'text': source, 'approvedGlossary': []}, ensure_ascii=False)},
                                 {'role': 'assistant', 'content': target}]})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'data/learning/advertising-fictional-v27.json')
    args = parser.parse_args()
    dataset = build()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(dataset, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    load_dataset(args.output)
    print(json.dumps({'path': str(args.output), 'sha256': hashlib.sha256(args.output.read_bytes()).hexdigest(),
        'counts': {key: len(value) for key, value in dataset['splits'].items()},
        'groups': {key: len(Counter(row['groupId'] for row in value)) for key, value in dataset['splits'].items()}}, ensure_ascii=False))


if __name__ == '__main__':
    main()
