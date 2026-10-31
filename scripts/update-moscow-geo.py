#!/usr/bin/env python3
"""Refresh Moscow geographic snapshots without inventing names or completeness.

Standard-library only. Raw exports remain outside git; normalized catalog and
the equivalent browser module are reproducible from this script.
"""
import argparse
import csv
import datetime as dt
import hashlib
import io
import json
from pathlib import Path
import re
import tempfile
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
HH_URL = 'https://api.hh.ru/metro/1'
OSM_URL = 'https://overpass-api.de/api/interpreter'
OSM_QUERY = '''[out:json][timeout:160];area(3600102269)->.m;
(way(area.m)[highway][name];nwr(area.m)[highway=bus_stop][name];
nwr(area.m)[railway=tram_stop][name];nwr(area.m)[public_transport=platform][name];);
out center tags;'''
RANEPA = 'https://www.ranepa.ru/upload/doc/1/Гид%20для%20иностранных%20студентов%20китай%2001271400.pdf'
REVIEWED = {
    'юго-западная': ('西南', RANEPA),
    'тропарево': ('特罗帕廖沃', RANEPA),
    'проспект вернадского': ('维尔纳德斯基大道', RANEPA),
}


def norm(value):
    return re.sub(r'\s+', ' ', str(value or '').strip()).casefold().replace('ё', 'е')


def download(url, body=None):
    request = urllib.request.Request(url, data=body, headers={
        'User-Agent': 'redcn-geography/1.0 (+https://github.com/leunch4off/redcn)',
        'Accept': 'application/json'})
    with urllib.request.urlopen(request, timeout=180) as response:
        content = response.read(50_000_001)
    if len(content) > 50_000_000:
        raise ValueError('Export exceeds 50 MB; use a scoped Moscow export')
    return content


def reviewed(row):
    if norm(row['ru']) in REVIEWED:
        row['zh'], row['translationSource'] = REVIEWED[norm(row['ru'])]
        row['translationStatus'] = 'reviewed'
    return row


def apply_translations(rows):
    """Curated official terms win; community nomenclature remains visibly unreviewed."""
    supplements = {}
    path = ROOT / 'data/metro-zh-supplement.json'
    if path.exists():
        payload = json.loads(path.read_text(encoding='utf-8'))
        source_urls = {s['id']: s.get('revision_url') or s['url'] for s in payload.get('sources', [])}
        licenses = {s['id']: s.get('license', payload.get('license', 'See source')) for s in payload.get('sources', [])}
        for entry in payload.get('stations', []):
            if entry.get('ru') and entry.get('zh'):
                supplements[norm(entry['ru'])] = (entry['zh'], source_urls.get(entry.get('source'), entry.get('source')), 'community', licenses.get(entry.get('source'), 'See source'))
    studio = ROOT / 'data/moscow-geo-translations.json'
    if studio.exists():
        payload = json.loads(studio.read_text(encoding='utf-8'))
        for ru, zh in payload.get('entries', {}).items():
            supplements[norm(ru)] = (zh, payload['source'], 'studio_dictionary', None)
    for row in rows:
        if norm(row['ru']) in REVIEWED:
            reviewed(row)
        elif row.get('kind') == 'metro' and norm(row['ru']) in supplements:
            row['zh'], row['translationSource'], row['translationStatus'], license_name = supplements[norm(row['ru'])]
            if license_name: row['translationLicense'] = license_name
    return rows


def hh_records(raw):
    payload = json.loads(raw)
    if payload.get('id') != '1' or not isinstance(payload.get('lines'), list):
        raise ValueError('Expected HeadHunter Moscow metro/1 export')
    rows, lines = [], []
    for line in payload['lines']:
        lines.append({'id': str(line['id']), 'ru': line['name'], 'color': line.get('hex_color', ''), 'serviceStatus': 'not_verified'})
        for station in line.get('stations', []):
            rows.append(reviewed({'id': 'hh:' + str(station['id']), 'kind': 'metro',
                'ru': station['name'], 'zh': '', 'translationStatus': 'untranslated',
                'source': 'hh-metro', 'lines': [str(line['id'])], 'serviceStatus': 'not_verified',
                'lat': station.get('lat'), 'lng': station.get('lng')}))
    if not rows:
        raise ValueError('Empty metro export rejected')
    return rows, lines


def osm_records(raw):
    payload = json.loads(raw)
    if payload.get('remark'):
        raise ValueError('Overpass returned incomplete/error response: ' + payload['remark'])
    if not isinstance(payload.get('elements'), list):
        raise ValueError('Expected Overpass JSON export')
    rows, streets = [], {}
    for element in payload['elements']:
        tags = element.get('tags', {})
        name = str(tags.get('name', '')).strip()
        if not name:
            continue
        stop = tags.get('highway') == 'bus_stop' or tags.get('railway') == 'tram_stop' or tags.get('public_transport') == 'platform'
        kind = 'stop' if stop else 'street' if tags.get('highway') and element['type'] == 'way' else None
        if not kind:
            continue
        center = element.get('center', element)
        zh = tags.get('name:zh-Hans') or tags.get('name:zh') or ''
        row = reviewed({'id': f"osm:{element['type']}:{element['id']}", 'kind': kind,
            'ru': name, 'zh': zh, 'translationStatus': 'source_supplied' if zh else 'untranslated',
            'source': 'osm-moscow', 'lat': center.get('lat'), 'lng': center.get('lon')})
        if kind == 'street':
            key = norm(name)
            if key in streets:
                streets[key]['segments'] += 1
                if zh and not streets[key]['zh']:
                    streets[key].update(zh=zh, translationStatus='source_supplied')
                continue
            row['segments'] = 1
            streets[key] = row
        else:
            rows.append(row)
    rows.extend(streets.values())
    if not streets or not any(r['kind'] == 'stop' for r in rows):
        raise ValueError('Expected named streets AND stops in Moscow export')
    return rows, payload.get('osm3s', {}).get('timestamp_osm_base')


def official_records(path, kind):
    """Accept Moscow portal JSON rows or UTF-8 CSV; never infer missing names."""
    raw = path.read_bytes()
    text = raw.decode('utf-8-sig')
    if path.suffix.lower() == '.csv':
        values = list(csv.DictReader(io.StringIO(text), delimiter=';' if text.partition('\n')[0].count(';') > text.partition('\n')[0].count(',') else ','))
    else:
        payload = json.loads(text)
        values = payload if isinstance(payload, list) else payload.get('rows') or payload.get('Rows') or payload.get('items') or []
    names = {'metro': ('NameOfStation', 'StationName', 'name', 'Name'),
             'street': ('Name', 'SIMPLE_NAME', 'FULL_NAME', 'name', 'Наименование'),
             'stop': ('Name', 'StationName', 'name', 'Наименование')}
    rows = []
    for index, item in enumerate(values):
        cells = item.get('Cells', item)
        name = next((cells[k] for k in names[kind] if isinstance(cells.get(k), str) and cells[k].strip()), None)
        if not name:
            continue
        rows.append(reviewed({'id': 'mos:' + kind + ':' + str(item.get('global_id', item.get('Number', index))),
            'kind': kind, 'ru': name.strip(), 'zh': '', 'translationStatus': 'untranslated',
            'source': 'mos-export-' + kind}))
    if not rows:
        raise ValueError(f'No {kind} names recognized in {path}; export rejected')
    return rows, raw


def source(identifier, url, raw, coverage, **extra):
    return {'id': identifier, 'url': url, 'fetchedAt': dt.datetime.now(dt.timezone.utc).isoformat(),
        'sha256': hashlib.sha256(raw).hexdigest(), 'coverage': coverage, **extra}


def render_module(catalog):
    template = (ROOT / 'scripts/geo-catalog-runtime.mjs').read_text(encoding='utf-8')
    return '// Generated by scripts/update-moscow-geo.py. Edit the source catalog via the importer.\nexport const GEO_CATALOG = ' + json.dumps(catalog, ensure_ascii=False, separators=(',', ':')) + ';\n' + template


def atomic_write(path, content):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, prefix='.' + path.name, delete=False) as target:
            temporary = Path(target.name)
            target.write(content)
        temporary.replace(path)
    finally:
        if temporary and temporary.exists(): temporary.unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hh-file', type=Path)
    parser.add_argument('--osm-file', type=Path)
    parser.add_argument('--download-hh', action='store_true')
    parser.add_argument('--download-osm', action='store_true')
    for kind in ('metro', 'street', 'stop'):
        parser.add_argument('--' + kind + '-export', type=Path)
    parser.add_argument('--official-source-url', help='Source URL of supplied official Moscow export')
    parser.add_argument('--official-as-of', help='Date supplied by export publisher (YYYY-MM-DD)')
    parser.add_argument('--render-only', action='store_true')
    args = parser.parse_args(argv)
    output = ROOT / 'data/moscow-geo.json'
    catalog = json.loads(output.read_text(encoding='utf-8')) if output.exists() else {'version': 1, 'records': [], 'lines': [], 'sources': []}
    sources = {s['id']: s for s in catalog['sources']}
    rows = catalog['records']
    if args.hh_file or args.download_hh:
        raw = args.hh_file.read_bytes() if args.hh_file else download(HH_URL)
        metro, catalog['lines'] = hh_records(raw)
        rows = [r for r in rows if r['kind'] != 'metro'] + metro
        sources['hh-metro'] = source('hh-metro', HH_URL, raw, 'All stations returned by HeadHunter Moscow metro/1; no claim of official transport completeness', publisher='HeadHunter', license='HeadHunter API terms; geographic factual records')
    if args.osm_file or args.download_osm:
        raw = args.osm_file.read_bytes() if args.osm_file else download(OSM_URL, urllib.parse.urlencode({'data': OSM_QUERY}).encode())
        places, timestamp = osm_records(raw)
        rows = [r for r in rows if r['kind'] not in ('street', 'stop')] + places
        sources['osm-moscow'] = source('osm-moscow', OSM_URL, raw, 'Named OSM highway ways and transport platforms inside Moscow administrative relation 102269, including New Moscow; OSM snapshot, not an official exhaustive register', publisher='OpenStreetMap contributors', license='ODbL 1.0', attribution='© OpenStreetMap contributors', attributionUrl='https://www.openstreetmap.org/copyright', asOf=timestamp, query=OSM_QUERY)
    for kind in ('metro', 'street', 'stop'):
        path = getattr(args, kind + '_export')
        if path:
            if not args.official_source_url or not args.official_as_of:
                parser.error('Official exports require --official-source-url and --official-as-of')
            places, raw = official_records(path, kind)
            rows = [r for r in rows if r['kind'] != kind] + places
            identifier = 'mos-export-' + kind
            sources[identifier] = source(identifier, args.official_source_url, raw, 'All recognized names in supplied export; completeness is not inferred', publisher='Moscow open data portal', asOf=args.official_as_of)
    if not rows:
        parser.error('Supply --hh-file/--osm-file or download flags for an initial catalog')
    rows = apply_translations(rows)
    supplement = ROOT / 'data/metro-zh-supplement.json'
    if supplement.exists(): catalog['translationSources'] = json.loads(supplement.read_text(encoding='utf-8')).get('sources', [])
    ids = set()
    for row in rows:
        if row['id'] in ids:
            raise ValueError('Duplicate ID: ' + row['id'])
        ids.add(row['id'])
    for row in rows:
        if row['kind']=='metro': row.setdefault('serviceStatus','not_verified')
    for line in catalog.get('lines',[]):line.setdefault('serviceStatus','not_verified')
    changed=any((args.hh_file,args.download_hh,args.osm_file,args.download_osm,args.metro_export,args.street_export,args.stop_export))
    catalog.update(records=sorted(rows, key=lambda r: (r['kind'], norm(r['ru']), r['id'])), sources=list(sources.values()),
        updatedAt=dt.datetime.now(dt.timezone.utc).isoformat() if changed or not catalog.get('updatedAt') else catalog['updatedAt'], coverage='Source snapshots; official exhaustive Moscow registry not asserted')
    module = render_module(catalog)
    atomic_write(output, json.dumps(catalog, ensure_ascii=False, indent=2) + '\n')
    atomic_write(ROOT / 'web/geo-catalog.mjs', module)
    print(json.dumps({'records': len(rows), **{k: sum(r['kind'] == k for r in rows) for k in ('metro', 'street', 'stop')}, 'sources': list(sources)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
