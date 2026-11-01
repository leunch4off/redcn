"""One owner per private workspace; paired devices are explicit delegates.

An owner credential is never obtained from a public HTTP page. The local CLI
can read its own 0600 credential and pass it to the browser in a fragment. The
server exchanges that fragment for an HttpOnly cookie; neither request URLs,
logs nor a different workspace reveal it.
"""
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile


class OwnershipError(PermissionError):
    pass


def owned_path(directory, relative):
    """Validate an application path without following a link to another owner."""
    root = Path(directory).expanduser()
    if root.is_symlink():
        raise OwnershipError('Хранилище рабочего пространства не должно быть ссылкой')
    value = Path(relative)
    if value.is_absolute() or not value.parts or any(part in ('..', '') for part in value.parts):
        raise OwnershipError('Путь не принадлежит рабочему пространству')
    current = root
    for part in value.parts:
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or (stat.S_ISREG(info.st_mode) and info.st_nlink != 1):
            raise OwnershipError('Файл не принадлежит изолированному хранилищу')
    return current


class WorkspaceOwner:
    """Immutable filesystem owner and scope checked before private dispatch."""
    def __init__(self, directory):
        self.directory = Path(directory).expanduser().absolute()
        if self.directory.is_symlink():
            raise OwnershipError('Хранилище рабочего пространства не должно быть ссылкой')
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        info = self.directory.stat()
        if hasattr(os, 'geteuid') and info.st_uid != os.geteuid():
            raise OwnershipError('Рабочее пространство принадлежит другому пользователю системы')
        self.directory.chmod(0o700)
        self.identity = info.st_dev, info.st_ino, info.st_uid
        self.canonical = self.directory.resolve()
        self.owner_id = hashlib.sha256(str(self.canonical).encode()).hexdigest()
        self.check()

    def check(self, directory=None):
        if directory is not None and Path(directory).expanduser().absolute() != self.directory:
            raise OwnershipError('Контроллер принадлежит другому рабочему пространству')
        info = self.directory.lstat()
        if (stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode)
                or (info.st_dev, info.st_ino, info.st_uid) != self.identity
                or self.directory.resolve() != self.canonical):
            raise OwnershipError('Владелец или хранилище рабочего пространства изменились')
        # Fixed-cost metadata checks, not a walk through thousands of photos or
        # model weights. Resource stores validate their addressed leaves too.
        for child in self.directory.iterdir():
            try:
                value = child.lstat()
            except FileNotFoundError:  # a completed SQLite journal may vanish
                continue
            if stat.S_ISLNK(value.st_mode) or (stat.S_ISREG(value.st_mode) and value.st_nlink != 1):
                raise OwnershipError('Хранилище содержит ссылку на чужие данные')
        for relative in ('learning/memory.sqlite3', 'card-assets/dossiers.sqlite3',
                         'import-diagnostics/campaigns.sqlite3',
                         'photo-instance-reviews/revisions.sqlite3'):
            owned_path(self.directory, relative)
        return True


class DesktopOwnerAccess:
    """Persistent owner access, excluded from portable application backups."""
    filename = 'desktop-owner.json'

    def __init__(self, scope, *, create=True):
        scope.check()
        self.scope = scope
        path = owned_path(scope.directory, self.filename)
        if not path.exists():
            if not create:
                raise OwnershipError('Сначала запустите локальный redcn от владельца рабочего пространства')
            token = secrets.token_urlsafe(32)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                        dir=scope.directory, prefix='.desktop-owner-', delete=False) as stream:
                    temporary = Path(stream.name)
                    temporary.chmod(0o600)
                    json.dump({'version': 1, 'token': token}, stream)
                    stream.flush(); os.fsync(stream.fileno())
                # A simultaneous launcher may have published the credential.
                try:
                    os.link(temporary, path)
                except FileExistsError:
                    pass
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        info = path.stat()
        if (info.st_size > 1024 or info.st_mode & 0o077
                or (hasattr(os, 'geteuid') and info.st_uid != os.geteuid())):
            raise OwnershipError('Доступ владельца должен храниться в приватном файле 0600')
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError, UnicodeError) as error:
            raise OwnershipError('Повреждён локальный доступ владельца') from error
        if (not isinstance(payload, dict) or set(payload) != {'version', 'token'}
                or type(payload['version']) is not int or payload['version'] != 1 or not isinstance(payload['token'], str)
                or not re.fullmatch(r'[A-Za-z0-9_-]{43}', payload['token'])):
            raise OwnershipError('Повреждён локальный доступ владельца')
        self.token = payload['token']

    def authorized(self, value):
        return (isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{43}', value) is not None
                and secrets.compare_digest(value, self.token))

    def cookie_name(self, port):
        return 'redcn_owner_' + str(port)

    def _digest(self, value):
        return hmac.new(self.token.encode(),value.encode(),hashlib.sha256).hexdigest()

    def session_path(self, port):
        return '/owner/'+self._digest('owner-path-v1|'+str(port))[:32]+'/access'

    def authorized_cookie(self, value, port):
        return (isinstance(value, str) and re.fullmatch(r'[a-f0-9]{64}', value) is not None
                and secrets.compare_digest(value, self._digest('owner-cookie-v1|' + str(port))))

    def proof(self, port, challenge):
        if not isinstance(challenge,str) or not re.fullmatch(r'[a-f0-9]{32}',challenge):
            raise ValueError('Неверный запрос подтверждения владельца')
        return self._digest('owner-proof-v1|'+str(port)+'|'+self.scope.owner_id+'|'+challenge)

    def cookie(self, port):
        prefix=self.session_path(port).removesuffix('access')
        return self.cookie_name(port)+'='+self._digest('owner-cookie-v1|'+str(port))+'; HttpOnly; SameSite=Strict; Path='+prefix+'; Max-Age=31536000'

    def launch_url(self, port, result_id=None):
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError('Неверный порт локального редактора')
        if result_id is not None and not re.fullmatch(r'[a-f0-9]{32}', str(result_id)):
            raise ValueError('Неверный ID карточки')
        path = '/listing/' + result_id if result_id else '/'
        return 'http://127.0.0.1:' + str(port) + path + '#redcn-owner=' + self.token
