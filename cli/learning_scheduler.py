"""Bounded idle LoRA jobs and a shared, process-wide local-model resource gate.

This module never opens an application controller or repairs import state. It
reads only aggregate diagnostic activity and approved learning examples. A
finished adapter remains a candidate until a separate quality review accepts it.
"""
from contextlib import contextmanager
from collections import deque
import hashlib
import json
import os
from pathlib import Path
import platform
import sqlite3
import subprocess
import threading
import time
import uuid

from learning_memory import LearningMemory

DEFAULTS = {'enabled': False, 'idleSeconds': 120, 'checkSeconds': 60,
            'minNewExamples': 20, 'maxSteps': 20, 'maxSeconds': 600,
            'maxSequenceLength': 256}
_THREADS = {}
_THREADS_LOCK = threading.Lock()
ROOT = Path(__file__).resolve().parents[1]


class ModelBusy(ValueError):
    pass


def _folder(directory):
    folder = Path(directory)/'learning'
    if folder.is_symlink():
        raise ValueError('Каталог обучения не должен быть ссылкой')
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    return folder


def _read(path, default):
    if path.is_symlink():
        raise ValueError('Файл обучения не должен быть ссылкой')
    try:
        if path.stat().st_size > 8_000_000:
            raise ValueError('Состояние обучения слишком велико')
        return json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return default


def _write(path, value):
    if path.is_symlink():
        raise ValueError('Файл обучения не должен быть ссылкой')
    temporary = path.with_name('.'+path.name+'-'+uuid.uuid4().hex)
    try:
        with temporary.open('x', encoding='utf-8') as stream:
            temporary.chmod(0o600)
            json.dump(value, stream, ensure_ascii=False, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def load_config(directory):
    value = _read(_folder(directory)/'scheduler.json', {})
    if not isinstance(value, dict) or not set(value) <= set(DEFAULTS):
        raise ValueError('Неверные настройки фонового обучения')
    config = {**DEFAULTS, **value}
    if type(config['enabled']) is not bool:
        raise ValueError('Включение обучения задаётся переключателем')
    for key, low, high in [('idleSeconds',60,3600), ('checkSeconds',30,300),
            ('minNewExamples',20,1000), ('maxSteps',1,200),
            ('maxSeconds',30,600), ('maxSequenceLength',64,512)]:
        if type(config[key]) is not int or not low <= config[key] <= high:
            raise ValueError('Неверный предел обучения: '+key)
    return config


def save_config(directory, updates):
    if not isinstance(updates, dict) or not set(updates) <= set(DEFAULTS):
        raise ValueError('Неизвестные настройки обучения')
    previous = _folder(directory)/'scheduler.json'
    value = {**load_config(directory), **updates}
    # Validate before replacing the active configuration.
    if type(value['enabled']) is not bool:
        raise ValueError('Включение обучения задаётся переключателем')
    for key, low, high in [('idleSeconds',60,3600), ('checkSeconds',30,300),
            ('minNewExamples',20,1000), ('maxSteps',1,200),
            ('maxSeconds',30,600), ('maxSequenceLength',64,512)]:
        if type(value[key]) is not int or not low <= value[key] <= high:
            raise ValueError('Неверный предел обучения: '+key)
    _write(previous, value)
    if not value['enabled']:
        touch_foreground(directory)
    return value


def touch_foreground(directory):
    """Give imports and interactive inference priority over background training."""
    folder = _folder(directory)
    _write(folder/'foreground.json', {'at': time.time()})
    _write(folder/'training.cancel', {'at': time.time()})


@contextmanager
def model_resource(directory, kind='foreground', timeout=0):
    """An advisory lock shared by model subprocesses, never a persistent model.

    A foreground request asks the bounded trainer to stop before acquiring the
    resource. A zero timeout returns a clear busy result instead of queuing an
    unbounded request. The caller controls its own process deadline afterwards.
    """
    import fcntl
    if kind not in {'foreground', 'background'} or not 0 <= timeout <= 30:
        raise ValueError('Неверный режим локальной модели')
    folder = _folder(directory)
    if kind == 'foreground':
        touch_foreground(directory)
    path = folder/'model-resource.lock'
    if path.is_symlink():
        raise ValueError('Блокировка модели не должна быть ссылкой')
    descriptor = os.open(path, os.O_CREAT|os.O_RDWR|getattr(os, 'O_NOFOLLOW', 0), 0o600)
    finish = time.monotonic()+timeout
    try:
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX|fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= finish:
                    raise ModelBusy('Локальная модель занята. Обучение приостанавливается; повторите запрос через несколько секунд.')
                time.sleep(min(.05, max(0, finish-time.monotonic())))
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def active_imports(directory):
    """Read aggregate activity only; never instantiate ImportDiagnostics."""
    path = Path(directory)/'import-diagnostics/campaigns.sqlite3'
    if not path.exists():
        return 0
    if path.is_symlink():
        return None
    try:
        with sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=.2) as db:
            return db.execute('SELECT COUNT(*) FROM attempts WHERE finished IS NULL').fetchone()[0]
    except (sqlite3.Error, ValueError):
        return None


def _samples(memory):
    # Only dataset identities are read; payloads are left to the approved export.
    with sqlite3.connect(memory.path.as_uri()+'?mode=ro', uri=True, timeout=1) as db:
        return sorted({row[0] for row in db.execute("SELECT fingerprint FROM examples WHERE status='approved' AND kind<>'tag_stat'")})


def _training_rows(path, limit, budget):
    """Prefer recent reviewed examples while keeping each job's dataset bounded."""
    recent = deque(maxlen=limit)
    with Path(path).open(encoding='utf-8') as source:
        for line in source:
            row = json.loads(line)
            messages = row.get('messages', [])
            if not messages or any(not isinstance(item.get('content'),str) or len(item['content'])>6000 for item in messages):
                continue
            row['status'] = 'approved'
            size = len(json.dumps(row, ensure_ascii=False).encode('utf-8'))
            if size <= 32_000:
                recent.append((row, size))
    selected, used = [], 0
    for row, size in reversed(recent):
        if used+size > budget:
            continue
        selected.append(row); used += size
    return list(reversed(selected))


def status(directory):
    state = _read(_folder(directory)/'scheduler-state.json', {})
    if not isinstance(state, dict):
        state = {}
    return {**load_config(directory), **{key: value for key, value in state.items()
            if key not in {'sampleHashes'}}, 'automaticPromotion': False}


class IdleTrainer:
    def __init__(self, directory, *, busy=None, clock=time.time, popen=subprocess.Popen):
        self.directory = Path(directory).resolve()
        self.folder = _folder(self.directory)
        self.memory = LearningMemory(self.directory)
        self.busy = busy or (lambda: False)
        self.clock = clock
        self.popen = popen
        self.stopped = threading.Event()
        self.thread = None
        self._process = None
        self._process_lock = threading.Lock()
        self.started = clock()
        self.state = _read(self.folder/'scheduler-state.json', {})
        if not isinstance(self.state, dict):
            self.state = {}

    def _state(self, phase, message, **extra):
        if self.stopped.is_set():
            return {key:value for key,value in self.state.items() if key!='sampleHashes'}
        self.state.update(phase=phase, message=message, updatedAt=self.clock(), **extra)
        _write(self.folder/'scheduler-state.json', self.state)
        return status(self.directory)

    def _busy(self):
        try:
            # A diagnostic database cannot see imports after its ten attempts;
            # the owning server supplies its existing in-memory activity too.
            return bool(self.busy()) or active_imports(self.directory) != 0
        except Exception:
            return True

    def _idle(self, config):
        foreground = _read(self.folder/'foreground.json', {})
        last = foreground.get('at', self.started) if isinstance(foreground, dict) else self.started
        return type(last) in (int,float) and self.clock()-max(self.started, last) >= config['idleSeconds']

    def _runtime(self):
        base = self.directory/'tools/text-ai'
        python = base/'venv/bin/python'
        model = base/'model'
        script = ROOT/'scripts/train-local-text-model.py'
        if not python.is_file() or not (model/'config.json').is_file() or not script.is_file():
            return None
        prefix = ['/usr/bin/arch', '-arm64'] if platform.system() == 'Darwin' else []
        return [*prefix, str(python), str(script)], model

    def tick(self):
        if self.stopped.is_set():
            return {key:value for key,value in self.state.items() if key!='sampleHashes'}
        config = load_config(self.directory)
        if not config['enabled']:
            return self._state('disabled', 'Фоновое обучение выключено.')
        if self._busy() or not self._idle(config):
            return self._state('waiting-idle', 'Обучение ждёт свободный Mac и завершение работы с объявлениями.')
        summary = self.memory.status()
        if not summary['trainingEligible']:
            return self._state('collecting', 'Нужно не менее 20 проверенных примеров из пяти независимых групп.')
        samples = _samples(self.memory)
        fingerprint = hashlib.sha256('\n'.join(samples).encode()).hexdigest()
        if fingerprint == self.state.get('holdoutFingerprint'):
            return self._state('needs-holdout', 'Нужны новые независимые проверочные примеры.')
        previous = self.state.get('sampleHashes', [])
        new_count = len(set(samples)-set(previous))
        same = fingerprint == self.state.get('fingerprint')
        if same and self.state.get('lastOutcome') == 'exhausted':
            return self._state('failed', 'Повторные попытки остановлены после трёх ошибок. Нужна проверка обучения или новые примеры.')
        if same and self.state.get('lastOutcome') in {'unaccepted', 'complete'}:
            return self._state('ready-for-review', 'Обученный адаптер ожидает проверки. Нужны новые подтверждённые примеры.')
        if same and self.clock() < self.state.get('retryAfter', 0):
            return self._state('backoff', 'Предыдущая попытка сохранена; следующая проверка отложена.')
        if not same and new_count < config['minNewExamples']:
            return self._state('collecting', 'Новые подтверждённые примеры накапливаются.', newExamples=new_count)
        runtime = self._runtime()
        if runtime is None:
            return self._state('runtime-unavailable', 'Изолированная языковая модель ещё не установлена.')
        try:
            with model_resource(self.directory, kind='background', timeout=0):
                if self._busy() or not self._idle(config):
                    return self._state('waiting-idle', 'Появилась работа с объявлениями; обучение отложено.')
                return self._run(config, runtime, fingerprint, samples, same)
        except ModelBusy:
            return self._state('waiting-model', 'Локальная модель обрабатывает текущий запрос.')

    def _run(self, config, runtime, fingerprint, samples, same):
        command, model = runtime
        job_id = self.state.get('jobId') if same and self.state.get('lastOutcome') == 'paused' else None
        if job_id:
            prior = self.folder/'training-runs'/job_id/'candidate'
            if not all((prior/name).is_file() and not (prior/name).is_symlink()
                    for name in ('adapters.safetensors','optimizer.safetensors','initial.safetensors','trainreceipt.json')):
                job_id = None
        if job_id:
            dataset = self.folder/'training-runs'/job_id/'dataset.json'
            output = dataset.parent/'candidate'
        else:
            job_id = 'idle-'+uuid.uuid4().hex
            snapshot = self.memory.snapshot(job_id)
            manifest = snapshot['manifest']
            if manifest['trainExamples'] < 16 or manifest['validExamples'] < 4:
                return self._state('needs-holdout', 'Нужны независимые проверочные примеры: минимум 16 учебных и 4 проверочных.', holdoutFingerprint=fingerprint)
            run = self.folder/'training-runs'/job_id
            run.mkdir(mode=0o700, parents=True)
            dataset, output = run/'dataset.json', run/'candidate'
            train = _training_rows(snapshot['train'], 2000, 3_000_000)
            valid = _training_rows(snapshot['valid'], 500, 1_000_000)
            if len(train)<16 or len(valid)<4:
                return self._state('needs-holdout', 'Недостаточно коротких независимых примеров для ограниченного учебного прогона.', holdoutFingerprint=fingerprint)
            _write(dataset, {'version':1, 'splits': {'train':train, 'validation':valid},
                'provenance': {'kind': 'approved-local-memory', 'snapshot': snapshot['manifest']}})
        cancel = self.folder/'training.cancel'
        if cancel.is_symlink():
            raise ValueError('Маркер отмены не должен быть ссылкой')
        cancel.unlink(missing_ok=True)
        # A request may arrive while the snapshot is prepared. Never erase that
        # cancellation then start expensive work behind an interactive request.
        if self._busy() or not self._idle(config):
            return self._state('waiting-idle', 'Текущий запрос получил приоритет над обучением.')
        attempt = self.state.get('attempts', 0)+1 if same else 1
        args = [*command, '--model', str(model), '--dataset', str(dataset), '--output', str(output),
                '--steps', str(config['maxSteps']), '--max-seconds', str(config['maxSeconds']),
                '--max-seq-length', str(config['maxSequenceLength']), '--cancel-file', str(cancel)]
        adapter = output/'adapters.safetensors'
        if job_id == self.state.get('jobId') and adapter.is_file():
            args += ['--resume-adapter', str(adapter)]
        self._state('training', 'Mac обучает кандидат; новый адаптер автоматически не применяется.',
            jobId=job_id, fingerprint=fingerprint, sampleHashes=samples, attempts=attempt,
            lastOutcome='running', retryAfter=0)
        self.memory.save_checkpoint(job_id, {'state':'running', 'step':0, 'snapshot':str(dataset), 'model':str(model)})
        started = self.clock()
        with self._process_lock:
            if self.stopped.is_set():
                return self._state('paused', 'Обучение остановлено вместе с сервером.')
            process = self.popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, start_new_session=True)
            self._process = process
        interrupted = False
        try:
            while process.poll() is None:
                if self.stopped.is_set() or self._busy() or cancel.exists():
                    interrupted = True
                    _write(cancel, {'at':self.clock()})
                    try: process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.terminate()
                        try: process.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            process.kill(); process.wait(timeout=2)
                    break
                if self.clock()-started > config['maxSeconds']+15:
                    interrupted = True
                    _write(cancel, {'at':self.clock()})
                    process.terminate()
                    try: process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill(); process.wait(timeout=2)
                    break
                self.stopped.wait(1)
        finally:
            if process.poll() is None:
                process.kill(); process.wait(timeout=2)
            with self._process_lock:
                if self._process is process:
                    self._process = None
        if self.stopped.is_set():
            return {key:value for key,value in self.state.items() if key!='sampleHashes'}
        receipt = _read(output/'trainreceipt.json', {})
        if not isinstance(receipt, dict):
            receipt = {}
        interrupted = interrupted or receipt.get('stoppedEarly') is True
        adapter_sha = hashlib.sha256(adapter.read_bytes()).hexdigest() if adapter.is_file() and not adapter.is_symlink() and adapter.stat().st_size <= 8_000_000 else None
        complete = (not interrupted and process.returncode == 0
            and receipt.get('status') == 'trained-unaccepted'
            and receipt.get('trainableParameters') == 540672
            and adapter_sha is not None and receipt.get('adapterSha256') == adapter_sha)
        step = receipt.get('stepsCompleted', 0)
        step = step if type(step) is int and step >= 0 else 0
        outcome = 'unaccepted' if complete else 'paused' if interrupted else 'failed'
        if not complete and not interrupted and attempt >= 3:
            outcome = 'exhausted'
        self.memory.save_checkpoint(job_id, {'state': 'unaccepted' if complete else 'paused' if interrupted else 'failed',
            'step':step, 'snapshot':str(dataset), 'model':str(model),
            **({'adapter':str(adapter)} if adapter.is_file() else {}),
            'metrics': {'trainableParameters':540672 if complete else 0}})
        return self._state('ready-for-review' if complete else 'paused' if interrupted else 'failed',
            'Кандидат сохранён и ожидает проверки качества.' if complete else
            'Обучение приостановлено для текущей работы; контрольная точка сохранена.' if interrupted else
            'Фоновое обучение не завершилось; состояние сохранено для проверки.',
            lastOutcome=outcome, retryAfter=self.clock()+3600, stepsCompleted=step,
            trainableParameters=540672 if complete else 0, adapterReady=adapter.is_file())

    def run(self):
        try:
            while not self.stopped.is_set():
                try:
                    self.tick()
                    if self.stopped.is_set():
                        break
                    delay = load_config(self.directory)['checkSeconds']
                except Exception:
                    try:self._state('failed', 'Не удалось завершить фоновую проверку обучения; повтор отложен.')
                    except Exception:pass
                    delay = 60
                self.stopped.wait(delay)
        finally:
            key = str(self.directory)
            with _THREADS_LOCK:
                if _THREADS.get(key) is self:
                    del _THREADS[key]

    def stop(self):
        """Cancel our worker and join our thread before the caller removes cache.

        An import belongs to the existing server, so closing this scheduler
        never waits for an import or terminates its process. Only a trainer
        created by this instance can be terminated here.
        """
        self.stopped.set()
        if self.folder.is_dir() and not self.folder.is_symlink():
            _write(self.folder/'training.cancel', {'at':self.clock()})
        with self._process_lock:
            process = self._process
        if process is not None and process.poll() is None:
            process.terminate()
            try:process.wait(timeout=.5)
            except subprocess.TimeoutExpired:
                process.kill();process.wait(timeout=1)
        thread = self.thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=6)
            if thread.is_alive():
                raise RuntimeError('Фоновое обучение ещё завершает запись; повторите закрытие сервера.')
        if self.state.get('phase') == 'training' and self.folder.is_dir():
            self.state.update(phase='paused', lastOutcome='paused', retryAfter=0,
                message='Обучение приостановлено при закрытии сервера; сохранённая контрольная точка будет проверена.',
                updatedAt=self.clock())
            _write(self.folder/'scheduler-state.json', self.state)
        with _THREADS_LOCK:
            if _THREADS.get(str(self.directory)) is self:
                del _THREADS[str(self.directory)]


def start_background(directory, busy=None):
    """Call only from the existing server; return a stoppable scheduler instance."""
    key = str(Path(directory).resolve())
    with _THREADS_LOCK:
        existing = _THREADS.get(key)
        if existing and not existing.stopped.is_set():
            return existing
        scheduler = IdleTrainer(directory, busy=busy)
        thread = threading.Thread(target=scheduler.run, name='redcn-idle-learning', daemon=True)
        scheduler.thread = thread
        _THREADS[key] = scheduler
        thread.start()
        return scheduler
