"""Explicit, temporary HTTPS access to the paired phone listener only.

The application installs no binary or account here. A separately installed
cloudflared opens an outbound Quick Tunnel; the phone server still authenticates
every private request. The parent registers the exact URL with its Host/Origin
allowlist through on_url and removes it through on_stop.
"""
from pathlib import Path
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
from urllib.parse import urlsplit


_QUICK_HOST = re.compile(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.trycloudflare\.com')
_ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')


def quick_tunnel_url(line):
    """Read only a complete HTTPS Quick Tunnel origin from local process output."""
    if not isinstance(line, str):
        return None
    for match in re.finditer(r'https://[^\s<>"\'|]+', _ANSI.sub('', line)):
        candidate = match.group(0).rstrip(',;)')
        try:
            parsed = urlsplit(candidate)
            if (parsed.scheme == 'https' and _QUICK_HOST.fullmatch(parsed.netloc)
                    and parsed.path in ('', '/') and not parsed.query
                    and not parsed.fragment and not parsed.username and not parsed.password):
                return 'https://' + parsed.netloc
        except ValueError:
            pass
    return None


def find_cloudflared(executable=None):
    """Locate an already installed binary; never download, update, or invoke a shell."""
    if executable is not None:
        candidates = [Path(executable).expanduser()]
    else:
        candidates = [
            Path.home() / '.local' / 'bin' / 'cloudflared',
            Path('/opt/homebrew/bin/cloudflared'),
            Path('/usr/local/bin/cloudflared'),
        ]
        discovered = shutil.which('cloudflared')
        if discovered:
            candidates.append(Path(discovered))
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate.resolve())
    return None


def _terminate(process):
    if process is None:
        return
    try:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
    except (OSError, subprocess.SubprocessError):
        pass


class RemoteAccess:
    def __init__(self, directory, on_url=None, on_stop=None, executable=None,
                 ready_timeout=35, forbidden_ports=(8765,)):
        if not 0 < ready_timeout <= 90:
            raise ValueError('Ожидание туннеля: до 90 секунд')
        self.directory = Path(directory)
        self.on_url = on_url or (lambda _url: None)
        self.on_stop = on_stop or (lambda _url: None)
        self.executable = executable
        self.ready_timeout = ready_timeout
        self.forbidden_ports = set(forbidden_ports)
        self.lock = threading.RLock()
        self._generation = 0
        self._state = 'stopped'
        self._url = None
        self._error = ''
        self._process = None
        self._cancel = None
        self._thread = None

    def status(self):
        with self.lock:
            return {
                'state': self._state, 'url': self._url, 'error': self._error,
                'pid': self._process.pid if self._process is not None else None,
                'available': find_cloudflared(self.executable) is not None,
                'temporary': True,
            }

    def _configuration(self):
        folder = self.directory / 'remote-access'
        if folder.is_symlink():
            raise ValueError('Неверный каталог настройки туннеля')
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        folder.chmod(0o700)
        path = folder / 'empty-config.yml'
        if path.is_symlink():
            raise ValueError('Неверный путь настройки туннеля')
        with tempfile.NamedTemporaryFile(mode='w', dir=folder, encoding='utf-8', delete=False) as output:
            output.write('{}\n')
            temporary = Path(output.name)
        temporary.chmod(0o600)
        temporary.replace(path)
        return path.resolve()

    def start(self, local_port):
        if type(local_port) is not int or not 1 <= local_port <= 65535 or local_port in self.forbidden_ports:
            raise ValueError('Внешний доступ разрешён только для отдельного порта телефона')
        with self.lock:
            if self._state in ('starting', 'ready'):
                return self.status()
            binary = find_cloudflared(self.executable)
            if binary is None:
                self._state = 'error'
                self._error = 'Не найден cloudflared. Установите его на Mac через brew install cloudflared или в ~/.local/bin/cloudflared.'
                return self.status()
            self._generation += 1
            generation = self._generation
            self._state = 'starting'
            self._url = None
            self._error = ''
            self._cancel = threading.Event()
            self._thread = threading.Thread(target=self._run, args=(generation, self._cancel, binary, local_port), daemon=True)
            self._thread.start()
            return self.status()

    def stop(self):
        # Never join the monitor: its callback may need the controller's lock.
        with self.lock:
            self._generation += 1
            if self._cancel:
                self._cancel.set()
            process, previous_url = self._process, self._url
            self._process = None
            self._url = None
            self._state = 'stopped'
            self._error = ''
        _terminate(process)
        if previous_url:
            self.on_stop(previous_url)
        return self.status()

    def _finish(self, generation, message):
        with self.lock:
            if generation != self._generation:
                return
            previous_url = self._url
            self._process = None
            self._url = None
            self._state = 'error'
            self._error = message
        if previous_url:
            self.on_stop(previous_url)

    @staticmethod
    def _read_lines(stream, messages):
        try:
            while True:
                line = stream.readline(4097)
                if not line:
                    break
                try:
                    messages.put_nowait(line[:4096])
                except queue.Full:
                    # Keep memory bounded even if the executable floods stderr.
                    try:
                        messages.get_nowait()
                    except queue.Empty:
                        pass
                    try:
                        messages.put_nowait(line[:4096])
                    except queue.Full:
                        pass
        except (OSError, ValueError):
            pass

    def _run(self, generation, cancel, binary, local_port):
        process = None
        try:
            configuration = self._configuration()
            command = [binary, 'tunnel', '--config', str(configuration), '--no-autoupdate',
                       '--protocol', 'http2', '--url', f'http://127.0.0.1:{local_port}', '--loglevel', 'info']
            environment = {key: value for key, value in os.environ.items() if not key.startswith('TUNNEL_')}
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.PIPE, text=True, bufsize=1,
                                       cwd=configuration.parent, env=environment, start_new_session=True)
            with self.lock:
                if generation != self._generation or cancel.is_set():
                    return
                self._process = process
            messages = queue.Queue(maxsize=256)
            threading.Thread(target=self._read_lines, args=(process.stderr, messages), daemon=True).start()
            deadline = time.monotonic() + self.ready_timeout
            connected = False
            while not cancel.is_set():
                try:
                    line = messages.get(timeout=0.1)
                except queue.Empty:
                    line = ''
                url = quick_tunnel_url(line)
                if url:
                    with self.lock:
                        if generation != self._generation:
                            return
                        is_new = self._url is None
                        if is_new:
                            self._url = url
                    if is_new:
                        self.on_url(url)
                        with self.lock:
                            stale = generation != self._generation
                        if stale:
                            self.on_stop(url)
                            return
                if 'Registered tunnel connection' in line:
                    connected = True
                with self.lock:
                    if generation != self._generation:
                        return
                    if connected and self._url:
                        self._state = 'ready'
                    ready = self._state == 'ready'
                if process.poll() is not None:
                    self._finish(generation, 'Туннель остановился. Проверьте интернет на Mac и нажмите «Включить внешний доступ» снова.')
                    return
                if not ready and time.monotonic() >= deadline:
                    seconds = max(1, round(self.ready_timeout))
                    self._finish(generation, f'Не удалось подключить внешний адрес за {seconds} секунд. Проверьте интернет/VPN на Mac и повторите запуск.')
                    return
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            self._finish(generation, 'Не удалось запустить внешний доступ: ' + str(error)[:250])
        except Exception:
            # A failed allowlist callback must close the tunnel, not leave it open.
            self._finish(generation, 'Не удалось разрешить внешний адрес. Перезапустите внешний доступ на Mac.')
        finally:
            _terminate(process)
