"""Validate an explicitly configured public HTTPS reverse-proxy origin.

No URL is fetched here. The configured origin is an exact Host/Origin allowlist
entry, never a wildcard. A later diagnostic must pin its own public DNS answer.
"""
import ipaddress
import queue
import re
import socket
import threading
from urllib.parse import urlsplit


_LABEL = re.compile(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?')
_LOCAL_SUFFIXES = ('localhost', 'local', 'localdomain', 'internal', 'intranet',
                   'home', 'lan', 'test', 'invalid', 'example', 'onion', 'arpa')
_LOOKUP_SLOT = threading.BoundedSemaphore(1)


def normalize_external_origin(value, resolve=False):
    """Return a canonical HTTPS origin; accept no credentials, port or URL path."""
    if not isinstance(value, str) or not value or len(value) > 270:
        raise ValueError('Введите публичный HTTPS-адрес без пути и параметров')
    if value != value.strip() or not value.isascii() or any(ord(char) < 33 for char in value):
        raise ValueError('Адрес должен содержать только имя HTTPS-домена')
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname or ''
        if (parsed.scheme != 'https' or parsed.username is not None or parsed.password is not None
                or parsed.port is not None or parsed.path not in ('', '/')
                or parsed.query or parsed.fragment or '?' in value or '#' in value
                or '\\' in value or not parsed.netloc):
            raise ValueError()
    except ValueError:
        raise ValueError('Нужен HTTPS-домен без порта, пути, логина и параметров') from None
    labels = hostname.split('.')
    if (len(hostname) > 253 or len(labels) < 2 or not all(_LABEL.fullmatch(label) for label in labels)
            or not re.fullmatch(r'(?:[a-z]{2,63}|xn--[a-z0-9-]{2,59})', labels[-1])
            or labels[-1] in _LOCAL_SUFFIXES
            or any(hostname == domain or hostname.endswith('.' + domain)
                   for domain in ('example.com', 'example.net', 'example.org'))):
        raise ValueError('Нужен публичный домен; локальные адреса и IP не подходят')
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise ValueError('Используйте HTTPS-домен с сертификатом, а не IP-адрес')
    if resolve:
        public_addresses(hostname)
    return 'https://' + hostname


def public_addresses(hostname, timeout=2):
    """Resolve within a small deadline and reject every non-public DNS answer."""
    if not _LOOKUP_SLOT.acquire(blocking=False):
        raise ValueError('Предыдущая проверка DNS ещё выполняется. Повторите позже')
    output = queue.Queue(maxsize=1)
    def lookup():
        try:
            output.put((True, socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)))
        except OSError as error:
            output.put((False, error))
        finally:
            _LOOKUP_SLOT.release()
    threading.Thread(target=lookup, daemon=True).start()
    try:
        ok, records = output.get(timeout=timeout)
    except queue.Empty:
        raise ValueError('DNS не ответил за 2 секунды. Проверьте домен и повторите') from None
    if not ok:
        raise ValueError('Публичный домен пока не найден в DNS') from None
    addresses = tuple(dict.fromkeys(record[4][0] for record in records))
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise ValueError('Внешний домен должен разрешаться только в публичные IP-адреса')
    return addresses
