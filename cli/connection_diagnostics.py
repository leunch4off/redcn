"""Explicit, anonymous connectivity checks for this server's own phone URL.

Probes never follow redirects or send a cookie/code. DNS and HTTP calls run in
bounded slots so a stuck system resolver cannot block the API or create a new
thread on every click. A tunnel connector is not proof of phone reachability.
"""
import copy
import http.client
import ipaddress
import queue
import socket
import ssl
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from remote_access import quick_tunnel_url


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


class _PinnedHTTPS(urllib.request.HTTPSHandler):
    def __init__(self, addresses, context):
        super().__init__(context=context)
        self.addresses = addresses

    def https_open(self, request):
        def connection(host, **kwargs):
            result = http.client.HTTPSConnection(host, **kwargs)
            def connect(_address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None):
                # Keep hostname/SNI/certificate verification while connecting only
                # to the already checked public DNS answer, avoiding DNS rebinding.
                deadline = time.monotonic() + (timeout if isinstance(timeout, (int, float)) else 4)
                for address in self.addresses[:4]:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError('HTTPS connection deadline exceeded')
                    try:
                        return socket.create_connection((address, 443), remaining, source_address)
                    except OSError:
                        if address == self.addresses[:4][-1]:
                            raise
                raise OSError('No public address')
            result._create_connection = connect
            return result
        return self.do_open(connection, request, context=self._context)


def fetch_shell(url, timeout, addresses=None):
    try:
        import certifi
        context = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        context = ssl.create_default_context()
    https = _PinnedHTTPS(addresses, context) if addresses else urllib.request.HTTPSHandler(context=context)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), https, _NoRedirect())
    request = urllib.request.Request(url, headers={
        'User-Agent': 'redcn-connectivity-check/1', 'Accept-Encoding': 'identity',
    })
    with opener.open(request, timeout=timeout) as response:
        body = response.read(512_001)
        if response.status != 200 or len(body) > 512_000 or b'id="phonePair"' not in body:
            raise ValueError('Получена другая страница: возможно, стартовая плашка сервиса туннеля')
    return 'Интерфейс МОСТ получен, HTTP 200'


def _error_message(error):
    if isinstance(error, TimeoutError):
        return 'Нет ответа за отведённое время'
    if isinstance(error, urllib.error.HTTPError):
        return 'Сервер вернул HTTP ' + str(error.code)
    if isinstance(error, socket.gaierror):
        return 'Имя адреса пока не разрешается в DNS'
    if isinstance(error, urllib.error.URLError):
        reason = error.reason
        if isinstance(reason, ssl.SSLError):
            return 'Не удалось подтвердить сертификат HTTPS'
        if isinstance(reason, socket.gaierror):
            return 'Имя адреса пока не разрешается в DNS'
        if isinstance(reason, TimeoutError):
            return 'Нет ответа за отведённое время'
        return 'Соединение не установлено; проверьте интернет на Mac'
    if isinstance(error, ValueError):
        return str(error)[:150]
    return 'Проверка соединения не завершилась'


class ConnectionDiagnostics:
    def __init__(self, fetch=fetch_shell, resolve=socket.getaddrinfo,
                 local_timeout=1.5, dns_timeout=2, https_timeout=4):
        self.fetch, self.resolve = fetch, resolve
        self.local_timeout, self.dns_timeout, self.https_timeout = local_timeout, dns_timeout, https_timeout
        self.lock = threading.RLock()
        self._generation = 0
        self._slots = {}
        self._value = {'state': 'idle', 'steps': [], 'startedAt': None,
                       'finishedAt': None, 'origin': None}

    def status(self):
        with self.lock:
            return copy.deepcopy(self._value)

    def close(self):
        with self.lock:
            self._generation += 1

    def start(self, phone_status):
        # Use a snapshot assembled by the server, never client-provided URLs.
        remote = phone_status.get('remote', {})
        origin = remote.get('url')
        custom = remote.get('provider') == 'custom' and remote.get('state') == 'configured'
        if custom:
            try:
                from external_origin import normalize_external_origin
                if normalize_external_origin(origin) != origin:
                    origin = None
            except (ImportError, ValueError, TypeError):
                origin = None
        elif remote.get('state') != 'ready' or quick_tunnel_url(origin) != origin:
            origin = None
        port = phone_status.get('port') if phone_status.get('enabled') else None
        if type(port) is not int or not 1 <= port <= 65535:
            port = None
        with self.lock:
            if self._value['state'] == 'running':
                return self.status()
            self._generation += 1
            generation = self._generation
            self._value = {'state': 'running', 'startedAt': time.time(),
                'finishedAt': None, 'origin': origin, 'steps': [
                    {'id': 'editor', 'label': 'Редактор на Mac', 'state': 'ok',
                     'message': 'Защищённый локальный API отвечает'},
                    {'id': 'phone', 'label': 'Телефонный сервер', 'state': 'waiting'},
                    {'id': 'tunnel', 'label': 'Адрес стороннего шлюза' if custom else 'Процесс туннеля', 'state': 'waiting'},
                    {'id': 'dns', 'label': 'DNS внешнего адреса', 'state': 'waiting'},
                    {'id': 'https', 'label': 'Внешний HTTPS с Mac', 'state': 'waiting'},
                ]}
            threading.Thread(target=self._run, args=(generation, port, origin, remote.get('state')),
                             daemon=True).start()
            return self.status()

    def _set(self, generation, step_id, state, message='', ms=None):
        with self.lock:
            if generation != self._generation:
                return False
            step = next(step for step in self._value['steps'] if step['id'] == step_id)
            step.update(state=state, message=message)
            if ms is not None:
                step['ms'] = ms
            return True

    def _bounded(self, key, call, timeout):
        with self.lock:
            previous = self._slots.get(key)
            if previous is not None and previous.is_alive():
                raise TimeoutError('Previous network call is still pending')
            output = queue.Queue(maxsize=1)
            def run():
                try:
                    output.put((True, call()))
                except Exception as error:
                    output.put((False, error))
            thread = threading.Thread(target=run, daemon=True)
            self._slots[key] = thread
            thread.start()
        try:
            ok, value = output.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError('Network deadline exceeded') from None
        if not ok:
            raise value
        return value

    def _probe(self, generation, step_id, call, timeout):
        if not self._set(generation, step_id, 'running', 'Проверяем…'):
            return False
        started = time.monotonic()
        try:
            message = self._bounded(step_id, call, timeout)
            state = 'ok'
        except Exception as error:
            message, state = _error_message(error), 'error'
        self._set(generation, step_id, state, message,
                  round((time.monotonic() - started) * 1000))
        return state == 'ok'

    def _run(self, generation, port, origin, tunnel_state):
        try:
            if port:
                self._probe(generation, 'phone', lambda: self.fetch(
                    f'http://127.0.0.1:{port}/phone/', self.local_timeout), self.local_timeout)
            else:
                self._set(generation, 'phone', 'error', 'Телефонный доступ выключен')
            self._set(generation, 'tunnel', 'ok' if origin else 'skipped',
                ('Внешний адрес настроен; проверяем фактический ответ' if tunnel_state == 'configured' else
                 'Коннектор подключён; доступность с телефона проверяется отдельно') if origin else
                'Нет готового внешнего адреса: ' + str(tunnel_state or 'stopped'))
            if origin:
                hostname = urlsplit(origin).hostname
                addresses = []
                def dns():
                    answers = self.resolve(hostname, 443, type=socket.SOCK_STREAM)
                    if not answers:
                        raise socket.gaierror('No DNS answers')
                    for answer in answers:
                        ip = answer[4][0]
                        if not ipaddress.ip_address(ip).is_global:
                            raise ValueError('DNS вернул локальный или служебный IP; внешний запрос отменён')
                        if ip not in addresses:
                            addresses.append(ip)
                    return 'DNS вернул адрес; проверяем HTTPS'
                if self._probe(generation, 'dns', dns, self.dns_timeout):
                    self._probe(generation, 'https', lambda: self.fetch(origin + '/phone/',
                        self.https_timeout, addresses=addresses), self.https_timeout)
                else:
                    self._set(generation, 'https', 'skipped', 'Сначала должен разрешиться DNS')
            else:
                for step in ('dns', 'https'):
                    self._set(generation, step, 'skipped', 'Включите внешний адрес и повторите проверку')
        finally:
            with self.lock:
                if generation == self._generation:
                    self._value.update(state='done', finishedAt=time.time())
