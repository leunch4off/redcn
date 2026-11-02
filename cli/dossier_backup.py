"""Explicit private dossier transfer and deliberately selected client packages.

These functions require the caller's existing desktop/paired-phone authorization.
They never discover user files, fetch source pages or enable a photo runtime.
"""
import base64
import hashlib
import io
import json
import math
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import tempfile
import time
import zipfile

import card_assets
import photo_store
import redcn

MAX_BUNDLE = 128 * 1024 * 1024
MAX_UNPACKED = 384 * 1024 * 1024
MAX_MANIFEST = 8 * 1024 * 1024
MAX_ENTRIES = 1200
FORMAT = 'redcn-private-dossier'


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')


def _card_payload(directory, card_id):
    card_assets._card(directory, card_id)
    with sqlite3.connect('file:'+str(Path(directory)/'listings.sqlite3')+'?mode=ro', uri=True) as db:
        db.execute('PRAGMA query_only=ON')
        row=db.execute('SELECT updated,payload FROM results WHERE id=?',(card_id,)).fetchone()
    return row[0],json.loads(row[1])


def _photos(card):
    fields=card.get('fields') or {}; listing=card.get('listing') or {}
    # A one-photo editor snapshot must not hide the rest of the source album in
    # a private backup. Selection/exclusion affects publication, not preservation.
    values=[]
    for holder,key in ((fields,'photos'),(listing,'images')):
        rows=holder.get(key)
        if isinstance(rows,list):values.extend(rows)
    for holder in (fields,listing):
        rows=holder.get('photoItems')
        if isinstance(rows,list):values.extend(row for row in rows if isinstance(row,dict) and not row.get('isLayout') and not row.get('isCianLayout'))
    urls=[]
    for row in values:
        value=(row.get('sourceUrl') or row.get('originalUrl') or row.get('url')) if isinstance(row,dict) else row
        if isinstance(value,str):
            value=redcn.allowed_url(value,images=True,resolve=False)
            if value not in urls:urls.append(value)
    return urls


def photo_count(manifest):
    """Count album identities, not alternative byte sizes of the same image."""
    return len(_photos(manifest['card']))


def _variant_passports(directory,card):
    from photo_variants import export_decision
    from phone_relay import photo_source_items
    sources=photo_source_items(card)
    return [value for url in _photos(card) if (source:=sources.get(url)) is not None
        and source.get('photoId') is not None and (value:=export_decision(directory,url,source=source)) is not None]


def _card_state(directory,card_id):
    """Hash related metadata, not just the listing's last text-save timestamp.

    The caller's file lock serializes normal attachment/export writes. This
    signature also catches archive/design/metadata changes from another process
    or a concurrent editor that does not share that lock.
    """
    stamp,card=_card_payload(directory,card_id)
    parts=[stamp,card]
    with card_assets._db(directory) as db:
        for table,clause,args in (
            ('card_archive','id=?',(card_id,)),('attachments','card_id=?',(card_id,)),
            ('exports','card_id=?',(card_id,)),('restored_designs','card_id=?',(card_id,)),
            ('designs','id=1',()),('design_images','1=1',()),
            ('export_publication_policy','export_id IN (SELECT id FROM exports WHERE card_id=?)',(card_id,)),
            ('export_design_snapshots','export_id IN (SELECT id FROM exports WHERE card_id=?)',(card_id,))):
            parts.append([table,db.execute('SELECT * FROM '+table+' WHERE '+clause+' ORDER BY 1',args).fetchall()])
    with photo_store._connection(directory) as db:
        originals=[]
        from photo_variants import variant_decision
        from phone_relay import photo_source_items
        sources=photo_source_items(card)
        for url in _photos(card):
            row=db.execute('SELECT id FROM photo_urls WHERE url=?',(url,)).fetchone()
            parts.append(['url',url,row])
            if row:originals.append(row[0])
            source=sources.get(url)
            decision=variant_decision(directory,url,source=source) if source and source.get('photoId') is not None else None
            if decision:
                parts.append(['variant',decision])
                for candidate in decision.get('candidates',[]):
                    if candidate.get('status')=='available':
                        originals.append(candidate['id'])
                        parts.append(['variant-url',candidate['url'],db.execute('SELECT id FROM photo_urls WHERE url=?',(candidate['url'],)).fetchone()])
        # Metadata for a derivative can change while the original's card stays
        # unchanged; retain the relationship in the consistency check too.
        for identifier,mime,raw in db.execute('SELECT id,mime,metadata FROM assets ORDER BY id'):
            meta=json.loads(raw)
            if identifier in originals or meta.get('originalId') in originals:
                parts.append(['asset',identifier,mime,raw])
        for row in db.execute('SELECT original_id,asset_id,verification,metadata,created FROM crop_links ORDER BY original_id,asset_id,verification'):
            if row[0] in originals:parts.append(['crop',*row])
    with sqlite3.connect('file:'+str(Path(directory)/'listings.sqlite3')+'?mode=ro',uri=True) as db:
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='fact_revisions'").fetchone():
            parts.append(['facts',db.execute('SELECT * FROM fact_revisions WHERE result_id=? ORDER BY revision',(card_id,)).fetchall()])
    return hashlib.sha256(_json(parts)).hexdigest()


def _make_zip(manifest, files):
    encoded=_json(manifest)
    if len(encoded)>MAX_MANIFEST:raise ValueError('Описание досье превышает 8 МБ')
    if len(files)+1>MAX_ENTRIES or sum(map(len,files.values()))+len(encoded)>MAX_UNPACKED:
        raise ValueError('Досье превышает лимит 384 МБ или 1200 файлов')
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=2) as bundle:
        bundle.writestr('manifest.json',encoded)
        for name,content in sorted(files.items()):bundle.writestr(name,content)
    if output.tell()>MAX_BUNDLE:raise ValueError('ZIP превышает 128 МБ. Перенесите крупные сканы отдельными файлами.')
    return output.getvalue()


@card_assets._serialize_files
def export_private(directory, card_id):
    """Capture one card and its referenced bytes, never the workspace database."""
    stamp,card=_card_payload(directory,card_id)
    initial_state=_card_state(directory,card_id)
    manifest={'version':1,'type':FORMAT,'created':time.time(),'sourceCardId':card_id,
              'card':card,'archive':card_assets.archive(directory,card_id),
              'attachments':[],'exports':[],'media':[],'mediaUrls':[],'cropLinks':[],'photoVariantDecisions':[],
              'design':None,'designImages':[],'factRevisions':[],'files':[]}
    with sqlite3.connect('file:'+str(Path(directory)/'listings.sqlite3')+'?mode=ro',uri=True) as db:
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='fact_revisions'").fetchone():
            manifest['factRevisions']=[json.loads(row[0]) for row in db.execute('SELECT payload FROM fact_revisions WHERE result_id=? ORDER BY revision',(card_id,))]
    files={}
    def blob(kind,digest):
        name='blobs/'+kind+'/'+digest
        if name not in files:
            content=card_assets._read(directory,digest) if kind=='card' else photo_store.asset(directory,digest)[0]
            files[name]=content
        return name
    with card_assets._db(directory) as db:
        for row in db.execute('SELECT id,name,category,mime,size,created,digest FROM attachments WHERE card_id=? ORDER BY created,id',(card_id,)):
            item=dict(zip(('id','name','category','mime','bytes','created','digest'),row));item['blob']=blob('card',item['digest']);manifest['attachments'].append(item)
        for row in db.execute('SELECT e.id,e.kind,e.name,e.mime,e.size,e.created,e.digest,p.version,s.payload FROM exports e LEFT JOIN export_publication_policy p ON p.export_id=e.id LEFT JOIN export_design_snapshots s ON s.export_id=e.id WHERE e.card_id=? ORDER BY e.created,e.id',(card_id,)):
            item=dict(zip(('id','kind','name','mime','bytes','created','digest','sourcePolicyVersion'),row[:8]));item['template']=json.loads(row[8]) if row[8] else None;item['blob']=blob('card',item['digest']);manifest['exports'].append(item)
        design=db.execute('SELECT payload FROM restored_designs WHERE card_id=?',(card_id,)).fetchone()
        if not design:design=db.execute('SELECT payload FROM designs WHERE id=1').fetchone()
        if design:
            manifest['design']=json.loads(design[0])
        referenced_images=set()
        for template in [manifest['design'],*[row.get('template') for row in manifest['exports']]]:
            if not isinstance(template,dict):continue
            brand=template.get('brand') or {}
            referenced_images.update(brand.get(key) for key in ('logoAssetId','avatarAssetId') if brand.get(key))
        for identifier in sorted(referenced_images):
            row=db.execute('SELECT id,role,name,mime,width,height,size,created,digest,original_digest,original_mime,original_size FROM design_images WHERE id=?',(identifier,)).fetchone()
            if not row:raise ValueError('Изображение дизайна отсутствует; исправьте дизайн перед копированием')
            item=dict(zip(('id','role','name','mime','width','height','bytes','created','digest','originalDigest','originalMime','originalBytes'),row))
            item['blob']=blob('card',item['digest']);item['originalBlob']=blob('card',item['originalDigest']);manifest['designImages'].append(item)
    manifest['photoVariantDecisions']=_variant_passports(directory,card)
    decisions={row['canonicalUrl']:row for row in manifest['photoVariantDecisions']}
    original_ids=set();url_ids={}
    with photo_store._connection(directory) as db:
        for url in _photos(card):
            row=db.execute('SELECT id FROM photo_urls WHERE url=?',(url,)).fetchone()
            if row:url_ids[url]=row[0];original_ids.add(row[0])
            else:
                decision=decisions.get(url,{})
                if decision.get('status')!='selected' or not decision.get('selected',{}).get('quality',{}).get('resolutionEligible'):
                    raise ValueError('Не все фотографии сохранены на Mac. Загрузите оригиналы карточки перед полной резервной копией.')
        for decision in manifest['photoVariantDecisions']:
            for candidate in decision['candidates']:
                if candidate['status']!='available':continue
                previous=url_ids.get(candidate['url'])
                if previous and previous!=candidate['id']:raise ValueError('Исходник изменился после выбора варианта фото. Проверьте фото перед копированием.')
                url_ids[candidate['url']]=candidate['id'];original_ids.add(candidate['id'])
        manifest['mediaUrls']=[{'url':url,'id':identifier} for url,identifier in sorted(url_ids.items())]
        selected_media={}
        for identifier,mime,raw in db.execute('SELECT id,mime,metadata FROM assets ORDER BY id'):
            meta=json.loads(raw)
            if identifier not in original_ids and meta.get('originalId') not in original_ids:continue
            selected_media[identifier]={'id':identifier,'mime':mime,'metadata':meta,'blob':blob('media',identifier)}
        for original,identifier,verification,raw,created in db.execute('SELECT original_id,asset_id,verification,metadata,created FROM crop_links ORDER BY created'):
            if original not in original_ids:continue
            meta=json.loads(raw)
            manifest['cropLinks'].append({'originalId':original,'assetId':identifier,'verification':verification,'metadata':meta,'created':created})
            if identifier not in original_ids:selected_media[identifier]={'id':identifier,'mime':meta['mime'],'metadata':meta,'blob':blob('media',identifier)}
        manifest['media']=list(selected_media.values())
    manifest['files']=[{'path':name,'bytes':len(content),'sha256':hashlib.sha256(content).hexdigest()} for name,content in sorted(files.items())]
    # Never make a mixed revision when the operator edits while ZIP is built.
    result=_make_zip(manifest,files)
    if _card_payload(directory,card_id)[0]!=stamp or _card_state(directory,card_id)!=initial_state:
        raise ValueError('Карточка, досье, дизайн или фотографии изменились во время копирования. Повторите экспорт.')
    return result


def _read_zip(content):
    if not isinstance(content,bytes) or not 1<=len(content)<=MAX_BUNDLE:raise ValueError('Нужен ZIP до 128 МБ')
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as bundle:
            entries=bundle.infolist();names=[item.filename for item in entries]
            if len(entries)>MAX_ENTRIES or len(names)!=len(set(names)) or sum(item.file_size for item in entries)>MAX_UNPACKED:
                raise ValueError('Архив слишком велик или содержит повторяющиеся пути')
            for item in entries:
                if item.flag_bits&1 or stat.S_ISLNK(item.external_attr>>16) or item.is_dir():raise ValueError('Ссылки, каталоги и шифрованные ZIP не поддерживаются')
                if item.filename!='manifest.json' and not re.fullmatch(r'blobs/(?:card|media)/[a-f0-9]{64}',item.filename):raise ValueError('Недопустимый путь внутри ZIP')
            if 'manifest.json' not in names or bundle.getinfo('manifest.json').file_size>MAX_MANIFEST:raise ValueError('Нет корректного manifest.json до 8 МБ')
            manifest=json.loads(bundle.read('manifest.json'))
            if not isinstance(manifest,dict) or manifest.get('version')!=1 or manifest.get('type')!=FORMAT:raise ValueError('Нужна приватная резервная копия redcn версии 1')
            rows=manifest.get('files')
            if not isinstance(rows,list):raise ValueError('Отсутствует перечень файлов')
            files={}
            for row in rows:
                if not isinstance(row,dict) or set(row)!= {'path','bytes','sha256'} or not isinstance(row['path'],str) or row['path'] in files:raise ValueError('Неверный перечень файлов')
                name=row['path']
                if name not in names or not re.fullmatch(r'blobs/(?:card|media)/[a-f0-9]{64}',name) or type(row['bytes']) is not int or row['bytes']<1:raise ValueError('Файл отсутствует или его размер неверен')
                data=bundle.read(name)
                if len(data)!=row['bytes'] or hashlib.sha256(data).hexdigest()!=row['sha256'] or name.rsplit('/',1)[-1]!=row['sha256']:raise ValueError('Контрольная сумма файла не совпадает')
                files[name]=data
            if set(names)!=set(files)|{'manifest.json'}:raise ValueError('ZIP содержит неописанные файлы')
            return manifest,files
    except (zipfile.BadZipFile,UnicodeDecodeError,json.JSONDecodeError,RuntimeError,KeyError,RecursionError) as error:
        raise ValueError('Повреждённая резервная копия') from error


def _identifier(value):
    if not isinstance(value,str) or not re.fullmatch(r'[a-f0-9]{32}',value):raise ValueError('Неверный ID в резервной копии')
    return value


def _validate_manifest(manifest,files):
    from fact_ledger import validate_result_fields,validate_listing_fields
    card=manifest.get('card');archive=manifest.get('archive');old_id=_identifier(manifest.get('sourceCardId'))
    if not isinstance(card,dict) or not isinstance(card.get('fields'),dict) or not isinstance(card.get('titles'),list) or not all(isinstance(title,str) for title in card['titles']) or not isinstance(card.get('body'),str) or not isinstance(card.get('tags'),str):raise ValueError('Неверная карточка в копии')
    validate_result_fields(card)
    revisions=manifest.get('factRevisions',[])
    if not isinstance(revisions,list) or len(revisions)>5000:raise ValueError('Неверная история фактов')
    previous=0
    for revision in revisions:
        if not isinstance(revision,dict) or type(revision.get('revision')) is not int or revision['revision']<=previous or not isinstance(revision.get('at'),str) or len(revision['at'])>100 or not isinstance(revision.get('actor'),str) or not 1<=len(revision['actor'])<=100:raise ValueError('Неверная версия фактов')
        validate_listing_fields(revision.get('fields'));previous=revision['revision']
    if not isinstance(archive,dict) or not isinstance(archive.get('status'),str) or archive['status'] not in card_assets._STATUSES or not isinstance(archive.get('notes'),str) or len(archive['notes'])>20000:raise ValueError('Неверное досье в копии')
    ids={old_id}
    def rows(name,maximum):
        value=manifest.get(name)
        if not isinstance(value,list) or len(value)>maximum or any(not isinstance(row,dict) for row in value):raise ValueError('Неверный список '+name)
        return value
    def check_blob(row,key='blob',digest='digest'):
        name=row.get(key)
        if name not in files or row.get(digest)!=name.rsplit('/',1)[-1]:raise ValueError('Неверная ссылка на содержимое')
        return files[name]
    for row in rows('attachments',100):
        identifier=_identifier(row.get('id'))
        if identifier in ids:raise ValueError('Повторяющийся ID')
        ids.add(identifier);name=card_assets._name(row.get('name'))
        if not isinstance(row.get('category'),str) or row['category'] not in card_assets._CATEGORIES:raise ValueError('Неверная категория файла')
        data=check_blob(row)
        if card_assets._validate_file(name,data)!=row.get('mime') or len(data)!=row.get('bytes'):raise ValueError('Тип или размер вложения не совпадает')
    for row in rows('exports',200):
        identifier=_identifier(row.get('id'))
        if identifier in ids:raise ValueError('Повторяющийся ID')
        ids.add(identifier);card_assets._name(row.get('name'));data=check_blob(row)
        if row.get('kind') not in ('html','pdf','intro','outro') or len(data)!=row.get('bytes'):raise ValueError('Неверный готовый материал')
        expected={'html':'text/html; charset=utf-8','pdf':'application/pdf','intro':'image/png','outro':'image/png'}[row['kind']]
        if row.get('mime')!=expected:raise ValueError('Неверный тип готового материала')
        if row['kind']=='html':card_assets.validate_html(data.decode('utf-8'))
        elif row['kind']=='pdf' and not data.startswith(b'%PDF-'):raise ValueError('Неверный PDF')
        elif row['kind'] in ('intro','outro'):
            if photo_store.image_mime(data)!='image/png':raise ValueError('Неверный PNG')
            image=photo_store.open_photo(data);image.close()
    media=rows('media',MAX_ENTRIES);media_ids=set()
    for row in media:
        identifier=row.get('id');meta=row.get('metadata')
        if not isinstance(identifier,str) or not re.fullmatch(r'[a-f0-9]{64}',identifier) or identifier in media_ids or not isinstance(meta,dict) or meta.get('id')!=identifier or meta.get('kind') not in ('original','crop'):raise ValueError('Неверная фотография')
        media_ids.add(identifier)
        if row.get('blob')!='blobs/media/'+identifier or row['blob'] not in files:raise ValueError('Нет байтов фотографии')
        data=files[row['blob']]
        if photo_store.image_mime(data)!=row.get('mime'):raise ValueError('Неверный тип фотографии')
        image=photo_store.open_photo(data)
        try:
            if image.width!=meta.get('width') or image.height!=meta.get('height'):raise ValueError('Размер фотографии не совпадает')
        finally:image.close()
    media_by_id={row['id']:row for row in media}
    for row in media:
        if row['metadata']['kind']=='crop' and media_by_id.get(row['metadata'].get('originalId'),{}).get('metadata',{}).get('kind')!='original':raise ValueError('Оригинал кадрирования отсутствует')
    url_ids={}
    for row in rows('mediaUrls',2400):
        if not isinstance(row.get('url'),str):raise ValueError('Неверный URL фотографии')
        redcn.allowed_url(row['url'],images=True,resolve=False)
        if row['url'] in url_ids:raise ValueError('Повторный URL фотографии')
        url_ids[row['url']]=row.get('id')
        if media_by_id.get(row.get('id'),{}).get('metadata',{}).get('kind')!='original':raise ValueError('URL фотографии не связан с оригиналом')
    passports=manifest.get('photoVariantDecisions',[])
    if not isinstance(passports,list) or len(passports)>400:raise ValueError('Неверный список решений о вариантах фото')
    from photo_variants import validate_portable_decision,_urls as variant_urls
    from phone_relay import photo_source_items
    sources=photo_source_items(card)
    assets={identifier:{'content':files[row['blob']],'mime':row['mime'],'metadata':row['metadata']} for identifier,row in media_by_id.items()}
    album=set(_photos(card));decisions={};allowed_urls=set(album)
    for passport in passports:
        canonical=passport.get('canonicalUrl') if isinstance(passport,dict) else None
        if canonical not in album or canonical in decisions:raise ValueError('Решение о вариантах не относится к уникальному фото этой карточки')
        source=sources.get(canonical)
        if not source or source.get('photoId') is None or variant_urls(canonical,source)[2]!=passport.get('groupSha'):
            raise ValueError('Решение о вариантах не связано с исходной группой фотографий этой карточки')
        value=validate_portable_decision(canonical,passport,assets=assets)
        decisions[canonical]=value
        for candidate in value['candidates']:
            if candidate['status']!='available':continue
            allowed_urls.add(candidate['url'])
            if url_ids.get(candidate['url'])!=candidate['id']:raise ValueError('URL варианта не связан с сохранёнными исходными байтами')
    for canonical in album:
        if canonical in url_ids:continue
        value=decisions.get(canonical,{})
        if value.get('status')!='selected' or not value.get('selected',{}).get('quality',{}).get('resolutionEligible'):
            raise ValueError('В резервной копии отсутствует оригинал или подтверждённый доступный вариант фото')
    if not set(url_ids)<=allowed_urls:raise ValueError('В копии есть URL фотографии вне исходного альбома и проверенных вариантов')
    manifest['photoVariantDecisions']=list(decisions.values())
    links=manifest.get('cropLinks',[])
    if not isinstance(links,list) or len(links)>5000:raise ValueError('Неверные связи кадрированных копий')
    for row in links:
        if not isinstance(row,dict) or not isinstance(row.get('metadata'),dict):raise ValueError('Неверная связь кадра')
        meta=row['metadata']
        if media_by_id.get(row.get('originalId'),{}).get('metadata',{}).get('kind')!='original' or row.get('assetId') not in media_ids or meta.get('id')!=row['assetId'] or meta.get('kind')!='crop' or meta.get('originalId')!=row['originalId'] or meta.get('verification')!=row.get('verification'):raise ValueError('Связь кадра не соответствует байтам оригинала')
        if type(row.get('created')) not in (int,float) or not math.isfinite(row['created']) or row['created']<0:raise ValueError('Неверная дата кадрирования')
    image_ids=set()
    for row in rows('designImages',200):
        identifier=_identifier(row.get('id'))
        if identifier in ids:raise ValueError('Повторяющийся ID')
        ids.add(identifier);image_ids.add(identifier);card_assets._name(row.get('name'))
        if row.get('role') not in ('logo','avatar'):raise ValueError('Неверный вид изображения бренда')
        for key,digest in (('blob','digest'),('originalBlob','originalDigest')):
            data=check_blob(row,key,digest);image=photo_store.open_photo(data)
            try:
                expected=row.get('mime' if key=='blob' else 'originalMime')
                if photo_store.image_mime(data)!=expected:raise ValueError('Неверный тип изображения бренда')
                if key=='blob' and (image.width!=row.get('width') or image.height!=row.get('height')):raise ValueError('Неверные размеры изображения бренда')
            finally:image.close()
    for template in [manifest.get('design'),*[row.get('template') for row in manifest['exports']]]:
        if template is not None:
            if not isinstance(template,dict):raise ValueError('Неверный дизайн')
            if template.get('version')==2:
                from publication_schema import validate_publication_template
                validate_publication_template(template)
                if any(value and value not in image_ids for key,value in template.get('brand',{}).items() if key.endswith('AssetId')):raise ValueError('Нет изображения из дизайна')
            else:
                with tempfile.TemporaryDirectory() as temp:card_assets.save_design(temp,template)
    # Timestamps are data only; IDs and file paths never come from them.
    for section in ('attachments','exports','designImages'):
        for row in manifest[section]:
            value=row.get('created')
            if type(value) not in (int,float) or not math.isfinite(value) or value<0:raise ValueError('Неверная дата файла')
    return ids


def _remap(value,mapping):
    if isinstance(value,list):return [_remap(item,mapping) for item in value]
    if isinstance(value,dict):
        # Platform photo identities and their observed URL witnesses are not
        # redcn storage IDs, even if an opaque value happens to be 32 hex chars.
        return {key:json.loads(_json(item)) if key in ('photoId','photoItems') else _remap(item,mapping) for key,item in value.items()}
    if isinstance(value,str):
        if value in mapping:return mapping[value]
        if value.startswith('/api/'):
            return '/'.join(mapping.get(part,part) for part in value.split('/'))
    return value


@card_assets._serialize_files
def restore_private(directory, content, *, return_mapping=False):
    manifest,files=_read_zip(content);identifiers=_validate_manifest(manifest,files)
    root=Path(directory);root.mkdir(parents=True,exist_ok=True)
    for name in ('media','media.sqlite3','listings.sqlite3','photo-variant-decisions.sqlite3'):
        if (root/name).is_symlink():raise ValueError('Каталог или база восстановления не могут быть ссылками')
    with card_assets._db(directory) as db:pass
    with photo_store._connection(directory) as db:
        for row in manifest['mediaUrls']:
            existing=db.execute('SELECT id FROM photo_urls WHERE url=?',(row['url'],)).fetchone()
            if existing and existing[0]!=row['id']:raise ValueError('На этом Mac уже сохранена другая версия URL фотографии. Восстановите копию в отдельное рабочее хранилище.')
    from photo_variants import export_decision,portable_record,validate_portable_decision,_connection as variant_connection
    variant_rows=[]
    for value in manifest['photoVariantDecisions']:
        previous=export_decision(directory,value['canonicalUrl'],group_sha=value['groupSha'])
        canonical,group,payload,digest=portable_record(value)
        if previous is not None:
            if json.dumps(previous,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)!=payload:
                raise ValueError('На этом Mac сохранён другой выбор вариантов фото. Восстановите копию в отдельное рабочее хранилище.')
        variant_rows.append((canonical,group,time.time(),payload,digest))
    if variant_rows:
        with variant_connection(directory,write=True):pass
    with redcn.open_cache(directory) as db:db.execute('CREATE TABLE IF NOT EXISTS results(id TEXT PRIMARY KEY,updated REAL,payload TEXT)')
    mapping={identifier:secrets.token_hex(16) for identifier in identifiers};new_id=mapping[manifest['sourceCardId']]
    card=_remap(manifest['card'],mapping);card['fields']['resultId']=new_id
    # Content-addressed blobs may be written first. On transaction failure no
    # card references them; existing bytes are never overwritten or removed.
    for name,data in files.items():
        kind,digest=name.split('/')[1:]
        if kind=='card':card_assets._write(directory,data)
        else:
            folder=root/'media';folder.mkdir(mode=0o700,exist_ok=True)
            target=folder/digest
            if target.is_symlink():raise ValueError('Недопустимый путь фотографии')
            if target.exists():
                if hashlib.sha256(target.read_bytes()).hexdigest()!=digest:raise ValueError('Существующая фотография повреждена; восстановление остановлено')
            else:
                with tempfile.NamedTemporaryFile(dir=folder,delete=False) as output:output.write(data);temporary=Path(output.name)
                temporary.chmod(0o600);temporary.replace(target)
    connection=sqlite3.connect(root/'listings.sqlite3',timeout=15)
    try:
        connection.execute('ATTACH DATABASE ? AS dossiers',(str(root/'card-assets'/'dossiers.sqlite3'),))
        connection.execute('ATTACH DATABASE ? AS media',(str(root/'media.sqlite3'),))
        if variant_rows:connection.execute('ATTACH DATABASE ? AS variants',(str(root/'photo-variant-decisions.sqlite3'),))
        connection.execute('BEGIN IMMEDIATE')
        stamp=time.time()
        connection.execute('INSERT INTO results(id,updated,payload) VALUES(?,?,?)',(new_id,stamp,_json(card).decode()))
        from fact_ledger import record_fact_revision
        if manifest.get('factRevisions'):
            connection.execute('CREATE TABLE IF NOT EXISTS fact_revisions(result_id TEXT NOT NULL,revision INTEGER NOT NULL,stamp TEXT NOT NULL,actor TEXT NOT NULL,kind TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(result_id,revision))')
            for previous in manifest['factRevisions']:
                version=_remap(previous,mapping)
                version.update(id=new_id+':'+str(previous['revision']),resultId=new_id,
                    actor=('backup:'+previous['actor'])[:100],kind='restore',importedFromBackup=True,sourceActor=previous['actor'],sourceKind=previous.get('kind'))
                connection.execute('INSERT INTO fact_revisions VALUES(?,?,?,?,?,?)',(new_id,version['revision'],version['at'],version['actor'],'restore',_json(version).decode()))
        record_fact_revision(connection,new_id,card.get('fields',{}),actor='backup-restore',kind='restore')
        archive=manifest['archive']
        connection.execute('INSERT INTO dossiers.card_archive(id,status,notes,updated) VALUES(?,?,?,?)',(new_id,archive['status'],archive['notes'],stamp))
        for row in manifest['attachments']:
            connection.execute('INSERT INTO dossiers.attachments(id,card_id,name,category,mime,size,created,digest) VALUES(?,?,?,?,?,?,?,?)',(mapping[row['id']],new_id,row['name'],row['category'],row['mime'],row['bytes'],row['created'],row['digest']))
        for row in manifest['exports']:
            connection.execute('INSERT INTO dossiers.exports(id,card_id,kind,name,mime,size,created,digest) VALUES(?,?,?,?,?,?,?,?)',(mapping[row['id']],new_id,row['kind'],row['name'],row['mime'],row['bytes'],row['created'],row['digest']))
            if row.get('template') is not None:connection.execute('INSERT INTO dossiers.export_design_snapshots(export_id,payload,created) VALUES(?,?,?)',(mapping[row['id']],_json(_remap(row['template'],mapping)).decode(),row['created']))
            # An imported archive cannot certify arbitrary PDF/PNG text. Retain
            # its private bytes and require regeneration for future client use.
        for row in manifest['designImages']:
            connection.execute('INSERT INTO dossiers.design_images VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(mapping[row['id']],row['role'],row['name'],row['mime'],row['width'],row['height'],row['bytes'],row['created'],row['digest'],row['originalDigest'],row['originalMime'],row['originalBytes']))
        for row in manifest['media']:
            previous=connection.execute('SELECT metadata FROM media.assets WHERE id=?',(row['id'],)).fetchone()
            if not previous:connection.execute('INSERT INTO media.assets(id,mime,metadata) VALUES(?,?,?)',(row['id'],row['mime'],_json(row['metadata']).decode()))
            elif row['metadata']['kind']=='original' and json.loads(previous[0]).get('kind')!='original':connection.execute('UPDATE media.assets SET mime=?,metadata=? WHERE id=?',(row['mime'],_json(row['metadata']).decode(),row['id']))
        for row in manifest.get('cropLinks',[]):
            connection.execute('INSERT OR IGNORE INTO media.crop_links(original_id,asset_id,verification,metadata,created) VALUES(?,?,?,?,?)',(row['originalId'],row['assetId'],row['verification'],_json(row['metadata']).decode(),row['created']))
        for row in manifest['mediaUrls']:
            existing=connection.execute('SELECT id FROM media.photo_urls WHERE url=?',(row['url'],)).fetchone()
            if existing and existing[0]!=row['id']:raise ValueError('Исходник URL фотографии изменился во время восстановления. Используйте отдельное хранилище.')
            connection.execute('INSERT OR IGNORE INTO media.photo_urls(url,id) VALUES(?,?)',(row['url'],row['id']))
        if variant_rows:
            assets={row['id']:{'content':files[row['blob']],'mime':row['mime'],'metadata':row['metadata']} for row in manifest['media']}
            for record in variant_rows:
                previous=connection.execute('SELECT payload,sha256 FROM variants.decisions WHERE canonical_url=? AND group_sha=? ORDER BY id DESC LIMIT 1',(record[0],record[1])).fetchone()
                if previous:
                    if hashlib.sha256(previous[0].encode()).hexdigest()!=previous[1]:raise ValueError('Существующее решение о вариантах повреждено')
                    try:existing=validate_portable_decision(record[0],json.loads(previous[0]),assets=assets)
                    except ValueError as error:raise ValueError('Выбор вариантов фото изменился во время восстановления. Используйте отдельное хранилище.') from error
                    if portable_record(existing)[2]!=record[3]:raise ValueError('Выбор вариантов фото изменился во время восстановления. Используйте отдельное хранилище.')
                else:connection.execute('INSERT INTO variants.decisions(canonical_url,group_sha,created,payload,sha256) VALUES(?,?,?,?,?)',record)
        design=_remap(manifest['design'],mapping)
        connection.execute('INSERT INTO dossiers.restored_designs(card_id,payload,created) VALUES(?,?,?)',(new_id,_json(design).decode(),stamp))
        connection.execute('INSERT INTO dossiers.restored_manifests(card_id,payload,created) VALUES(?,?,?)',(new_id,_json(manifest).decode(),stamp))
        connection.commit()
    except Exception:
        connection.rollback();raise
    finally:connection.close()
    return {'result_id':new_id,'attachments':len(manifest['attachments']),'exports':len(manifest['exports']),
            'photos':photo_count(manifest),'restoredDesign':design is not None,
            'photoVariantDecisions':len(manifest['photoVariantDecisions']),
            'clientMaterialsRequireRegeneration':bool(manifest['exports']),
            **({'idMap':mapping} if return_mapping else {})}


def get_restored_design(directory,card_id):
    card_assets._card(directory,card_id)
    with card_assets._db(directory) as db:row=db.execute('SELECT payload FROM restored_designs WHERE card_id=?',(card_id,)).fetchone()
    return {'template':json.loads(row[0]) if row else None}


def client_options(directory,card_id):
    archive=card_assets.archive(directory,card_id)
    return {'attachments':[dict(row,shareable=row['category']!='contract') for row in archive['attachments']],
            'exports':card_assets.list_exports(directory,card_id),
            'notesIncluded':False,'contractsIncluded':False}


@card_assets._serialize_files
def export_client(directory,card_id,selection):
    from client_publication import client_card,PUBLICATION_POLICY_VERSION
    from client_bundle_consistency import snapshot,require_current
    revision=snapshot(directory,card_id)
    _,raw=_card_payload(directory,card_id);card=client_card(raw)
    if not isinstance(selection,dict) or set(selection)-{'attachmentIds','exportIds'}:raise ValueError('Нужен явный выбор файлов')
    for key in ('attachmentIds','exportIds'):
        if not isinstance(selection.get(key),list) or len(selection[key])>100:raise ValueError('Выберите до 100 уникальных файлов каждого вида')
        for identifier in selection[key]:_identifier(identifier)
        if len(selection[key])!=len(set(selection[key])):raise ValueError('Не выбирайте файл дважды')
    files={};manifest={'version':1,'type':'redcn-client-dossier','created':time.time(),'publicationPolicyVersion':PUBLICATION_POLICY_VERSION,'cardRevisionSha256':revision,'files':[]}
    with card_assets._db(directory) as db:
        for index,identifier in enumerate(selection['attachmentIds'],1):
            row=db.execute('SELECT name,category,digest,mime FROM attachments WHERE card_id=? AND id=?',(card_id,identifier)).fetchone()
            if not row:raise ValueError('Выбранное вложение не принадлежит этой квартире')
            if row[1]=='contract':raise ValueError('Договоры остаются приватными и не входят в клиентскую копию досье')
            name='attachments/'+str(index).zfill(3)+'-'+card_assets._name(row[0]);files[name]=card_assets._read(directory,row[2])
            manifest['files'].append({'name':name,'category':row[1],'mime':row[3]})
        for index,identifier in enumerate(selection['exportIds'],1):
            row=db.execute('SELECT 1 FROM exports WHERE card_id=? AND id=?',(card_id,identifier)).fetchone()
            if not row:raise ValueError('Выбранный материал не принадлежит этой квартире')
            content,mime,label=card_assets.export_asset(directory,identifier)
            name='materials/'+str(index).zfill(3)+'-'+card_assets._name(label);files[name]=content
            manifest['files'].append({'name':name,'category':'publication','mime':mime})
    files['post.txt']='\n\n'.join([*(card.get('titles') or [])[:1],card.get('body',''),card.get('tags','')]).encode()
    for row in manifest['files']:
        data=files[row['name']];row.update(bytes=len(data),sha256=hashlib.sha256(data).hexdigest())
    result=_make_zip(manifest,files)
    require_current(directory,card_id,revision)
    return result
