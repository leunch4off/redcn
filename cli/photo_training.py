"""Private operator photo labels. Model proposals are never training truth.

The authenticated caller supplies a card ID and server-derived actor. A rejected
proposal is distinct from verified absence; unknown and incomplete labels remain
pending. Exports resolve current originals and retain apartment groups.
"""
from collections import Counter, defaultdict
import copy
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import time

from trained_photo_detector import CLASS_IDS
from local_photo_ai import FEATURE_LABELS
from workspace_ownership import owned_path

VERSION = 1
MAX_ROWS = 630
SHA = re.compile(r'[a-f0-9]{64}\Z')
CARD = re.compile(r'[a-f0-9]{32}\Z')
ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z')
STATES = ('pending', 'present', 'absent', 'unknown', 'rejected')
SPLITS = ('unassigned', 'train', 'calibration', 'test')


class PhotoTrainingConflict(ValueError):
    code = 'PHOTO_TRAINING_REVISION_CONFLICT'


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def content_sha(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def identity(photo_id, category):
    return hashlib.sha256((photo_id+'|'+category).encode()).hexdigest()


def _id(value, pattern, message):
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError(message)
    return value


def _region(value):
    if (not isinstance(value, list) or len(value) != 4
            or any(type(x) not in (int, float) or not math.isfinite(x) for x in value)
            or not all(0 <= x <= 1 for x in value) or value[2] <= 0 or value[3] <= 0
            or value[0]+value[2] > 1 or value[1]+value[3] > 1):
        raise ValueError('Рамка: x, y, ширина, высота от 0 до 1 внутри оригинала')
    return [float(x) for x in value]


def _db_path(directory):
    return owned_path(directory, 'photo-training/reviews.sqlite3')


def _load(directory, card_id):
    path = _db_path(directory)
    if not path.exists():
        return 0, None
    with sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=5) as db:
        row = db.execute('SELECT revision,document,sha256 FROM revisions WHERE card_id=? ORDER BY revision DESC LIMIT 1', (card_id,)).fetchone()
    if not row:
        return 0, None
    if hashlib.sha256(row[1].encode()).hexdigest() != row[2]:
        raise ValueError('Контрольная сумма фоторазметки не совпадает')
    return row[0], json.loads(row[1])


def _card(directory, card_id):
    _id(card_id, CARD, 'Неверный ID карточки')
    from language_service import _read
    from phone_relay import card_media
    card, _ = _read(directory, card_id)
    photos = {row['id']: row for row in card_media(directory, card, include_crops=False)
              if isinstance(row.get('id'), str) and SHA.fullmatch(row['id']) and row.get('kind') == 'original'}
    return card, photos


def _proposals(card, allowed):
    edits = card.get('fields', {}).get('photoEdits', {})
    edits = edits if isinstance(edits, dict) else {}
    rows = {}
    journal = edits.get('reviewJournal', {})
    journal = journal.get('entries', []) if isinstance(journal, dict) else []
    observations = edits.get('observations', {})
    observations = observations if isinstance(observations, dict) else {}
    source_rows = list(journal[:600]) if isinstance(journal, list) else []
    for observation in list(observations.values())[:600]:
        if not isinstance(observation, dict):
            continue
        analysis = observation.get('analysis', {})
        if not isinstance(analysis, dict):
            continue
        candidates = analysis.get('candidates', [])
        for candidate in candidates[:80] if isinstance(candidates, list) else []:
            if not isinstance(candidate, dict):
                continue
            category = candidate.get('id')
            rejected = observation.get('rejected', {})
            confirmed = observation.get('confirmed', {})
            state = ('rejected' if isinstance(rejected, dict) and rejected.get(category) is True
                     else 'confirmed' if isinstance(confirmed, dict) and category in confirmed else 'pending')
            source_rows.append({'originalId': analysis.get('originalId'), 'featureId': category,
                                'state': state, 'evidence': candidate.get('evidence', [])})
    for row in source_rows:
        if not isinstance(row, dict):
            continue
        photo_id, category = row.get('originalId'), row.get('featureId')
        if photo_id not in allowed or category not in CLASS_IDS:
            continue
        key = identity(photo_id, category)
        boxes = []
        evidence = row.get('evidence', [])
        for item in evidence[:8] if isinstance(evidence, list) else []:
            if not isinstance(item, dict) or item.get('originalId', photo_id) != photo_id:
                continue
            try:
                region = _region(item.get('region'))
            except ValueError:
                continue
            if not any(box['region'] == region for box in boxes):
                boxes.append({'region': region})
        rows[key] = {'id': key, 'photoId': photo_id, 'category': category,
                     'state': 'pending', 'verified': False, 'exhaustive': False,
                     'boxes': boxes, 'sourceDecision': str(row.get('state', 'pending'))[:40],
                     'sourceKind': 'listing-review-journal'}
    return rows


def view(directory, card_id, *, namespace='local'):
    card, photos = _card(directory, card_id)
    revision, saved = _load(directory, card_id)
    rows = _proposals(card, photos)
    if saved:
        for row in saved.get('rows', []):
            rows[row['id']] = {**row, 'stale': row['photoId'] not in photos}
    rights = copy.deepcopy((saved or {}).get('rights', {'allowed': False, 'basis': '', 'photoIds': []}))
    if set(rights.get('photoIds', [])) != set(photos):
        rights['allowed'] = False
    document = {**(saved or {}), 'version': VERSION, 'listingId': card_id,
                'groupId': (saved or {}).get('groupId', ''),
                'split': (saved or {}).get('split', 'unassigned'),
                'rights': rights,
                'duplicatesReviewed': (saved or {}).get('duplicatesReviewed', False),
                'rows': list(rows.values())[:MAX_ROWS]}
    states = Counter(row['state'] for row in document['rows'])
    return {'revision': revision, 'document': document,
            'photos': [{'id': key, 'width': value.get('width'), 'height': value.get('height'),
                        'url': '/api/'+namespace+'/media/'+key} for key, value in photos.items()],
            'categories': CLASS_IDS, 'categoryLabels': {key: FEATURE_LABELS.get(key, {
                'gasStove':'Газовая плита', 'cabinetry':'Встроенные шкафы', 'cupboard':'Шкаф'} .get(key,key)) for key in CLASS_IDS},
            'states': dict(states),
            'message': 'Подтверждение находки не является проверенной обучающей меткой. Проверьте рамки и категорию на всём фото.',
            'automaticTruth': False}


def normalize(document, card_id, photo_ids, *, actor, previous=None, now=None):
    if not isinstance(document, dict) or document.get('version', VERSION) != VERSION:
        raise ValueError('Неверный формат фоторазметки')
    if set(document)-{'version', 'listingId', 'groupId', 'split', 'rights', 'duplicatesReviewed', 'rows', 'updatedAt', 'reviewedBy'}:
        raise ValueError('Неизвестные поля фоторазметки')
    if document.get('listingId', card_id) != card_id:
        raise ValueError('Разметка относится к другой карточке')
    if not isinstance(actor, str) or not actor.strip() or len(actor) > 150 or re.search(r'[\x00-\x1f\x7f]', actor):
        raise ValueError('Нужен автор проверки')
    group = document.get('groupId', '')
    if group:
        _id(group, ID, 'Группа квартиры: до 64 латинских букв, цифр, _ или -')
    split = document.get('split', 'unassigned')
    if split not in SPLITS:
        raise ValueError('Неверная часть корпуса')
    rights = document.get('rights', {})
    if (not isinstance(rights, dict) or set(rights)-{'allowed', 'basis', 'photoIds'}
            or type(rights.get('allowed', False)) is not bool
            or rights.get('basis', '') not in ('', 'owner-authorized', 'licensed')):
        raise ValueError('Укажите основание разрешения использования фотографий')
    if rights.get('allowed') is True and not rights.get('basis'):
        raise ValueError('Разрешение требует основания')
    # Annotation revisions alone cannot detect a card gaining new originals.
    # Bind every save to the exact set displayed by that editor; never expand
    # an old permission to photographs the operator has not loaded.
    displayed = rights.get('photoIds')
    if (not isinstance(displayed, list) or len(displayed) > MAX_ROWS
            or any(not isinstance(item, str) or not SHA.fullmatch(item) for item in displayed)
            or len(set(displayed)) != len(displayed)):
        raise PhotoTrainingConflict('Нужен актуальный набор оригиналов. Загрузите разметку карточки заново.')
    if set(displayed) != set(photo_ids):
        raise PhotoTrainingConflict('Оригиналы карточки изменились. Загрузите разметку заново и подтвердите права на текущие фото.')
    if type(document.get('duplicatesReviewed', False)) is not bool:
        raise ValueError('Проверка повторных ракурсов должна быть явной')
    raw_rows = document.get('rows', [])
    if not isinstance(raw_rows, list) or len(raw_rows) > MAX_ROWS:
        raise ValueError('Слишком много строк разметки')
    old = {row['id']: row for row in (previous or {}).get('rows', [])}
    rows, seen = [], set()
    moment = time.time() if now is None else now
    for raw in raw_rows:
        if not isinstance(raw, dict):
            raise ValueError('Неверная строка разметки')
        photo_id, category = raw.get('photoId'), raw.get('category')
        if photo_id not in photo_ids or category not in CLASS_IDS:
            raise ValueError('Нужны оригинал текущей карточки и известная категория')
        key = identity(photo_id, category)
        if raw.get('id', key) != key or key in seen:
            raise ValueError('Повтор или неверный ID разметки')
        seen.add(key)
        state = raw.get('state', 'pending')
        if state not in STATES or type(raw.get('verified', False)) is not bool or type(raw.get('exhaustive', False)) is not bool:
            raise ValueError('Неверное решение разметчика')
        boxes = raw.get('boxes', [])
        if not isinstance(boxes, list) or len(boxes) > 100:
            raise ValueError('Нужны не более 100 рамок на категорию')
        normalized_boxes = []
        for box in boxes:
            if not isinstance(box, dict) or set(box)-{'region', 'instanceId', 'linkConfirmed'}:
                raise ValueError('Неверная рамка предмета')
            value = {'region': _region(box.get('region'))}
            if box.get('instanceId'):
                value['instanceId'] = _id(box['instanceId'], ID, 'Неверный ID предмета между ракурсами')
                value['linkConfirmed'] = box.get('linkConfirmed') is True
            normalized_boxes.append(value)
        if state == 'present' and not normalized_boxes:
            raise ValueError('Для видимого предмета нужна рамка')
        if state == 'absent' and normalized_boxes:
            raise ValueError('Проверенное отсутствие не может содержать рамку предмета')
        verified = raw.get('verified') is True
        if verified and (state not in ('present', 'absent') or raw.get('exhaustive') is not True):
            raise ValueError('Проверенная метка требует наличия/отсутствия и полной проверки категории на фото')
        row = {'id': key, 'photoId': photo_id, 'category': category, 'state': state,
               'verified': verified, 'exhaustive': raw.get('exhaustive') is True, 'boxes': normalized_boxes,
               'sourceKind': 'operator-photo-review'}
        old_row = old.get(key, {})
        source_decision = old_row.get('sourceDecision')
        if source_decision in ('pending', 'confirmed', 'rejected', 'stale', 'unknown'):
            row['sourceDecision'] = source_decision
        comparable = {k:v for k,v in old_row.items() if k not in ('reviewedBy', 'reviewedAt', 'stale')}
        if comparable == row:
            row.update({k:old_row[k] for k in ('reviewedBy', 'reviewedAt') if k in old_row})
        else:
            row.update(reviewedBy=actor.strip(), reviewedAt=moment)
        rows.append(row)
    return {'version': VERSION, 'listingId': card_id, 'groupId': group, 'split': split,
            'rights': {'allowed': rights.get('allowed') is True, 'basis': rights.get('basis', ''),
                       'photoIds': sorted(photo_ids)},
            'duplicatesReviewed': document.get('duplicatesReviewed') is True,
            'rows': rows, 'updatedAt': moment, 'reviewedBy': actor.strip()}


def save(directory, card_id, document, *, expected_revision, actor):
    _, photos = _card(directory, card_id)
    current, previous = _load(directory, card_id)
    if type(expected_revision) is not int or expected_revision < 0 or current != expected_revision:
        raise PhotoTrainingConflict('Разметка изменилась. Обновите карточку.')
    if previous is None:
        card, _ = _card(directory, card_id)
        previous = {'rows': list(_proposals(card, photos).values())}
    normalized = normalize(document, card_id, set(photos), actor=actor, previous=previous)
    folder = owned_path(directory, 'photo-training'); folder.mkdir(mode=0o700, exist_ok=True)
    path = _db_path(directory)
    with sqlite3.connect(path, timeout=5) as db:
        path.chmod(0o600)
        db.execute('CREATE TABLE IF NOT EXISTS revisions (card_id TEXT, revision INTEGER, created REAL, actor TEXT, document TEXT, sha256 TEXT, PRIMARY KEY(card_id,revision))')
        db.execute('BEGIN IMMEDIATE')
        revision = db.execute('SELECT COALESCE(MAX(revision),0) FROM revisions WHERE card_id=?', (card_id,)).fetchone()[0]
        if revision != expected_revision:
            raise PhotoTrainingConflict('Разметка изменилась. Обновите карточку.')
        payload = canonical(normalized)
        db.execute('INSERT INTO revisions VALUES (?,?,?,?,?,?)',
                   (card_id, revision+1, time.time(), actor, payload, hashlib.sha256(payload.encode()).hexdigest()))
    return view(directory, card_id)


def export_manifest(directory, card_ids):
    """Export only explicitly verified labels, never raw proposals or paths."""
    if not isinstance(card_ids, list) or not 1 <= len(card_ids) <= 300 or len(set(card_ids)) != len(card_ids):
        raise ValueError('Выберите от 1 до 300 разных карточек')
    images, excluded, group_splits, photo_groups = {}, Counter(), defaultdict(set), defaultdict(set)
    source_revisions = []
    for card_id in card_ids:
        value = view(directory, card_id)
        doc = value['document']; photo_ids = {row['id'] for row in value['photos']}
        if not value['revision']:
            excluded['card-without-operator-review'] += 1; continue
        if not doc['groupId'] or doc['split'] == 'unassigned' or not doc['duplicatesReviewed']:
            excluded['group-split-or-duplicate-review-missing'] += 1; continue
        if doc['rights']['allowed'] is not True:
            excluded['image-use-permission-missing'] += 1; continue
        group_splits[doc['groupId']].add(doc['split'])
        source_revisions.append({'listingId': card_id, 'revision': value['revision'], 'sha256': content_sha(doc)})
        for row in doc['rows']:
            if row['photoId'] not in photo_ids:
                excluded['stale-original'] += 1; continue
            if not row['verified'] or row['state'] not in ('present', 'absent') or not row['exhaustive']:
                excluded[row['state']] += 1; continue
            # Resolve bytes afresh, so a replaced/removed original cannot inherit a label.
            import photo_store
            content, _, meta = photo_store.asset(directory, row['photoId'])
            if meta.get('kind') != 'original' or hashlib.sha256(content).hexdigest() != row['photoId']:
                raise ValueError('Оригинал разметки изменился')
            photo_groups[row['photoId']].add(doc['groupId'])
            image = images.setdefault(row['photoId'], {'imageId': row['photoId'], 'sha256': row['photoId'],
                'groupId': doc['groupId'], 'sourceSplit': doc['split'], 'domain': 'rental-apartment',
                'individualLicenseVerified': True, 'rightsBasis': doc['rights']['basis'],
                'boxes': [], 'verifiedClassIndices': [], 'negativeClassIndices': [], 'reviewedLabels': []})
            index = CLASS_IDS.index(row['category'])
            if index in image['verifiedClassIndices']:
                existing = next(x for x in image['reviewedLabels'] if x['category'] == row['category'])
                if existing['state'] != row['state'] or existing['boxes'] != row['boxes']:
                    raise ValueError('Разные карточки содержат противоречивые метки одного оригинала')
                continue
            image['verifiedClassIndices'].append(index)
            if row['state'] == 'absent': image['negativeClassIndices'].append(index)
            for box in row['boxes']:
                x,y,w,h = box['region']
                image['boxes'].append({'classIndex': index, 'xyxy': [x,y,x+w,y+h],
                    **{k:box[k] for k in ('instanceId','linkConfirmed') if k in box}})
            image['reviewedLabels'].append(copy.deepcopy(row))
    if any(len(values) > 1 for values in group_splits.values()):
        raise ValueError('Одна квартира оказалась в разных частях корпуса')
    if any(len(values) > 1 for values in photo_groups.values()):
        raise ValueError('Одинаковый оригинал отнесён к разным квартирам; объедините группы')
    splits = {name: [] for name in ('train', 'calibration', 'test')}
    for image in images.values(): splits[image['sourceSplit']].append(image)
    for rows in splits.values(): rows.sort(key=lambda row: row['imageId'])
    manifest = {'version': 1, 'kind': 'operator-rental-photo-corpus', 'domain': 'rental-apartment',
                'classes': [{'id':key} for key in CLASS_IDS], 'splits': splits,
                'sourceRevisions': source_revisions, 'excluded': dict(excluded),
                'automaticAcceptance': False, 'independenceVerified': False,
                'humanReviewDeclared': True, 'missingLabelsMeanAbsence': False,
                'limitations': ['Operator declarations require independent review.',
                    'Exact duplicates are blocked across groups; crop/re-encoding identity needs manual review.']}
    manifest['sha256'] = content_sha(manifest)
    return manifest
