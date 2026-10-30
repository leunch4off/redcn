"""Observed import stages and a bounded history of successful durations.

Only timing/count metadata is kept here: no URL, listing text or credentials.
The first import has no invented ETA. Manual browser checks suspend countdowns.
"""
import copy
import inspect
import json
import math
from pathlib import Path
import tempfile
import threading
import time


STAGES = (
    ('preflight', 'Проверка генератора'), ('browser', 'Запуск Chrome'),
    ('navigate', 'Загрузка страницы'), ('challenge', 'Проверка в браузере'),
    ('scroll', 'Прокрутка и догрузка'), ('extract', 'Чтение объявления'),
    ('generate', 'Китайский текст'), ('photos', 'Загрузка фотографий'),
    ('crop', 'Автоматическое кадрирование'), ('save', 'Сохранение карточки'),
)
LABELS = dict(STAGES)
PROFILES = ('import:auto', 'import:chrome', 'import:http', 'import:cache', 'prepare')


def emit(report, message, **fields):
    """Keep one-argument CLI/test callbacks while enriching web job callbacks."""
    if report is None:
        return
    if fields.get('stage') == 'autoCrop':
        fields['stage'] = 'crop'
    try:
        inspect.signature(report).bind(message, **fields)
    except (TypeError, ValueError):
        report(message)
    else:
        report(message, **fields)


class DurationHistory:
    def __init__(self, directory, limit=30):
        self.path = Path(directory) / 'import-timings.json'
        self.limit = limit
        self.lock = threading.RLock()

    def _read(self):
        if self.path.is_symlink():
            return {}
        try:
            if self.path.stat().st_size > 262_144:
                return {}
            value = json.loads(self.path.read_text(encoding='utf-8'))
            if value.get('version') != 1 or not isinstance(value.get('samples'), dict):
                return {}
            result = {}
            for key, rows in value['samples'].items():
                if key not in PROFILES or not isinstance(rows, list):
                    continue
                result[key] = []
                for row in rows[-self.limit:]:
                    if (not isinstance(row, dict) or type(row.get('seconds')) not in (int,float)
                            or not math.isfinite(row['seconds']) or not 0 < row['seconds'] <= 7200):
                        continue
                    measured = {'seconds':row['seconds']}
                    if type(row.get('at')) in (int,float) and math.isfinite(row['at']):
                        measured['at'] = row['at']
                    if isinstance(row.get('stages'),dict):
                        measured['stages'] = {stage:seconds for stage,seconds in row['stages'].items()
                            if stage in LABELS and type(seconds) in (int,float) and math.isfinite(seconds)
                            and 0 <= seconds <= 7200}
                    result[key].append(measured)
            return result
        except (OSError, ValueError, AttributeError):
            return {}

    def estimate(self, key):
        with self.lock:
            rows = self._read().get(key, [])
        if not rows:
            return {'estimateSeconds': None, 'estimateSource': 'none',
                    'sampleCount': 0, 'historyRangeSeconds': None}
        values = [row['seconds'] for row in rows]
        return {'estimateSeconds': round(sum(values) / len(values), 1),
                'estimateSource': 'history', 'sampleCount': len(values),
                'historyRangeSeconds': [round(min(values), 1), round(max(values), 1)]}

    def record(self, key, seconds, stages):
        if key not in PROFILES or not 0 < seconds <= 7200:
            return
        with self.lock:
            if self.path.is_symlink():
                return
            samples = self._read()
            rows = samples.setdefault(key, [])
            rows.append({'seconds': round(seconds, 3), 'at': time.time(),
                'stages': {stage: round(duration, 3) for stage, duration in stages.items()
                           if stage in LABELS and 0 <= duration <= 7200}})
            samples[key] = rows[-self.limit:]
            temporary = None
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(mode='w', dir=self.path.parent,
                        encoding='utf-8', delete=False) as output:
                    temporary = Path(output.name)
                    json.dump({'version': 1, 'samples': samples}, output, ensure_ascii=False)
                    output.write('\n')
                temporary.chmod(0o600)
                temporary.replace(self.path)
            except OSError:
                # Telemetry cannot turn a completed listing into an error.
                pass
            finally:
                if temporary is not None:
                    try:temporary.unlink(missing_ok=True)
                    except OSError:pass


class JobProgress:
    def __init__(self, history, key, *, clock=time.monotonic, wall_clock=time.time):
        self.history, self.key = history, key
        self.clock, self.wall_clock = clock, wall_clock
        self.lock = threading.RLock()
        self.started = self.stage_started = clock()
        self.started_at = wall_clock()
        self.current = 'preflight' if key.startswith('import:') else 'photos'
        self.message = ''
        self.state = 'running'
        self.stopped = None
        self.stage_counts = {self.current:{}}
        self.counts = self.stage_counts[self.current]
        self.durations = {}
        self.completed_stages = []
        self.seen = [self.current]
        self.awaiting_user = False
        self.wait_limit = None
        self.estimate = history.estimate(key)

    def set_profile(self, key):
        if key not in PROFILES:
            raise ValueError('Неизвестный тип измерения')
        with self.lock:
            if key != self.key:
                self.key, self.estimate = key, self.history.estimate(key)

    def update(self, message, stage=None, completed=None, total=None,
               awaitingUser=None, maxTotalSeconds=None, **_unused):
        with self.lock:
            if self.state != 'running':
                return
            stage = 'crop' if stage == 'autoCrop' else stage
            if stage not in LABELS:
                stage = self.current
            now = self.clock()
            if stage != self.current:
                self.durations[self.current] = self.durations.get(self.current, 0) + now - self.stage_started
                if self.current not in self.completed_stages:
                    self.completed_stages.append(self.current)
                self.current, self.stage_started = stage, now
                if stage not in self.seen:
                    self.seen.append(stage)
                self.awaiting_user = stage == 'challenge'
                self.wait_limit = None
                self.counts = self.stage_counts.setdefault(stage,{})
            self.message = str(message)
            if awaitingUser is not None:
                self.awaiting_user = bool(awaitingUser)
            if type(maxTotalSeconds) in (int, float) and maxTotalSeconds > 0:
                self.wait_limit = maxTotalSeconds
            if type(completed) is int and completed >= 0:
                self.counts['completed'] = completed
            if type(total) is int and total >= 0:
                self.counts['total'] = total

    def finish(self, success):
        with self.lock:
            if self.state != 'running':
                return
            self.stopped = self.clock()
            self.durations[self.current] = self.durations.get(self.current, 0) + self.stopped - self.stage_started
            self.state = 'done' if success else 'error'
            if success:
                if self.current not in self.completed_stages:
                    self.completed_stages.append(self.current)
                self.history.record(self.key, self.stopped - self.started, self.durations)
                self.estimate = self.history.estimate(self.key)

    def snapshot(self):
        with self.lock:
            now = self.stopped if self.stopped is not None else self.clock()
            elapsed = max(0, now - self.started)
            stage_elapsed = max(0, now - self.stage_started)
            average = self.estimate['estimateSeconds']
            exceeded = average is not None and elapsed > average and self.state == 'running'
            remaining = (max(0, round(average-elapsed, 1)) if average is not None
                and not exceeded and not self.awaiting_user and self.state == 'running' else None)
            rows = []
            for stage in self.seen:
                current = stage == self.current
                duration = self.durations.get(stage, 0)
                if current and self.state == 'running':
                    duration += stage_elapsed
                rows.append({'id': stage, 'label': LABELS[stage],
                    'state': ('error' if self.state == 'error' else
                              'waitingManual' if self.awaiting_user and self.state == 'running' else
                              'running' if self.state == 'running' else 'done') if current else 'done',
                    'elapsedSeconds': round(duration, 1),
                    **self.stage_counts.get(stage,{})})
            return {'stage': 'done' if self.state == 'done' else self.current,
                'label': 'Готово' if self.state == 'done' else LABELS[self.current],
                'message': self.message, 'state': self.state,
                'startedAt': self.started_at, 'serverTime': self.wall_clock(),
                'profile': self.key,
                'elapsedSeconds': round(elapsed, 1), 'stageElapsedSeconds': round(stage_elapsed, 1),
                'awaitingUser': self.awaiting_user and self.state == 'running',
                'maxTotalSeconds': self.wait_limit, 'remainingSeconds': remaining,
                'estimateExceeded': exceeded, **copy.deepcopy(self.estimate),
                **self.counts, 'stages': rows}
