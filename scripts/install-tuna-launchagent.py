#!/usr/bin/env python3
"""Prepare one fixed-origin TUNA LaunchAgent; default is a read-only dry run.

--install writes a new known plist and private working directory only. This
script first checks the official TUNA service registry and refuses a competing
agent. It never bootstraps launchd, starts/stops TUNA, kills a process, logs in,
reads authentication files, or changes an account/subscription.
"""
import argparse
import json
import os
from pathlib import Path
import platform
import plistlib
import pwd
import shlex
import stat
import subprocess
import tempfile
from urllib.parse import urlsplit

LABEL = 'app.redcn.tuna'
ORIGIN = 'https://free-quail-5273.a.tuna.free'
DOMAIN = 'free-quail-5273.a.tuna.free'
UPSTREAM = '127.0.0.1:8767'
BINARY = '/opt/homebrew/bin/tuna'
SERVICE_NAME = 'redcn-phone'
PUBLIC_OPTION_NAMES = {'--token', '--config', '--log-level', '--log-format', '--log',
    '--location', '--subdomain', '--request-header', '--response-header', '--host-header',
    '--basic-auth', '--key-auth', '--cors', '--qr', '--no-tui', '--tui', '--no-ui', '--ui',
    '--headless', '--no-color', '--color', '--https-redirect', '--port', '--scheme'}


def user_home():
    # No environment dump, authentication lookup or shell expansion.
    return Path(pwd.getpwuid(os.getuid()).pw_dir)


def _path(value):
    value = str(value)
    if not value or any(ord(char) < 32 for char in value):
        raise ValueError('Неверный путь')
    result = Path(value)
    if not result.is_absolute() or '..' in result.parts:
        raise ValueError('Нужен абсолютный путь без переходов к родителю')
    return result


def prepare(home, binary=BINARY, origin=ORIGIN, upstream=UPSTREAM):
    home, binary = _path(home), _path(binary)
    parsed = urlsplit(origin)
    if (parsed.scheme != 'https' or parsed.netloc != DOMAIN or parsed.path not in ('', '/')
            or parsed.query or parsed.fragment or parsed.username or parsed.password):
        raise ValueError('Сохраняется только существующий постоянный HTTPS-адрес')
    if upstream != UPSTREAM:
        raise ValueError('Разрешён только телефонный сервер 127.0.0.1:8767; порт редактора запрещён')
    if binary.name != 'tuna':
        raise ValueError('Нужен установленный исполняемый файл tuna')
    target = home/'Library/LaunchAgents'/f'{LABEL}.plist'
    working = home/'.cache/redcn/tuna-launchagent'
    document = {'Label': LABEL,
        'ProgramArguments': [str(binary), 'http', UPSTREAM, '--domain='+DOMAIN, '--inspect=false',
            '--https-redirect', '--log=stdout'],
        'WorkingDirectory': str(working), 'RunAtLoad': True, 'KeepAlive': True,
        'ThrottleInterval': 30, 'ExitTimeOut': 10, 'ProcessType': 'Background',
        'Umask': 0o077, 'StandardInPath': '/dev/null',
        'StandardOutPath': '/dev/null', 'StandardErrorPath': '/dev/null'}
    payload = plistlib.dumps(document, fmt=plistlib.FMT_XML, sort_keys=True)
    if plistlib.loads(payload) != document:
        raise ValueError('Не удалось проверить plist')
    return {'home': home, 'binary': binary, 'path': target, 'working': working,
        'document': document, 'payload': payload}


def _home_metadata(home):
    if home.is_symlink() or not home.is_dir() or home.stat().st_uid != os.getuid():
        raise ValueError('Каталог пользователя должен принадлежать текущему пользователю и не быть ссылкой')


def _binary_metadata(binary):
    # Homebrew normally uses a symlink. Inspect only executable metadata, not
    # configuration, tokens, process environments or the binary's contents.
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise ValueError('Установленный tuna не найден или не исполняется')


def native_lint(payload):
    if platform.system() != 'Darwin':
        return None
    result = subprocess.run(['/usr/bin/plutil', '-lint', '-'], input=payload,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=5)
    if result.returncode:
        raise ValueError('Нативная проверка plutil отклонила подготовленный plist')
    return True


def _directory(home, relative):
    current = home
    for part in relative:
        current = current/part
        if current.is_symlink():
            raise ValueError('Каталог LaunchAgent не должен содержать ссылок')
        try:
            current.mkdir(mode=0o700)
        except FileExistsError:
            pass
        info = current.stat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            raise ValueError('Неверный владелец или тип каталога LaunchAgent')
    return current


def install(agent):
    if platform.system() != 'Darwin':
        raise ValueError('Установка LaunchAgent доступна только на macOS')
    _home_metadata(agent['home'])
    _binary_metadata(agent['binary'])
    inventory = service_inventory(agent['binary'])
    if inventory['decision'] == 'already-managed':
        return {'installed': False, 'started': False, 'reason': 'already-managed',
            'serviceInventory': inventory}
    if inventory['decision'] != 'clear':
        raise ValueError('Реестр сервисов TUNA недоступен или неоднозначен; создание конкурирующего агента запрещено')
    native_lint(agent['payload'])
    # Never read or replace an existing plist, even if it may be identical.
    if agent['path'].exists() or agent['path'].is_symlink():
        raise ValueError('app.redcn.tuna.plist уже существует; автоматическая замена запрещена')
    parent = _directory(agent['home'], ('Library', 'LaunchAgents'))
    working = _directory(agent['home'], ('.cache', 'redcn', 'tuna-launchagent'))
    if stat.S_IMODE(working.stat().st_mode) & 0o077:
        raise ValueError('Приватный рабочий каталог должен иметь права 0700')
    temporary = None
    try:
        fd, name = tempfile.mkstemp(prefix='.'+LABEL+'-', suffix='.plist', dir=parent)
        temporary = Path(name)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'wb') as target:
            target.write(agent['payload'])
            target.flush()
            os.fsync(target.fileno())
        # Atomic, non-overwriting creation. A target appearing in the meantime
        # causes FileExistsError; neither ordinary files nor symlinks replace it.
        os.link(temporary, agent['path'], follow_symlinks=False)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return {'installed': True, 'started': False, 'path': str(agent['path'])}


def _match_arguments(tokens, registered_service=None, pid=None):
    """Only compare allowlisted options; never return their arbitrary values."""
    mode = bool(tokens) and tokens[0] == 'http'
    domain, upstream, unknown = None, None, False
    service_name = None
    unknown_options, unknown_positional = [], False
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token.startswith('--domain='):
            if domain is not None: unknown = True
            domain = token.split('=', 1)[1]
        elif token in ('--domain', '-d') and index+1 < len(tokens):
            if domain is not None: unknown = True
            index += 1; domain = tokens[index]
        elif token in ('--inspect=false', '--inspect=true'):
            pass
        elif token in ('--https-redirect', '--https-redirect=true', '--log=stdout', '--log=stderr', '--log=/dev/null'):
            pass
        elif token == '--log' and index+1 < len(tokens) and tokens[index+1] in ('stdout', 'stderr', '/dev/null'):
            index += 1
        elif token.startswith('--service=') or (token == '--service' and index+1 < len(tokens)):
            if service_name is not None: unknown = True
            if token == '--service':
                index += 1; service_name = tokens[index]
            else:
                service_name = token.split('=', 1)[1]
            # Internal CLI flag is accepted only for the exact registry-verified
            # service and PID. A name in process argv alone is not sufficient.
            if not (registered_service and registered_service.get('name') == SERVICE_NAME
                    and registered_service.get('pid') == pid and service_name == SERVICE_NAME):
                unknown = True
                unknown_options.append('--service')
        elif token in (UPSTREAM, 'http://'+UPSTREAM):
            if upstream is not None: unknown = True
            upstream = UPSTREAM
        else:
            unknown = True
            if token.startswith('-'):
                name = token.split('=', 1)[0]
                unknown_options.append(name if name in PUBLIC_OPTION_NAMES else '<unrecognized-option>')
            else:
                unknown_positional = True
        index += 1
    return {'modeMatches': mode, 'domainMatches': domain == DOMAIN,
        'explicitPhoneUpstream': upstream == UPSTREAM,
        'argumentsMatch': mode and domain == DOMAIN and upstream == UPSTREAM and not unknown,
        'unrecognizedOptionNames': sorted(set(unknown_options)),
        'unrecognizedPositionalPresent': unknown_positional}


def service_inventory(binary=BINARY, runner=None):
    """Query the installed CLI registry; return no Args/File/Log/auth values.

    An empty, successfully parsed registry permits preparing a fresh agent.
    Any unrecognized registration blocks installation rather than guessing its
    ownership or creating another tunnel with the same origin.
    """
    runner = runner or subprocess.run
    unavailable = {'queryState': 'unavailable', 'decision': 'blocked',
        'registeredServiceCount': None, 'existingService': None}
    try:
        result = runner([str(binary), 'service', 'list', '--json'],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            check=False, timeout=8)
        if result.returncode or not isinstance(result.stdout, str) or len(result.stdout) > 256_000:
            return unavailable
        rows = json.loads(result.stdout)
    except (ValueError, TypeError, OSError, subprocess.SubprocessError):
        return unavailable
    if not isinstance(rows, list) or len(rows) > 100 or any(not isinstance(row, dict) for row in rows):
        return {**unavailable, 'queryState': 'invalid'}
    summary = {'queryState': 'available', 'decision': 'clear' if not rows else 'blocked',
        'registeredServiceCount': len(rows), 'existingService': None}
    if len(rows) != 1:
        return summary
    row = rows[0]
    pid = row.get('PID')
    if (row.get('Name') != SERVICE_NAME or type(pid) is not int or pid <= 1
            or type(row.get('Scope')) is not int or row['Scope'] != 1
            or row.get('State') != 'running'):
        return summary
    executable = row.get('Exe')
    exact_binary = (isinstance(executable, str) and Path(executable).is_absolute()
        and Path(executable).resolve() == Path(binary).resolve())
    raw_arguments = row.get('Args')
    try:
        tokens = shlex.split(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
        if not isinstance(tokens, list) or not all(isinstance(token, str) for token in tokens):
            return summary
        if tokens and Path(tokens[0]).is_absolute():
            if Path(tokens[0]).resolve() != Path(binary).resolve():
                return summary
            tokens = tokens[1:]
    except (ValueError, OSError):
        return summary
    arguments = _match_arguments(tokens, {'name': SERVICE_NAME, 'pid': pid}, pid)
    summary['existingService'] = {'name': SERVICE_NAME, 'pid': pid,
        'scope': 1, 'state': 'running',
        'executableMatches': exact_binary, **arguments}
    if exact_binary and arguments['argumentsMatch']:
        summary['decision'] = 'already-managed'
    return summary


def process_match(pid, binary=BINARY, runner=subprocess.run, registered_service=None):
    """Sanitized, read-only metadata; no stop operation is implemented.

    A bare `tuna http --domain=...` has no provable explicit upstream. It is
    reported as unverified rather than guessing a cached/default upstream.
    """
    if type(pid) is not int or pid <= 1:
        raise ValueError('Нужен конкретный PID больше 1')
    values = {}
    for field in ('uid', 'comm', 'args'):
        result = runner(['/bin/ps', '-ww', '-p', str(pid), '-o', field+'='],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, check=False, timeout=3)
        if result.returncode or len(result.stdout) > 16_000:
            return {'pid': pid, 'exactKnownTunnelMatch': False, 'reason': 'metadata-unavailable'}
        values[field] = result.stdout.strip()
    try:
        tokens = shlex.split(values['args'])
        own_user = int(values['uid']) == os.getuid()
    except (ValueError, TypeError):
        return {'pid': pid, 'exactKnownTunnelMatch': False, 'reason': 'metadata-invalid'}
    executable = Path(values['comm'])
    argv_binary = Path(tokens[0]) if tokens else Path('.')
    exact_binary = (executable.is_absolute() and argv_binary.is_absolute()
        and executable.resolve() == Path(binary).resolve() == argv_binary.resolve())
    arguments = _match_arguments(tokens[1:], registered_service, pid)
    exact = own_user and exact_binary and arguments['argumentsMatch']
    return {'pid': pid, 'ownUser': own_user, 'executableMatches': exact_binary,
        'domainMatches': arguments['domainMatches'], 'explicitPhoneUpstream': arguments['explicitPhoneUpstream'],
        'unrecognizedOptionNames': arguments['unrecognizedOptionNames'],
        'unrecognizedPositionalPresent': arguments['unrecognizedPositionalPresent'],
        'exactKnownTunnelMatch': exact,
        'reason': 'verified-metadata-only' if exact else 'upstream-not-explicit' if not arguments['explicitPhoneUpstream'] else 'metadata-mismatch'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument('--dry-run', action='store_true', help='Проверить план без записи; поведение по умолчанию')
    choice.add_argument('--install', action='store_true', help='Только создать новый plist; не запускать сервис')
    parser.add_argument('--home', type=Path, default=None)
    parser.add_argument('--binary', default=BINARY)
    parser.add_argument('--origin', default=ORIGIN)
    parser.add_argument('--check-existing-pid', type=int)
    args = parser.parse_args(argv)
    try:
        agent = prepare(args.home or user_home(), binary=args.binary, origin=args.origin)
        _home_metadata(agent['home'])
        _binary_metadata(agent['binary'])
        lint = native_lint(agent['payload'])
        inventory = service_inventory(agent['binary'])
        exists = agent['path'].exists() or agent['path'].is_symlink()
        result = {'mode': 'install-plist-only' if args.install else 'dry-run',
            'label': LABEL, 'origin': ORIGIN, 'upstream': UPSTREAM,
            'path': str(agent['path']), 'workingDirectory': str(agent['working']),
            'nativePlistValid': lint, 'existingPlistPresentNotRead': exists,
            'canInstallWithoutOverwrite': not exists and inventory['decision'] == 'clear', 'paidPlanRequired': False,
            'serviceInventory': inventory,
            'plist': agent['document'], 'installed': False, 'started': False,
            'deferredGates': ['v25 server health verified', 'activeJobs=0', 'campaign active attempts=0',
                'old process handoff verified independently before stopping anything'],
            'laterCommands': ([['/bin/launchctl', 'bootstrap', 'gui/'+str(os.getuid()), str(agent['path'])],
                ['/bin/launchctl', 'kickstart', 'gui/'+str(os.getuid())+'/'+LABEL]]
                if inventory['decision'] == 'clear' else []),
            'rebootAcceptance': 'not-tested'}
        if args.check_existing_pid is not None:
            registered = inventory['existingService'] if inventory['decision'] == 'already-managed' else None
            result['existingProcess'] = process_match(args.check_existing_pid, binary=args.binary,
                registered_service=registered)
        if args.install:
            result.update(install(agent))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(json.dumps({'error': str(error), 'installed': False, 'started': False}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
