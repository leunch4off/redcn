"""Versioned operator approvals; callers authenticate before dispatch.

This ledger never rewrites the active personal dictionary or starts a model.
Machine drafts remain proposals until a separate, version-checked approval.
"""
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import secrets
import sqlite3


class DictionaryConflict(ValueError):
    pass


def _text(value, limit, label, *, empty=False):
    if not isinstance(value, str) or len(value) > limit or re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', value):
        raise ValueError('Неверный текст: ' + label)
    value = value.strip()
    if not value and not empty:
        raise ValueError('Заполните поле: ' + label)
    return value


def _key(value):
    return re.sub(r'\s+', ' ', value).casefold()


def _now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def _connect(directory):
    folder = Path(directory)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / 'dictionary-reviews.sqlite3'
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.executescript('''
      CREATE TABLE IF NOT EXISTS dictionary_revision (id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL);
      INSERT OR IGNORE INTO dictionary_revision VALUES (1,0);
      CREATE TABLE IF NOT EXISTS dictionary_entries (
        id TEXT PRIMARY KEY, ru TEXT NOT NULL, ru_key TEXT NOT NULL, zh TEXT NOT NULL,
        state TEXT NOT NULL, source TEXT NOT NULL, origin TEXT NOT NULL,
        proposed_by TEXT NOT NULL, reviewed_by TEXT, created TEXT NOT NULL,
        reviewed_at TEXT, previous_zh TEXT, revision INTEGER NOT NULL, reason TEXT
      );
      CREATE UNIQUE INDEX IF NOT EXISTS dictionary_active_ru ON dictionary_entries(ru_key) WHERE state='approved';
      CREATE TABLE IF NOT EXISTS dictionary_events (
        revision INTEGER PRIMARY KEY, at TEXT NOT NULL, actor TEXT NOT NULL,
        action TEXT NOT NULL, entry_id TEXT NOT NULL, ru TEXT NOT NULL,
        before_zh TEXT, after_zh TEXT, source TEXT NOT NULL, reason TEXT
      );
    ''')
    connection.commit()
    path.chmod(0o600)
    return connection


def _snapshot(connection):
    revision = connection.execute('SELECT revision FROM dictionary_revision WHERE id=1').fetchone()[0]
    entries = [dict(row) for row in connection.execute('SELECT * FROM dictionary_entries ORDER BY revision DESC,id DESC LIMIT 500')]
    approved = list(connection.execute("SELECT ru,zh FROM dictionary_entries WHERE state='approved' ORDER BY ru_key"))
    events = [dict(row) for row in connection.execute('SELECT * FROM dictionary_events ORDER BY revision DESC LIMIT 200')]
    return {'version': 1, 'revision': revision, 'rules': {row['ru']: row['zh'] for row in approved},
            'entries': entries, 'events': events, 'truncatedEntries': connection.execute('SELECT COUNT(*) FROM dictionary_entries').fetchone()[0] > len(entries)}


def snapshot(directory):
    connection = _connect(directory)
    try:
        return _snapshot(connection)
    finally:
        connection.close()


def mutate(directory, data, *, actor='operator'):
    if not isinstance(data, dict):
        raise ValueError('Нужен объект изменения словаря')
    action = data.get('action')
    if action not in ('propose', 'approve', 'reject'):
        raise ValueError('Неизвестное действие словаря')
    if type(data.get('expectedRevision')) is not int or data['expectedRevision'] < 0:
        raise ValueError('Обновите версию словаря перед изменением')
    actor = _text(actor, 100, 'автор действия')
    allowed = {'action', 'expectedRevision', 'ru', 'zh', 'source', 'origin'} if action == 'propose' else {'action', 'expectedRevision', 'id', 'replaceApproved', 'expectedPrevious', 'reason'}
    if set(data) - allowed:
        raise ValueError('Неизвестные поля изменения словаря')
    connection = _connect(directory)
    try:
        connection.execute('BEGIN IMMEDIATE')
        revision = connection.execute('SELECT revision FROM dictionary_revision WHERE id=1').fetchone()[0]
        if revision != data['expectedRevision']:
            raise DictionaryConflict('Словарь изменился на другом устройстве. Обновите версию и проверьте изменения.')
        version, at = revision + 1, _now()
        reason, before, after = None, None, None
        if action == 'propose':
            ru, zh = _text(data.get('ru'), 200, 'русская фраза'), _text(data.get('zh'), 2000, 'китайская фраза')
            source = _text(data.get('source'), 2000, 'источник или пример исправления')
            origin = data.get('origin', 'operator')
            if origin not in ('operator', 'machine', 'knowledge'):
                raise ValueError('Неизвестное происхождение предложения')
            if not re.search(r'[\u3400-\u9fff]', zh):
                raise ValueError('Китайская пара должна содержать китайские иероглифы; непереведённый русский фрагмент не утверждается автоматически')
            if connection.execute('SELECT COUNT(*) FROM dictionary_entries').fetchone()[0] >= 5000:
                raise ValueError('Достигнут предел 5000 версий словаря')
            existing = connection.execute("SELECT zh FROM dictionary_entries WHERE ru_key=? AND state='approved'", (_key(ru),)).fetchone()
            before = existing['zh'] if existing else None
            entry_id = secrets.token_hex(16)
            connection.execute('INSERT INTO dictionary_entries VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (entry_id, ru, _key(ru), zh, 'proposed', source, origin, actor, None, at, None, before, version, None))
            after = zh
        else:
            entry_id = data.get('id')
            if not isinstance(entry_id, str) or not re.fullmatch(r'[a-f0-9]{32}', entry_id):
                raise ValueError('Неверный ID предложения')
            entry = connection.execute('SELECT * FROM dictionary_entries WHERE id=?', (entry_id,)).fetchone()
            if not entry:
                raise ValueError('Предложение не найдено')
            if entry['state'] != 'proposed':
                raise DictionaryConflict('Предложение уже рассмотрено. Обновите словарь.')
            ru, source = entry['ru'], entry['source']
            current = connection.execute("SELECT id,zh FROM dictionary_entries WHERE ru_key=? AND state='approved'", (entry['ru_key'],)).fetchone()
            before = current['zh'] if current else None
            if action == 'reject':
                reason = _text(data.get('reason'), 1000, 'причина отклонения')
                connection.execute("UPDATE dictionary_entries SET state='rejected',reviewed_by=?,reviewed_at=?,revision=?,reason=? WHERE id=?", (actor, at, version, reason, entry_id))
            else:
                after = entry['zh']
                if current and current['zh'] != after:
                    if data.get('replaceApproved') is not True or data.get('expectedPrevious') != current['zh']:
                        raise DictionaryConflict('Для русской фразы уже есть другая утверждённая пара. Сравните до/после и явно подтвердите замену.')
                if current:
                    connection.execute("UPDATE dictionary_entries SET state='superseded' WHERE id=?", (current['id'],))
                connection.execute("UPDATE dictionary_entries SET state='approved',reviewed_by=?,reviewed_at=?,previous_zh=?,revision=? WHERE id=?", (actor, at, before, version, entry_id))
        connection.execute('UPDATE dictionary_revision SET revision=? WHERE id=1', (version,))
        connection.execute('INSERT INTO dictionary_events VALUES (?,?,?,?,?,?,?,?,?,?)',
            (version, at, actor, action, entry_id, ru, before, after, source, reason))
        connection.commit()
        return _snapshot(connection)
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def dispatch(directory, path, data=None, *, actor='operator'):
    if path != 'dictionary-reviews':
        return None
    try:
        value = snapshot(directory) if data is None else mutate(directory, data, actor=actor)
        return 200, value, 'application/json; charset=utf-8', {}
    except DictionaryConflict as error:
        return 409, {'error': str(error)}, 'application/json; charset=utf-8', {}
    except ValueError as error:
        return 400, {'error': str(error)}, 'application/json; charset=utf-8', {}
