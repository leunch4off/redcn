"""Validate independently reviewed property targets; record only when requested.

No model is invoked. The default checks a private label file and changes no
memory. --record-reviewed requires an explicit cache and creates an auditable
fact-bound event for each checked row. Assistant review is never human review.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'cli'))
from advertising_learning import prepare_payload, verify_target, validate_training_dataset
from text_model_contract import content_sha, file_sha


def checked_rows(cases, labels, *, stamp=None):
    cases, labels = Path(cases).resolve(), Path(labels)
    if labels.is_symlink() or not labels.is_file() or labels.stat().st_size > 2_000_000:
        raise ValueError('Нужен ограниченный частный файл редакторских решений')
    value = json.loads(labels.read_text())
    if not isinstance(value, dict) or set(value) != {'rows'} or not isinstance(value['rows'], list) or not 1 <= len(value['rows']) <= 500:
        raise ValueError('Ожидается объект rows с проверенными примерами')
    stamp = stamp or datetime.now(timezone.utc).isoformat()
    rows, seen_groups, seen_inputs = [], set(), {}
    for label in value['rows']:
        if not isinstance(label, dict) or set(label) != {'caseFile', 'title', 'body', 'review'}:
            raise ValueError('В каждой строке нужны caseFile, title, body и review')
        name = label['caseFile']
        if not isinstance(name, str) or not re.fullmatch(r'case-\d{3}\.private\.json', name):
            raise ValueError('Неверное имя исходного примера')
        path = cases/name
        # The read-only audit also contains historical examples and the full
        # vocabulary; only its bounded live fields enter the training payload.
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 2_000_000:
            raise ValueError(name+': исходный пример отсутствует или слишком велик')
        case = json.loads(path.read_text())
        identity = case['identity']
        identifier, group = identity['id'], identity['groupId']
        if (not isinstance(identifier, str) or hashlib.sha256(identifier.encode()).hexdigest() != group
                or group in seen_groups or identity.get('split') not in ('train', 'validation')):
            raise ValueError('Повтор или неподтверждённая идентичность независимой квартиры: '+name)
        expected_split = 'validation' if int(group[:8],16)/2**32 < .2 else 'train'
        if identity['split'] != expected_split:
            raise ValueError('Нельзя переносить квартиру между учебной и проверочной частями')
        review = label['review']
        if not isinstance(review, dict) or set(review) not in ({'actor','humanVerified','reason'}, {'actor','humanVerified','reason','reviewedAt'}):
            raise ValueError('Нужно явное происхождение редакторской проверки')
        try:
            payload = prepare_payload(case['fields'], {'title': label['title'], 'body': label['body']},
                rules=case.get('rules'), review={**review, 'reviewedAt': review.get('reviewedAt', stamp)},
                source_payload_sha=identity['payloadSha256'])
            payload = verify_target(payload)
        except (ValueError,KeyError,TypeError) as error:
            raise ValueError(name+': '+str(error)) from error
        if payload['inputSha256'] in seen_inputs:
            raise ValueError('Повтор канонического запроса: '+name+' / '+seen_inputs[payload['inputSha256']])
        seen_groups.add(group); seen_inputs[payload['inputSha256']] = name
        rows.append({'caseFile': name, 'groupId': identifier, 'partition': expected_split,
            'payload': payload, 'eventId': 'grounded-'+content_sha([group,payload])})
    return rows


def run(args):
    rows = checked_rows(args.cases, args.labels)
    result = {'checked':len(rows), 'train':sum(row['partition']=='train' for row in rows),
        'validation':sum(row['partition']=='validation' for row in rows),
        'assistantReviewed':sum(row['payload']['review']['humanVerified'] is False for row in rows),
        'humanVerified':sum(row['payload']['review']['humanVerified'] is True for row in rows),
        'recorded':False, 'modelCalled':False, 'weightsUpdated':False}
    if args.record_reviewed:
        if not args.cache:
            raise ValueError('Для записи укажите отдельный --cache явно')
        from learning_memory import LearningMemory
        from learning_scheduler import _write
        memory = LearningMemory(Path(args.cache).resolve())
        for row in rows:
            review = row['payload']['review']
            memory.record('grounded_listing', row['payload'], status='approved',
                source='manual' if review['humanVerified'] else 'assistant-reviewed',
                group_id=row['groupId'], actor=review['actor'], event_id=row['eventId'])
        snapshot = memory.snapshot(lane='advertising')
        splits = {}
        for name, key in [('train','train'),('validation','valid')]:
            splits[name] = [dict(json.loads(line),status='approved') for line in Path(snapshot[key]).read_text().splitlines()]
        data = {'version':1,'splits':splits,'provenance':{'kind':'approved-local-memory','snapshot':snapshot['manifest']}}
        validate_training_dataset(data)
        dataset = Path(snapshot['directory'])/'dataset.json'
        _write(dataset,data); memory.register_dataset(dataset)
        result.update(recorded=True, dataset=str(dataset), datasetSha256=file_sha(dataset),
            status=memory.status(lane='advertising'))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases',required=True)
    parser.add_argument('--labels',required=True)
    parser.add_argument('--cache')
    parser.add_argument('--record-reviewed',action='store_true')
    arguments = parser.parse_args()
    try:
        print(json.dumps(run(arguments),ensure_ascii=False))
    except (ValueError,KeyError,OSError) as error:
        parser.exit(2,str(error)+'\n')
