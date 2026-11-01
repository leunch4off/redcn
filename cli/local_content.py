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
    LISTING_SYSTEM, TRANSLATION_SYSTEM)

FACT_KEYS = ('city', 'district', 'districtZh', 'complex', 'metro', 'metroLine', 'walk',
    'area', 'rooms', 'floor', 'floors', 'price', 'priceCurrency', 'pricePeriod',
    'deposit', 'depositCurrency', 'layout', 'separate', 'bedrooms', 'livingRooms',
    'children', 'pets', 'petPolicy', 'childrenPolicy', 'registration', 'serviceConfirmations',
    'features', 'featureCounts', 'photoEvidence', 'photoRejectedFeatures', 'sourceAudit',
    'utilities', 'ceiling', 'balconies', 'loggias', 'universityRoutes')
VOICES = ('自然简洁，少用表情，用短段落', '像认真介绍房子的朋友，先讲位置，再讲生活空间',
    '先说预算与空间，用清楚的小标题', '先写一个真实亮点，段落疏朗，最多三个表情',
    '不写套话，像房源笔记，先讲已确认的家具与布局')


def _clean(value, maximum=20_000):
    if not isinstance(value, str) or len(value) > maximum or '\x00' in value:
        raise ValueError('Текст превышает допустимый размер')
    # Contact details are not training context or model instructions.
    return re.sub(r'https?://\S+|\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b|(?:微信|wechat|telegram|телефон)\s*[:：=]\s*\S+', '', value, flags=re.I).strip()


def _model(request, directory, *, deadline=None):
    from local_text_model import generate
    return generate(request, directory, deadline=deadline)


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
    text=re.sub(r'(?<=\d)\s*[wW](?=\W|$)','万',text)
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


def validate_draft(draft, baseline, fields=None):
    """Reject conspicuous changed figures/claims; still not a full semantic proof."""
    output = draft['title']+'\n'+draft['body']
    reference = '\n'.join([*baseline.get('titles', []), baseline.get('body', '')])
    issues = []
    if not draft['title'] or not draft['body'] or not language_check(draft['body'],'zh')['passed']:
        issues.append('Модель не вернула полный китайский текст')
    if not _title_language(draft['title'],fields):
        issues.append('Заголовок должен быть на китайском; исходное имя станции или ЖК можно сохранить')
    if len(re.findall(r'[\u3400-\u9fff]',draft['title'])) > 45 or len(re.findall(r'[\u3400-\u9fff]',draft['body'])) > 150:
        issues.append('Сократите заголовок до 45, а описание до 150 китайских знаков')
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


def generate_preview(directory, data):
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
    style = secrets.choice(VOICES)
    candidates = re.findall(r'#[\w\u3400-\u9fff-]+', baseline.get('tags', ''))[:30]
    ranking = memory.rank_tags(candidates, limit=8)
    messages = [{'role': 'system', 'content': LISTING_SYSTEM+' 标题和正文都不能只改表情或标点，也不能重复给出的历史版本。'},
        {'role': 'user', 'content': json.dumps({'verifiedText': reference[:1200], 'style': style,
            'previousTextToAvoid': _clean(prior, 20000)[:250],
            'previousTitleToAvoid': _clean(prior_title,500)[:120],
            'recentTitlesToAvoid':[title[:120] for title in list(dict.fromkeys(recent_titles))[:5]],
            'approvedStyleExamples': [row['payload']['text'][:150] for row in examples if row['kind'] in ('title', 'description')][:2],
            'variation': secrets.token_hex(4)}, ensure_ascii=False)}]
    begin = time.monotonic()
    for attempt in range(2):
        result = _model({'messages': messages, 'maxTokens': 420, 'temperature': .75 if attempt==0 else .25},
            directory, deadline=begin+55)
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
            memory.record('title',{'text':draft['title'],'language':'zh'},source='generated',group_id=group_id)
            memory.record('description',{'text':draft['body'],'language':'zh'},source='generated',group_id=group_id)
        except (OSError,ValueError,sqlite3.Error):
            meta['memoryWarning']='Черновик создан; история генерации не записана'
    return {**meta, **draft, 'tags': ' '.join(row['tag'] for row in ranking), 'tagRanking': ranking,
        'status': 'blocked' if issues else 'review', 'autoApply': False,
        'warnings': [*issues, 'Проверьте смысл и китайский язык перед применением. Проверка чисел не подтверждает все утверждения.'],
        'variation': style, 'factsHash': hashlib.sha256(reference.encode()).hexdigest()}


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
