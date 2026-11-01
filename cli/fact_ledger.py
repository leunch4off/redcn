"""Bounded listing validation, private fact versions and import-cache-only cleanup.

No server/controller is constructed here. Version snapshots contain an explicit
fact allowlist, never source HTML, arbitrary notes, credentials or image bytes.
"""
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import sqlite3


NUMERIC_FIELDS = frozenset('price deposit commission commissionAmount area rooms bedrooms livingRooms kitchen living bedroomArea occupants floor floors combinedWcs separateWcs buildingYear entrances ceiling balconies loggias prepayMonths contractMonths rentFreeMonths minMonths registrationMonths registrationPeople latitude longitude'.split())
BOOLEAN_FIELDS = frozenset('ready newRenovation separate children pets shortTerm pickup luggage arrival support isDemo eligibleForNew'.split())
LIST_FIELDS = frozenset('features photos photoItems layouts transitDetails sourceDatesList photoEvidence photoAutoFeatures photoRejectedFeatures relatedOffers universityRoutes oneTimeFees'.split())
MAP_FIELDS = frozenset('featureCounts photoEdits sourceAudit sourceReadiness sourceDates photoFeatureBaseline studioProfile serviceConfirmations'.split())
TEXT_FIELDS = frozenset('url source name district complex districtZh metro walk university uniTime uniMode route details style layout transit universities highlight description utilities registration registrationPolicy childrenPolicy petPolicy smokingPolicy guestPolicy subletPolicy shortTermPolicy campus region audience seller commissionStatus commissionSource commissionCurrency depositCurrency pricePeriod priceCurrency phraseNotes city address renovation windows bathroom buildingType buildingSeries floorType heating emergency leaseTerm sourceDescription railways lift resultId editorIdentity titleFormula sellerSource publicationStatus'.split())
TEXT_OR_NUMBER_FIELDS = frozenset(('walk', 'uniTime'))
TRACKED_FACTS = tuple('price pricePeriod priceCurrency deposit depositCurrency commission commissionStatus commissionAmount commissionCurrency utilities prepayMonths contractMonths leaseTerm area living kitchen bedrooms livingRooms rooms floor floors layout metro walk transit university uniTime uniMode route campus universities universityRoutes latitude longitude address district city seller buildingType buildingYear renovation bathroom ceiling windows balconies loggias ready newRenovation features featureCounts childrenPolicy petPolicy smokingPolicy guestPolicy subletPolicy registrationPolicy registration registrationMonths registrationPeople pickup luggage arrival support'.split())


def _stamp(value=None):
    if value is None:
        return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
    if not isinstance(value, str):
        raise ValueError('Неверная дата версии фактов')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise ValueError('Неверная дата версии фактов') from None
    if parsed.tzinfo is None:
        raise ValueError('У даты версии нужен часовой пояс')
    return value


def _json_value(value, depth=0, active=None):
    if depth > 20:
        raise ValueError('Слишком глубокая структура карточки')
    if value is None or type(value) in (str, bool):
        if isinstance(value, str) and (len(value) > 8_000_000 or '\x00' in value):
            raise ValueError('Слишком длинный или некорректный текст карточки')
        return
    if type(value) in (int, float):
        if not math.isfinite(value):
            raise ValueError('Нечисловое значение карточки')
        return
    if type(value) not in (dict, list):
        raise ValueError('Карточка должна содержать только JSON-значения')
    active = set() if active is None else active
    if id(value) in active:
        raise ValueError('Циклическая структура карточки')
    if len(value) > 5000:
        raise ValueError('Слишком большая коллекция карточки')
    active.add(id(value))
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or key in ('__proto__', 'constructor', 'prototype'):
                raise ValueError('Неверное имя поля карточки')
            _json_value(item, depth + 1, active)
    else:
        for item in value:
            _json_value(item, depth + 1, active)
    active.remove(id(value))


def validate_listing_fields(fields):
    if type(fields) is not dict:
        raise ValueError('Поля объявления должны быть объектом')
    _json_value(fields)
    problems = []
    for key, value in fields.items():
        if value is None:
            continue  # Missing old source facts remain unknown, never zero.
        if key in NUMERIC_FIELDS:
            if type(value) not in (str, int, float):
                problems.append(key + ': требуется число или пустое значение')
                continue
            # Legacy form controls also retain source/manual prose (for example
            # "52 м²; комиссия 50%"). Keep that text for operator/publication
            # review without coercing it into a fabricated numeric fact.
            number = _numeric_fact(value)
            if isinstance(value, str) and value.strip().lower() in ('nan', 'infinity', '+infinity', '-infinity', 'inf', '+inf', '-inf'):
                problems.append(key + ': требуется конечное число')
            elif key in ('latitude', 'longitude') and isinstance(value, str) and value.strip() and number is None:
                problems.append(key + ': требуются числовые координаты')
            elif number is not None:
                minimum, maximum = (-90, 90) if key == 'latitude' else (-180, 180) if key == 'longitude' else (0, 1_000_000_000)
                if not math.isfinite(number) or not minimum <= number <= maximum:
                    problems.append(key + ': число вне допустимого диапазона')
        elif key in BOOLEAN_FIELDS and type(value) is not bool:
            problems.append(key + ': требуется логическая отметка')
        elif key in LIST_FIELDS and type(value) is not list:
            problems.append(key + ': требуется список')
        elif key in MAP_FIELDS and type(value) is not dict:
            problems.append(key + ': требуется объект')
        elif key in TEXT_OR_NUMBER_FIELDS and type(value) not in (str, int, float):
            problems.append(key + ': требуется текст или число минут')
        elif key in TEXT_FIELDS and key not in TEXT_OR_NUMBER_FIELDS and type(value) is not str:
            problems.append(key + ': требуется текст')
    for key in ('features', 'photos', 'photoAutoFeatures', 'photoRejectedFeatures'):
        if isinstance(fields.get(key), list) and any(not isinstance(item, str) for item in fields[key]):
            problems.append(key + ': список должен содержать текстовые значения')
    if isinstance(fields.get('featureCounts'), dict):
        for value in fields['featureCounts'].values():
            if type(value) not in (str, int, float) or not re.fullmatch(r'\d+(?:\.\d+)?', str(value)):
                problems.append('featureCounts: неверное количество предметов')
                break
    if problems:
        raise ValueError('Неверные поля объявления: ' + ' · '.join(problems))
    if len(json.dumps(fields, ensure_ascii=False)) > 16_000_000:
        raise ValueError('Поля объявления больше 16 МБ')
    return fields


def validate_result_fields(result):
    if type(result) is not dict:
        raise ValueError('Карточка должна быть объектом')
    if 'fields' in result:
        validate_listing_fields(result['fields'])
    for key in ('body', 'tags', 'title'):
        if key in result and result[key] is not None and not isinstance(result[key], str):
            raise ValueError(key + ': требуется текст публикации')
    if 'titles' in result and result['titles'] is not None and not isinstance(result['titles'], str) and (not isinstance(result['titles'], list) or any(not isinstance(row, str) for row in result['titles'])):
        raise ValueError('titles: требуется текст или список текстовых заголовков')
    return result


def _numeric_fact(value):
    if type(value) in (int, float):
        return value
    if isinstance(value, str):
        literal = value.strip()
        if re.fullmatch(r'-?(?:\d+|\d{1,3}(?:\s+\d{3})+)(?:[.,]\d+)?', literal):
            compact = re.sub(r'\s+', '', literal)
            return float(compact.replace(',', '.'))
    return None


def fact_snapshot(fields):
    validate_listing_fields(fields)
    snapshot={key: fields[key] for key in TRACKED_FACTS if key in fields}
    def scalars(row, keys):
        return {key:row[key][:2000] if isinstance(row[key],str) else row[key] for key in keys if key in row and type(row[key]) in (str,int,float,bool)}
    if isinstance(snapshot.get('universityRoutes'),list):
        keys='name university campus campusId address latitude longitude time mode transport line routeDistanceKm method checkedAt sourceUrl accessMinutes egressMinutes transfers'.split()
        routes=[]
        for row in snapshot['universityRoutes'][:10]:
            if not isinstance(row,dict):continue
            route=scalars(row,keys)
            for group,limit,allowed in (('stops',3,('name','address','latitude','longitude')),('segments',8,('mode','transport','from','to','minutes'))):
                if isinstance(row.get(group),list):route[group]=[scalars(child,allowed) for child in row[group][:limit] if isinstance(child,dict)]
            routes.append(route)
        snapshot['universityRoutes']=routes
    return json.loads(json.dumps(snapshot, ensure_ascii=False))


def compare_fact_snapshots(previous, current):
    def canonical(value, key):
        if key in NUMERIC_FIELDS or key in TEXT_OR_NUMBER_FIELDS:
            numeric = _numeric_fact(value)
            if numeric is not None:return numeric
        if key == 'features' and isinstance(value,list):return sorted(set(value))
        if key == 'featureCounts' and isinstance(value,dict):return {name:_numeric_fact(count) for name,count in value.items()}
        return value
    return [{'field': key, 'before': previous.get(key), 'after': current.get(key)}
            for key in TRACKED_FACTS if canonical(previous.get(key),key) != canonical(current.get(key),key)
            or (key in previous) != (key in current)]


def record_fact_revision(connection, result_id, fields, *, actor='operator', kind='save', at=None):
    current = fact_snapshot(fields)
    if not isinstance(actor, str) or not actor.strip() or len(actor) > 100:
        raise ValueError('У версии должен быть автор до 100 знаков')
    if kind not in ('save', 'import', 'manual', 'confirm', 'restore'):
        raise ValueError('Неверный тип версии фактов')
    at = _stamp(at)
    connection.execute('CREATE TABLE IF NOT EXISTS fact_revisions (result_id TEXT NOT NULL, revision INTEGER NOT NULL, stamp TEXT NOT NULL, actor TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(result_id,revision))')
    row = connection.execute('SELECT revision,payload FROM fact_revisions WHERE result_id=? ORDER BY revision DESC LIMIT 1', (result_id,)).fetchone()
    previous = json.loads(row[1]).get('fields', {}) if row else {}
    changes = compare_fact_snapshots(previous, current)
    if row and not changes:
        return json.loads(row[1])
    version = {'version': 1, 'id': result_id + ':' + str((row[0] + 1) if row else 1),
               'revision': (row[0] + 1) if row else 1, 'resultId': result_id,
               'at': at, 'actor': actor.strip(), 'kind': kind,
               'fields': current, 'changes': changes}
    connection.execute('INSERT INTO fact_revisions VALUES (?,?,?,?,?,?)', (result_id, version['revision'], at, version['actor'], kind, json.dumps(version, ensure_ascii=False)))
    return version


def list_fact_revisions(directory, result_id, limit=100):
    if not re.fullmatch(r'[a-f0-9]{32}', str(result_id)):
        raise ValueError('Неверный ID карточки')
    path = Path(directory) / 'listings.sqlite3'
    if not path.is_file():
        return []
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=3) as connection:
        connection.execute('PRAGMA query_only=ON')
        exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='fact_revisions'").fetchone()
        if not exists:
            return []
        return [json.loads(row[0]) for row in connection.execute('SELECT payload FROM fact_revisions WHERE result_id=? ORDER BY revision DESC LIMIT ?', (result_id, min(500, max(1, int(limit)))))]


def clear_import_cache(directory):
    path = Path(directory) / 'listings.sqlite3'
    if not path.is_file():
        return {'cleared': 0, 'scope': 'import-cache'}
    with sqlite3.connect(path, timeout=3) as connection:
        exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cache'").fetchone()
        count = connection.execute('DELETE FROM cache').rowcount if exists else 0
        return {'cleared': count, 'scope': 'import-cache'}
