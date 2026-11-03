"""Private TTL reuse of explicitly checked route notes; never a route calculator.

The authenticated workspace dispatcher mounts these endpoints. No network calls,
keys, source HTML, imports or listing writes occur here. A cached note keeps its
original verification date: saving it again must not make old evidence current.
"""
from contextlib import closing
from datetime import datetime, timezone, timedelta
import hashlib
import json
import math
import re
from pathlib import Path
import sqlite3
import time
from urllib.parse import urlsplit

from university_routes import validate_routes
from university_store import read_catalog, fold

DATABASE = 'university-route-cache.sqlite3'
TTL_SECONDS = 3600
MAX_ENTRIES = 1000
PREFIX = 'university-route-cache/'


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _address(value):
    if value is None:
        return ''
    if not isinstance(value, str) or len(value)>1000 or '\x00' in value:
        raise ValueError('Неверный адрес начала маршрута')
    return fold(value)


def _point(value):
    lat, lon = value.get('latitude'), value.get('longitude')
    if lat in (None, '') and lon in (None, ''):
        return None
    result = []
    for number, bound in ((lat, 90), (lon, 180)):
        if isinstance(number, bool):
            raise ValueError('Нужны обе координаты маршрута')
        if isinstance(number, str) and not re.fullmatch(r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[-+]?\d+)?',number.strip(),re.I):
            raise ValueError('Неверный формат координат маршрута')
        try:
            number = float(number)
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError('Нужны обе координаты маршрута') from error
        if not math.isfinite(number) or abs(number)>bound:
            raise ValueError('Координаты маршрута вне диапазона')
        result.append(number)
    return result


def _request(data, catalog_path=None):
    if not isinstance(data, dict) or set(data)!={'origin', 'route'} or not isinstance(data['origin'], dict) or set(data['origin'])-{'latitude', 'longitude', 'address'}:
        raise ValueError('Нужны исходная точка и конкретный маршрут')
    rows = validate_routes([data['route']])
    if not rows:
        raise ValueError('Нужен непустой маршрут')
    row = rows[0]
    if row['method']=='provider' and not row['sourceUrl']:
        raise ValueError('Для кеша сервиса нужен URL проверенного источника')
    if not row['campusId'] or row['mode']=='unknown':
        raise ValueError('Выберите конкретный корпус и способ поездки')
    loaded = read_catalog(catalog_path)
    campuses = {campus['id']: campus for campus in loaded[0]['campuses']} if loaded else {}
    campus = campuses.get(row['campusId'])
    if not campus:
        raise ValueError('Корпус отсутствует в текущем справочнике')
    if row['address'] and _address(row['address'])!=_address(campus['addressRu']):
        raise ValueError('Адрес маршрута отличается от выбранного корпуса')
    origin = {'point': _point(data['origin']), 'address': _address(data['origin'].get('address'))}
    if not origin['point'] and not origin['address']:
        raise ValueError('Укажите исходный адрес или координаты')
    # Include published-point revision and explicit overrides. Never reuse a
    # main-building trip for another faculty, entrance override or changed point.
    destination = {'campusId': campus['id'], 'address': campus['addressRu'],
        'point': _point(row), 'catalogPoint': _point(campus),
        'coordinateEvidence': campus.get('coordinateEvidence'), 'sourceIds': campus['sourceIds']}
    provider = ('provider:'+urlsplit(row['sourceUrl']).hostname) if row['method']=='provider' and row['sourceUrl'] else 'manual-confirmed'
    key = hashlib.sha256(_json({'v': 1, 'origin': origin, 'destination': destination, 'mode': row['mode'], 'provider': provider}).encode()).hexdigest()
    return key, row


def _connect(directory):
    folder = Path(directory)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = folder / DATABASE
    connection = sqlite3.connect(path, timeout=5)
    path.chmod(0o600)
    connection.execute('PRAGMA busy_timeout=5000')
    connection.execute('CREATE TABLE IF NOT EXISTS route_notes (cache_key TEXT PRIMARY KEY, saved_at REAL NOT NULL, expires_at REAL NOT NULL, route TEXT NOT NULL)')
    return connection


def cache(directory, action, data, *, now=None, catalog_path=None):
    if action not in ('remember', 'lookup'):
        raise ValueError('Неизвестное действие кеша маршрутов')
    stamp = time.time() if now is None else float(now)
    if not math.isfinite(stamp):
        raise ValueError('Неверные часы кеша маршрутов')
    key, row = _request(data, catalog_path)
    if action=='remember':
        if row['method'] not in ('manual-confirmed', 'provider') or not row['checkedAt'] or not row['time'] or row['method']=='provider' and not row['sourceUrl']:
            raise ValueError('Для кеша нужны проверенный маршрут, время, дата и источник сервиса')
        checked = datetime.fromisoformat(row['checkedAt']).replace(tzinfo=timezone(timedelta(hours=3))).timestamp()
        # A date is coarser than cache retrieval time. Limit it to today or the
        # preceding Moscow day; cached reuse never refreshes this evidence date.
        if checked>stamp or stamp-checked>172800:
            raise ValueError('Дата маршрута устарела или находится в будущем; проверьте его снова')
    with closing(_connect(directory)) as connection, connection:
        if action=='remember':
            connection.execute('DELETE FROM route_notes WHERE expires_at<=?', (stamp,))
            connection.execute('INSERT OR REPLACE INTO route_notes VALUES (?,?,?,?)', (key, stamp, stamp+TTL_SECONDS, _json(row)))
            connection.execute('DELETE FROM route_notes WHERE cache_key IN (SELECT cache_key FROM route_notes ORDER BY saved_at DESC LIMIT -1 OFFSET ?)', (MAX_ENTRIES,))
            return {'cached': True, 'ttlSeconds': TTL_SECONDS, 'savedAt': stamp, 'expiresAt': stamp+TTL_SECONDS, 'method': row['method'], 'checkedAt': row['checkedAt'], 'sourceUrl': row['sourceUrl']}
        found = connection.execute('SELECT saved_at,expires_at,route FROM route_notes WHERE cache_key=?', (key,)).fetchone()
        if not found:
            return {'hit': False, 'reason': 'not-found', 'ttlSeconds': TTL_SECONDS}
        if found[0]>stamp or found[1]<=stamp:
            return {'hit': False, 'reason': 'expired', 'ttlSeconds': TTL_SECONDS, 'expiresAt': found[1]}
        cached = json.loads(found[2])
        # Labels are local to the current editor; only checked journey facts
        # come from cache, preserving source, method and original date.
        cached.update({key: row[key] for key in ('name', 'campus', 'campusId', 'address', 'latitude', 'longitude', 'mode')})
        return {'hit': True, 'route': cached, 'savedAt': found[0], 'expiresAt': found[1], 'ageSeconds': stamp-found[0], 'ttlSeconds': TTL_SECONDS}


def dispatch(directory, path, data):
    if not path.startswith(PREFIX):
        return None
    if data is None:
        raise ValueError('Кеш маршрута запрашивается с конкретной исходной точкой и корпусом')
    return 200, cache(directory, path[len(PREFIX):], data), 'application/json; charset=utf-8', {}
