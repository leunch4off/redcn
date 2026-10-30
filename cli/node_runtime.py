"""Find the shared JS runtime even when launchd supplies a minimal PATH.

No shell or installation is involved. Version results are cached by the actual
executable's stat signature, so a Homebrew upgrade invalidates the cache.
"""
from functools import lru_cache
import os
from pathlib import Path
import re
import shutil
import subprocess
import time


MINIMUM_MAJOR = 22
COMMON_NODE_PATHS = (
    '/opt/homebrew/bin/node', '/usr/local/bin/node',
    '/opt/homebrew/opt/node@22/bin/node', '/usr/local/opt/node@22/bin/node',
    '/opt/homebrew/opt/node/bin/node', '/usr/local/opt/node/bin/node',
)


class NodeRuntimeError(ValueError):
    """An actionable preflight failure, before opening a listing in Chrome."""


def _candidates(executable=None):
    configured = executable if executable is not None else os.environ.get('REDCN_NODE')
    if configured:
        # An explicit executable must not silently select a different runtime.
        values = [configured]
    else:
        values = [shutil.which('node'), *COMMON_NODE_PATHS]
    seen = set()
    for value in values:
        if not value:
            continue
        path = Path(value).expanduser()
        if not path.is_absolute():
            continue
        try:
            path = path.resolve(strict=True)
            if not path.is_file() or not os.access(path, os.X_OK):
                continue
            if str(path) not in seen:
                seen.add(str(path))
                yield path
        except OSError:
            continue


@lru_cache(maxsize=16)
def _version(path, signature, timeout):
    # signature is intentionally part of the key; it changes on replacement.
    result = subprocess.run([path, '--version'], stdin=subprocess.DEVNULL,
        capture_output=True, text=True, encoding='utf-8', check=False, timeout=timeout)
    match = re.fullmatch(r'v(\d+)\.(\d+)\.(\d+)(?:[-+][0-9A-Za-z.-]+)?\s*', result.stdout)
    if result.returncode or not match:
        raise NodeRuntimeError('Не удалось проверить версию найденного Node.js')
    return tuple(int(value) for value in match.groups())


def resolve_node(executable=None):
    """Return an absolute Node >=22 executable, or raise a precise ValueError.

    Both the HTTP parser and generator call this function. A server can call it
    before Chrome as a cheap preflight; successful probes are cached.
    """
    deadline = time.monotonic() + 4
    versions = []
    failures = False
    for path in _candidates(executable):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            failures = True
            break
        try:
            stat = path.stat()
            signature = (stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_size)
            # A fixed probe timeout keeps the successful cache key reusable.
            version = _version(str(path), signature, min(1.5, remaining))
            if version[0] >= MINIMUM_MAJOR:
                return str(path)
            versions.append('.'.join(map(str, version)))
        except (OSError, subprocess.SubprocessError, NodeRuntimeError):
            failures = True
    if versions:
        raise NodeRuntimeError('Найден Node.js ' + ', '.join(dict.fromkeys(versions))
            + ', но требуется версия 22 или новее. Обновите Node.js и перезапустите сервер.')
    if failures:
        raise NodeRuntimeError('Node.js найден, но не запускается или не отвечает на проверку версии. '
            'Проверьте установленный Node.js и перезапустите сервер.')
    raise NodeRuntimeError('Фоновый сервер не нашёл Node.js 22+. Установите Node.js или задайте '
        'полный путь в REDCN_NODE. Проверены PATH и стандартные каталоги Homebrew.')


# A descriptive alias for callers which perform dependency checks before import.
require_node = resolve_node
