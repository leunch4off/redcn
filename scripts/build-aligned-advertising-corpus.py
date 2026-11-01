#!/usr/bin/env python3
"""Separate fictional curriculum using the actual advertising message contract.

The original v27 experiment is preserved. This builder never opens heldout cases,
user listings, model outputs, or private histories. Every advertising answer is
checked against its complete production-rendered facts before it can be saved.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import importlib.util
import json
from pathlib import Path
import random
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'cli'))
from advertising_styles import ADVERTISING_SYSTEM, STYLES
from local_content import advertising_baseline, advertising_messages, _advertising_issues, _content_fingerprint
from text_model_contract import file_sha, validate_dataset
from text_quality import TRANSLATION_SYSTEM, translation_checks

SEED = 20261103
# These are fictional labels, not proposed translations of real metro stations.
STATIONS = ('Кедровая Долина', 'Тихая Сосна', 'Белый Ручей', 'Северная Липа', '青叶', '云林')
EQUIPMENT = (
    ('fridge', '冰箱', 'холодильник'), ('washer', '洗衣机', 'стиральная машина'),
    ('oven', '烤箱', 'духовой шкаф'), ('microwave', '微波炉', 'микроволновая печь'),
    ('dishwasher', '洗碗机', 'посудомоечная машина'), ('ac', '空调', 'кондиционер'),
    ('sofa', '沙发', 'диван'), ('wardrobe', '衣柜', 'шкаф'),
    ('desk', '书桌', 'письменный стол'), ('tv', '电视', 'телевизор'),
)
CTA = ('欢迎了解这套房源。', '感兴趣可以继续了解详情。', '租住细节可进一步沟通。',
       '欢迎询问详细信息。', '先核对条件，再安排看房。', '看房安排可再沟通。')
ORDER = (
    ('money', 'space', 'equipment', 'route', 'conditions', 'service'),
    ('route', 'space', 'equipment', 'money', 'conditions', 'service'),
    ('space', 'equipment', 'route', 'money', 'conditions', 'service'),
    ('equipment', 'space', 'route', 'money', 'conditions', 'service'),
    ('money', 'conditions', 'space', 'equipment', 'route', 'service'),
    ('space', 'route', 'equipment', 'conditions', 'money', 'service'),
    ('space', 'money', 'conditions', 'equipment', 'route', 'service'),
    ('money', 'space', 'route', 'conditions', 'equipment', 'service'),
    ('space', 'equipment', 'conditions', 'route', 'money', 'service'),
    ('money', 'space', 'route', 'equipment', 'conditions', 'service'),
    ('equipment', 'money', 'space', 'route', 'conditions', 'service'),
    ('space', 'conditions', 'equipment', 'route', 'money', 'service'),
)


def property_fields(index):
    """Independent draws avoid encoding station→layout/policy shortcuts."""
    rng = random.Random(SEED + index * 7919)
    price = rng.randrange(46, 119) * 1000
    fields = {'city': 'Москва', 'price': str(price), 'priceCurrency': 'RUB',
              'pricePeriod': 'month', 'rooms': str(rng.choice((0, 1, 2, 3))),
              'area': str(rng.randrange(28, 84)), 'deposit': str(price * rng.choice((1, 2)) // 2),
              'utilities': rng.choice(('included', 'meters', 'extra', 'unknown')),
              'petPolicy': rng.choice(('yes', 'no', 'negotiable', 'conflict')),
              'childrenPolicy': rng.choice(('yes', 'no', 'conflict'))}
    if rng.random() < .8:
        fields['metro'] = rng.choice(STATIONS)
        if rng.random() < .6:
            fields['walk'] = str(rng.randrange(4, 19))
    floors = rng.randrange(9, 27)
    if rng.random() < .6:
        fields.update(floor=str(rng.randrange(1, floors + 1)), floors=str(floors))
    fields['features'] = [item[0] for item in rng.sample(EQUIPMENT, rng.choice((0, 1, 2, 3)))]
    if rng.random() < .35:
        fields['smokingPolicy'] = 'no'
    if rng.random() < .25:
        fields['guestPolicy'] = 'no'
    if rng.random() < .3:
        fields['subletPolicy'] = 'no'
    if rng.random() < .25:
        fields['contractMonths'] = str(rng.choice((8, 10, 12)))
    if rng.random() < .2:
        fields['prepayMonths'] = str(rng.choice((1, 2)))
    return fields


def factual_blocks(baseline):
    """Keep factual lines and restrictions; remove redundant sales narrative.

    A renderer section called 床与收纳 is a heading, not proof of a bed. The
    independent production fact check below catches any accidental lost fact.
    """
    groups = defaultdict(list)
    for block in baseline['body'].split('\n\n'):
        if block.startswith('🏡 '):
            continue
        for original in block.splitlines():
            if original.startswith('📩 '):
                continue
            line = re.sub(r'^[^\w\u3400-\u9fff]+', '', original).strip()
            if not line or line in ('床与收纳', '餐厨设备', '洗浴与洗衣', '客厅与休闲', '交通概况：'):
                continue
            if original.startswith('✅'):
                kind = 'equipment'
            elif re.search(r'租金|押金|物业|水电|费用|预付|合同期限', line):
                kind = 'money'
            elif re.search(r'户型|面积|楼层', line):
                kind = 'space'
            elif re.search(r'步行|时间待确认', line):
                kind = 'route'
            elif '登记' in line:
                kind = 'service'
            else:
                kind = 'conditions'
            groups[kind].append(line)
    return groups


def render_target(fields, baseline, style_index):
    groups = factual_blocks(baseline)
    area = fields['area'] + '㎡'
    titles = (
        f'莫斯科租住预算｜{area}', f'莫斯科房源｜{area}与出行信息',
        f'莫斯科{area}｜空间说明', f'莫斯科公寓｜{area}配置笔记',
        f'租金与条件｜莫斯科{area}', f'莫斯科房源笔记｜{area}',
        f'莫斯科租房清单｜{area}', f'莫斯科房源速览｜{area}',
        f'走进莫斯科{area}公寓', f'莫斯科租房｜{area}与费用',
        f'莫斯科{area}房源观察', f'莫斯科公寓｜看看这套{area}',
    )
    headings = {'money': '费用', 'space': '空间', 'equipment': '配置', 'route': '出行',
                'conditions': '入住条件', 'service': '登记协助'}
    sections = []
    for kind in ORDER[style_index]:
        lines = groups[kind]
        if not lines:
            continue
        if kind == 'equipment':
            text = '已确认配置：' + '、'.join(lines) + '。'
        else:
            text = '\n'.join(lines)
        if style_index in (2, 4, 9):
            text = headings[kind] + '\n' + text
        elif style_index in (0, 6):
            text = '▫️ ' + text
        sections.append(text)
    if style_index in (5, 10, 11):
        sections.insert(0, ('这套房源的已知信息如下。', '看看这套房源的具体信息。',
                            '这份房源资料可以先了解。')[style_index % 3])
    draft = {'title': titles[style_index], 'body': '\n\n'.join([*sections, CTA[style_index % len(CTA)]])}
    issues = _advertising_issues(draft, baseline, fields)
    if issues:
        raise ValueError(f'Authored target failed production facts ({style_index}): {issues}')
    return draft


def translations(index, fields):
    """Short bilingual exercises with facts independent of station/layout.

    Equipment absence/uncertainty is deliberate authored information here, not
    inferred from missing checkboxes in the advertising field projection.
    """
    rng = random.Random(SEED + index * 3571)
    present, missing = rng.sample(EQUIPMENT, 2)
    ru = (f'Квартира в Москве. Площадь {fields["area"]} м². '
          f'Аренда {fields["price"]} рублей в месяц, залог {fields["deposit"]} рублей. '
          f'Есть {present[2]}. ')
    zh = (f'莫斯科公寓，面积{fields["area"]}㎡。月租{fields["price"]}卢布，'
          f'押金{fields["deposit"]}卢布。配有{present[1]}。')
    if rng.choice((False, True)):
        ru += f'Отсутствует {missing[2]}. '
        zh += f'未配备{missing[1]}。'
    else:
        ru += f'Наличие предмета «{missing[2]}» пока не подтверждено. '
        zh += f'{missing[1]}是否配备尚未确认。'
    policy = rng.choice((0, 1, 2))
    ru += ('Животные запрещены.', 'Проживание с животными возможно по согласованию.',
           'Возможность проживания с животными пока не подтверждена.')[policy]
    zh += ('不接受宠物。', '携带宠物需事先协商。', '宠物条件待确认。')[policy]
    return ru, zh


def balanced_translation_replay():
    path = ROOT / 'scripts/prepare-balanced-text-training.py'
    spec = importlib.util.spec_from_file_location('balanced_authored_replay', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    prior = module.prepare()
    return {split: [row for row in rows if row.get('task', '').startswith('translation-')]
            for split, rows in prior['splits'].items()}


def build():
    replay = balanced_translation_replay()
    value = {'version': 1, 'provenance': {'kind': 'project-authored-fictional-production-contract',
        'seed': SEED, 'supersedesExperimentData': False, 'containsUserListings': False,
        'containsCopiedAdvertisements': False, 'containsHeldoutCases': False,
        'containsModelGeneratedTargets': False, 'nativeEditorAccepted': False,
        'stationNames': 'fictional names, never proposed metro translations',
        'advertisingSystemSha256': hashlib.sha256(ADVERTISING_SYSTEM.encode()).hexdigest(),
        'generatorSha256': file_sha(__file__),
        'sourceContractSha256': file_sha(ROOT / 'cli/local_content.py'),
        'replay': 'Only project-authored balanced translation pairs; original splits preserved'},
        'splits': {'train': [], 'validation': []}}
    for split, indices in (('train', range(24)), ('validation', range(1000, 1008))):
        groups = []
        for index in indices:
            fields = property_fields(index)
            grounded, baseline = advertising_baseline(fields)
            reference = '\n'.join([baseline['titles'][0], baseline['body']]).strip()
            group_id = f'fictional-production-v27b-{index:04d}'
            common = {'groupId': group_id, 'status': 'project-authored',
                      'source': 'redcn-fictional-production-v27b', 'fictional': True}
            rows = []
            for offset in range(4):
                style_index = (index * 5 + offset) % len(STYLES)
                style = STYLES[style_index]
                answer = render_target(grounded, baseline, style_index)
                prior = render_target(grounded, baseline, (style_index + 5) % len(STYLES)) if offset % 2 else None
                messages = advertising_messages(reference, style[1], seed=SEED + index * 13 + offset,
                    previous_body=prior['body'] if prior else '', previous_title=prior['title'] if prior else '',
                    recent_titles=(prior['title'],) if prior else ())
                if prior and (_content_fingerprint(prior['body']) == _content_fingerprint(answer['body'])
                              or _content_fingerprint(prior['title']) == _content_fingerprint(answer['title'])):
                    raise ValueError('An authored target repeats its supplied history')
                rows.append({**common, 'id': group_id + ':' + style[0], 'task': 'advertising',
                    'styleId': style[0], 'historyPresent': prior is not None, 'verifiedFields': grounded,
                    'messages': messages + [{'role': 'assistant', 'content': json.dumps(answer, ensure_ascii=False, separators=(',', ':'))}]})
            ru, zh = translations(index, fields)
            for direction, language, source, target in (
                    ('ru-zh', 'Simplified Chinese', ru, zh), ('zh-ru', 'Russian', zh, ru)):
                checks = translation_checks(source, target, direction)
                if not checks['passed']:
                    raise ValueError(f'Authored translation failed factual checks: {checks["issues"]}')
                rows.append({**common, 'id': group_id + ':' + direction, 'task': 'translation-' + direction,
                    'messages': [{'role': 'system', 'content': TRANSLATION_SYSTEM.format(language=language)},
                        {'role': 'user', 'content': json.dumps({'text': source, 'approvedGlossary': []}, ensure_ascii=False)},
                        {'role': 'assistant', 'content': target}]})
            groups.append(rows)
        # Spread the first 32 validation-loss rows across all new property groups
        # and tasks. The independent generation acceptance cases remain separate.
        rows = [group[position] for position in (0, 4, 5, 1, 2, 3) for group in groups]
        value['splits'][split] = rows + replay[split]
    return validate_dataset(value)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'data/learning/advertising-production-v27b.json')
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Preserve previous experiment data: use a new output path')
    value = build()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'path': str(args.output), 'sha256': file_sha(args.output),
        'counts': {split: len(rows) for split, rows in value['splits'].items()},
        'tasks': {split: dict(Counter(row['task'] for row in rows)) for split, rows in value['splits'].items()}}, ensure_ascii=False))


if __name__ == '__main__':
    main()
