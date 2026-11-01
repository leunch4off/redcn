"""Manual links between visible object instances and immutable photo regions.

This module does not compare image embeddings, infer unseen objects or sum model
counts. Only explicit reviewed links establish distinct visible instances. Even
a fully linked set does not establish the apartment's total inventory.
"""
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import time

from local_photo_ai import FEATURE_LABELS

VERSION = 1
MAX_OBSERVATIONS = 1000
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z')
_SHA = re.compile(r'[a-f0-9]{64}\Z')
_LISTING = re.compile(r'[a-f0-9]{32}\Z')
_GROUPS = frozenset(('roomFurniture', 'kitchenFurniture', 'homeCinema', 'balcony', 'loggia', 'closet', 'pantry'))


class InstanceReviewConflict(ValueError):
    """The editor must reload rather than overwrite another operator's review."""
    code = 'PHOTO_INSTANCE_REVISION_CONFLICT'


def _identity(value, pattern, message):
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError(message)
    return value


def _feature(value):
    value = 'bedAny' if value == 'bed' else value
    if not isinstance(value, str) or value not in FEATURE_LABELS or value in _GROUPS:
        raise ValueError('Нужен отдельный видимый предмет, а не общее свойство помещения')
    return value


def normalize_review(document, listing_id):
    """Keep a small evidence contract; never retain image bytes, notes or URLs."""
    _identity(listing_id, _LISTING, 'Неверный ID карточки для экземпляров')
    if not isinstance(document, dict) or type(document.get('version', VERSION)) is not int or document.get('version', VERSION) != VERSION:
        raise ValueError('Неверный формат проверки экземпляров')
    if document.get('listingId', listing_id) != listing_id:
        raise ValueError('Экземпляры относятся к другой карточке')
    values = document.get('observations', [])
    if not isinstance(values, list) or len(values) > MAX_OBSERVATIONS:
        raise ValueError('Допускается до 1000 наблюдений предметов')
    observations, seen = [], set()
    for raw in values:
        if not isinstance(raw, dict):
            raise ValueError('Наблюдение предмета должно быть объектом')
        identity = _identity(raw.get('id'), _ID, 'Неверный ID наблюдения')
        if identity in seen:
            raise ValueError('ID наблюдения повторяется')
        seen.add(identity)
        digest = _identity(raw.get('photoId'), _SHA, 'Нужен SHA-256 исходной фотографии')
        feature = _feature(raw.get('feature'))
        region = raw.get('region')
        if (not isinstance(region, list) or len(region) != 4
                or any(type(value) not in (int, float) or not math.isfinite(value) for value in region)
                or not all(0 <= value <= 1 for value in region)
                or region[2] <= 0 or region[3] <= 0
                or region[0]+region[2] > 1.000001 or region[1]+region[3] > 1.000001):
            raise ValueError('Нужна ненулевая область предмета внутри фотографии: x, y, ширина, высота')
        status = raw.get('status', 'unreviewed')
        if status not in ('unreviewed', 'confirmed', 'rejected'):
            raise ValueError('Неверный статус наблюдения')
        rounded = [round(float(value), 6) for value in region]
        if rounded[2] <= 0 or rounded[3] <= 0 or rounded[0]+rounded[2] > 1.000001 or rounded[1]+rounded[3] > 1.000001:
            raise ValueError('Область предмета слишком мала или выходит за фотографию')
        row = {'id': identity, 'photoId': digest, 'feature': feature,
               'region': rounded, 'status': status}
        instance = raw.get('instanceId')
        if instance not in (None, ''):
            row['instanceId'] = _identity(instance, _ID, 'Неверный ID экземпляра')
        row['linkConfirmed'] = bool(row.get('instanceId') and status == 'confirmed' and raw.get('linkConfirmed') is True)
        observations.append(row)
    confirmations = document.get('countConfirmations', {})
    if not isinstance(confirmations, dict) or len(confirmations) > len(FEATURE_LABELS):
        raise ValueError('Неверные подтверждения количества')
    counts = {}
    for key, count in confirmations.items():
        feature = _feature(key)
        if type(count) is not int or not 1 <= count <= MAX_OBSERVATIONS:
            raise ValueError('Подтверждённое количество должно быть целым числом от 1 до 1000')
        if feature in counts and counts[feature] != count:
            raise ValueError('Повторяющиеся подтверждения одного вида предмета противоречат друг другу')
        counts[feature] = count
    return {'version': VERSION, 'listingId': listing_id,
            'observations': observations, 'countConfirmations': counts}


def _photos(photo_ids):
    if not isinstance(photo_ids, (list, tuple, set, frozenset)) or len(photo_ids) > 500:
        raise ValueError('Нужен список исходных фотографий текущей карточки')
    return {_identity(value, _SHA, 'Неверный SHA-256 фотографии карточки') for value in photo_ids}


def _iou(left, right):
    width = max(0, min(left[0]+left[2], right[0]+right[2])-max(left[0], right[0]))
    height = max(0, min(left[1]+left[3], right[1]+right[3])-max(left[1], right[1]))
    intersection = width*height
    return intersection/(left[2]*left[3]+right[2]*right[3]-intersection)


def summarize_review(document, listing_id, *, photo_ids):
    normalized = normalize_review(document, listing_id)
    allowed = _photos(photo_ids)
    instances, issues, unlinked, affected = {}, [], [], set()
    for row in normalized['observations']:
        if row['status'] == 'rejected':
            continue
        if row['photoId'] not in allowed:
            issues.append({'code': 'photo-not-in-listing', 'observationIds': [row['id']], 'feature': row['feature']})
            affected.add(row['feature'])
            continue
        if row['status'] != 'confirmed' or not row['linkConfirmed']:
            unlinked.append(row['id']); affected.add(row['feature'])
            continue
        instances.setdefault(row['instanceId'], []).append(row)
    counts, accepted, duplicate_groups = {}, [], set()
    groups = list(instances.items())
    for index, (left_id, left_rows) in enumerate(groups):
        for right_id, right_rows in groups[index+1:]:
            if any(left['feature'] == right['feature'] and left['photoId'] == right['photoId']
                   and _iou(left['region'], right['region']) >= .8 for left in left_rows for right in right_rows):
                issues.append({'code': 'overlapping-distinct-instances', 'instanceIds': [left_id, right_id]})
                duplicate_groups.update((left_id, right_id))
    for identity, rows in instances.items():
        features = {row['feature'] for row in rows}
        conflict = identity in duplicate_groups
        if len(features) != 1:
            issues.append({'code': 'instance-feature-conflict', 'instanceId': identity,
                           'observationIds': [row['id'] for row in rows], 'features': sorted(features)})
            conflict = True
        # Two disjoint confirmed regions in the same immutable image cannot
        # silently become one instance. Overlapping duplicate boxes may agree.
        for index, left in enumerate(rows):
            for right in rows[index+1:]:
                if left['photoId'] == right['photoId'] and _iou(left['region'], right['region']) < .8:
                    issues.append({'code': 'same-frame-distinct-regions', 'instanceId': identity,
                                   'photoId': left['photoId'], 'observationIds': [left['id'], right['id']]})
                    conflict = True
        if conflict:
            affected.update(features)
            continue
        feature = rows[0]['feature']
        counts[feature] = counts.get(feature, 0)+1
        accepted.append({'id': identity, 'feature': feature,
                         'observationIds': [row['id'] for row in rows],
                         'photoIds': list(dict.fromkeys(row['photoId'] for row in rows)),
                         'reviewedByOperator': True})
    confirmed = {}
    for feature, count in normalized['countConfirmations'].items():
        if feature in affected or counts.get(feature) != count:
            issues.append({'code': 'count-confirmation-conflict', 'feature': feature,
                           'requested': count, 'linkedVisible': counts.get(feature, 0)})
        else:
            confirmed[feature] = count
    return {'version': VERSION, 'listingId': listing_id,
            'state': 'review-required' if issues or unlinked else 'manually-linked' if accepted else 'empty',
            'instances': accepted, 'confirmedVisibleCounts': counts,
            'manuallyConfirmedVisibleCounts': confirmed, 'unlinkedObservationIds': unlinked,
            'issues': issues, 'apartmentTotalExact': False,
            'countMeaning': 'distinct-manually-linked-visible-instances',
            'automaticCrossViewMatchingAccepted': False}


def _connection(directory):
    folder = Path(directory)/'photo-instance-reviews'
    if folder.is_symlink():
        raise ValueError('Каталог проверок экземпляров не должен быть ссылкой')
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = folder/'revisions.sqlite3'
    if path.is_symlink():
        raise ValueError('База проверок экземпляров не должна быть ссылкой')
    db = sqlite3.connect(path, timeout=5)
    path.chmod(0o600)
    db.execute('CREATE TABLE IF NOT EXISTS revisions (listing_id TEXT NOT NULL, revision INTEGER NOT NULL, created REAL NOT NULL, actor TEXT NOT NULL, document TEXT NOT NULL, sha256 TEXT NOT NULL, PRIMARY KEY(listing_id, revision))')
    return db


def save_review(directory, listing_id, document, *, photo_ids, expected_revision, actor='operator'):
    normalized = normalize_review(document, listing_id)
    allowed = _photos(photo_ids)
    if any(row['photoId'] not in allowed for row in normalized['observations']):
        raise ValueError('Наблюдение ссылается на фотографию другой или изменённой карточки')
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError('Нужна текущая версия проверки экземпляров')
    if not isinstance(actor, str) or not actor.strip() or len(actor) > 150 or re.search(r'[\x00-\x1f\x7f]', actor):
        raise ValueError('Нужен автор ручной проверки')
    payload = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    digest = hashlib.sha256(payload.encode()).hexdigest()
    db = _connection(directory)
    try:
        db.execute('BEGIN IMMEDIATE')
        current = db.execute('SELECT COALESCE(MAX(revision),0) FROM revisions WHERE listing_id=?', (listing_id,)).fetchone()[0]
        if current != expected_revision:
            raise InstanceReviewConflict('Проверка экземпляров уже изменена. Обновите карточку.')
        revision = current+1
        db.execute('INSERT INTO revisions VALUES (?,?,?,?,?,?)',
                   (listing_id, revision, time.time(), actor.strip(), payload, digest))
        db.commit()
    except BaseException:
        db.rollback(); raise
    finally:
        db.close()
    return {'revision': revision, 'sha256': digest, 'document': normalized,
            'summary': summarize_review(normalized, listing_id, photo_ids=allowed)}


def load_review(directory, listing_id, *, photo_ids, revision=None):
    _identity(listing_id, _LISTING, 'Неверный ID карточки для экземпляров')
    if revision is not None and (type(revision) is not int or revision < 1):
        raise ValueError('Неверная версия проверки экземпляров')
    db = _connection(directory)
    try:
        row = db.execute('SELECT revision,document,sha256,actor,created FROM revisions WHERE listing_id=?'+(' AND revision=?' if revision is not None else '')+' ORDER BY revision DESC LIMIT 1',
                         (listing_id, revision) if revision is not None else (listing_id,)).fetchone()
    finally:
        db.close()
    if row is None:
        empty = {'version': VERSION, 'listingId': listing_id, 'observations': [], 'countConfirmations': {}}
        return {'revision': 0, 'document': empty, 'summary': summarize_review(empty, listing_id, photo_ids=photo_ids)}
    if hashlib.sha256(row[1].encode()).hexdigest() != row[2]:
        raise ValueError('Контрольная сумма проверки экземпляров не совпадает')
    document = normalize_review(json.loads(row[1]), listing_id)
    return {'revision': row[0], 'document': document, 'sha256': row[2], 'actor': row[3], 'created': row[4],
            'summary': summarize_review(document, listing_id, photo_ids=photo_ids)}
