"""Lazy, source-owned Moscow university catalogue in a separate SQLite file.

The JSON resource is authoritative. Its transaction replaces only this module's
catalogue tables; card, phone profile and private dossier databases are untouched.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import unicodedata
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
        _string(source.get('verifiedAt'), 'source.verifiedAt', 50)
    university_ids = set()
    for university in value['universities']:
        if not isinstance(university, dict):
            raise ValueError('Неверная запись вуза')
        identifier = _id(university.get('id'))
        if identifier in university_ids:
            raise ValueError('Повтор ID вуза')
        university_ids.add(identifier)
        _validate_place(university, source_ids)
        evidence = university.get('chinaEvidence')
        if not isinstance(evidence, dict) or evidence.get('type') not in (
                'official-chinese-page', 'official-enrollment', 'official-student-testimonials',
                'official-international-admissions', 'institution-social-channel'):
            raise ValueError('Неверное основание включения вуза')
        _references(evidence.get('sourceIds'), source_ids)
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
        _validate_place(campus, source_ids)
        _string(campus.get('addressRu'), 'campus.addressRu', 1000)
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
