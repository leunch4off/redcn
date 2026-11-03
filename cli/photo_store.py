"""Verified originals, local OCR and reversible crop derivatives. No page scraping."""
import hashlib
import base64
import io
import json
import math
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import warnings

import redcn

_ocr_lock = threading.Lock()
_photo_locks = {}
_guard = threading.Lock()
MAX_PIXELS = 40_000_000
AUTO_CROP_POLICY = 'verified-edge-v3-cian-avito'
AUTO_CROP_MIN_SHORT_SIDE = 320
AUTO_CROP_MAX_AREA_LOSS = .25
AUTO_CROP_SECONDS = 12
_auto_crop_lock = threading.Lock()


def pillow():
    try:
        from PIL import Image, ImageOps
    except ImportError as error:
        raise ValueError('Для обработки фото установите: .venv/bin/python -m pip install -r cli/requirements-media.txt') from error
    return Image, ImageOps


def open_photo(content):
    Image, ImageOps = pillow()
    with warnings.catch_warnings():
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        try:
            image = Image.open(io.BytesIO(content))
            if image.format not in ('JPEG','PNG','WEBP') or getattr(image,'n_frames',1) != 1:
                raise ValueError('Нужна статичная фотография JPEG, PNG или WebP')
            if image.width < 32 or image.height < 32 or image.width * image.height > MAX_PIXELS:
                raise ValueError('Размер фото: от 32 px, до 40 мегапикселей')
            image.load()
            return ImageOps.exif_transpose(image)
        except (Image.DecompressionBombWarning, Image.DecompressionBombError) as error:
            raise ValueError('Фото превышает безопасный размер 40 МП') from error


def _connection(directory):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    from workspace_ownership import owned_path
    connection=sqlite3.connect(owned_path(directory,'media.sqlite3'),timeout=15)
    connection.execute('CREATE TABLE IF NOT EXISTS assets (id TEXT PRIMARY KEY, mime TEXT, metadata TEXT)')
    connection.execute('CREATE TABLE IF NOT EXISTS photo_urls (url TEXT PRIMARY KEY, id TEXT)')
    connection.execute('CREATE TABLE IF NOT EXISTS crop_links (original_id TEXT,asset_id TEXT,verification TEXT,metadata TEXT,created REAL,PRIMARY KEY(original_id,asset_id,verification))')
    return connection


def _merge_original_metadata(previous,current):
    # Pixel-identical URLs can belong to different source groups. Preserve
    # byte/crop facts, but never relabel old URL witnesses with a new photoId.
    identity_changed=previous.get('photoId') is not None and current.get('photoId') is not None and str(previous['photoId'])!=str(current['photoId'])
    unknown_alias=current.get('sourceUrl') is not None and previous.get('sourceUrl')!=current['sourceUrl'] and current.get('photoId') is None
    if identity_changed or unknown_alias:
        previous={key:value for key,value in previous.items() if key not in ('photoId','order','variants','observedVariants','identitySource')}
    output={**previous,**current}
    for key in ('isLayout','isCianLayout'):
        if previous.get(key) is True:output[key]=True
    return output


def _save(directory,content,mime,metadata):
    from workspace_ownership import owned_path
    digest=hashlib.sha256(content).hexdigest();folder=owned_path(directory,'media');folder.mkdir(parents=True,exist_ok=True)
    path=owned_path(directory,Path('media')/digest)
    if not path.exists():
        with tempfile.NamedTemporaryFile(dir=folder,delete=False) as f: f.write(content);temp=Path(f.name)
        temp.replace(path)
    with _connection(directory) as db:
        existing=db.execute('SELECT metadata FROM assets WHERE id=?',(digest,)).fetchone()
    previous={}
    if existing:
        try:previous=json.loads(existing[0])
        except (ValueError,TypeError):pass
    if metadata.get('kind')=='original' and previous.get('kind')=='original':metadata=_merge_original_metadata(previous,metadata)
    metadata={**metadata,'id':digest,'mime':mime,'bytes':len(content)}
    with _connection(directory) as db:
        if metadata.get('kind')=='crop':
            db.execute('INSERT OR REPLACE INTO crop_links VALUES(?,?,?,?,?)',(metadata.get('originalId'),digest,str(metadata.get('verification','unknown'))[:40],json.dumps(metadata,ensure_ascii=False),time.time()))
        # One byte sequence may be both an original and another photo's crop.
        # Preserve the original row; the derivative association lives separately.
        if not(previous.get('kind')=='original' and metadata.get('kind')=='crop'):
            db.execute('INSERT OR REPLACE INTO assets VALUES (?,?,?)',(digest,mime,json.dumps(metadata,ensure_ascii=False)))
    return metadata


def asset(directory,digest):
    if not isinstance(digest,str) or not re.fullmatch(r'[a-f0-9]{64}',digest): raise ValueError('Неверный ID фотографии')
    with _connection(directory) as db: row=db.execute('SELECT mime,metadata FROM assets WHERE id=?',(digest,)).fetchone()
    if not row: raise ValueError('Фотография ещё не загружена')
    from workspace_ownership import owned_path
    content=owned_path(directory,Path('media')/digest).read_bytes()
    if hashlib.sha256(content).hexdigest()!=digest: raise ValueError('Сохранённый файл повреждён; загрузите оригинал повторно')
    return content,row[0],json.loads(row[1])


def image_mime(content):
    """Use bytes, not a .jpg suffix or browser/HTTP label, as the MIME evidence."""
    if content.startswith(b'\xff\xd8\xff'):return 'image/jpeg'
    if content.startswith(b'\x89PNG\r\n\x1a\n'):return 'image/png'
    if content.startswith(b'RIFF') and content[8:12]==b'WEBP':return 'image/webp'
    raise ValueError('Сигнатура не соответствует JPEG, PNG или WebP')


def _original_metadata(content,image,reported_mime):
    Image,_=pillow()
    with Image.open(io.BytesIO(content)) as raw:
        actual={'JPEG':'image/jpeg','PNG':'image/png','WEBP':'image/webp'}.get(raw.format)
        if actual!=image_mime(content):raise ValueError('Декодер и сигнатура фото не согласованы')
        try:orientation=int(raw.getexif().get(274,1))
        except (ValueError,TypeError):orientation=1
        return actual,{'width':image.width,'height':image.height,
            'encodedWidth':raw.width,'encodedHeight':raw.height,'exifOrientation':orientation,
            'orientationNormalized':True,'coordinateSpace':'exif-transposed-normalized',
            'reportedMime':str(reported_mime or '')[:80],'mimeEvidence':'magic-and-decoder',
            'kind':'original','markState':'not-checked','cleanlinessConfirmed':False}


def _source_metadata(source):
    if source is None:return {}
    if not isinstance(source,dict):raise ValueError('Неверные метаданные источника фото')
    selected={}
    photo_id=source.get('photoId')
    if isinstance(photo_id,(str,int)) and not isinstance(photo_id,bool):
        text=str(photo_id)
        if re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}',text):selected['photoId']=text
    order=source.get('order')
    if isinstance(order,int) and not isinstance(order,bool) and 0<=order<1000:selected['order']=order
    for key in ('isLayout','isCianLayout'):
        if isinstance(source.get(key),bool):selected[key]=source[key]
    variants=source.get('variants')
    if isinstance(variants,dict):
        safe={}
        for key in ('fullUrl','thumbnailUrl','thumbnail2Url','miniUrl'):
            if isinstance(variants.get(key),str):
                try:safe[key]=redcn.allowed_url(variants[key],images=True,resolve=False)
                except ValueError:pass
        if safe:selected['variants']=safe
    observed=source.get('observedVariants')
    if isinstance(observed,list):
        safe=[]
        for row in observed[:60]:
            if not isinstance(row,dict) or row.get('source') not in ('data-original','data-src','data-srcset','srcset','currentSrc','src','href'):continue
            try:url=redcn.allowed_url(row.get('url'),images=True,resolve=False)
            except (ValueError,TypeError):continue
            if not any(item['url']==url for item in safe):safe.append({'url':url,'source':row['source']})
        if safe:selected['observedVariants']=safe[:60]
    if source.get('identitySource') in ('platform-photo-id','observed-dom-node','observed-picture','observed-cian-photo-url'):selected['identitySource']=source['identitySource']
    return selected


def _annotate_source(directory,metadata,source):
    selected=_source_metadata(source)
    if not selected:return metadata
    metadata=_merge_original_metadata(metadata,selected)
    with _connection(directory) as db:
        db.execute('UPDATE assets SET metadata=? WHERE id=?',(json.dumps(metadata,ensure_ascii=False),metadata['id']))
    return metadata


def cached_observed_original(directory,url,*,verify_bytes=True,source=None,allow_unscoped_variants=False):
    """A resolution-eligible observed fallback, never an invented full image."""
    from photo_variants import variant_decision
    if source is None and not allow_unscoped_variants:return None
    decision=variant_decision(directory,url,source=source,verify_bytes=verify_bytes)
    selected=decision.get('selected') if isinstance(decision,dict) else None
    if not decision or decision.get('status')!='selected' or not isinstance(selected,dict) or selected.get('quality',{}).get('resolutionEligible') is not True:return None
    if verify_bytes:metadata=asset(directory,selected['id'])[2]
    else:
        with _connection(directory) as db:row=db.execute('SELECT metadata FROM assets WHERE id=?',(selected['id'],)).fetchone()
        metadata=json.loads(row[0]) if row else {}
    if metadata.get('kind')!='original' or metadata.get('id')!=selected['id']:return None
    return {**metadata,'sourceUrl':selected['url'],'requestedSourceUrl':url,'observedFallback':selected['url']!=url,'cached':True}


def load_observed_photo(directory,url,source,*,refresh=False,cancel=None,max_seconds=8):
    """Probe a source-declared group once, keeping each candidate's own bytes."""
    from photo_variants import probe_variants
    observed=source.get('observedVariants') if isinstance(source,dict) and isinstance(source.get('observedVariants'),list) else []
    variants=source.get('variants') if isinstance(source,dict) and isinstance(source.get('variants'),dict) else {}
    urls={value for value in [url,*variants.values(),*[row.get('url') for row in observed if isinstance(row,dict)]] if isinstance(value,str) and value}
    if not source.get('photoId') or len(urls)<=1:return load_photo(directory,url,refresh=refresh,source=source)
    original=None
    if not refresh:
        with _connection(directory) as db:cached=db.execute('SELECT id FROM photo_urls WHERE url=?',(url,)).fetchone()
        if cached:
            try:original={**asset(directory,cached[0])[2],'sourceUrl':url}
            except (OSError,ValueError):pass
    decision=probe_variants(directory,url,source,original=original,refresh=refresh,cancel=cancel,max_seconds=max_seconds)
    canonical=next((row for row in decision['candidates'] if row['url']==url and row.get('status')=='available'),None)
    selected=canonical or (decision.get('selected') if decision.get('status')=='selected' else None)
    if not selected:raise ValueError('Наблюдаемые варианты фото недоступны или доступны только маленькие превью. Оригинал не подменён миниатюрой.')
    metadata=asset(directory,selected['id'])[2]
    return {**metadata,**_source_metadata(source),'sourceUrl':selected['url'],'requestedSourceUrl':url,'cached':decision.get('cached',False),
        'observedFallback':selected['url']!=url,'variantStatus':decision['status'],'variantReason':decision['reason'],
        'variantGroupSha':decision['groupSha'],'variantDecisionSha':decision['decisionSha']}


def load_photo(directory,url,refresh=False,*,source=None,network_timeout=None,cancel=None,allow_observed_fallback=True):
    url=redcn.allowed_url(url,images=True,resolve=False)
    _source_metadata(source)
    with _guard: lock=_photo_locks.setdefault(url,threading.Lock())
    with lock:
        if not refresh:
            with _connection(directory) as db: row=db.execute('SELECT id FROM photo_urls WHERE url=?',(url,)).fetchone()
            if row:
                try:return {**_annotate_source(directory,asset(directory,row[0])[2],source),'sourceUrl':url,'cached':True}
                except (OSError,ValueError):pass
            elif allow_observed_fallback:
                try:
                    fallback=cached_observed_original(directory,url,source=source)
                    if fallback:return fallback
                except (OSError,ValueError,sqlite3.Error):pass
        options={}
        if network_timeout is not None:options['timeout']=network_timeout
        if cancel is not None:options['cancel']=cancel
        content,reported_mime=redcn.fetch_bytes(url,images=True,**options)
        image=open_photo(content)
        try:
            mime,metadata=_original_metadata(content,image,reported_mime)
            result=_save(directory,content,mime,{**metadata,'sourceUrl':url,**_source_metadata(source)})
        finally:image.close()
        with _connection(directory) as db:db.execute('INSERT OR REPLACE INTO photo_urls VALUES (?,?)',(url,result['id']))
        return {**result,'cached':False}


def attach_photo(directory,url,data,*,source=None,manual=True):
    url=redcn.allowed_url(url,images=True,resolve=False)
    if not isinstance(data,str) or not re.match(r'^data:image/(?:jpeg|png|webp);base64,',data):raise ValueError('Нужна фотография JPEG/PNG/WebP')
    try:content=base64.b64decode(data.split(',',1)[1],validate=True)
    except ValueError as error:raise ValueError('Неверный файл фото') from error
    if len(content)>redcn.MAX_IMAGE:raise ValueError('Фото больше 12 МБ')
    image=open_photo(content)
    try:
        mime,metadata=_original_metadata(content,image,data[5:data.index(';')])
        result=_save(directory,content,mime,{**metadata,'sourceUrl':url,**({'attached':True} if manual else {}),**_source_metadata(source)})
    finally:image.close()
    with _connection(directory) as db:db.execute('INSERT OR REPLACE INTO photo_urls VALUES (?,?)',(url,result['id']))
    return result


def crop_rect(width,height,margins):
    values={}
    for k in ('left','top','right','bottom'):
        value=margins.get(k,0)
        if isinstance(value,bool) or not isinstance(value,(float,int)) or not math.isfinite(value) or not 0<=value<=40:raise ValueError('Отступы должны быть числами от 0 до 40%')
        values[k]=value/100
    x,y=math.floor(width*values['left']),math.floor(height*values['top'])
    right,bottom=math.ceil(width*(1-values['right'])),math.ceil(height*(1-values['bottom']))
    if right-x<32 or bottom-y<32 or (right-x)*(bottom-y)<width*height*.55:raise ValueError('Кадр слишком мал: нужно оставить минимум 55% площади')
    return x,y,right,bottom


def _check_photo_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise ValueError('Загрузка отменена')


def _ocr_timeout(deadline, maximum):
    if deadline is None:return maximum
    remaining=deadline-time.monotonic()
    if remaining<=0:raise ValueError('Лимит автоматической проверки фото исчерпан; оригинал сохранён')
    return min(maximum,remaining)


def recognize(image,directory,*,cancel=None,deadline=None):
    """Only recognized strings and boxes are returned. No data leaves the machine."""
    _check_photo_cancel(cancel)
    folder=Path(directory)/'ocr';folder.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=folder) as temp:
        path=Path(temp)/'photo.png';image.convert('RGB').save(path)
        if sys.platform=='darwin' and Path('/usr/bin/swiftc').exists():
            source=Path(__file__).with_name('recognize-photo.swift');digest=hashlib.sha256(source.read_bytes()).hexdigest()[:16];binary=folder/('vision-'+digest)
            acquired=_ocr_lock.acquire(timeout=_ocr_timeout(deadline,120))
            if not acquired:raise ValueError('OCR занят; оригинал сохранён')
            try:
                _check_photo_cancel(cancel)
                if not binary.exists():
                    with tempfile.NamedTemporaryFile(dir=folder,prefix=binary.name+'.',delete=False) as output:temporary=Path(output.name)
                    try:
                        compiled=subprocess.run(['/usr/bin/swiftc',str(source),'-o',str(temporary)],capture_output=True,timeout=_ocr_timeout(deadline,120))
                        if compiled.returncode:raise ValueError('Не удалось собрать локальный OCR Vision; доступна ручная граница кадра')
                        temporary.chmod(0o700);temporary.replace(binary)
                    finally:temporary.unlink(missing_ok=True)
            finally:_ocr_lock.release()
            _check_photo_cancel(cancel)
            result=subprocess.run([str(binary),str(path)],capture_output=True,text=True,timeout=_ocr_timeout(deadline,30))
            _check_photo_cancel(cancel)
            if result.returncode:raise ValueError('Vision не смог прочитать фото; доступна ручная граница кадра')
            rows=json.loads(result.stdout)
            if not isinstance(rows,list) or any(not isinstance(row,dict) for row in rows):raise ValueError('OCR вернул неверный формат; нужна повторная проверка')
            return rows,'Apple Vision'
        executable=shutil.which('tesseract')
        if not executable:raise ValueError('OCR не установлен: на Mac нужен Swift/Vision; на Linux/Windows — Tesseract. Можно задать отступ вручную.')
        result=subprocess.run([executable,str(path),'stdout','-l','eng','--psm','11','tsv'],capture_output=True,text=True,timeout=_ocr_timeout(deadline,30))
        _check_photo_cancel(cancel)
        if result.returncode:raise ValueError('OCR Tesseract завершился ошибкой')
        rows=[]
        for line in result.stdout.splitlines()[1:]:
            cells=line.split('\t')
            if len(cells)<12 or not cells[11].strip():continue
            try:rows.append({'text':cells[11],'confidence':float(cells[10])/100,'x':int(cells[6])/image.width,'y':int(cells[7])/image.height,'width':int(cells[8])/image.width,'height':int(cells[9])/image.height})
            except ValueError:continue
        return rows,'Tesseract'


def _watermark_brand(text):
    """Recognize bounded OCR brand tokens, never arbitrary word substrings.

    Common Cyrillic/Latin lookalikes and adjacent OCR fragments are accepted.
    This is an OCR policy, not a trained watermark detector or a clean-photo
    guarantee. A printed word in the photographed scene still needs review.
    """
    normalized=str(text).casefold().translate(str.maketrans({
        'с':'c','ц':'c','і':'i','и':'i','а':'a','н':'n','о':'o','т':'t','в':'v'}))
    for brand,pattern in (('cian',r'c[i1l]an'),('avito',r'av[i1l]to')):
        if re.search(r'(?<![a-zа-яё0-9])'+pattern+r'(?:\.ru)?(?![a-zа-яё0-9])',normalized):return brand
        # Joining is useful only if the whole measured group is the brand.
        # "musician", "Avitometer" and neighbouring unrelated words are not.
        compact=re.sub(r'[\s.·-]','',normalized)
        if re.fullmatch(pattern+r'(?:ru)?',compact):return brand
    return None


def watermark_boxes(rows,allow_id_footer=False):
    # OCR may split a brand into several adjacent words. Join only nearby
    # tokens on the same line, keeping their measured union as evidence.
    safe_rows=[]
    for row in rows if isinstance(rows,list) else []:
        if not isinstance(row,dict):continue
        try:
            numbers={k:float(row[k]) for k in ('x','y','width','height','confidence')}
            if not all(math.isfinite(v) for v in numbers.values()):continue
            if any(type(row[k]) is bool for k in numbers):continue
            if not (0<=numbers['confidence']<=1 and 0<=numbers['x']<1 and 0<=numbers['y']<1
                    and numbers['width']>0 and numbers['height']>0
                    and numbers['x']+numbers['width']<=1.00001
                    and numbers['y']+numbers['height']<=1.00001):continue
            safe_rows.append({**row,**numbers})
        except (KeyError,TypeError,ValueError):continue
    rows=safe_rows
    joined=[]
    for first in rows:
        try:
            line=[row for row in rows if abs(float(row['y'])-float(first['y']))<=max(float(first['height']),float(row['height']))*.35]
            line.sort(key=lambda row:float(row['x']))
            for start in range(len(line)):
                group=[line[start]]
                for row in line[start+1:start+4]:
                    previous=group[-1]
                    if float(row['x'])-(float(previous['x'])+float(previous['width']))>max(float(row['height']),float(previous['height']))*1.8:break
                    group.append(row)
                    x=min(float(v['x']) for v in group);y=min(float(v['y']) for v in group)
                    joined.append({'text':' '.join(str(v.get('text','')) for v in group),'confidence':min(float(v.get('confidence',0)) for v in group),'x':x,'y':y,'width':max(float(v['x'])+float(v['width']) for v in group)-x,'height':max(float(v['y'])+float(v['height']) for v in group)-y})
        except (KeyError,TypeError,ValueError):continue
    result=[]
    for row in [*rows,*joined]:
        original=str(row.get('text','')).lower()
        brand=_watermark_brand(original)
        footer=allow_id_footer and re.search(r'\bid\s*[:#]?\s*\d{4,}',original) and float(row.get('x',0))>.45 and float(row.get('y',0))>.65 and float(row.get('confidence',0))>=.8
        if not (brand or footer):continue
        if float(row.get('confidence',0))<.45:continue
        try:box={k:float(row[k]) for k in ('x','y','width','height')}
        except (KeyError,TypeError,ValueError):continue
        if not all(math.isfinite(v) and 0<=v<=1 for v in box.values()) or box['width']<=0 or box['height']<=0 or box['x']+box['width']>1.00001 or box['y']+box['height']>1.00001:continue
        if footer:
            # CIAN's pin + brand sit above its ID line; scale the exclusion
            # zone with the measured text height instead of fixed pixels.
            box['y']=max(0,box['y']-3.3*box['height']);box['height']=min(1-box['y'],box['height']*2)
        candidate={**box,'text':row['text'],'confidence':row.get('confidence'),'brand':'cian' if footer else brand,'method':'cian-id-footer' if footer else 'brand-text'}
        if not any(all(abs(candidate[k]-other[k])<.001 for k in ('x','y','width','height')) for other in result):result.append(candidate)
    return result


def detect_photo_marks(image,directory,allow_id_footer=False,*,cancel=None,deadline=None):
    options={'cancel':cancel,'deadline':deadline} if cancel is not None or deadline is not None else {}
    rows,engine=recognize(image,directory,**options);boxes=watermark_boxes(rows,allow_id_footer)
    if boxes:return boxes,engine
    # A low-contrast footer can disappear in whole-image OCR. Inspect it at
    # higher resolution, with two contrast treatments, then map boxes back.
    from PIL import ImageOps, ImageEnhance, ImageFilter, ImageChops
    y=int(image.height*.60);footer=image.crop((0,y,image.width,image.height)).convert('L')
    variants=[ImageEnhance.Contrast(ImageOps.autocontrast(footer)).enhance(2),ImageOps.autocontrast(ImageChops.subtract(footer,footer.filter(ImageFilter.GaussianBlur(9)),scale=1,offset=128))]
    for variant in variants:
        enlarged=variant.resize((variant.width*2,variant.height*2))
        try:found,_=recognize(enlarged,directory,**options)
        finally:enlarged.close();variant.close()
        if not isinstance(found,list) or any(not isinstance(row,dict) for row in found):
            footer.close();raise ValueError('OCR вернул неверный формат; нужна повторная проверка')
        try:mapped=[{**row,'y':(y+float(row['y'])*(image.height-y))/image.height,'height':float(row['height'])*(image.height-y)/image.height} for row in found]
        except (KeyError,TypeError,ValueError) as error:
            footer.close();raise ValueError('OCR вернул неверные координаты; нужна повторная проверка') from error
        boxes=watermark_boxes(mapped,allow_id_footer)
        if boxes:footer.close();return boxes,engine+' · усиление контраста'
    footer.close();return [],engine


def propose_crop(width,height,boxes):
    if min(width,height)<AUTO_CROP_MIN_SHORT_SIDE:return {'status':'review','reason':'small-image','message':'Маленькое изображение: автоматическая обрезка отключена, оригинал сохранён.'}
    if not boxes:return {'status':'review','message':'Метка ЦИАН или Авито не найдена уверенно. Оригинал сохранён; выберите отступ вручную.'}
    margins={'left':0,'top':0,'right':0,'bottom':0}
    # Padding covers the pin/icon adjacent to or above the OCR text.
    for box in boxes:
        x,y,w,h=(box[k] for k in ('x','y','width','height'))
        # ID footer boxes already include the icon above the text. Avoid
        # adding the icon height a second time; keep a small safety gap.
        vertical_pad=max(.008,h*.3) if box.get('method')=='cian-id-footer' else max(.025,h*1.25)
        horizontal_pad=max(.035,w*.25)
        choices={'bottom':(1-y+vertical_pad)*100,'top':(y+h+vertical_pad)*100,'right':(1-x+horizontal_pad)*100,'left':(x+w+horizontal_pad)*100}
        edge,value=min(choices.items(),key=lambda v:v[1])
        if value>28:return {'status':'review','message':'Метка далеко от края: автоматическая обрезка уберёт слишком много кадра. Нужна проверка.','boxes':boxes}
        margins[edge]=max(margins[edge],round(value,4))
    try: rect=crop_rect(width,height,margins)
    except ValueError as error:return {'status':'review','message':str(error),'boxes':boxes}
    retained=(rect[2]-rect[0])*(rect[3]-rect[1])/(width*height)
    if retained<1-AUTO_CROP_MAX_AREA_LOSS:return {'status':'review','reason':'area-loss','message':'Предложенная обрезка убирает более 25% площади; нужна ручная проверка.','boxes':boxes,'retainedArea':round(retained,4)}
    return {'status':'detected','margins':margins,'rect':rect,'boxes':boxes,'method':'normalized-ocr-edge','retainedArea':round(retained,4)}


def crop_photo(directory,digest,margins=None,*,cancel=None,deadline=None,min_confidence=0):
    _check_photo_cancel(cancel)
    content,_,original=asset(directory,digest)
    if original.get('kind')!='original':raise ValueError('Обрезка выполняется только из оригинала')
    if margins is None and (original.get('isLayout') or original.get('isCianLayout')):return {'status':'review','reason':'layout','originalId':digest,'message':'Планировка: автоматическая обрезка отключена; доступна ручная граница.'}
    image=open_photo(content)
    try:
        engine=None;boxes=[];allow_id_footer=bool(re.match(r'https://(?:[\w-]+\.)*(?:cian\.ru|cdn-cian\.ru|ciancdn\.ru)/',original.get('sourceUrl','')))
        if margins is None:
            options={'cancel':cancel,'deadline':deadline} if cancel is not None or deadline is not None else {}
            try: boxes,engine=detect_photo_marks(image,directory,allow_id_footer,**options)
            except (OSError,ValueError,subprocess.SubprocessError) as error:return {'status':'review','message':str(error),'originalId':digest,'retryable':True}
            boxes=[box for box in boxes if float(box.get('confidence',0))>=min_confidence]
            proposal=propose_crop(image.width,image.height,boxes)
            if proposal['status']!='detected':return {**proposal,'ocr':engine,'originalId':digest}
            margins=proposal['margins']
        if not isinstance(margins,dict):raise ValueError('Неверные отступы кадра')
        rect=crop_rect(image.width,image.height,margins);cropped=image.crop(rect).convert('RGB')
        verification='manual';remaining=[]
        if engine:
            try: rows,_=recognize(cropped,directory,**options);remaining=watermark_boxes(rows,allow_id_footer);verification='mark-remains' if remaining else 'ocr-checked'
            except (OSError,ValueError,subprocess.SubprocessError):verification='not-checked'
        buffer=io.BytesIO();cropped.save(buffer,'JPEG',quality=95,subsampling=0)
        if buffer.getvalue()==content:
            cropped.close()
            return {**original,'originalId':digest,'status':'unchanged','message':'Границы не изменили исходные байты. Оригинал сохранён без новой производной.'}
        metadata=_save(directory,buffer.getvalue(),'image/jpeg',{**_source_metadata(original),'kind':'crop','originalId':digest,'sourceUrl':original['sourceUrl'],'width':cropped.width,'height':cropped.height,'margins':margins,'rect':rect,'ocr':engine,'verification':verification,'cropMethod':'normalized-ocr-edge' if engine else 'manual','coordinateSpace':'exif-transposed-normalized','originalWidth':image.width,'originalHeight':image.height,'markState':'known-mark-excluded' if verification=='ocr-checked' else 'requires-review','cleanlinessConfirmed':False,'verificationEvidence':{'knownRegionsExcluded':bool(engine) and all((box['x']+box['width'])*image.width<=rect[0] or box['x']*image.width>=rect[2] or (box['y']+box['height'])*image.height<=rect[1] or box['y']*image.height>=rect[3] for box in boxes),'postOcr':'mark-detected' if remaining else 'not-detected' if verification=='ocr-checked' else 'not-checked'},'retainedArea':round(cropped.width*cropped.height/(image.width*image.height),4)})
        cropped.close()
        return {**metadata,'status':'review' if verification in ('mark-remains','not-checked') else 'cropped','boxes':boxes,'remaining':remaining,'message': 'В копии ещё распознаётся метка: проверьте кадр.' if remaining else 'Копия кадрирована; оригинал сохранён.'}
    finally:image.close()


def auto_crop_photo(directory,digest,*,cancel=None,max_seconds=AUTO_CROP_SECONDS,deadline=None):
    """Reuse a checked decision or attempt one bounded, reversible edge crop.

    Empty/uncertain detections keep the original. Failed OCR is retryable and is
    not remembered as a negative finding. No unverified derivative is selected.
    """
    _check_photo_cancel(cancel)
    if isinstance(max_seconds,bool) or not isinstance(max_seconds,(int,float)) or not 0<max_seconds<=60:
        raise ValueError('Лимит обработки фотографии: от 0 до 60 секунд')
    if not isinstance(digest,str) or not re.fullmatch(r'[a-f0-9]{64}',digest):raise ValueError('Неверный ID фотографии')
    original=asset(directory,digest)[2]
    if original.get('isLayout') or original.get('isCianLayout'):return {'originalId':digest,'status':'review','autoStatus':'skipped','reason':'layout','cached':False,'markState':'requires-review','cleanlinessConfirmed':False}
    if min(original.get('width',0),original.get('height',0))<AUTO_CROP_MIN_SHORT_SIDE:return {'originalId':digest,'status':'review','autoStatus':'skipped','reason':'small-image','cached':False,'markState':'requires-review','cleanlinessConfirmed':False}
    parameters={'minConfidence':.8,'maxAreaLoss':AUTO_CROP_MAX_AREA_LOSS,'minShortSide':AUTO_CROP_MIN_SHORT_SIDE,'coordinateSpace':'exif-transposed-normalized'}
    policy=AUTO_CROP_POLICY+'-'+hashlib.sha256(Path(__file__).with_name('recognize-photo.swift').read_bytes()+json.dumps(parameters,sort_keys=True).encode()).hexdigest()[:12]
    end=min(time.monotonic()+max_seconds,deadline) if deadline is not None else time.monotonic()+max_seconds
    while not _auto_crop_lock.acquire(timeout=min(.2,max(.001,end-time.monotonic()))):
        _check_photo_cancel(cancel)
        if time.monotonic()>=end:return {'originalId':digest,'status':'review','autoStatus':'skipped','reason':'time-budget','cached':False}
    try:
        with _connection(directory) as db:
            db.execute('CREATE TABLE IF NOT EXISTS auto_crop_decisions (original_id TEXT,policy TEXT,decision TEXT,PRIMARY KEY(original_id,policy))')
            row=db.execute('SELECT decision FROM auto_crop_decisions WHERE original_id=? AND policy=?',(digest,policy)).fetchone()
            manual_rows=db.execute('SELECT metadata FROM crop_links WHERE original_id=? ORDER BY created DESC',(digest,)).fetchall()+db.execute('SELECT metadata FROM assets WHERE instr(metadata,?)>0 ORDER BY rowid DESC',(digest,)).fetchall()
        # An operator's explicit crop has priority over a later automatic pass.
        for raw, in manual_rows:
            try:
                meta=json.loads(raw)
                if meta.get('kind')=='crop' and meta.get('originalId')==digest and meta.get('verification')=='manual':
                    asset(directory,meta['id'])
                    return {**meta,'originalId':digest,'status':'cropped','autoStatus':'manual','cached':True}
            except (OSError,ValueError,KeyError,TypeError):continue
        if row:
            try:
                decision=json.loads(row[0])
                # A missing/corrupt derivative must not become a cached success.
                if decision.get('autoStatus')=='cropped':
                    meta=asset(directory,decision['id'])[2]
                    if meta.get('verification')!='ocr-checked' or meta.get('originalId')!=digest:raise ValueError('Копия не проверена')
                return {**decision,'cached':True}
            except (OSError,ValueError,KeyError,TypeError):pass
        _check_photo_cancel(cancel)
        if time.monotonic()>=end:return {'originalId':digest,'status':'review','autoStatus':'skipped','reason':'time-budget','cached':False}
        decision=crop_photo(directory,digest,cancel=cancel,deadline=end,min_confidence=.8)
        _check_photo_cancel(cancel)
        verified=decision.get('status')=='cropped' and decision.get('verification')=='ocr-checked'
        auto_status='cropped' if verified else 'unavailable' if decision.get('retryable') or decision.get('verification')=='not-checked' else 'review' if decision.get('boxes') else 'unchanged'
        decision={**decision,'autoStatus':auto_status,'policy':policy,'originalId':digest,'cached':False,'markState':decision.get('markState') or ('detected' if decision.get('boxes') else 'not-checked' if auto_status in ('unavailable','skipped') else 'not-detected'),'cleanlinessConfirmed':False,'parameters':parameters}
        if not decision.get('boxes') and auto_status=='unchanged':decision['message']='Уверенная метка не обнаружена; оригинал сохранён без изменений.'
        if auto_status not in ('unavailable','skipped'):
            with _connection(directory) as db:
                db.execute('INSERT OR REPLACE INTO auto_crop_decisions VALUES (?,?,?)',(digest,policy,json.dumps(decision,ensure_ascii=False)))
        return decision
    finally:_auto_crop_lock.release()


def analyze_photo(directory,digest,*,cancel=None,deadline=None,status=None):
    """Analyze the verified original; suggested objects never become facts here."""
    import vision_photo
    from ai_telemetry import sanitize_ai
    from job_progress import emit
    started=time.monotonic()
    _check_photo_cancel(cancel)
    if deadline is not None and deadline<=time.monotonic():raise ValueError('Время анализа фото истекло')
    content,mime,original=asset(directory,digest)
    if original.get('kind')!='original':raise ValueError('Распознавание выполняется по оригиналу')
    if original.get('isLayout') or original.get('isCianLayout'):raise ValueError('Планировка не анализируется как фотография комнаты')
    folder=Path(directory)/'vision';folder.mkdir(parents=True,exist_ok=True)
    from photo_ai_settings import load_config
    config=load_config(directory)
    trained_identity=b''
    if config.get('model')=='redcn-furniture-v1':
        import trained_photo_detector
        trained_identity=Path(trained_photo_detector.__file__).read_bytes()+trained_photo_detector.cache_identity(directory).encode()
    elif config.get('model')=='redcn-furniture-world-v2':
        import trained_photo_detector, world_photo_detector
        trained_identity=(Path(trained_photo_detector.__file__).read_bytes()+Path(world_photo_detector.__file__).read_bytes()
            +world_photo_detector.cache_identity(directory).encode())
    source_hash=hashlib.sha256(Path(vision_photo.__file__).read_bytes()+Path(__file__).with_name('classify-photo.swift').read_bytes()+Path(__file__).with_name('local_photo_ai.py').read_bytes()+Path(__file__).with_name('photo_auto_policy.py').read_bytes()+trained_identity+json.dumps(config,sort_keys=True).encode()).hexdigest()[:16]
    cache=folder/(digest+'-'+source_hash+'.json')
    if cache.exists():
        try:
            saved=json.loads(cache.read_text())
            if isinstance(saved,dict) and saved.get('originalId')==digest and saved.get('status')=='review' and not saved.get('fallback'):
                _check_photo_cancel(cancel)
                cached_ai=sanitize_ai({**(saved.get('ai') if isinstance(saved.get('ai'),dict) else {}),
                    'backend':saved.get('provider'),'model':saved.get('model'),'operation':'furniture',
                    'phase':'cached','cached':True,'elapsedSeconds':time.monotonic()-started})
                emit(status,'Наблюдения по этому оригиналу загружены из кеша',stage='vision',ai=cached_ai)
                return {**saved,'cached':True,'ai':cached_ai}
        except (OSError,ValueError):pass
    image=open_photo(content)
    emit(status,'Анализируем оригинал: мебель и техника',stage='vision',ai=sanitize_ai({
        'backend':{'redcn-furniture-v1':'yolox-trained','redcn-furniture-world-v2':'onnx-world'}.get(config.get('model'),
            'ollama' if config.get('enabled') else 'apple-vision'),
        'model':config.get('model'),'operation':'furniture','phase':'loading','cached':False,'elapsedSeconds':time.monotonic()-started}))
    try:result={**vision_photo.analyze_image(image,directory,content=content,mime=mime,cancel=cancel,deadline=deadline,status=status),'originalId':digest,'sourceUrl':original.get('sourceUrl'),'width':image.width,'height':image.height,'cached':False}
    finally:image.close()
    _check_photo_cancel(cancel)
    result['ai']=sanitize_ai({**(result.get('ai') if isinstance(result.get('ai'),dict) else {}),
        'backend':result.get('provider'),'model':result.get('model'),'operation':'furniture',
        'phase':'complete' if result.get('status')=='review' else 'unavailable',
        'cached':False,'elapsedSeconds':time.monotonic()-started})
    emit(status,result.get('message','Анализ фото завершён'),stage='vision',ai=result['ai'])
    if result.get('status')=='review' and not result.get('fallback'):
        with tempfile.NamedTemporaryFile(mode='w',dir=folder,encoding='utf-8',delete=False) as stream:
            temp=Path(stream.name);temp.chmod(0o600);json.dump(result,stream,ensure_ascii=False)
        temp.replace(cache)
    return result


def visual_signature(image):
    """Conservative similarity hint, never an identity or deletion decision.

    Compare the central scene above the usual footer. A small distance can also
    describe different plain rooms; callers must retain every original.
    """
    Image,ImageOps=pillow()
    center=image.crop((int(image.width*.05),int(image.height*.05),
                       int(image.width*.95),int(image.height*.80)))
    gray=ImageOps.grayscale(center)
    resized=gray.resize((17,16),Image.Resampling.LANCZOS)
    try:
        pixels=list(resized.get_flattened_data() if hasattr(resized,'get_flattened_data') else resized.getdata());number=0
        for y in range(16):
            for x in range(16):number=(number<<1)|int(pixels[y*17+x]>pixels[y*17+x+1])
        return {'algorithm':'central-dhash256-v1','value':format(number,'064x'),
                'region':[.05,.05,.95,.80],'decision':'review-only'}
    finally:center.close();gray.close();resized.close()


def near_duplicate_candidates(directory,digests,*,distance_limit=10):
    """Return review pairs only. Never merges, excludes or deletes any asset."""
    if not isinstance(distance_limit,int) or isinstance(distance_limit,bool) or not 0<=distance_limit<=32:
        raise ValueError('Порог сходства: целое число от 0 до 32')
    if not isinstance(digests,list) or len(digests)>60:raise ValueError('Сравнение: до 60 фотографий')
    rows=[];seen=set()
    for digest in digests:
        if digest in seen:continue
        seen.add(digest);content,_,meta=asset(directory,digest)
        if meta.get('kind')!='original' or meta.get('isLayout') or meta.get('isCianLayout'):continue
        image=open_photo(content)
        try:signature=visual_signature(image)
        finally:image.close()
        rows.append({'id':digest,'signature':signature,'width':meta['width'],'height':meta['height']})
    pairs=[]
    for index,left in enumerate(rows):
        for right in rows[index+1:]:
            ratio=left['width']/left['height']/(right['width']/right['height'])
            if not .97<=ratio<=1.03:continue
            distance=(int(left['signature']['value'],16)^int(right['signature']['value'],16)).bit_count()
            if distance<=distance_limit:pairs.append({'leftId':left['id'],'rightId':right['id'],
                'distance':distance,'bits':256,'algorithm':'central-dhash256-v1',
                'status':'candidate','requiresReview':True,'action':'retain-both'})
    return sorted(pairs,key=lambda row:(row['distance'],row['leftId'],row['rightId']))


def accepted_photo_for_url(directory, url, *, margins=None, prefer_original=False, verify_bytes=True, original=None,source=None,allow_unscoped_variants=False):
    """Select cached evidence only; no download, crop or OCR.

    Polling may use verify_bytes=False: delivery through asset() still verifies
    the selected bytes. A saved per-card margin never selects a different crop.
    """
    url=redcn.allowed_url(url,images=True,resolve=False)
    if source is not None:_source_metadata(source)
    if margins is not None:
        if not isinstance(margins,dict):raise ValueError('Неверные сохранённые границы кадра')
        crop_rect(1000,1000,margins)
        margins={key:float(margins.get(key,0)) for key in ('left','top','right','bottom')}
    with _connection(directory) as db:
        row=db.execute('SELECT id FROM photo_urls WHERE url=?',(url,)).fetchone()
        canonical_mapped=row is not None
        if not row:
            original=cached_observed_original(directory,url,verify_bytes=verify_bytes,source=source,allow_unscoped_variants=allow_unscoped_variants)
            if not original:raise ValueError('Оригинал фотографии ещё не сохранён')
            row=(original['id'],)
        candidates=db.execute('SELECT metadata FROM crop_links WHERE original_id=? ORDER BY created DESC',(row[0],)).fetchall()+db.execute('SELECT metadata FROM assets WHERE instr(metadata,?)>0 ORDER BY rowid DESC',(row[0],)).fetchall()
        if not verify_bytes and (not isinstance(original,dict) or original.get('id')!=row[0]):
            raw=db.execute('SELECT metadata FROM assets WHERE id=?',(row[0],)).fetchone();original=json.loads(raw[0]) if raw else {}
    actual_url=url if canonical_mapped else original.get('sourceUrl',url)
    if verify_bytes:original=asset(directory,row[0])[2]
    original={**original,'sourceUrl':actual_url}
    if original.get('kind')!='original':raise ValueError('URL не связан с оригиналом фотографии')
    if isinstance(source,dict) and source.get('photoId'):original={**original,'photoId':str(source['photoId'])}
    def choose_crop(base,verifications):
        for verification in verifications:
            for raw, in candidates:
                try:
                    meta=json.loads(raw)
                    if meta.get('kind')!='crop' or meta.get('originalId')!=base['id'] or meta.get('verification')!=verification:continue
                    if not isinstance(meta.get('margins'),dict):continue
                    rectangle=crop_rect(base['width'],base['height'],meta['margins'])
                    if (rectangle[2]-rectangle[0],rectangle[3]-rectangle[1])!=(meta.get('width'),meta.get('height')):continue
                    if margins is not None and {key:float((meta.get('margins') or {}).get(key,0)) for key in margins}!=margins:continue
                    if verification=='ocr-checked':
                        evidence=meta.get('verificationEvidence') or {}
                        if evidence.get('knownRegionsExcluded') is not True or evidence.get('postOcr')!='not-detected':continue
                    if verify_bytes:asset(directory,meta['id'])
                    else:
                        target=Path(directory)/'media'/meta['id']
                        if target.is_symlink() or not target.is_file():continue
                    return meta
                except (KeyError,ValueError,TypeError,OSError):continue
        return None
    selected_original=original;variant_proof=None;decision=None
    chosen=choose_crop(original,('manual',)) if not prefer_original else None
    if not chosen and not prefer_original and margins is None:
        try:
            from photo_variants import variant_decision
            decision=variant_decision(directory,url,source=source,verify_bytes=verify_bytes) if source is not None or allow_unscoped_variants else None
            canonical=next((row for row in decision.get('candidates',[]) if row.get('url')==url and row.get('status')=='available'),None) if isinstance(decision,dict) else None
            if canonical and canonical.get('id')!=original['id']:decision=None
            if decision and canonical is None and original.get('sourceUrl')==url and min(original.get('width',0),original.get('height',0))>=480 and original.get('width',0)*original.get('height',0)>=400_000:decision=None
            if original.get('attached') is True:decision=None
            selected=decision.get('selected') if isinstance(decision,dict) else None
            if (decision and decision.get('status')=='selected' and isinstance(selected,dict)
                    and selected.get('quality',{}).get('resolutionEligible') is True and (selected.get('id')!=original['id'] or selected.get('url')!=url)):
                if verify_bytes:selected_original=asset(directory,selected['id'])[2]
                else:
                    with _connection(directory) as db:
                        saved=db.execute('SELECT metadata FROM assets WHERE id=?',(selected['id'],)).fetchone()
                    selected_original=json.loads(saved[0]) if saved else original
                if selected_original.get('id')==selected.get('id'):selected_original={**selected_original,'sourceUrl':selected['url'],'photoId':decision['photoId']}
                if selected_original.get('id')==selected['id'] and selected_original.get('kind')=='original':
                    if selected_original['id']!=original['id']:
                        with _connection(directory) as db:candidates+=db.execute('SELECT metadata FROM crop_links WHERE original_id=? ORDER BY created DESC',(selected_original['id'],)).fetchall()+db.execute('SELECT metadata FROM assets WHERE instr(metadata,?)>0 ORDER BY rowid DESC',(selected_original['id'],)).fetchall()
                    variant_proof={'photoId':decision['photoId'],'canonicalOriginalId':original['id'],
                        'sourceOriginalId':selected['id'],'selectedUrl':selected['url'],'groupSha':decision['groupSha'],
                        'decisionSha':decision['decisionSha'],'policy':decision['policy'],'reason':decision['reason'],
                        'quality':selected['quality'],'cleanlinessConfirmed':selected.get('cleanlinessConfirmed') is True}
                else:selected_original=original
        except (OSError,ValueError,TypeError,KeyError,sqlite3.Error):selected_original=original
        chosen=choose_crop(selected_original,('manual','ocr-checked'))
    keys=('id','mime','bytes','width','height','sourceUrl','kind','originalId','photoId','verification','margins','rect','markState','cleanlinessConfirmed')
    def compact(meta):
        result={key:meta[key] for key in keys if key in meta}
        if meta.get('verification')=='ocr-checked':
            proof=meta.get('verificationEvidence') or {}
            result['verificationEvidence']={'knownRegionsExcluded':proof.get('knownRegionsExcluded') is True,
                'postOcr':'not-detected' if proof.get('postOcr')=='not-detected' else 'not-checked'}
        return result
    selected_view={**(chosen or selected_original),'sourceUrl':selected_original['sourceUrl']}
    output={'original':compact(original),'selectedOriginal':compact(selected_original),'selected':compact(selected_view),
            'reason':chosen['verification'] if chosen else 'explicit-original' if prefer_original else 'manual-not-cached' if margins is not None else 'observed-variant' if variant_proof else 'original'}
    if variant_proof:output['variantSelection']=variant_proof
    if decision:
        output['variantReview']={key:decision[key] for key in ('photoId','policy','groupSha','decisionSha','status','reason','truncated','retryable') if key in decision}
        output['variantReview']['candidates']=[{key:row[key] for key in ('url','id','provenance','status','availability','mime','mimeEvidence','width','height','bytes','quality','markState','cleanlinessConfirmed','reason','errorClass','message') if key in row} for row in decision.get('candidates',[]) if isinstance(row,dict)]
    return output


def exact_photo_duplicates(directory, *, maximum_cards=1000):
    """Cross-card equality is verified by SHA-256 bytes, not visual similarity."""
    if type(maximum_cards) is not int or not 1<=maximum_cards<=5000:raise ValueError('Лимит карточек: 1–5000')
    path=Path(directory)/'listings.sqlite3'
    if not path.exists():return {'groups':[],'cardsChecked':0,'truncated':False,'action':'retain-all'}
    with sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True,timeout=15) as db:
        db.execute('PRAGMA query_only=ON')
        try:cards=db.execute('SELECT id,payload FROM results ORDER BY updated DESC,id LIMIT ?',(maximum_cards+1,)).fetchall()
        except sqlite3.OperationalError:return {'groups':[],'cardsChecked':0,'truncated':False,'action':'retain-all'}
    with _connection(directory) as db:urls=dict(db.execute('SELECT url,id FROM photo_urls'))
    groups={}
    for card_id,raw in cards[:maximum_cards]:
        try:card=json.loads(raw)
        except (ValueError,TypeError):continue
        fields=card.get('fields') or {};listing=card.get('listing') or {}
        photos=fields.get('photos',listing.get('images',[]))
        if not isinstance(photos,list):continue
        for url in dict.fromkeys(item for item in photos if isinstance(item,str)):
            digest=urls.get(url)
            if digest:groups.setdefault(digest,[]).append({'cardId':card_id,'sourceUrl':url})
    result=[]
    for digest,refs in sorted(groups.items()):
        if len({ref['cardId'] for ref in refs})<2:continue
        try:content,_,meta=asset(directory,digest)
        except (ValueError,OSError):continue
        if meta.get('kind')!='original':continue
        result.append({'sha256':digest,'bytes':len(content),'references':refs,
                       'verified':True,'action':'retain-all'})
    return {'groups':result,'cardsChecked':min(len(cards),maximum_cards),
            'truncated':len(cards)>maximum_cards,'action':'retain-all'}
