"""A user-requested property map: one bounded viewport, attributed and cached.

Only the viewport currently requested by the designer is fetched. This is not an
area download, prefetcher or map archive. References:
https://operations.osmfoundation.org/policies/tiles/
https://osmfoundation.org/wiki/Licence/Attribution_Guidelines
"""
import base64
from email.utils import parsedate_to_datetime
import io
import json
import math
from pathlib import Path
import re
import ssl
import tempfile
import threading
import time
import urllib.error
import urllib.request

TILE_URL = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png'
USER_AGENT = 'MOST-Redcn/10.0 property-presentation (+https://github.com/leunch4off/redcn)'
ATTRIBUTION = '© OpenStreetMap contributors'
COPYRIGHT_URL = 'https://www.openstreetmap.org/copyright'
MAX_LATITUDE = 85.0511287798066
FALLBACK_TTL = 7 * 24 * 60 * 60
MAX_TILE_BYTES = 1_000_000
SOURCE_WIDTH, SOURCE_HEIGHT = 512, 342
WIDTH, HEIGHT = 768, 512
_map_lock = threading.Lock()


def coordinates(latitude, longitude, zoom=15):
    def number(value, low, high, name):
        if isinstance(value, bool):
            raise ValueError('Неверная координата: ' + name)
        if not isinstance(value, (str, int, float)) or isinstance(value, str) and not value.strip():
            raise ValueError('Укажите координату: ' + name)
        try:
            value = float(value)
        except (TypeError, ValueError) as error:
            raise ValueError('Неверная координата: ' + name) from error
        if not math.isfinite(value) or not low <= value <= high:
            raise ValueError('Неверная координата: ' + name)
        return value
    latitude = number(latitude, -MAX_LATITUDE, MAX_LATITUDE, 'широта')
    longitude = number(longitude, -180, 180, 'долгота')
    if isinstance(zoom, bool) or not isinstance(zoom, int) or not 12 <= zoom <= 17:
        raise ValueError('Масштаб карты: целое число от 12 до 17')
    return latitude, longitude, zoom


def project(latitude, longitude, zoom):
    """Web Mercator coordinates in standard 256px tile pixels."""
    size = 256 * 2 ** zoom
    rad = math.radians(latitude)
    return (longitude + 180) / 360 * size, (1 - math.asinh(math.tan(rad)) / math.pi) / 2 * size


class _TileRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # This fixed public tile service must never turn into an arbitrary URL fetcher.
        if not re.fullmatch(r'https://tile\.openstreetmap\.org/\d+/\d+/\d+\.png', newurl):
            raise ValueError('Сервис карты перенаправил запрос на неизвестный адрес')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _opener():
    try:
        import certifi
        context = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        context = ssl.create_default_context()
    return urllib.request.build_opener(_TileRedirect(), urllib.request.HTTPSHandler(context=context))


def _cache_headers(headers, previous=None):
    values = dict(previous or {})
    for name in ('Cache-Control', 'Expires', 'ETag', 'Last-Modified', 'Date', 'Age'):
        if headers.get(name) is not None:
            values[name] = str(headers.get(name))[:1000]
    return values


def _expiry(headers, now):
    control = headers.get('Cache-Control', '').lower()
    if 'no-store' in control or 'no-cache' in control:
        return now
    match = re.search(r'(?:^|,)\s*max-age\s*=\s*"?(\d+)', control)
    if match:
        try:
            age = max(0, int(headers.get('Age', '0')))
        except (ValueError, TypeError):
            age = 0
        return now + min(31_536_000, max(0, int(match[1]) - age))
    if headers.get('Expires'):
        try:
            return max(now, parsedate_to_datetime(headers['Expires']).timestamp())
        except (ValueError, TypeError, OverflowError):
            pass
    return now + FALLBACK_TTL


def _atomic(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as output:
        output.write(content)
        temporary = Path(output.name)
    temporary.replace(path)


def _tile(directory, zoom, x, y, opener):
    """Read a fresh tile or conditionally revalidate exactly one expired tile."""
    path = Path(directory) / 'map-tiles' / str(zoom) / str(x) / (str(y) + '.png')
    meta_path = path.with_suffix('.json')
    metadata, content = {}, None
    try:
        candidate = path.read_bytes()
        parsed = json.loads(meta_path.read_text(encoding='utf-8'))
        if len(candidate) <= MAX_TILE_BYTES and candidate.startswith(b'\x89PNG\r\n\x1a\n') and isinstance(parsed, dict):
            content, metadata = candidate, parsed
    except (OSError, ValueError):
        pass
    now = time.time()
    if content and isinstance(metadata.get('expires'), (int, float)) and metadata['expires'] > now:
        return content
    headers = {'User-Agent': USER_AGENT, 'Accept': 'image/png'}
    old_headers = metadata.get('headers', {})
    if content and isinstance(old_headers, dict):
        for source, target in (('ETag', 'If-None-Match'), ('Last-Modified', 'If-Modified-Since')):
            if isinstance(old_headers.get(source), str):
                headers[target] = old_headers[source]
    request = urllib.request.Request(TILE_URL.format(z=zoom, x=x, y=y), headers=headers)
    try:
        with opener.open(request, timeout=5) as response:
            if response.headers.get_content_type() != 'image/png':
                raise ValueError('Сервис карты вернул не PNG')
            content = response.read(MAX_TILE_BYTES + 1)
            if len(content) > MAX_TILE_BYTES or not content.startswith(b'\x89PNG\r\n\x1a\n'):
                raise ValueError('Неверный фрагмент карты')
            cached_headers = _cache_headers(response.headers)
    except urllib.error.HTTPError as error:
        if error.code != 304 or content is None:
            raise ValueError('Карта недоступна: HTTP ' + str(error.code)) from error
        cached_headers = _cache_headers(error.headers, old_headers)
    except (OSError, urllib.error.URLError) as error:
        raise ValueError('Не удалось загрузить карту. Проверьте интернет на Mac.') from error
    # Validate actual dimensions before persisting untrusted image bytes.
    try:
        from PIL import Image
        with Image.open(io.BytesIO(content)) as tile:
            if tile.format != 'PNG' or tile.size != (256, 256):
                raise ValueError('Неверный размер фрагмента карты')
            tile.load()
    except (OSError, ImportError) as error:
        raise ValueError('Не удалось прочитать фрагмент карты; нужен Pillow') from error
    if 'no-store' not in cached_headers.get('Cache-Control', '').lower():
        _atomic(path, content)
        _atomic(meta_path, json.dumps({'expires': _expiry(cached_headers, now), 'headers': cached_headers}).encode())
    else:
        path.unlink(missing_ok=True)
        meta_path.unlink(missing_ok=True)
    return content


def _font(size):
    from PIL import ImageFont
    for candidate in ('/System/Library/Fonts/Helvetica.ttc', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            pass
    return ImageFont.load_default()


def map_image(directory, latitude, longitude, zoom=15):
    """Create an attributed 768x512 property viewport from at most nine tiles."""
    latitude, longitude, zoom = coordinates(latitude, longitude, zoom)
    try:
        from PIL import Image, ImageDraw
    except ImportError as error:
        raise ValueError('Для карты установите зависимости фото: cli/requirements-media.txt') from error
    center_x, center_y = project(latitude, longitude, zoom)
    left, top = math.floor(center_x - SOURCE_WIDTH / 2), math.floor(center_y - SOURCE_HEIGHT / 2)
    first_x, last_x = left // 256, (left + SOURCE_WIDTH - 1) // 256
    first_y, last_y = top // 256, (top + SOURCE_HEIGHT - 1) // 256
    count = 2 ** zoom
    view = Image.new('RGB', (SOURCE_WIDTH, SOURCE_HEIGHT), '#e9eee7')
    with _map_lock:
        opener = _opener()
        for tile_y in range(first_y, last_y + 1):
            if tile_y < 0 or tile_y >= count:
                continue
            for tile_x in range(first_x, last_x + 1):
                content = _tile(directory, zoom, tile_x % count, tile_y, opener)
                try:
                    with Image.open(io.BytesIO(content)) as image:
                        view.paste(image.convert('RGB'), (tile_x * 256 - left, tile_y * 256 - top))
                except OSError as error:
                    raise ValueError('Повреждённый фрагмент карты. Повторите загрузку.') from error
    view = view.resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)
    draw = ImageDraw.Draw(view)
    pin_x = round((center_x - left) * WIDTH / SOURCE_WIDTH)
    pin_y = round((center_y - top) * HEIGHT / SOURCE_HEIGHT)
    draw.ellipse((pin_x - 23, pin_y - 23, pin_x + 23, pin_y + 23), fill='#ffffff', outline='#ffffff', width=3)
    draw.ellipse((pin_x - 17, pin_y - 17, pin_x + 17, pin_y + 17), fill='#bd4f35')
    draw.ellipse((pin_x - 5, pin_y - 5, pin_x + 5, pin_y + 5), fill='#ffffff')
    credit = ATTRIBUTION + ' · openstreetmap.org/copyright'
    font = _font(14)
    # Burn attribution into the raster so PNG and PDF copies retain it.
    draw.rectangle((0, HEIGHT - 32, WIDTH, HEIGHT), fill='#ffffff')
    draw.text((12, HEIGHT - 25), credit, fill='#203b2f', font=font)
    output = io.BytesIO()
    view.save(output, format='PNG', optimize=True)
    return {'dataUrl': 'data:image/png;base64,' + base64.b64encode(output.getvalue()).decode(),
            'attribution': ATTRIBUTION, 'copyrightUrl': COPYRIGHT_URL,
            'sourceUrl': f'https://www.openstreetmap.org/?mlat={latitude:.7f}&mlon={longitude:.7f}#map={zoom}/{latitude:.7f}/{longitude:.7f}',
            'latitude': latitude, 'longitude': longitude, 'zoom': zoom}
