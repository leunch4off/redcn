"""Private, reviewed examples for retrieval and later bounded local training.

This is a durable dataset, not an automatic weight updater. Generated/imported
text enters review; only approved examples are exported. The server must call
``review`` only after an operator action. No browser/controller is instantiated.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import tempfile
import time
import uuid

KINDS = {'title', 'description', 'translation', 'dictionary', 'furniture', 'tag_stat'}
SOURCES = {'user', 'user-edit', 'manual', 'generated', 'imported', 'dictionary'}
STATES = {'pending', 'approved', 'rejected', 'superseded'}
MAX_EVENTS = 100_000
MAX_TEXT = 12_000
SCHEMA_VERSION = 1
_SECRETS = re.compile(r'(?:password|passwd|api[_ -]?key|access[_ -]?token|refresh[_ -]?token|authorization|cookie|пароль|токен|куки|密码|密钥|令牌)\s*[:：=]|-----BEGIN [A-Z ]*PRIVATE KEY-----|\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9_]{20,})|(?:паспорт|passport|护照)\s*(?:серия|номер|number|no\.?|号码)?\s*[:：=]\s*\w', re.I)
_CONTACT = re.compile(r'https?://\S+|\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b|(?<!\w)@[\w-]{3,}|(?<!\w)\+?\d[\d ()-]{8,}\d(?!\w)|(?:微信|wechat|телефон|phone|telegram|телеграм|联系电话)\s*[:：=]\s*[^\s,;]+', re.I)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _hash(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _term(value):
    return ' '.join(value.casefold().replace('ё', 'е').split())


def _text(value, limit=MAX_TEXT):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError('Нужен непустой текст допустимой длины')
    value = value.strip()
    if _SECRETS.search(value) or re.search(r'<\s*/?\s*[a-zA-Z][^>]*>', value):
        raise ValueError('Учебные примеры не принимают секреты и исходный HTML')
    value = ''.join(c for c in value if c >= ' ' or c in '\n\t')
    def redact(match):
        text = match.group(0)
        # Preserve prices/areas with grouping spaces; require ten real digits
        # before treating an unlabeled numeric sequence as a phone number.
        if re.fullmatch(r'\+?\d[\d ()-]*\d', text) and len(re.sub(r'\D', '', text)) < 10:
            return text
        return '[контакт удалён]'
    value = _CONTACT.sub(redact, value)
    return value


def _stamp(value):
    if value is None:
        return _now()
    if not isinstance(value, str) or len(value) > 50:
        raise ValueError('Неверная дата наблюдения')
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if stamp.tzinfo is None or stamp.timestamp() > time.time()+86400:
            raise ValueError('Неверная дата наблюдения')
        return stamp.astimezone(timezone.utc).isoformat()
    except (ValueError, OverflowError, OSError) as error:
        raise ValueError('Неверная дата наблюдения') from error


def _payload(kind, value):
    if not isinstance(value, dict):
        raise ValueError('Неверный учебный пример')
    if kind in {'title', 'description'}:
        if set(value) != {'text', 'language'} or value['language'] not in {'ru', 'zh'}:
            raise ValueError('Нужны текст и язык ru/zh')
        return {'text': _text(value['text'], 500 if kind == 'title' else MAX_TEXT), 'language': value['language']}
    if kind in {'dictionary', 'translation'}:
        if set(value) != {'ru', 'zh'}:
            raise ValueError('Нужна пара ru/zh')
        return {'ru': _text(value['ru']), 'zh': _text(value['zh'])}
    if kind == 'furniture':
        if (not {'items'} <= set(value) <= {'items', 'excluded'}
                or not isinstance(value['items'], list)
                or not isinstance(value.get('excluded', []), list)
                or not 1 <= len(value['items'])+len(value.get('excluded', [])) <= 200):
            raise ValueError('Нужны подтверждённые или снятые пользователем отметки предметов')
        items = list(dict.fromkeys(_text(item, 120) for item in value['items']))
        excluded = list(dict.fromkeys(_text(item, 120) for item in value.get('excluded', [])))
        if set(items) & set(excluded):
            raise ValueError('Предмет нельзя одновременно подтвердить и снять')
        result = {'items': items}
        if 'excluded' in value: result['excluded'] = excluded
        return result
    if kind == 'tag_stat':
        if not {'tag'} <= set(value) <= {'tag', 'views', 'uses', 'observedAt', 'platform', 'context', 'post'}:
            raise ValueError('Неверные поля статистики тега')
        tag = _text(value['tag'], 100).lstrip('#').strip()
        if not re.fullmatch(r'[\w\u3400-\u9fff-]{1,80}', tag):
            raise ValueError('Неверный тег')
        views, uses = value.get('views'), value.get('uses', 1)
        if views is not None and (type(views) is not int or not 0 <= views <= 10_000_000_000):
            raise ValueError('Просмотры задаются неотрицательным целым числом')
        if type(uses) is not int or not 1 <= uses <= 1_000_000:
            raise ValueError('Количество публикаций должно быть положительным')
        platform = value.get('platform', 'rednote')
        if not isinstance(platform, str) or not re.fullmatch(r'[\w-]{1,40}', platform):
            raise ValueError('Неверная площадка')
        result = {'tag': '#'+tag, 'views': views, 'uses': uses, 'observedAt': _stamp(value.get('observedAt')),
                  'platform': platform, 'context': _text(value['context'], 500) if value.get('context') else ''}
        if 'post' in value:
            from tag_post_evaluation import normalize_post
            post = normalize_post(value['post'])
            if result['tag'] not in post['tags'] or views != post['views'] or uses != 1:
                raise ValueError('Статистика поста не соответствует тегу, просмотрам или числу публикаций')
            result['post'] = post
        return result
    raise ValueError('Неизвестный вид учебного примера')


class LearningMemory:
    """One instance per cache is inexpensive; each operation uses its own connection."""
    def __init__(self, directory):
        self.directory = Path(directory) / 'learning'
        if self.directory.is_symlink():
            raise ValueError('Каталог обучения не должен быть ссылкой')
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = self.directory / 'memory.sqlite3'
        with self._db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS examples (
                  id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL,
                  source TEXT NOT NULL, status TEXT NOT NULL, group_id TEXT NOT NULL,
                  created TEXT NOT NULL, supersedes TEXT, fingerprint TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS examples_state ON examples(status,kind);
                CREATE INDEX IF NOT EXISTS examples_group ON examples(group_id);
                CREATE TABLE IF NOT EXISTS review_history (
                  sequence INTEGER PRIMARY KEY, event_id TEXT NOT NULL,
                  previous TEXT, current TEXT NOT NULL, created TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS splits (
                  group_id TEXT PRIMARY KEY, partition TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS checkpoints (
                  job_id TEXT PRIMARY KEY, payload TEXT NOT NULL, updated TEXT NOT NULL);
            ''')
        self.path.chmod(0o600)

    @contextmanager
    def _db(self):
        if self.directory.is_symlink() or self.path.is_symlink():
            raise ValueError('Хранилище обучения не должно быть ссылкой')
        db = sqlite3.connect(self.path, timeout=3)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA busy_timeout=3000')
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _row(row):
        return {'id': row['id'], 'kind': row['kind'], 'payload': json.loads(row['payload']),
                'source': row['source'], 'status': row['status'], 'groupId': row['group_id'],
                'createdAt': row['created'], 'supersedes': row['supersedes']}

    @staticmethod
    def _supersede_pair_conflicts(db, row, stamp):
        """The latest explicit acceptance wins for a term in the same group.

        A pending generated correction must never retire the accepted spelling.
        Keep every replaced event and its decision in the review history.
        """
        if row['status'] != 'approved' or row['kind'] not in {'dictionary', 'translation'}:
            return
        term = _term(json.loads(row['payload'])['ru'])
        others = db.execute("SELECT * FROM examples WHERE group_id=? AND id<>? AND status='approved' AND kind IN ('dictionary','translation')", (row['group_id'], row['id'])).fetchall()
        for other in others:
            if _term(json.loads(other['payload'])['ru']) != term:
                continue
            db.execute("UPDATE examples SET status='superseded' WHERE id=?", (other['id'],))
            db.execute('INSERT INTO review_history(event_id,previous,current,created) VALUES (?,?,?,?)',
                       (other['id'], 'approved', 'superseded', stamp))

    @staticmethod
    def _retire_predecessor(db, row, stamp):
        if row['status'] != 'approved' or not row['supersedes']:
            return
        old = db.execute('SELECT * FROM examples WHERE id=?', (row['supersedes'],)).fetchone()
        if old is None or old['status'] == 'superseded':
            raise ValueError('Предыдущая версия уже исправлена; обновите учебный пример')
        db.execute("UPDATE examples SET status='superseded' WHERE id=?", (old['id'],))
        db.execute('INSERT INTO review_history(event_id,previous,current,created) VALUES (?,?,?,?)',
                   (old['id'], old['status'], 'superseded', stamp))

    def record(self, kind, payload, *, source='user', status='pending', group_id=None,
               supersedes=None, event_id=None):
        """Save a bounded example. Explicit operator edits may be approved at ingestion.

        ``group_id`` is an opaque listing ID (URLs are hashed and never stored).
        Reusing ``event_id`` is idempotent only when its entire content matches.
        A correction supersedes its predecessor atomically, within the same group.
        """
        if kind not in KINDS or source not in SOURCES or status not in {'pending', 'approved', 'rejected'}:
            raise ValueError('Неверные параметры учебного события')
        payload = _payload(kind, payload)
        if source in {'generated', 'imported'} and status == 'approved':
            status = 'pending'
        event_id = event_id or uuid.uuid4().hex
        if not isinstance(event_id, str) or not re.fullmatch(r'[\w-]{8,100}', event_id):
            raise ValueError('Неверный ID учебного события')
        if group_id is not None and (not isinstance(group_id, str) or not 1 <= len(group_id) <= 2000):
            raise ValueError('Неверная группа учебного события')
        group = _hash(group_id) if group_id else None
        encoded = _json(payload)
        fingerprint = _hash(kind+'\n'+encoded)
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM examples WHERE id=?', (supersedes,)).fetchone() if supersedes else None
            if supersedes and (old is None or old['kind'] != kind):
                raise ValueError('Исправляемый пример не найден или относится к другому виду')
            if old is not None:
                if group is not None and group != old['group_id']:
                    raise ValueError('Исправление должно относиться к той же карточке')
                group = old['group_id']
            group = group or _hash(event_id)
            existing = db.execute('SELECT * FROM examples WHERE id=?', (event_id,)).fetchone()
            if existing:
                if (existing['kind'], existing['payload'], existing['source'], existing['group_id'], existing['supersedes']) != (kind, encoded, source, group, supersedes):
                    raise ValueError('Этот ID уже занят другим учебным событием')
                return self._row(existing)
            if old is not None and old['status'] == 'superseded':
                raise ValueError('Этот пример уже исправлен; обновите текущую версию')
            if db.execute('SELECT COUNT(*) FROM examples').fetchone()[0] >= MAX_EVENTS:
                raise ValueError('Достигнут предел локальной истории обучения')
            created = _now()
            db.execute('INSERT INTO examples VALUES (?,?,?,?,?,?,?,?,?)',
                       (event_id, kind, encoded, source, status, group, created, supersedes, fingerprint))
            db.execute('INSERT INTO review_history(event_id,previous,current,created) VALUES (?,NULL,?,?)', (event_id, status, created))
            row = db.execute('SELECT * FROM examples WHERE id=?', (event_id,)).fetchone()
            self._retire_predecessor(db, row, created)
            self._supersede_pair_conflicts(db, row, created)
            return self._row(row)

    def review(self, event_id, status):
        if status not in {'pending', 'approved', 'rejected'}:
            raise ValueError('Неверное решение проверки')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM examples WHERE id=?', (event_id,)).fetchone()
            if row is None or row['status'] == 'superseded':
                raise ValueError('Пример не найден или заменён исправлением')
            changed = row['status'] != status
            if changed:
                db.execute('UPDATE examples SET status=? WHERE id=?', (status, event_id))
                db.execute('INSERT INTO review_history(event_id,previous,current,created) VALUES (?,?,?,?)', (event_id, row['status'], status, _now()))
            row = db.execute('SELECT * FROM examples WHERE id=?', (event_id,)).fetchone()
            if changed: self._retire_predecessor(db, row, _now())
            self._supersede_pair_conflicts(db, row, _now())
            return self._row(row)

    def list_events(self, status=None, limit=30):
        if status is not None and status not in STATES:
            raise ValueError('Неверное состояние учебных событий')
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError('Предел списка: 1–200')
        with self._db() as db:
            rows = db.execute('SELECT * FROM examples '+('WHERE status=? ' if status else '')+'ORDER BY created DESC,id DESC LIMIT ?',
                              (status, limit) if status else (limit,)).fetchall()
        return [self._row(row) for row in rows]

    def status(self):
        with self._db() as db:
            counts = {state: 0 for state in sorted(STATES)}
            counts.update({row[0]: row[1] for row in db.execute('SELECT status,COUNT(*) FROM examples GROUP BY status')})
            trainable = db.execute("SELECT COUNT(*) FROM examples WHERE status='approved' AND kind<>'tag_stat'").fetchone()[0]
            groups = db.execute("SELECT COUNT(DISTINCT group_id) FROM examples WHERE status='approved' AND kind<>'tag_stat'").fetchone()[0]
            last = db.execute('SELECT updated,payload FROM checkpoints ORDER BY updated DESC LIMIT 1').fetchone()
        return {'schemaVersion': SCHEMA_VERSION, 'counts': counts, 'approvedExamples': trainable,
                'approvedGroups': groups, 'trainingEligible': trainable >= 20 and groups >= 5,
                'queueState': 'ready' if trainable >= 20 and groups >= 5 else 'collecting',
                'weightsUpdated': False, 'automaticPromotion': False,
                'lastCheckpoint': {'updatedAt': last[0], **json.loads(last[1])} if last else None}

    def recent_generated(self, group_id, limit=10, *, kind='description'):
        """Return same-card history for avoidance, never approved training data.

        The default preserves callers that previously requested descriptions.
        Pending/rejected generated variants still help avoid repeating a title.
        """
        if not isinstance(group_id,str) or not 1<=len(group_id)<=2000 or type(limit) is not int or not 1<=limit<=30 or not isinstance(kind,str) or kind not in {'description','title'}:
            raise ValueError('Неверная группа истории генерации')
        with self._db() as db:
            rows=db.execute("SELECT payload FROM examples WHERE group_id=? AND source='generated' AND kind=? ORDER BY created DESC,rowid DESC LIMIT ?",(_hash(group_id),kind,limit)).fetchall()
        return [json.loads(row[0])['text'] for row in rows]

    def retrieve(self, query, limit=6):
        query = _text(query, 2000).casefold()
        if type(limit) is not int or not 1 <= limit <= 30:
            raise ValueError('Предел примеров: 1–30')
        tokens = set(re.findall(r'[\w\u3400-\u9fff]+', query))
        with self._db() as db:
            rows = db.execute("SELECT * FROM examples WHERE status='approved' AND kind IN ('dictionary','translation','title','description','furniture') ORDER BY created DESC LIMIT 5000").fetchall()
        ranked = []
        for row in rows:
            payload = json.loads(row['payload'])
            strings = [v for v in payload.values() if isinstance(v, str)]
            if row['kind'] == 'furniture': strings.extend(payload['items']+payload.get('excluded', []))
            normalized = '\n'.join(strings).casefold()
            score = sum(1 for token in tokens if token in normalized)
            if query in normalized or normalized in query: score += 3
            if score: ranked.append((score, row['created'], self._row(row)))
        return [item[2] for item in sorted(ranked, key=lambda item: (item[0], item[1]), reverse=True)[:limit]]

    def approved_glossary(self, text, reverse=False, limit=64):
        """Return accepted RU/ZH terms occurring in text, longest phrase first.

        Scan at most 5,000 recently accepted pairs and return at most 128. The
        latest acceptance wins for an identical source phrase, even when that
        event originally waited in review longer than another example. Forward
        Russian matches respect word boundaries; reverse Chinese matches use
        substrings. This performs glossary lookup only, not sentence inference.
        """
        text = _term(_text(text, MAX_TEXT))
        if type(reverse) is not bool or type(limit) is not int or not 1 <= limit <= 128:
            raise ValueError('Неверные параметры поиска словаря')
        with self._db() as db:
            rows = db.execute("""SELECT e.* FROM examples e
                LEFT JOIN (SELECT event_id,MAX(sequence) AS accepted
                           FROM review_history WHERE current='approved' GROUP BY event_id) r ON r.event_id=e.id
                WHERE e.status='approved' AND e.kind IN ('dictionary','translation')
                ORDER BY r.accepted DESC,e.created DESC,e.id DESC LIMIT 5000""").fetchall()
        matches, seen = [], set()
        for row in rows:
            payload = json.loads(row['payload'])
            term = _term(payload['zh' if reverse else 'ru'])
            if term in seen: continue
            seen.add(term)
            found = term in text if reverse else bool(re.search(r'(?<!\w)'+re.escape(term)+r'(?!\w)', text))
            if found:
                matches.append({'id': row['id'], 'kind': row['kind'], 'groupId': row['group_id'], **payload})
        return sorted(matches, key=lambda row: -len(_term(row['zh' if reverse else 'ru'])))[:limit]

    def rank_tags(self, tags, limit=8):
        """Rank supplied relevant tags using only operator-entered observations.

        A post's views cannot establish that a tag caused those views. ``score``
        is a shrinkage/recency heuristic, never a fabricated popularity count.
        Unmeasured supplied tags remain candidates with ``views=None``.
        """
        if not isinstance(tags, list) or len(tags) > 100 or type(limit) is not int or not 1 <= limit <= 30:
            raise ValueError('Неверный список подходящих тегов')
        tags = list(dict.fromkeys(_payload('tag_stat', {'tag': tag})['tag'] for tag in tags))
        grouped = {tag: [] for tag in tags}
        with self._db() as db:
            rows = db.execute("SELECT payload FROM examples WHERE status='approved' AND kind='tag_stat'").fetchall()
        legacy, latest_posts = [], {}
        for row in rows:
            payload = json.loads(row[0])
            post = payload.get('post')
            if isinstance(post, dict):
                previous = latest_posts.get(post['postId'])
                if previous is None or post['observedAt'] > previous['post']['observedAt']:
                    latest_posts[post['postId']] = payload
            else:
                legacy.append(payload)
        for payload in [*legacy, *latest_posts.values()]:
            measured_tags = payload.get('post', {}).get('tags', [payload['tag']])
            for tag in measured_tags:
                if tag in grouped: grouped[tag].append({**payload, 'tag': tag})
        result = []
        for tag in tags:
            measurements = grouped[tag]
            known = [row for row in measurements if row['views'] is not None]
            uses = sum(row['uses'] for row in measurements)
            mean = sum(row['views'] for row in known)/len(known) if known else None
            latest = max((row['observedAt'] for row in measurements), default=None)
            age = max(0, (time.time()-datetime.fromisoformat(latest).timestamp())/86400) if latest else None
            score = math.log1p(mean) * len(known)/(len(known)+5) * 2**(-age/90) if known else 0
            result.append({'tag': tag, 'score': round(score, 6), 'views': mean,
                           'observations': len(known), 'uses': uses, 'lastObservedAt': latest,
                           'confidence': 'unmeasured' if not known else 'limited' if len(known)<5 else 'observed',
                           'source': 'manual', 'causalEffectKnown': False})
        return sorted(result, key=lambda row: -row['score'])[:limit]

    def record_post_observation(self, post, *, approved=False):
        """One real publication observation, not one duplicate row per tag."""
        from tag_post_evaluation import normalize_post
        if type(approved) is not bool:
            raise ValueError('Подтверждение наблюдения должно быть явным')
        post = normalize_post(post)
        stamp = post['observedAt']
        if len(stamp) == 10:
            # This legacy ranking field needs a timestamp. The canonical post
            # keeps its date-only precision; matched analysis uses that value.
            stamp += 'T00:00:00+00:00'
        payload = {'tag': post['tags'][0], 'views': post['views'], 'uses': 1,
                   'observedAt': stamp, 'post': post}
        event_id = 'post-stat-'+_hash(_json(post))[:40]
        return self.record('tag_stat', payload, source='manual',
            status='approved' if approved else 'pending', group_id=post['postId'], event_id=event_id)

    def compare_post_observations(self, eligible_tags):
        from tag_post_evaluation import compare_posts, MAX_POSTS
        with self._db() as db:
            values = db.execute("SELECT payload FROM examples WHERE status='approved' AND kind='tag_stat' ORDER BY created DESC LIMIT ?", (MAX_POSTS,)).fetchall()
            pending = db.execute("SELECT COUNT(*) FROM examples WHERE status='pending' AND kind='tag_stat'").fetchone()[0]
        posts = [row['post'] for value in values if isinstance((row := json.loads(value[0])).get('post'), dict)]
        return {**compare_posts(posts, eligible_tags), 'pendingTagObservationsExcluded': pending,
                'legacyTagOnlyObservationsExcluded': len(values)-len(posts)}

    @staticmethod
    def _training_row(row):
        payload, kind = json.loads(row['payload']), row['kind']
        if kind in {'dictionary', 'translation'}:
            prompt, answer = 'Переведи на упрощённый китайский, сохрани факты:\n'+payload['ru'], payload['zh']
        elif kind == 'furniture':
            prompt = ('Сохрани ручные отметки мебели и техники. items — выбранные предметы; '
                      'excluded — снятые отметки (не подтверждены, наличие неизвестно). '
                      'Снятая отметка не доказывает отсутствие предмета. Верни JSON без догадок:')
            answer = _json({'items': payload['items'], 'excluded': payload.get('excluded', []),
                            'excludedMeaning': 'unconfirmed-not-absence'})
        else:
            # Standalone prose has no listing facts attached. Export as a style
            # example, never as a grounded listing-generation target.
            prompt, answer = 'Пример '+('заголовка' if kind == 'title' else 'описания')+' на '+payload['language']+' для изучения стиля:', payload['text']
        return {'id': row['id'], 'kind': kind, 'groupId': row['group_id'],
                'messages': [{'role': 'user', 'content': prompt}, {'role': 'assistant', 'content': answer}],
                'source': row['source']}

    def snapshot(self, destination=None, eval_fraction=.2):
        """Write approved-only JSONL + manifest; partitions persist across snapshots.

        The optional destination is a new direct subdirectory name under the
        private snapshots directory, not a filesystem path. Identical payloads
        that cross partitions are excluded from training (validation wins).
        """
        if type(eval_fraction) not in (float, int) or not .05 <= eval_fraction <= .5:
            raise ValueError('Доля проверки: 0.05–0.5')
        destination = destination or uuid.uuid4().hex
        if not isinstance(destination, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', destination):
            raise ValueError('Неверное имя снимка обучения')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            rows = db.execute("SELECT * FROM examples WHERE status='approved' AND kind<>'tag_stat' ORDER BY created,id").fetchall()
            partitions = {}
            for row in rows:
                group = row['group_id']
                if group in partitions: continue
                existing = db.execute('SELECT partition FROM splits WHERE group_id=?', (group,)).fetchone()
                part = existing[0] if existing else ('valid' if int(group[:8],16)/2**32 < eval_fraction else 'train')
                db.execute('INSERT OR IGNORE INTO splits VALUES (?,?)', (group, part))
                partitions[group] = part
        validation_fingerprints = {row['fingerprint'] for row in rows if partitions[row['group_id']] == 'valid'}
        train, valid, seen = [], [], set()
        for row in rows:
            part = partitions[row['group_id']]
            fingerprint = row['fingerprint']
            if fingerprint in seen or (part == 'train' and fingerprint in validation_fingerprints): continue
            seen.add(fingerprint)
            (valid if part == 'valid' else train).append(self._training_row(row))
        folder = self.directory/'snapshots'
        if folder.is_symlink(): raise ValueError('Каталог снимков не должен быть ссылкой')
        folder.mkdir(mode=0o700, exist_ok=True)
        target = folder/destination
        if target.exists() or target.is_symlink(): raise ValueError('Снимок с таким именем уже существует')
        temporary = Path(tempfile.mkdtemp(prefix='.snapshot-', dir=folder))
        contents = {}
        for name, values in [('train.jsonl',train), ('valid.jsonl',valid)]:
            contents[name] = ''.join(_json(row)+'\n' for row in values).encode('utf-8')
        manifest = {'schemaVersion': SCHEMA_VERSION, 'createdAt': _now(),
                    'trainExamples': len(train), 'validExamples': len(valid),
                    'trainGroups': len({row['groupId'] for row in train}),
                    'validGroups': len({row['groupId'] for row in valid}),
                    'approvedOnly': True, 'weightsUpdated': False, 'automaticPromotion': False,
                    'files': {name: {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)} for name,data in contents.items()}}
        contents['manifest.json'] = (_json(manifest)+'\n').encode('utf-8')
        try:
            for name, data in contents.items():
                path = temporary/name
                path.write_bytes(data); path.chmod(0o600)
            temporary.rename(target)
        finally:
            if temporary.exists():
                for path in temporary.iterdir(): path.unlink()
                temporary.rmdir()
        return {'directory': str(target), 'train': str(target/'train.jsonl'), 'valid': str(target/'valid.jsonl'), 'manifest': manifest}

    def save_checkpoint(self, job_id, payload):
        """Record a resumable job receipt; this method never starts or promotes a model."""
        if not isinstance(job_id, str) or not re.fullmatch(r'[\w-]{1,100}', job_id) or not isinstance(payload, dict):
            raise ValueError('Неверная контрольная точка обучения')
        allowed = {'state', 'step', 'epoch', 'snapshot', 'model', 'adapter', 'metrics', 'error'}
        if not set(payload) <= allowed or payload.get('state') not in {'queued', 'running', 'paused', 'failed', 'complete', 'unaccepted'}:
            raise ValueError('Неверные поля контрольной точки')
        for key in ('step','epoch'):
            if key in payload and (type(payload[key]) is not int or not 0 <= payload[key] <= 1_000_000_000):
                raise ValueError('Неверный шаг обучения')
        clean = dict(payload)
        for key in ('snapshot','model','adapter','error'):
            if key in clean: clean[key] = _text(clean[key], 500)
        if 'metrics' in clean:
            metrics = clean['metrics']
            if not isinstance(metrics, dict) or len(metrics)>30 or any(not isinstance(k,str) or not re.fullmatch(r'[\w-]{1,60}', k) or type(v) not in (int,float) or not math.isfinite(v) for k,v in metrics.items()):
                raise ValueError('Неверные измерения обучения')
        with self._db() as db:
            db.execute('INSERT INTO checkpoints VALUES (?,?,?) ON CONFLICT(job_id) DO UPDATE SET payload=excluded.payload,updated=excluded.updated', (job_id,_json(clean),_now()))
        return {'jobId': job_id, **clean}

    def checkpoints(self, limit=10):
        if type(limit) is not int or not 1 <= limit <= 50: raise ValueError('Предел контрольных точек: 1–50')
        with self._db() as db:
            rows = db.execute('SELECT * FROM checkpoints ORDER BY updated DESC LIMIT ?', (limit,)).fetchall()
        return [{'jobId': row['job_id'], 'updatedAt': row['updated'], **json.loads(row['payload'])} for row in rows]
