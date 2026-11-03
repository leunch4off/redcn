"""Bounded, transient text jobs scoped to one workspace and their creator.

No requests, prompts or credentials enter progress or logs. Unlike import
jobs, a preview need not save a card; a restarted process cannot resume it.
The model's existing resource gate and cancellation callback own inference.
"""
import copy
import hashlib
import json
from pathlib import Path
import re
import secrets
import threading
import time

from ai_telemetry import sanitize_ai
from job_progress import DurationHistory, JobProgress


UNAVAILABLE = 'Задание недоступно или истекло. Откройте карточку и запустите новую генерацию при необходимости.'
_ID = re.compile(r'[a-f0-9]{32}\Z')
_REQUEST = re.compile(r'[A-Za-z0-9_-]{12,80}\Z')
_PHASE_MESSAGES = {
    'waiting': 'ИИ ожидает освобождения локальной модели',
    'loading': 'Загрузка локальной текстовой модели',
    'preparing': 'Проверка фактов квартиры и подтверждённых предметов',
    'prefill': 'Модель читает факты квартиры и задание',
    'generating': 'ИИ пишет индивидуальное объявление',
    'complete': 'Проверка завершённого ответа модели',
    'cancelled': 'Генерация отменяется',
    'timeout': 'Модель достигла предела времени',
    'unavailable': 'Локальная модель недоступна',
    'error': 'Не удалось завершить работу локальной модели',
}
_DRAFT_KEYS = frozenset(('title', 'body', 'tags', 'status', 'autoApply', 'fallback',
    'modelAccepted', 'textSource', 'generationMode', 'contract', 'modelContribution',
    'factualTextSource', 'factsHash', 'inventoryCount', 'photoInventoryCount',
    'sourceContextCharacters', 'sourceContextTruncated', 'styleId', 'variation',
    'part', 'model', 'backend', 'ai', 'attempts', 'memoryWarning', 'elapsedSeconds', 'warnings'))


class TextJobError(ValueError):
    def __init__(self, message, *, status=400, code='TEXT_JOB_INVALID'):
        super().__init__(message)
        self.status, self.code = status, code


class _Cancelled(Exception):
    pass


def _json_copy(value, *, limit):
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode('utf-8')) > limit:
            raise ValueError()
        return json.loads(encoded)
    except (TypeError, ValueError, OverflowError, RecursionError):
        raise TextJobError('Недопустимый размер или формат данных генерации') from None


def _request(data, allowed_actions):
    if not isinstance(data, dict) or data.get('action') not in allowed_actions:
        raise TextJobError('Недоступный способ генерации текста')
    action = data['action']
    keys = {'action', 'requestId', 'part'} | ({'fields', 'output', 'groupId'} if action == 'preview' else {'cardId'})
    if set(data) - keys or data.get('part', 'all') not in ('title', 'body', 'all'):
        raise TextJobError('Укажите заголовок, описание или оба поля')
    request_id = data.get('requestId')
    if request_id is not None and (not isinstance(request_id, str) or not _REQUEST.fullmatch(request_id)):
        raise TextJobError('Неверный идентификатор запроса')
    if action == 'preview':
        if not isinstance(data.get('fields'), dict):
            raise TextJobError('Нужны факты квартиры')
        output = data.get('output', {})
        if (not isinstance(output, dict) or set(output) - {'title', 'body'}
                or any(not isinstance(output.get(key, ''), str) or len(output.get(key, '')) > limit
                    for key, limit in (('title', 500), ('body', 20000)))):
            raise TextJobError('Неверный текущий текст объявления')
        group = data.get('groupId')
        if group is not None and (not isinstance(group, str) or not 1 <= len(group) <= 2000):
            raise TextJobError('Неверная группа истории генерации')
    elif not isinstance(data.get('cardId'), str) or not _ID.fullmatch(data['cardId']):
        raise TextJobError('Неверный идентификатор карточки')
    payload = _json_copy(data, limit=1_000_000)
    payload.setdefault('part', 'all')
    payload.pop('requestId', None)
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return payload, request_id, fingerprint


class TextGenerationJobs:
    def __init__(self, directory, *, history=None, limit=32, ttl=900,
                 clock=time.monotonic, wall_clock=time.time):
        self.directory = Path(directory)
        self.history = history if history is not None else DurationHistory(self.directory)
        self.limit, self.ttl = max(1, min(128, int(limit))), max(1, min(3600, float(ttl)))
        self.clock, self.wall_clock = clock, wall_clock
        self.lock = threading.RLock()
        self.jobs, self.requests, self.workers = {}, {}, {}
        self.closed = False

    def _cleanup(self):
        expired = [key for key, job in self.jobs.items()
            if job['state'] != 'running' and self.clock() - job['finished'] >= self.ttl]
        for key in expired:
            self._remove(key)

    def _remove(self, key):
        self.jobs.pop(key, None)
        for request, entry in list(self.requests.items()):
            if entry[0] == key:
                self.requests.pop(request, None)

    def _owned(self, job_id, principal):
        self._cleanup()
        job = self.jobs.get(job_id)
        if job is None or job['principal'] != principal:
            raise TextJobError(UNAVAILABLE, status=404, code='TEXT_JOB_UNAVAILABLE')
        return job

    def _snapshot(self, job):
        result = {key: job[key] for key in ('id', 'action', 'state', 'message', 'resultType', 'createdAt', 'updatedAt')}
        result.update(kind='text', canCancel=job['state'] == 'running' and not job['committing'] and not job['cancel'].is_set(),
            cancelRequested=job['cancel'].is_set(), progress=job['progress'].snapshot())
        if job.get('code'):
            result['code'] = job['code']
        if job['state'] == 'done':
            result['result'] = copy.deepcopy(job['result'])
        return result

    def status(self, job_id, principal):
        with self.lock:
            return self._snapshot(self._owned(job_id, principal))

    def running(self):
        with self.lock:
            return any(job['state'] == 'running' for job in self.jobs.values())

    def start(self, data, *, principal, authorize, actor='Mac owner', allowed_actions=('preview', 'rewrite')):
        payload, request_id, fingerprint = _request(data, allowed_actions)
        if not isinstance(principal, str) or not principal or len(principal) > 200:
            raise TextJobError('Недоступный создатель задания', status=403, code='TEXT_JOB_FORBIDDEN')
        authorize()
        with self.lock:
            self._cleanup()
            if self.closed:
                raise TextJobError('Сервер завершает работу. Повторите запрос после запуска.', status=503, code='TEXT_JOB_CLOSED')
            previous = self.requests.get((principal, request_id)) if request_id else None
            if previous:
                if previous[1] != fingerprint:
                    raise TextJobError('Этот запрос уже использован с другими данными. Запустите новую генерацию.',
                        status=409, code='TEXT_JOB_REQUEST_CONFLICT')
                job = self._owned(previous[0], principal)
                return {'job_id': job['id'], 'state': job['state'], 'replayed': True}
            if self.running():
                raise TextJobError('Дождитесь завершения текущей генерации текста или отмените её на запустившем устройстве.',
                    status=409, code='TEXT_JOB_BUSY')
            while len(self.jobs) >= self.limit:
                oldest = min((job for job in self.jobs.values() if job['state'] != 'running'),
                    key=lambda job: job['finished'], default=None)
                if oldest is None:
                    raise TextJobError('Очередь генерации занята', status=409, code='TEXT_JOB_BUSY')
                self._remove(oldest['id'])
            job_id, now = secrets.token_hex(16), self.wall_clock()
            progress = JobProgress(self.history, 'text:individual', clock=self.clock, wall_clock=self.wall_clock)
            progress.update(_PHASE_MESSAGES['preparing'], stage='generate')
            job = {'id': job_id, 'principal': principal, 'action': payload['action'], 'state': 'running',
                'resultType': 'draft' if payload['action'] == 'preview' else 'card',
                'message': _PHASE_MESSAGES['preparing'], 'progress': progress, 'cancel': threading.Event(),
                'committing': False, 'createdAt': now, 'updatedAt': now}
            self.jobs[job_id] = job
            if request_id:
                self.requests[(principal, request_id)] = job_id, fingerprint
            worker = threading.Thread(target=self._run, args=(job, payload, authorize, actor),
                daemon=True, name='redcn-text-'+job_id[:8])
            self.workers[job_id] = worker
            try:
                worker.start()
            except Exception:
                self.workers.pop(job_id, None)
                self._remove(job_id)
                raise TextJobError('Не удалось запустить генерацию. Повторите запрос.', status=503, code='TEXT_JOB_START_FAILED') from None
            return {'job_id': job_id, 'state': 'running', 'replayed': False}

    def _run(self, job, payload, authorize, actor):
        def report(_message='', **fields):
            ai = sanitize_ai(fields.get('ai'))
            message = _PHASE_MESSAGES.get(ai.get('phase'), _PHASE_MESSAGES['preparing'])
            with self.lock:
                if job['state'] == 'running' and not job['cancel'].is_set():
                    job.update(message=message, updatedAt=self.wall_clock())
                    job['progress'].update(message, stage='generate', ai=ai)

        def before_save():
            with self.lock:
                if self.closed or job['cancel'].is_set():
                    raise _Cancelled()
                authorize()
                # From here cancel must not promise that a committed save was undone.
                job.update(committing=True, message='Сохранение проверенного текста', updatedAt=self.wall_clock())
                job['progress'].update(job['message'], stage='save')

        try:
            authorize()
            if job['cancel'].is_set():
                raise _Cancelled()
            if payload['action'] == 'preview':
                from individual_copy import generate_preview
                result = generate_preview(self.directory, payload, status=report, cancel=job['cancel'])
                if not isinstance(result, dict):
                    raise ValueError()
                result = {key: value for key, value in result.items() if key in _DRAFT_KEYS}
                if 'ai' in result:
                    result['ai'] = sanitize_ai(result['ai'])
            else:
                from language_service import rewrite
                result = rewrite(self.directory, payload['cardId'], {'part': payload['part'], 'mode': 'individual'},
                    actor=actor, status=report, cancel=job['cancel'], before_save=before_save)
            result = _json_copy(result, limit=3_000_000)
            with self.lock:
                if job['cancel'].is_set() and not job['committing']:
                    raise _Cancelled()
                message = ('Карточка сохранена' if job['resultType'] == 'card' else
                    'Индивидуальный черновик готов к проверке' if result.get('status') == 'review' else
                    'Ответ модели не принят; текущий текст сохранён')
                job['progress'].update(message)
                accepted = job['resultType'] == 'card' or result.get('status') == 'review'
                job['progress'].finish(True, learn=accepted)
                job.update(state='done', result=result, message=message,
                    updatedAt=self.wall_clock(), finished=self.clock())
        except Exception as error:
            from language_service import LanguageConflict, LanguageDraftRejected, MissingCard
            cancelled = isinstance(error, _Cancelled) or job['cancel'].is_set() and not job['committing']
            if cancelled:
                message, code = 'Генерация отменена; карточка не изменена', 'TEXT_JOB_CANCELLED'
            elif isinstance(error, LanguageConflict):
                message, code = 'Карточка изменилась во время генерации. Обновите её и повторите действие.', 'TEXT_JOB_CARD_CHANGED'
            elif isinstance(error, MissingCard):
                message, code = 'Карточка недоступна. Обновите список объявлений.', 'TEXT_JOB_CARD_UNAVAILABLE'
            elif isinstance(error, LanguageDraftRejected):
                message, code = error.public_message, 'TEXT_JOB_REJECTED'
            elif isinstance(error, PermissionError):
                message, code = 'Доступ устройства или рабочего пространства изменился; генерация остановлена.', 'TEXT_JOB_FORBIDDEN'
            else:
                message, code = 'Не удалось принять новый текст. Откройте карточку и повторите генерацию.', 'TEXT_JOB_FAILED'
            with self.lock:
                measured = sanitize_ai(job['progress'].snapshot().get('ai'))
                job['progress'].update(message, ai={**measured, 'operation': 'text', 'phase': 'cancelled' if cancelled else 'error'})
                job['progress'].finish(False, learn=False)
                job.update(state='cancelled' if cancelled else 'error', code=code, message=message,
                    updatedAt=self.wall_clock(), finished=self.clock())
        finally:
            with self.lock:
                self.workers.pop(job['id'], None)

    def cancel(self, job_id, principal):
        with self.lock:
            job = self._owned(job_id, principal)
            if job['state'] == 'running' and not job['committing']:
                job['cancel'].set()
                job.update(message='Отмена генерации; ожидание остановки модели', updatedAt=self.wall_clock())
                job['progress'].update(job['message'])
            return self._snapshot(job)

    def close(self):
        with self.lock:
            self.closed = True
            for job in self.jobs.values():
                if job['state'] == 'running' and not job['committing']:
                    job['cancel'].set()
            workers = list(self.workers.values())
        deadline = time.monotonic()+2
        for worker in workers:
            if worker is not threading.current_thread():
                worker.join(max(0, deadline-time.monotonic()))


def dispatch(manager, path, data=None, *, principal, authorize, actor='Mac owner', allowed_actions=('preview',)):
    if not (path == 'text-jobs' or path.startswith('text-jobs/')):
        return None
    try:
        if path == 'text-jobs' and data is not None:
            return 202, manager.start(data, principal=principal, authorize=authorize, actor=actor, allowed_actions=allowed_actions)
        match = re.fullmatch(r'text-jobs/([a-f0-9]{32})(/cancel)?', path)
        if match:
            authorize()
            if match[2] and data is not None:
                if data:
                    raise TextJobError('Отмена задания не изменяет его данные')
                return 200, manager.cancel(match[1], principal)
            if not match[2] and data is None:
                return 200, manager.status(match[1], principal)
        return 404, {'error': UNAVAILABLE, 'code': 'TEXT_JOB_UNAVAILABLE'}
    except TextJobError as error:
        return error.status, {'error': str(error), 'code': error.code}
    except PermissionError:
        return 403, {'error': 'Доступ устройства изменился. Обновите приложение.', 'code': 'TEXT_JOB_FORBIDDEN'}
