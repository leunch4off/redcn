"""Choose among explicitly observed photo variants; preserve every source.

Resolution and image statistics describe the bytes we decoded. They are not a
watermark detector or a calibrated photography-quality score. In particular a
small, apparently clean thumbnail cannot replace a usable full-size original.
"""
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import time

import photo_store
import redcn

POLICY = 'observed-variants-v1-resolution-480-400000'
MAX_VARIANTS = 6
MIN_SHORT_SIDE = 480
MIN_PIXELS = 400_000
_SHA = re.compile(r'[a-f0-9]{64}\Z')
_PHOTO_ID = re.compile(r'[A-Za-z0-9_.:-]{1,100}\Z')
_VARIANTS = ('fullUrl', 'thumbnailUrl', 'thumbnail2Url', 'miniUrl')
_OBSERVATIONS = frozenset(('srcset', 'data-srcset', 'data-original', 'data-src', 'src', 'currentSrc', 'href'))


def _urls(canonical_url, source):
    canonical = redcn.allowed_url(canonical_url, images=True, resolve=False)
    if not isinstance(source, dict):
        raise ValueError('Нужно происхождение наблюдаемых вариантов фото')
    identity = source.get('photoId')
    if type(identity) not in (str, int) or not _PHOTO_ID.fullmatch(str(identity)):
        raise ValueError('Для сравнения вариантов нужен явный photoId источника')
    variants = source.get('variants', {})
    observed = source.get('observedVariants', [])
    if not isinstance(variants, dict) or not isinstance(observed, list) or len(observed) > 60:
        raise ValueError('Неверный список наблюдаемых вариантов фотографии')
    rows = {}
    def add(value, provenance):
        try:
            url = redcn.allowed_url(value, images=True, resolve=False)
        except (ValueError, TypeError):
            return
        row = rows.setdefault(url, {'url': url, 'provenance': []})
        if provenance not in row['provenance']:
            row['provenance'].append(provenance)
    add(canonical, 'canonical-listing-url')
    for key in _VARIANTS:
        if key in variants:
            add(variants[key], 'source.variants.'+key)
    for row in observed:
        if isinstance(row, dict) and row.get('source') in _OBSERVATIONS:
            add(row.get('url'), 'observed.'+row['source'])
    all_rows = list(rows.values())
    identity = str(identity)
    group = {'policy': POLICY, 'photoId': identity, 'canonicalUrl': canonical, 'observed': all_rows}
    digest = hashlib.sha256(json.dumps(group, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return canonical, identity, digest, all_rows[:MAX_VARIANTS], len(all_rows),all_rows


def _connection(directory, *, write=False):
    directory = Path(directory)
    path = directory/'photo-variant-decisions.sqlite3'
    if path.is_symlink():
        raise ValueError('База вариантов фотографии не должна быть ссылкой')
    if not write:
        if not path.is_file():
            return None
        return sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True, timeout=5)
    directory.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=5)
    path.chmod(0o600)
    db.execute('CREATE TABLE IF NOT EXISTS decisions (id INTEGER PRIMARY KEY AUTOINCREMENT,canonical_url TEXT NOT NULL,group_sha TEXT NOT NULL,created REAL NOT NULL,payload TEXT NOT NULL,sha256 TEXT NOT NULL)')
    db.execute('CREATE INDEX IF NOT EXISTS decisions_lookup ON decisions(canonical_url,group_sha,id)')
    return db


def variant_decision(directory, canonical_url, source=None, *, group_sha=None,verify_bytes=False,strict=False):
    """Read a verified decision without downloading, decoding or running OCR.

    verify_bytes=True hashes existing candidates too. Polling may leave that off:
    photo_store.asset still checks the selected immutable bytes on delivery.
    """
    canonical = redcn.allowed_url(canonical_url, images=True, resolve=False)
    if group_sha is not None and (not isinstance(group_sha,str) or not _SHA.fullmatch(group_sha)):raise ValueError('Неверный SHA группы вариантов')
    if source is not None:
        source_group=_urls(canonical,source)[2]
        if group_sha is not None and group_sha!=source_group:raise ValueError('SHA группы не соответствует источнику карточки')
        group_sha=source_group
    db = _connection(directory)
    if db is None:
        return None
    try:
        row = db.execute('SELECT payload,sha256 FROM decisions WHERE canonical_url=?'+(' AND group_sha=?' if group_sha else '')+' ORDER BY id DESC LIMIT 1',
                         (canonical, group_sha) if group_sha else (canonical,)).fetchone()
    finally:
        db.close()
    if row is None:
        return None
    if hashlib.sha256(row[0].encode()).hexdigest() != row[1]:
        raise ValueError('Контрольная сумма решения о вариантах не совпадает')
    value = json.loads(row[0])
    if (not isinstance(value, dict) or value.get('policy') != POLICY
            or value.get('canonicalUrl') != canonical
            or group_sha is not None and value.get('groupSha') != group_sha
            or not isinstance(value.get('candidates'), list) or len(value['candidates']) > MAX_VARIANTS):
        raise ValueError('Неверный паспорт решения о вариантах фотографии')
    for candidate in value['candidates']:
        if not isinstance(candidate, dict):
            raise ValueError('Неверный вариант фотографии в решении')
        if candidate.get('status') != 'available':
            continue
        digest = candidate.get('id')
        if not isinstance(digest, str) or not _SHA.fullmatch(digest):
            raise ValueError('Неверный SHA-256 варианта фотографии')
        target = Path(directory)/'media'/digest
        if target.is_symlink() or not target.is_file():
            if strict:raise ValueError('Исходник записанного варианта отсутствует; полная резервная копия невозможна')
            return None
        if verify_bytes:
            try:
                photo_store.asset(directory, digest)
            except (ValueError, OSError) as error:
                if strict:raise ValueError('Исходник записанного варианта повреждён; полная резервная копия невозможна') from error
                return None
    selected = value.get('selected')
    if selected is not None and (not isinstance(selected, dict) or not any(
            candidate.get('status') == 'available' and candidate.get('id') == selected.get('id')
            and candidate.get('url') == selected.get('url') for candidate in value['candidates'])):
        raise ValueError('Принятый вариант не связан с проверенными исходниками')
    return {**value, 'decisionSha': row[1], 'cached': True}


def _quality(directory, metadata):
    identity = metadata.get('id') if isinstance(metadata, dict) else None
    if not isinstance(identity, str) or not _SHA.fullmatch(identity):
        raise ValueError('Вариант не связан с проверенным исходником')
    content, stored_mime, stored = photo_store.asset(directory, identity)
    return _quality_bytes(content,stored_mime,stored,identity)


def _quality_bytes(content,stored_mime,stored,identity):
    if not isinstance(content,bytes) or not 0<len(content)<=redcn.MAX_IMAGE or hashlib.sha256(content).hexdigest()!=identity:
        raise ValueError('Байты варианта не соответствуют SHA-256')
    if not isinstance(stored,dict) or stored.get('id')!=identity:
        raise ValueError('Метаданные варианта не связаны с исходником')
    if stored.get('kind') != 'original':
        raise ValueError('Вариантом должен быть исходник, а не кадрированная копия')
    actual_mime = photo_store.image_mime(content)
    if actual_mime != stored_mime:
        raise ValueError('MIME сохранённого исходника не согласован с байтами')
    image = photo_store.open_photo(content)
    try:
        from PIL import ImageFilter, ImageStat
        width, height = image.size
        sample = image.convert('L')
        try:
            sample.thumbnail((256, 256))
            entropy = float(sample.entropy())
            edges = sample.filter(ImageFilter.FIND_EDGES)
            try:
                if min(edges.size) <= 2:
                    edge_rms = 0.0
                else:
                    inner = edges.crop((1, 1, edges.width-1, edges.height-1))
                    try:
                        edge_rms = float(ImageStat.Stat(inner).rms[0])
                    finally:
                        inner.close()
            finally:
                edges.close()
        finally:
            sample.close()
    finally:
        image.close()
    eligible = min(width, height) >= MIN_SHORT_SIDE and width*height >= MIN_PIXELS
    return {'id': identity, 'kind': 'original', 'mime': actual_mime, 'mimeEvidence': 'magic-and-decoder',
            'width': width, 'height': height, 'bytes': len(content),
            'availability': 'decoded-and-sha-verified',
            'quality': {'status': 'resolution-checked' if eligible else 'small-preview',
                        'resolutionEligible': eligible, 'pixels': width*height,
                        'shortSide': min(width, height), 'edgeRms': round(edge_rms, 4),
                        'entropyBits': round(entropy, 4), 'statisticsMeaning': 'descriptive-not-calibrated'},
            'markState': stored.get('markState') if stored.get('markState') in ('not-checked','not-detected','detected','requires-review','known-mark-excluded') else 'not-checked',
            'cleanlinessConfirmed': False}


def _choose(candidates,canonical):
    available=[row for row in candidates if row['status']=='available']
    eligible=[row for row in available if row['quality']['resolutionEligible']]
    primary=next((row for row in available if row['url']==canonical),None)
    if primary and primary['quality']['resolutionEligible']:return primary,'canonical-resolution-eligible','selected'
    if eligible:return max(eligible,key=lambda row:(row['quality']['pixels'],row['quality']['shortSide'],row['quality']['edgeRms'])),'observed-resolution-eligible','selected'
    selected=primary or (available[0] if available else None)
    return (selected,'preview-requires-review','review') if selected else (None,'no-available-variant','unavailable')


def _persist(directory, value):
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    digest = hashlib.sha256(payload.encode()).hexdigest()
    db = _connection(directory, write=True)
    try:
        db.execute('INSERT INTO decisions(canonical_url,group_sha,created,payload,sha256) VALUES (?,?,?,?,?)',
                   (value['canonicalUrl'], value['groupSha'], time.time(), payload, digest))
        db.commit()
    finally:
        db.close()
    return {**value, 'decisionSha': digest, 'cached': False}


def probe_variants(directory, canonical_url, source, *, original=None, refresh=False, cancel=None, max_seconds=8):
    if type(max_seconds) not in (int, float) or not math.isfinite(max_seconds) or not 0 < max_seconds <= 60:
        raise ValueError('Срок проверки вариантов: больше нуля и до 60 секунд')
    if type(refresh) is not bool:
        raise ValueError('Неверный признак повторной проверки вариантов')
    canonical, identity, group_sha, observed, observed_count,all_observed = _urls(canonical_url, source)
    if cancel is not None and cancel.is_set():
        raise ValueError('Проверка вариантов отменена')
    deadline = time.monotonic()+max_seconds
    if not refresh:
        try:
            previous = variant_decision(directory, canonical, source, verify_bytes=True)
        except (OSError, ValueError, sqlite3.Error):
            previous = None
        supplied = original.get('id') if isinstance(original, dict) and original.get('sourceUrl') == canonical else None
        old_canonical = next((row.get('id') for row in (previous or {}).get('candidates', []) if row.get('url') == canonical), None)
        if previous and not previous.get('retryable') and (supplied is None or supplied == old_canonical):
            return previous
    candidates, quality_cache = [], {}
    for observed_row in observed:
        if cancel is not None and cancel.is_set():
            raise ValueError('Проверка вариантов отменена')
        row = {**observed_row, 'sourceUrl': observed_row['url'], 'photoId': identity,
               'status': 'unavailable', 'availability': 'not-checked',
               'markState': 'not-checked', 'cleanlinessConfirmed': False}
        remaining = deadline-time.monotonic()
        if remaining <= 0:
            row.update(errorClass='TimeoutError', reason='time-budget', message='Истёк срок проверки варианта')
        else:
            try:
                if observed_row['url'] == canonical and isinstance(original, dict) and original.get('sourceUrl') == canonical:
                    metadata = original
                else:
                    metadata = photo_store.load_photo(directory, observed_row['url'], refresh=refresh,
                        source=source, network_timeout=min(60, max(.001, remaining)), cancel=cancel,allow_observed_fallback=False)
                digest = metadata.get('id') if isinstance(metadata, dict) else None
                if digest not in quality_cache:
                    quality_cache[digest] = _quality(directory, metadata)
                row.update(quality_cache[digest], status='available')
            except Exception as error:
                if cancel is not None and cancel.is_set():
                    raise ValueError('Проверка вариантов отменена') from error
                row.update(errorClass=type(error).__name__, reason='load-or-decode-failed',
                           message=str(error)[:200] or 'Вариант недоступен')
        candidates.append(row)
    selected,reason,status=_choose(candidates,canonical)
    value = {'version': 1, 'policy': POLICY, 'canonicalUrl': canonical, 'photoId': identity,
             'groupSha': group_sha,'observed':all_observed, 'candidates': candidates, 'selected': selected,
             'reason': reason, 'status': status, 'observedCount': observed_count,
             'probeLimit': MAX_VARIANTS, 'truncated': observed_count > MAX_VARIANTS,
             'retryable': any(row['status'] != 'available' for row in candidates),
             'watermarkAffectsSelection': False,
             'message': 'Сравнивались только наблюдаемые URL; состояние watermark не означает качество или отсутствие логотипов.'}
    return _persist(directory, value)


def validate_portable_decision(canonical_url,value,*,assets):
    """Pure backup validation: actual bytes determine eligibility, never I/O.

    assets maps SHA to {content: bytes, mime: str, metadata: dict}. The caller
    owns atomic restore; this function never writes, downloads or runs OCR.
    """
    canonical=redcn.allowed_url(canonical_url,images=True,resolve=False)
    if not isinstance(value,dict) or type(value.get('version')) is not int or value.get('version')!=1 or value.get('policy')!=POLICY or value.get('canonicalUrl')!=canonical:
        raise ValueError('Неверный переносимый паспорт вариантов')
    identity=value.get('photoId')
    if not isinstance(identity,str) or not _PHOTO_ID.fullmatch(identity):raise ValueError('Неверный photoId переносимых вариантов')
    observed=value.get('observed')
    if not isinstance(observed,list):
        if value.get('truncated'):raise ValueError('Старый сокращённый паспорт не содержит весь список наблюдаемых URL; требуется повторная проверка')
        legacy=value.get('candidates')
        if not isinstance(legacy,list):raise ValueError('Неверный состав наблюдаемых вариантов')
        observed=[{'url':row.get('url'),'provenance':row.get('provenance')} for row in legacy if isinstance(row,dict)]
    if not 1<=len(observed)<=65:raise ValueError('Неверное число наблюдаемых вариантов')
    cleaned=[];seen=set();known={'canonical-listing-url',*('source.variants.'+key for key in _VARIANTS),*('observed.'+key for key in _OBSERVATIONS)}
    for row in observed:
        if not isinstance(row,dict):raise ValueError('Неверное происхождение варианта')
        url=redcn.allowed_url(row.get('url'),images=True,resolve=False);provenance=row.get('provenance')
        if url!=row.get('url') or url in seen or not isinstance(provenance,list) or not 1<=len(provenance)<=12 or any(not isinstance(item,str) or item not in known for item in provenance) or len(provenance)!=len(set(provenance)):raise ValueError('Неверный URL или свидетельство наблюдаемого варианта')
        if ('canonical-listing-url' in provenance)!=(url==canonical):raise ValueError('Каноническая ссылка варианта противоречива')
        cleaned.append({'url':url,'provenance':list(provenance)});seen.add(url)
    if cleaned[0]['url']!=canonical:raise ValueError('Первым должен быть канонический вариант источника')
    group={'policy':POLICY,'photoId':identity,'canonicalUrl':canonical,'observed':cleaned}
    group_sha=hashlib.sha256(json.dumps(group,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    if group_sha!=value.get('groupSha'):raise ValueError('SHA группы наблюдаемых вариантов не совпадает')
    if type(value.get('observedCount')) is not int or value.get('observedCount')!=len(cleaned) or type(value.get('probeLimit')) is not int or value.get('probeLimit')!=MAX_VARIANTS or value.get('truncated') is not (len(cleaned)>MAX_VARIANTS):raise ValueError('Предел наблюдаемых вариантов противоречив')
    rows=value.get('candidates')
    if not isinstance(rows,list) or len(rows)!=min(MAX_VARIANTS,len(cleaned)):raise ValueError('Не совпадает состав проверенных вариантов')
    if not isinstance(assets,dict):raise ValueError('Нужны проверенные байты вариантов')
    output=[]
    for raw,witness in zip(rows,cleaned):
        if not isinstance(raw,dict) or raw.get('url')!=witness['url'] or raw.get('sourceUrl')!=witness['url'] or raw.get('photoId')!=identity or raw.get('provenance')!=witness['provenance']:raise ValueError('Вариант не принадлежит объявленной группе')
        row={**witness,'sourceUrl':witness['url'],'photoId':identity,'status':raw.get('status'),'availability':'not-checked','markState':'not-checked','cleanlinessConfirmed':False}
        if row['status']=='available':
            digest=raw.get('id');stored=assets.get(digest) if isinstance(digest,str) and _SHA.fullmatch(digest) else None
            if not isinstance(stored,dict):raise ValueError('В резервной копии отсутствует исходник наблюдаемого варианта')
            actual=_quality_bytes(stored.get('content'),stored.get('mime'),stored.get('metadata'),digest)
            if any(raw.get(key)!=actual[key] for key in ('id','kind','mime','mimeEvidence','width','height','bytes','availability')):raise ValueError('Формат или размеры варианта не совпадают с реальными байтами')
            quality=raw.get('quality')
            if not isinstance(quality,dict) or any(quality.get(key)!=actual['quality'][key] for key in actual['quality']):raise ValueError('Проверка качества варианта не соответствует реальным байтам')
            if raw.get('cleanlinessConfirmed') is not False:raise ValueError('Паспорт не может обещать отсутствие watermark')
            row.update(actual)
        elif row['status']=='unavailable':
            if raw.get('id') is not None:raise ValueError('Недоступный вариант не должен подменять сохранённый исходник')
            for key in ('reason','errorClass','message'):
                text=raw.get(key)
                if isinstance(text,str):row[key]=re.sub(r'[\x00-\x1f\x7f]',' ',text)[:200]
        else:raise ValueError('Неизвестное состояние доступности варианта')
        output.append(row)
    selected,reason,status=_choose(output,canonical);claimed=value.get('selected')
    if (selected is None)!=(claimed is None) or selected is not None and (not isinstance(claimed,dict) or any(claimed.get(key)!=selected[key] for key in ('id','url','width','height','mime','quality'))):raise ValueError('Принятый вариант противоречит проверенным исходникам')
    if value.get('status')!=status or value.get('reason')!=reason:raise ValueError('Причина выбора варианта противоречит правилам качества')
    return {'version':1,'policy':POLICY,'canonicalUrl':canonical,'photoId':identity,'groupSha':group_sha,'observed':cleaned,
        'candidates':output,'selected':selected,'reason':reason,'status':status,'observedCount':len(cleaned),'probeLimit':MAX_VARIANTS,
        'truncated':len(cleaned)>MAX_VARIANTS,'retryable':any(row['status']!='available' for row in output),'watermarkAffectsSelection':False,
        'message':'Сравнивались только наблюдаемые URL; состояние watermark не означает качество или отсутствие логотипов.'}


def portable_record(decision):
    """Serialize an already validated decision for the caller's transaction."""
    if not isinstance(decision,dict) or decision.get('policy')!=POLICY:raise ValueError('Нужен проверенный паспорт вариантов')
    payload=json.dumps(decision,ensure_ascii=False,sort_keys=True,separators=(',',':'))
    return decision['canonicalUrl'],decision['groupSha'],payload,hashlib.sha256(payload.encode()).hexdigest()


def export_decision(directory,canonical_url,source=None,*,group_sha=None):
    """Export one current decision, hashing/decoding only its saved sources."""
    value=variant_decision(directory,canonical_url,source=source,group_sha=group_sha,verify_bytes=True,strict=True)
    if value is None:return None
    assets={}
    for row in value['candidates']:
        if row.get('status')!='available':continue
        content,mime,metadata=photo_store.asset(directory,row['id']);assets[row['id']]={'content':content,'mime':mime,'metadata':metadata}
        with photo_store._connection(directory) as db:alias=db.execute('SELECT id FROM photo_urls WHERE url=?',(row['url'],)).fetchone()
        if alias and alias[0]!=row['id']:raise ValueError('Источник варианта изменился после выбора; обновите его проверку перед резервной копией')
    return validate_portable_decision(canonical_url,value,assets=assets)
