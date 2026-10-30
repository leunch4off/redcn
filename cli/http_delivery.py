"""Small process-only gzip cache for complete textual HTTP responses."""
from collections import OrderedDict
import gzip
import hashlib
import re
import threading


MIN_COMPRESS_BYTES = 1024
MAX_CACHE_ENTRIES = 8
MAX_CACHE_BYTES = 16 * 1024 * 1024


def accepts_gzip(value):
    """Honor exact coding tokens and explicit q=0 over a wildcard."""
    if not isinstance(value, str):
        return False
    codings = {}
    for item in value.split(','):
        parts = [part.strip() for part in item.split(';')]
        coding = parts[0].lower()
        if not coding:
            continue
        weight = 1.0
        for parameter in parts[1:]:
            key, separator, raw = parameter.partition('=')
            if key.strip().lower() != 'q':
                continue
            raw = raw.strip()
            if not separator or not re.fullmatch(r'(?:0(?:\.\d{0,3})?|1(?:\.0{0,3})?)', raw):
                weight = 0.0
            else:
                weight = float(raw)
        codings[coding] = max(codings.get(coding, 0), weight)
    return codings.get('gzip', codings.get('*', 0)) > 0


def compressible(content_type):
    mime = str(content_type).partition(';')[0].strip().lower()
    return (mime.startswith('text/') and mime != 'text/event-stream') or mime in (
        'application/json', 'application/javascript', 'application/manifest+json',
        'application/xml', 'image/svg+xml',
    ) or mime.endswith('+json')


class ResponseCompressionCache:
    """Only compressed bytes and hashes are retained; never writes to disk."""
    def __init__(self, max_entries=MAX_CACHE_ENTRIES, max_bytes=MAX_CACHE_BYTES):
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self._values = OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()

    def compress(self, content):
        key = hashlib.sha256(content).digest()
        with self._lock:
            if key in self._values:
                self._values.move_to_end(key)
                return self._values[key]
            compressed = gzip.compress(content, compresslevel=5, mtime=0)
            if len(compressed) < len(content) and len(compressed) <= self.max_bytes and self.max_entries > 0:
                while self._values and (len(self._values) >= self.max_entries or self._bytes + len(compressed) > self.max_bytes):
                    _, old = self._values.popitem(last=False)
                    self._bytes -= len(old)
                self._values[key] = compressed
                self._bytes += len(compressed)
            return compressed

    def info(self):
        with self._lock:
            return {'entries': len(self._values), 'bytes': self._bytes}

    def clear(self):
        with self._lock:
            self._values.clear()
            self._bytes = 0


_cache = ResponseCompressionCache()


def response_bytes(content, content_type, accept_encoding=''):
    """Return body and additional headers; caller sets Content-Length afterward.

    This deliberately does not change Cache-Control. Personalized pages and
    API data retain their existing no-store policy even when gzip is selected.
    """
    if not isinstance(content, (bytes, bytearray)):
        raise TypeError('HTTP content must be bytes')
    content = bytes(content)
    if not compressible(content_type):
        return content, {}
    headers = {'Vary': 'Accept-Encoding'}
    if len(content) <= MIN_COMPRESS_BYTES or not accepts_gzip(accept_encoding):
        return content, headers
    compressed = _cache.compress(content)
    if len(compressed) >= len(content):
        return content, headers
    return compressed, {**headers, 'Content-Encoding': 'gzip'}
