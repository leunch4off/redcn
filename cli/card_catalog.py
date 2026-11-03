"""Catalogue order, publication edits and personal viewed state.

These records do not change a card payload or its synchronization timestamp.
Legacy dates are recovered only from stored import/revision evidence; an
unknown creation date stays unknown while its first observed position is kept.
"""
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import time

import redcn


_ID = re.compile(r'[a-f0-9]{32}')
_SCOPE = re.compile(r'(?:owner|phone:[a-f0-9]{64})')


def create_tables(db):
    db.execute('''CREATE TABLE IF NOT EXISTS card_catalog_state (
        card_id TEXT PRIMARY KEY NOT NULL CHECK(length(card_id)=32 AND card_id NOT GLOB '*[^0-9a-f]*'),
        order_stamp REAL NOT NULL CHECK(order_stamp>=0),
        added_at REAL CHECK(added_at>=0),
        origin TEXT NOT NULL CHECK(origin IN ('created','import-journal','fact-revision','legacy-snapshot')),
        publication_digest TEXT NOT NULL CHECK(length(publication_digest)=64 AND publication_digest NOT GLOB '*[^0-9a-f]*'),
        edited_at REAL CHECK(edited_at>=0))''')
    db.execute('''CREATE TABLE IF NOT EXISTS card_catalog_views (
        card_id TEXT NOT NULL CHECK(length(card_id)=32 AND card_id NOT GLOB '*[^0-9a-f]*'),
        viewer_scope TEXT NOT NULL CHECK(viewer_scope='owner' OR (length(viewer_scope)=70 AND substr(viewer_scope,1,6)='phone:' AND substr(viewer_scope,7) NOT GLOB '*[^0-9a-f]*')),
        viewed_at REAL NOT NULL CHECK(viewed_at>=0),
        PRIMARY KEY(card_id,viewer_scope))''')


def _stamp(value):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
        except (ValueError, OverflowError):
            return None
    return float(value) if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def _text(value):
    return value.replace('\r\n', '\n').replace('\r', '\n').strip() if isinstance(value, str) else ''


def publication_digest(card):
    card = card if isinstance(card, dict) else {}
    titles = card.get('titles')
    # Only the selected publication title belongs to the catalogue. Reordering
    # unused alternative titles is not an edit of the published text.
    title = card.get('title') if isinstance(card.get('title'), str) else titles[0] if isinstance(titles, list) and titles else ''
    values = [_text(title), _text(card.get('body', card.get('output', ''))), _text(card.get('tags', ''))]
    return hashlib.sha256(json.dumps(values, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def viewer_scope(role='owner', session=None):
    if role == 'owner':
        return 'owner'
    if role not in ('editor', 'viewer') or not isinstance(session, str) or not session:
        raise ValueError('Нужна действующая сессия просмотра')
    return 'phone:' + hashlib.sha256(session.encode()).hexdigest()


def _scope(value):
    if not isinstance(value, str) or not _SCOPE.fullmatch(value):
        raise ValueError('Неверное пространство отметок просмотра')
    return value


def _legacy_origins(db, directory, identifiers):
    dates = {}
    journal = Path(directory) / 'import-jobs' / 'jobs.sqlite3'
    if journal.is_file() and not journal.is_symlink():
        try:
            with sqlite3.connect(journal.resolve().as_uri()+'?mode=ro', uri=True, timeout=2) as imports:
                for card_id, stamp in imports.execute("SELECT result_id,MIN(created) FROM jobs WHERE kind='import' GROUP BY result_id"):
                    stamp = _stamp(stamp)
                    if card_id in identifiers and stamp is not None:
                        dates[card_id] = (stamp, 'import-journal')
        except sqlite3.Error:
            # A busy/older optional import journal cannot block the catalogue.
            pass
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='fact_revisions'").fetchone():
        for card_id, stamp in db.execute('SELECT result_id,stamp FROM fact_revisions ORDER BY revision'):
            stamp = _stamp(stamp)
            if card_id in identifiers and card_id not in dates and stamp is not None:
                dates[card_id] = (stamp, 'fact-revision')
    return dates


def _legacy_saved_edits(db, identifiers):
    """Only two distinct persisted save snapshots prove an earlier text edit."""
    edited = {}
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='history'").fetchone():
        return edited
    first = {}
    for stamp, payload in db.execute('SELECT stamp,payload FROM history ORDER BY stamp,id'):
        try:
            row = json.loads(payload)
        except (ValueError, TypeError):
            continue
        if not isinstance(row, dict) or row.get('kind') != 'save':
            continue
        fields = row.get('fields') if isinstance(row.get('fields'), dict) else {}
        card_id = row.get('resultId') or fields.get('resultId')
        stamp = _stamp(stamp)
        if card_id not in identifiers or stamp is None:
            continue
        digest = publication_digest(row)
        if card_id in first and digest != first[card_id]:
            edited[card_id] = stamp
        first.setdefault(card_id, digest)
    return edited


def ensure_entries(db, directory):
    """Initialize missing metadata only; never re-rank an existing entry."""
    create_tables(db)
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='results'").fetchone():
        return
    rows = db.execute('SELECT r.id,r.updated,r.payload FROM results r LEFT JOIN card_catalog_state c ON c.card_id=r.id WHERE c.card_id IS NULL ORDER BY r.updated,r.id').fetchall()
    if not rows:
        return
    identifiers = {row[0] for row in rows if isinstance(row[0], str) and _ID.fullmatch(row[0])}
    dates = _legacy_origins(db, directory, identifiers)
    edited = _legacy_saved_edits(db, identifiers)
    for card_id, updated, payload in rows:
        if card_id not in identifiers:
            continue
        try:
            card = json.loads(payload)
        except (ValueError, TypeError):
            continue
        if not isinstance(card, dict):
            continue
        added, origin = dates.get(card_id, (None, 'legacy-snapshot'))
        order = added if added is not None else (_stamp(updated) or 0.0)
        db.execute('INSERT OR IGNORE INTO card_catalog_state VALUES (?,?,?,?,?,?)',
                   (card_id, order, added, origin, publication_digest(card), edited.get(card_id)))


def record_save(db, directory, card_id, previous, current, *, at=None, publication_edit=True):
    """Call in the same transaction as the result write, after ensure_entries."""
    create_tables(db)
    stamp = time.time() if at is None else _stamp(at)
    if stamp is None:
        raise ValueError('Неверное время сохранения')
    digest = publication_digest(current)
    existing = db.execute('SELECT publication_digest FROM card_catalog_state WHERE card_id=?', (card_id,)).fetchone()
    if not existing:
        greatest = db.execute('SELECT COALESCE(MAX(order_stamp),0) FROM card_catalog_state').fetchone()[0]
        db.execute('INSERT INTO card_catalog_state VALUES (?,?,?,?,?,NULL)',
                   (card_id, max(stamp, greatest+0.000001), stamp, 'created', digest))
        return
    changed = publication_edit and previous is not None and publication_digest(previous) != digest
    db.execute('UPDATE card_catalog_state SET publication_digest=?,edited_at=CASE WHEN ? THEN ? ELSE edited_at END WHERE card_id=?',
               (digest, bool(changed), stamp, card_id))


def summaries(db, identifiers, *, viewer='owner'):
    scope = _scope(viewer)
    if not identifiers:
        return {}
    placeholders = ','.join('?' for _ in identifiers)
    rows = db.execute('SELECT c.card_id,c.order_stamp,c.added_at,c.origin,c.edited_at,v.viewed_at '
                      'FROM card_catalog_state c LEFT JOIN card_catalog_views v ON v.card_id=c.card_id AND v.viewer_scope=? '
                      'WHERE c.card_id IN ('+placeholders+')', (scope, *identifiers)).fetchall()
    return {card_id: {'catalogOrder': order, 'addedAt': added, 'catalogOrigin': origin,
                     'contentEdited': edited is not None, 'contentEditedAt': edited,
                     'viewed': viewed is not None, 'viewedAt': viewed}
            for card_id, order, added, origin, edited, viewed in rows}


def mark_viewed(directory, card_id, *, viewer='owner'):
    if not isinstance(card_id, str) or not _ID.fullmatch(card_id):
        raise ValueError('Неверный ID карточки')
    scope = _scope(viewer)
    with redcn.open_cache(directory) as db:
        db.execute('BEGIN IMMEDIATE')
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='results'").fetchone():
            raise ValueError('Карточка не найдена')
        row = db.execute('SELECT payload FROM results WHERE id=?', (card_id,)).fetchone()
        if not row:
            raise ValueError('Карточка не найдена')
        card = json.loads(row[0])
        if not isinstance(card, dict) or not isinstance(card.get('fields'), dict) or card['fields'].get('isDemo') is True:
            raise ValueError('Карточка не найдена')
        ensure_entries(db, directory)
        db.execute('INSERT OR IGNORE INTO card_catalog_views VALUES (?,?,?)', (card_id, scope, time.time()))
        return {'id': card_id, **summaries(db, [card_id], viewer=scope)[card_id]}
