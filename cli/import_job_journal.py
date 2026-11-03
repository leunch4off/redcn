"""Durable import identity without automatic reapplication of interrupted work.

A per-job flock proves an owner is still running. A stored ``running`` value or
PID alone does not. Recovery never edits a card or treats a partial checkpoint
as a finished import; an existing card is offered for review/photo preparation.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time

JOB_ID=re.compile(r'[a-f0-9]{32}')
REQUEST_ID=re.compile(r'[A-Za-z0-9_-]{12,80}')
STAGES=frozenset(('preflight','browser','navigate','challenge','scroll','extract','generate','photos','crop','vision','save'))
FINAL=frozenset(('done','error','cancelled'))
MAX_JOBS=10_000


class ImportRequestConflict(ValueError):
    code='IMPORT_REQUEST_CONFLICT'


def _identifier(value):
    if not isinstance(value,str) or not JOB_ID.fullmatch(value):raise ValueError('Неверный ID задания')
    return value


def _canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


class ImportJobJournal:
    def __init__(self,directory):
        self.root=Path(directory).absolute();self.folder=self.root/'import-jobs';self.path=self.folder/'jobs.sqlite3'
        if self.root.is_symlink() or self.folder.is_symlink() or self.path.is_symlink():raise ValueError('Каталог журнала импорта недоступен')
        self.folder.mkdir(mode=0o700,parents=True,exist_ok=True)
        self.leases=self.folder/'leases'
        if self.leases.is_symlink():raise ValueError('Каталог блокировок недоступен')
        self.leases.mkdir(mode=0o700,exist_ok=True)
        self._held={};self._lock=threading.RLock()
        with self._db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, request_key TEXT UNIQUE, fingerprint TEXT NOT NULL,
                kind TEXT NOT NULL, state TEXT NOT NULL, stage TEXT NOT NULL,
                result_id TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL,
                partial INTEGER NOT NULL DEFAULT 0)''')
        self.path.chmod(0o600)

    @contextmanager
    def _db(self):
        if self.path.is_symlink():raise ValueError('Журнал импорта недоступен')
        db=sqlite3.connect(self.path,timeout=10)
        db.row_factory=sqlite3.Row
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback();raise
        finally:db.close()

    def _lease(self,job_id):
        path=self.leases/(_identifier(job_id)+'.lock')
        descriptor=os.open(path,os.O_CREAT|os.O_RDWR|getattr(os,'O_NOFOLLOW',0),0o600)
        try:fcntl.flock(descriptor,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:os.close(descriptor);return None
        except BaseException:os.close(descriptor);raise
        return descriptor

    @staticmethod
    def _release(descriptor):
        try:fcntl.flock(descriptor,fcntl.LOCK_UN)
        finally:os.close(descriptor)

    def _card_exists(self,card_id):
        path=self.root/'listings.sqlite3'
        if not path.is_file() or path.is_symlink():return False
        db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=2)
        try:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='results'").fetchone():return False
            return db.execute('SELECT 1 FROM results WHERE id=?',(card_id,)).fetchone() is not None
        finally:db.close()

    def recover(self):
        """Mark only jobs whose OS lease was released by their prior owner."""
        with self._lock,self._db() as db:
            rows=db.execute("SELECT id FROM jobs WHERE state='running' ORDER BY created LIMIT ?",(MAX_JOBS,)).fetchall()
        recovered=[]
        for row in rows:
            with self._lock:
                if row['id'] in self._held:continue
                descriptor=self._lease(row['id'])
                if descriptor is None:continue
                try:
                    with self._db() as db:
                        changed=db.execute("UPDATE jobs SET state='interrupted',updated=? WHERE id=? AND state='running'",(time.time(),row['id'])).rowcount
                    if changed:recovered.append(row['id'])
                finally:self._release(descriptor)
        return recovered

    def reserve(self,params,*,actor,kind='import',request_id=None,result_id=None,allow_new=True,identity_params=None):
        if kind not in {'import','prepare'}:raise ValueError('Неверный вид задания')
        if not isinstance(actor,str) or not actor.strip() or len(actor)>100:raise ValueError('Нужен автор запроса')
        if not isinstance(params,dict):raise ValueError('Неверные параметры импорта')
        if request_id is not None and (not isinstance(request_id,str) or not REQUEST_ID.fullmatch(request_id)):
            raise ValueError('Неверный идентификатор повторного запроса')
        if kind=='prepare' and (result_id is None or not self._card_exists(_identifier(result_id))):raise ValueError('Карточка подготовки не найдена')
        if kind=='import' and result_id is not None:raise ValueError('Новый импорт не перезаписывает существующую карточку')
        if identity_params is not None and not isinstance(identity_params,dict):raise ValueError('Неверные параметры исходного запроса')
        raw=_canonical({'kind':kind,'params':params if identity_params is None else identity_params,'resultId':result_id})
        if len(raw)>2_000_000:raise ValueError('Слишком большие параметры импорта')
        fingerprint=hashlib.sha256(raw).hexdigest()
        request_key=hashlib.sha256(_canonical({'actor':actor,'requestId':request_id})).hexdigest() if request_id else None
        self.recover()
        with self._lock,self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT * FROM jobs WHERE request_key=?',(request_key,)).fetchone() if request_key else None
            if old:
                if old['fingerprint']!=fingerprint:raise ImportRequestConflict('Этот идентификатор уже относится к другому запросу. Повторите исходный запрос или создайте новый.')
                return {'jobId':old['id'],'resultId':old['result_id'],'replayed':True,'state':old['state']}
            if not allow_new or db.execute("SELECT 1 FROM jobs WHERE state='running' LIMIT 1").fetchone():
                raise ValueError('Дождитесь текущего импорта или отмените его')
            if db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]>=MAX_JOBS:raise ValueError('Журнал импорта заполнен. Сохраните диагностический архив перед его обслуживанием.')
            job_id=secrets.token_hex(16);card_id=result_id or secrets.token_hex(16);now=time.time()
            descriptor=self._lease(job_id)
            if descriptor is None:raise ValueError('Не удалось занять задание импорта')
            try:
                db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?)',(job_id,request_key,fingerprint,kind,'running','preflight',card_id,now,now,0))
                db.commit();self._held[job_id]=descriptor
            except BaseException:self._release(descriptor);raise
        return {'jobId':job_id,'resultId':card_id,'replayed':False,'state':'running'}

    def progress(self,job_id,stage):
        _identifier(job_id)
        if not isinstance(stage,str) or stage not in STAGES:return False
        with self._lock:
            if job_id not in self._held:return False
            with self._db() as db:
                return bool(db.execute("UPDATE jobs SET stage=?,updated=? WHERE id=? AND state='running' AND stage<>?",(stage,time.time(),job_id,stage)).rowcount)

    def reserved_result_id(self,job_id):
        with self._db() as db:row=db.execute('SELECT result_id FROM jobs WHERE id=?',(_identifier(job_id),)).fetchone()
        if not row:raise ValueError('Задание не найдено')
        return row['result_id']

    def finish(self,job_id,state,*,partial=False):
        _identifier(job_id)
        if state not in FINAL or type(partial) is not bool:raise ValueError('Неверное завершение задания')
        with self._lock:
            descriptor=self._held.get(job_id)
            if descriptor is None:raise ValueError('Этот процесс не владеет заданием')
            if state=='done' and not self._card_exists(self.reserved_result_id(job_id)):
                raise ValueError('Нельзя завершить задание без сохранённой карточки')
            with self._db() as db:
                changed=db.execute("UPDATE jobs SET state=?,partial=?,updated=? WHERE id=? AND state='running'",(state,int(partial),time.time(),job_id)).rowcount
                if not changed:raise ValueError('Состояние задания уже изменилось')
            self._held.pop(job_id);self._release(descriptor)
        return self.status(job_id)

    def status(self,job_id):
        with self._db() as db:row=db.execute('SELECT * FROM jobs WHERE id=?',(_identifier(job_id),)).fetchone()
        if not row:return None
        present=self._card_exists(row['result_id']);state=row['state']
        action='open-card-and-prepare-photos' if present else 'repeat-import'
        messages={'running':'Импорт выполняется.','done':'Карточка сохранена.'+(' Часть подготовки требует проверки.' if row['partial'] else ''),
                  'cancelled':'Задание отменено.','error':'Обработка завершилась с ошибкой.','interrupted':'Сервер остановился до завершения задания.'}
        message=messages.get(state,'Состояние задания неизвестно.')
        if state in {'error','cancelled','interrupted'}:
            message+=' Откройте сохранённую карточку: ваши правки сохранены; подготовку фото можно повторить.' if present else ' Карточка ещё не создана. Можно повторить импорт.'
        return {'id':row['id'],'kind':row['kind'],'cardId':row['result_id'] if row['kind']=='prepare' else None,
            'state':state,'message':message,'result_id':row['result_id'] if present else None,
            'partial':bool(row['partial']),'stage':row['stage'],'updated':row['updated'],
            'recovery':{'available':state in {'error','cancelled','interrupted'},'action':action,
                        'automaticResume':False,'partialCheckpointIsComplete':False},
            'progress':{'active':state=='running','stages':[]}}

    def latest(self):
        with self._db() as db:row=db.execute('SELECT id FROM jobs ORDER BY created DESC,id DESC LIMIT 1').fetchone()
        return self.status(row['id']) if row else None

    def abandon(self,job_id):
        """Release a finished worker's lease when its durable update failed."""
        with self._lock:
            descriptor=self._held.pop(_identifier(job_id),None)
            if descriptor is not None:self._release(descriptor)

    def close(self):
        """Release owned leases only after this process has stopped its workers."""
        with self._lock:
            held=list(self._held.values());self._held.clear()
            for descriptor in held:self._release(descriptor)
