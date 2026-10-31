"""An explicitly armed, bounded campaign of ten manual Cian imports.

The HTTP controller owns authentication and decides which imports are manual.
This module never fetches a listing, launches a browser or sends an archive.
Only whitelisted timing/count metadata and canonical public offer links survive.
"""
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import io
import json
import math
import os
from pathlib import Path
import platform
import re
import secrets
import sqlite3
import stat
import subprocess
import tempfile
import threading
import time
from urllib.parse import urlsplit
import zipfile


TARGET = 10
MAX_EVENTS = 200
MAX_CAMPAIGNS = 20
MAX_BUNDLE_BYTES = 8 * 1024 * 1024
MAX_SECONDS = 86400
STAGES = frozenset(('preflight', 'browser', 'navigate', 'challenge', 'scroll',
                    'extract', 'generate', 'photos', 'crop', 'vision', 'save'))
SECTIONS = frozenset(('gallery', 'description', 'characteristics'))
PROFILES = frozenset(('import:auto', 'import:chrome', 'import:http',
                      'import:cache', 'prepare', 'import:vision', 'prepare:vision'))
OUTCOMES = frozenset(('success', 'error', 'cancelled', 'interrupted'))
_ID = re.compile(r'^[a-f0-9]{32}$')
_STATES = frozenset(('armed', 'paused', 'finishing', 'complete'))


def canonical_cian_url(value):
    """Recognize a public offer without DNS/network access; discard tracking."""
    if not isinstance(value, str) or len(value) > 4096:
        return None
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or '').lower()
        if (parsed.scheme != 'https' or parsed.username or parsed.password
                or parsed.port not in (None, 443)
                or not (host == 'cian.ru' or host.endswith('.cian.ru'))):
            return None
        match = re.fullmatch(r'/(rent|sale)/(flat|room|house|suburban|commercial|garage)/(\d{1,20})/?', parsed.path)
        if not match:
            return None
        return 'https://www.cian.ru/' + '/'.join(match.groups()) + '/'
    except ValueError:
        return None


def sanitize_message(value, limit=320):
    """Keep useful error prose while removing common credentials and PII."""
    text = str(value or '')[:8192]
    text = re.sub(r'[\ud800-\udfff]', '\ufffd', text)
    # Redact a whole header first: cookie values can contain many key/value pairs.
    text = re.sub(r'(?im)["\']?\b(?:authorization|proxy-authorization|set-cookie|cookie)\b["\']?\s*[:=]\s*[^\r\n]*', '[private header]', text)
    text = re.sub(r'(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]+', '[credential]', text)
    text = re.sub(r'''(?ix)\b(?:redcn[-_]phone|x[-_]?(?:redcn[-_]token|phone[-_]csrf)|csrf(?:[-_]?token)?|access[-_]?token|refresh[-_]?token|token|password|passwd|secret|session(?:[-_]?(?:id|token))?|api[-_]?key)\b["']?\s*(?:[:=]\s*|\s+)(?:"[^"]*"|'[^']*'|[^\s,;]+)''', '[private value]', text)
    text = re.sub(r'''(?ix)\b(?:raw[-_]?html|source[-_]?html|html|description|listing[-_]?(?:text|body)|body)\b["']?\s*[:=]\s*(?:"[^"]*"|'[^']*'|[^\r\n]+)''', '[page content]', text)
    text = re.sub(r'(?is)<(?!urlopen\b)(?:[a-z][a-z0-9:_-]*|!doctype)\b[^>]*>.*', '[page content]', text)
    text = re.sub(r'[a-z][a-z0-9+.-]{1,15}://[^\s<>"\']+', '[url]', text, flags=re.I)
    text = re.sub(r'\b[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\b', '[credential]', text)
    text = re.sub(r'(?i)\b[a-z0-9.!#$%&\'*+/=?^_`{|}~-]+@[a-z0-9.-]+\.[a-z]{2,}\b', '[email]', text)
    text = re.sub(r'(?i)(?:[A-Z]:\\|/(?:Users|home|private|tmp|var|workspace)/)[^\s<>"\']+', '[local path]', text)
    text = re.sub(r'(?<![\w\d])\+?\d(?:[ ()-]*\d){9,14}(?!\d)', '[phone]', text)
    text = re.sub(r'(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])', '[ip]', text)
    text = re.sub(r'(?<![A-Za-z0-9])[A-Za-z0-9_+/=-]{32,}(?![A-Za-z0-9])', '[identifier]', text)
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
    return text[:limit]


def _number(value, maximum=MAX_SECONDS):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= maximum:
        return None
    return round(value, 3)


def _count(value):
    return value if type(value) is int and 0 <= value <= 1000000 else None


def _event_fields(message='', **fields):
    output = {}
    if message:
        output['message'] = sanitize_message(message)
    stage = 'crop' if fields.get('stage') == 'autoCrop' else fields.get('stage')
    if stage in STAGES:
        output['stage'] = stage
    if fields.get('section') in SECTIONS:
        output['section'] = fields['section']
    for name in ('completed', 'total', 'reads', 'blocksVisited', 'scrolls', 'imageCandidates',
                 'pageHeight', 'scrollTop', 'documentBytes', 'textChars', 'imageCount', 'domReads'):
        value = _count(fields.get(name))
        if value is not None:
            output[name] = value
    for name in ('awaitingUser', 'sectionReady', 'ready', 'found', 'cached',
                 'domReady', 'imagesReady', 'descriptionReady', 'characteristicsReady'):
        if type(fields.get(name)) is bool:
            output[name] = fields[name]
    for name in ('seconds', 'activeSeconds', 'maxTotalSeconds'):
        value = _number(fields.get(name))
        if value is not None:
            output[name] = value
    if fields.get('profile') in PROFILES:
        output['profile'] = fields['profile']
    if fields.get('readyState') in ('loading', 'interactive', 'complete'):
        output['readyState'] = fields['readyState']
    if fields.get('phase') in ('navigation', 'domcontentloaded', 'document', 'section-probe',
                             'images', 'description', 'characteristics'):
        output['phase'] = fields['phase']
    if fields.get('browserChannel') in ('chrome', 'chromium'):
        output['browserChannel'] = fields['browserChannel']
    return output


def _progress(value):
    if not isinstance(value, dict):
        return {}
    output = {}
    if value.get('profile') in PROFILES:
        output['profile'] = value['profile']
    for key in ('elapsedSeconds', 'stageElapsedSeconds', 'estimateSeconds'):
        measured = _number(value.get(key))
        if measured is not None:
            output[key] = measured
    if type(value.get('awaitingUser')) is bool:
        output['awaitingUser'] = value['awaitingUser']
    rows = []
    for row in value.get('stages', [])[:40] if isinstance(value.get('stages'), list) else []:
        if not isinstance(row, dict) or row.get('id') not in STAGES:
            continue
        stage = {'id': row['id']}
        duration = _number(row.get('elapsedSeconds'))
        if duration is not None:
            stage['elapsedSeconds'] = duration
        for key in ('completed', 'total'):
            count = _count(row.get(key))
            if count is not None:
                stage[key] = count
        if row.get('state') in ('running', 'done', 'error', 'waitingManual'):
            stage['state'] = row['state']
        rows.append(stage)
    if rows:
        output['stages'] = rows
    return output


def _capture(value):
    if not isinstance(value, dict):
        return {}
    output = {}
    if value.get('strategy') in ('sections', 'page'):
        output['strategy'] = value['strategy']
    for key in ('version', 'scrolls', 'imageCandidates'):
        count = _count(value.get(key))
        if count is not None:
            output[key] = count
    for key in ('completed', 'bounded', 'pageBottomReached', 'sectionFallback'):
        if type(value.get(key)) is bool:
            output[key] = value[key]
    for key in ('seconds', 'activeSeconds'):
        duration = _number(value.get(key))
        if duration is not None:
            output[key] = duration
    sections = []
    for row in value.get('sections', [])[:3] if isinstance(value.get('sections'), list) else []:
        if not isinstance(row, dict) or row.get('section') not in SECTIONS:
            continue
        sections.append(_event_fields(**row))
    if sections:
        output['sections'] = sections
    return output


def _environment():
    """One bounded local probe when explicitly arming, never at each import."""
    result = {'python': platform.python_version(), 'system': platform.system(),
              'systemRelease': platform.release(), 'machine': platform.machine()}
    for name in ('playwright', 'Pillow', 'certifi'):
        try:
            result[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            pass
    try:
        from node_runtime import resolve_node
        node = Path(resolve_node())
        result['nodeFound'] = result['nodeReady'] = True
    except (OSError, ValueError) as error:
        node = None
        result['nodeFound'] = result['nodeReady'] = False
        result['nodeProbeError'] = type(error).__name__
    if node is not None:
        try:
            probe = subprocess.run([str(node), '--version'], stdin=subprocess.DEVNULL,
                capture_output=True, text=True, timeout=1.5, check=False)
            if probe.returncode == 0 and re.fullmatch(r'v\d+\.\d+\.\d+(?:[-+][\w.-]+)?\s*', probe.stdout):
                result['nodeVersion'] = probe.stdout.strip()
            else:
                result['nodeProbeError'] = 'invalid_version'
        except (OSError, subprocess.SubprocessError) as error:
            result['nodeProbeError'] = type(error).__name__
    return result


def _safe_environment(value):
    output = {}
    if not isinstance(value, dict):
        return output
    for key in ('python', 'system', 'systemRelease', 'machine', 'playwright', 'Pillow',
                'certifi', 'nodeVersion', 'nodeProbeError', 'appVersion', 'browserChannel'):
        if isinstance(value.get(key), str):
            output[key] = sanitize_message(value[key], 100)
    for key in ('nodeFound', 'nodeReady'):
        if type(value.get(key)) is bool:
            output[key] = value[key]
    return output


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def _private_file(path):
    if path.is_symlink() or (path.exists() and not stat.S_ISREG(path.stat().st_mode)):
        raise ValueError('Недопустимый файл диагностики')


def _atomic_write(path, content):
    _private_file(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.tg-', delete=False) as output:
            temporary = Path(output.name)
            output.write(content)
        temporary.chmod(0o600)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class ImportDiagnostics:
    """Persistent campaign; integrate begin/report/finally at the manual job owner."""
    def __init__(self, directory, *, clock=time.monotonic, wall_clock=time.time,
                 environment=_environment):
        root = Path(directory).expanduser()
        if root.is_symlink():
            raise ValueError('Каталог диагностики не может быть ссылкой')
        root.mkdir(parents=True, exist_ok=True)
        self.root = root
        self.folder = root / 'import-diagnostics'
        if self.folder.is_symlink():
            raise ValueError('Каталог диагностики не может быть ссылкой')
        self.folder.mkdir(mode=0o700, exist_ok=True)
        self.folder.chmod(0o700)
        self.archives = self.folder / 'archives'
        if self.archives.is_symlink():
            raise ValueError('Каталог архивов не может быть ссылкой')
        self.archives.mkdir(mode=0o700, exist_ok=True)
        self.archives.chmod(0o700)
        self.path = self.folder / 'campaigns.sqlite3'
        self.clock, self.wall_clock, self.environment = clock, wall_clock, environment
        self.lock, self.started, self.closed = threading.RLock(), {}, False
        with self._database():
            pass

    @contextmanager
    def _database(self):
        for folder in (self.root, self.folder, self.archives):
            if folder.is_symlink() or not folder.is_dir():
                raise ValueError('Недопустимый каталог диагностики')
        _private_file(self.path)
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        self.path.chmod(0o600)
        try:
            connection.execute('CREATE TABLE IF NOT EXISTS campaigns (id TEXT PRIMARY KEY, target INTEGER NOT NULL CHECK(target=10), state TEXT NOT NULL, created REAL NOT NULL, finished REAL, environment TEXT NOT NULL, bundle_sha TEXT, bundle_bytes INTEGER, bundle_error TEXT)')
            connection.execute('CREATE TABLE IF NOT EXISTS attempts (id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL, job_hash TEXT NOT NULL, ordinal INTEGER NOT NULL, url TEXT NOT NULL, started REAL NOT NULL, finished REAL, elapsed REAL, outcome TEXT NOT NULL, progress TEXT NOT NULL, capture TEXT NOT NULL, error_class TEXT NOT NULL, error_message TEXT NOT NULL, events_dropped INTEGER NOT NULL DEFAULT 0, UNIQUE(campaign_id,job_hash), UNIQUE(campaign_id,ordinal))')
            connection.execute('CREATE TABLE IF NOT EXISTS events (attempt_id TEXT NOT NULL, sequence INTEGER NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(attempt_id,sequence))')
            connection.commit()
            yield connection
        finally:
            connection.close()

    def _latest(self, db):
        return db.execute('SELECT * FROM campaigns ORDER BY created DESC,rowid DESC LIMIT 1').fetchone()

    def _elapsed(self, attempt):
        start = self.started.get(attempt['id'])
        value = self.clock() - start if start is not None else self.wall_clock() - attempt['started']
        return round(min(MAX_SECONDS, max(0, value)), 3)

    def arm(self, target=TARGET):
        if type(target) is not int or target != TARGET:
            raise ValueError('Диагностика запускается ровно на 10 ручных проверок')
        with self.lock, self._database() as db:
            db.execute('BEGIN IMMEDIATE')
            old = self._latest(db)
            if old is not None and old['state'] != 'complete':
                if old['state'] == 'paused':
                    reserved = db.execute('SELECT COUNT(*) FROM attempts WHERE campaign_id=?', (old['id'],)).fetchone()[0]
                    db.execute('UPDATE campaigns SET state=? WHERE id=?', ('finishing' if reserved >= TARGET else 'armed', old['id']))
                db.commit()
            else:
                if db.execute('SELECT COUNT(*) FROM campaigns').fetchone()[0] >= MAX_CAMPAIGNS:
                    raise ValueError('Сохранено 20 диагностических кампаний. Сначала разберите локальные архивы.')
                environment = _safe_environment(self.environment())
                db.execute('INSERT INTO campaigns(id,target,state,created,environment) VALUES (?,?,?,?,?)',
                           (secrets.token_hex(16), TARGET, 'armed', self.wall_clock(), _json(environment)))
                db.commit()
            self.closed = False
        return self.status()

    def pause(self):
        with self.lock, self._database() as db:
            row = self._latest(db)
            if row is not None and row['state'] != 'complete':
                db.execute('UPDATE campaigns SET state=? WHERE id=?', ('paused', row['id']))
                db.commit()
        return self.status()

    def begin(self, job_id, url, *, manual=True):
        if self.closed or manual is not True or not isinstance(job_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', job_id):
            return None
        source = canonical_cian_url(url)
        if source is None:
            return None
        job_hash = hashlib.sha256(job_id.encode()).hexdigest()
        with self.lock, self._database() as db:
            db.execute('BEGIN IMMEDIATE')
            campaign = self._latest(db)
            if campaign is None or campaign['state'] not in ('armed', 'finishing'):
                return None
            old = db.execute('SELECT id FROM attempts WHERE campaign_id=? AND job_hash=?', (campaign['id'], job_hash)).fetchone()
            if old is not None:
                return old['id']
            reserved = db.execute('SELECT COUNT(*) FROM attempts WHERE campaign_id=?', (campaign['id'],)).fetchone()[0]
            if campaign['state'] != 'armed' or reserved >= TARGET:
                return None
            token, now = secrets.token_hex(16), self.wall_clock()
            db.execute('INSERT INTO attempts(id,campaign_id,job_hash,ordinal,url,started,outcome,progress,capture,error_class,error_message) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                       (token, campaign['id'], job_hash, reserved + 1, source, now, 'running', '{}', '{}', '', ''))
            db.execute('INSERT INTO events VALUES (?,?,?)', (token, 0, _json({'elapsedSeconds': 0, 'at': now, 'stage': 'preflight'})))
            if reserved + 1 == TARGET:
                db.execute('UPDATE campaigns SET state=? WHERE id=?', ('finishing', campaign['id']))
            db.commit()
            self.started[token] = self.clock()
            return token

    def record(self, token, message='', **fields):
        if not isinstance(token, str) or not _ID.fullmatch(token):
            return False
        event = _event_fields(message, **fields)
        with self.lock, self._database() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM attempts WHERE id=?', (token,)).fetchone()
            if row is None or row['outcome'] != 'running':
                return False
            count = db.execute('SELECT COUNT(*) FROM events WHERE attempt_id=?', (token,)).fetchone()[0]
            if count >= MAX_EVENTS:
                db.execute('UPDATE attempts SET events_dropped=min(events_dropped+1,1000000) WHERE id=?', (token,))
                db.commit()
                return False
            event.update(elapsedSeconds=self._elapsed(row), at=self.wall_clock())
            db.execute('INSERT INTO events VALUES (?,?,?)', (token, count, _json(event)))
            db.commit()
            return True

    def complete(self, token, outcome='success', *, progress=None, capture=None, error=None):
        outcome = {'done': 'success', 'cancel': 'cancelled'}.get(outcome, outcome)
        if outcome not in OUTCOMES:
            raise ValueError('Неизвестный результат диагностической проверки')
        if not isinstance(token, str) or not _ID.fullmatch(token):
            return self.status()
        campaign_id = None
        with self.lock, self._database() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM attempts WHERE id=?', (token,)).fetchone()
            if row is not None and row['outcome'] == 'running':
                error_class = type(error).__name__ if isinstance(error, BaseException) else ('ImportError' if error else '')
                if error_class and not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,79}', error_class):
                    error_class = 'Error'
                db.execute('UPDATE attempts SET finished=?,elapsed=?,outcome=?,progress=?,capture=?,error_class=?,error_message=? WHERE id=?',
                           (self.wall_clock(), self._elapsed(row), outcome, _json(_progress(progress)), _json(_capture(capture)), error_class,
                            sanitize_message(error) if error else '', token))
                campaign_id = row['campaign_id']
                completed = db.execute('SELECT COUNT(*) FROM attempts WHERE campaign_id=? AND outcome<>?', (campaign_id, 'running')).fetchone()[0]
                if completed == TARGET:
                    db.execute('UPDATE campaigns SET state=?,finished=? WHERE id=?', ('complete', self.wall_clock(), campaign_id))
                db.commit()
                self.started.pop(token, None)
        if campaign_id:
            self._ensure_bundle(campaign_id)
        return self.status()

    def recover_interrupted(self):
        """Call once when the owning server starts, before accepting new jobs."""
        with self.lock, self._database() as db:
            rows = db.execute('SELECT id FROM attempts WHERE outcome=? ORDER BY started,ordinal', ('running',)).fetchall()
        for row in rows:
            self.complete(row['id'], 'interrupted', error='Сервер завершился до окончания ручной проверки')
        return self.status()

    def close(self):
        # A graceful controller may still let its in-flight worker finish.
        self.closed = True

    def status(self):
        with self.lock, self._database() as db:
            row = self._latest(db)
            if row is None:
                return {'version': 1, 'state': 'idle', 'enabled': False, 'target': TARGET,
                        'reserved': 0, 'completed': 0, 'inFlight': 0, 'remaining': TARGET,
                        'campaignId': None, 'bundleReady': False, 'bundleName': 'tg.zip', 'checks': []}
            attempts = db.execute('SELECT * FROM attempts WHERE campaign_id=? ORDER BY ordinal', (row['id'],)).fetchall()
            completed = sum(item['outcome'] != 'running' for item in attempts)
            archive = self.archives / (row['id'] + '.zip')
            ready = bool(row['bundle_sha'] and archive.is_file() and not archive.is_symlink())
            checks = [{'number': item['ordinal'], 'url': item['url'], 'outcome': item['outcome'],
                       'elapsedSeconds': self._elapsed(item) if item['outcome'] == 'running' else item['elapsed'],
                       'eventsDropped': item['events_dropped']} for item in attempts]
            return {'version': 1, 'state': row['state'] if row['state'] in _STATES else 'paused',
                    'enabled': not self.closed and row['state'] == 'armed' and len(attempts) < TARGET,
                    'target': TARGET, 'reserved': len(attempts), 'completed': completed,
                    'inFlight': len(attempts) - completed, 'remaining': TARGET - len(attempts),
                    'campaignId': row['id'], 'startedAt': row['created'], 'finishedAt': row['finished'],
                    'bundleReady': ready, 'bundleName': 'tg.zip', 'bundleBytes': row['bundle_bytes'],
                    'bundleError': row['bundle_error'], 'checks': checks}

    @staticmethod
    def _measure(attempt, events):
        total = attempt['elapsed'] or 0
        stages, sections = {}, {}
        current_stage, stage_at, current_section, section_at = 'preflight', 0, None, 0
        for event in events:
            at = min(total, max(0, event['elapsedSeconds']))
            stage = event.get('stage')
            if stage in STAGES and stage != current_stage:
                stages[current_stage] = stages.get(current_stage, 0) + max(0, at - stage_at)
                current_stage, stage_at = stage, at
            section = event.get('section')
            if section in SECTIONS:
                if section != current_section:
                    if current_section:
                        sections[current_section] = sections.get(current_section, 0) + max(0, at - section_at)
                    current_section, section_at = section, at
                # The same section is reported at its start and bounded end.
                # Whole-page fallback must not inflate its measured duration.
                if 'sectionReady' in event:
                    sections[current_section] = sections.get(current_section, 0) + max(0, at - section_at)
                    current_section = None
            elif current_section and stage in ('scroll', 'extract'):
                sections[current_section] = sections.get(current_section, 0) + max(0, at - section_at)
                current_section = None
        stages[current_stage] = stages.get(current_stage, 0) + max(0, total - stage_at)
        if current_section:
            # The extract stage ends the section walk, not photo download/generation.
            end = next((event['elapsedSeconds'] for event in events if event.get('stage') == 'extract'
                        and event['elapsedSeconds'] >= section_at), total)
            sections[current_section] = sections.get(current_section, 0) + max(0, end - section_at)
        progress = json.loads(attempt['progress'])
        if progress.get('stages'):
            stages = {item['id']: item['elapsedSeconds'] for item in progress['stages'] if 'elapsedSeconds' in item}
        capture = json.loads(attempt['capture'])
        for row in capture.get('sections', []):
            if 'seconds' in row:
                sections[row['section']] = row['seconds']
        return {key: round(value, 3) for key, value in stages.items()}, {key: round(value, 3) for key, value in sections.items()}

    def _ensure_bundle(self, campaign_id):
        if not _ID.fullmatch(campaign_id):
            return
        try:
            with self.lock, self._database() as db:
                db.execute('BEGIN IMMEDIATE')
                campaign = db.execute('SELECT * FROM campaigns WHERE id=?', (campaign_id,)).fetchone()
                if campaign is None or campaign['state'] != 'complete' or campaign['bundle_sha']:
                    return
                attempts = db.execute('SELECT * FROM attempts WHERE campaign_id=? ORDER BY ordinal', (campaign_id,)).fetchall()
                if len(attempts) != TARGET or any(row['outcome'] == 'running' for row in attempts):
                    return
                checks, flat_events = [], []
                for attempt in attempts:
                    events = [json.loads(row[0]) for row in db.execute('SELECT payload FROM events WHERE attempt_id=? ORDER BY sequence', (attempt['id'],))]
                    stages, sections = self._measure(attempt, events)
                    checks.append({'number': attempt['ordinal'], 'url': attempt['url'], 'startedAt': attempt['started'],
                                   'finishedAt': attempt['finished'], 'elapsedSeconds': attempt['elapsed'],
                                   'outcome': attempt['outcome'], 'stagesSeconds': stages, 'sectionsSeconds': sections,
                                   'progress': json.loads(attempt['progress']), 'capture': json.loads(attempt['capture']),
                                   'errorClass': attempt['error_class'], 'errorMessage': attempt['error_message'],
                                   'eventsStored': len(events), 'eventsDropped': attempt['events_dropped']})
                    flat_events.extend({'check': attempt['ordinal'], **event} for event in events)
                durations = [row['elapsedSeconds'] for row in checks]
                slowest = sorted(checks, key=lambda row: row['elapsedSeconds'], reverse=True)
                manifest = {'version': 1, 'name': 'tg.zip', 'campaignId': campaign_id,
                            'createdAtUtc': datetime.fromtimestamp(campaign['created'], timezone.utc).isoformat(),
                            'completedAtUtc': datetime.fromtimestamp(campaign['finished'], timezone.utc).isoformat(),
                            'manualOnly': True, 'target': TARGET, 'completed': len(checks),
                            'captureEnabled': False, 'outcomes': dict(Counter(row['outcome'] for row in checks)),
                            'meanSeconds': round(sum(durations) / TARGET, 3), 'maxSeconds': max(durations),
                            'eventLimitPerCheck': MAX_EVENTS,
                            'excluded': ['cookies', 'authorization headers', 'tokens', 'profile', 'contracts', 'raw HTML', 'photos', 'listing body'],
                            'files': ['manifest.json', 'checks.jsonl', 'events.ndjson', 'environment.json', 'summary.txt']}
                lines = ['Диагностика 10 ручных проверок ЦИАН',
                         'Сбор завершён автоматически; обычный импорт объявлений продолжает работать.',
                         'Среднее фактическое время: ' + str(manifest['meanSeconds']) + ' с.',
                         'Длительность включает ожидание ручной проверки источника; профиль cache означает чтение кеша.',
                         'Исходные HTML, фотографии, cookie, токены и личные досье не включены.', '', 'Самые долгие проверки:']
                for row in slowest:
                    lines.append(str(row['number']) + ': ' + str(row['elapsedSeconds']) + ' с, ' + row['outcome'] + ', ' + row['url'])
                    lines.append('  этапы: ' + _json(row['stagesSeconds']) + '; блоки: ' + _json(row['sectionsSeconds']))
                stream = io.BytesIO()
                with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
                    bundle.writestr('manifest.json', _json(manifest))
                    bundle.writestr('checks.jsonl', ''.join(_json(row) + '\n' for row in checks))
                    bundle.writestr('events.ndjson', ''.join(_json(row) + '\n' for row in flat_events))
                    bundle.writestr('environment.json', campaign['environment'])
                    bundle.writestr('summary.txt', '\n'.join(lines) + '\n')
                content = stream.getvalue()
                if len(content) > MAX_BUNDLE_BYTES:
                    raise ValueError('Архив диагностики превышает 8 МиБ')
                _atomic_write(self.archives / (campaign_id + '.zip'), content)
                _atomic_write(self.folder / 'tg.zip', content)
                db.execute('UPDATE campaigns SET bundle_sha=?,bundle_bytes=?,bundle_error=NULL WHERE id=?',
                           (hashlib.sha256(content).hexdigest(), len(content), campaign_id))
                db.commit()
        except (OSError, ValueError, sqlite3.Error, zipfile.BadZipFile) as error:
            # Diagnostics must never turn the user's successful import into failure.
            try:
                with self._database() as db:
                    db.execute('UPDATE campaigns SET bundle_error=? WHERE id=?', (type(error).__name__, campaign_id))
                    db.commit()
            except (OSError, ValueError, sqlite3.Error):
                pass

    def bundle_asset(self):
        with self.lock, self._database() as db:
            row = db.execute('SELECT id FROM campaigns WHERE state=? ORDER BY finished DESC,rowid DESC LIMIT 1', ('complete',)).fetchone()
        if row is None:
            raise ValueError('Архив появится после 10 завершённых ручных проверок')
        self._ensure_bundle(row['id'])
        with self.lock, self._database() as db:
            campaign = db.execute('SELECT * FROM campaigns WHERE id=?', (row['id'],)).fetchone()
            path = self.archives / (row['id'] + '.zip')
            _private_file(path)
            if not campaign['bundle_sha'] or not path.is_file() or path.stat().st_size > MAX_BUNDLE_BYTES:
                raise ValueError('Архив диагностики пока недоступен')
            with path.open('rb') as source:
                content = source.read(MAX_BUNDLE_BYTES + 1)
            if len(content) != campaign['bundle_bytes'] or hashlib.sha256(content).hexdigest() != campaign['bundle_sha']:
                raise ValueError('Архив диагностики повреждён')
            return content, 'application/zip', 'tg.zip'
