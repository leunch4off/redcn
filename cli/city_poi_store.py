"""Read-only, source-owned city reference. Never reads cards or geocodes.

Published marker/route/meeting points retain their actual scope. A place is
neither an apartment advantage nor a verified current accessible entrance.
"""
from datetime import date
import json
import math
from pathlib import Path
import re
from urllib.parse import urlsplit

CATALOG=Path(__file__).resolve().parents[1]/'data/city-pois.json'
CATEGORIES=frozenset(('chinese-grocery','chinese-restaurant','park'))
SOURCE_TYPES=frozenset(('owner','owner-embedded-public-map','official-city-document'))
SCOPES={'chinese-grocery':{'named-store-marker'},'chinese-restaurant':{'restaurant-route-destination'},'park':{'historical-park-meeting-point','historical-park-main-entrance-point'}}
MAX_BYTES=1_000_000


def _text(value, name, limit=2000):
    if not isinstance(value,str) or not value.strip() or len(value)>limit or '\x00' in value:
        raise ValueError('Неверное поле справочника мест: '+name)
    return value


def _date(value):
    if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):
        raise ValueError('Нужна дата проверки городского места')
    try:
        if date.fromisoformat(value)>date.today():
            raise ValueError()
    except ValueError as error:
        raise ValueError('Неверная дата источника городского места') from error


def read_catalog(path=None):
    with Path(path or CATALOG).open('rb') as handle:
        raw=handle.read(MAX_BYTES+1)
    if len(raw)>MAX_BYTES:
        raise ValueError('Справочник мест превышает ограничение размера')
    value=json.loads(raw)
    if not isinstance(value,dict) or value.get('version')!=1 or value.get('coverage',{}).get('exhaustive') is not False:
        raise ValueError('Неверная версия или область справочника мест')
    _date(value.get('verifiedAt'))
    if not isinstance(value.get('sources'),list) or len(value['sources'])>5000 or not isinstance(value.get('places'),list) or len(value['places'])>5000:
        raise ValueError('Неверный список городских мест/источников')
    sources={}
    for source in value['sources']:
        identifier=_text(source.get('id'),'source.id',120)
        if identifier in sources or source.get('type') not in SOURCE_TYPES:
            raise ValueError('Повтор или неизвестный тип источника места')
        url=urlsplit(_text(source.get('url'),'source.url'))
        if url.scheme!='https' or not url.hostname or url.username or url.password or url.port not in (None,443):
            raise ValueError('Неверная ссылка источника места')
        _date(source.get('verifiedAt'))
        if source.get('publishedAt') is not None:
            _date(source['publishedAt'])
        sources[identifier]=source
    for source in sources.values():
        if source['type']=='owner-embedded-public-map' and sources.get(source.get('ownerSourceId'),{}).get('type')!='owner':
            raise ValueError('У встроенной карты должен быть источник владельца')
    ids=set()
    for row in value['places']:
        identifier=_text(row.get('id'),'id',120)
        if identifier in ids or row.get('category') not in CATEGORIES or row.get('translationStatus')!='editorial-category' or row.get('city')!='Москва':
            raise ValueError('Неверная или повторная запись городского места')
        ids.add(identifier)
        for key in ('ru','zh','addressRu','noteRu'):
            _text(row.get(key),key)
        if not isinstance(row.get('aliases'),list) or len(row['aliases'])>100:
            raise ValueError('Неверные алиасы городского места')
        for alias in row['aliases']:
            _text(alias,'alias',300)
        if not isinstance(row.get('sourceIds'),list) or not row['sourceIds'] or any(identifier not in sources for identifier in row['sourceIds']):
            raise ValueError('Неизвестный источник городского места')
        for key,bound in (('latitude',90),('longitude',180)):
            number=row.get(key)
            if isinstance(number,bool) or not isinstance(number,(int,float)) or not math.isfinite(number) or abs(number)>bound:
                raise ValueError('Неверная пара координат городского места')
        evidence=row.get('coordinateEvidence',{})
        if evidence.get('scope') not in SCOPES[row['category']] or evidence.get('accuracyMeters') is not None or evidence.get('accuracyStatus')!='not-published' or evidence.get('originalOrder') not in ('latitude-longitude','longitude-latitude'):
            raise ValueError('Неверная область или точность опубликованной точки')
        _date(evidence.get('verifiedAt'))
        if not isinstance(evidence.get('sourceIds'),list) or not evidence['sourceIds'] or any(identifier not in row['sourceIds'] or sources[identifier]['verifiedAt']!=evidence['verifiedAt'] for identifier in evidence['sourceIds']):
            raise ValueError('Точка должна ссылаться на датированный источник этого места')
        if row.get('openingStatus')!='unknown' or row.get('accessibility')!='unknown':
            raise ValueError('Текущее открытие/доступность этими источниками не подтверждены')
    return value


def stats(directory=None,catalog_path=None):
    value=read_catalog(catalog_path)
    return {'places':len(value['places']),'categories':{key:sum(row['category']==key for row in value['places'])for key in sorted(CATEGORIES)},'sources':len(value['sources']),'verifiedAt':value['verifiedAt'],'exhaustive':False}


def _fold(value):
    return re.sub(r'\s+',' ',str(value).casefold().replace('ё','е')).strip()


def search(directory,query='',*,category='',offset=0,limit=30,catalog_path=None):
    if not isinstance(query,str) or len(query)>200 or '\x00' in query or not isinstance(category,str) or category and category not in CATEGORIES:
        raise ValueError('Неверный поиск городских мест')
    if isinstance(offset,bool) or not isinstance(offset,int) or not 0<=offset<=10000 or isinstance(limit,bool) or not isinstance(limit,int) or not 1<=limit<=100:
        raise ValueError('Неверная страница городских мест')
    value=read_catalog(catalog_path);sources={row['id']:row for row in value['sources']};q=_fold(query)
    rows=[row for row in value['places'] if (not category or row['category']==category) and (not q or any(q in _fold(text)for text in [row['ru'],row['zh'],row['addressRu'],*row['aliases']]))]
    return {'entries':[{**row,'sourceId':'city-pois','sourceUrl':sources[row['sourceIds'][0]]['url'],'reviewed':False,'sources':[sources[id]for id in row['sourceIds']]}for row in rows[offset:offset+limit]],'total':len(rows),'offset':offset,'limit':limit,'exhaustive':False,'verifiedAt':value['verifiedAt']}
