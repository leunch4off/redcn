"""Bounded real-time mixed-load runner for an explicitly isolated workspace.

Commands are supplied by the operator as argument arrays, never a shell string.
The runner measures wall/monotonic time; a short run can never satisfy the two
hour criterion. Model phases hold the production model-resource lock and yield
to a newer foreground marker. A stopped worker is never reported as successful.
Physical Mac sleep and an iPhone are separate observations, not inferred here.
"""
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import time

KINDS = ('import', 'editor', 'photo', 'training')
MAX_LOG_BYTES = 2_000_000
MAX_RECEIPT_BYTES = 1_000_000


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def atomic(path, value):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('Report path cannot be a link')
    raw = canonical(value).encode()
    if len(raw) > MAX_RECEIPT_BYTES:
        raise ValueError('Report limit exceeded')
    temporary = path.with_name('.'+path.name+'.tmp')
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def read_object(path, limit=MAX_RECEIPT_BYTES):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= limit:
        raise ValueError('Expected a bounded regular JSON file')
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError('Expected a JSON object')
    return value


def validate_recipe(value, workspace, source):
    if not isinstance(value, dict) or set(value) != {'version', 'description', 'phases'} or value['version'] != 1:
        raise ValueError('Unsupported soak recipe')
    if not isinstance(value['description'], str) or not 1 <= len(value['description']) <= 500:
        raise ValueError('Bounded description required')
    phases = value['phases']
    if not isinstance(phases, list) or not 4 <= len(phases) <= 20:
        raise ValueError('A mixed recipe needs 4–20 phases')
    if set(row.get('kind') for row in phases if isinstance(row, dict)) != set(KINDS):
        raise ValueError('Recipe must include import, editor, photo and training')
    ids = set()
    for row in phases:
        if not isinstance(row, dict) or set(row) != {'id', 'kind', 'argv', 'timeoutSeconds', 'modelResource', 'proof'}:
            raise ValueError('Unexpected phase fields')
        identifier = row['id']
        if not isinstance(identifier, str) or not identifier or len(identifier) > 60 or not all(c.isascii() and (c.isalnum() or c in '-_') for c in identifier) or identifier in ids:
            raise ValueError('Unique safe phase IDs required')
        ids.add(identifier)
        if type(row['timeoutSeconds']) not in (int, float) or not math.isfinite(row['timeoutSeconds']) or not 1 <= row['timeoutSeconds'] <= 600:
            raise ValueError('Phase deadline must be 1–600 seconds')
        if type(row['modelResource']) is not bool or row['modelResource'] != (row['kind'] in {'photo', 'training'}):
            raise ValueError('Photo and training phases require shared model resource')
        args = row['argv']
        if not isinstance(args, list) or not 1 <= len(args) <= 80 or any(not isinstance(a, str) or not a or len(a) > 4000 or '\0' in a for a in args):
            raise ValueError('Commands require bounded argument arrays')
        if not Path(args[0]).is_absolute() or Path(args[0]).name in {'sh','bash','zsh','fish','dash'}:
            raise ValueError('Use an absolute executable without a shell')
        if any(token in ' '.join(args) for token in ('{production}', '${', '$(')):
            raise ValueError('Unsupported command expansion')
        # Only these two placeholders exist. No environment or shell expansion.
        row['argv'] = [a.replace('{workspace}', str(workspace)).replace('{source}', str(source)) for a in args]
        proof = Path(row['proof'])
        if proof.is_absolute() or '..' in proof.parts or proof.suffix != '.json':
            raise ValueError('Proof must be a JSON path relative to isolated workspace')
    return value


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered)*fraction)-1)]


def process_rss(pid):
    """Measure this process group only, including children; no command lines."""
    try:
        raw = subprocess.check_output(['/bin/ps', '-axo', 'pid=,pgid=,rss='], text=True, timeout=3)
        rows = [tuple(map(int, line.split())) for line in raw.splitlines() if len(line.split()) == 3]
        return sum(rss*1024 for _, group, rss in rows if group == pid)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def memory_snapshot():
    out = {}
    if hasattr(os, 'sysconf'):
        try:
            out['physicalBytes'] = os.sysconf('SC_PHYS_PAGES')*os.sysconf('SC_PAGE_SIZE')
        except (OSError, ValueError):
            pass
    for args, key in ((['/usr/bin/vm_stat'], 'vmStat'), (['/usr/sbin/sysctl', '-n', 'vm.swapusage'], 'swap')):
        if not Path(args[0]).is_file():
            continue
        try:
            text = subprocess.check_output(args, text=True, timeout=3).strip()
            # OS aggregate counters only. Do not include process names or paths.
            out[key] = text[:4000]
        except (OSError, subprocess.SubprocessError):
            out[key] = 'unavailable'
    return out


def foreground_marker(cache):
    path = Path(cache)/'learning/foreground.json'
    if not path.exists() and not path.is_symlink():
        return 0
    try:
        value = read_object(path, 5000).get('at', 0)
        return float(value) if type(value) in (int, float) and math.isfinite(value) else math.inf
    except FileNotFoundError:
        return 0
    except (OSError, ValueError):
        return math.inf


def production_busy(cache):
    from learning_scheduler import active_imports
    return active_imports(cache) != 0


@contextmanager
def resource(cache, needed):
    descriptor = None
    acquired = not needed
    try:
        if needed:
            folder = Path(cache)/'learning'
            if not folder.is_dir() or folder.is_symlink():
                raise ValueError('Existing production resource directory required')
            descriptor = os.open(folder/'model-resource.lock', os.O_RDWR | os.O_NOFOLLOW)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = not production_busy(cache)
            except BlockingIOError:
                pass
        yield acquired
    finally:
        if descriptor is not None:
            fcntl.flock(descriptor, fcntl.LOCK_UN); os.close(descriptor)


def stop_process(process):
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=5)
    except ProcessLookupError:
        return
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL); process.wait(timeout=5)


def phase_proof(path, kind, started):
    if not path.exists() or path.stat().st_mtime < started:
        return {'verified': False, 'reason': 'fresh-proof-missing'}
    try:
        raw = path.read_bytes()
        proof = read_object(path)
    except (OSError, ValueError):
        return {'verified': False, 'reason': 'invalid-proof'}
    common = proof.get('kind') == kind and proof.get('ok') is True and proof.get('isolated') is True and proof.get('mocked') is False
    native = kind not in {'photo','training'} or proof.get('backend') in {'mlx','onnxruntime'}
    if kind == 'training':
        native = native and type(proof.get('completedSteps')) is int and proof['completedSteps'] > 0
    return {'verified': common and native, 'sha256': hashlib.sha256(raw).hexdigest(),
            'reason': 'phase-proof' if common and native else 'proof-contract',
            'backend': proof.get('backend') if native else None}


def run_phase(row, workspace, source, production, iteration, cancel, deadline, rss_limit):
    start = time.monotonic(); wall = time.time()
    receipt = {'phase':row['id'], 'kind':row['kind'], 'iteration':iteration,
               'state':'waiting', 'queueDepth':0, 'startedAt':datetime.now(timezone.utc).isoformat()}
    with resource(production, row['modelResource']) as acquired:
        if not acquired:
            return {**receipt, 'state':'yielded-busy', 'seconds':round(time.monotonic()-start, 6), 'queueDepth':1}
        marker = foreground_marker(production)
        if row['modelResource'] and (not math.isfinite(marker) or production_busy(production)):
            return {**receipt, 'state':'yielded-foreground', 'seconds':round(time.monotonic()-start, 6)}
        logs = workspace/'logs'; logs.mkdir(exist_ok=True, mode=0o700)
        path = logs/(str(iteration)+'-'+row['id']+'.log')
        proof_path = workspace/row['proof']
        if proof_path.is_symlink():
            raise ValueError('Proof path cannot be a link')
        proof_path.unlink(missing_ok=True)
        descriptor = os.open(path, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
        process = None; peak = 0; rss_samples = 0
        try:
            with os.fdopen(descriptor, 'wb') as log:
                environment = dict(os.environ)
                environment['REDCN_SOAK_WORKSPACE'] = str(workspace)
                environment['REDCN_SOAK_PHASE'] = row['kind']
                environment['REDCN_SOAK_RESOURCE_HELD'] = '1' if row['modelResource'] else '0'
                process = subprocess.Popen(row['argv'], cwd=source, env=environment, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                receipt['state'] = 'running'
                while process.poll() is None:
                    now = time.monotonic()
                    sampled = process_rss(process.pid)
                    if sampled is not None:
                        peak = max(peak, sampled); rss_samples += 1
                    reason = ('cancelled' if cancel() else 'run-deadline' if now >= deadline else
                              'phase-timeout' if now-start >= row['timeoutSeconds'] else
                              'memory-limit' if sampled is not None and sampled > rss_limit else
                              'log-limit' if path.stat().st_size > MAX_LOG_BYTES else
                              'yielded-foreground' if row['modelResource'] and (foreground_marker(production) > marker or production_busy(production)) else None)
                    if reason:
                        receipt['state'] = reason; stop_process(process); break
                    time.sleep(.2)
                code = process.wait(timeout=5)
                if receipt['state'] == 'running':
                    receipt['state'] = 'complete' if code == 0 else 'failed'
                receipt['exitCode'] = code
        finally:
            if process is not None:
                stop_process(process)
        receipt.update(seconds=round(time.monotonic()-start,6), peakProcessGroupRssBytes=peak,
                       rssSamples=rss_samples, proof=phase_proof(proof_path,row['kind'],wall))
        if receipt['state'] == 'complete' and not receipt['proof']['verified']:
            receipt['state'] = 'unverified'
    return receipt


def summary(rows, elapsed, requested, *, cancelled, memory, source_sha, recipe_sha):
    success = [row for row in rows if row['state'] == 'complete']
    coverage = Counter(row['kind'] for row in success)
    durations = defaultdict(list)
    for row in success:
        durations[row['kind']].append(row['seconds'])
    states = Counter(row['state'] for row in rows)
    failed = sum(states[name] for name in ('failed','phase-timeout','memory-limit','log-limit','unverified'))
    return {'version':1, 'task':'RCN-356', 'sourceSha':source_sha, 'recipeSha256':recipe_sha,
            'elapsedSeconds':round(elapsed,6), 'requestedSeconds':requested, 'cancelled':cancelled,
            'attempts':len(rows), 'states':dict(states), 'successfulPhaseCounts':dict(coverage),
            'latencySeconds':{key:{'p50':percentile(value,.5),'p95':percentile(value,.95),'count':len(value)} for key,value in durations.items()},
            'peakProcessGroupRssBytes':max((row.get('peakProcessGroupRssBytes',0) for row in rows),default=0),
            'peakObservedQueueDepth':max((row.get('queueDepth',0) for row in rows),default=0),
            'memorySamples':memory,
            'acceptance':{'twoHoursMeasured':elapsed >= 7200, 'requestedDurationCompleted':elapsed >= requested,
                          'allFourRealPhases':all(coverage[kind] for kind in KINDS), 'noPhaseErrors':failed == 0,
                          'physicalSleepWake':'not-measured', 'fullCriterionMet':False,
                          'notice':'Process interruption is not physical Mac sleep; independent sleep/wake evidence remains required.'}}
