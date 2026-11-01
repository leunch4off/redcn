"""Portable private workspace bundles, restored into a new offline directory.

This is an explicit operator action. It does not open a server/controller, repair
imports, upload files, discover credentials, or copy model/runtime state. Card
archives and byte hashes use the same validators as individual dossier transfer.
"""
import argparse
import base64
import ctypes
import errno
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import tempfile
import time
import zipfile

import card_assets
import dossier_backup

FORMAT='redcn-private-workspace'
MAX_BUNDLE=512*1024*1024
MAX_EXPANDED=1024*1024*1024
MAX_METADATA=32*1024*1024
MAX_CARDS=500
MAX_ROWS=200_000
MAX_HTTP_INSPECT=64*1024*1024
_HEX=re.compile(r'[a-f0-9]{32}')
_DIGEST=re.compile(r'[a-f0-9]{64}')
_TABLES={
    'listings':('listings.sqlite3',('history','publication_reviews','publication_review_events','variants','seen')),
    'dictionary':('dictionary-reviews.sqlite3',('dictionary_revision','dictionary_entries','dictionary_events')),
    # Checkpoints contain runtime paths and optimizer identities for another
    # machine. Portable examples/decisions/splits survive; jobs start afresh.
    'learning':('learning/memory.sqlite3',('examples','review_history','splits')),
    'photo-instances':('photo-instance-reviews/revisions.sqlite3',('revisions',)),
}


def _json(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def _sha(content):
    return hashlib.sha256(content).hexdigest()


def _read_json(path,limit):
    if path.is_symlink():raise ValueError('Резервная копия не читает символические ссылки')
    if not path.exists():return None
    if not path.is_file() or not 0<path.stat().st_size<=limit:raise ValueError('Неверный размер данных для копирования')
    return json.loads(path.read_text(encoding='utf-8'))


def _encode(value):
    return {'binary':base64.b64encode(value).decode()} if isinstance(value,bytes) else value


def _decode(value):
    if value is None or type(value) in (str,int):return value
    if type(value) is float and math.isfinite(value):return value
    if isinstance(value,dict) and set(value)=={'binary'} and isinstance(value['binary'],str):
        try:return base64.b64decode(value['binary'],validate=True)
        except ValueError as error:raise ValueError('Неверный бинарный снимок истории') from error
    raise ValueError('Неверное значение в переносимой таблице')


def _cards(directory):
    path=Path(directory)/'listings.sqlite3'
    if path.is_symlink() or not path.is_file():raise ValueError('Рабочая библиотека не найдена')
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=3) as db:
        db.execute('PRAGMA query_only=ON')
        try:rows=db.execute('SELECT id,updated,payload FROM results ORDER BY id').fetchall()
        except sqlite3.OperationalError as error:raise ValueError('В рабочей библиотеке нет карточек') from error
    if len(rows)>MAX_CARDS:raise ValueError('За один раз переносится не более 500 карточек')
    if any(not isinstance(row[0],str) or not _HEX.fullmatch(row[0]) for row in rows):
        raise ValueError('В библиотеке есть карточка с неверным ID')
    return rows


def _metadata(directory):
    root=Path(directory)
    profile=_read_json(root/'phone-profile.json',300_000) or {}
    # Validate and normalize through the established schema, in a disposable
    # directory, never through PairingState or the active server's settings.
    from phone_relay import save_profile
    with tempfile.TemporaryDirectory(prefix='redcn-profile-check-') as folder:
        save_profile(folder,profile)
        profile=json.loads((Path(folder)/'phone-profile.json').read_text())
    tables={};count=0
    for store,(relative,names) in _TABLES.items():
        path=root/relative
        if path.is_symlink():raise ValueError('База копирования не должна быть ссылкой')
        if not path.exists():continue
        with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=3) as db:
            db.execute('PRAGMA query_only=ON')
            for name in names:
                if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(name,)).fetchone():continue
                columns=[row[1] for row in db.execute('PRAGMA table_info("'+name+'")')]
                rows=db.execute('SELECT * FROM "'+name+'" ORDER BY rowid').fetchmany(MAX_ROWS+1)
                count+=len(rows)
                if count>MAX_ROWS:raise ValueError('Слишком много строк для одного резервного комплекта')
                tables[store+'/'+name]={'columns':columns,'rows':[[_encode(item) for item in row] for row in rows]}
    value={'version':1,'profile':profile,'tables':tables}
    if len(_json(value))>MAX_METADATA:raise ValueError('Словари и история превышают 32 МБ')
    return value


def _global_design(directory,files):
    design=card_assets.load_design(directory)['template']
    images=[]
    if isinstance(design,dict):
        ids={value for key,value in (design.get('brand') or {}).items() if key in ('logoAssetId','avatarAssetId') and value}
        with card_assets._db(directory) as db:
            for identifier in sorted(ids):
                row=db.execute('SELECT id,role,name,mime,width,height,size,created,digest,original_digest,original_mime,original_size FROM design_images WHERE id=?',(identifier,)).fetchone()
                if not row:raise ValueError('Изображение общего дизайна отсутствует')
                item=dict(zip(('id','role','name','mime','width','height','bytes','created','digest','originalDigest','originalMime','originalBytes'),row))
                for field,digest in (('blob',item['digest']),('originalBlob',item['originalDigest'])):
                    name='blobs/card/'+digest;files[name]=card_assets._read(directory,digest);item[field]=name
                images.append(item)
    return {'template':design,'images':images}


def _state(directory):
    # A cache cleanup or an unrelated raw page download is not a library edit.
    # Hash only portable state and explicit card/dossier relationships.
    rows=_cards(directory)
    with card_assets._db(directory) as db:
        global_state=[db.execute('SELECT * FROM designs ORDER BY id').fetchall(),db.execute('SELECT * FROM design_images ORDER BY id').fetchall()]
    return _sha(_json([rows,_metadata(directory),global_state,
        [(identifier,dossier_backup._card_state(directory,identifier)) for identifier,_,_ in rows]]))


def _path_allowed(name):
    return name=='metadata.json' or bool(re.fullmatch(r'cards/[a-f0-9]{32}\.zip|blobs/card/[a-f0-9]{64}',name))


def _pack(manifest,files):
    manifest={**manifest,'files':[{'path':name,'bytes':len(data),'sha256':_sha(data)} for name,data in sorted(files.items())]}
    encoded=_json(manifest)
    if len(encoded)>2*1024*1024 or sum(len(data) for data in files.values())+len(encoded)>MAX_EXPANDED:
        raise ValueError('Резервный комплект превышает 1 ГБ распакованных данных')
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=2) as bundle:
        bundle.writestr('manifest.json',encoded)
        for name,data in sorted(files.items()):bundle.writestr(name,data)
    if out.tell()>MAX_BUNDLE:raise ValueError('Резервный ZIP превышает 512 МБ; переносите досье отдельно')
    return out.getvalue()


@card_assets._serialize_files
def export_workspace(directory):
    """Export only the authorized workspace's portable user data, not sessions."""
    root=Path(directory).resolve()
    if Path(directory).is_symlink():raise ValueError('Хранилище копирования не должно быть ссылкой')
    rows=_cards(root)
    # Initialize only established attachment/media schemas before signing state.
    with card_assets._db(root):pass
    import photo_store
    with photo_store._connection(root):pass
    initial=_state(root)
    metadata=_metadata(root)
    _validate_metadata_schema(metadata,{identifier for identifier,_,_ in rows})
    files={'metadata.json':_json(metadata)}
    cards=[];expanded=0
    for identifier,_,_ in rows:
        content=dossier_backup.export_private(root,identifier)
        manifest,blobs=dossier_backup._read_zip(content)
        dossier_backup._validate_manifest(manifest,blobs)
        expanded+=sum(len(value) for value in blobs.values())
        if expanded>MAX_EXPANDED:raise ValueError('Вложения карточек превышают бюджет одного комплекта')
        name='cards/'+identifier+'.zip';files[name]=content
        cards.append({'id':identifier,'file':name,'attachments':len(manifest['attachments']),
            'exports':len(manifest['exports']),'photos':dossier_backup.photo_count(manifest),
            'photoVariantDecisions':len(manifest.get('photoVariantDecisions',[]))})
    global_design=_global_design(root,files)
    result=_pack({'version':1,'type':FORMAT,'created':time.time(),'cards':cards,'globalDesign':global_design,
        'excluded':['sessions','pairing','cookies','credentials','import-diagnostics','raw-page-cache','models','runtime-jobs'],
        'restorePolicy':'new-directory-only; re-pair devices; regenerate historical client exports'},files)
    if _state(root)!=initial:raise ValueError('Библиотека или словарь изменились во время копирования. Повторите экспорт.')
    return result


def _read(content):
    if not isinstance(content,bytes) or not 0<len(content)<=MAX_BUNDLE:raise ValueError('Нужен резервный ZIP до 512 МБ')
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as bundle:
            entries=bundle.infolist();names=[row.filename for row in entries]
            if len(entries)>MAX_CARDS+410 or len(set(names))!=len(names) or sum(row.file_size for row in entries)>MAX_EXPANDED:
                raise ValueError('Архив слишком велик или содержит повторные пути')
            for row in entries:
                if row.is_dir() or row.flag_bits&1 or stat.S_ISLNK(row.external_attr>>16) or row.filename!='manifest.json' and not _path_allowed(row.filename):
                    raise ValueError('Недопустимый путь или тип файла в архиве')
            if 'manifest.json' not in names or bundle.getinfo('manifest.json').file_size>2*1024*1024:
                raise ValueError('Отсутствует ограниченный манифест')
            manifest=json.loads(bundle.read('manifest.json'))
            if not isinstance(manifest,dict) or manifest.get('version')!=1 or manifest.get('type')!=FORMAT:
                raise ValueError('Нужна приватная резервная копия рабочего пространства redcn версии 1')
            files={}
            if not isinstance(manifest.get('files'),list):raise ValueError('Нет перечня контрольных сумм')
            for row in manifest['files']:
                if not isinstance(row,dict) or set(row)!= {'path','bytes','sha256'} or not isinstance(row['path'],str) or not _path_allowed(row['path']) or row['path'] in files:
                    raise ValueError('Неверный перечень файлов')
                if row['path'] not in names or type(row['bytes']) is not int or row['bytes']<1:
                    raise ValueError('Неверный размер или отсутствующий файл')
                data=bundle.read(row['path'])
                if len(data)!=row['bytes'] or _sha(data)!=row['sha256']:raise ValueError('Контрольная сумма файла не совпадает')
                files[row['path']]=data
            if set(files)|{'manifest.json'}!=set(names) or 'metadata.json' not in files:
                raise ValueError('В архиве есть неописанные файлы или отсутствует metadata.json')
    except (zipfile.BadZipFile,json.JSONDecodeError,UnicodeError,KeyError,RuntimeError,RecursionError) as error:
        raise ValueError('Повреждённый резервный комплект') from error
    return manifest,files


def _validate(manifest,files):
    cards=manifest.get('cards')
    if not isinstance(cards,list) or len(cards)>MAX_CARDS:raise ValueError('Неверный список карточек')
    seen=set();expanded=0
    for row in cards:
        if not isinstance(row,dict) or not isinstance(row.get('id'),str) or not _HEX.fullmatch(row['id']) or row['id'] in seen or row.get('file')!='cards/'+row['id']+'.zip' or row['file'] not in files:
            raise ValueError('Неверный ID или путь карточки')
        seen.add(row['id']);card,blobs=dossier_backup._read_zip(files[row['file']]);dossier_backup._validate_manifest(card,blobs)
        if card['sourceCardId']!=row['id']:raise ValueError('ID карточки не совпадает с вложенным досье')
        for name,expected in (('attachments',len(card['attachments'])),('exports',len(card['exports'])),('photos',dossier_backup.photo_count(card))):
            if type(row.get(name)) is not int or row[name]!=expected:raise ValueError('Счётчик карточки не совпадает с содержимым')
        count=row.get('photoVariantDecisions',0)
        if type(count) is not int or count!=len(card.get('photoVariantDecisions',[])):raise ValueError('Счётчик решений о вариантах не совпадает с содержимым')
        expanded+=sum(len(value) for value in blobs.values())
        if expanded>MAX_EXPANDED:raise ValueError('Вложения превышают 1 ГБ')
    if {name for name in files if name.startswith('cards/')}!={row['file'] for row in cards}:
        raise ValueError('В архиве есть карточка вне перечня')
    if len(files['metadata.json'])>MAX_METADATA:raise ValueError('История и словари превышают 32 МБ')
    try:metadata=json.loads(files['metadata.json'])
    except (ValueError,UnicodeError) as error:raise ValueError('Неверный переносимый словарь') from error
    if not isinstance(metadata,dict) or set(metadata)!= {'version','profile','tables'} or metadata['version']!=1 or not isinstance(metadata['tables'],dict):
        raise ValueError('Неверные переносимые настройки')
    count=0
    for key,table in metadata['tables'].items():
        if not isinstance(key,str) or '/' not in key:raise ValueError('Неизвестная переносимая таблица')
        store,name=key.split('/',1)
        if store not in _TABLES or name not in _TABLES[store][1] or not isinstance(table,dict) or set(table)!= {'columns','rows'}:
            raise ValueError('Неизвестная переносимая таблица')
        if not isinstance(table['columns'],list) or not table['columns'] or any(not isinstance(column,str) or not re.fullmatch(r'[a-z_][a-z0-9_]*',column) for column in table['columns']) or len(set(table['columns']))!=len(table['columns']):
            raise ValueError('Неверные столбцы таблицы')
        if not isinstance(table['rows'],list):raise ValueError('Неверные строки таблицы')
        count+=len(table['rows'])
        if count>MAX_ROWS:raise ValueError('Слишком много переносимых строк')
        for row in table['rows']:
            if not isinstance(row,list) or len(row)!=len(table['columns']):raise ValueError('Размер строки не совпадает со схемой')
            for value in row:_decode(value)
    if not isinstance(metadata['profile'],dict):raise ValueError('Неверные настройки словаря')
    from phone_relay import save_profile
    with tempfile.TemporaryDirectory(prefix='redcn-profile-validate-') as folder:save_profile(folder,metadata['profile'])
    _validate_metadata_schema(metadata,seen)
    design=manifest.get('globalDesign')
    if not isinstance(design,dict) or set(design)!= {'template','images'} or not isinstance(design['images'],list) or len(design['images'])>2:
        raise ValueError('Неверный общий дизайн')
    # Reuse established validators for brand bytes and linked asset IDs.
    dummy={'sourceCardId':'0'*32,'card':{'fields':{},'titles':[],'body':'','tags':''},
        'archive':{'status':'active','notes':''},'attachments':[],'exports':[],'media':[],
        'mediaUrls':[],'cropLinks':[],'design':design['template'],'designImages':design['images']}
    dossier_backup._validate_manifest(dummy,files)
    expected_blobs={row[key] for row in design['images'] for key in ('blob','originalBlob')}
    if {name for name in files if name.startswith('blobs/')}!=expected_blobs:raise ValueError('В архиве есть неиспользуемые изображения')
    return metadata


def inspect_workspace(content):
    manifest,files=_read(content);metadata=_validate(manifest,files)
    return {'version':1,'type':FORMAT,'sha256':_sha(content),'bytes':len(content),
        'cards':len(manifest['cards']),'attachments':sum(row.get('attachments',0) for row in manifest['cards']),
        'exports':sum(row.get('exports',0) for row in manifest['cards']),
        'photos':sum(row.get('photos',0) for row in manifest['cards']),
        'photoVariantDecisions':sum(row.get('photoVariantDecisions',0) for row in manifest['cards']),
        'dictionaryRules':len(metadata['profile'].get('rules',{})),
        'learningExamples':len(metadata['tables'].get('learning/examples',{}).get('rows',[])),
        'requiresNewWorkspace':True,'devicesMustPairAgain':True,'clientMaterialsRequireRegeneration':True}


def request_limit(path):
    return ((MAX_HTTP_INSPECT+2)//3)*4+100 if path=='workspace-backup/inspect' else None


def dispatch(directory,path,data=None,*,namespace='local'):
    """The caller must verify the desktop owner token before dispatching."""
    if path not in ('workspace-backup.zip','workspace-backup/inspect'):return None
    mime='application/json; charset=utf-8'
    # A paired owner is still a phone session. A whole library's private
    # contracts must require the desktop authority, rather than that cookie.
    if namespace!='local':return 403,{'error':'Резервная копия всей базы доступна только в редакторе Mac'},mime,{}
    if path=='workspace-backup.zip' and data is None:
        return 200,export_workspace(directory),'application/zip',{'Content-Disposition':'attachment; filename="redcn-private-workspace.zip"'}
    if path=='workspace-backup/inspect' and data is not None:
        if not isinstance(data,dict) or set(data)!= {'data'} or not isinstance(data['data'],str) or len(data['data'])>((MAX_HTTP_INSPECT+2)//3)*4:
            raise ValueError('Для проверки здесь нужен ZIP до 64 МБ; большие копии проверьте через CLI')
        try:content=base64.b64decode(data['data'],validate=True)
        except ValueError as error:raise ValueError('Неверное содержимое ZIP') from error
        if len(content)>MAX_HTTP_INSPECT:raise ValueError('Для проверки здесь нужен ZIP до 64 МБ')
        return 200,inspect_workspace(content),mime,{}
    return 405,{'error':'Неверный метод резервного копирования'},mime,{}


def _initialize_tables(directory,stores):
    directory=Path(directory)
    if 'listings' in stores:
        from redcn import open_cache
        with open_cache(directory) as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS results(id TEXT PRIMARY KEY,updated REAL,payload TEXT);
              CREATE TABLE IF NOT EXISTS history(id TEXT PRIMARY KEY,stamp REAL,payload TEXT,png BLOB);
              CREATE TABLE IF NOT EXISTS variants(key TEXT PRIMARY KEY,value INTEGER NOT NULL);''')
        import publication_review
        with publication_review._connect(directory):pass
    if 'dictionary' in stores:
        import dictionary_reviews
        with dictionary_reviews._connect(directory):pass
    if 'learning' in stores:
        from learning_memory import LearningMemory
        LearningMemory(directory)
    if 'photo-instances' in stores:
        import photo_instances
        with photo_instances._connection(directory):pass


def _validate_metadata_schema(metadata,card_ids):
    """Dry-load only trusted local schemas; inspection promises portability.

    No archive SQL is executed, and the destination/active cache is not opened.
    SQLite's trusted PK/UNIQUE/CHECK constraints complement strict scalar types.
    """
    stores={key.split('/')[0] for key in metadata['tables']}
    with tempfile.TemporaryDirectory(prefix='redcn-workspace-schema-') as folder:
        _initialize_tables(folder,stores)
        for store in stores:
            path=Path(folder)/_TABLES[store][0]
            with sqlite3.connect(path) as db:
                for key,table in metadata['tables'].items():
                    namespace,name=key.split('/')
                    if namespace!=store:continue
                    schema=db.execute('PRAGMA table_info("'+name+'")').fetchall()
                    if [column[1] for column in schema]!=table['columns']:
                        raise ValueError('Схема резервной копии несовместима с этой версией: '+key)
                    db.execute('DELETE FROM "'+name+'"')
                    for source in table['rows']:
                        row=[_decode(value) for value in source]
                        for column,value in zip(schema,row):
                            declared=column[2].upper()
                            if value is None:
                                valid=not (column[3] or column[5])
                            elif declared=='TEXT':valid=isinstance(value,str)
                            elif declared=='INTEGER':valid=type(value) is int and -(2**63)<=value<2**63
                            elif declared=='REAL':valid=(type(value) is float and math.isfinite(value)) or (type(value) is int and -(2**63)<=value<2**63)
                            elif declared=='BLOB':valid=isinstance(value,bytes)
                            else:valid=False
                            if not valid:raise ValueError('Тип данных несовместим со схемой: '+key+'/'+column[1])
                        values=dict(zip(table['columns'],row))
                        if key=='listings/history':
                            try:document=json.loads(values['payload'])
                            except (ValueError,TypeError) as error:raise ValueError('Повреждённая запись истории') from error
                            if not isinstance(document,dict):raise ValueError('Повреждённая запись истории')
                        if store=='photo-instances':
                            import photo_instances
                            if values['listing_id'] not in card_ids:raise ValueError('Наблюдения фото относятся к карточке вне комплекта')
                            if _sha(values['document'].encode())!=values['sha256']:raise ValueError('Контрольная сумма наблюдений фото не совпадает')
                            try:photo_instances.normalize_review(json.loads(values['document']),values['listing_id'])
                            except (ValueError,TypeError) as error:raise ValueError('Неверные наблюдения фото') from error
                        try:db.execute('INSERT INTO "'+name+'" VALUES('+','.join('?' for _ in schema)+')',row)
                        except sqlite3.Error as error:raise ValueError('Нарушены ограничения таблицы: '+key) from error


def _restore_metadata(directory,metadata,mapping):
    from phone_relay import save_profile
    save_profile(directory,metadata['profile'])
    stores={key.split('/')[0] for key in metadata['tables']}
    # Even a design/dictionary-only library must reopen as a valid empty cache.
    _initialize_tables(directory,stores|{'listings'})
    for store in stores:
        path=Path(directory)/_TABLES[store][0]
        with sqlite3.connect(path,timeout=3) as db:
            db.execute('BEGIN IMMEDIATE')
            for key,table in metadata['tables'].items():
                namespace,name=key.split('/')
                if namespace!=store:continue
                # Private imported review history is preserved in a receipt,
                # not reinstated as another machine's live approval grant.
                if name in ('publication_reviews','publication_review_events'):continue
                expected=[row[1] for row in db.execute('PRAGMA table_info("'+name+'")')]
                if expected!=table['columns']:raise ValueError('Схема резервной копии несовместима с этой версией: '+key)
                db.execute('DELETE FROM "'+name+'"')
                rows=[]
                for source in table['rows']:
                    row=[_decode(value) for value in source]
                    if store=='listings' and name=='history':
                        position=table['columns'].index('payload')
                        try:row[position]=_json(dossier_backup._remap(json.loads(row[position]),mapping)).decode()
                        except ValueError as error:raise ValueError('Повреждённая запись истории') from error
                    if store=='photo-instances':
                        import photo_instances
                        positions={key:table['columns'].index(key) for key in ('listing_id','document','sha256')}
                        original=row[positions['document']]
                        if not isinstance(original,str) or _sha(original.encode())!=row[positions['sha256']]:raise ValueError('Контрольная сумма наблюдений фото не совпадает')
                        previous=row[positions['listing_id']]
                        if previous not in mapping:raise ValueError('Наблюдения фото относятся к карточке вне комплекта')
                        document=photo_instances.normalize_review(json.loads(original),previous)
                        document=dossier_backup._remap(document,mapping)
                        row[positions['listing_id']]=mapping[previous]
                        encoded=_json(document).decode();row[positions['document']]=encoded;row[positions['sha256']]=_sha(encoded.encode())
                    rows.append(row)
                if rows:db.executemany('INSERT INTO "'+name+'" VALUES('+','.join('?' for _ in expected)+')',rows)
        path.chmod(0o600)


def _install_global_design(directory,section,files):
    for name,data in files.items():
        if name.startswith('blobs/card/'):
            if card_assets._write(directory,data)!=name.rsplit('/',1)[-1]:raise ValueError('Неверный digest общего дизайна')
    with card_assets._db(directory) as db:
        for row in section['images']:
            db.execute('INSERT INTO design_images VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',tuple(row[key] for key in ('id','role','name','mime','width','height','bytes','created','digest','originalDigest','originalMime','originalBytes')))
    if section['template'] is not None:card_assets.save_design(directory,section['template'])


def _publish(staging,destination):
    """Atomically publish without replacing even an empty competing directory."""
    library=ctypes.CDLL(None,use_errno=True)
    source=os.fsencode(staging);target=os.fsencode(destination)
    if sys.platform=='darwin' and hasattr(library,'renamex_np'):
        function=library.renamex_np;function.argtypes=[ctypes.c_char_p,ctypes.c_char_p,ctypes.c_uint];function.restype=ctypes.c_int
        result=function(source,target,4)  # RENAME_EXCL
    elif sys.platform.startswith('linux') and hasattr(library,'renameat2'):
        function=library.renameat2;function.argtypes=[ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint];function.restype=ctypes.c_int
        result=function(-100,source,-100,target,1)  # AT_FDCWD, RENAME_NOREPLACE
    else:raise ValueError('Эта платформа не поддерживает безопасную атомарную публикацию новой папки')
    if result!=0:
        code=ctypes.get_errno()
        if code in (errno.EEXIST,errno.ENOTEMPTY):raise FileExistsError('Целевая папка появилась во время восстановления; её данные сохранены')
        raise OSError(code,os.strerror(code))


def restore_workspace(content,destination):
    """Restore off-line. Never merge into or replace an active user cache."""
    destination=Path(destination).expanduser().absolute()
    if destination.exists() or destination.is_symlink():raise FileExistsError('Выберите новую, ещё не существующую папку рабочего пространства')
    if not destination.parent.is_dir() or destination.parent.is_symlink():raise ValueError('Родительская папка должна существовать и не быть ссылкой')
    manifest,files=_read(content);metadata=_validate(manifest,files)
    with tempfile.TemporaryDirectory(prefix='.redcn-restore-',dir=destination.parent) as temporary:
        stage=Path(temporary)/'workspace';stage.mkdir(mode=0o700)
        mapping={};cards=[]
        for row in manifest['cards']:
            restored=dossier_backup.restore_private(stage,files[row['file']],return_mapping=True)
            mapping.update(restored.pop('idMap'));cards.append(restored)
        # References to another restored card cannot be resolved by an isolated
        # per-card restore. Resolve them only after all new IDs are known.
        if cards:
            with sqlite3.connect(stage/'listings.sqlite3') as db:
                for identifier,payload in db.execute('SELECT id,payload FROM results').fetchall():
                    db.execute('UPDATE results SET payload=? WHERE id=?',(_json(dossier_backup._remap(json.loads(payload),mapping)).decode(),identifier))
        _restore_metadata(stage,metadata,mapping)
        _install_global_design(stage,manifest['globalDesign'],files)
        receipt={'version':1,'type':FORMAT,'sha256':_sha(content),'restoredAt':time.time(),'cards':cards,
            'devicesMustPairAgain':True,'modelsRestored':False,'clientMaterialsRequireRegeneration':True,
            'sourcePublicationReviews':{key:value for key,value in metadata['tables'].items() if key in ('listings/publication_reviews','listings/publication_review_events')}}
        target=stage/'workspace-restore-receipt.json';target.write_bytes(_json(receipt));target.chmod(0o600)
        for path in stage.rglob('*'):
            if path.is_symlink():raise ValueError('В восстановленном комплекте не допускаются ссылки')
            path.chmod(0o700 if path.is_dir() else 0o600)
            if path.is_file():
                with path.open('rb') as stream:os.fsync(stream.fileno())
        for folder in [path for path in stage.rglob('*') if path.is_dir()]+[stage]:
            descriptor=os.open(folder,os.O_RDONLY)
            try:os.fsync(descriptor)
            finally:os.close(descriptor)
        _publish(stage,destination)
        descriptor=os.open(destination.parent,os.O_RDONLY)
        try:os.fsync(descriptor)
        finally:os.close(descriptor)
    return {**{key:value for key,value in receipt.items() if key!='sourcePublicationReviews'},'destination':str(destination)}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    action=parser.add_subparsers(dest='action',required=True)
    export=action.add_parser('export');export.add_argument('--cache',required=True);export.add_argument('--output',required=True)
    inspect=action.add_parser('inspect');inspect.add_argument('archive')
    restore=action.add_parser('restore');restore.add_argument('archive');restore.add_argument('--destination',required=True)
    args=parser.parse_args()
    if args.action=='export':
        output=Path(args.output)
        if output.exists() or output.is_symlink():raise FileExistsError('Файл вывода уже существует')
        data=export_workspace(args.cache)
        descriptor=os.open(output,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(descriptor,'wb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
        result=inspect_workspace(data)
    else:
        archive=Path(args.archive)
        if archive.is_symlink() or not archive.is_file() or not 0<archive.stat().st_size<=MAX_BUNDLE:raise ValueError('Нужен ZIP до 512 МБ')
        content=archive.read_bytes()
        result=inspect_workspace(content) if args.action=='inspect' else restore_workspace(content,args.destination)
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
