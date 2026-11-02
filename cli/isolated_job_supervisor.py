"""Cancel only explicitly owned test/training children and release their lease.

The watcher covers blocking phases as well as polling loops. It never scans
other processes or changes production settings. Signals and foreground work
request cooperative cancellation first; owned process groups are bounded.
"""
import math
import os
import signal
import subprocess
import threading
import time


class IsolatedJobInterrupted(RuntimeError):
    def __init__(self,reason):
        self.reason=reason
        super().__init__('Isolated operation interrupted: '+reason)


class IsolatedJobSupervisor:
    def __init__(self,*,busy=None,seconds,grace_seconds=5):
        if type(seconds) not in (int,float) or not math.isfinite(seconds) or seconds<=0:
            raise ValueError('A positive real deadline is required')
        if type(grace_seconds) not in (int,float) or not 0<=grace_seconds<=10:
            raise ValueError('A bounded cleanup grace is required')
        self.busy=busy or (lambda:False)
        self.seconds=seconds;self.grace_seconds=grace_seconds
        self.started=time.monotonic();self.deadline=self.started+seconds
        self.cancelled=threading.Event();self.reason=None
        self._closed=threading.Event();self._lock=threading.RLock()
        self._processes={};self._signals={};self._thread=None

    def cancel(self,reason):
        with self._lock:
            if not self.cancelled.is_set():
                self.reason=reason;self.cancelled.set()

    def _poll_reason(self):
        if self.cancelled.is_set():return
        if time.monotonic()>=self.deadline:self.cancel('supervisor-deadline');return
        try:busy=self.busy()
        except Exception:self.cancel('foreground-state-unavailable');return
        if busy:self.cancel('foreground-request')

    def check(self):
        self._poll_reason()
        if self.cancelled.is_set():raise IsolatedJobInterrupted(self.reason)

    def _signal(self,process,number):
        if process.poll() is not None:return
        try:
            # spawn() always creates a fresh session. Never signal a group
            # obtained from a PID file or an unrelated system process.
            if os.getpgid(process.pid)!=process.pid:raise RuntimeError('Owned process group changed')
            os.killpg(process.pid,number)
        except ProcessLookupError:pass

    def _sweep(self):
        now=time.monotonic()
        with self._lock:
            for pid,row in list(self._processes.items()):
                process=row['process']
                if process.poll() is not None:
                    self._processes.pop(pid,None);continue
                if not self.cancelled.is_set():continue
                if row['terminatedAt'] is None:
                    self._signal(process,signal.SIGTERM);row['terminatedAt']=now
                elif now-row['terminatedAt']>=self.grace_seconds:self._signal(process,signal.SIGKILL)

    def _watch(self):
        while not self._closed.is_set():
            self._poll_reason();self._sweep();self._closed.wait(.1)

    def __enter__(self):
        if threading.current_thread() is threading.main_thread():
            for number in (signal.SIGINT,signal.SIGTERM):
                self._signals[number]=signal.getsignal(number)
                signal.signal(number,lambda number,_frame:self.cancel('signal-'+str(number)))
        self._thread=threading.Thread(target=self._watch,name='redcn-isolated-supervisor',daemon=True)
        self._thread.start()
        return self

    def spawn(self,command,**kwargs):
        self.check()
        if kwargs.get('shell'):raise ValueError('Owned commands must use an argument array')
        if not isinstance(command,(list,tuple)) or not command:raise ValueError('Expected an argument array')
        kwargs['start_new_session']=True
        with self._lock:
            self.check()
            process=subprocess.Popen(command,**kwargs)
            self._processes[process.pid]={'process':process,'terminatedAt':None}
        return process

    def wait(self,process,*,timeout=None,poll_hook=None):
        finish=self.deadline if timeout is None else min(self.deadline,time.monotonic()+timeout)
        while process.poll() is None:
            if time.monotonic()>=finish:self.cancel('phase-deadline')
            self.check()
            if poll_hook is not None:poll_hook(process)
            self.cancelled.wait(.1)
        self.check()
        return process.returncode

    def stop_owned(self,process):
        if process is None or process.poll() is not None:return
        with self._lock:
            if self._processes.get(process.pid,{}).get('process') is not process:
                if process.poll() is not None:return
                raise ValueError('This process is not owned by the supervisor')
        self._signal(process,signal.SIGTERM)
        try:process.wait(timeout=self.grace_seconds)
        except subprocess.TimeoutExpired:
            self._signal(process,signal.SIGKILL);process.wait(timeout=3)

    def receipt(self):
        return {'actualDurationSeconds':time.monotonic()-self.started,'maximumSeconds':self.seconds,
                'interrupted':self.cancelled.is_set(),'reason':self.reason,
                'cleanupGraceSeconds':self.grace_seconds}

    def __exit__(self,kind,value,trace):
        self._closed.set()
        if self._thread:self._thread.join(timeout=4)
        try:
            with self._lock:processes=[row['process'] for row in self._processes.values()]
            if any(process.poll() is None for process in processes):
                self.cancel('owner-exception' if kind else 'owner-cleanup')
                for process in processes:self._signal(process,signal.SIGTERM)
                finish=time.monotonic()+self.grace_seconds
                while any(process.poll() is None for process in processes) and time.monotonic()<finish:time.sleep(.05)
                for process in processes:
                    if process.poll() is None:self._signal(process,signal.SIGKILL)
                for process in processes:
                    if process.poll() is None:process.wait(timeout=3)
        finally:
            for number,handler in self._signals.items():signal.signal(number,handler)
        if kind is None:self.check()
        return False
