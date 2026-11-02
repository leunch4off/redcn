"""Explicit validator confusion matrix and direct-answer failure taxonomy."""
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

from text_quality import parse_card, translation_checks

CATEGORIES = {
    'format': ('JSON', 'формат'),
    'numbers-units': ('Числа', 'числа', 'единиц', 'суммы, площадь', 'площадь', 'этаж', 'количество', 'валют'),
    'money-role': ('назначение суммы', 'суммы аренды и залога'),
    'payment-period': ('период оплаты',),
    'station-route': ('станц', 'маршрут', 'метро', 'локация'),
    'missing-fact': ('пропущен', 'пропущены', 'не сохранен', 'не сохранён', 'не сохранена'),
    'unsupported-fact': ('Добавлен', 'Не подтвержд', 'отсутствующие'),
    'polarity-condition': ('наличие, отрицание', 'ограничение', 'условие', 'неопредел', 'отрицание'),
    'fee-scope': ('оплат', 'комисс', 'бесплат', 'регистрац'),
    'language': ('китайский текст', 'на китайском', 'перевод на выбранном'),
}


def classify(issues, stop_reason=None):
    categories = {name for name, words in CATEGORIES.items() if any(word in issue for issue in issues for word in words)}
    if stop_reason and stop_reason not in ('eos','stream-ended'):
        categories.add('completion:'+stop_reason)
    if issues and not categories:
        categories.add('unclassified')
    return sorted(categories)


def validator_audit(path):
    from local_content import _advertising_issues
    path=Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size>500000:
        raise ValueError('Small regular independent validator corpus required')
    data=json.loads(path.read_text())
    if data.get('version')!=1 or data.get('trainingUseAllowed') is not False:
        raise ValueError('Validator cases cannot train the model')
    rows=[]; totals=Counter(); by_class=defaultdict(Counter)
    for row in data['cases']:
        if type(row.get('expectedPass')) is not bool:
            raise ValueError('Every case needs an explicit expected decision')
        if row['task']=='translation':
            result=translation_checks(row['source'],row['output'],row['direction'])
            issues=result['issues']
        elif row['task']=='advertising':
            try:issues=_advertising_issues(parse_card(row['output']),row['baseline'],row['fields'])
            except (ValueError,TypeError):issues=['Некорректный JSON title/body']
        else:raise ValueError('Unknown validator task')
        passed=not issues
        outcome=('correctAccept' if passed and row['expectedPass'] else 'correctReject' if not passed and not row['expectedPass']
                 else 'falseAccept' if passed else 'falseReject')
        totals[outcome]+=1;by_class[row['category']][outcome]+=1
        rows.append({'id':row['id'],'expectedPass':row['expectedPass'],'actualPass':passed,
            'outcome':outcome,'issues':issues,'errorClasses':classify(issues)})
    return {'version':1,'datasetSha256':hashlib.sha256(path.read_bytes()).hexdigest(),
        'cases':len(rows),'confusion':dict(totals),'byClass':{key:dict(value) for key,value in by_class.items()},
        'results':rows,'humanOrNativeEditorApproved':False,'allExpectedDecisionsMatched':not(totals['falseAccept'] or totals['falseReject'])}


def summarize_direct_report(report):
    summary={}
    for label in ('current','candidate'):
        rows=report.get(label,[]);categories=Counter();stops=Counter()
        for row in rows:
            categories.update(classify(row.get('checks',{}).get('issues',[]),row.get('stopReason')))
            stops[row.get('stopReason','legacy-unrecorded')]+=1
        summary[label]={'attempted':len(rows),'completed':sum(row.get('status')=='measured' for row in rows),
            'factsPassed':sum(row.get('status')=='measured' and row.get('checks',{}).get('passed') is True for row in rows),
            'partialOutputsPassingOnlyFactChecks':sum(row.get('status')!='measured' and row.get('checks',{}).get('passed') is True for row in rows),
            'errorClasses':dict(categories),'stopReasons':dict(stops),
            'fallbackCredited':False,'rawAnswersRemainInPrivateReport':True}
    return {'version':1,'models':summary,'limitations':'Automatic taxonomy, not native-editor or semantic certification.'}
