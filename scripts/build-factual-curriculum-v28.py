#!/usr/bin/env python3
"""Authored fictional curriculum for full factual coverage, never model approval.

This builder reads only the production renderer and prior authored layout code.
It does not open user listings, benchmark cases, candidate answers or histories.
New fictional groups contain both typed fields and explicitly authored additional
verified facts. All targets must pass the unchanged production checks.
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
from local_content import (advertising_baseline, advertising_messages,
                           _advertising_issues, _content_fingerprint)
from text_model_contract import file_sha, validate_dataset
from text_quality import TRANSLATION_SYSTEM, translation_checks

SPEC = importlib.util.spec_from_file_location('v27_layout_helpers', ROOT / 'scripts/build-aligned-advertising-corpus.py')
layouts = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(layouts)

SEED = 20261104
HISTORY_CONTEXT_CHARACTERS = 240
# Invented labels teach exact name retention, not real metro translations.
STATIONS = ('УчКедр', 'УчЛипа', 'УчИва', 'УчЯсень', 'УчТополь', 'УчМёд',
            'УчВереск', 'УчБерёза', 'УчСвет', 'УчОльха', 'УчСосна', 'УчРябина')
EQUIPMENT = (
    ('fridge', '冰箱', 'холодильник'), ('washer', '洗衣机', 'стиральная машина'),
    ('oven', '烤箱', 'духовой шкаф'), ('microwave', '微波炉', 'микроволновая печь'),
    ('dishwasher', '洗碗机', 'посудомоечная машина'), ('ac', '空调', 'кондиционер'),
    ('sofa', '沙发', 'диван'), ('wardrobe', '衣柜', 'шкаф'),
    ('desk', '书桌', 'письменный стол'), ('tv', '电视', 'телевизор'),
    ('bed', '床', 'кровать'), ('table', '餐桌', 'обеденный стол'),
    ('chairs', '椅子', 'стулья'), ('tvStand', '电视柜', 'тумба под телевизор'),
    ('walkInCloset', '衣帽间', 'гардеробная'), ('bathtub', '浴缸', 'ванна'),
    ('hood', '抽油烟机', 'вытяжка'), ('hob', '电磁炉', 'индукционная плита'),
)


def property_facts(index):
    """Sample price/layout/policies/equipment independently, with sparse cases."""
    rng = random.Random(SEED + index * 104729)
    # The existing renderer prints W with two decimals (100 RUB). A rent
    # divisible by 200 keeps both its exact amount and half-rent deposit exact.
    price = rng.randrange(41, 142) * 1000 + rng.choice((0, 200, 400, 600, 800))
    sparse = index % 4 == 0
    fields = {'city': 'Москва', 'price': str(price), 'priceCurrency': 'RUB',
              'pricePeriod': 'month', 'utilities': rng.choice(('included', 'meters', 'extra', 'unknown')),
              'petPolicy': rng.choice(('no', 'negotiable', 'conflict', 'yes')),
              'childrenPolicy': rng.choice(('no', 'conflict', 'yes'))}
    if not sparse or rng.random() < .5:
        fields['area'] = str(rng.randrange(27, 103))
    if not sparse or rng.random() < .5:
        rooms = rng.choice((0, 1, 2, 3))
        fields['layout'] = 'studio' if rooms == 0 else 'classic'
        if rooms:
            fields['rooms'] = str(rooms)
    if rng.random() < .75:
        fields['deposit'] = str(price * rng.choice((1, 2)) // 2)
    if not sparse and rng.random() < .5:
        floors = rng.randrange(10, 29)
        fields.update(floor=str(rng.randrange(1, floors + 1)), floors=str(floors))
    stations = rng.sample(STATIONS, 2)
    if rng.random() < .85:
        fields['metro'] = stations[0]
        if rng.random() < .5:
            fields['walk'] = str(rng.randrange(4, 23))
    for key in ('smokingPolicy', 'guestPolicy', 'subletPolicy'):
        if rng.random() < .35:
            fields[key] = 'no'
    if not sparse and rng.random() < .25:
        fields['contractMonths'] = str(rng.choice((7, 9, 11, 14)))
    pool = rng.sample(EQUIPMENT, len(EQUIPMENT))
    count = rng.randrange(0, 3) if sparse else rng.randrange(4, 8)
    present = pool[:count]
    absent = pool[count:count + rng.choice((1, 2))]
    uncertain = pool[count + 2:count + 3]
    # Positive equipment is stated as an additional verified text block. This
    # exercises facts from source/photo review that have no typed-field entry.
    fields['features'] = []
    extras = []
    if present:
        extras.append('已确认配有' + '、'.join(item[1] for item in present) + '。')
    extras.append('未配备' + '、'.join(item[1] for item in absent) + '。')
    extras.append(uncertain[0][1] + '是否配备尚未确认。')
    if index % 3 == 0:
        extras.append('▪️ ' + stations[1] + '｜时间待确认')
    # No quantity is inferred from a photo; this is a deliberately authored
    # fictional source assertion, distinct from the missing/uncertain classes.
    if present and index % 5 == 0:
        extras.append('已确认' + str(rng.choice((2, 3))) + '个' + present[0][1] + '。')
    return fields, '\n'.join(extras), {'present': present, 'absent': absent,
        'uncertain': uncertain, 'stations': stations, 'sparse': sparse}


def production_basis(fields, extra):
    grounded, baseline = advertising_baseline(fields)
    baseline = {**baseline, 'body': baseline['body'] + '\n' + extra}
    return grounded, baseline


def render_target(fields, baseline, style_index):
    groups = layouts.factual_blocks(baseline)
    # The renderer's baseline narrative is redundant with its factual rows.
    # Every explicit condition, fee and extra verified assertion remains.
    area = fields.get('area', '')
    focus = area + '㎡' if area else '租住资料'
    titles = (
        f'莫斯科租房预算｜{focus}', f'莫斯科房源｜出行与{focus}',
        f'空间资料｜莫斯科{focus}', f'莫斯科公寓｜{focus}配置',
        f'莫斯科租住条件｜{focus}', f'莫斯科房源笔记｜{focus}',
        f'莫斯科看房清单｜{focus}', f'莫斯科租房速览｜{focus}',
        f'看看莫斯科这套公寓｜{focus}', f'莫斯科租房问答｜{focus}',
        f'莫斯科房源观察｜{focus}', f'莫斯科找房参考｜{focus}',
    )
    headings = {'money': '费用', 'space': '空间', 'equipment': '配置',
                'route': '出行', 'conditions': '具体条件', 'service': '登记协助'}
    sections = []
    for kind in layouts.ORDER[style_index]:
        lines = groups[kind]
        if not lines:
            continue
        text = '、'.join(lines) if kind == 'equipment' else '\n'.join(lines)
        if style_index in (2, 4, 9):
            text = headings[kind] + '\n' + text
        elif style_index in (0, 6):
            text = '▫️ ' + text
        sections.append(text)
    endings = ('欢迎了解租住细节。', '感兴趣可以进一步了解。', '看房安排可再沟通。',
               '先核对条件，再安排看房。', '欢迎询问房源详情。', '租住信息可继续沟通。')
    if style_index in (5, 10, 11):
        sections.insert(0, ('这套房源的具体资料如下。', '看看这份房源资料。',
                            '找房时，可以先了解这些信息。')[style_index % 3])
    draft = {'title': titles[style_index], 'body': '\n\n'.join([*sections, endings[style_index % 6]])}
    issues = _advertising_issues(draft, baseline, fields)
    if issues:
        raise ValueError(f'Authored advertisement failed production checks ({style_index}): {issues}')
    return draft


def translation_pair(index, fields, meta):
    """Parallel authored statements, including name copying and mixed polarity."""
    price = fields['price']
    ru = ['Квартира в Москве.', f'Аренда {price} рублей в месяц.']
    zh = ['莫斯科公寓。', f'月租{price}卢布。']
    if fields.get('deposit'):
        ru.append(f'Залог {fields["deposit"]} рублей.')
        zh.append(f'押金{fields["deposit"]}卢布。')
    if fields.get('area'):
        ru.append(f'Площадь {fields["area"]} м².')
        zh.append(f'面积{fields["area"]}㎡。')
    if fields.get('floor'):
        ru.append(f'Квартира на {fields["floor"]} этаже.')
        zh.append(f'公寓位于{fields["floor"]}楼。')
    if meta['present']:
        ru.append('Есть ' + ', '.join(item[2] for item in meta['present']) + '.')
        zh.append('配有' + '、'.join(item[1] for item in meta['present']) + '。')
    absence = 'Отсутствует' if len(meta['absent']) == 1 and meta['absent'][0][0] != 'chairs' else 'Отсутствуют'
    ru.append(absence + ': ' + ', '.join(item[2] for item in meta['absent']) + '.')
    zh.append('未配备' + '、'.join(item[1] for item in meta['absent']) + '。')
    ru.append('Наличие предмета «' + meta['uncertain'][0][2] + '» пока не подтверждено.')
    zh.append(meta['uncertain'][0][1] + '是否配备尚未确认。')
    policies = {
        'no': ('Животные запрещены.', '不接受宠物。'),
        'negotiable': ('Проживание с животными возможно по согласованию.', '携带宠物需事先协商。'),
        'conflict': ('Возможность проживания с животными пока не подтверждена.', '宠物条件待确认。'),
        'yes': ('Можно проживать с животными.', '可以携带宠物入住。'),
    }
    first, second = policies[fields['petPolicy']]
    ru.append(first); zh.append(second)
    children = {'no': ('Проживание с детьми запрещено.', '不接受带孩子入住。'),
        'conflict': ('Возможность проживания с детьми пока не подтверждена.', '儿童入住条件待确认。'),
        'yes': ('Можно проживать с детьми.', '可以带孩子入住。')}
    first, second = children[fields['childrenPolicy']]
    ru.append(first); zh.append(second)
    if fields.get('metro'):
        station = fields['metro']
        if fields.get('walk'):
            ru.append(f'До станции метро «{station}» пешком {fields["walk"]} минут.')
            zh.append(f'步行{fields["walk"]}分钟到{station}地铁站。')
        else:
            ru.append(f'Указана станция метро «{station}»; время в пути пока не подтверждено.')
            zh.append(f'地铁站{station}；通勤时间尚未确认。')
    if index % 3 == 0:
        station = meta['stations'][1]
        ru.append(f'Указана станция «{station}»; время в пути пока не подтверждено.')
        zh.append(f'{station}站；通勤时间尚未确认。')
    utility_pairs = {
        'included': ('Все платежи уже входят в стоимость аренды.', '租金包含各项费用。'),
        'extra': ('Вода оплачивается отдельно. Электричество оплачивается отдельно. Прочие расходы оплачиваются отдельно.',
                  '水费另付。电费另付。其他费用另付。'),
        'meters': ('Обслуживание дома включено. Вода оплачивается по счётчикам. Электричество оплачивается по счётчикам.',
                   '物业费已包含。水费按表计费。电费按表计费。'),
    }
    if fields['utilities'] in utility_pairs:
        first, second = utility_pairs[fields['utilities']]
        ru.append(first); zh.append(second)
    if index % 4 == 1:
        ru.append('Собственник не предоставляет регистрацию. Студия может помочь с регистрацией; условия и стоимость необходимо уточнить.')
        zh.append('房东不提供登记。工作室可协助办理登记；条件和费用尚未确认。')
    return ' '.join(ru), ''.join(zh)


def build(train_groups=120, validation_groups=24):
    if not 12 <= train_groups <= 180 or not 12 <= validation_groups <= 36:
        raise ValueError('Bounded independent group counts are required')
    value = {'version': 1, 'provenance': {
        'kind': 'project-authored-fictional-factual-curriculum', 'seed': SEED,
        'supersedesExperimentData': False, 'containsUserListings': False,
        'containsCopiedAdvertisements': False, 'containsHeldoutCases': False,
        'containsModelGeneratedTargets': False, 'nativeEditorAccepted': False,
        'approval': 'Project-authored synthetic targets; not user or native-editor approval.',
        'stationNames': 'Invented labels used verbatim; never proposed real metro translations.',
        'additionalFacts': 'Explicit authored fictional assertions, never inferred from unchecked fields.',
        'trainingHistoryCharacters': HISTORY_CONTEXT_CHARACTERS,
        'productionHistoryMaximumCharacters': 300,
        'lengthPolicy': 'Only optional prior draft context is bounded; verified facts and complete answers are never truncated.',
        'advertisingSystemSha256': hashlib.sha256(ADVERTISING_SYSTEM.encode()).hexdigest(),
        'generatorSha256': file_sha(__file__), 'layoutHelpersSha256': file_sha(layouts.__file__),
        'sourceContractSha256': file_sha(ROOT / 'cli/local_content.py'),
        'trainGroups': train_groups, 'validationGroups': validation_groups},
        'splits': {'train': [], 'validation': []}}
    for split, indices in (('train', range(train_groups)),
                           ('validation', range(2000, 2000 + validation_groups))):
        groups = []
        for index in indices:
            fields, extra, meta = property_facts(index)
            grounded, baseline = production_basis(fields, extra)
            reference = '\n'.join([baseline['titles'][0], baseline['body']]).strip()
            group_id = f'fictional-factual-v28-{index:04d}'
            common = {'groupId': group_id, 'status': 'project-authored',
                      'source': 'redcn-authored-factual-curriculum-v28', 'fictional': True}
            rows = []
            history = []
            for offset in range(3):
                style_index = (index * 5 + offset * 3) % len(STYLES)
                style = STYLES[style_index]
                answer = render_target(grounded, baseline, style_index)
                previous = history[-1] if history else {'title': baseline['titles'][0], 'body': baseline['body']}
                messages = advertising_messages(reference, style[1], seed=SEED + index * 31 + offset,
                    previous_body=previous['body'][:HISTORY_CONTEXT_CHARACTERS], previous_title=previous['title'],
                    recent_titles=[item['title'] for item in history])
                if (_content_fingerprint(previous['body']) == _content_fingerprint(answer['body']) or
                        _content_fingerprint(previous['title']) == _content_fingerprint(answer['title'])):
                    raise ValueError('Authored target repeats supplied history')
                rows.append({**common, 'id': group_id + ':' + style[0], 'task': 'advertising',
                    'styleId': style[0], 'historyPresent': True, 'verifiedFields': grounded,
                    'additionalVerifiedText': extra, 'sparse': meta['sparse'],
                    'messages': messages + [{'role': 'assistant', 'content': json.dumps(answer, ensure_ascii=False, separators=(',', ':'))}]})
                history.append(answer)
            ru, zh = translation_pair(index, fields, meta)
            for direction, language, source, target in (
                    ('ru-zh', 'Simplified Chinese', ru, zh), ('zh-ru', 'Russian', zh, ru)):
                checks = translation_checks(source, target, direction)
                if not checks['passed']:
                    raise ValueError(f'Authored translation {index} {direction} failed factual checks: {checks["issues"]}')
                rows.append({**common, 'id': group_id + ':' + direction, 'task': 'translation-' + direction,
                    'messages': [{'role': 'system', 'content': TRANSLATION_SYSTEM.format(language=language)},
                        {'role': 'user', 'content': json.dumps({'text': source, 'approvedGlossary': []}, ensure_ascii=False)},
                        {'role': 'assistant', 'content': target}]})
            groups.append(rows)
        # Validation loss's bounded first 32 rows include all tasks and groups.
        value['splits'][split] = [group[position] for block in range(0, len(groups), 8)
            for position in (0, 3, 4, 1, 2) for group in groups[block:block + 8]]
    return validate_dataset(value)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'data/learning/factual-curriculum-v28.json')
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Preserve experiment data: select a new output path')
    value = build()
    encoded = json.dumps(value, ensure_ascii=False, indent=2) + '\n'
    if len(encoded.encode()) > 8_000_000:
        raise ValueError('Corpus exceeds the training contract size bound')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(encoded, encoding='utf-8')
    print(json.dumps({'path': str(args.output), 'sha256': file_sha(args.output),
        'counts': {key: len(rows) for key, rows in value['splits'].items()},
        'tasks': {key: dict(Counter(row['task'] for row in rows)) for key, rows in value['splits'].items()}}, ensure_ascii=False))


if __name__ == '__main__':
    main()
