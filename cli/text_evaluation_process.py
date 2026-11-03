"""Durable answer journals and capabilities for isolated native evaluations.

No journal grants model selection. A crashed or incomplete worker's answers
remain available for diagnosis but are excluded from paired acceptance.
"""
import fcntl
import json
import math
import os
from pathlib import Path
import select
import stat
import threading
import time

from learning_scheduler import _write
from text_model_contract import content_sha


def atomic_record(path, value, *, new=False):
    path=Path(path)
    if new and (path.exists() or path.is_symlink()):
        raise ValueError('An evaluation record cannot overwrite an earlier attempt')
    # _write fsyncs the new private file before its atomic rename. This keeps
    # completed answers across a process crash; no power-loss claim is made.
    _write(path,value)


def read_record(path, *, maximum=200_000):
    path=Path(path)
    if path.is_symlink() or not path.is_file() or not 0<path.stat().st_size<=maximum:
        raise ValueError('Invalid bounded evaluation record')
    value=json.loads(path.read_text())
    if not isinstance(value,dict):raise ValueError('Evaluation record must be an object')
    return value


def journal_answer(folder, side, inputs_sha, index, row):
    if side not in ('current','candidate') or type(index) is not int or not 0<=index<1000:
        raise ValueError('Invalid evaluation answer identity')
    if not isinstance(row,dict) or not isinstance(row.get('id'),str):
        raise ValueError('Answer requires its exact probe ID')
    path=Path(folder)/'rows'/f'{index:05d}.json'
    path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    atomic_record(path,{'version':1,'modelSide':side,'inputsSha256':inputs_sha,
        'index':index,'rowSha256':content_sha(row),'row':row},new=True)


def inspect_side(folder, side, inputs_sha, expected_ids, return_code):
    """Recover valid ordered rows, admitting only a clean terminal worker."""
    folder=Path(folder); rows=[]; error=None; terminal=None
    try:
        paths=sorted((folder/'rows').glob('*.json'))
        if len(paths)>len(expected_ids):raise ValueError('extra-journal-answers')
        for index,path in enumerate(paths):
            if path.name!=f'{index:05d}.json':raise ValueError('missing-or-unordered-journal-answer')
            record=read_record(path)
            row=record.get('row')
            if (record.get('version')!=1 or record.get('modelSide')!=side
                    or record.get('inputsSha256')!=inputs_sha or type(record.get('index')) is not int
                    or record.get('index')!=index
                    or not isinstance(row,dict) or row.get('id')!=expected_ids[index]
                    or record.get('rowSha256')!=content_sha(row)):
                raise ValueError('journal-identity-or-hash-mismatch')
            rows.append(row)
        if (folder/'terminal.json').exists():
            terminal=read_record(folder/'terminal.json')
            if (terminal.get('version')!=1 or terminal.get('modelSide')!=side
                    or terminal.get('inputsSha256')!=inputs_sha
                    or terminal.get('expectedIdsSha256')!=content_sha(expected_ids)
                    or type(terminal.get('rowCount')) is not int or terminal.get('rowCount')!=len(rows)
                    or terminal.get('rowsSha256')!=content_sha(rows)):
                raise ValueError('terminal-identity-or-hash-mismatch')
    except (OSError,ValueError,TypeError) as failure:
        error=str(failure) if isinstance(failure,ValueError) else type(failure).__name__
    admitted=(type(return_code) is int and return_code==0 and error is None and len(rows)==len(expected_ids)
              and terminal is not None and terminal.get('complete') is True
              and terminal.get('inputsStillFresh') is True)
    return {'admitted':admitted,'rows':rows,'journalError':error,
            'terminal':terminal,'journalRows':len(rows),'returnCode':return_code,
            'signal':-return_code if type(return_code) is int and return_code<0 else None,
            'rawAnswersRecovered':bool(rows),'fallbackCredited':False}


def validate_inherited_lease(directory, descriptor):
    """Require the already-held open-file-description, not merely a lock path."""
    if type(descriptor) is not int or descriptor<3:raise ValueError('An inherited resource FD is required')
    path=Path(directory)/'learning/model-resource.lock'
    if path.is_symlink():raise ValueError('Resource lock cannot be a symlink')
    owned=os.fstat(descriptor); named=path.stat()
    if not stat.S_ISREG(owned.st_mode) or (owned.st_dev,owned.st_ino)!=(named.st_dev,named.st_ino):
        raise ValueError('Inherited FD does not identify this resource lease')
    independent=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
    try:
        check=os.fstat(independent)
        if (check.st_dev,check.st_ino)!=(owned.st_dev,owned.st_ino):raise ValueError('Resource lease path changed')
        try:fcntl.flock(independent,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:pass
        else:raise ValueError('Resource lease was not already held by the parent')
    finally:os.close(independent)
    try:fcntl.flock(descriptor,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:raise ValueError('Inherited FD is not the existing resource owner')


class WorkerLifetime:
    """A parent's pipe and deadline bound an orphaned worker, including load."""
    def __init__(self, descriptor, deadline, folder, grace=3):
        if type(descriptor) is not int or descriptor<3 or not stat.S_ISFIFO(os.fstat(descriptor).st_mode):
            raise ValueError('An inherited parent-lifetime pipe is required')
        if type(deadline) not in (int,float) or not math.isfinite(deadline) or not time.monotonic()-5<deadline<=time.monotonic()+1801:
            raise ValueError('Invalid bounded native worker deadline')
        self.descriptor=descriptor;self.deadline=deadline;self.folder=Path(folder)
        self.grace=grace;self.reason=None;self.closed=threading.Event();self.thread=None

    def stop_reason(self):
        if self.reason:return self.reason
        if time.monotonic()>=self.deadline:self.reason='time-limit'
        elif select.select([self.descriptor],[],[],0)[0]:
            if os.read(self.descriptor,1)==b'':self.reason='parent-disconnected'
            else:self.reason='invalid-parent-pipe-message'
        return self.reason

    def _watch(self):
        since=None
        while not self.closed.wait(.1):
            reason=self.stop_reason()
            if not reason:continue
            if since is None:
                since=time.monotonic()
                try:atomic_record(self.folder/'lifetime-stop.json',{'version':1,'reason':reason,'at':time.time()})
                except (OSError,ValueError):pass
            elif time.monotonic()-since>=self.grace:
                # This process is the owned worker itself. Never target a PID
                # recovered from a journal or any unrelated application.
                os._exit(75)

    def __enter__(self):
        self.thread=threading.Thread(target=self._watch,name='redcn-text-worker-lifetime',daemon=True)
        self.thread.start();return self

    def __exit__(self,*unused):
        self.closed.set()
        if self.thread:self.thread.join(timeout=1)
