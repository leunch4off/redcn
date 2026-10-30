"""Validate explicit university-route notes without calculating journeys.

No user-supplied URL is accepted. The shared browser module constructs fixed
provider HTTPS links from these bounded fields; it labels coordinate-derived
distance as straight-line distance and never infers bus numbers or duration.
"""
from functools import lru_cache
import json
import math
from pathlib import Path
import re

MAX_ROUTES = 10
MAX_STOPS = 3
_MODES = {'unknown', 'public', 'metro', 'bus', 'walk', 'car', 'bike'}
_FIELDS = {'name', 'campus', 'campusId', 'address', 'latitude', 'longitude',
           'time', 'mode', 'transport', 'routeDistanceKm', 'stops'}
_STOP_FIELDS = {'name', 'latitude', 'longitude'}


@lru_cache(maxsize=1)
def _campus_ids():
    catalog = json.loads((Path(__file__).resolve().parents[1] /
                          'data' / 'moscow-universities.json').read_text())
    return frozenset(row['id'] for row in catalog['campuses'])


def _text(value, field, limit):
    if value is None:
        return ''
    if not isinstance(value, str):
        raise ValueError('Поле маршрута «' + field + '» должно быть строкой')
    if len(value) > limit:
        raise ValueError('Слишком длинное поле маршрута «' + field + '»')
    return value.strip()


def _number(value, field, lower, upper):
    if value is None or isinstance(value, str) and not value.strip():
        return ''
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError('Неверное числовое поле маршрута «' + field + '»')
    if isinstance(value, str) and not re.fullmatch(r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[-+]?\d+)?', value.strip(), re.I):
        raise ValueError('Неверное числовое поле маршрута «' + field + '»')
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError('Неверное числовое поле маршрута «' + field + '»') from error
    if not math.isfinite(result) or not lower <= result <= upper:
        raise ValueError('Число вне диапазона в поле маршрута «' + field + '»')
    return int(result) if result.is_integer() else result


def validate_routes(value):
    """Return a detached canonical list; an empty list explicitly clears it."""
    if not isinstance(value, list) or len(value) > MAX_ROUTES:
        raise ValueError('Нужно не более десяти маршрутов до вузов')
    output = []
    for row in value:
        if not isinstance(row, dict) or set(row) - _FIELDS:
            raise ValueError('Неизвестные поля маршрута до вуза')
        campus_id = _text(row.get('campusId', ''), 'campusId', 100)
        if campus_id and campus_id not in _campus_ids():
            raise ValueError('Кампус отсутствует в текущем справочнике')
        mode = _text(row.get('mode', 'unknown'), 'mode', 20)
        if mode not in _MODES:
            raise ValueError('Неверный способ поездки')
        stops = row.get('stops', [])
        if not isinstance(stops, list) or len(stops) > MAX_STOPS:
            raise ValueError('Нужно не более трёх остановок / ориентиров маршрута')
        clean_stops = []
        for stop in stops:
            if not isinstance(stop, dict) or set(stop) - _STOP_FIELDS:
                raise ValueError('Неверные поля остановки маршрута')
            clean = {'name': _text(stop.get('name', ''), 'stop.name', 240),
                     'latitude': _number(stop.get('latitude', ''), 'stop.latitude', -90, 90),
                     'longitude': _number(stop.get('longitude', ''), 'stop.longitude', -180, 180)}
            if clean['name']:
                clean_stops.append(clean)
        clean = {
            'name': _text(row.get('name', ''), 'name', 240),
            'campus': _text(row.get('campus', ''), 'campus', 240),
            'campusId': campus_id,
            'address': _text(row.get('address', ''), 'address', 500),
            'latitude': _number(row.get('latitude', ''), 'latitude', -90, 90),
            'longitude': _number(row.get('longitude', ''), 'longitude', -180, 180),
            'time': _text(row.get('time', ''), 'time', 80),
            'mode': mode,
            'transport': _text(row.get('transport', ''), 'transport', 600),
            'routeDistanceKm': _number(row.get('routeDistanceKm', ''), 'routeDistanceKm', 0, 20000),
            'stops': clean_stops,
        }
        if any(clean[key] for key in ('name', 'campus', 'campusId', 'address')):
            output.append(clean)
    return output
