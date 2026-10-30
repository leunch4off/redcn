#!/usr/bin/env python3
"""Install/remove only redcn's per-user LaunchAgent; never needs sudo."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request


LABEL = 'app.redcn.server'
ROOT = Path(__file__).resolve().parents[1]


def manifest(root=ROOT, home=None, runtime_arch=None):
    root = Path(root).resolve()
    home = Path.home() if home is None else Path(home)
    log_dir = home/'Library'/'Logs'/'redcn'
    arguments = [str(root/'.venv'/'bin'/'python'), '-u',
                 str(root/'cli'/'background_server.py'),
                 '--cache-dir', str(home/'.cache'/'redcn')]
    if runtime_arch is not None:
        if runtime_arch not in ('arm64','x86_64'):
            raise ValueError('Архитектура Python: arm64 или x86_64')
        arguments = ['/usr/bin/arch','-'+runtime_arch,*arguments]
    return {
        'Label': LABEL,
        'ProgramArguments': arguments,
        'WorkingDirectory': str(root), 'RunAtLoad': True, 'KeepAlive': True,
        'ThrottleInterval': 10, 'Umask': 0o077,
        'StandardOutPath': str(log_dir/'server.log'),
        'StandardErrorPath': str(log_dir/'server.log'),
        'EnvironmentVariables': {'PYTHONUNBUFFERED': '1',
            'PATH': '/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin'},
    }


def select_runtime_arch(python, preferred=None):
    """Probe existing dependencies, including their compiled extensions.

    A universal Python may choose ARM64 under launchd while a terminal under
    Rosetta has installed x86_64-only wheels. Pin a working architecture instead
    of modifying that environment or relying on the installer's own process.
    """
    machine = platform.machine() if preferred is None else preferred
    candidates = ('arm64','x86_64') if machine in ('arm64','aarch64') else ('x86_64','arm64')
    probe = ('import platform\nfrom PIL import Image, ImageOps\n'
             'import playwright.sync_api, certifi\n'
             'ImageOps.exif_transpose(Image.new("RGB", (1, 1)))\n'
             'certifi.where()\nprint(platform.machine())\n')
    for architecture in candidates:
        try:
            result = subprocess.run(['/usr/bin/arch','-'+architecture,str(python),'-I','-c',probe],
                                    capture_output=True,text=True,timeout=15)
        except (OSError,subprocess.TimeoutExpired):
            continue
        if result.returncode == 0 and result.stdout.strip() == architecture:
            return architecture
    raise ValueError('Среда Python не прошла проверку Pillow, Playwright и certifi ни для ARM64, ни для x86_64. Зависимости не изменены; проверьте .venv проекта перед установкой сервера.')


def assert_owned_manifest(path, root=None):
    """Never overwrite/unload an agent installed by another project copy."""
    path = Path(path)
    if path.is_symlink():
        raise ValueError('Путь LaunchAgent не должен быть ссылкой')
    if not path.exists():
        return
    root = ROOT if root is None else Path(root).resolve()
    with path.open('rb') as stream:
        saved = plistlib.load(stream)
    expected = manifest(root)
    arguments = saved.get('ProgramArguments') if isinstance(saved, dict) else None
    if isinstance(arguments,list) and arguments[:2] in (
            ['/usr/bin/arch','-arm64'],['/usr/bin/arch','-x86_64']):
        arguments = arguments[2:]
    if (not isinstance(saved, dict) or saved.get('Label') != LABEL
            or saved.get('WorkingDirectory') != str(root)
            or not isinstance(arguments, list)
            or arguments[:3] != expected['ProgramArguments'][:3]):
        raise ValueError('LaunchAgent принадлежит другой копии приложения')


def write_manifest(path, payload):
    path = Path(path)
    assert_owned_manifest(path, payload['WorkingDirectory'])
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as output:
            temporary = Path(output.name)
            plistlib.dump(payload, output)
        temporary.chmod(0o600)
        temporary.replace(path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def launchctl(*arguments, check=True):
    try:
        result = subprocess.run(['/bin/launchctl', *arguments], capture_output=True, text=True, timeout=5)
    except subprocess.TimeoutExpired as error:
        raise ValueError('launchctl не ответил за 5 секунд. Повторите команду после проверки scripts/macos-service.py status.') from error
    if check and result.returncode:
        raise ValueError('launchctl: ' + (result.stderr.strip() or str(result.returncode))[:500])
    return result


def _not_loaded(result):
    return result.returncode in (3, 113) or (
        result.returncode != 0 and 'could not find service' in result.stderr.lower())


def _reload_failure(message, result=None):
    detail = '' if result is None else ' (код ' + str(result.returncode) + ': ' + result.stderr.strip()[:250] + ')'
    return ValueError(message + detail + '. Проверьте .venv/bin/python scripts/macos-service.py status и журнал ~/Library/Logs/redcn/server.log.')


def reload_agent(domain, target, timeout=8):
    """Wait for asynchronous bootout before re-registering this owned agent.

    launchd may return bootstrap error 5 briefly after a completed bootout.
    Retry that one error only while the label is absent, never unload it twice
    or touch a newly registered service to force a retry.
    """
    if not 0 < timeout <= 30:
        raise ValueError('Ожидание перезапуска: от 0 до 30 секунд')
    assert_owned_manifest(target)
    service = domain+'/'+LABEL
    result = launchctl('bootout', service, check=False)
    if result.returncode and not _not_loaded(result):
        raise _reload_failure('Не удалось остановить прежний LaunchAgent', result)
    deadline = time.monotonic()+timeout
    while True:
        state = launchctl('print', service, check=False)
        if _not_loaded(state):
            break
        if state.returncode:
            raise _reload_failure('Не удалось проверить остановку LaunchAgent', state)
        remaining = deadline-time.monotonic()
        if remaining <= 0:
            raise _reload_failure('LaunchAgent ещё завершает работу. Повторите установку через несколько секунд')
        time.sleep(min(.2, remaining))
    for attempt in range(3):
        result = launchctl('bootstrap', domain, str(target), check=False)
        if result.returncode == 0:
            return
        if result.returncode != 5:
            raise _reload_failure('Не удалось зарегистрировать LaunchAgent', result)
        state = launchctl('print', service, check=False)
        if not _not_loaded(state):
            raise _reload_failure('LaunchAgent уже зарегистрирован или его состояние недоступно; повторный запуск отменён', result)
        remaining = deadline-time.monotonic()
        if attempt == 2 or remaining <= 0:
            raise _reload_failure('launchd не принял LaunchAgent после остановки. Повторите установку через несколько секунд', result)
        time.sleep(min(.3, remaining))


def service_status(domain, home=None):
    home = Path.home() if home is None else Path(home)
    result = launchctl('print', domain+'/'+LABEL, check=False)
    pid = re.search(r'^\s*pid = (\d+)\s*$', result.stdout, re.MULTILINE)
    editor = phone = False
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open('http://127.0.0.1:8765/api/local/health', timeout=2) as response:
            health = json.loads(response.read(4097))
        expected_storage = hashlib.sha256(str((home/'.cache'/'redcn').resolve()).encode()).hexdigest()
        editor = health == {'app':'redcn', 'protocol':7, 'storage':expected_storage, 'browser_channel':'chrome'}
    except (OSError, ValueError):
        pass
    try:
        with opener.open('http://127.0.0.1:8767/phone/', timeout=2) as response:
            phone = response.status == 200
    except OSError:
        pass
    return {'label':LABEL, 'loaded':result.returncode == 0,
            'pid':int(pid[1]) if pid else None, 'editorReady':editor,
            'phoneListenerReady':phone, 'editorUrl':'http://127.0.0.1:8765/',
            'log':str(home/'Library'/'Logs'/'redcn'/'server.log')}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Автозапуск redcn после входа в macOS')
    parser.add_argument('action', choices=('install', 'status', 'remove'))
    args = parser.parse_args(argv)
    if sys.platform != 'darwin':
        parser.error('Этот помощник предназначен для macOS')
    domain = 'gui/'+str(os.getuid())
    target = Path.home()/'Library'/'LaunchAgents'/(LABEL+'.plist')
    if args.action == 'install':
        assert_owned_manifest(target)
        python = ROOT/'.venv'/'bin'/'python'
        if not python.is_file() or not os.access(python, os.X_OK):
            raise ValueError('Сначала запустите start-redcn.command: нужна .venv проекта')
        if not (ROOT/'docs'/'index.html').is_file():
            raise ValueError('Сначала соберите приложение: npm run build')
        runtime_arch = select_runtime_arch(python)
        log_dir = Path.home()/'Library'/'Logs'/'redcn'
        if log_dir.is_symlink():
            raise ValueError('Каталог журнала не должен быть ссылкой')
        log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        log_dir.chmod(0o700)
        log_file = log_dir/'server.log'
        if log_file.is_symlink():
            raise ValueError('Журнал не должен быть ссылкой')
        log_file.touch(mode=0o600, exist_ok=True)
        log_file.chmod(0o600)
        write_manifest(target, manifest(runtime_arch=runtime_arch))
        reload_agent(domain, target)
        print('Автозапуск включён (Python '+runtime_arch+'). Сервер работает после входа в macOS, пока компьютер включён.')
    elif args.action == 'remove':
        assert_owned_manifest(target)
        launchctl('bootout', domain+'/'+LABEL, check=False)
        target.unlink(missing_ok=True)
        print('Автозапуск выключен. Карточки и документы сохранены.')
    print(json.dumps(service_status(domain), ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
