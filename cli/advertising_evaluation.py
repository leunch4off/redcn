"""Production advertising probes over frozen independent rental facts.

The v26 benchmark remains unchanged. Its sparse typed fields go through the
production renderer; all independently annotated generation facts are retained
as one additional verified block, including conditions not expressible in the
typed controls. No fallback or accepted-memory output is measured here.
"""
from advertising_styles import STYLES
from local_content import (_advertising_issues, _clean, advertising_baseline,
                           advertising_messages)
from rental_heldout_quality import assess as assess_frozen, probes as frozen_probes
from text_quality import parse_card


def active_rules(directory):
    from language_service import _profile
    rules = _profile(directory).get('rules', {})
    if not isinstance(rules, dict):
        raise ValueError('Неверный активный словарь для рекламного сравнения')
    return rules


def prepare_probes(corpus, *, rules=None):
    bases = {}
    for case in corpus['cases']:
        fields, rendered = advertising_baseline(case['fields'], rules=rules)
        baseline = {**rendered, 'body': rendered.get('body', '')+'\n'+case['generationFactsZh']}
        reference = _clean('\n'.join([*(baseline.get('titles') or [])[:1], baseline['body']]), 12000)
        bases[case['id']] = {'fields': fields, 'baseline': baseline, 'reference': reference}
    rows = []
    for probe in frozen_probes(corpus):
        if probe['task'] == 'listing':
            case_index = next(i for i, case in enumerate(corpus['cases']) if case['id'] == probe['caseId'])
            style = STYLES[(case_index*3+probe['variant']-1) % len(STYLES)]
            probe = {**probe, **bases[probe['caseId']], 'style': style[1], 'styleId': style[0]}
        rows.append(probe)
    return rows


def request_for(probe, history, seed, policy):
    if probe['task'] == 'listing':
        previous = history[-1] if history else {
            'title': (probe['baseline'].get('titles') or [''])[0], 'body': probe['baseline']['body']}
        messages = advertising_messages(probe['reference'], probe['style'], seed=seed,
            previous_body=previous['body'], previous_title=previous['title'],
            recent_titles=[row['title'] for row in history[-6:]])
        return {'messages': messages, 'maxTokens': policy['listingMaxTokens'],
                'temperature': policy['listingTemperature'], 'seed': seed}
    return {'messages': probe['messages'], 'maxTokens': policy['translationMaxTokens'],
            'temperature': policy['translationTemperature'], 'seed': seed}


def assess(probe, text):
    if probe['task'] != 'listing':
        return assess_frozen(probe, text)
    try:
        draft = parse_card(text)
    except (ValueError, TypeError):
        return {'passed': False, 'issues': ['Некорректный JSON title/body'], 'jsonShapeCorrect': False}
    issues = _advertising_issues(draft, probe['baseline'], probe['fields'])
    # Retain every named source station, including the second observed station
    # which the single typed metro field cannot represent.
    output = (draft['title']+' '+draft['body']).casefold()
    for station in probe['case']['observedStations']:
        if not any(name.casefold() in output for name in station['aliases']):
            issues.append('Пропущена наблюдавшаяся станция: '+station['ru'])
    return {'passed': not issues, 'issues': list(dict.fromkeys(issues)),
            'jsonShapeCorrect': True, 'productionAdvertisingContract': True,
            'automaticSemanticVerification': False}


def basis(probes):
    return [{'caseId': row['caseId'], 'fields': row['fields'],
             'baseline': row['baseline'], 'reference': row['reference']}
            for row in probes if row['task'] == 'listing' and row['variant'] == 1]
