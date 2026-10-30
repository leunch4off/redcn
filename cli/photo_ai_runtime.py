"""Start only the previously installed private Mac photo-AI service.

No downloads, model pulls, GUI, first-run key creation or global configuration.
Startup is optional: failure returns False and the caller keeps its fallback.
"""
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

OLLAMA_VERSION = '0.40.13'
PRIVATE_ENDPOINT = 'http://127.0.0.1:11435'
INSTALLER = Path(__file__).resolve().parents[1] / 'scripts' / 'install-photo-ai-macos.py'
MAX_START_SECONDS = 60
_CLEANUP_SECONDS = 4
_START_LOCK = threading.Lock()
_BACKGROUND_LOCK = threading.Lock()
_BACKGROUND_THREAD = None


def _cancelled(cancel):
    try:
        return cancel is not None and cancel.is_set()
    except (AttributeError, TypeError):
        return True


def _installed():
    base = Path.home() / '.cache' / 'redcn' / 'tools' / 'ollama'
    app = base / OLLAMA_VERSION / 'Ollama.app'
    cli = app / 'Contents' / 'Resources' / 'ollama'
    keys = Path.home() / '.ollama'
    # File presence is only an early eligibility check; the helper verifies
    # the pinned install manifest, signature, ownership and loopback readiness.
    return (not base.is_symlink() and not (base / OLLAMA_VERSION).is_symlink()
            and not app.is_symlink() and cli.is_file()
            and (base / OLLAMA_VERSION / 'install.json').is_file()
            and not INSTALLER.is_symlink() and INSTALLER.is_file()
            and (keys / 'id_ed25519').is_file()
            and (keys / 'id_ed25519.pub').is_file())


def _stop_starting_helper(process, finish):
    """Cancel only our helper; its SIGTERM handler cleans new startup children."""
    if process.poll() is not None:
        return
    try:
        process.terminate()
    except ProcessLookupError:
        return
    grace = min(finish, time.monotonic() + _CLEANUP_SECONDS - .25)
    while process.poll() is None and time.monotonic() < grace:
        try:
            process.wait(timeout=min(.1, max(.001, grace - time.monotonic())))
        except subprocess.TimeoutExpired:
            pass
    if process.poll() is None:
        # start_new_session creates this private helper group. No matching by
        # process name, user Ollama port or any unrelated service is performed.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=max(.001, min(.2, finish - time.monotonic())))
        except (subprocess.TimeoutExpired, ProcessLookupError):
            pass


def ensure_started(directory, config, *, deadline=None, cancel=None):
    """Return whether the owned private service is ready within the caller budget.

    ``deadline`` is an absolute monotonic time, as in the photo-analysis caller.
    No paths are created and no subprocess is started on Linux, disabled config,
    another endpoint, missing app/keys or a cancelled/exhausted caller budget.
    """
    if (sys.platform != 'darwin' or not isinstance(config, dict)
            or config.get('enabled') is not True or config.get('endpoint') != PRIVATE_ENDPOINT
            or _cancelled(cancel)):
        return False
    now = time.monotonic()
    if deadline is not None and (type(deadline) not in (int, float) or not math.isfinite(deadline)):
        return False
    finish = min(now + MAX_START_SECONDS, deadline) if deadline is not None else now + MAX_START_SECONDS
    if finish - now <= _CLEANUP_SECONDS + .25:
        return False
    try:
        if not _installed():
            return False
    except (OSError, ValueError):
        return False
    acquired = False
    process = None
    try:
        while not acquired:
            if _cancelled(cancel) or time.monotonic() >= finish - _CLEANUP_SECONDS:
                return False
            acquired = _START_LOCK.acquire(timeout=min(.05, max(.001, finish - time.monotonic())))
        if _cancelled(cancel) or time.monotonic() >= finish - _CLEANUP_SECONDS:
            return False
        # The installer's default start forbids first-run key initialization.
        # HOME is inherited unchanged; private daemon environment is set there.
        process = subprocess.Popen([sys.executable, str(INSTALLER), '--stage', 'start', '--apply'],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True, cwd=str(INSTALLER.parent.parent))
        while process.poll() is None:
            if _cancelled(cancel) or time.monotonic() >= finish - _CLEANUP_SECONDS:
                _stop_starting_helper(process, finish)
                return False
            try:
                process.wait(timeout=min(.05, max(.001, finish - _CLEANUP_SECONDS - time.monotonic())))
            except subprocess.TimeoutExpired:
                pass
        return process.returncode == 0 and not _cancelled(cancel) and time.monotonic() < finish
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        if process is not None:
            _stop_starting_helper(process, finish)
        return False
    finally:
        if acquired:
            _START_LOCK.release()


def start_background(directory):
    """Return immediately; at most one optional startup thread runs at a time."""
    global _BACKGROUND_THREAD
    if sys.platform != 'darwin':
        return None
    with _BACKGROUND_LOCK:
        if _BACKGROUND_THREAD is not None and _BACKGROUND_THREAD.is_alive():
            return _BACKGROUND_THREAD

        def launch():
            try:
                from photo_ai_settings import load_config
                config = load_config(directory)
                ensure_started(directory, config)
            except Exception:
                # Optional service availability never makes web startup fatal.
                pass

        _BACKGROUND_THREAD = threading.Thread(target=launch, name='redcn-photo-ai-start', daemon=True)
        _BACKGROUND_THREAD.start()
        return _BACKGROUND_THREAD
