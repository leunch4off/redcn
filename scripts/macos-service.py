#!/usr/bin/env python3
"""Install/remove only redcn's per-user LaunchAgent; never needs sudo."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request


LABEL = 'app.redcn.server'
ROOT = Path(__file__).resolve().parents[1]


def manifest(root=ROOT, home=None):
    root = Path(root).resolve()
    home = Path.home() if home is None else Path(home)
    log_dir = home/'Library'/'Logs'/'redcn'
    return {
        'Label': LABEL,
        'ProgramArguments': [str(root/'.venv'/'bin'/'python'), '-u',
                             str(root/'cli'/'background_server.py'),
                             '--cache-dir', str(home/'.cache'/'redcn')],
        'WorkingDirectory': str(root), 'RunAtLoad': True, 'KeepAlive': True,
        'ThrottleInterval': 10, 'Umask': 0o077,
        'StandardOutPath': str(log_dir/'server.log'),
        'StandardErrorPath': str(log_dir/'server.log'),
        'EnvironmentVariables': {'PYTHONUNBUFFERED': '1'},
    }


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
    result = subprocess.run(['/bin/launchctl', *arguments], capture_output=True, text=True)
    if check and result.returncode:
        raise ValueError('launchctl: ' + (result.stderr.strip() or str(result.returncode))[:500])
    return result


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
        write_manifest(target, manifest())
        launchctl('bootout', domain+'/'+LABEL, check=False)
        launchctl('bootstrap', domain, str(target))
        print('Автозапуск включён. Сервер работает после входа в macOS, пока компьютер включён.')
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
