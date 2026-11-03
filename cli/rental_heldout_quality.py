"""Frozen, independent real-listing fact extracts; never a training corpus."""
import hashlib
import json
from pathlib import Path
import re
import unicodedata

from local_content import validate_draft
from text_quality import LISTING_SYSTEM, TRANSLATION_SYSTEM, parse_card, polarity_issues, translation_checks

STYLES = ('先说预算和已确认的配置；不用表情；用一个自然短段落。',
          '先写真实配置亮点，再写租赁条件；用疏朗的短行，最多两个表情。',
          '先写位置资料，再写配置和预算；像克制的房源笔记，避免复用上次句式。')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_cases(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 200000:
        raise ValueError('Нужен фиксированный небольшой корпус без ссылок и частных файлов')
    value = json.loads(path.read_text())
    if value.get('version') != 1 or value.get('annotation', {}).get('trainingUseAllowed') is not False:
        raise ValueError('Проверочный корпус нельзя использовать для обучения')
    cases = value.get('cases')
    if not isinstance(cases, list) or not 2 <= len(cases) <= 30:
        raise ValueError('Нужно от 2 до 30 независимых карточек')
    seen = set()
    for row in cases:
        if not isinstance(row, dict) or not re.fullmatch(r'heldout-\d{2}', row.get('id', '')) or row['id'] in seen:
            raise ValueError('Неверные или повторяющиеся ID проверочных карточек')
        seen.add(row['id'])
        for key in ('sourceRu', 'referenceZh', 'generationFactsZh'):
            text = row.get(key)
            if not isinstance(text, str) or not 20 <= len(text) <= 3000 or re.search(r'https?://|\b[\w.-]+@[\w.-]+\b', text):
                raise ValueError('Нужны обезличенные тексты без контактов и ссылок')
        if not isinstance(row.get('fields'), dict) or set(row['fields'])-{'price', 'area', 'metro', 'priceCurrency'}:
            raise ValueError('Неверные подтверждённые поля корпуса')
        if not re.fullmatch(r'[a-f0-9]{64}', row.get('sourceClauseSha256', '')):
            raise ValueError('Нужен отпечаток независимого исходного фрагмента')
        if not isinstance(row.get('observedStations'), list) or not row['observedStations']:
            raise ValueError('Нужны наблюдавшиеся имена станций')
    return value


def normalized(value):
    return ''.join(c.casefold() for c in value if unicodedata.category(c)[0] in ('L', 'N'))


def check_training_disjoint(dataset, training):
    """Reject exact case/group overlap; do not claim semantic independence proof."""
    raw = json.dumps(training, ensure_ascii=False)
    canonical = normalized(raw)
    conflicts = []
    for case in dataset['cases']:
        for key in ('sourceRu', 'referenceZh', 'generationFactsZh'):
            if normalized(case[key]) in canonical:
                conflicts.append(case['id']+':'+key)
    if conflicts:
        raise ValueError('Проверочные тексты пересекаются с обучением: '+', '.join(conflicts))
    return {'exactTextOverlap': False, 'semanticOverlapAutomaticallyExcluded': False,
            'caseSourceSeparateFromAuthoredTrainingSituations': True}


def probes(dataset):
    rows = []
    for case in dataset['cases']:
        for direction, source in (('ru-zh', case['sourceRu']), ('zh-ru', case['referenceZh'])):
            rows.append({'id': case['id']+'-'+direction, 'caseId': case['id'], 'task': direction,
                         'source': source, 'case': case,
                         'messages': [{'role': 'system', 'content': TRANSLATION_SYSTEM.format(language='Simplified Chinese' if direction == 'ru-zh' else 'Russian')},
                                      {'role': 'user', 'content': json.dumps({'text': source, 'approvedGlossary': []}, ensure_ascii=False)}]})
        for variant, style in enumerate(STYLES):
            rows.append({'id': case['id']+'-listing-'+str(variant+1), 'caseId': case['id'],
                         'task': 'listing', 'case': case, 'style': style, 'variant': variant+1})
    return rows


def generation_messages(probe, history):
    return [{'role': 'system', 'content': LISTING_SYSTEM}, {'role': 'user', 'content': json.dumps({
        'verifiedText': probe['case']['generationFactsZh'], 'style': probe['style'],
        'recentTitlesToAvoid': [row['title'] for row in history[-2:]],
        'previousTextToAvoid': history[-1]['body'] if history else '',
        'variation': probe['id']}, ensure_ascii=False)}]


def assess(probe, text):
    case = probe['case']
    if probe['task'] != 'listing':
        result = translation_checks(probe['source'], text, probe['task'])
        required = [station['aliases'] if probe['task'] == 'ru-zh' else [station['ru']]
                    for station in case['observedStations']]
        missing = [choices[0] for choices in required if not any(name.casefold() in text.casefold() for name in choices)]
        result['stationNamesPreserved'] = not missing
        if missing:
            result['issues'].append('Пропущены наблюдавшиеся станции: '+', '.join(missing))
            result['passed'] = False
        return result
    try:
        draft = parse_card(text)
    except (ValueError, TypeError):
        return {'passed': False, 'issues': ['Некорректный JSON title/body'], 'jsonShapeCorrect': False}
    reference = case['generationFactsZh']
    issues = validate_draft(draft, {'titles': [], 'body': reference}, case['fields'])
    issues += polarity_issues(reference, draft['body'], require_mentions=True)
    all_aliases = [name for station in case['observedStations'] for name in station['aliases']]
    if not any(name.casefold() in (draft['title']+' '+draft['body']).casefold() for name in all_aliases):
        issues.append('Не сохранено ни одно наблюдавшееся имя станции')
    return {'passed': not issues, 'issues': list(dict.fromkeys(issues)),
            'jsonShapeCorrect': True, 'automaticSemanticVerification': False}


def variation_summary(rows):
    result = []
    for identity in dict.fromkeys(row['caseId'] for row in rows):
        variants = []
        for row in rows:
            if row['caseId'] == identity and row['task'] == 'listing' and row.get('status') == 'measured':
                try:
                    variants.append(parse_card(row['text']))
                except ValueError:
                    pass
        titles = [normalized(value['title']) for value in variants]
        bodies = [normalized(value['body']) for value in variants]
        result.append({'caseId': identity, 'parseableVariants': len(variants),
                       'uniqueNormalizedTitles': len(set(titles)), 'uniqueNormalizedBodies': len(set(bodies)),
                       'onlyEmojiOrPunctuationCountsAsVariation': False,
                       'allThreeSurfacesDifferent': len(variants) == 3 and len(set(titles)) == len(set(bodies)) == 3,
                       'nativeEditorialOriginalityAccepted': False})
    return result
