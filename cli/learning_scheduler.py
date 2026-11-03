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
import re
import sqlite3
import subprocess
import threading
import time
import uuid

from learning_memory import LearningMemory, LANE_KINDS

DEFAULTS = {'enabled': False, 'idleSeconds': 120, 'checkSeconds': 60,
            'minNewExamples': 20, 'maxSteps': 20, 'maxSeconds': 600,
            'maxSequenceLength': 256, 'automaticPromotion': False,
            'evaluationSeconds': 900, 'advertisingMaxSequenceLength': 1536}
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
    if any(type(config[key]) is not bool for key in ('enabled', 'automaticPromotion')):
        raise ValueError('Включение обучения задаётся переключателем')
    for key, low, high in [('idleSeconds',60,3600), ('checkSeconds',30,300),
            ('minNewExamples',20,1000), ('maxSteps',1,200),
            ('maxSeconds',30,600), ('maxSequenceLength',64,512), ('evaluationSeconds',60,1800),
            ('advertisingMaxSequenceLength',512,1536)]:
        if type(config[key]) is not int or not low <= config[key] <= high:
            raise ValueError('Неверный предел обучения: '+key)
    return config


def save_config(directory, updates):
    if not isinstance(updates, dict) or not set(updates) <= set(DEFAULTS):
        raise ValueError('Неизвестные настройки обучения')
    previous = _folder(directory)/'scheduler.json'
    value = {**load_config(directory), **updates}
    # Validate before replacing the active configuration.
    if any(type(value[key]) is not bool for key in ('enabled', 'automaticPromotion')):
        raise ValueError('Включение обучения задаётся переключателем')
    for key, low, high in [('idleSeconds',60,3600), ('checkSeconds',30,300),
            ('minNewExamples',20,1000), ('maxSteps',1,200),
            ('maxSeconds',30,600), ('maxSequenceLength',64,512), ('evaluationSeconds',60,1800),
            ('advertisingMaxSequenceLength',512,1536)]:
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
        # A supervised child may inherit this descriptor. If the server dies,
        # its still-running training child continues to own the OS lease.
        yield descriptor
    finally:
        # A native child inherits this same open-file-description. Explicit
        # LOCK_UN would also release its lease while it is still running.
        # Closing only our reference keeps the lock until the final owner exits.
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


def valid_checkpoint(folder,dataset):
    """An interrupted multi-file save is preserved, but never resumed blindly."""
    try:
        receipt=_read(Path(folder)/'trainreceipt.json',{})
        if not isinstance(receipt,dict) or type(receipt.get('stepsCompleted')) is not int or receipt['stepsCompleted']<1:
            return False
        for name,key in (('adapters.safetensors','adapterSha256'),('optimizer.safetensors','optimizerSha256'),('initial.safetensors','initialSha256')):
            path=Path(folder)/name
            if path.is_symlink() or not path.is_file() or not 0<path.stat().st_size<=8_000_000 or hashlib.sha256(path.read_bytes()).hexdigest()!=receipt.get(key):
                return False
        path=Path(dataset)
        return (not path.is_symlink() and path.is_file() and 0<path.stat().st_size<=8_000_000
                and hashlib.sha256(path.read_bytes()).hexdigest()==receipt.get('datasetSha256'))
    except (OSError,ValueError,TypeError):
        return False


def training_lane(directory):
    """A configured separate writer is trained in its own lane, never 0.5B."""
    from local_text_model import settings
    return 'advertising' if settings(directory).get('advertisingModelPath') else 'translation'


def _state_path(folder, lane):
    return folder/('advertising-scheduler-state.json' if lane == 'advertising' else 'scheduler-state.json')


def _samples(memory, lane='translation'):
    # Only dataset identities are read; payloads are left to the approved export.
    with sqlite3.connect(memory.path.as_uri()+'?mode=ro', uri=True, timeout=1) as db:
        kinds = LANE_KINDS[lane]
        return sorted({row[0] for row in db.execute("SELECT fingerprint FROM examples WHERE status='approved' AND kind IN ("+
            ','.join('?' for _ in kinds)+')', kinds)})


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
    lane = training_lane(directory)
    state = _read(_state_path(_folder(directory), lane), {})
    if not isinstance(state, dict):
        state = {}
    if lane == 'advertising':
        from advertising_promotion import public_status
    else:
        from model_promotion import public_status
    config = load_config(directory)
    with _THREADS_LOCK:
        current = _THREADS.get(str(Path(directory).resolve()))
    worker_alive = bool(current and current.thread and current.thread.is_alive() and not current.stopped.is_set())
    process = current._process if current else None
    running = bool(process is not None and process.poll() is None)
    progress = None
    job_id = state.get('jobId')
    if running and isinstance(job_id, str) and re.fullmatch(r'idle-[a-f0-9]{32}', job_id):
        try:
            receipt = _read(_folder(directory)/'training-runs'/job_id/'candidate/trainreceipt.json', {})
        except (ValueError, OSError):
            receipt = {}
        if (isinstance(receipt, dict) and type(receipt.get('stepsCompleted')) is int
                and 0 <= receipt['stepsCompleted'] <= 2000):
            progress = {'stepsCompleted':receipt['stepsCompleted'], 'requestedSteps':config['maxSteps'],
                'source':'last-written-native-checkpoint', 'elapsedSeconds':receipt.get('elapsedSeconds'),
                'peakMemoryBytes':receipt.get('peakMemoryBytes')}
    memory = LearningMemory(directory).status(lane=lane)
    return {**{key: value for key, value in state.items() if key not in {'sampleHashes'}},
            **config, 'lane': lane, 'promotion': public_status(directory),
            'readiness': memory['lanes'][lane], 'styleOnlyExamples': memory['styleOnlyExamples'],
            'workerAlive': worker_alive, 'modelProcessRunning': running,
            'trainingProgress': progress,
            'recordedPhaseIsProcessProof': False,
            'approvedExamples': memory['approvedExamples'], 'approvedGroups': memory['approvedGroups'],
            'examplesStillNeeded': max(0, 20-memory['approvedExamples']),
            'independentGroupsStillNeeded': max(0, 5-memory['approvedGroups']),
            'nextCheckSeconds': config['checkSeconds'] if worker_alive else None}


class IdleTrainer:
    def __init__(self, directory, *, busy=None, clock=time.time, popen=subprocess.Popen, resource_sampler=None):
        self.directory = Path(directory).resolve()
        self.folder = _folder(self.directory)
        self.memory = LearningMemory(self.directory)
        self.busy = busy or (lambda: False)
        self.clock = clock
        self.popen = popen
        from training_resources import sample
        self.resource_sampler = resource_sampler or sample
        self.stopped = threading.Event()
        self.thread = None
        self._process = None
        self._process_lock = threading.Lock()
        self.started = clock()
        self.lane = training_lane(self.directory)
        self.state_path = _state_path(self.folder, self.lane)
        self.state = _read(self.state_path, {})
        if not isinstance(self.state, dict):
            self.state = {}
        if self.state.get('phase') in {'training','evaluating'}:
            self.state.update(phase='recovery-pending',updatedAt=self.clock(),
                message='Предыдущий процесс прерван. Проверяем блокировку и контрольную точку до возобновления.')
            _write(self.state_path,self.state)

    def _state(self, phase, message, **extra):
        if self.stopped.is_set():
            return {key:value for key,value in self.state.items() if key!='sampleHashes'}
        if phase=='training' and extra.get('jobId')!=self.state.get('jobId'):
            self.state.update(stepsCompleted=0,trainableParameters=0,adapterReady=False,
                resourceStopReason=None,resourceGuard=None,qualityPassed=None,qualityRequested=None,qualityMessage=None)
        self.state.update(phase=phase, lane=self.lane, message=message, updatedAt=self.clock(), **extra)
        _write(self.state_path, self.state)
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
        if self.lane == 'advertising':
            from local_text_model import settings
            from text_model_contract import training_contract
            profile = settings(self.directory)
            if not profile['enabled'] or not profile.get('advertisingModelPath'):
                return None
            python = Path(profile.get('advertisingPythonPath') or profile['pythonPath'])
            model = Path(profile['advertisingModelPath'])
            try:
                training_contract(_read(model/'config.json', {}), 'advertising')
            except (OSError, ValueError):
                return None
        script = ROOT/'scripts/train-local-text-model.py'
        if not python.is_file() or not (model/'config.json').is_file() or not script.is_file():
            return None
        prefix = ['/usr/bin/arch', '-arm64'] if platform.system() == 'Darwin' else []
        return [*prefix, str(python), str(script)], model

    def tick(self):
        if self.stopped.is_set():
            return {key:value for key,value in self.state.items() if key!='sampleHashes'}
        config = load_config(self.directory)
        lane = training_lane(self.directory)
        if lane != self.lane:
            self.lane, self.state_path = lane, _state_path(self.folder, lane)
            self.state = _read(self.state_path, {})
            if not isinstance(self.state, dict): self.state = {}
        if not config['enabled']:
            return self._state('disabled', 'Фоновое обучение выключено.')
        if self._busy() or not self._idle(config):
            return self._state('waiting-idle', 'Обучение ждёт свободный Mac и завершение работы с объявлениями.')
        # Imports are indexed as source text, never auto-approved labels. This
        # bounded sweep also catches old cards and saves interrupted before the
        # post-commit discovery hook. It does useful work without loading MLX.
        from learning_discovery import sweep_saved_cards
        discovery = sweep_saved_cards(self.directory)
        if discovery['indexed']:
            self.state['lastSourceDiscovery'] = {**discovery, 'at': self.clock()}
        if (config['automaticPromotion'] and self.state.get('lastOutcome') == 'unaccepted'
                and self.state.get('jobId') and self.state.get('qualityOutcome') not in {'accepted', 'blocked'}
                and self.clock() >= self.state.get('qualityRetryAfter', 0)):
            return self._evaluate(config)
        summary = self.memory.status(lane=self.lane)
        if not summary['trainingEligible']:
            return self._state('collecting',
                'Проверено примеров: '+str(summary['approvedExamples'])+'/20; независимых групп: '+
                str(summary['approvedGroups'])+'/5. '+('Для объявлений нужны полные тексты, проверенные по фактам каждой квартиры. Старые примеры стиля в этот счёт не входят.' if self.lane == 'advertising' else
                'Новые исходные фразы собраны отдельно; их перевод ещё нужно проверить.'),
                approvedExamples=summary['approvedExamples'], approvedGroups=summary['approvedGroups'])
        samples = _samples(self.memory, self.lane)
        fingerprint = hashlib.sha256('\n'.join(samples).encode()).hexdigest()
        if fingerprint == self.state.get('holdoutFingerprint'):
            return self._state('needs-holdout', 'Нужны новые независимые проверочные примеры.')
        previous = self.state.get('sampleHashes', [])
        new_count = len(set(samples)-set(previous))
        same = fingerprint == self.state.get('fingerprint')
        if same and self.state.get('lastOutcome') == 'exhausted':
            return self._state('failed', 'Повторные попытки остановлены после трёх ошибок. Нужна проверка обучения или новые примеры.')
        if same and self.state.get('lastOutcome') in {'unaccepted', 'complete'}:
            if self.state.get('qualityOutcome') == 'accepted':
                return self._state('accepted', 'Новые веса приняты после сравнения; ожидаем новые подтверждённые примеры.')
            if self.state.get('qualityOutcome') == 'blocked':
                return self._state('quality-blocked', self.state.get('qualityMessage', 'Проверка не разрешила замену весов; текущая модель сохранена.'))
            return self._state('ready-for-review', 'Обученный адаптер ожидает проверки. Нужны новые подтверждённые примеры.')
        if same and self.clock() < self.state.get('retryAfter', 0):
            return self._state('backoff', 'Предыдущая попытка сохранена; следующая проверка отложена.')
        if not same and new_count < config['minNewExamples']:
            return self._state('collecting', 'Новые подтверждённые примеры накапливаются.', newExamples=new_count)
        runtime = self._runtime()
        if runtime is None:
            return self._state('runtime-unavailable', 'Изолированная языковая модель ещё не установлена.')
        try:
            with model_resource(self.directory, kind='background', timeout=0) as resource_fd:
                if self._busy() or not self._idle(config):
                    return self._state('waiting-idle', 'Появилась работа с объявлениями; обучение отложено.')
                result = self._run(config, runtime, fingerprint, samples, same, resource_fd=resource_fd)
            # The evaluation process acquires the same model gate itself.
            # Never hold the parent's training lock while starting that child.
            if (config['automaticPromotion'] and self.state.get('lastOutcome') == 'unaccepted'
                    and not self.stopped.is_set()):
                return self._evaluate(config)
            return result
        except ModelBusy:
            return self._state('waiting-model', 'Локальная модель обрабатывает текущий запрос.')

    def _run(self, config, runtime, fingerprint, samples, same, *, resource_fd=None):
        command, model = runtime
        from training_resources import ResourceGuard
        limits = {'maxProcessGroupRssBytes': 4_500_000_000,
            'minimumAvailableEstimateBytes': 768*1024**2, 'maxSwapGrowthBytes': 512*1024**2} if self.lane == 'advertising' else None
        resources = ResourceGuard(self.resource_sampler, limits=limits)
        expected_parameters = 540672
        if self.lane == 'advertising':
            from text_model_contract import training_contract
            expected_parameters = training_contract(_read(model/'config.json', {}), self.lane)['trainableParameters']
        reason = resources.check(admission=True)
        if reason:
            return self._state('waiting-resources', 'Фоновое обучение ждёт достаточной памяти Mac.',
                               resourceStopReason=reason, resourceGuard=resources.receipt())
        job_id = self.state.get('jobId') if same and self.state.get('lastOutcome') in {'paused','running'} else None
        if job_id:
            prior = self.folder/'training-runs'/job_id/'candidate'
            if not valid_checkpoint(prior,prior.parent/'dataset.json'):
                job_id = None
        if job_id:
            dataset = self.folder/'training-runs'/job_id/'dataset.json'
            output = dataset.parent/'candidate'
        else:
            job_id = 'idle-'+uuid.uuid4().hex
            snapshot = self.memory.snapshot(job_id, lane=self.lane)
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
        if self.lane == 'advertising':
            from advertising_learning import validate_training_dataset
            validate_training_dataset(_read(dataset, {}))
        try:
            self.memory.register_dataset(dataset)
        except ValueError:
            return self._state('consent-withdrawn', 'Корпус содержит отозванный или изменённый пример. Подготовьте новую версию данных.',
                               lastOutcome='consent-withdrawn', adapterReady=False, qualityOutcome='blocked')
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
                '--max-seq-length', str(config['advertisingMaxSequenceLength'] if self.lane == 'advertising' else config['maxSequenceLength']), '--cancel-file', str(cancel),
                '--consent-cache', str(self.directory)]
        if self.lane == 'advertising':
            args += ['--profile', 'advertising', '--sampling', 'shuffled']
        adapter = output/'adapters.safetensors'
        if job_id == self.state.get('jobId') and adapter.is_file():
            args += ['--resume-adapter', str(adapter)]
        self._state('training', 'Mac обучает кандидат. Выбор новых весов возможен только после отдельного сравнения качества.',
            jobId=job_id, fingerprint=fingerprint, sampleHashes=samples, attempts=attempt,
            lastOutcome='running', retryAfter=0, qualityOutcome='pending', qualityRetryAfter=0)
        self.memory.save_checkpoint(job_id, {'state':'running', 'step':0, 'snapshot':str(dataset), 'model':str(model)})
        started = self.clock()
        with self._process_lock:
            if self.stopped.is_set():
                return self._state('paused', 'Обучение остановлено вместе с сервером.')
            log_path=output.parent/('worker-'+str(attempt)+'.log')
            if log_path.is_symlink(): raise ValueError('Журнал обучения не должен быть ссылкой')
            descriptor=os.open(log_path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
            with os.fdopen(descriptor,'wb') as log:
                process = self.popen(args, stdin=subprocess.DEVNULL, stdout=log,
                                     stderr=log, start_new_session=True,
                                     **({'pass_fds':(resource_fd,)} if resource_fd is not None else {}))
            self._process = process
        interrupted = False
        consent_withdrawn = False
        resource_reason = None
        consent_checked = self.clock()
        try:
            while process.poll() is None:
                if self.clock()-consent_checked >= 5:
                    consent_checked = self.clock()
                    resource_reason = resources.check(getattr(process,'pid',None))
                    _write(output.parent/'resource-receipt.json',resources.receipt())
                    try:
                        self.memory.assert_dataset_consent(dataset)
                    except ValueError:
                        consent_withdrawn = True
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
                if consent_withdrawn or resource_reason:
                    interrupted = True
                    _write(cancel, {'at':self.clock(), 'reason':'consent-withdrawn' if consent_withdrawn else resource_reason})
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
            _write(output.parent/'resource-receipt.json',resources.receipt())
        if self.stopped.is_set():
            return {key:value for key,value in self.state.items() if key!='sampleHashes'}
        receipt = _read(output/'trainreceipt.json', {})
        if not isinstance(receipt, dict):
            receipt = {}
        interrupted = interrupted or receipt.get('stoppedEarly') is True
        adapter_sha = hashlib.sha256(adapter.read_bytes()).hexdigest() if adapter.is_file() and not adapter.is_symlink() and adapter.stat().st_size <= 8_000_000 else None
        complete = (not interrupted and process.returncode == 0
            and receipt.get('status') == 'trained-unaccepted'
            and receipt.get('trainableParameters') == expected_parameters
            and (self.lane != 'advertising' or receipt.get('trainingLane') == 'advertising'
                 and type(receipt.get('changedParameterElements')) is int and receipt['changedParameterElements'] > 0)
            and type(receipt.get('stepsCompleted')) is int and receipt['stepsCompleted']==config['maxSteps']
            and receipt.get('stoppedEarly') is False and valid_checkpoint(output,dataset)
            and adapter_sha is not None and receipt.get('adapterSha256') == adapter_sha)
        if complete:
            try:
                self.memory.register_candidate(adapter_sha, dataset)
            except ValueError:
                complete = False
                consent_withdrawn = True
        step = receipt.get('stepsCompleted', 0)
        step = step if type(step) is int and step >= 0 else 0
        outcome = 'unaccepted' if complete else 'paused' if interrupted else 'failed'
        failure_reason=None
        if not complete and not interrupted:
            log_path=output.parent/('worker-'+str(attempt)+'.log')
            try:
                with log_path.open('rb') as log:
                    log.seek(max(0,log_path.stat().st_size-16_000)); diagnostic=log.read(16_000).decode(errors='replace')
                failure_reason=('complete-example-exceeds-token-limit' if 'complete examples above the token limit' in diagnostic else
                    'not-enough-complete-examples' if 'fewer than two complete examples' in diagnostic else
                    'native-memory-limit' if any(item in diagnostic.lower() for item in ('out of memory','memory limit','failed to allocate')) else
                    'native-worker-failed')
            except OSError: failure_reason='native-worker-failed'
        if not complete and not interrupted and attempt >= 3:
            outcome = 'exhausted'
        self.memory.save_checkpoint(job_id, {'state': 'unaccepted' if complete else 'paused' if interrupted else 'failed',
            'step':step, 'snapshot':str(dataset), 'model':str(model),
            **({'adapter':str(adapter)} if adapter.is_file() else {}),
            'metrics': {'trainableParameters':expected_parameters if complete else 0}})
        return self._state('consent-withdrawn' if consent_withdrawn else 'ready-for-review' if complete else 'paused' if interrupted else 'failed',
            'Согласие на учебный пример отозвано. Кандидат заблокирован; нужна новая версия данных и переобучение.' if consent_withdrawn else
            'Обучение приостановлено из-за давления на память. Следующая попытка проверит сохранённую контрольную точку.' if resource_reason else
            'Кандидат сохранён и ожидает проверки качества.' if complete else
            'Обучение приостановлено для текущей работы; контрольная точка сохранена.' if interrupted else
            'Полный текст примера превышает учебное окно. Ничего не обрезано: нужен явно проверенный корпус подходящей длины.' if failure_reason=='complete-example-exceeds-token-limit' else
            'Модели не хватило памяти в установленном бюджете; кандидат не принят.' if failure_reason=='native-memory-limit' else
            'Фоновое обучение не завершилось; частный журнал сохранён для проверки.',
            lastOutcome='consent-withdrawn' if consent_withdrawn else outcome, retryAfter=self.clock()+3600, stepsCompleted=step,
            trainableParameters=expected_parameters if complete else 0, adapterReady=not consent_withdrawn and (complete or valid_checkpoint(output,dataset)),
            failureReason=failure_reason, resourceStopReason=resource_reason,
            resourceGuard={key:value for key,value in resources.receipt().items() if key!='samples'})

    def _evaluate(self, config):
        """Evaluate saved weights offline; imports and foreground work preempt it."""
        if self._busy() or not self._idle(config) or not load_config(self.directory)['automaticPromotion']:
            return self._state('waiting-idle', 'Сравнение весов ждёт свободный Mac.')
        runtime = self._runtime()
        if runtime is None:
            return self._state('runtime-unavailable', 'Среда проверки модели пока недоступна.')
        job_id = self.state.get('jobId', '')
        if not isinstance(job_id, str) or not job_id.startswith('idle-') or not job_id[5:].isalnum():
            raise ValueError('Неверный идентификатор кандидата')
        run = self.folder/'training-runs'/job_id
        output = run/'candidate'
        report = run/('quality-'+uuid.uuid4().hex+'.json')
        evaluator = 'evaluate-advertising-adapter.py' if self.lane == 'advertising' else 'evaluate-text-candidate.py'
        command = runtime[0][:-1]+[str(ROOT/'scripts'/evaluator),
            '--cache', str(self.directory), '--candidate', str(output),
            '--training-dataset', str(run/'dataset.json'), '--output', str(report),
            '--max-seconds', str(config['evaluationSeconds'])]
        cancel = self.folder/'training.cancel'
        if cancel.is_symlink():
            raise ValueError('Маркер отмены не должен быть ссылкой')
        cancel.unlink(missing_ok=True)
        if self._busy() or not self._idle(config):
            return self._state('waiting-idle', 'Текущий запрос получил приоритет над проверкой весов.')
        self._state('evaluating', 'Mac сравнивает текущие и новые веса на независимых объявлениях.', qualityOutcome='running')
        started, interrupted = self.clock(), False
        with self._process_lock:
            if self.stopped.is_set():
                return self._state('quality-paused', 'Проверка остановлена вместе с сервером.')
            process = self.popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, start_new_session=True)
            self._process = process
        try:
            while process.poll() is None:
                if (self.stopped.is_set() or self._busy() or not self._idle(config) or cancel.exists()
                        or self.clock()-started > config['evaluationSeconds']+15):
                    interrupted = True
                    _write(cancel, {'at': self.clock()})
                    process.terminate()
                    try: process.wait(timeout=5)
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
        if interrupted or self._busy() or not self._idle(config):
            return self._state('quality-paused', 'Сравнение приостановлено для текущего запроса; выбранные веса сохранены.',
                qualityOutcome='paused', qualityRetryAfter=self.clock()+300)
        if process.returncode or not report.is_file():
            return self._state('quality-blocked', 'Сравнение не завершилось. Текущие веса сохранены; нужна проверка кандидата.',
                qualityOutcome='blocked', qualityMessage='Сравнение не завершилось; текущие веса сохранены.')
        try:
            if self.lane == 'advertising':
                from advertising_promotion import promote
            else:
                from model_promotion import promote
            decision = promote(self.directory, report, automatic=True)
        except ModelBusy:
            return self._state('quality-paused', 'Локальная модель занята; приёмка весов отложена.',
                qualityOutcome='paused', qualityRetryAfter=self.clock()+300)
        except (ValueError, OSError):
            return self._state('quality-blocked', 'Настройки или артефакты изменились после проверки; текущие веса сохранены.',
                qualityOutcome='blocked', qualityMessage='Приёмка остановлена: нужна новая независимая проверка.')
        accepted = decision.get('accepted') is True
        return self._state('accepted' if accepted else 'quality-blocked', decision['message'],
            qualityOutcome='accepted' if accepted else 'blocked', qualityMessage=decision['message'],
            qualityPassed=decision.get('candidatePassed'), qualityRequested=decision.get('requested'))

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
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill();process.wait(timeout=2)
        thread = self.thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=6)
            if thread.is_alive():
                raise RuntimeError('Фоновое обучение ещё завершает запись; повторите закрытие сервера.')
        if self.state.get('phase') in {'training', 'evaluating'} and self.folder.is_dir():
            evaluating = self.state.get('phase') == 'evaluating'
            self.state.update(phase='paused', lastOutcome='paused', retryAfter=0,
                message='Обучение приостановлено при закрытии сервера; сохранённая контрольная точка будет проверена.',
                updatedAt=self.clock())
            if evaluating:
                self.state.update(phase='quality-paused', lastOutcome='unaccepted', qualityOutcome='paused',
                    qualityRetryAfter=0, message='Проверка новых весов приостановлена вместе с сервером.')
            _write(self.state_path, self.state)
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
