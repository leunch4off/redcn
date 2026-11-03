"""Free model-written publication copy from one apartment's checked evidence.

This live inference contract deliberately does not alter rental-facts-v1, its
training corpora, or the legacy renderer. Rejected model prose never becomes a
successful template rewrite. It also never becomes approved training data.
"""
import hashlib
import json
import re
import sqlite3
import time
from collections import Counter

from local_content import (FACT_KEYS, _clean, _content_fingerprint, _model, _numeral_text,
    _quantities, _with_advertising_conditions, validate_draft)
from text_quality import language_check, parse_card, payment_periods


CONTRACT = 'individual-rental-v1'
# Kept separate from the historical FACT_KEYS: richer live context must not
# invalidate the hashes of already published training/evaluation datasets.
LIVE_KEYS = (*FACT_KEYS, 'description', 'sourceDescription', 'renovation', 'windows',
    'bathroom', 'buildingYear', 'details', 'transit', 'universities', 'university',
    'uniTime', 'uniMode', 'route', 'highlight', 'registrationPolicy',
    'url', 'editorIdentity', 'resultId', 'id', 'pickup', 'luggage', 'arrival', 'support', 'serviceBenefits')

SYSTEM = '''你是中文租房文案编辑，为这一套房独立组织标题和正文。只介绍提供的具体事实，不扩写居住体验、环境或常识。
标题突出面积、户型、价格或一种具体配置，不写清单、资料或核实。
选择具体的房源特点作为开头。正文必须写全城市、户型、面积和楼层等核心参数，不能只把城市或户型写在标题里。下列配置段落、费用与限制的条目必须完整保留，可以安排在不同位置，不改写条目内容，不改成押几付几。
没有物品房间位置资料，因此配置独立成段，不描述物品所在房间。清单没有说明不存在其他物品，也不是设备齐全的证明；不写全套、全配、配置完整或齐全。不要提照片、来源或核实过程。
交通条目逐行保留原句，每个站名、时间和方式保持对应。只介绍有资料的事项，未知内容直接省略。不要写额外数字、宣传承诺、佣金、联系方式或标签。
输出格式（标记用全角冒号，不用Markdown）：
标题：40字以内的具体标题
正文：
完整中文广告'''

SYNONYMS = {
    'bedAny': ('床铺',), 'desk': ('工作桌',), 'dresser': ('抽屉柜',),
    'armchair': ('扶手椅',), 'cabinet': ('收纳柜',), 'ottoman': ('软包凳',),
    'router': ('路由器',), 'stove': ('灶台', '灶具', '炉具'), 'hood': ('油烟机',),
    'kettle': ('电水壶',), 'table': ('饭桌',), 'chairs': ('椅子',),
    'kitchenFurniture': ('橱柜',), 'shower': ('淋浴',), 'washbasin': ('洗脸盆',),
    'closet': ('衣帽间',), 'shelves': ('书架', '收纳架'), 'loggia': ('封闭阳台',),
}


def _source_text(fields):
    source = fields.get('sourceDescription') or (fields.get('sourceAudit') or {}).get('description') or fields.get('description') or ''
    if not isinstance(source, str):
        return ''
    source = _clean(source, 50_000)
    source = re.sub(r'(?<!\d)(?:\+7|8)[\s()\-]*\d{3}[\s()\-]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}(?!\d)', '', source)
    # Raw source is optional context, never the only copy of a known condition.
    # A length limit is explicit in the brief; checked facts stay complete.
    return source[:8000]


def build_brief(fields, rules=None):
    """Resolve the same evidence/permissions as publication, with no DB writes."""
    from fact_ledger import validate_listing_fields
    from language_service import node_language
    if not isinstance(fields, dict):
        raise ValueError('Сначала выберите карточку объявления')
    validate_listing_fields(fields)
    grounded = {key: fields[key] for key in LIVE_KEYS if key in fields}
    # A source narrative is input evidence, not pre-approved public prose. The
    # engine's existing dedicated fields carry its independently checked facts.
    render_fields = {key: value for key, value in grounded.items()
        if key not in ('description', 'details', 'highlight')}
    baseline = node_language({'action': 'individual-brief', 'fields': render_fields, 'rules': rules or {}})
    resolved = baseline.get('factualBrief')
    if baseline.get('problems') or not isinstance(resolved, dict) or resolved.get('version') != CONTRACT:
        raise ValueError('Не удалось собрать проверенные факты квартиры')
    grounded.update(resolved.get('effectiveConditions') or {})
    # Service copy is included only when it has a confirmation receipt. The
    # historical renderer may offer generic registration help by default; that
    # is not evidence that the source apartment permits/finances registration.
    sections = dict(resolved['sections'])
    city = str(fields.get('city') or '').strip()
    city_zh = '莫斯科' if city.casefold() in ('москва', 'moscow', '莫斯科') else city
    if not city:
        # The legacy renderer targets Moscow by default. A missing city in a
        # saved card is not evidence for that default in individual prose.
        sections['location'] = sections.get('location', '').replace('📍 莫斯科｜', '📍 ')
    if not resolved.get('services'):
        sections['service'] = ''
    baseline_body = '\n\n'.join(sections[key] for key in ('location', 'facts', 'equipment', 'transport', 'conditions', 'service') if sections.get(key))
    extra_conditions = _with_advertising_conditions({'titles': [], 'body': ''}, grounded)['body'].strip()
    if grounded.get('registrationPolicy') == 'not_available':
        extra_conditions += '\n房东不提供落地签登记。'
    source_restrictions = []
    for row in resolved.get('sourceRestrictions') or []:
        material = {'adjacent-rooms': '房间相连。',
            'building-demolition': '该楼已纳入城市更新计划，将被拆除。'}
        if isinstance(row, dict) and row.get('kind') in material:
            line = material[row['kind']]
            source_restrictions.append({'kind': row['kind'], 'text': line})
            extra_conditions += '\n'+line
            continue
        if isinstance(row, dict) and row.get('kind') == 'internet-extra':
            line = '网络费用另付。'
            source_restrictions.append({'kind': 'internet-extra', 'text': line})
            extra_conditions += '\n'+line
            continue
        if isinstance(row, dict) and row.get('kind') == 'occupancy-maximum':
            maximum, adults_only = row.get('maximum'), row.get('adultsOnly')
            if type(maximum) is not int or not 1 <= maximum <= 50 or type(adults_only) is not bool:
                raise ValueError('Не подтверждён предел числа жильцов')
            line = f'最多{maximum}名成人入住。' if adults_only else f'入住人数不超过{maximum}人。'
            source_restrictions.append({'kind': 'occupancy-maximum', 'maximum': maximum,
                'adultsOnly': adults_only, 'text': line})
            extra_conditions += '\n'+line
            continue
        if not isinstance(row, dict) or row.get('kind') != 'occupancy-range':
            raise ValueError('Неизвестное ограничение исходного объявления')
        minimum, maximum = row.get('minimum'), row.get('maximum')
        if type(minimum) is not int or type(maximum) is not int or not 1 <= minimum <= maximum <= 50:
            raise ValueError('Не подтверждён диапазон числа жильцов')
        line = '入住人数：'+str(minimum)+'–'+str(maximum)+'人。'
        source_restrictions.append({'kind': 'occupancy-range', 'minimum': minimum,
            'maximum': maximum, 'text': line})
        extra_conditions += '\n'+line
    if extra_conditions:
        baseline_body += '\n' + extra_conditions
    baseline = {**baseline, 'body': ('城市：'+city_zh+'\n' if city_zh else '')+baseline_body, 'titles': []}
    inventory = resolved.get('inventory') or []
    single_counts = [row['zh']+'（'+('至少' if row.get('countKind') == 'minimum' else '')+'1件）' for row in inventory if row.get('count') == 1]
    if single_counts:
        baseline['body'] += '\n已确认物品数量：'+'、'.join(single_counts)
    core = []
    if city_zh:
        core.append('城市：'+city_zh)
    core.append('户型：'+resolved['layout'])
    for key, label, suffix in (('price', '月租', '₽/月'), ('area', '总面积', '㎡')):
        if fields.get(key) not in (None, '', 0):
            core.append(label+'：'+str(fields[key])+suffix)
    if fields.get('floor') or fields.get('floors'):
        core.append('楼层：'+str(fields.get('floor') or '待确认')+'/'+str(fields.get('floors') or '待确认')+'层')
    # Remove generated rent-splitting examples: division is not an occupancy
    # permission and is unnecessary in the mandatory publication contract.
    conditions = sections.get('conditions', '')
    conditions = re.sub(r'💸 合租预算参考：[\s\S]*?(?:入住人数及合租安排需与房东确认。)', '', conditions).strip()
    conditions = '\n'.join(line for line in conditions.splitlines() if line.strip())
    if extra_conditions:
        conditions += '\n'+extra_conditions
    public = {'核心参数': core, '已知费用与限制': conditions,
        '核实物品清单': [row['label']+('（'+('至少' if row.get('countKind')=='minimum' else '')+'1件）' if row.get('count') == 1 else '') for row in inventory],
        '其他核实资料': '\n\n'.join(sections[key] for key in ('location', 'facts', 'transport') if sections.get(key)),
        '已确认服务': sections.get('service', ''),
        '公寓原始描述': _source_text(fields),
        '照片证据说明': '只有清单中标注照片可见的物品有已接受照片证据。不得臆测照片内容、房间位置、颜色或数量。',
        '存在冲突需注明待确认的配置': resolved.get('photoConflicts') or []}
    # No image URL, file path, identity receipt or operator name enters MLX.
    digest = hashlib.sha256(json.dumps(public, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return {'contract': CONTRACT, 'facts': public, 'fields': grounded, 'baseline': baseline,
        'inventory': inventory, 'featureVocabulary': resolved.get('featureVocabulary') or [],
        'layout': resolved['layout'], 'metro': resolved.get('metro', ''), 'services': resolved.get('services') or [],
        'factsHash': digest, 'sourceContextTruncated': len(str(fields.get('sourceDescription') or '')) > 8000,
        'sourceContextCharacters': len(public['公寓原始描述']), 'photoConflicts': resolved.get('photoConflicts') or [],
        'sourceRestrictions': source_restrictions}


def _retry_instructions(problems):
    translations = (
        ('Условие 押', '删除所有押几付几的简称，分别保留核实的月租、押金金额、预付月数。'),
        ('Не все перечисленные предметы подтверждены фотографиями', '不要把来源说明的物品说成照片可见。可省略照片来源标注，但必须保留每种已确认物品。'),
        ('Перечень находок не подтверждает отсутствие', '删除仅有、只有、仅包含等限定词；清单不代表没有其他物品。'),
        ('происхождение', '只能把照片确认组的物品说成照片可见，不要扩大到来源确认组。'),
        ('расположение', '删除物品位于客厅、卧室或厨房的说法，只介绍确认存在的物品。'),
        ('полнот', '清单仅表示已经确认的物品，不要声称仅有这些物品或设备齐全。'),
        ('Пропущены подтверждённые предметы', '正文必须写出核实物品清单中的每一种物品，不能遗漏。'),
        ('Не подтверждены предметы', '删除清单之外的所有家具电器。不得补充常见但未核实的配置。'),
        ('Не подтверждена формулировка', '删除核实资料中没有的物品、承诺和服务。'),
        ('Добавлен неподтверждённый', '删除核实资料中没有的物品、权限和条件。'),
        ('押金', '正文明确保留押金金额及币种，不能当作月租，也不能遗漏。'),
        ('залога', '正文明确保留押金金额及币种，不能当作月租，也不能遗漏。'),
        ('месячная аренда', '正文使用“月租：已核实金额₽/月”，金额不能改变。'),
        ('локация', '正文明确写出核心参数中的城市，不要只写地铁站名或把城市留在标题里。'),
        ('Количество', '没有数量的物品不要写几个、几台、几张；已确认的最低数量必须保留“至少”。'),
        ('количество', '物品数量必须对应同一种物品。最低数量必须保留“至少”。'),
        ('этаж', '正文保留核实楼层/总楼层，不要交换面积或楼层。'),
        ('коммуналь', '完整写出已知费用：物业费是否包含，水电是否按表另付。'),
        ('коммунальные', '完整写出已知费用：物业费是否包含，水电是否按表另付。'),
        ('счётчики', '保留“物业费已包含，水电按表另付”的费用条件。'),
        ('срок', '保留已知的预付月数、合同期限和最低租期，按各自名称写出。'),
        ('числа жильцов', '正文必须保留已知入住人数限制，不能变成合租预算建议。'),
        ('сносе здания', '必须保留“该楼已纳入城市更新计划，将被拆除”。不要推测拆除日期。'),
        ('смежные комнаты', '明确写出房间相连，不能说房间独立。'),
        ('оплата интернета', '明确保留网络费用另付，不能写费用已包含。'),
        ('Маршрут', '站名、时间和方式必须逐一对应核实交通资料，不能挪用另一个车站的数字。'),
        ('запрет', '必须清楚写出已知限制，不允许宠物等条件不得遗漏或改成允许。'),
        ('услуг', '不要新增接送、免费服务、登记承诺或中介支持。'),
        ('Услуг', '不要新增接送、免费服务、登记承诺或中介支持。'),
        ('Студия', '开放式Studio不能写成独立卧室或一室一厅。'),
        ('не подтвержден', '删除无法从核实资料确认的广告说法。'),
        ('Не подтвержден', '删除无法从核实资料确认的广告说法。'),
        ('числа', '所有数字与单位必须来自核实资料，并保持原来的含义。'),
        ('повтор', '改变整篇中文的措辞和组织方式，不要重复上一版。'),
    )
    translated = []
    for issue in problems or []:
        instruction = next((chinese for token, chinese in translations if token in issue),
            '逐项核对资料；只输出“标题：”和“正文：”标记的完整中文广告，不要解释，不要遗漏已知条件。')
        terms = re.findall(r'[\u3400-\u9fff]+', issue)
        if terms:
            instruction += '本次具体检查项目：'+'、'.join(terms)+'。'
        translated.append(instruction)
    return list(dict.fromkeys(translated))[:8]


def generation_request(brief, *, seed=0, instruction='', previous_title='', previous_body='', problems=None, rejected_draft=None):
    # Raw source remains evidence for the existing condition parser and claim
    # checks. Passing an entire unverified sales narrative to a compact model
    # made it override checked prices, invent services and copy source fees.
    # The writer receives the resolved facts, not a second competing narrative.
    payload = {key: value for key, value in brief['facts'].items()
        if key not in ('公寓原始描述', '照片证据说明', '核实物品清单')}
    payload['核心参数'] = [line for line in payload.get('核心参数', []) if not line.startswith('月租：')]
    inventory_items = []
    for row in brief['inventory']:
        label = row['label'].replace('（照片可见）', '')
        if row.get('count') == 1:
            label += '（'+('至少' if row.get('countKind') == 'minimum' else '')+'1件）'
        inventory_items.append(label)
    # The publication needs the actual inventory, not a narrative about its
    # provenance. Keep provenance in the brief/validator and give the model a
    # complete, room-neutral inventory clause. This is input to full-prose
    # inference; it is never appended to, or substituted for, a failed draft.
    if inventory_items:
        payload['正文中须保留的配置段落'] = '配置：'+'、'.join(inventory_items)+'。'
    extra = payload.get('其他核实资料', '')
    # The factual renderer also produces a generic introduction. Remove just
    # that generated paragraph; measurements, location and transit remain.
    extra = re.sub(r'(?:^|\n)\s*(?:🏡|🌿|🏠|✨)\s*(?:房源介绍|关于这套住房|莫斯科租房参考|空间与配置|这套公寓的信息)[：:][^\n]*\n[^\n]*', '', extra)
    windows = str(brief['fields'].get('windows') or '').strip().casefold()
    if windows in ('во двор', 'двор', 'окна во двор'):
        extra = re.sub(r'(?m)^🪟[^\n]*', '窗户朝向庭院。', extra)
    # Source sites use short Russian enum labels, while the shared dictionary
    # usually contains the full phrase (e.g. "дизайнерский ремонт"). Resolve
    # those exact enum values before asking a Chinese-only publication model
    # to copy them; do not guess unknown values or turn them into praise.
    renovation = str(brief['fields'].get('renovation') or '').strip().casefold()
    renovation_zh = {'дизайнерский': '设计感装修', 'дизайнерский ремонт': '设计感装修',
        'косметический': '基础装修', 'косметический ремонт': '基础装修',
        'евро': '现代装修', 'евроремонт': '现代装修',
        'без ремонта': '未装修'}.get(renovation)
    if '\ufffd' in renovation:
        # Replacement characters mean the source value has lost bytes. Do
        # not infer a repair category from a damaged word or copy mojibake.
        extra = re.sub(r'(?m)^✨\s*装修[：:][^\n]*\n?', '', extra)
    elif renovation_zh:
        extra = re.sub(r'(?m)^✨\s*装修[：:][^\n]*', '装修：'+renovation_zh+'。', extra)
    payload['其他核实资料'] = extra.strip()
    # Keep explicit roles and numbers. Neither Chinese payment shorthand nor
    # a rent multiple is evidence for a differently valued deposit.
    conditions = payload.get('已知费用与限制', '')
    # Calendar-marked source restrictions (e.g. no daily rentals) are facts,
    # not numeric payment terms. Replace only the exact roles rebuilt below.
    lines = [line for line in conditions.splitlines()
        if not re.match(r'\s*(?:[💰🔐]|📄\s*预付|📅\s*(?:描述所列合同期限|卡片所列最低租期|合同期限|最低租期))', line)]
    fields = brief['fields']
    exact = []
    if fields.get('price') not in (None, ''):
        exact.append('月租：'+str(fields['price'])+'₽/月')
    if fields.get('deposit') not in (None, ''):
        exact.append('无需押金' if float(fields['deposit']) == 0 else '押金：'+str(fields['deposit'])+'₽')
    for key, label in (('prepayMonths', '预付'), ('contractMonths', '合同期限'), ('minMonths', '最低租期')):
        if fields.get(key) and (key != 'minMonths' or fields.get('leaseTerm') == 'long'):
            exact.append(label+'：'+str(fields[key])+'个月')
    payload['已知费用与限制'] = '\n'.join(exact+lines)
    payload['本次写作方向'] = instruction or '自然介绍这套房，根据实际信息安排段落，不添加空泛夸赞。'
    # Earlier public copy may itself be an inaccurate template. Novelty is
    # enforced against durable fingerprints after inference; feeding the old
    # prose back as a negative example can reintroduce its unsupported claims.
    payload['请现在完成'] = '逐项保留物品清单和明确条件。只输出标题：及正文：，写出完整中文广告。'
    context = '\n\n'.join(key+'：\n'+('\n'.join(value) if isinstance(value, list) else str(value))
        for key, value in payload.items() if value)
    messages = [{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': context}]
    if problems:
        feedback = '上一版未通过核对。请从上述事实重新写作，不要解释。\n'
        feedback += '\n'.join(_retry_instructions(problems))
        feedback += '\n正文必须明确写出的核心参数：\n'+'\n'.join(payload['核心参数'])
        feedback += '\n必须逐项保留的配置：'+ '、'.join(inventory_items)
        feedback += '\n已知费用与限制（保留原句）：\n'+payload['已知费用与限制']
        messages.append({'role': 'user', 'content': feedback})
    return {'messages': messages, 'maxTokens': 1024, 'maxPromptTokens': 4096,
        'temperature': .25, 'purpose': 'advertising', 'seed': seed % (2**32)}


def parse_individual_text(text):
    """Parse a complete title/body response without asking MLX to escape JSON.

Strict JSON remains readable for an older independently tested model, but there
is no substring extraction, quote repair, or recovery of truncated responses.
"""
    if not isinstance(text, str) or len(text) > 20_000 or '\x00' in text:
        raise ValueError('Неверный размер ответа модели')
    raw = text.strip()
    if raw.startswith('{'):
        return parse_card(raw)
    match = re.fullmatch(r'标题[：:]\s*([^\n]+)\n+\s*(?:正文[：:]\s*)?([\s\S]+)', raw)
    if not match:
        raise ValueError('Нужны одна строка 标题： и полный текст объявления на следующих строках')
    title, body = match[1].strip(), match[2].strip()
    if not title or not body or len(title) > 240 or len(body) > 6000:
        raise ValueError('Модель не вернула полный заголовок и описание')
    if re.search(r'(?m)^\s*(?:标题|正文)[：:]|```|<\|im_|</?think>', body):
        raise ValueError('Модель повторила разметку или включила служебный текст')
    return {'title': title, 'body': body}


def _names(row):
    return tuple(dict.fromkeys((row['zh'], *SYNONYMS.get(row['id'], ()))))


def _mentions(text, row, vocabulary):
    # A TV stand is not a TV; a bedside table is not proof of a bed. Longer
    # compound labels must not satisfy or invent their shorter neighbours.
    text = re.sub(r'(?m)^[^\w\u3400-\u9fff]*床与收纳(?:清单|信息)?[：:]?\s*$', '', text)
    if row['id'] == 'internet':
        # A separately payable subscription is a cost condition. It must not
        # invent installed Wi-Fi or satisfy the required equipment inventory.
        text = re.sub(r'网络(?:费用|费)[^，。；\n]{0,24}', '', text)
    for name in _names(row):
        remaining = text
        longer = [term for item in vocabulary if item['id'] != row['id'] for term in _names(item) if name in term and len(term) > len(name)]
        for term in sorted(longer, key=len, reverse=True):
            remaining = remaining.replace(term, '')
        if name in remaining:
            return True
    return False


def _object_counts(text, row, vocabulary):
    digits = {'零': 0, '一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}
    def value(number):
        if number.isascii():
            return float(number)
        if '十' in number:
            left, right = number.split('十', 1)
            return digits.get(left, 1)*10+digits.get(right, 0)
        return digits.get(number, -1)
    results = []
    for name in _names(row):
        remaining = text
        for term in sorted({term for item in vocabulary if item['id'] != row['id'] for term in _names(item) if name in term and len(term) > len(name)}, key=len, reverse=True):
            remaining = remaining.replace(term, '')
        number = r'(至少)?\s*(\d+(?:\.\d+)?|[零一二两三四五六七八九十]+)\s*(?:件|个|张|台|把|套)'
        for pattern in (number+r'\s*'+re.escape(name), re.escape(name)+r'[（(：:\s]*'+number):
            results += [(value(count), bool(minimum)) for minimum, count in re.findall(pattern, remaining)]
    return results


def _labelled_amounts(text, labels):
    # Stop at punctuation/next role, so a nearby deposit cannot satisfy rent.
    matches = re.findall('(?:'+labels+r')(?:金额)?(?:[：:\s是为]|每月)*(\d+(?:\.\d+)?)\s*(万)?\s*(卢布|₽|/月|每月|月)?', _numeral_text(text))
    return {round(float(number)*(10000 if mult else 1), 6) for number, mult, unit in matches if mult or unit}


def _floor_roles(text):
    """Read explicitly labelled floor roles without borrowing nearby numbers.

    Natural prose uses both ``14层/15层`` and ``第14层，楼栋共15层``.
    Neither an area nor a kitchen/room count proves either floor role.
    """
    number = r'(\d{1,3})(?![\d.])'
    pair_pattern = r'(?<![\d.])'+number+r'\s*(?:层|楼)?\s*/\s*(?:共|总)?\s*'+number+r'\s*(?:层|楼)'
    pairs = [(int(floor), int(total)) for floor, total in re.findall(pair_pattern, text)]
    # Remove pairs before reading standalone labels: the total in 14/15层
    # cannot be mistaken for the apartment's floor.
    prose = re.sub(pair_pattern, '', text)
    floors = {floor for floor, _total in pairs}
    totals = {total for _floor, total in pairs}
    floor_pattern = r'(?:第\s*|(?:位于|坐落在|所在(?:楼层)?|楼层|在)\s*(?:第|为|是|[：:])?\s*)'+number+r'\s*(?:层|楼)'
    total_pattern = r'(?:总(?:共|层数|楼层数|楼层|高)?|共|楼栋(?:共|总共)|(?:楼栋|楼房|楼宇|住宅楼|大楼|整栋)(?:共|总共|有|为|是))\s*[：:]?\s*'+number+r'\s*(?:层|楼)'
    floors.update(int(value) for value in re.findall(floor_pattern, prose))
    totals.update(int(value) for value in re.findall(total_pattern, prose))
    return floors, totals


def _small_integer(value):
    """The numeric words used in Chinese rental payment abbreviations."""
    if re.fullmatch(r'\d{1,3}', value):
        return int(value)
    digits = {'零': 0, '一': 1, '二': 2, '两': 2, '三': 3, '四': 4,
        '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}
    if value in digits:
        return digits[value]
    if re.fullmatch(r'[一二两三四五六七八九]?十[一二三四五六七八九]?', value):
        left, right = value.split('十')
        return digits.get(left, 1)*10+digits.get(right, 0)
    return None


def _payment_idiom_issues(text, fields):
    """押一付三 claims both a one-rent deposit and three prepaid months.

    A supplied cash deposit is never treated as an arbitrary number of months.
    Each role must be independently supported by the corresponding input.
    """
    issues = []
    numeral = r'(\d{1,3}|[零一二两三四五六七八九十]{1,3})'
    for match in re.finditer(r'押\s*'+numeral+r'\s*付\s*'+numeral, text):
        if re.search(r'(?:不是|不采用|并非|不接受)\s*$', text[max(0, match.start()-8):match.start()]):
            continue
        deposit_months, prepaid_months = (_small_integer(value) for value in match.groups())
        rent, deposit, prepay = fields.get('price'), fields.get('deposit'), fields.get('prepayMonths')
        if (deposit_months is None or rent in (None, '', 0, '0') or deposit in (None, '')
                or abs(float(rent)*deposit_months-float(deposit)) > .001):
            issues.append('Условие '+match.group(0)+' противоречит подтверждённой сумме залога')
        if prepaid_months is None or prepay in (None, '') or abs(float(prepay)-prepaid_months) > .001:
            issues.append('Условие '+match.group(0)+' меняет подтверждённый срок предоплаты')
    return issues


def _route_claim_issues(output, reference):
    """Keep station, mode and minutes paired; a shared numeral is not evidence.

    The reference uses checked, already translated transport rows. This guard
    reads explicit adjacent Chinese route claims; it is not a general parser
    for directions, and it does not require optional routes to be repeated.
    """
    duration = r'\d+(?:\.\d+)?(?:\s*[-–—至]\s*\d+(?:\.\d+)?)?'
    mode_words = r'公共交通|步行|走路|徒步|公交车|公交|巴士|地铁|驾车|开车|车程'
    mode_aliases = {'走路': '步行', '徒步': '步行', '公交车': '公交', '巴士': '公交',
        '开车': '驾车', '车程': '驾车'}
    def time_key(value):
        return tuple(float(part) for part in re.split(r'[-–—至]', value.replace(' ', '')))
    routes = {}
    for line in reference.splitlines():
        match = re.fullmatch(r'\s*[▪️•]+\s*(.+?)｜(步行|地铁|公交|公共交通|驾车|出行方式待确认)约?('+duration+r')分钟(?:｜.*)?\s*', line)
        if match:
            routes.setdefault(match[1], set()).add((match[2], time_key(match[3])))
    issues = []
    for name, expected in routes.items():
        target = re.escape(name)+r'(?:地铁)?(?:站)?'
        before = (r'(?P<mode>'+mode_words+r')?\s*(?:约|大约|只需|需|仅需)?\s*'
            r'(?P<time>'+duration+r')\s*分钟(?:内)?\s*(?:即可|就可|可)?(?:以)?'
            r'(?:到达|抵达|到|达|至)?\s*(?:地铁)?'+target)
        after = (target+r'\s*[｜:：]?\s*(?P<mode>'+mode_words+r')?\s*'
            r'(?:约|大约|只需|需|仅需|为)?\s*(?P<time>'+duration+r')\s*分钟')
        for pattern in (before, after):
            # A line break separates route entries. The last duration of one
            # entry must never be read as a prefix of the next station name.
            pattern = pattern.replace(r'\s', r'[^\S\r\n]')
            for match in re.finditer(pattern, output):
                minutes = time_key(match['time'])
                stated_mode = mode_aliases.get(match['mode'], match['mode'])
                options = [(mode, time) for mode, time in expected if time == minutes]
                supported = bool(options) and (stated_mode is None or any(
                    mode == stated_mode or stated_mode == '公共交通' and mode in ('公交', '地铁')
                    for mode, _time in options))
                if not supported:
                    issues.append('Изменены время или способ маршрута до станции: '+name)
    return list(dict.fromkeys(issues))


def _inventory_provenance_issues(output, brief):
    """A positive item list does not prove completeness or its room location."""
    inventory, vocabulary = brief['inventory'], brief['featureVocabulary']
    nonvisual = [row for row in inventory if not row.get('photoVisible')]
    issues = []
    for clause in re.split(r'[。！？!?；;\n]', output):
        universal_photo = re.search(
            r'(?:所有|全部|上述|以上|这些).{0,12}(?:物品|家具|电器|配置|设备).{0,15}(?:照片|实拍)'
            r'|(?:照片|实拍).{0,15}(?:所有|全部|上述|以上|这些).{0,8}(?:物品|家具|电器|配置|设备)', clause)
        negated_universal = re.search(r'(?:并非|不是|不代表|不能|没有).{0,10}(?:所有|全部|均|都|照片|实拍)', clause)
        if universal_photo and not negated_universal and (nonvisual or not inventory):
            issues.append('Не все перечисленные предметы подтверждены фотографиями')
        photo_assertion = r'(?:照片(?:中)?(?:可见|可以看到|可看到|显示|展示)|(?:在|从)?照片(?:中)?(?:可以|可|能)?(?:看到|见到))'
        # Keep item-scoped annotations intact: in a mixed list, a visible bed
        # does not imply a visible refrigerator. Only an unscoped assertion at
        # the list level applies to every item named in that same sentence.
        unscoped = re.sub(r'[（(]\s*'+photo_assertion+r'\s*[）)]', '', clause)
        if re.search(photo_assertion, unscoped) and any(_mentions(unscoped, row, vocabulary) for row in nonvisual):
            issues.append('Предметам из источника приписано неподтверждённое фото: '+', '.join(
                row['zh'] for row in nonvisual if _mentions(unscoped, row, vocabulary)))
        for row in nonvisual:
            if any(re.search(re.escape(name)+r'\s*[（(]\s*'+photo_assertion+r'\s*[）)]', clause) for name in _names(row)):
                issues.append('Предмету приписано неподтверждённое фото: '+row['zh'])
        # A scope such as "known items only" is different from asserting that
        # the apartment contains only the enumerated objects.
        exclusive = re.search(r'(?:家具|电器|物品|配置)(?:和|与|及|、|家具|电器|物品|配置)*'
            r'(?:仅|只)(?:有|包含|包括|提供|配有)', clause)
        explicitly_scoped = exclusive and re.search(r'(?:已确认|已核实|照片中可见|本次识别)(?:的)?\s*$', clause[:exclusive.start()])
        if exclusive and not explicitly_scoped or re.search(r'^(?:室内|房屋|公寓|这套房)?(?:仅|只)有', clause.strip()) and any(
                _mentions(clause, row, vocabulary) for row in inventory):
            issues.append('Перечень находок не подтверждает отсутствие остальной мебели и техники')
        complete_furnishing = re.search(r'全配(?:家具(?:和|与|及|、)?家电|家具|家电)'
            r'|(?:家具(?:和|与|及|、)?家电|家具|家电)(?:均|都)?全配'
            r'|(?:完整|齐全|全套)(?:的)?(?:配置|设备|家具|家电)'
            r'|(?:配置|设备|家具|家电)(?:均|都|很)?(?:完整|齐全)', clause)
        if complete_furnishing and not re.search(r'(?:不代表|不能证明|不能据此认定)\s*$', clause[:complete_furnishing.start()]):
            issues.append('Перечень находок не подтверждает полную комплектацию мебели и техники')
        # The live evidence contract carries item presence, not verified room
        # assignments. Category headings (厨房电器：冰箱) do not assert placement;
        # predicates (客厅有沙发 / 厨房配备冰箱) do, and must not be inferred.
        located = re.search(r'(客厅|卧室|厨房|卫生间|浴室|玄关|走廊|阳台|书房)'
            r'(?:内|里|中)?(?:(?:面积(?:为|是|[：:])?)?\s*\d+(?:\.\d+)?\s*(?:㎡|平方米|m²)[，,]\s*)?'
            r'(?:均|都|并)?(?:有|设有|摆放着|摆有|放有|放着|配有|配备|配|放置)[：:\s]*([^，,]*)', clause)
        if located and any(_mentions(located[2], row, vocabulary) for row in inventory):
            issues.append('Не подтверждено расположение предметов по комнатам: '+located[1])
        # A room-labelled list also assigns its items without an explicit 有:
        # 浴室：浴缸、置物架. Bathroom count/type can intervene before 含, so
        # retain that explicit room scope across commas, not across sentences.
        room_names = r'客厅|卧室|厨房|卫生间|浴室|卫浴|玄关|走廊|阳台|书房'
        room_scope = None
        for segment in re.split(r'[，,]', clause):
            if re.match(r'\s*(?:室内|房屋|公寓|全屋)(?:的)?(?:家具|家电|物品|配置)?'
                    r'(?:[：:\s]|有|配备|配有|设有|配置)', segment):
                room_scope = None
            labelled = re.search(r'('+room_names+r')(?:内|里|中)?\s*[：:]\s*(.*)', segment)
            introduced = re.search(r'(?:配备|设有|有|带)(?:独立)?('+room_names+r')\s*$', segment)
            contained = re.match(r'\s*(?:含|包含|包括|配备|配有|设有|有)\s*(.*)', segment)
            if labelled:
                room_scope, items = labelled[1], labelled[2]
            elif introduced:
                room_scope, items = introduced[1], ''
            else:
                items = contained[1] if room_scope and contained else ''
            if room_scope and any(_mentions(items, row, vocabulary) for row in inventory):
                issues.append('Не подтверждено расположение предметов по комнатам: '+room_scope)
    return list(dict.fromkeys(issues))


def _affirmed_source(pattern, source):
    positive = False
    for clause in re.split(r'[.!?;\n。；]', source):
        for match in re.finditer(pattern, clause, re.I):
            before = clause[max(0, match.start()-50):match.start()]
            if re.search(r'(?:\b(?:не|нет|без|никогда)\b)[^,;]{0,40}$|(?:不是|并非|没有|非|不)[^，；]{0,14}$', before, re.I):
                return False
            positive = True
    return positive


def validate_individual(draft, brief, *, part='all'):
    """Checks known rental relations; not a claim of general semantic proof."""
    fields, baseline = brief['fields'], brief['baseline']
    reference = baseline['body']
    output = draft['title']+'\n'+draft['body']
    # Dense confirmed inventory can legitimately need >280 Chinese characters.
    maximum = min(2400, max(800, len(re.findall(r'[\u3400-\u9fff]', reference))*2+200))
    def semantic_text(value):
        # 无线 is the lexical prefix "wireless", not a negation of every
        # appliance after the router in a natural comma-separated inventory.
        return value.replace('无线路由器', '路由器')
    semantic_baseline = {**baseline, 'body': semantic_text(reference)+('\n地铁' if brief.get('metro') else '')}
    semantic_draft = {key: semantic_text(value) for key, value in draft.items()}
    issues = validate_draft(semantic_draft, semantic_baseline, fields, max_body=maximum)
    if not str(fields.get('city') or '').strip() and '莫斯科' in output and '莫斯科' not in reference:
        issues.append('Город Москва не указан в фактах карточки; значение по умолчанию не подтверждает локацию')
    if fields.get('layout') == 'studio' and language_check(re.sub(r'Studio', '', draft['title'], flags=re.I), 'zh')['passed']:
        issues = [issue for issue in issues if not issue.startswith('Заголовок должен быть на китайском')]
    body = draft['body']
    if len(re.findall(r'[\u3400-\u9fff]', body)) < 40:
        issues.append('Модель не собрала полное описание квартиры')
    if re.search(r'(?:\[/?(?:title|body)|<\|im_|<think>|\{\s*"title")', output, re.I):
        issues.append('В ответ попала служебная разметка модели')
    if re.search(r'未知条件不承诺|已知不利条件不遗漏|只用核实资料|原始描述仅为参考|不要JSON|严格输出|所有正文用中文|只输出一个|不要复制输入|不能覆盖核实参数|清单中的每一种物品', output, re.I):
        issues.append('Модель переписала инструкцию вместо текста для арендатора')
    clauses = [_content_fingerprint(clause) for clause in re.split(r'[。！？!?；;\n]', body)]
    repeated = Counter(clause for clause in clauses if len(clause) >= 10)
    if any(count >= 3 for count in repeated.values()) or re.search(r'([\u3400-\u9fff]{2,12})(?:[、,，\s]+\1){3,}', body):
        issues.append('Модель многократно повторила одну и ту же фразу или список')
    if re.search(r'#[^\s#]+', output):
        issues.append('Теги должны оставаться в отдельном поле')
    if re.search(r'物品清单|配置清单|核实资料|已确认资料', draft['title']):
        issues.append('Заголовок должен описывать квартиру, а не процесс составления описи')
    if re.search(r'дизайнерск\w*|диайнерск\w*|косметическ\w*|евроремонт|без ремонта', output, re.I):
        issues.append('Тип ремонта нужно написать по-китайски, без русского фрагмента')
    inventory = brief['inventory']; vocabulary = brief['featureVocabulary']
    accepted_ids = {row['id'] for row in inventory}
    missing = [row['zh'] for row in inventory if not _mentions(body, row, vocabulary)]
    if missing:
        issues.append('Пропущены подтверждённые предметы: '+', '.join(missing))
    unsupported = [row['zh'] for row in vocabulary if row['id'] not in accepted_ids and _mentions(output, row, vocabulary)]
    if unsupported:
        issues.append('Не подтверждены предметы: '+', '.join(unsupported))
    for row in inventory:
        counts = _object_counts(output, row, vocabulary)
        if row.get('count'):
            expected = (float(row['count']), row.get('countKind') == 'minimum')
            if expected not in counts or any(item != expected for item in counts):
                issues.append('Изменено подтверждённое количество или его нижняя граница: '+row['zh'])
        elif counts:
            issues.append('Количество этого предмета не подтверждено: '+row['zh'])
    if fields.get('price'):
        rent_amounts = _labelled_amounts(body, '月租金?|每月租金|租金')
        if rent_amounts != {round(float(fields['price']), 6)}:
            issues.append('Цена должна быть указана именно как месячная аренда с валютой')
    if fields.get('deposit') not in (None, ''):
        if float(fields['deposit']) == 0:
            if not re.search(r'(?:无(?:需)?|免|不需要)押金', body) or _labelled_amounts(body, '押金|保证金') - {0}:
                issues.append('Не сохранено отсутствие залога')
        elif _labelled_amounts(body, '押金|保证金') != {round(float(fields['deposit']), 6)}:
            issues.append('Не сохранена сумма залога с правильным назначением')
    if fields.get('floor') and fields.get('floors'):
        pair = str(fields['floor'])+'/'+str(fields['floors'])+'层'
        floors, totals = _floor_roles(body)
        if floors != {float(fields['floor'])} or totals != {float(fields['floors'])}:
            issues.append('Не сохранены этаж и этажность: '+pair)
    layout = brief['layout']
    if fields.get('layout') == 'studio':
        if not re.search(r'Studio|studio|开放式|开间', output):
            issues.append('Не сохранена открытая планировка студии')
        if re.search(r'独立卧室|独立\S{0,2}居|[一二两三四]室[一二两]厅|[一二两三四]间卧室', output):
            issues.append('Студия ошибочно описана как квартира с отдельной спальней')
    elif layout and layout not in output:
        issues.append('Не сохранён подтверждённый тип планировки: '+layout)
    # Every stated quantity must still come from the checked fields. Optional
    # secondary dimensions/routes may be omitted; their values cannot migrate
    # to another quantity, payment role or unit.
    if _quantities(output)-_quantities(reference):
        issues.append('Изменено назначение суммы, площади или времени')
    for field, role in (('kitchen', '厨房'), ('bedroomArea', '卧室'), ('living', '居住面积')):
        values = re.findall(role+r'(?:面积)?\s*[：:]?\s*(\d+(?:\.\d+)?)\s*(?:㎡|平方米|m²)', body)
        if values and (not fields.get(field) or any(abs(float(value)-float(fields[field]))>.001 for value in values)):
            issues.append('Изменена площадь помещения: '+role)
    if '物业及水电等费用另付' in reference and not re.search(r'(?:物业|公共|杂费).{0,12}(?:另付|自付|另计|额外)|(?:另付|自付|另计).{0,12}(?:物业|公共|杂费)', body):
        issues.append('Пропущены отдельно оплачиваемые коммунальные расходы')
    if '仅水电按表计费' in reference and not (re.search(r'水电.{0,12}(?:表|自付|另付|另计)', body) and re.search(r'物业费.{0,8}(?:包含|已含|包含在|包括)', body)):
        issues.append('Не сохранено: коммунальные включены, счётчики оплачиваются отдельно')
    if '租金包含各项费用' in reference and not re.search(r'(?:租金|费用|水电).{0,12}(?:全包|包含|已含|包括)|全包', body):
        issues.append('Пропущено включение коммунальных расходов в аренду')
    for key, subject in (('petPolicy', '宠物'), ('childrenPolicy', '孩子'), ('smokingPolicy', '吸烟'), ('guestPolicy', '访客'), ('subletPolicy', '转租'), ('shortTermPolicy', '短租')):
        state = fields.get(key)
        if state == 'no' and not re.search(r'(?:不可|不允许|不接受|禁止|不得|不能|不接待|不适合|仅限不).{0,10}'+subject+'|'+subject+r'.{0,8}(?:不允许|不可|禁止)', body):
            issues.append('Пропущен известный запрет: '+subject)
        if state in ('negotiable', 'conflict') and subject in reference and not re.search(subject+r'.{0,16}(?:协商|确认)|(?:协商|确认).{0,12}'+subject, body):
            issues.append('Условие требует согласования: '+subject)
    if fields.get('registrationPolicy') == 'not_available' and not re.search(r'(?:房东|业主).{0,8}(?:不提供|不办理|不能).{0,8}(?:落地签|登记|注册)|(?:落地签|登记|注册).{0,8}(?:不提供|不办理)', body):
        issues.append('Не сохранён отказ собственника от регистрации')
    for key, label in (('prepayMonths', '预付'), ('contractMonths', '合同'), ('minMonths', '最低租期')):
        if fields.get(key) and (key != 'minMonths' or fields.get('leaseTerm') == 'long'):
            count = str(fields[key])
            labels = r'(?:最低租期|租期(?:最低|至少))' if key == 'minMonths' else label
            if not re.search(labels+r'.{0,15}'+re.escape(count)+r'\s*个月', body):
                issues.append('Пропущен известный срок: '+label)
    for restriction in brief.get('sourceRestrictions', []):
        if restriction['kind'] == 'building-demolition':
            if not re.search(r'(?:楼|楼栋|建筑|房屋).{0,18}(?:更新|改造|拆迁).{0,12}计划', body) or not re.search(r'(?:将|会|拟|计划).{0,6}(?:被)?拆(?:除|迁)', body):
                issues.append('Пропущено прямое уведомление о сносе здания')
            if re.search(r'不(?:会|需|将|用).{0,6}拆(?:除|迁)', output):
                issues.append('Изменён смысл уведомления о сносе здания')
            continue
        if restriction['kind'] == 'adjacent-rooms':
            if not re.search(r'房间.{0,5}(?:相连|连通)|连通式房间', body) or re.search(r'房间.{0,5}(?:独立|分开)', output):
                issues.append('Не сохранены смежные комнаты')
            continue
        if restriction['kind'] == 'internet-extra':
            fee = r'(?:网络(?:费用|费)|网费|宽带费)'
            if not re.search(fee+r'.{0,10}(?:另付|另计|单独支付|自付|额外支付)|(?:另付|另计|单独支付)\s*'+fee, body):
                issues.append('Пропущена отдельная оплата интернета: 网络费用另付。')
            if re.search(fee+r'.{0,8}(?:已含|包含|免费|全包|免收)', output):
                issues.append('Отдельная оплата интернета ошибочно названа включённой')
            continue
        if restriction['kind'] == 'occupancy-maximum':
            subject = r'(?:名|位)?(?:成人|成年人)' if restriction['adultsOnly'] else r'(?:名|位)?(?:人|租客)'
            if not re.search(r'(?:最多|至多|不超过)\s*'+str(restriction['maximum'])+r'\s*'+subject, body):
                issues.append('Пропущено ограничение числа жильцов: '+restriction['text'])
            continue
        minimum, maximum = restriction['minimum'], restriction['maximum']
        interval = str(minimum)+r'\s*[-–—至到]\s*'+str(maximum)+r'\s*(?:位|名)?(?:人|租客)'
        bounded = str(maximum)+r'\s*(?:位|名)?(?:人|租客)'
        stated = re.search(interval, body) or (minimum == 1 and re.search(r'(?:最多|至多|不超过)\s*'+bounded, body))
        if not stated:
            issues.append('Пропущено ограничение числа жильцов: '+restriction['text'])
    issues.extend(_payment_idiom_issues(output, fields))
    issues.extend(_route_claim_issues(output, reference))
    issues.extend(_inventory_provenance_issues(output, brief))
    if payment_periods(body)-payment_periods(reference):
        issues.append('В тексте появилось неподтверждённое условие оплаты')
    if re.search(r'接送|接机|接站|搬运行李', output) and 'pickup' not in brief.get('services', []):
        issues.append('Услуга встречи или трансфера не подтверждена для этой карточки')
    if re.search(r'翻译(?:合同|文件)|合同翻译|签约陪同|全程陪同|提供接送|协助搬家', output) and 'support' not in brief.get('services', []):
        issues.append('Дополнительная услуга студии не подтверждена для этой карточки')
    if brief.get('photoConflicts') and not re.search(r'(?:配置|物品|家具).{0,18}(?:确认|核实)|(?:确认|核实).{0,18}(?:配置|物品|家具)', body):
        issues.append('Фото расходится с источником: актуальную комплектацию нужно подтвердить')
    source = brief['facts']['公寓原始描述']
    qualification_sources = {
        '全新': r'(?:全新|нов(?:ая|ый|ое|ые)\s+(?:мебель|квартир|техник|ремонт)|никто\s+не\s+жил)',
        '首次出租': r'(?:首次出租|сда[её]тся\s+впервые|впервые\s+сда)',
        **{term: r'(?:采光(?:充足|很好|良好|极佳)|明亮|светл(?:ая|ое|ые)|солнечн)'
            for term in ('采光充足', '采光很好', '采光良好', '明亮通透', '阳光充足')},
        '落地窗': r'(?:落地窗|панорамн.{0,12}окн|окна\s+в\s+пол)',
        '朝南': r'(?:朝南|окна.{0,16}юг)', '森林': r'(?:森林|лес(?:ом|а|у|ной|опарк)\b)',
        '精装修': r'(?:精装修|дизайнерск.{0,12}ремонт|евроремонт)',
    }
    for term in ('高端', '豪华', '全新', '首次出租', '采光很好', '采光充足', '采光良好', '明亮通透', '阳光充足',
        '拎包', '家具齐全', '家电齐全', '设备齐全', '配套齐全', '安全', '双电梯', '森林', '地暖',
        '落地窗', '朝南', '随时入住', '立即入住', '随时看房', '包落地', '包登记', '学生优先', '只要女生', '男女不限', '精装修'):
        if term in output and term not in reference:
            pattern = qualification_sources.get(term)
            if not pattern or not _affirmed_source(pattern, source):
                issues.append('Не подтверждено рекламное утверждение: '+term)
    if re.search(r'人民币|美元|欧元|港币|英镑|\b(?:CNY|USD|EUR)\b|[￥¥$]|\d+(?:[.,]\d+)?\s*(?:万元|元)', output, re.I):
        issues.append('Для этой карточки разрешена только подтверждённая валюта RUB')
    return list(dict.fromkeys(issues))


def _claim_parts(directory, group_id, draft, parts, previous=None):
    from advertising_styles import variant_fingerprint
    from learning_memory import LearningMemory
    # Match the existing history schema, but reserve only parts actually changed.
    # A body rewrite may correctly preserve the user's manual title and tags.
    with LearningMemory(directory)._db() as db:
        db.execute('CREATE TABLE IF NOT EXISTS advertising_history '
            '(group_key TEXT NOT NULL, kind TEXT NOT NULL, fingerprint TEXT NOT NULL, '
            'PRIMARY KEY(group_key,kind,fingerprint))')
        db.execute('BEGIN IMMEDIATE')
        group = hashlib.sha256(str(group_id).encode()).hexdigest()
        values = [('title' if part == 'title' else 'description', variant_fingerprint(draft[part])) for part in parts]
        if any(db.execute('SELECT 1 FROM advertising_history WHERE group_key=? AND kind=? AND fingerprint=?', (group, kind, fingerprint)).fetchone() for kind, fingerprint in values):
            return False
        for part in parts:
            prior = (previous or {}).get(part)
            if isinstance(prior, str) and prior.strip():
                db.execute('INSERT OR IGNORE INTO advertising_history(group_key,kind,fingerprint) VALUES(?,?,?)',
                    (group, 'title' if part == 'title' else 'description', variant_fingerprint(prior)))
        for kind, fingerprint in values:
            db.execute('INSERT INTO advertising_history(group_key,kind,fingerprint) VALUES(?,?,?)', (group, kind, fingerprint))
        return True


def generate_preview(directory, data, *, status=None, cancel=None):
    from advertising_styles import next_style
    from job_progress import emit
    from language_service import _profile
    from learning_memory import LearningMemory
    started = time.monotonic()
    part = data.get('part', 'all')
    if part not in ('title', 'body', 'all'):
        raise ValueError('Индивидуальный ИИ изменяет заголовок, описание или оба поля')
    brief = build_brief(data.get('fields'), _profile(directory).get('rules', {}))
    group = data.get('groupId') or brief['factsHash']
    if not isinstance(group, str) or not 1 <= len(group) <= 2000:
        raise ValueError('Неверная группа истории генерации')
    current = data.get('output') if isinstance(data.get('output'), dict) else {}
    current = {'title': _clean(current.get('title', ''), 500), 'body': _clean(current.get('body', ''), 20000)}
    parts = ('title', 'body') if part == 'all' else (part,)
    memory = LearningMemory(directory)
    histories = {key: {_content_fingerprint(value) for value in memory.recent_generated(group, limit=30, kind='title' if key == 'title' else 'description')+[current[key]] if value} for key in parts}
    style = next_style(directory, group)
    meta = {'status': 'blocked', 'autoApply': False, 'fallback': False, 'modelAccepted': False,
        'textSource': 'model', 'generationMode': 'individual-model-prose', 'contract': CONTRACT,
        'modelContribution': 'full-publication-prose', 'factualTextSource': 'checked-listing-and-accepted-photo-evidence',
        'factsHash': brief['factsHash'], 'inventoryCount': len(brief['inventory']),
        'photoInventoryCount': sum(bool(row.get('photoVisible')) for row in brief['inventory']),
        'sourceContextCharacters': brief['sourceContextCharacters'], 'sourceContextTruncated': brief['sourceContextTruncated'],
        'styleId': style['id'], 'variation': style['label'], 'part': part}
    omitted_source_fields = ['renovation'] if '\ufffd' in str(brief['fields'].get('renovation') or '') else []
    meta['omittedCorruptSourceFields'] = omitted_source_fields
    issues = []
    rejected_draft = ''
    for attempt in range(3):
        if cancel is not None and cancel.is_set():
            return {**meta, 'status': 'cancelled', 'warnings': ['Генерация отменена; текущий текст сохранён.']}
        emit(status, 'ИИ пишет объявление по описанию квартиры и подтверждённым предметам', stage='generate',
            ai={'backend': 'mlx', 'operation': 'text', 'phase': 'preparing', 'tokenBased': True})
        request = generation_request(brief, seed=style['seed']+attempt, instruction=style['instruction'],
            previous_title=current['title'], previous_body=current['body'], problems=issues, rejected_draft=rejected_draft)
        result = _model(request, directory, deadline=started+55, status=status, cancel=cancel)
        meta.update({key: result[key] for key in ('model', 'backend', 'ai') if key in result})
        meta['attempts'] = attempt+1
        if cancel is not None and cancel.is_set():
            return {**meta, 'status': 'cancelled', 'warnings': ['Генерация отменена; текущий текст сохранён.']}
        if result.get('status') != 'review':
            issues = [result.get('message') or 'Локальная модель не вернула готовый ответ']
            meta['status'] = result.get('status', 'blocked')
            break
        rejected_draft = result.get('text', '')
        try:
            draft = parse_individual_text(rejected_draft)
            issues = validate_individual(draft, brief, part=part)
            if result.get('stopReason') == 'length' or result.get('ai', {}).get('generatedTokens', 0) >= request['maxTokens']:
                issues.insert(0, 'Модель достигла лимита токенов: ответ не подтверждён как завершённый')
        except (ValueError, TypeError) as error:
            issues = ['Модель не вернула завершённое объявление в формате title/body: '+str(error)[:140]]
        if not issues:
            if cancel is not None and cancel.is_set():
                return {**meta, 'status': 'cancelled', 'warnings': ['Генерация отменена; текущий текст сохранён.']}
            if any(_content_fingerprint(draft[key]) in histories[key] for key in parts):
                issues = ['Модель повторила уже использованный текст выбранного поля']
            elif not _claim_parts(directory, group, draft, parts, previous=current):
                issues = ['Этот вариант уже использован; текущий текст сохранён']
            else:
                for key in parts:
                    try:
                        memory.record('title' if key == 'title' else 'description', {'text': draft[key], 'language': 'zh'}, source='generated', group_id=group)
                    except (OSError, ValueError, sqlite3.Error):
                        meta['memoryWarning'] = 'Черновик создан; подробная история не записана'
                return {**meta, **draft, 'status': 'review', 'modelAccepted': True,
                    'tags': brief['baseline'].get('tags', ''), 'elapsedSeconds': time.monotonic()-started,
                    'warnings': [*(['Повреждённое значение ремонта в источнике не включено в текст; уточните его в карточке.'] if omitted_source_fields else []),
                        'Текст написала локальная модель. Проверьте китайскую подачу перед публикацией.']}
        if time.monotonic()-started > 35:
            break
    return {**meta, 'status': meta['status'] if meta['status'] != 'review' else 'blocked',
        'elapsedSeconds': time.monotonic()-started,
        'warnings': [*issues[:10], 'Ответ ИИ не принят. Текущий текст сохранён; шаблон вместо него не подставлен.']}
