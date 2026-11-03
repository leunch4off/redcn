"""Explicitly reviewed, body-only replacement of saved listing copy.

Planning and preparation use read-only SQLite connections. No model, learning
approval or photo inference runs here. Application requires a separate review
of the prepared receipt, rebuilds the live factual brief, and compares the
complete original payload in the same transaction as its revision record.
"""
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
import uuid

from individual_copy import build_brief, validate_individual
from publication_provenance import bind_generation, metadata_for, part_origin, text_parts
from workspace_ownership import owned_path


VERSION = 1
_ID = re.compile(r'[a-f0-9]{32}')
_LEGACY = {'unknown', 'rules-unverified', 'legacy-model'}


class RefreshConflict(ValueError):
    """The exact reviewed card, rules or receipt is no longer current."""


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False)


def _sha(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _seal(value, key):
    value[key] = _sha(_json({name: item for name, item in value.items() if name != key}))
    return value


def _check_seal(value, key, kind=None):
    if not isinstance(value, dict) or (kind is not None and
            (value.get('version') != VERSION or value.get('kind') != kind)):
        raise ValueError('Неверная версия документа обновления описаний')
    expected = _sha(_json({name: item for name, item in value.items() if name != key}))
    if value.get(key) != expected:
        raise RefreshConflict('Документ обновления изменён после проверки')


def _stamp(value=None):
    value = time.time() if value is None else value
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError('Неверное время обновления')
    return float(value)


def _db_path(directory):
    path = owned_path(directory, 'listings.sqlite3')
    if not path.is_file():
        raise ValueError('Хранилище объявлений не найдено; новое хранилище не создаётся')
    for suffix in ('-wal', '-shm', '-journal'):
        owned_path(directory, 'listings.sqlite3'+suffix)
    return path.resolve()


def _workspace(directory):
    return _sha(str(_db_path(directory)))


def _connect(directory, *, writable=False):
    path = _db_path(directory)
    db = sqlite3.connect(path.as_uri()+('?mode=rw' if writable else '?mode=ro'),
                         uri=True, isolation_level=None, timeout=10)
    if not writable:
        db.execute('PRAGMA query_only=ON')
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='results'").fetchone():
        db.close()
        raise ValueError('В хранилище нет сохранённых объявлений')
    return db


def _rules(directory):
    path = owned_path(directory, 'phone-profile.json')
    if not path.exists():
        return {}
    if path.stat().st_size > 5_000_000:
        raise ValueError('Профиль рабочего пространства слишком велик')
    profile = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(profile, dict) or not isinstance(profile.get('rules', {}), dict):
        raise ValueError('Неверные правила рабочего пространства')
    return profile.get('rules', {})


def _snapshot(identifier, updated, payload):
    if not isinstance(identifier, str) or not _ID.fullmatch(identifier):
        raise ValueError('Неверный ID сохранённого объявления')
    if not isinstance(payload, str):
        raise ValueError('Сохранённая карточка должна быть текстовым JSON')
    card = json.loads(payload)
    if not isinstance(card, dict) or not isinstance(card.get('fields'), dict):
        raise ValueError('В сохранённой карточке нет полей квартиры')
    if not isinstance(card.get('body'), str):
        raise ValueError('В сохранённой карточке нет текстового описания')
    info = card.get('templateInfo') if isinstance(card.get('templateInfo'), dict) else {}
    manual = info.get('manualParts', [])
    if not isinstance(manual, list) or any(not isinstance(part, str) for part in manual):
        raise ValueError('Неверные отметки ручного текста')
    parts = text_parts(card)
    origin = part_origin(card, 'body')
    metadata = metadata_for(card)
    # Old explicit manual scope is still a veto, even before digest-bound
    # per-part attribution existed. A manual title says nothing about the body.
    if metadata.get('generationMode') == 'manual' and metadata.get('part', 'all') in ('body', 'all'):
        origin = 'manual'
    value = {'id': identifier, 'updated': _stamp(updated), 'payloadSha256': _sha(payload),
        'oldBodySha256': _sha(card['body']), 'oldBody': card['body'],
        'savedTitle': parts['title'], 'manualParts': list(manual),
        'bodyOrigin': origin}
    value['status'] = 'skipped' if value['bodyOrigin'] == 'manual' else 'review-required'
    if value['status'] == 'skipped':
        value['reason'] = 'Ручное описание сохранено без изменений'
    return value, card


def _read_rows(directory):
    db = _connect(directory)
    try:
        return {identifier: (updated, payload) for identifier, updated, payload in
                db.execute('SELECT id,updated,payload FROM results ORDER BY id')}
    finally:
        db.close()


def plan_refresh(directory, identifiers=None, *, now=None):
    """Return private original text and immutable hashes, without DB writes."""
    available = _read_rows(directory)
    selected = list(available) if identifiers is None else list(identifiers)
    if len(set(selected)) != len(selected) or any(identifier not in available for identifier in selected):
        raise ValueError('Повторяющаяся или отсутствующая карточка в плане')
    rows = [_snapshot(identifier, *available[identifier])[0] for identifier in selected]
    return _seal({'version': VERSION, 'kind': 'individual-body-refresh-plan',
        'createdAt': _stamp(now), 'workspaceSha256': _workspace(directory),
        'rulesSha256': _sha(_json(_rules(directory))), 'rows': rows,
        'readOnly': True, 'modelCalls': 0}, 'planSha256')


def _review(value, snapshot=None):
    if not isinstance(value, dict):
        raise ValueError('Нужна явная независимая редакционная проверка')
    actor, reason = value.get('actor'), value.get('reason')
    if (not isinstance(actor, str) or not actor.strip() or len(actor) > 120
            or not isinstance(reason, str) or len(reason.strip()) < 10 or len(reason) > 4000
            or value.get('humanVerified') is not False):
        raise ValueError('Нужны автор, причина проверки и humanVerified=false без подмены проверки человеком')
    if snapshot is not None:
        if value.get('oldBodyReviewed') is not True or value.get('oldBodySha256') != snapshot['oldBodySha256']:
            raise ValueError('Нужна проверка точного старого описания с его SHA-256')
        basis = value.get('replacementBasis')
        if basis not in ('legacy-template', 'reviewed-rewrite'):
            raise ValueError('Не указано основание замены старого описания')
        if snapshot['bodyOrigin'] in _LEGACY and basis != 'legacy-template':
            raise ValueError('Неизвестное происхождение: требуется явная проверка legacy-template, одних метаданных недостаточно')
    return copy.deepcopy(value)


def _same(snapshot, expected):
    return all(snapshot.get(key) == expected.get(key) for key in
        ('id', 'updated', 'payloadSha256', 'oldBodySha256', 'manualParts', 'bodyOrigin', 'savedTitle'))


def _validated(card, body, rules):
    brief = build_brief(copy.deepcopy(card['fields']), rules=rules)
    titles = [text_parts(card)['title']]
    # Legacy cards can retain both aliases. Both remain in the saved payload;
    # never use a favourable candidate title to mask an invalid visible alias.
    if isinstance(card.get('title'), str) and card['title'].strip() and card['title'] not in titles:
        titles.append(card['title'])
    issues = []
    for title in titles:
        issues.extend(validate_individual({'title': title, 'body': body}, brief, part='body'))
    if issues:
        raise ValueError('Описание не прошло проверку с сохранённым заголовком: '+' · '.join(dict.fromkeys(issues)))
    return brief


def prepare_refresh(directory, plan, candidates, *, reconcile_photo_journal=False, now=None):
    """Validate proposed bodies. This receipt is not permission to apply them."""
    _check_seal(plan, 'planSha256', 'individual-body-refresh-plan')
    rules = _rules(directory)
    if plan['workspaceSha256'] != _workspace(directory) or plan['rulesSha256'] != _sha(_json(rules)):
        raise RefreshConflict('Рабочее пространство или правила изменились после плана')
    proposed = candidates.get('rows') if isinstance(candidates, dict) else candidates
    if not isinstance(proposed, list) or not proposed or len(proposed) > 1000:
        raise ValueError('Нужен ограниченный список проверенных описаний')
    planned = {row['id']: row for row in plan['rows']}
    saved = _read_rows(directory)
    rows, seen = [], set()
    for candidate in proposed:
        identifier = candidate.get('id') if isinstance(candidate, dict) else None
        if identifier not in planned or identifier in seen:
            raise ValueError('Кандидат повторяется или отсутствует в исходном плане')
        seen.add(identifier)
        result = {'id': identifier, 'status': 'blocked'}
        try:
            if identifier not in saved:
                raise RefreshConflict('Карточка удалена после плана')
            snapshot, card = _snapshot(identifier, *saved[identifier])
            if not _same(snapshot, planned[identifier]):
                raise RefreshConflict('Карточка изменена после плана; требуется новый план')
            result.update(snapshot)
            if snapshot['bodyOrigin'] == 'manual':
                rows.append(result)
                continue
            body = candidate.get('body')
            if not isinstance(body, str) or not body.strip() or len(body) > 20_000 or '\x00' in body:
                raise ValueError('Неверный текст нового описания')
            if body == snapshot['oldBody']:
                result.update(status='skipped', reason='Описание уже совпадает; запись не требуется')
                rows.append(result)
                continue
            review = _review(candidate.get('review'), snapshot)
            reference_title = candidate.get('title')
            if reference_title is not None and (not isinstance(reference_title, str) or len(reference_title) > 240):
                raise ValueError('Неверный справочный заголовок кандидата')
            brief = _validated(card, body, rules)
            result.update(status='ready', body=body, newBodySha256=_sha(body), review=review,
                factsHash=brief['factsHash'], contract=brief['contract'],
                inventoryCount=len(brief['inventory']), referenceTitle=reference_title)
            _seal(result, 'candidateSha256')
        except (ValueError, TypeError, KeyError) as error:
            result.update(status='blocked', reason=str(error)[:2000])
        rows.append(result)
    return _seal({'version': VERSION, 'kind': 'individual-body-refresh-prepared',
        'createdAt': _stamp(now), 'workspaceSha256': plan['workspaceSha256'],
        'rulesSha256': plan['rulesSha256'], 'planSha256': plan['planSha256'],
        'reconcilePhotoJournal': bool(reconcile_photo_journal), 'rows': rows,
        'counts': {state: sum(row['status'] == state for row in rows) for state in ('ready', 'skipped', 'blocked')},
        'readOnly': True, 'modelCalls': 0, 'requiresSeparateReview': True}, 'preparedSha256')


def write_private_json(path, value):
    """Write a new private artifact, refusing to replace an existing receipt."""
    _write_private(Path(path), (_json(value)+'\n').encode('utf-8'))


def _write_private(path, value):
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())


def _backup(base, originals, prepared, review):
    base = Path(base).expanduser().absolute()
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    if base.is_symlink() or not base.is_dir() or stat.S_IMODE(base.stat().st_mode) & 0o077:
        raise ValueError('Для резервных копий нужна отдельная закрытая папка с правами 0700')
    destination = base/('body-refresh-'+uuid.uuid4().hex)
    destination.mkdir(mode=0o700)
    for row, payload, card, brief in originals:
        _write_private(destination/(row['id']+'.payload.json'), payload.encode('utf-8'))
        _write_private(destination/(row['id']+'.body.txt'), card['body'].encode('utf-8'))
    write_private_json(destination/'prepared.private.json', prepared)
    write_private_json(destination/'review.private.json', review)
    write_private_json(destination/'manifest.private.json', {'version': VERSION,
        'purpose': 'Exact pre-write originals; application is recorded atomically in individual_body_revisions',
        'preparedSha256': prepared['preparedSha256'], 'reviewSha256': _sha(_json(review)),
        'rows': [{key: row[key] for key in ('id', 'payloadSha256', 'oldBodySha256', 'candidateSha256')}
                 for row, payload, card, brief in originals]})
    for directory in (destination, base):
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    return destination


def _replacement(card, row, brief, prepared):
    from fact_ledger import validate_result_fields
    updated = copy.deepcopy(card)
    updated['body'] = row['body']
    info = updated.get('templateInfo')
    if isinstance(info, dict):
        info.pop('descriptionId', None)
        info.pop('copyEngine', None)
    metadata = {'part': 'body', 'status': 'saved', 'generationMode': 'assistant-editorial',
        'textSource': 'assistant-reviewed', 'modelAccepted': False, 'fallback': False,
        'modelContribution': 'none', 'backend': 'assistant-editorial', 'ai': False,
        'factualTextSource': 'confirmed-card-facts', 'factsHash': brief['factsHash'],
        'contract': brief['contract'], 'inventoryCount': len(brief['inventory'])}
    updated = bind_generation(updated, metadata, previous=card, parts=('body',))
    if prepared['reconcilePhotoJournal']:
        from photo_auto_policy import reconcile_saved_review_journal
        updated = reconcile_saved_review_journal(updated)
    validate_result_fields(updated)
    return updated


def apply_refresh(directory, prepared, review, *, backup_directory, now=None):
    """Atomically apply only separately approved, still-current body rows."""
    from card_catalog import ensure_entries, record_save
    _check_seal(prepared, 'preparedSha256', 'individual-body-refresh-prepared')
    _review(review)
    if (review.get('version') != VERSION or review.get('kind') != 'individual-body-refresh-review'
            or review.get('approved') is not True or review.get('preparedSha256') != prepared['preparedSha256']):
        raise ValueError('Нужна отдельная явная проверка именно этого подготовленного документа')
    rules = _rules(directory)
    if prepared['workspaceSha256'] != _workspace(directory) or prepared['rulesSha256'] != _sha(_json(rules)):
        raise RefreshConflict('Рабочее пространство или правила изменились; подготовьте новый документ')
    approvals = review.get('rows')
    if not isinstance(approvals, list) or not approvals:
        raise ValueError('В проверке не выбраны описания для применения')
    ready = {row['id']: row for row in prepared['rows'] if row.get('status') == 'ready'}
    saved, originals, replacements, seen = _read_rows(directory), [], {}, set()
    for approval in approvals:
        identifier = approval.get('id') if isinstance(approval, dict) else None
        row = ready.get(identifier)
        if row is None or identifier in seen or approval.get('candidateSha256') != row.get('candidateSha256'):
            raise ValueError('Проверка не соответствует единственному готовому кандидату')
        seen.add(identifier)
        _check_seal(row, 'candidateSha256')
        if identifier not in saved:
            raise RefreshConflict('Карточка удалена после подготовки')
        updated, payload = saved[identifier]
        snapshot, card = _snapshot(identifier, updated, payload)
        if not _same(snapshot, row) or snapshot['bodyOrigin'] == 'manual':
            raise RefreshConflict('Карточка изменена или описание стало ручным; обновление отменено')
        _review(row['review'], snapshot)
        if _sha(row['body']) != row['newBodySha256']:
            raise RefreshConflict('Текст кандидата изменился после проверки')
        brief = _validated(card, row['body'], rules)
        if brief['factsHash'] != row['factsHash'] or brief['contract'] != row['contract']:
            raise RefreshConflict('Проверенные факты изменились после подготовки')
        originals.append((row, payload, card, brief))
        replacements[identifier] = _replacement(card, row, brief, prepared)
    backup = _backup(backup_directory, originals, prepared, review)
    review_sha = _sha(_json(review))
    db = _connect(directory, writable=True)
    applied = []
    try:
        db.execute('BEGIN IMMEDIATE')
        # Node/semantic checks run outside the lock. These exact comparisons
        # bind their inputs to the transaction, including every unrelated field.
        for row, payload, card, brief in originals:
            current = db.execute('SELECT updated,payload FROM results WHERE id=?', (row['id'],)).fetchone()
            if current != (row['updated'], payload):
                raise RefreshConflict('Параллельное изменение карточки; весь пакет отменён')
        if _sha(_json(_rules(directory))) != prepared['rulesSha256']:
            raise RefreshConflict('Правила изменились перед сохранением; весь пакет отменён')
        ensure_entries(db, Path(directory))
        db.execute('''CREATE TABLE IF NOT EXISTS individual_body_revisions (
            revision INTEGER PRIMARY KEY AUTOINCREMENT, result_id TEXT NOT NULL,
            stamp REAL NOT NULL, actor TEXT NOT NULL, source TEXT NOT NULL,
            previous_updated REAL NOT NULL, updated REAL NOT NULL,
            previous_payload_sha256 TEXT NOT NULL, previous_body_sha256 TEXT NOT NULL,
            body_sha256 TEXT NOT NULL, previous_payload TEXT NOT NULL,
            previous_body TEXT NOT NULL, body TEXT NOT NULL,
            prepared_sha256 TEXT NOT NULL, candidate_sha256 TEXT NOT NULL,
            review_sha256 TEXT NOT NULL, review_payload TEXT NOT NULL,
            backup_directory TEXT NOT NULL)''')
        greatest = db.execute('SELECT COALESCE(MAX(updated),0) FROM results').fetchone()[0]
        stamp = max(_stamp(now), _stamp(greatest)+0.000001)
        for row, payload, card, brief in originals:
            replacement = replacements[row['id']]
            new_payload = _json(replacement)
            cursor = db.execute('UPDATE results SET payload=?,updated=? WHERE id=? AND payload=? AND updated=?',
                (new_payload, stamp, row['id'], payload, row['updated']))
            if cursor.rowcount != 1:
                raise RefreshConflict('Сохранение не прошло сравнение полной карточки; весь пакет отменён')
            record_save(db, Path(directory), row['id'], card, replacement, at=stamp)
            revision = db.execute('INSERT INTO individual_body_revisions '
                '(result_id,stamp,actor,source,previous_updated,updated,previous_payload_sha256,'
                'previous_body_sha256,body_sha256,previous_payload,previous_body,body,'
                'prepared_sha256,candidate_sha256,review_sha256,review_payload,backup_directory) '
                'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (row['id'], stamp, review['actor'], 'assistant-editorial', row['updated'], stamp,
                 row['payloadSha256'], row['oldBodySha256'], row['newBodySha256'], payload,
                 card['body'], row['body'], prepared['preparedSha256'], row['candidateSha256'],
                 review_sha, _json({'candidate': row['review'], 'application': review}), str(backup))).lastrowid
            applied.append({'id': row['id'], 'revision': revision, 'updated': stamp,
                'payloadSha256': _sha(new_payload), 'oldBodySha256': row['oldBodySha256'],
                'newBodySha256': row['newBodySha256'], 'candidateSha256': row['candidateSha256']})
            stamp += 0.000001
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()
    return _seal({'version': VERSION, 'kind': 'individual-body-refresh-applied',
        'preparedSha256': prepared['preparedSha256'], 'reviewSha256': review_sha,
        'backupDirectory': str(backup), 'rows': applied, 'applied': len(applied),
        'modelCalls': 0, 'generationMode': 'assistant-editorial', 'humanVerified': False}, 'appliedSha256')
