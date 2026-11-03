"""Private source-phrase discovery, distinct from reviewed training and weights.

Imports are evidence of wording, not labels proving a Chinese translation.
Only an explicit operator review can turn a candidate into approved memory.
The idle sweep indexes saved cards without a browser, network, or model load.
"""
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
import unicodedata

from learning_memory import LearningMemory, _text

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = '''
CREATE TABLE IF NOT EXISTS source_phrase_candidates (
 id TEXT PRIMARY KEY, normalized TEXT NOT NULL UNIQUE, text TEXT NOT NULL,
 status TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS source_phrase_occurrences (
 card_hash TEXT NOT NULL, source_sha TEXT NOT NULL, phrase_id TEXT NOT NULL,
 created REAL NOT NULL, PRIMARY KEY(card_hash,source_sha,phrase_id));
CREATE TABLE IF NOT EXISTS source_phrase_captures (
 card_hash TEXT NOT NULL, source_sha TEXT NOT NULL, receipt TEXT NOT NULL,
 created REAL NOT NULL, PRIMARY KEY(card_hash,source_sha));
'''


def normalize(text):
    return ' '.join(unicodedata.normalize('NFC', text).casefold().replace('ё', 'е').split())


def _digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _memory(directory):
    memory = LearningMemory(directory)
    with memory._db() as db:
        db.executescript(SCHEMA)
    return memory


@lru_cache(maxsize=2)
def _open_terms(path, stamp, size):
    if not stamp or size > 64_000_000:
        return frozenset()
    value = json.loads(Path(path).read_text(encoding='utf-8'))
    return frozenset(normalize(row[0]) for row in value.get('entries', [])
                     if isinstance(row, list) and row and isinstance(row[0], str))


@lru_cache(maxsize=4)
def _core_terms(directory, signature):
    # The same Node dictionary as generation supplies terms; never regex-parse
    # JavaScript or introduce a stale second handwritten dictionary in Python.
    from language_service import node_language, _profile
    profile = _profile(directory)
    value = node_language({'action': 'source-term-index', 'rules': profile.get('rules', {})}, timeout=2)
    terms = value.get('terms')
    if not isinstance(terms, list) or any(not isinstance(term, str) for term in terms):
        raise ValueError('Неверный индекс исходных словарных фраз')
    return frozenset(normalize(term) for term in terms)


def _known(memory):
    complete = True
    path = ROOT/'data/ru-zh-open-lexicon.json'
    try:
        info = path.stat()
        terms = set(_open_terms(str(path), info.st_mtime_ns, info.st_size))
    except (OSError, ValueError):
        terms = set()
        complete = False
    directory = memory.directory.parent
    watched = [ROOT/name for name in ('cli/language.mjs','web/engine.mjs','web/rental-lexicon.mjs','web/community-phrases.mjs')]
    watched.append(directory/'phone-profile.json')
    signature = []
    for item in watched:
        try:
            info = item.stat(); signature.append((str(item), info.st_mtime_ns, info.st_size))
        except OSError:
            signature.append((str(item), None, None))
    try:
        terms.update(_core_terms(str(directory), tuple(signature)))
    except (OSError, ValueError):
        complete = False
    with memory._db() as db:
        for (payload,) in db.execute("SELECT payload FROM examples WHERE status='approved' AND kind IN ('dictionary','translation')"):
            terms.add(normalize(json.loads(payload)['ru']))
    return terms, complete


def source_phrases(card):
    fields = card.get('fields') if isinstance(card, dict) else None
    source = fields.get('sourceDescription') if isinstance(fields, dict) else None
    if not isinstance(source, str) or not source.strip():
        return '', []
    source = _text(source, 60_000)
    phrases = []
    # A decimal point is not a sentence boundary. Long paragraphs are kept as
    # bounded discovery excerpts; they are never silently used for training.
    for raw in re.split(r'\n+|(?<=[.!?;])\s+', source):
        text = ' '.join(raw.split()).strip(' •—–-\t')
        if not re.search('[А-Яа-яЁё]', text) or len(text) < 8:
            continue
        if text == '[контакт удалён]':
            continue
        for offset in range(0, min(len(text), 3200), 800):
            excerpt = text[offset:offset+800].strip()
            if len(excerpt) >= 8:
                phrases.append(excerpt)
        if len(phrases) >= 128:
            break
    return source, list(dict.fromkeys(phrases))[:128]


def capture_import(directory, card, group_id):
    """Idempotently discover source phrases after a card save has committed."""
    if not isinstance(group_id, str) or not 1 <= len(group_id) <= 2000:
        raise ValueError('Нужен ID карточки для происхождения фраз')
    source, phrases = source_phrases(card)
    if not source:
        return {'status': 'no-source', 'newPhrases': 0, 'modelWeightsUpdated': False}
    memory = _memory(directory)
    card_hash, source_sha = _digest(group_id), _digest(source)
    with memory._db() as db:
        prior = db.execute('SELECT receipt FROM source_phrase_captures WHERE card_hash=? AND source_sha=?',
                           (card_hash, source_sha)).fetchone()
    if prior:
        return {**json.loads(prior[0]), 'status': 'already-captured', 'newPhrases': 0}
    known, dictionary_complete = _known(memory)
    fresh = duplicates = known_count = 0
    stamp = time.time()
    with memory._db() as db:
        db.execute('BEGIN IMMEDIATE')
        prior = db.execute('SELECT receipt FROM source_phrase_captures WHERE card_hash=? AND source_sha=?',
                           (card_hash, source_sha)).fetchone()
        if prior:
            return {**json.loads(prior[0]), 'status': 'already-captured', 'newPhrases': 0}
        for text in phrases:
            normalized = normalize(text)
            if normalized in known:
                known_count += 1
                continue
            phrase_id = _digest(normalized)
            inserted = db.execute('INSERT OR IGNORE INTO source_phrase_candidates VALUES (?,?,?,?,?,?)',
                (phrase_id, normalized, text, 'pending', stamp, stamp)).rowcount
            fresh += inserted
            duplicates += 1-inserted
            db.execute('UPDATE source_phrase_candidates SET updated=? WHERE id=?', (stamp, phrase_id))
            db.execute('INSERT OR IGNORE INTO source_phrase_occurrences VALUES (?,?,?,?)',
                       (card_hash, source_sha, phrase_id, stamp))
        result = {'status': 'captured', 'newPhrases': fresh, 'duplicatePhrases': duplicates,
            'knownPhrases': known_count, 'sourceSentences': len(phrases), 'capturedAt': stamp,
            'sourceSha256': source_sha, 'modelWeightsUpdated': False,
            'dictionaryIndexComplete': dictionary_complete,
            'nextStep': 'review-translation', 'countMeaning': 'new-source-fragments-not-learned-words'}
        db.execute('INSERT INTO source_phrase_captures VALUES (?,?,?,?)',
                   (card_hash, source_sha, json.dumps(result, ensure_ascii=False), stamp))
    return result


def status(directory, limit=10):
    memory = _memory(directory)
    with memory._db() as db:
        count = db.execute('SELECT COUNT(*) FROM source_phrase_candidates').fetchone()[0]
        pending = db.execute("SELECT COUNT(*) FROM source_phrase_candidates WHERE status='pending'").fetchone()[0]
        cards = db.execute('SELECT COUNT(DISTINCT card_hash) FROM source_phrase_captures').fetchone()[0]
        last = db.execute('SELECT receipt FROM source_phrase_captures ORDER BY created DESC LIMIT 1').fetchone()
        rows = db.execute("SELECT id,text,status,created FROM source_phrase_candidates WHERE status='pending' ORDER BY created DESC,id LIMIT ?",
                          (min(30, max(0, int(limit))),)).fetchall()
    return {'uniqueSourcePhrases': count, 'pendingTranslation': pending, 'indexedCards': cards,
        'lastCapture': json.loads(last[0]) if last else None,
        'candidates': [dict(row) for row in rows], 'modelWeightsUpdated': False,
        'countMeaning': 'source-fragments-awaiting-reviewed-translation'}


def review_phrase(directory, phrase_id, zh, *, actor='operator'):
    if not isinstance(phrase_id, str) or not re.fullmatch('[a-f0-9]{64}', phrase_id):
        raise ValueError('Неверный ID фразы')
    zh = _text(zh)
    if not re.search('[\u3400-\u9fff]', zh):
        raise ValueError('Нужен проверенный китайский перевод')
    memory = _memory(directory)
    with memory._db() as db:
        row = db.execute('SELECT text FROM source_phrase_candidates WHERE id=?', (phrase_id,)).fetchone()
    if not row:
        raise ValueError('Фраза не найдена')
    event_id = _digest('reviewed-source-phrase\n'+phrase_id+'\n'+zh)
    # Shared phrases can connect many apartments. Until those connected source
    # groups are adjudicated, keep this whole pool in one conservative group;
    # twenty phrases must not masquerade as twenty independent apartments.
    event = memory.record('translation', {'ru': row[0], 'zh': zh}, source='manual', status='approved',
                          group_id='source-phrase-discovery', event_id=event_id, actor=actor)
    with memory._db() as db:
        db.execute("UPDATE source_phrase_candidates SET status='reviewed',updated=? WHERE id=?", (time.time(), phrase_id))
    return {'event': event, 'discovery': status(directory)}


def sweep_saved_cards(directory, limit=8):
    """Bounded useful idle work; unchanged source digests are never re-indexed."""
    directory = Path(directory)
    path = directory/'listings.sqlite3'
    if not path.is_file() or path.is_symlink():
        return {'indexed': 0, 'newPhrases': 0, 'modelWeightsUpdated': False}
    memory = _memory(directory)
    with memory._db() as db:
        seen = set(tuple(row) for row in db.execute('SELECT card_hash,source_sha FROM source_phrase_captures'))
    with sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=.5) as db:
        rows = db.execute('SELECT id,payload FROM results ORDER BY updated DESC LIMIT 1000').fetchall()
    indexed = fresh = skipped = 0
    started = time.monotonic()
    for card_id, encoded in rows:
        if indexed >= limit or time.monotonic()-started > 2:
            break
        try:
            card = json.loads(encoded)
            source, _ = source_phrases(card)
            if not source or (_digest(card_id), _digest(source)) in seen:
                continue
            result = capture_import(directory, card, card_id)
            indexed += 1
            fresh += result['newPhrases']
        except (ValueError, TypeError):
            skipped += 1
    return {'indexed': indexed, 'newPhrases': fresh, 'skipped': skipped, 'modelWeightsUpdated': False}
