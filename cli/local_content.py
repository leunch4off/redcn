"""Grounded local language drafts. Model prose requires editorial acceptance."""
import hashlib
import json
import re
import secrets
import sqlite3
import subprocess
import time
import unicodedata

from learning_memory import LearningMemory
from text_quality import (parse_card, language_check, polarity_issues, translation_checks,
    payment_periods, LISTING_SYSTEM, TRANSLATION_SYSTEM)
from rental_conditions import fee_issues, metro_proximity_issues

FACT_KEYS = ('city', 'district', 'districtZh', 'complex', 'metro', 'metroLine', 'walk',
    'area', 'rooms', 'floor', 'floors', 'price', 'priceCurrency', 'pricePeriod',
    'deposit', 'depositCurrency', 'layout', 'separate', 'bedrooms', 'livingRooms',
    'children', 'pets', 'petPolicy', 'childrenPolicy', 'registration', 'serviceConfirmations',
    'features', 'featureCounts', 'photoEvidence', 'photoRejectedFeatures', 'sourceAudit',
    'utilities', 'ceiling', 'balconies', 'loggias', 'universityRoutes',
    'kitchen', 'living', 'bedroomArea', 'occupants', 'registrationMonths',
    'registrationPeople', 'shortTerm', 'minMonths', 'shortTermPolicy',
    'smokingPolicy', 'guestPolicy', 'subletPolicy', 'leaseTerm', 'contractMonths', 'prepayMonths')
VOICES = ('自然简洁，少用表情，用短段落', '像认真介绍房子的朋友，先讲位置，再讲生活空间',
    '先说预算与空间，用清楚的小标题', '先写一个真实亮点，段落疏朗，最多三个表情',
    '不写套话，像房源笔记，先讲已确认的家具与布局')


def _clean(value, maximum=20_000):
    if not isinstance(value, str) or len(value) > maximum or '\x00' in value:
        raise ValueError('Текст превышает допустимый размер')
    # Contact details are not training context or model instructions.
    return re.sub(r'https?://\S+|\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b|(?:微信|wechat|telegram|телефон)\s*[:：=]\s*\S+', '', value, flags=re.I).strip()


def _model(request, directory, *, deadline=None, status=None, cancel=None):
    from local_text_model import generate
    return generate(request, directory, deadline=deadline, status=status, cancel=cancel)


def _fallback_translate(text, glossary, directory, timeout):
    """The existing independent RU→EN→ZH route remains a review candidate."""
    from language_service import machine_translate
    from learning_scheduler import model_resource
    rules={row['ru']:row['zh'] for row in glossary}
    started=time.monotonic()
    try:
        with model_resource(directory,kind='foreground',timeout=min(3,max(0,timeout-1))):
            remaining=timeout-(time.monotonic()-started)
            if remaining<1:return None
            return machine_translate(text,rules,remaining)
    except (OSError,ValueError,subprocess.TimeoutExpired):
        return None


def _profile_glossary(directory,text,direction):
    from language_service import _profile
    rules=_profile(directory).get('rules',{})
    if not isinstance(rules,dict):return []
    matches=[]
    for ru,zh in rules.items():
        if not isinstance(ru,str) or not isinstance(zh,str) or not ru.strip() or not zh.strip():continue
        key=ru if direction=='ru-zh' else zh
        found=(re.search(r'(?<!\w)'+re.escape(key)+r'(?!\w)',text,re.I) is not None
            if direction=='ru-zh' else key in text)
        if found:matches.append({'ru':ru,'zh':zh,'source':'active-profile-rule'})
    return sorted(matches,key=lambda row:-len(row['ru'] if direction=='ru-zh' else row['zh']))[:8]


def _parse(text):
    value = parse_card(text)
    return {'title': _clean(value['title'], 240), 'body': _clean(value['body'], 6000)}


def _content_fingerprint(text):
    return ''.join(character for character in unicodedata.normalize('NFKC',text).casefold() if character.isalnum())


def _numeral_text(text):
    text=re.sub(r'(?<=\d),(?=\d{3}(?:\D|$))','',text)
    # Chinese letters are word characters too: 8.5W独立两居 is the same rent
    # as 85000, not a new unrelated 8.5. Do not consume a Latin word suffix.
    text=re.sub(r'(?<=\d)\s*[wW](?![A-Za-z0-9_])','万',text)
    return text.replace(',','.')


def _numeric_tokens(text):
    text=re.sub(r'(\d+(?:\.\d+)?)\s*万',lambda m:format(float(m[1])*10000,'.12g'),_numeral_text(text))
    return {format(float(value),'.12g') for value in re.findall(r'\d+(?:\.\d+)?',text)}


def _quantities(text):
    values=set()
    for number,multiplier,unit in re.findall(r'(\d+(?:\.\d+)?)\s*(万)?\s*(卢布|₽|㎡|平方米|m²|分钟)?',_numeral_text(text)):
        if not multiplier and not unit:continue
        kind='area' if unit in ('㎡','平方米','m²') else 'minutes' if unit=='分钟' else 'money'
        values.add((kind,format(float(number)*(10000 if multiplier else 1),'.12g')))
    return values


def _title_language(title,fields):
    # A station/complex may retain its exact source spelling when no Chinese
    # rendering is available. Only explicit field values receive that exception;
    # arbitrary Russian/English marketing text still fails the language check.
    remaining=title
    for key in ('metro','complex','district','city'):
        name=(fields or {}).get(key)
        if isinstance(name,str) and len(name)>=2 and re.search(r'[A-Za-zА-Яа-яЁё]',name):
            remaining=remaining.replace(name,'')
    return language_check(remaining,'zh')['passed']


def validate_draft(draft, baseline, fields=None, *, max_body=150):
    """Reject conspicuous changed figures/claims; still not a full semantic proof."""
    output = draft['title']+'\n'+draft['body']
    reference = '\n'.join([*baseline.get('titles', []), baseline.get('body', '')])
    issues = []
    if not draft['title'] or not draft['body'] or not language_check(draft['body'],'zh')['passed']:
        issues.append('Модель не вернула полный китайский текст')
    if not _title_language(draft['title'],fields):
        issues.append('Заголовок должен быть на китайском; исходное имя станции или ЖК можно сохранить')
    if len(re.findall(r'[\u3400-\u9fff]',draft['title'])) > 45 or len(re.findall(r'[\u3400-\u9fff]',draft['body'])) > max_body:
        issues.append(f'Сократите заголовок до 45, а описание до {max_body} китайских знаков')
    if _numeric_tokens(output)-_numeric_tokens(reference):
        issues.append('В ИИ-черновике есть числа, отсутствующие в проверенном тексте')
    if _quantities(output)-_quantities(reference):
        issues.append('Проверьте соответствие суммы, площади и времени их единицам')
    # A low-memory model can invent permissions, equipment, commute or fees.
    claims = ('冰箱', '洗衣机', '洗碗机', '烤箱', '微波炉', '沙发', '双人床', '衣柜',
        '书桌', '空调', '电视', '钢琴', '电脑', '地暖', '净水', '门禁', '监控',
        '可养宠', '可带宠物', '可带孩子', '免费', '全新装修', '拎包入住', '家电齐全',
        '步行', '公交', '直达', '落地签', '登记')
    for claim in claims:
        if claim in output and claim not in reference:
            issues.append('Не подтверждена формулировка: '+claim)
    issues.extend(polarity_issues(reference,output))
    issues.extend(fee_issues(reference, output))
    issues.extend(metro_proximity_issues(reference, output, fields))
    if re.search(r'佣金|中介费|电话|微信|微訊|v信|https?://|\b(?:wechat|weixin|whatsapp|telegram|vx)\b', output, re.I):
        issues.append('В черновик попал контакт или комиссия')
    if re.search(r'售价|出售|购买|买房',output) and not re.search(r'售价|出售|购买|买房',reference):
        issues.append('Аренда ошибочно описана как продажа или покупка')
    fields=fields or {}
    if fields.get('priceCurrency','RUB')=='RUB' and fields.get('price'):
        amounts=[float(number)*(10000 if multiplier else 1) for number,multiplier,unit in re.findall(r'(\d+(?:\.\d+)?)\s*(万)?\s*(卢布|₽)?',_numeral_text(output)) if multiplier or unit]
        if not any(abs(value-float(fields['price']))<.001 for value in amounts):
            issues.append('В черновике не сохранена исходная цена с единицей оплаты')
    if fields.get('area'):
        areas=[float(value) for value in re.findall(r'(\d+(?:\.\d+)?)\s*(?:㎡|平方米|m²)',output)]
        if not any(abs(value-float(fields['area']))<.001 for value in areas):
            issues.append('В черновике не сохранена исходная площадь')
    if '莫斯科' in reference and '莫斯科' not in output:
        issues.append('В черновике пропущена локация Москва')
    return list(dict.fromkeys(issues))


def generate_preview(directory, data, *, status=None, cancel=None):
    if data.get('styleMode') == 'advertising-structure':
        return _structured_preview(directory, data, status=status, cancel=cancel)
    if data.get('styleMode') == 'advertising':
        return _advertising_preview(directory, data, status=status, cancel=cancel)
    from language_service import node_language, _profile
    from fact_ledger import validate_listing_fields
    fields = data.get('fields')
    if not isinstance(fields, dict):
        raise ValueError('Сначала выберите карточку объявления')
    validate_listing_fields(fields)
    grounded = {key: fields[key] for key in FACT_KEYS if key in fields}
    profile = _profile(directory)
    # Existing renderer resolves confirmed photo evidence and publication policy.
    baseline = node_language({'action': 'generate', 'fields': grounded,
        'rules': profile.get('rules', {}), 'prefs': {'benefits': '', 'contact': '', 'brandTags': ''}})
    if baseline.get('problems'):
        raise ValueError(' · '.join(baseline['problems']))
    reference = _clean('\n'.join([*(baseline.get('titles') or [])[:1], baseline.get('body', '')]), 12000)
    memory = LearningMemory(directory)
    group_id=data.get('groupId') or hashlib.sha256(reference.encode()).hexdigest()
    recent=memory.recent_generated(group_id)
    recent_titles=memory.recent_generated(group_id,kind='title')
    current=data.get('output') if isinstance(data.get('output'),dict) else {}
    prior=current.get('body','')
    prior_title=current.get('title','')
    recent.extend([prior] if isinstance(prior,str) and prior else [])
    recent_titles.extend([prior_title] if isinstance(prior_title,str) and prior_title else [])
    query = ' '.join(str(fields.get(key, '')) for key in ('metro', 'district', 'layout')).strip()
    examples = memory.retrieve(query or '莫斯科租房', limit=3)
    style_examples = [row for row in examples if row['kind'] in ('title', 'description')][:2]
    style = secrets.choice(VOICES)
    candidates = re.findall(r'#[\w\u3400-\u9fff-]+', baseline.get('tags', ''))[:30]
    ranking = memory.rank_tags(candidates, limit=8)
    messages = [{'role': 'system', 'content': LISTING_SYSTEM+' 标题和正文都不能只改表情或标点，也不能重复给出的历史版本。'},
        {'role': 'user', 'content': json.dumps({'verifiedText': reference[:1200], 'style': style,
            'previousTextToAvoid': _clean(prior, 20000)[:250],
            'previousTitleToAvoid': _clean(prior_title,500)[:120],
            'recentTitlesToAvoid':[title[:120] for title in list(dict.fromkeys(recent_titles))[:5]],
            'approvedStyleExamples': [row['payload']['text'][:150] for row in style_examples],
            'variation': secrets.token_hex(4)}, ensure_ascii=False)}]
    begin = time.monotonic()
    for attempt in range(2):
        result = _model({'messages': messages, 'maxTokens': 420, 'temperature': .75 if attempt==0 else .25},
            directory, deadline=begin+55, status=status, cancel=cancel)
        meta = {key: result[key] for key in ('model', 'autoApply', 'message') if key in result}
        meta.update(elapsedSeconds=time.monotonic()-begin, attempts=attempt+1)
        if result.get('status') != 'review':
            return {**meta, 'status': result.get('status', 'unavailable'), 'warnings': [result.get('message', 'Модель недоступна')]}
        try:
            draft = _parse(result.get('text', ''))
            issues = validate_draft(draft, baseline, grounded)
            if _content_fingerprint(draft['body']) in {_content_fingerprint(text) for text in recent}:
                issues.append('Описание повторяет предыдущую версию; запросите новую подачу')
            if _content_fingerprint(draft['title']) in {_content_fingerprint(text) for text in recent_titles}:
                issues.append('Заголовок повторяет предыдущую версию; смена смайликов и пунктуации не считается новым названием')
        except (ValueError, TypeError):
            draft = {'title':'','body':''}
            issues = ['Модель не вернула корректный формат. Карточка сохранена без изменений.']
        if not issues or time.monotonic()-begin>45:
            break
        messages[0]['content'] += ' 特别注意：只返回合法JSON，不得添加新数字或单位。'
    if not issues:
        try:
            memory.record('title',{'text':draft['title'],'language':'zh'},source='generated',group_id=group_id,
                          derived_from=[row['id'] for row in style_examples])
            memory.record('description',{'text':draft['body'],'language':'zh'},source='generated',group_id=group_id,
                          derived_from=[row['id'] for row in style_examples])
        except (OSError,ValueError,sqlite3.Error):
            meta['memoryWarning']='Черновик создан; история генерации не записана'
    return {**meta, **draft, 'tags': ' '.join(row['tag'] for row in ranking), 'tagRanking': ranking,
        'status': 'blocked' if issues else 'review', 'autoApply': False,
        'warnings': [*issues, 'Проверьте смысл и китайский язык перед применением. Проверка чисел не подтверждает все утверждения.'],
        'variation': style, 'factsHash': hashlib.sha256(reference.encode()).hexdigest()}


def _advertising_money_roles(text):
    """Read labelled amounts, not '先了解面积、租金与入住条件：50㎡'."""
    result = set()
    normalized = _numeral_text(text)
    pattern = (r'(押金|租金|月租|房租)(?:(?:\s|[:：])|约|为|是|每月|参考|卢布|费用|金额|需支付|需|支付|共计|共){0,20}'
               r'(\d+(?:\.\d+)?\s*万?)')
    for match in re.finditer(pattern, normalized):
        for value in _numeric_tokens(match[2]):
            result.add(('deposit' if match[1] == '押金' else 'rent', value))
    return result


def _with_advertising_conditions(rendered, fields):
    """Carry explicit rental restrictions into the advertising fact context.

    Legacy positive-only copy may omit these fields; absence of a checkbox is
    not a prohibition. Studio registration assistance is a separate service.
    """
    clauses = []
    pets = fields.get('petPolicy')
    children = fields.get('childrenPolicy')
    if pets == 'no':
        clauses.append('不接受宠物。')
    elif pets == 'negotiable':
        clauses.append('携带宠物需事先协商。')
    elif pets == 'conflict':
        clauses.append('宠物条件待确认。')
    if children == 'no':
        clauses.append('不接受带孩子入住。')
    elif children == 'conflict':
        clauses.append('儿童入住条件待确认。')
    body = rendered.get('body', '')
    return {**rendered, 'body': '\n'.join([body, *(clause for clause in clauses if clause not in body)]).strip()}


def advertising_baseline(fields, rules=None):
    """The same public-fact projection is used by preview and weight acceptance."""
    from fact_ledger import validate_listing_fields
    from language_service import node_language
    if not isinstance(fields, dict):
        raise ValueError('Сначала выберите карточку объявления')
    validate_listing_fields(fields)
    grounded = {key: fields[key] for key in FACT_KEYS if key in fields}
    baseline = _with_advertising_conditions(node_language({'action': 'generate', 'fields': grounded,
        'rules': rules or {}, 'prefs': {'benefits': '', 'contact': '', 'brandTags': ''}}), grounded)
    if baseline.get('problems'):
        raise ValueError(' · '.join(baseline['problems']))
    return grounded, baseline


def advertising_messages(reference, instruction, *, seed, previous_body='', previous_title='', recent_titles=()):
    """One initial inference contract, shared with independent model probes."""
    from advertising_styles import ADVERTISING_SYSTEM
    return [{'role': 'system', 'content': ADVERTISING_SYSTEM},
            {'role': 'user', 'content': json.dumps({'verifiedText': reference, 'style': instruction,
                'previousTextToAvoid': previous_body[:300], 'previousTitleToAvoid': previous_title[:120],
                'recentTitlesToAvoid': list(dict.fromkeys(recent_titles))[:6],
                'variation': str(seed)}, ensure_ascii=False)}]


def _advertising_issues(draft, baseline, fields, *, max_body=280):
    """Advertising must keep stated fees and figures, not just avoid new ones."""
    # A bedroom section containing a desk is called "床与收纳" by the existing
    # renderer. That heading is not evidence of a bed in the apartment.
    def factual_prose(value):
        value = re.sub(r'(?m)^[^\w\u3400-\u9fff]*床与收纳(?:清单|信息)?\s*$', '', value)
        return value + ('\n地铁' if fields.get('metro') else '')
    fact_baseline = {**baseline, 'body': factual_prose(baseline.get('body', ''))}
    fact_draft = {**draft, 'body': factual_prose(draft['body'])}
    issues = validate_draft(fact_draft, fact_baseline, fields, max_body=max_body)
    reference = '\n'.join([*(baseline.get('titles') or [])[:1], baseline.get('body', '')])
    output = draft['title']+'\n'+draft['body']
    missing = _quantities(reference)-_quantities(output)
    if missing:
        issues.append('В рекламном тексте пропущены проверенные суммы, площадь или время')
    issues.extend(fee_issues(reference, output, require_mentions=True))
    issues.extend(polarity_issues(factual_prose(reference), factual_prose(output), require_mentions=True))
    if _advertising_money_roles(reference) != _advertising_money_roles(output):
        issues.append('В рекламном тексте изменено назначение суммы аренды или залога')
    if payment_periods(reference) != payment_periods(output):
        issues.append('В рекламном тексте изменён или пропущен период оплаты')
    # Compact models are allowed to vary the introduction and structure. Keep
    # restriction/uncertainty clauses verbatim (ignoring punctuation) because
    # the shared concept parser does not cover every rental-contract condition.
    normalized_output = _content_fingerprint(output)
    condition_output = _content_fingerprint(output.replace('尚未确认', '待确认'))
    for clause in re.split(r'[。；;\n]+', baseline.get('body', '')):
        if re.search(r'禁止|不得|不接待|不吸烟|不接受|不含|另付|另计|仅限|需.{0,20}确认|待确认|协商|至少|最少|个月起|预付|合同期限|最低租期|可入住\s*\d+\s*人', clause):
            normalized_clause = _content_fingerprint(clause.replace('尚未确认', '待确认'))
            if normalized_clause and normalized_clause not in condition_output:
                issues.append('В рекламном тексте изменено ограничение или условие, требующее подтверждения')
    def counted_terms(value):
        units = re.findall(r'(\d+(?:\.\d+)?)\s*(个月|人|楼|层|间|个|套|张|台|把|扇|部)', _numeral_text(value))
        return {('floor' if unit in ('楼', '层') else unit, format(float(number), '.12g')) for number, unit in units}
    if counted_terms(reference) != counted_terms(output):
        issues.append('В рекламном тексте изменены сроки, этаж или подтверждённое количество')
    # Keep named transport/college rows from the renderer. A generic "metro"
    # mention does not establish that the model retained the correct station.
    for line in baseline.get('body', '').splitlines():
        route = re.match(r'^\s*[▪•\-]\s*([^|｜：:\n]+)[|｜].*(?:分钟|时间待确认)', line)
        if route and _content_fingerprint(route[1]) not in normalized_output:
            issues.append('В рекламном тексте пропущено или изменено название маршрута')
    for term in ('高端', '豪华', '全新', '首次出租', '采光很好', '采光充足', '阳光充足',
                 '拎包', '家具齐全', '设备齐全', '配套齐全', '安全', '双电梯', '森林',
                 '地暖', '落地窗', '朝南', '随时入住', '立即入住', '随时看房', '可短租',
                 '包落地', '包登记', '学生优先', '只要女生', '男女不限'):
        if term in output and term not in reference:
            issues.append('Не подтверждено рекламное утверждение: '+term)
    if re.search(r'人民币|美元|欧元|港币|英镑|\b(?:CNY|USD|EUR)\b|[￥¥$]|\d+(?:[.,]\d+)?\s*(?:万元|元)', output, re.I):
        if not re.search(r'人民币|美元|欧元|港币|英镑|\b(?:CNY|USD|EUR)\b|[￥¥$]|\d+(?:[.,]\d+)?\s*(?:万元|元)', reference, re.I):
            issues.append('В рекламном тексте изменена валюта')
    # Keep an explicitly stated floor pair intact. It must not turn into area,
    # money, a count of tenants, or disappear in a catchy shortened draft.
    for match in re.finditer(r'\d+\s*/\s*\d+\s*层', reference):
        pair = re.sub(r'\s+', '', match[0])
        if pair not in re.sub(r'\s+', '', output):
            issues.append('В рекламном тексте не сохранены этаж и этажность')
    return list(dict.fromkeys(issues))


def _structured_preview(directory, data, *, status=None, cancel=None):
    """The model selects a finite layout; exact fact blocks stay renderer-owned."""
    from advertising_structure import PLAN_COUNT, parse_plan, render_plan, request_for_structure
    from advertising_styles import next_style, claim_variant, used_variant_fingerprints, variant_fingerprint
    from language_service import _profile
    started = time.monotonic()
    grounded, baseline = advertising_baseline(data.get('fields'), _profile(directory).get('rules',{}))
    reference = _clean('\n'.join([*(baseline.get('titles') or [])[:1],baseline['body']]),12000)
    group_id = data.get('groupId') or hashlib.sha256(reference.encode()).hexdigest()
    if not isinstance(group_id,str) or not 1 <= len(group_id) <= 2000:
        raise ValueError('Неверная группа истории генерации')
    memory=LearningMemory(directory); current=data.get('output') if isinstance(data.get('output'),dict) else {}
    titles=memory.recent_generated(group_id,limit=30,kind='title')
    bodies=memory.recent_generated(group_id,limit=30)
    titles += [_clean(current.get('title',''),500)]; bodies += [_clean(current.get('body',''),20000)]
    title_history={_content_fingerprint(x) for x in titles}; body_history={_content_fingerprint(x) for x in bodies}
    plans={index:render_plan(baseline,index) for index in range(PLAN_COUNT)}
    used=used_variant_fingerprints(directory,group_id)
    allowed=[index for index,draft in plans.items() if _content_fingerprint(draft['title']) not in title_history
        and _content_fingerprint(draft['body']) not in body_history
        and variant_fingerprint(draft['title']) not in used['title']
        and variant_fingerprint(draft['body']) not in used['description']]
    meta={'autoApply':False,'fallback':False,'generationMode':'model-structure-verified-renderer',
          'modelContribution':'selection-of-layout-only','factualTextSource':'verified-renderer',
          'factsHash':hashlib.sha256(reference.encode()).hexdigest()}
    if not allowed:
        return {**meta,'status':'blocked','variantsExhausted':True,
            'warnings':['Доступные схемы этой карточки уже использованы. Текущий текст сохранён.']}
    style=next_style(directory,group_id)
    result=_model(request_for_structure(reference,allowed,seed=style['seed']),directory,
        deadline=started+55,status=status,cancel=cancel)
    meta.update({key:result[key] for key in ('model','ai','backend') if key in result})
    meta['elapsedSeconds']=time.monotonic()-started
    if cancel is not None and cancel.is_set():
        return {**meta,'status':'cancelled','warnings':['Генерация отменена; текущий текст сохранён.']}
    if result.get('status')!='review':
        return {**meta,'status':result.get('status','blocked'),
            'warnings':[result.get('message','Модель не смогла выбрать завершённую схему.')]}
    try:
        plan=parse_plan(result.get('text',''),allowed); draft=plans[plan]
        maximum=max(280,len(re.findall(r'[\u3400-\u9fff]',reference))+100)
        issues=_advertising_issues(draft,baseline,grounded,max_body=maximum)
    except (ValueError,TypeError):
        return {**meta,'status':'blocked','warnings':['Модель не вернула допустимую завершённую схему. Текущий текст сохранён.']}
    if issues or not claim_variant(directory,group_id,draft['title'],draft['body']):
        return {**meta,'status':'blocked','warnings':issues or ['Схема уже использована другим запросом; текущий текст сохранён.']}
    ranking=memory.rank_tags(re.findall(r'#[\w\u3400-\u9fff-]+',baseline.get('tags',''))[:30],limit=8)
    try:
        for kind,key in (('title','title'),('description','body')):
            memory.record(kind,{'text':draft[key],'language':'zh'},source='generated',group_id=group_id)
    except (OSError,ValueError,sqlite3.Error):
        meta['memoryWarning']='Черновик создан; подробная история не записана'
    return {**meta,**draft,'status':'review','styleId':style['id'],
        'tags':' '.join(row['tag'] for row in ranking),'tagRanking':ranking,
        'warnings':['ИИ выбрал структуру. Все факты перенесены генератором из подтверждённых блоков.',
                    'Проверьте китайскую подачу перед публикацией.']}


def _advertising_preview(directory, data, *, status=None, cancel=None):
    from advertising_styles import next_style, claim_variant
    from job_progress import emit
    from language_service import node_language, _profile

    started = time.monotonic()
    fields = data.get('fields')
    profile = _profile(directory)
    grounded, baseline = advertising_baseline(fields, profile.get('rules', {}))
    render_args = {'action': 'generate', 'fields': grounded, 'rules': profile.get('rules', {}),
                   'prefs': {'benefits': '', 'contact': '', 'brandTags': ''}}
    reference = _clean('\n'.join([*(baseline.get('titles') or [])[:1], baseline.get('body', '')]), 12000)
    group_id = data.get('groupId') or hashlib.sha256(reference.encode()).hexdigest()
    if not isinstance(group_id, str) or not 1 <= len(group_id) <= 2000:
        raise ValueError('Неверная группа истории генерации')
    memory = LearningMemory(directory)
    style = next_style(directory, group_id)
    current = data.get('output') if isinstance(data.get('output'), dict) else {}
    prior = _clean(current.get('body', ''), 20000)
    prior_title = _clean(current.get('title', ''), 500)
    recent = memory.recent_generated(group_id, limit=30) + ([prior] if prior else [])
    titles = memory.recent_generated(group_id, limit=30, kind='title') + ([prior_title] if prior_title else [])
    body_history, title_history = ({_content_fingerprint(text) for text in recent},
                                  {_content_fingerprint(text) for text in titles})
    candidates = re.findall(r'#[\w\u3400-\u9fff-]+', baseline.get('tags', ''))[:30]
    ranking = memory.rank_tags(candidates, limit=8)
    meta = {'status': 'blocked', 'autoApply': False, 'styleId': style['id'],
            'variation': style['label'], 'factsHash': hashlib.sha256(reference.encode()).hexdigest()}
    issues = []

    def cancelled():
        return cancel is not None and cancel.is_set()

    def accept(draft, *, fallback=False, extra=None):
        if cancelled():
            return None
        if (_content_fingerprint(draft['body']) in body_history
                or _content_fingerprint(draft['title']) in title_history):
            return None
        if not claim_variant(directory, group_id, draft['title'], draft['body']):
            return None
        value = {**meta, **draft, **(extra or {}), 'status': 'review', 'fallback': fallback,
                 'elapsedSeconds': time.monotonic()-started,
                 'tags': ' '.join(row['tag'] for row in ranking), 'tagRanking': ranking,
                 'warnings': (['Ответ модели не принят. Новая подача собрана из проверенных фактов.'] if fallback else [])
                    + ['Проверьте китайский текст перед публикацией.']}
        try:
            memory.record('title', {'text': draft['title'], 'language': 'zh'}, source='generated', group_id=group_id)
            memory.record('description', {'text': draft['body'], 'language': 'zh'}, source='generated', group_id=group_id)
        except (OSError, ValueError, sqlite3.Error):
            value['memoryWarning'] = 'Черновик создан; подробная история не записана'
        return value

    messages = advertising_messages(reference, style['instruction'], seed=style['seed'],
        previous_body=prior, previous_title=prior_title, recent_titles=titles)
    # Do not silently cut off terms at the end of a rich property description.
    # Large facts stay in the deterministic renderer below in their entirety.
    if len(messages[1]['content']) <= 4000:
        for attempt in range(2):
            if cancelled():
                break
            emit(status, 'Составляю рекламное описание: '+style['label'], stage='generate',
                 ai={'backend': 'mlx', 'operation': 'text', 'phase': 'preparing', 'tokenBased': True})
            result = _model({'messages': messages, 'maxTokens': 768, 'temperature': .75 if attempt == 0 else .4,
                             'seed': (style['seed']+attempt) % 2**32},
                            directory, deadline=started+55, status=status, cancel=cancel)
            meta.update({key: result[key] for key in ('model', 'backend', 'ai') if key in result})
            meta['attempts'] = attempt+1
            if result.get('status') != 'review':
                issues = [result.get('message', 'Модель недоступна')]
                break
            try:
                draft = _parse(result.get('text', ''))
                issues = _advertising_issues(draft, baseline, grounded)
            except (ValueError, TypeError):
                issues = ['Модель не вернула корректный формат рекламного черновика']
            if not issues:
                accepted = accept(draft)
                if accepted:
                    return accepted
                issues = ['Название или описание уже встречались в истории этой карточки']
            if time.monotonic()-started > 40:
                break
            messages[0]['content'] += ' 必须保留所有已知金额、面积、时间、楼层和费用条件；限制和待确认条件原句保留；不能重复历史版本。'
    else:
        issues = ['Много проверенных фактов: сохраняем полные блоки без сокращения']
    if cancelled():
        return {**meta, 'status': 'cancelled', 'warnings': ['Генерация отменена; карточка не изменена']}

    # Reuse the established fact renderer and its commission/service/evidence
    # gates. Only presentation indices change; no model claim is salvaged.
    for offset in range(12):
        if cancelled():
            break
        variant = (style['seed'] % 1000000) + style['sequence']*13 + offset + 1
        rendered = node_language({**render_args, 'fields': {**grounded,
            'titleVariant': variant, 'copyVariant': variant, 'tagVariant': variant,
            'style': 'compact' if style['id'] in ('budget', 'checklist', 'compact') else 'story'}})
        rendered = _with_advertising_conditions(rendered, grounded)
        if rendered.get('problems'):
            continue
        for title in (rendered.get('titles') or [])[:3]:
            draft = {'title': _clean(title, 240), 'body': _clean(rendered.get('body', ''), 6000)}
            maximum = max(280, len(re.findall(r'[\u3400-\u9fff]', reference))+100)
            if _advertising_issues(draft, baseline, grounded, max_body=maximum):
                continue
            accepted = accept(draft, fallback=True, extra={'backend': 'grounded-advertising',
                'fallbackFrom': meta.get('model', 'local-model'), 'modelIssues': issues[:8],
                'ai': {**meta.get('ai', {}), 'textSource': 'rules'}})
            if accepted:
                emit(status, 'Рекламная подача собрана из проверенных фактов; ответ модели не принят',
                     stage='generate', ai={**meta.get('ai', {}), 'phase': 'complete', 'textSource': 'rules'})
                return accepted
    return {**meta, 'status': 'cancelled' if cancelled() else 'blocked', 'elapsedSeconds': time.monotonic()-started,
            'warnings': [*issues[:8], 'Новый вариант не прошёл проверку. Текущий текст сохранён.']}


def generate_import_advertisement(directory, result, *, status=None, cancel=None):
    """A new import may gain validated prose; manual-save protection is upstream.

    A rejected/aborted model draft cannot damage a successful source import.
    Generated text remains pending learning data, never self-approved training.
    """
    if cancel is not None and cancel.is_set():
        return result
    try:
        preview = generate_preview(directory, {'fields': result.get('fields'), 'styleMode': 'advertising',
            'groupId': str(result.get('id') or result.get('sourceUrl') or result.get('url') or '') or None,
            'output': {'title': (result.get('titles') or [''])[0], 'body': result.get('body', '')}},
            status=status, cancel=cancel)
    except (OSError, ValueError, sqlite3.Error, subprocess.SubprocessError) as error:
        return {**result, 'aiAdvertisement': {'status': 'unavailable',
            'warnings': ['Рекламный вариант недоступен; сохранён исходный текст.'], 'errorType': type(error).__name__}}
    if cancel is not None and cancel.is_set():
        return result
    metadata = {key: preview[key] for key in ('status', 'model', 'backend', 'styleId', 'variation',
        'factsHash', 'elapsedSeconds', 'fallback', 'warnings', 'ai') if key in preview}
    if preview.get('status') != 'review':
        return {**result, 'aiAdvertisement': metadata}
    return {**result, 'titles': [preview['title']], 'body': preview['body'], 'tags': preview['tags'],
            'aiAdvertisement': metadata}


def translate_preview(directory, data):
    started=time.monotonic()
    text = _clean(data.get('text'), 5000)
    direction = data.get('direction', 'ru-zh')
    if not text or direction not in ('ru-zh', 'zh-ru'):
        raise ValueError('Укажите текст и направление ru-zh или zh-ru')
    if len(text)>1500:
        raise ValueError('Для компактной локальной модели разделите текст на части до 1500 знаков')
    memory = LearningMemory(directory)
    custom=_profile_glossary(directory,text,direction)
    examples = memory.retrieve(text, limit=8)
    source_key, target_key = ('ru', 'zh') if direction == 'ru-zh' else ('zh', 'ru')
    for row in examples:
        if row['kind'] in ('dictionary', 'translation') and row['payload'][source_key].strip().casefold() == text.casefold():
            return {'status': 'review', 'text': row['payload'][target_key], 'model': 'approved-memory',
                'autoApply': False, 'elapsedSeconds': time.monotonic()-started, 'warnings': [], 'message': 'Подтверждённый перевод из вашей памяти',
                'knowledgeContext':{'matched':1,'sources':['approved-memory'],'modelWeightsUpdated':False}}
    for row in custom:
        if row[source_key].strip().casefold()==text.casefold():
            return {'status':'review','text':row[target_key],'model':'active-profile-rule','autoApply':False,
                'elapsedSeconds':time.monotonic()-started,'warnings':[],'message':'Точная формулировка из действующего словаря',
                'knowledgeContext':{'matched':1,'sources':['active-profile-rule'],'modelWeightsUpdated':False}}
    approved=[{**row,'source':'approved-memory'} for row in memory.approved_glossary(text,reverse=direction=='zh-ru',limit=8)]
    merged={}
    for row in [*custom,*approved]:
        if len(row['ru'])<=150 and len(row['zh'])<=150:merged[row[source_key].casefold()]=row
    selected=sorted(merged.values(),key=lambda row:(row['source']!='approved-memory',-len(row[source_key])))[:8]
    glossary=[{key:row[key] for key in ('ru','zh')} for row in selected]
    knowledge={'matched':len(selected),'sources':sorted({row['source'] for row in selected}),
        'openLexiconCandidatesUsed':0,'modelWeightsUpdated':False}
    result = _model({'messages': [{'role': 'system', 'content': TRANSLATION_SYSTEM.format(language='Simplified Chinese' if direction == 'ru-zh' else 'Russian')},
        {'role': 'user', 'content': json.dumps({'text': text, 'approvedGlossary': glossary}, ensure_ascii=False)}],
        'maxTokens': 500, 'temperature': .2}, directory,deadline=started+55)
    if result.get('status') != 'review':
        return {**result,'knowledgeContext':knowledge, 'warnings': [result.get('message', 'Модель недоступна')]}
    output = _clean(result.get('text', ''), 12000)
    quality = translation_checks(text, output, direction)
    if not quality['passed'] and direction=='ru-zh':
        remaining=min(10,55-(time.monotonic()-started))
        if remaining>=2:
            alternate=_fallback_translate(text,glossary,directory,remaining)
            if isinstance(alternate,dict) and isinstance(alternate.get('text'),str):
                alternative_text=_clean(alternate['text'],12000)
                alternative_quality=translation_checks(text,alternative_text,direction)
                if alternative_quality['passed']:
                    return {'status':'review','text':alternative_text,'model':'existing-argos-ru-en-zh',
                        'backend':'nmt-pivot','fallbackFrom':result.get('model'),'autoApply':False,
                        'knowledgeContext':knowledge,
                        'elapsedSeconds':time.monotonic()-started,'qualityChecks':alternative_quality,
                        'message':'Показан альтернативный перевод: локальная текстовая модель не прошла проверку фактов.',
                        'warnings':['Перевод через английский требует проверки смысла, названий и условий перед применением.']}
                quality['alternativeIssues']=alternative_quality['issues']
    return {**result, 'text': output, 'status': 'review' if quality['passed'] else 'blocked',
        'knowledgeContext':knowledge,
        'elapsedSeconds':time.monotonic()-started,
        'qualityChecks':quality,
        'warnings': quality['issues']+
            ['Перевод локальной модели требует проверки; отрицания и условия должны совпадать с оригиналом.']}
