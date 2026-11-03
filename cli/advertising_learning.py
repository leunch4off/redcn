"""Fact-bound editorial supervision for the separate advertising model.

Saving an import or a model answer is not approval. A reviewed row binds the
source fields, canonical writer request and independently checked target. Old
standalone style examples remain memory; they are not invented input/label pairs.
"""
from datetime import datetime, timezone
import copy
import hashlib
import json
import re

from text_model_contract import content_sha

KIND = 'grounded_listing'
LANE = 'advertising'
VERSION = 1
MIN_EXAMPLES = 20
MIN_GROUPS = 5
MIN_TRAIN = 16
MIN_VALID = 4
MAX_PAYLOAD_BYTES = 300_000


def _sha(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _object(value):
    if not isinstance(value, dict):
        raise ValueError('Нужен проверенный пример объявления с исходными фактами')
    raw = json.dumps(value, ensure_ascii=False, allow_nan=False)
    if len(raw.encode()) > MAX_PAYLOAD_BYTES:
        raise ValueError('Снимок фактов объявления превышает допустимый размер')
    return json.loads(raw)


def _target(value):
    from learning_memory import _text
    if not isinstance(value, dict) or set(value) != {'title', 'body'}:
        raise ValueError('Нужны проверенные заголовок и полное описание')
    result = {'title': _text(value['title'], 240), 'body': _text(value['body'], 6000)}
    if any(result[key] != value[key].strip() for key in result):
        raise ValueError('Удалите контакты из учебного текста и проверьте его заново')
    return result


def _review(value):
    from learning_memory import _text
    from learning_provenance import actor_name
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {'actor', 'humanVerified', 'reason', 'reviewedAt'}:
        raise ValueError('Нужны участник, вид проверки, её основание и дата')
    actor = actor_name(value['actor'])
    if type(value['humanVerified']) is not bool:
        raise ValueError('Укажите, кто проверил пример: человек или ассистент')
    if actor.startswith('assistant_') and value['humanVerified']:
        raise ValueError('Проверку ассистента нельзя выдавать за проверку человека')
    if not value['humanVerified'] and not actor.startswith('assistant_'):
        raise ValueError('Проверка ассистента должна иметь явного участника assistant_…')
    try:
        stamp = datetime.fromisoformat(value['reviewedAt'].replace('Z', '+00:00'))
        if stamp.tzinfo is None or stamp.timestamp() > datetime.now(timezone.utc).timestamp()+300:
            raise ValueError()
    except (AttributeError, TypeError, ValueError, OverflowError) as error:
        raise ValueError('Неверная дата проверки учебного примера') from error
    return {'actor': actor, 'humanVerified': value['humanVerified'],
            'reason': _text(value['reason'], 2000), 'reviewedAt': stamp.isoformat()}


def prepare_payload(fields, target, *, rules=None, review=None, source_payload_sha=None):
    """Freeze the complete live input, without a model call or memory write."""
    from individual_copy import LIVE_KEYS, build_brief, generation_request
    source = _object(fields)
    frozen = {key: source[key] for key in LIVE_KEYS if key in source}
    rules = _object(rules or {})
    if len(rules) > 2000 or any(not isinstance(k, str) or not isinstance(v, str)
                               or len(k) > 500 or len(v) > 1000 for k, v in rules.items()):
        raise ValueError('Неверный снимок словаря проверяемого объявления')
    brief = build_brief(frozen, rules)
    request = generation_request(brief, seed=0)
    if source_payload_sha is not None and not re.fullmatch(r'[a-f0-9]{64}', source_payload_sha):
        raise ValueError('Неверная контрольная сумма исходной карточки')
    return {'version': VERSION, 'contract': brief['contract'], 'fields': frozen, 'rules': rules,
            'factsHash': brief['factsHash'], 'request': request,
            'inputSha256': content_sha(request['messages']), 'target': _target(target),
            'review': _review(review), 'sourcePayloadSha256': source_payload_sha}


def normalize_payload(value):
    """Reject altered/stale prompts instead of reconstructing them silently."""
    expected = {'version', 'contract', 'fields', 'rules', 'factsHash', 'request',
                'inputSha256', 'target', 'review', 'sourcePayloadSha256'}
    value = _object(value)
    if set(value) != expected or value.get('version') != VERSION:
        raise ValueError('Неверная версия примера объявления с фактами')
    frozen = prepare_payload(value['fields'], value['target'], rules=value['rules'],
        review=value['review'], source_payload_sha=value['sourcePayloadSha256'])
    if any(value[key] != frozen[key] for key in ('contract', 'factsHash', 'request', 'inputSha256')):
        raise ValueError('Факты или контракт запроса изменились; пример нужно проверить заново')
    return frozen


def verify_target(payload, *, require_review=True):
    from individual_copy import build_brief, validate_individual
    payload = normalize_payload(payload)
    if require_review and payload['review'] is None:
        raise ValueError('Пример ещё не проверен по источнику и подтверждённым фактам')
    brief = build_brief(payload['fields'], payload['rules'])
    issues = validate_individual(payload['target'], brief)
    if issues:
        raise ValueError('Учебный текст не прошёл сверку фактов: '+' · '.join(issues[:12]))
    return payload


def training_row(row):
    """A unique property input and a complete target, never a generic prompt."""
    payload = verify_target(json.loads(row['payload']))
    review = payload['review']
    messages = copy.deepcopy(payload['request']['messages'])
    messages.append({'role': 'assistant', 'content':
        '标题：'+payload['target']['title']+'\n正文：\n'+payload['target']['body']})
    return {'id': row['id'], 'kind': KIND, 'task': 'individual-listing',
        'groupId': row['group_id'], 'messages': messages, 'source': row['source'],
        'grounding': {'fields': payload['fields'], 'rules': payload['rules'],
            'factsHash': payload['factsHash'], 'inputSha256': payload['inputSha256'],
            'request': payload['request'], 'contract': payload['contract']},
        'provenance': {'kind': 'human-reviewed' if review['humanVerified'] else 'assistant-reviewed',
            'reviewer': review['actor'], 'humanVerified': review['humanVerified'],
            'reviewReason': review['reason'], 'permission': 'approved-for-local-training',
            'sourceSha256': row['fingerprint'], 'groupId': row['group_id']}}


def validate_training_dataset(data):
    """Recheck the immutable prompt/target binding before native work starts."""
    from individual_copy import build_brief, generation_request, parse_individual_text, validate_individual
    from text_model_contract import validate_dataset
    data = validate_dataset(data)
    for rows in data['splits'].values():
        for row in rows:
            grounding, provenance = row.get('grounding'), row.get('provenance')
            if row.get('kind') != KIND or not isinstance(grounding, dict) or not isinstance(provenance, dict):
                raise ValueError('Для обучения объявлений нужен проверенный пример с замороженными фактами')
            if provenance.get('permission') != 'approved-for-local-training' or row.get('status') != 'approved':
                raise ValueError('Обучение объявлений требует явного разрешения на каждый пример')
            human = provenance.get('humanVerified')
            reviewer = provenance.get('reviewer', '')
            if type(human) is not bool or not isinstance(reviewer, str) or not reviewer:
                raise ValueError('В учебном примере не указан участник проверки')
            if human == reviewer.startswith('assistant_'):
                raise ValueError('Проверка ассистента не является проверкой человека')
            brief = build_brief(grounding.get('fields'), grounding.get('rules'))
            request = generation_request(brief, seed=0)
            if (grounding.get('factsHash') != brief['factsHash']
                    or grounding.get('contract') != brief['contract']
                    or grounding.get('request') != request
                    or grounding.get('inputSha256') != content_sha(request['messages'])
                    or row['messages'][:-1] != request['messages']):
                raise ValueError('Запрос учебного примера не совпадает с его замороженными фактами')
            draft = parse_individual_text(row['messages'][-1]['content'])
            issues = validate_individual(draft, brief)
            if issues:
                raise ValueError('Учебная цель не прошла сверку фактов: '+' · '.join(issues[:12]))
    return data


def lane_summary(rows, *, partitions=None):
    """Read-only readiness; counts are deduplicated just like the snapshot."""
    partitions = partitions or {}
    selected = [row for row in rows if row['status'] == 'approved' and row['kind'] == KIND]
    groups = {row['group_id'] for row in selected}
    def split(row):
        return partitions.get(row['group_id'], 'valid' if int(row['group_id'][:8], 16)/2**32 < .2 else 'train')
    def fingerprint(row):
        payload = json.loads(row['payload'])
        return content_sha([payload['request']['messages'], payload['target']])
    valid_hashes = {fingerprint(row) for row in selected if split(row) == 'valid'}
    inputs = {'train': set(), 'valid': set()}
    seen, counts = set(), {'train': 0, 'valid': 0}
    for row in selected:
        digest, part = fingerprint(row), split(row)
        if digest in seen or part == 'train' and digest in valid_hashes:
            continue
        seen.add(digest); counts[part] += 1
        inputs[part].add(json.loads(row['payload'])['inputSha256'])
    reasons = []
    if len(seen) < MIN_EXAMPLES: reasons.append('reviewed-examples-needed')
    if len(groups) < MIN_GROUPS: reasons.append('independent-properties-needed')
    if counts['train'] < MIN_TRAIN: reasons.append('training-split-needed')
    if counts['valid'] < MIN_VALID: reasons.append('validation-split-needed')
    if inputs['train'] & inputs['valid']: reasons.append('duplicate-input-across-splits')
    return {'lane': LANE, 'approvedExamples': len(seen), 'approvedGroups': len(groups),
        'approvedRecords': len(selected),
        'trainExamples': counts['train'], 'validExamples': counts['valid'],
        'minimumExamples': MIN_EXAMPLES, 'minimumGroups': MIN_GROUPS,
        'minimumTrain': MIN_TRAIN, 'minimumValidation': MIN_VALID,
        'trainingEligible': not reasons, 'blockingReasons': reasons,
        'pendingExamples': sum(row['kind'] == KIND and row['status'] == 'pending' for row in rows),
        'humanVerified': sum(bool((json.loads(row['payload']).get('review') or {}).get('humanVerified'))
                             for row in selected),
        'assistantReviewed': sum(not bool((json.loads(row['payload']).get('review') or {}).get('humanVerified'))
                                for row in selected),
        'nativeTrainingProven': False, 'modelWeightsUpdated': False}
