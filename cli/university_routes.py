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
from datetime import date, datetime
from urllib.parse import urlsplit

MAX_ROUTES = 10
MAX_STOPS = 3
MAX_SEGMENTS = 8
_MODES = {'unknown', 'public', 'metro', 'bus', 'walk', 'car', 'bike'}
_METHODS = {'unknown', 'manual-confirmed', 'provider', 'estimate'}
_FIELDS = {'name', 'campus', 'campusId', 'address', 'latitude', 'longitude',
           'time', 'mode', 'transport', 'routeDistanceKm', 'stops', 'method',
           'checkedAt', 'sourceUrl', 'accessMinutes', 'egressMinutes', 'transfers', 'segments',
           'originPoint','destinationPoint','useEntrances','calculation'}
_STOP_FIELDS = {'name', 'latitude', 'longitude'}
_SEGMENT_FIELDS = {'mode', 'transport', 'from', 'to', 'minutes'}


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


def _route_point(value):
    if value is None:
        return None
    keys={'latitude','longitude','name','kind','availability','checkedAt','sourceUrl'}
    if not isinstance(value,dict) or set(value)-keys:
        raise ValueError('Неверные поля выбранной точки маршрута')
    kind=value.get('kind','point');availability=value.get('availability','unknown')
    if kind not in ('point','building-entrance','metro-exit') or availability not in ('available','closed','unknown'):
        raise ValueError('Неверный тип или доступность точки маршрута')
    day=_text(value.get('checkedAt',''),'point.checkedAt',10)
    if day:
        try:
            if date.fromisoformat(day).isoformat()!=day:raise ValueError()
        except ValueError as error:raise ValueError('Неверная дата проверки точки') from error
    url=_text(value.get('sourceUrl',''),'point.sourceUrl',1200)
    if url:
        parsed=urlsplit(url)
        if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None,443):raise ValueError('Источник точки должен быть HTTPS-ссылкой')
    return {'latitude':_number(value.get('latitude',''),'point.latitude',-90,90),'longitude':_number(value.get('longitude',''),'point.longitude',-180,180),'name':_text(value.get('name',''),'point.name',160),'kind':kind,'availability':availability,'checkedAt':day,'sourceUrl':url}


def _route_calculation(value):
    if value is None:return None
    keys={'provider','calculatedAt','expiresAt','durationSeconds','distanceKm','travelTimeKind','responseSha256'}
    if not isinstance(value,dict) or set(value)!=keys or value['provider']!='valhalla-fossgis-demo' or value['travelTimeKind']!='network-estimate':raise ValueError('Неверные данные расчёта маршрута')
    stamps=[]
    for key in ('calculatedAt','expiresAt'):
        text=_text(value[key],key,40)
        try:
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z',text):raise ValueError()
            stamps.append(datetime.fromisoformat(text.replace('Z','+00:00')).timestamp())
        except ValueError as error:raise ValueError('Неверная дата расчёта маршрута') from error
    if not 0<stamps[1]-stamps[0]<=600:raise ValueError('Неверный срок расчёта маршрута')
    if not isinstance(value['responseSha256'],str) or not re.fullmatch(r'[a-f0-9]{64}',value['responseSha256']):raise ValueError('Неверный хеш ответа маршрута')
    if any(type(value[key]) not in (int,float) for key in ('durationSeconds','distanceKm')):raise ValueError('Метрики маршрута должны быть числами')
    return {**value,'durationSeconds':_number(value['durationSeconds'],'durationSeconds',0,172800),'distanceKm':_number(value['distanceKm'],'distanceKm',0,2000)}


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
        method = _text(row.get('method', 'unknown'), 'method', 30)
        if method not in _METHODS:
            raise ValueError('Неизвестный способ проверки маршрута')
        checked_at = _text(row.get('checkedAt', ''), 'checkedAt', 10)
        if checked_at:
            try:
                if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', checked_at):
                    raise ValueError()
                date.fromisoformat(checked_at)
            except ValueError as error:
                raise ValueError('Неверная дата проверки маршрута') from error
        source_url = _text(row.get('sourceUrl', ''), 'sourceUrl', 2048)
        if source_url:
            try:
                parsed = urlsplit(source_url)
                if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None, 443):
                    raise ValueError()
            except ValueError as error:
                raise ValueError('Источник маршрута должен быть обычной HTTPS-ссылкой') from error
        def integer(value, field, maximum):
            result = _number(value, field, 0, maximum)
            if result != '' and not isinstance(result, int):
                raise ValueError('Нужно целое число в поле маршрута «' + field + '»')
            return result
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
        segments = row.get('segments', [])
        if not isinstance(segments, list) or len(segments) > MAX_SEGMENTS:
            raise ValueError('Нужно не более восьми участков маршрута')
        clean_segments = []
        for segment in segments:
            if not isinstance(segment, dict) or set(segment) - _SEGMENT_FIELDS:
                raise ValueError('Неверные поля участка маршрута')
            segment_mode = _text(segment.get('mode', 'unknown'), 'segment.mode', 20)
            if segment_mode not in _MODES:
                raise ValueError('Неверный транспорт участка маршрута')
            part = {'mode': segment_mode,
                    'transport': _text(segment.get('transport', ''), 'segment.transport', 160),
                    'from': _text(segment.get('from', ''), 'segment.from', 160),
                    'to': _text(segment.get('to', ''), 'segment.to', 160),
                    'minutes': integer(segment.get('minutes', ''), 'segment.minutes', 1440)}
            if any(part[key] for key in ('transport', 'from', 'to')) or part['minutes'] != '':
                clean_segments.append(part)
        if type(row.get('useEntrances',False)) is not bool:raise ValueError('Расчёт по входам задаётся переключателем')
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
            'method': method,
            'checkedAt': checked_at,
            'sourceUrl': source_url,
            'accessMinutes': integer(row.get('accessMinutes', ''), 'accessMinutes', 180),
            'egressMinutes': integer(row.get('egressMinutes', ''), 'egressMinutes', 180),
            'transfers': integer(row.get('transfers', ''), 'transfers', 12),
            'segments': clean_segments,
            'originPoint': _route_point(row.get('originPoint')),
            'destinationPoint': _route_point(row.get('destinationPoint')),
            'useEntrances': row.get('useEntrances',False),
            'calculation': _route_calculation(row.get('calculation')),
        }
        if any(clean[key] for key in ('name', 'campus', 'campusId', 'address')):
            output.append(clean)
    return output
