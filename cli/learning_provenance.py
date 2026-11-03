"""Consent and corpus lineage inside the owner's existing private memory DB.

Withdrawal prevents future use; it does not claim to erase influence from
already trained weights. Historical rows remain auditable and are never used
as implicit permission to train again.
"""
import hashlib
import json
from pathlib import Path
import re
import unicodedata

TABLES = ('learning_audit', 'learning_edges', 'learning_withdrawals',
          'learning_snapshot_members', 'learning_dataset_members', 'learning_candidates', 'learning_model_history')
SCHEMA = '''
CREATE INDEX IF NOT EXISTS examples_fingerprint ON examples(fingerprint);
CREATE INDEX IF NOT EXISTS examples_supersedes ON examples(supersedes);
CREATE TABLE IF NOT EXISTS learning_audit (
  sequence INTEGER PRIMARY KEY, event_id TEXT NOT NULL, action TEXT NOT NULL,
  actor TEXT NOT NULL, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS learning_edges (
  parent_id TEXT NOT NULL, child_id TEXT NOT NULL, PRIMARY KEY(parent_id,child_id));
CREATE TABLE IF NOT EXISTS learning_withdrawals (
  event_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, root_id TEXT NOT NULL,
  actor TEXT NOT NULL, created TEXT NOT NULL, reason TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS learning_withdrawn_fingerprint ON learning_withdrawals(fingerprint);
CREATE TABLE IF NOT EXISTS learning_snapshot_members (
  snapshot_id TEXT NOT NULL, event_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
  partition TEXT NOT NULL, PRIMARY KEY(snapshot_id,event_id));
CREATE TABLE IF NOT EXISTS learning_dataset_members (
  dataset_sha TEXT NOT NULL, event_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
  PRIMARY KEY(dataset_sha,event_id));
CREATE TABLE IF NOT EXISTS learning_candidates (
  weights_sha TEXT PRIMARY KEY, dataset_sha TEXT NOT NULL, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS learning_model_history (
  sequence INTEGER PRIMARY KEY, report_sha TEXT NOT NULL UNIQUE,
  weights_sha TEXT NOT NULL, payload TEXT NOT NULL, recorded REAL NOT NULL);
'''
_ID = re.compile(r'[\w-]{8,100}')
_SHA = re.compile(r'[a-f0-9]{64}')


def actor_name(actor):
    if not isinstance(actor, str) or not 1 <= len(actor) <= 120 or any(ord(c) < 32 for c in actor):
        raise ValueError('Неверный участник решения об обучении')
    return actor


def parents(value, supersedes=None):
    if value is None:
        value = []
    if not isinstance(value, list) or len(value) > 100 or any(not isinstance(x, str) or not _ID.fullmatch(x) for x in value):
        raise ValueError('Неверные исходные примеры производной записи')
    return list(dict.fromkeys([*value, *([supersedes] if supersedes else [])]))


def assert_ingest(db, fingerprint, source_ids):
    if db.execute('SELECT 1 FROM learning_withdrawals WHERE fingerprint=?', (fingerprint,)).fetchone():
        raise ValueError('Согласие на этот пример отозвано. Обычное подтверждение не восстанавливает разрешение.')
    for identifier in source_ids:
        row = db.execute('SELECT status FROM examples WHERE id=?', (identifier,)).fetchone()
        if row is None:
            raise ValueError('Исходный пример производной записи не найден')
        if row[0] == 'withdrawn' or db.execute('SELECT 1 FROM learning_withdrawals WHERE event_id=?', (identifier,)).fetchone():
            raise ValueError('Исходный пример отозван; его производные нельзя использовать для обучения')


def record(db, identifier, source_ids, action, actor, created):
    actor = actor_name(actor)
    for parent in source_ids:
        if parent == identifier:
            raise ValueError('Пример не может происходить из самого себя')
        db.execute('INSERT OR IGNORE INTO learning_edges VALUES (?,?)', (parent, identifier))
    db.execute('INSERT INTO learning_audit(event_id,action,actor,created) VALUES (?,?,?,?)',
               (identifier, action, actor, created))


def withdraw(db, identifier, actor, created, reason=''):
    actor = actor_name(actor)
    if not isinstance(identifier, str) or not _ID.fullmatch(identifier):
        raise ValueError('Неверный ID отзыва')
    if not isinstance(reason, str) or len(reason) > 500 or any(ord(c) < 32 for c in reason):
        raise ValueError('Причина отзыва должна быть короткой строкой')
    first = db.execute('SELECT * FROM examples WHERE id=?', (identifier,)).fetchone()
    if first is None:
        raise ValueError('Учебный пример не найден')
    # Edges and supersedes cover explicit derivatives, including historical
    # rows written before the edge table existed. Exact copies are also revoked.
    affected, fingerprints = set(), set()
    pending = [identifier]
    while pending:
        current = pending.pop()
        if current in affected:
            continue
        row = db.execute('SELECT * FROM examples WHERE id=?', (current,)).fetchone()
        if row is None:
            continue
        affected.add(current)
        pending.extend(r[0] for r in db.execute('SELECT child_id FROM learning_edges WHERE parent_id=?', (current,)))
        pending.extend(r[0] for r in db.execute('SELECT id FROM examples WHERE supersedes=?', (current,)))
        if row['fingerprint'] not in fingerprints:
            fingerprints.add(row['fingerprint'])
            pending.extend(r[0] for r in db.execute('SELECT id FROM examples WHERE fingerprint=?', (row['fingerprint'],)))
    for current in sorted(affected):
        row = db.execute('SELECT * FROM examples WHERE id=?', (current,)).fetchone()
        db.execute('INSERT OR IGNORE INTO learning_withdrawals VALUES (?,?,?,?,?,?)',
                   (current, row['fingerprint'], identifier, actor, created, reason))
        if row['status'] != 'withdrawn':
            db.execute("UPDATE examples SET status='withdrawn' WHERE id=?", (current,))
            db.execute('INSERT INTO review_history(event_id,previous,current,created) VALUES (?,?,?,?)',
                       (current, row['status'], 'withdrawn', created))
            record(db, current, [], 'withdrawn', actor, created)
    impacted = [row[0] for row in db.execute('''SELECT DISTINCT c.weights_sha
        FROM learning_candidates c JOIN learning_dataset_members m ON m.dataset_sha=c.dataset_sha
        JOIN learning_withdrawals w ON w.event_id=m.event_id OR w.fingerprint=m.fingerprint''')]
    return {'withdrawn': True, 'affectedExamples': len(affected), 'affectedCandidateHashes': impacted,
            'retrainingRequired': bool(impacted), 'existingWeightInfluenceRemoved': False,
            'message': 'Пример и известные производные исключены из будущих обучений. Влияние на готовые веса не удалено; затронутые модели требуют переобучения.'}


def details(db, identifier):
    row = db.execute('SELECT * FROM examples WHERE id=?', (identifier,)).fetchone()
    if row is None:
        raise ValueError('Учебный пример не найден')
    history = [dict(r) for r in db.execute('SELECT action,actor,created FROM learning_audit WHERE event_id=? ORDER BY sequence', (identifier,))]
    snapshots = [dict(r) for r in db.execute('SELECT snapshot_id AS snapshotId,partition FROM learning_snapshot_members WHERE event_id=? ORDER BY snapshot_id', (identifier,))]
    datasets = [r[0] for r in db.execute('SELECT dataset_sha FROM learning_dataset_members WHERE event_id=? ORDER BY dataset_sha', (identifier,))]
    withdrawal = db.execute('SELECT actor,created,root_id AS rootId,reason FROM learning_withdrawals WHERE event_id=?', (identifier,)).fetchone()
    return {'id': identifier, 'source': row['source'], 'createdAt': row['created'],
            'groupId': row['group_id'], 'fingerprint': row['fingerprint'],
            'derivedFrom': [r[0] for r in db.execute('SELECT parent_id FROM learning_edges WHERE child_id=?', (identifier,))],
            'reviewHistory': history, 'historicalActorUnknown': not bool(history),
            'snapshots': snapshots, 'datasetHashes': datasets,
            'withdrawal': dict(withdrawal) if withdrawal else None,
            'existingWeightInfluenceRemoved': False}


def dataset_rows(path, *, snapshot=None):
    # A trainer may have already loaded an immutable byte snapshot. Its
    # consent must be checked against those exact bytes, never a replacement
    # file found later at the same path. Invalid snapshots never fall back.
    if snapshot is not None:
        if type(snapshot) is not bytes or not 0 < len(snapshot) <= 8_000_000:
            raise ValueError('Нужен неизменяемый ограниченный снимок учебного корпуса')
        raw = snapshot
    else:
        path = Path(path)
        if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= 8_000_000:
            raise ValueError('Неверный учебный корпус для проверки согласия')
        raw = path.read_bytes()
        if not 0 < len(raw) <= 8_000_000:
            raise ValueError('Неверный размер учебного корпуса для проверки согласия')
    value = json.loads(raw)
    if not isinstance(value, dict) or not isinstance(value.get('splits'), dict):
        raise ValueError('Нужен корпус с явно указанными частями')
    rows = []
    for part in value['splits'].values():
        if not isinstance(part, list):
            raise ValueError('Неверная часть учебного корпуса')
        rows.extend(part)
    if len(rows) > 100_000 or any(not isinstance(row, dict) for row in rows):
        raise ValueError('Неверные записи учебного корпуса')
    return hashlib.sha256(raw).hexdigest(), value, rows


def _consent_row(row):
    """Compare durable consent with the exact approved text, without inference.

    Grounded targets are validated when approved/exported and again against
    the frozen dataset before MLX loads. Re-rendering every property here would
    spawn Node repeatedly while a large native model is resident. Consent is
    about the same immutable event remaining approved, including its original
    request/target and fingerprint; stale facts are not silently reconstructed.
    """
    from learning_memory import LearningMemory, _json, _hash
    if row['kind'] != 'grounded_listing':
        return LearningMemory._training_row(row)
    try:
        payload = json.loads(row['payload'])
        if _hash(row['kind']+'\n'+_json(payload)) != row['fingerprint']:
            raise ValueError('Изменились сохранённые данные проверенного примера')
        request, target, review = payload['request'], payload['target'], payload['review']
        if (not isinstance(request,dict) or not isinstance(request.get('messages'),list)
                or not request['messages'] or not isinstance(target,dict)
                or any(not isinstance(target.get(key),str) or not target[key] for key in ('title','body'))
                or not isinstance(review,dict) or type(review.get('humanVerified')) is not bool
                or not isinstance(review.get('actor'),str)
                or review['humanVerified'] == review['actor'].startswith('assistant_')):
            raise ValueError('Неверная сохранённая проверка учебного примера')
        messages = [*request['messages'], {'role':'assistant','content':
            '标题：'+target['title']+'\n正文：\n'+target['body']}]
        return {'id':row['id'],'kind':row['kind'],'groupId':row['group_id'],
                'messages':messages,'source':row['source']}
    except (KeyError,TypeError,json.JSONDecodeError) as error:
        raise ValueError('Неверные сохранённые данные учебного примера') from error


def dataset_consent(db, path, *, register=False, snapshot=None):
    sha, document, rows = dataset_rows(path, snapshot=snapshot)
    provenance = document.get('provenance', {})
    if not isinstance(provenance,dict):
        raise ValueError('Неверное происхождение корпуса')
    local = provenance.get('kind') == 'approved-local-memory'
    def message_key(messages):
        if not isinstance(messages,list) or any(not isinstance(message,dict) or not isinstance(message.get('content'),str) for message in messages):return None
        value=[(message.get('role'),' '.join(unicodedata.normalize('NFKC',message['content']).casefold().split())) for message in messages]
        return hashlib.sha256(json.dumps(value,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    # A renamed ID/envelope does not erase a known revoked payload. This also
    # covers whitespace-only copies with their row provenance stripped.
    withdrawn_messages={message_key(_consent_row(row)['messages']) for row in
        db.execute("SELECT e.* FROM examples e JOIN learning_withdrawals w ON w.event_id=e.id WHERE e.kind IN ('dictionary','translation','furniture','title','description','grounded_listing')")}
    for item in rows:
        row_provenance=item.get('provenance',{})
        if not isinstance(row_provenance,dict):raise ValueError('Неверное происхождение учебной строки')
        fingerprint=row_provenance.get('sourceSha256')
        if isinstance(fingerprint,str) and db.execute('SELECT 1 FROM learning_withdrawals WHERE fingerprint=?',(fingerprint,)).fetchone():
            raise ValueError('Учебный корпус содержит отозванный исходный пример')
        key=message_key(item.get('messages'))
        if key is not None and key in withdrawn_messages:
            raise ValueError('Учебный корпус содержит копию отозванного примера')
        operator_row=(row_provenance.get('kind')=='operator-reviewed' or
            row_provenance.get('permission')=='approved-for-local-training' or item.get('status')=='approved')
        identifier = item.get('id')
        row = db.execute('SELECT * FROM examples WHERE id=?', (identifier,)).fetchone() if isinstance(identifier, str) else None
        if row is None:
            if local or operator_row:
                raise ValueError('Локальный учебный пример отсутствует; корпус требует новой подготовки')
            continue
        # A changed payload under the same ID is not a fresh permission grant.
        if row['status'] != 'approved' or db.execute('SELECT 1 FROM learning_withdrawals WHERE event_id=? OR fingerprint=?',
                (row['id'], row['fingerprint'])).fetchone():
            raise ValueError('Учебный корпус содержит отозванный или неутверждённый пример')
        expected = _consent_row(row)
        if row['kind']=='grounded_listing' and fingerprint!=row['fingerprint']:
            raise ValueError('Происхождение корпуса отличается от утверждённого примера')
        if any(item.get(key) != expected[key] for key in ('id', 'kind', 'groupId', 'messages', 'source')):
            raise ValueError('Содержимое корпуса отличается от утверждённого примера')
        if register:
            db.execute('INSERT OR IGNORE INTO learning_dataset_members VALUES (?,?,?)',
                       (sha, row['id'], row['fingerprint']))
    if db.execute('''SELECT 1 FROM learning_dataset_members m JOIN learning_withdrawals w
                    ON w.event_id=m.event_id OR w.fingerprint=m.fingerprint WHERE m.dataset_sha=? LIMIT 1''', (sha,)).fetchone():
        raise ValueError('Согласие на часть этого корпуса отозвано; нужна новая версия корпуса')
    return sha


def candidate_status(db, weights_sha):
    if not isinstance(weights_sha, str) or not _SHA.fullmatch(weights_sha):
        raise ValueError('Неверная контрольная сумма кандидата')
    registered = db.execute('SELECT dataset_sha FROM learning_candidates WHERE weights_sha=?', (weights_sha,)).fetchone()
    affected = db.execute('''SELECT COUNT(DISTINCT w.event_id)
        FROM learning_candidates c JOIN learning_dataset_members m ON m.dataset_sha=c.dataset_sha
        JOIN learning_withdrawals w ON w.event_id=m.event_id OR w.fingerprint=m.fingerprint
        WHERE c.weights_sha=?''', (weights_sha,)).fetchone()[0]
    return {'lineageRecorded': registered is not None, 'withdrawnExamples': affected,
            'retrainingRequired': affected > 0, 'existingWeightInfluenceRemoved': False}
