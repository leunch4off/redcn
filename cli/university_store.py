"""Lazy, source-owned Moscow university catalogue in a separate SQLite file.

The JSON resource is authoritative. Its transaction replaces only this module's
catalogue tables; card, phone profile and private dossier databases are untouched.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import unicodedata
from datetime import date
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT/'data/moscow-universities.json'
DATABASE = 'university-catalog.sqlite3'
MAX_CATALOG_BYTES = 6_000_000
IDENTIFIER = re.compile(r'[a-z0-9][a-z0-9_-]{0,120}\Z')
REGIONS = ('Москва', 'Московская область')


def fold(value):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFC', str(value)).casefold().replace('ё', 'е')).strip()


def _string(value, name, limit=2500, *, empty=False):
    if not isinstance(value, str) or len(value)>limit or '\x00' in value or (not empty and not value.strip()):
        raise ValueError('Неверное поле справочника вузов: '+name)
    return value


def _id(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError('Неверный ID справочника вузов')
    return value


def _aliases(value):
    if not isinstance(value, list) or len(value)>256:
        raise ValueError('Неверные варианты названия вуза')
    return [_string(item, 'alias', 600) for item in value]


def _references(value, known):
    if not isinstance(value, list) or not value or len(value)>100:
        raise ValueError('У записи вуза должны быть источники')
    if any(_id(item) not in known for item in value):
        raise ValueError('Неизвестный источник справочника вузов')


def read_catalog(path=None):
    path = Path(path or CATALOG)
    try:
        with path.open('rb') as handle:
            raw = handle.read(MAX_CATALOG_BYTES+1)
    except FileNotFoundError:
        return None
    if len(raw)>MAX_CATALOG_BYTES:
        raise ValueError('Справочник вузов превышает ограничение размера')
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as error:
        raise ValueError('Повреждён справочник вузов') from error
    if not isinstance(value, dict) or value.get('version')!=1:
        raise ValueError('Неизвестная версия справочника вузов')
    for key, maximum in (('universities', 2000), ('campuses', 10000), ('sources', 10000)):
        if not isinstance(value.get(key), list) or len(value[key])>maximum:
            raise ValueError('Неверный раздел справочника вузов: '+key)
    source_ids = set()
    for source in value['sources']:
        if not isinstance(source, dict):
            raise ValueError('Неверный источник вуза')
        identifier = _id(source.get('id'))
        if identifier in source_ids:
            raise ValueError('Повтор ID источника вуза')
        source_ids.add(identifier)
        _string(source.get('title'), 'source.title')
        url = urlsplit(_string(source.get('url'), 'source.url', 2000))
        if url.scheme not in ('https', 'http') or not url.hostname or url.username or url.password:
            raise ValueError('Неверная ссылка источника вуза')
        if source.get('status')!='verified':
            raise ValueError('В каталог допускаются только проверенные источники')
        verified = _string(source.get('verifiedAt'), 'source.verifiedAt', 10)
        try:
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', verified) or date.fromisoformat(verified)>date.today():
                raise ValueError()
        except ValueError as error:
            raise ValueError('Неверная дата подтверждения источника вуза') from error
    source_records = {source['id']: source for source in value['sources']}
    university_ids = set()
    for university in value['universities']:
        if not isinstance(university, dict):
            raise ValueError('Неверная запись вуза')
        identifier = _id(university.get('id'))
        if identifier in university_ids:
            raise ValueError('Повтор ID вуза')
        university_ids.add(identifier)
        _validate_place(university, source_records)
        evidence = university.get('chinaEvidence')
        if not isinstance(evidence, dict) or evidence.get('type') not in (
                'official-chinese-page', 'official-enrollment', 'official-student-testimonials',
                'official-international-admissions', 'institution-social-channel'):
            raise ValueError('Неверное основание включения вуза')
        _references(evidence.get('sourceIds'), source_ids)
        _validate_china_student_evidence(university, source_records)
        _validate_academic_unit_history(university, source_records)
    campus_ids = set()
    for campus in value['campuses']:
        if not isinstance(campus, dict):
            raise ValueError('Неверная запись кампуса')
        identifier = _id(campus.get('id'))
        if identifier in campus_ids or identifier in university_ids:
            raise ValueError('Повтор ID кампуса')
        campus_ids.add(identifier)
        if _id(campus.get('universityId')) not in university_ids:
            raise ValueError('Кампус ссылается на неизвестный вуз')
        _validate_place(campus, source_records)
        _string(campus.get('addressRu'), 'campus.addressRu', 1000)
        _validate_location_lifecycle(campus, source_records)
    campus_records = {row['id']: row for row in value['campuses']}
    for campus in value['campuses']:
        replacement = campus.get('locationLifecycle', {}).get('currentCampusId')
        if replacement:
            current = campus_records.get(replacement)
            if (current is None or current['universityId'] != campus['universityId']
                    or current.get('locationLifecycle', {}).get('status') in ('former-address', 'closed')):
                raise ValueError('Исторический адрес должен вести к действующему адресу того же вуза')
    return value, hashlib.sha256(raw).hexdigest()


def _validate_place(value, sources):
    _string(value.get('ru'), 'ru', 1000)
    _string(value.get('zh'), 'zh', 1000)
    _string(value.get('city'), 'city', 300)
    _aliases(value.get('aliases', []))
    if value.get('region') not in REGIONS:
        raise ValueError('Вуз или кампус должен быть в Москве или Московской области')
    if value.get('translationStatus') not in ('official', 'editorial'):
        raise ValueError('Неверный статус китайского названия')
    _references(value.get('sourceIds'), sources)
    _validate_coordinates(value, sources)


def _validate_coordinates(value, sources):
    """A published building point is not a measured entrance or geocoder guess."""
    coordinates = [value.get('latitude'), value.get('longitude')]
    if coordinates == [None, None]:
        if value.get('coordinateEvidence') is not None:
            raise ValueError('Основание координат без самой точки')
        return
    for number, bound in zip(coordinates, (90, 180)):
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) or abs(number)>bound:
            raise ValueError('Нужна полная допустимая пара координат кампуса')
    evidence = value.get('coordinateEvidence')
    if not isinstance(evidence, dict) or evidence.get('method')!='official-published-point' or evidence.get('scope') not in ('building', 'institution', 'campus'):
        raise ValueError('Для координат нужен первичный источник и область точки')
    if (evidence.get('boundCampusId') != value.get('id')
            or fold(evidence.get('boundAddressRu', '')) != fold(value.get('addressRu', ''))
            or evidence.get('accuracyScope') != 'published-'+evidence['scope']+'-point'):
        raise ValueError('Точность и координаты должны относиться к выбранному адресному объекту')
    _references(evidence.get('sourceIds'), sources)
    if any(identifier not in value['sourceIds'] for identifier in evidence['sourceIds']):
        raise ValueError('Источник координат должен относиться к выбранному корпусу')
    for identifier in evidence['sourceIds']:
        source = sources[identifier]
        if (value.get('id') not in source.get('campusIds', []) or 'published-coordinate' not in source.get('supports', [])
                or source.get('verifiedAt')!=evidence.get('checkedAt')
                or source.get('evidenceType')!='official-university'
                or source.get('institutionId')!=value.get('universityId')):
            raise ValueError('Источник не подтверждает точку именно выбранного корпуса на указанную дату')
    checked = evidence.get('checkedAt')
    try:
        if not isinstance(checked, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', checked) or date.fromisoformat(checked)>date.today():
            raise ValueError()
    except ValueError as error:
        raise ValueError('Неверная дата проверки координат') from error
    accuracy = evidence.get('accuracyMeters')
    if accuracy is None:
        if evidence.get('accuracyStatus')!='not-published':
            raise ValueError('Неизвестная точность должна быть явно обозначена')
    elif isinstance(accuracy, bool) or not isinstance(accuracy, (int, float)) or not math.isfinite(accuracy) or not 0<accuracy<=10000 or evidence.get('accuracyStatus')!='published':
        raise ValueError('Неверная опубликованная точность координат')
    _string(evidence.get('publishedPrecision'), 'coordinateEvidence.publishedPrecision', 200)
    if evidence.get('entranceVerified') is not False:
        raise ValueError('Точка корпуса не подтверждает конкретный доступный вход')


def _validate_location_lifecycle(campus, sources):
    """A former faculty address is not evidence that its building is closed."""
    evidence = campus.get('locationLifecycle')
    if evidence is None:
        return
    if not isinstance(evidence, dict) or evidence.get('status') not in ('current-listed', 'former-address', 'closed'):
        raise ValueError('Неверный статус адреса подразделения')
    _references(evidence.get('sourceIds'), sources)
    if any(identifier not in campus['sourceIds'] for identifier in evidence['sourceIds']):
        raise ValueError('История адреса должна подтверждаться источником этого объекта')
    checked = evidence.get('checkedAt')
    try:
        if not isinstance(checked, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', checked) or date.fromisoformat(checked)>date.today():
            raise ValueError()
    except ValueError as error:
        raise ValueError('Неверная дата проверки истории адреса') from error
    if any(sources[identifier]['verifiedAt'] != checked for identifier in evidence['sourceIds']):
        raise ValueError('Дата истории адреса не совпадает с проверкой источника')
    _string(evidence.get('noteRu'), 'locationLifecycle.noteRu', 2500)
    if evidence.get('status') == 'former-address':
        if _id(evidence.get('currentCampusId')) == campus['id']:
            raise ValueError('Исторический адрес не может заменять сам себя')
        if evidence.get('buildingClosed') is not False:
            raise ValueError('Переезд подразделения не подтверждает закрытие здания')
    elif evidence.get('status') == 'closed':
        scope = evidence.get('closureScope')
        if scope not in ('department', 'building') or not any(
                scope+'-closure' in sources[identifier].get('supports', [])
                for identifier in evidence['sourceIds']):
            raise ValueError('Закрытие подразделения или корпуса требует отдельного прямого подтверждения')
    if evidence.get('status') != 'former-address' and evidence.get('currentCampusId') is not None:
        raise ValueError('Только исторический адрес может иметь замену')
    if evidence.get('physicalAccessVerified') is not False:
        raise ValueError('Справочная публикация адреса не подтверждает доступность входа')


def _validate_academic_unit_history(university, sources):
    records=university.get('academicUnitHistory', [])
    if not isinstance(records,list) or len(records)>100:
        raise ValueError('Неверная история подразделений вуза')
    for record in records:
        if (not isinstance(record,dict) or record.get('status')!='closed'
                or record.get('closureScope')!='academic-unit-only'
                or record.get('effectiveClosureDate') is not None):
            raise ValueError('История подразделения не должна означать закрытие вуза или здания')
        for key in ('id','ru','zh','noteRu'):
            _string(record.get(key),'academicUnitHistory.'+key,2500)
        try:
            published=date.fromisoformat(record.get('documentDate',''))
            checked=date.fromisoformat(record.get('checkedAt',''))
            if published.isoformat()!=record['documentDate'] or checked.isoformat()!=record['checkedAt'] or published>checked or checked>date.today():
                raise ValueError()
        except (TypeError,ValueError) as error:
            raise ValueError('Неверная дата акта о закрытии подразделения') from error
        _references(record.get('sourceIds'),sources)
        for identifier in record['sourceIds']:
            source=sources[identifier]
            if (identifier not in university['sourceIds'] or source.get('institutionId')!=university['id']
                    or source.get('evidenceType')!='official-university'
                    or 'academic-unit-closure' not in source.get('supports',[])
                    or source.get('verifiedAt')!=record.get('checkedAt')
                    or source.get('publishedAt')!=record.get('documentDate')):
                raise ValueError('Закрытие подразделения требует отдельного первичного акта именно этого вуза')


def _validate_china_student_evidence(university, sources):
    evidence=university.get('chinaStudentEvidence')
    if evidence is None:
        return
    if not isinstance(evidence,dict) or set(evidence)!={'coverage','rankingAllowed','statistics','accounts'} or evidence['coverage']!='historical-institution-reported-non-exhaustive' or evidence['rankingAllowed'] is not False:
        raise ValueError('Исторические данные студентов не должны превращаться в рейтинг')
    ids=set()
    def dated_source(row, support):
        identifier=_id(row.get('id'))
        if identifier in ids:
            raise ValueError('Повтор ID исторического свидетельства')
        ids.add(identifier)
        _references(row.get('sourceIds'),sources)
        for key in ('publishedAt','verifiedAt'):
            text=_string(row.get(key),'chinaStudentEvidence.'+key,10)
            try:
                if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',text) or date.fromisoformat(text)>date.today():
                    raise ValueError()
            except ValueError as error:
                raise ValueError('Неверная дата исторического свидетельства студентов') from error
        if row['publishedAt']>row['verifiedAt']:
            raise ValueError('Проверка не может предшествовать публикации')
        for identifier in row['sourceIds']:
            source=sources[identifier]
            if identifier not in university['sourceIds'] or source.get('institutionId')!=university['id'] or source.get('evidenceType')!='official-university' or support not in source.get('supports',[]) or source.get('publishedAt')!=row['publishedAt'] or source.get('verifiedAt')!=row['verifiedAt']:
                raise ValueError('Историческое свидетельство должно иметь источник именно этого вуза и даты')
    if not isinstance(evidence['statistics'],list) or len(evidence['statistics'])>50 or not isinstance(evidence['accounts'],list) or len(evidence['accounts'])>50:
        raise ValueError('Слишком много исторических свидетельств')
    statistic_fields={'id','population','metric','value','relation','denominator','measurementDate','publishedAt','verifiedAt','programScope','campusScope','sourceIds','noteRu'}
    for row in evidence['statistics']:
        if not isinstance(row,dict) or set(row)!=statistic_fields or row['population']!='PRC-students' or row['metric']!='enrolled-students' or row['relation'] not in ('greater-than','reported-exact') or type(row['value']) is not int or not 0<=row['value']<=1_000_000 or row['denominator'] is not None or row['measurementDate'] is not None or row['programScope']!='not-separated-by-level' or row['campusScope']!='not-separated-by-campus':
            raise ValueError('Китайские обучающиеся, иностранный набор и гостевые программы считаются раздельно')
        _string(row.get('noteRu'),'chinaStudentEvidence.noteRu',1500)
        dated_source(row,'Chinese-nationality-enrollment')
    account_fields={'id','personName','programType','evidenceType','publishedAt','verifiedAt','sourceIds','summaryRu','scopeNoteRu'}
    for row in evidence['accounts']:
        if not isinstance(row,dict) or set(row)!=account_fields or row['evidenceType']!='official-published-firsthand-account' or row['programType'] not in ('degree-student','degree-alumnus','visiting-summer-school'):
            raise ValueError('Неверный тип опубликованной истории студента')
        for key,limit in (('personName',300),('summaryRu',1500),('scopeNoteRu',1500)):
            _string(row.get(key),'chinaStudentEvidence.'+key,limit)
        dated_source(row,'firsthand-student-account')


def _connect(directory):
    folder = Path(directory)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = folder/DATABASE
    connection = sqlite3.connect(path, timeout=5)
    path.chmod(0o600)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA foreign_keys=ON')
    connection.execute('PRAGMA busy_timeout=5000')
    connection.executescript('''
        CREATE TABLE IF NOT EXISTS catalog_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS universities (
            id TEXT PRIMARY KEY, region TEXT NOT NULL, evidence_type TEXT NOT NULL,
            search_text TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS campuses (
            id TEXT PRIMARY KEY, university_id TEXT NOT NULL REFERENCES universities(id),
            region TEXT NOT NULL, search_text TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sources (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS campuses_university ON campuses(university_id);
    ''')
    return connection


def _search_text(row, parent=None):
    values = [row['ru'], row['zh'], *row.get('aliases', []), row.get('addressRu', ''),
              row.get('addressZh', ''), row.get('city', ''), row.get('region', '')]
    values.extend(record['personName'] for record in row.get('chinaStudentEvidence',{}).get('accounts',[]))
    values.extend(record['ru'] for record in row.get('academicUnitHistory',[]))
    if parent:
        values.extend([parent['ru'], parent['zh'], *parent.get('aliases', [])])
    return fold('\n'.join(values))


def _open_synced(directory, path=None):
    loaded = read_catalog(path)
    if loaded is None:
        return None, None
    catalog, digest = loaded
    connection = _connect(directory)
    try:
        connection.execute('BEGIN IMMEDIATE')
        previous = connection.execute("SELECT value FROM catalog_meta WHERE key='sha256'").fetchone()
        if previous is None or previous[0]!=digest:
            connection.execute('DELETE FROM campuses')
            connection.execute('DELETE FROM universities')
            connection.execute('DELETE FROM sources')
            by_id = {row['id']: row for row in catalog['universities']}
            connection.executemany('INSERT INTO universities VALUES (?,?,?,?,?)',
                [(row['id'], row['region'], row['chinaEvidence']['type'], _search_text(row),
                  json.dumps(row, ensure_ascii=False)) for row in catalog['universities']])
            connection.executemany('INSERT INTO campuses VALUES (?,?,?,?,?)',
                [(row['id'], row['universityId'], row['region'], _search_text(row, by_id[row['universityId']]),
                  json.dumps(row, ensure_ascii=False)) for row in catalog['campuses']])
            connection.executemany('INSERT INTO sources VALUES (?,?)',
                [(row['id'], json.dumps(row, ensure_ascii=False)) for row in catalog['sources']])
            meta = {'sha256': digest, 'asOf': str(catalog.get('asOf', '')),
                    'note': str(catalog.get('note', '')), 'coverage': json.dumps(catalog.get('coverage'), ensure_ascii=False)}
            connection.executemany('INSERT OR REPLACE INTO catalog_meta VALUES (?,?)', list(meta.items()))
        connection.commit()
        return connection, catalog
    except Exception:
        connection.rollback()
        connection.close()
        raise


def _stats(connection):
    counts = {name: connection.execute('SELECT COUNT(*) FROM '+name).fetchone()[0]
              for name in ('universities', 'campuses', 'sources')}
    meta = dict(connection.execute('SELECT key,value FROM catalog_meta').fetchall())
    regions = {region: {'universities': connection.execute('SELECT COUNT(*) FROM universities WHERE region=?', (region,)).fetchone()[0],
                        'campuses': connection.execute('SELECT COUNT(*) FROM campuses WHERE region=?', (region,)).fetchone()[0]}
               for region in REGIONS}
    evidence = dict(connection.execute('SELECT evidence_type,COUNT(*) FROM universities GROUP BY evidence_type').fetchall())
    return {'available': True, **counts, 'regions': regions, 'chinaEvidence': evidence,
            'catalogSha256': meta.get('sha256'), 'asOf': meta.get('asOf'),
            'note': meta.get('note'), 'coverage': json.loads(meta.get('coverage', 'null')), 'exhaustive': False}


def stats(directory, catalog_path=None):
    connection, _ = _open_synced(directory, catalog_path)
    if connection is None:
        return {'available': False, 'universities': 0, 'campuses': 0, 'sources': 0, 'exhaustive': False}
    try:
        return _stats(connection)
    finally:
        connection.close()


def search(directory, query, *, kind='all', offset=0, limit=40, catalog_path=None):
    if not isinstance(query, str) or not query.strip() or len(query)>200 or '\x00' in query:
        raise ValueError('Поиск вузов: от 1 до 200 символов')
    if kind not in ('all', 'university', 'campus'):
        raise ValueError('Неизвестный раздел вузов')
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1<=limit<=100:
        raise ValueError('Размер страницы вузов: от 1 до 100')
    if isinstance(offset, bool) or not isinstance(offset, int) or not 0<=offset<=1_000_000:
        raise ValueError('Неверная позиция страницы вузов')
    connection, catalog = _open_synced(directory, catalog_path)
    if connection is None:
        return {'entries': [], 'total': 0, 'offset': offset, 'limit': limit,
                'stats': {'available': False, 'universities': 0, 'campuses': 0, 'sources': 0}}
    try:
        q = fold(query)
        parents = {row['id']: row for row in catalog['universities']}
        sources = {row['id']: row for row in catalog['sources']}
        matches = []
        for name, entry_kind in (('universities', 'university'), ('campuses', 'campus')):
            if kind not in ('all', entry_kind):
                continue
            for stored in connection.execute('SELECT payload FROM '+name+' WHERE instr(search_text,?)>0', (q,)):
                row = json.loads(stored[0])
                related = [sources[key] for key in row['sourceIds']]
                parent = parents.get(row.get('universityId'))
                matches.append({**row, 'kind': entry_kind, 'sourceId': 'moscow-universities',
                    'reviewed': row['translationStatus']=='official', 'sources': related,
                    'sourceUrl': related[0]['url'] if related else None,
                    **({'parentRu': parent['ru'], 'parentZh': parent['zh']} if parent else {})})
        def order(row):
            names = [fold(row['ru']), fold(row['zh']), *map(fold, row.get('aliases', []))]
            return (0 if q in names else 1 if any(value.startswith(q) for value in names) else 2,
                    row['kind']!='university', fold(row['ru']), row['id'])
        matches.sort(key=order)
        return {'entries': matches[offset:offset+limit], 'total': len(matches),
                'offset': offset, 'limit': limit, 'stats': _stats(connection)}
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache-dir', default=str(Path.home()/'.cache/redcn'))
    parser.add_argument('--query')
    parser.add_argument('--kind', choices=('all', 'university', 'campus'), default='all')
    parser.add_argument('--limit', type=int, default=40)
    args = parser.parse_args()
    result = search(args.cache_dir, args.query, kind=args.kind, limit=args.limit) if args.query else stats(args.cache_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__=='__main__':
    main()
