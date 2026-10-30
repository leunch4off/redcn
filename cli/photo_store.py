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
import warnings

import redcn

_ocr_lock = threading.Lock()
_photo_locks = {}
_guard = threading.Lock()
MAX_PIXELS = 40_000_000


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
    connection=sqlite3.connect(directory/'media.sqlite3',timeout=15)
    connection.execute('CREATE TABLE IF NOT EXISTS assets (id TEXT PRIMARY KEY, mime TEXT, metadata TEXT)')
    connection.execute('CREATE TABLE IF NOT EXISTS photo_urls (url TEXT PRIMARY KEY, id TEXT)')
    return connection


def _save(directory,content,mime,metadata):
    digest=hashlib.sha256(content).hexdigest();folder=Path(directory)/'media';folder.mkdir(parents=True,exist_ok=True)
    path=folder/digest
    if not path.exists():
        with tempfile.NamedTemporaryFile(dir=folder,delete=False) as f: f.write(content);temp=Path(f.name)
        temp.replace(path)
    metadata={**metadata,'id':digest,'mime':mime,'bytes':len(content)}
    with _connection(directory) as db: db.execute('INSERT OR REPLACE INTO assets VALUES (?,?,?)',(digest,mime,json.dumps(metadata,ensure_ascii=False)))
    return metadata


def asset(directory,digest):
    if not isinstance(digest,str) or not re.fullmatch(r'[a-f0-9]{64}',digest): raise ValueError('Неверный ID фотографии')
    with _connection(directory) as db: row=db.execute('SELECT mime,metadata FROM assets WHERE id=?',(digest,)).fetchone()
    if not row: raise ValueError('Фотография ещё не загружена')
    content=(Path(directory)/'media'/digest).read_bytes()
    if hashlib.sha256(content).hexdigest()!=digest: raise ValueError('Сохранённый файл повреждён; загрузите оригинал повторно')
    return content,row[0],json.loads(row[1])


def load_photo(directory,url,refresh=False):
    url=redcn.allowed_url(url,images=True,resolve=False)
    with _guard: lock=_photo_locks.setdefault(url,threading.Lock())
    with lock:
        if not refresh:
            with _connection(directory) as db: row=db.execute('SELECT id FROM photo_urls WHERE url=?',(url,)).fetchone()
            if row:
                try:return {**asset(directory,row[0])[2],'cached':True}
                except (OSError,ValueError):pass
        content,mime=redcn.fetch_bytes(url,images=True)
        valid=(mime=='image/jpeg' and content.startswith(b'\xff\xd8\xff')) or (mime=='image/png' and content.startswith(b'\x89PNG\r\n\x1a\n')) or (mime=='image/webp' and content.startswith(b'RIFF') and content[8:12]==b'WEBP')
        if not valid: raise ValueError('Сигнатура не соответствует формату фото')
        image=open_photo(content)
        try: result=_save(directory,content,mime,{'sourceUrl':url,'width':image.width,'height':image.height,'kind':'original'})
        finally:image.close()
        with _connection(directory) as db:db.execute('INSERT OR REPLACE INTO photo_urls VALUES (?,?)',(url,result['id']))
        return {**result,'cached':False}


def attach_photo(directory,url,data):
    url=redcn.allowed_url(url,images=True,resolve=False)
    if not isinstance(data,str) or not re.match(r'^data:image/(?:jpeg|png|webp);base64,',data):raise ValueError('Нужна фотография JPEG/PNG/WebP')
    try:content=base64.b64decode(data.split(',',1)[1],validate=True)
    except ValueError as error:raise ValueError('Неверный файл фото') from error
    if len(content)>redcn.MAX_IMAGE:raise ValueError('Фото больше 12 МБ')
    image=open_photo(content)
    try:result=_save(directory,content,data[5:data.index(';')],{'sourceUrl':url,'width':image.width,'height':image.height,'kind':'original','attached':True})
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


def recognize(image,directory):
    """Only recognized strings and boxes are returned. No data leaves the machine."""
    folder=Path(directory)/'ocr';folder.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=folder) as temp:
        path=Path(temp)/'photo.png';image.convert('RGB').save(path)
        if sys.platform=='darwin' and Path('/usr/bin/swiftc').exists():
            source=Path(__file__).with_name('recognize-photo.swift');digest=hashlib.sha256(source.read_bytes()).hexdigest()[:16];binary=folder/('vision-'+digest)
            with _ocr_lock:
                if not binary.exists():
                    compiled=subprocess.run(['/usr/bin/swiftc',str(source),'-o',str(binary)],capture_output=True,timeout=120)
                    if compiled.returncode:raise ValueError('Не удалось собрать локальный OCR Vision; доступна ручная граница кадра')
            result=subprocess.run([str(binary),str(path)],capture_output=True,text=True,timeout=30)
            if result.returncode:raise ValueError('Vision не смог прочитать фото; доступна ручная граница кадра')
            return json.loads(result.stdout),'Apple Vision'
        executable=shutil.which('tesseract')
        if not executable:raise ValueError('OCR не установлен: на Mac нужен Swift/Vision; на Linux/Windows — Tesseract. Можно задать отступ вручную.')
        result=subprocess.run([executable,str(path),'stdout','-l','eng','--psm','11','tsv'],capture_output=True,text=True,timeout=30)
        if result.returncode:raise ValueError('OCR Tesseract завершился ошибкой')
        rows=[]
        for line in result.stdout.splitlines()[1:]:
            cells=line.split('\t')
            if len(cells)<12 or not cells[11].strip():continue
            try:rows.append({'text':cells[11],'confidence':float(cells[10])/100,'x':int(cells[6])/image.width,'y':int(cells[7])/image.height,'width':int(cells[8])/image.width,'height':int(cells[9])/image.height})
            except ValueError:continue
        return rows,'Tesseract'


def watermark_boxes(rows,allow_id_footer=False):
    # OCR may split a brand into several adjacent words. Join only nearby
    # tokens on the same line, keeping their measured union as evidence.
    safe_rows=[]
    for row in rows if isinstance(rows,list) else []:
        if not isinstance(row,dict):continue
        try:
            numbers={k:float(row[k]) for k in ('x','y','width','height','confidence')}
            if not all(math.isfinite(v) for v in numbers.values()):continue
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
        text=re.sub(r'[^a-zа-я]','',str(row.get('text','')).lower()).translate(str.maketrans({'с':'c','і':'i','а':'a','н':'h'}))
        original=str(row.get('text','')).lower()
        footer=allow_id_footer and re.search(r'\bid\s*[:#]?\s*\d{4,}',original) and float(row.get('x',0))>.45 and float(row.get('y',0))>.65 and float(row.get('confidence',0))>=.8
        if not (re.search(r'ци[аa][нh]|c[i1l][aа][nп]|сian',original) or 'cian' in text or footer):continue
        if float(row.get('confidence',0))<.45:continue
        try:box={k:float(row[k]) for k in ('x','y','width','height')}
        except (KeyError,TypeError,ValueError):continue
        if not all(math.isfinite(v) and 0<=v<=1 for v in box.values()) or box['width']<=0 or box['height']<=0 or box['x']+box['width']>1.00001 or box['y']+box['height']>1.00001:continue
        if footer:
            # CIAN's pin + brand sit above its ID line; scale the exclusion
            # zone with the measured text height instead of fixed pixels.
            box['y']=max(0,box['y']-3.3*box['height']);box['height']=min(1-box['y'],box['height']*2)
        candidate={**box,'text':row['text'],'confidence':row.get('confidence'),'method':'cian-id-footer' if footer else 'brand-text'}
        if not any(all(abs(candidate[k]-other[k])<.001 for k in ('x','y','width','height')) for other in result):result.append(candidate)
    return result


def detect_photo_marks(image,directory,allow_id_footer=False):
    rows,engine=recognize(image,directory);boxes=watermark_boxes(rows,allow_id_footer)
    if boxes:return boxes,engine
    # A low-contrast footer can disappear in whole-image OCR. Inspect it at
    # higher resolution, with two contrast treatments, then map boxes back.
    from PIL import ImageOps, ImageEnhance, ImageFilter, ImageChops
    y=int(image.height*.60);footer=image.crop((0,y,image.width,image.height)).convert('L')
    variants=[ImageEnhance.Contrast(ImageOps.autocontrast(footer)).enhance(2),ImageOps.autocontrast(ImageChops.subtract(footer,footer.filter(ImageFilter.GaussianBlur(9)),scale=1,offset=128))]
    for variant in variants:
        enlarged=variant.resize((variant.width*2,variant.height*2))
        try:found,_=recognize(enlarged,directory)
        finally:enlarged.close();variant.close()
        mapped=[{**row,'y':(y+float(row['y'])*(image.height-y))/image.height,'height':float(row['height'])*(image.height-y)/image.height} for row in found]
        boxes=watermark_boxes(mapped,allow_id_footer)
        if boxes:footer.close();return boxes,engine+' · усиление контраста'
    footer.close();return [],engine


def propose_crop(width,height,boxes):
    if not boxes:return {'status':'review','message':'Метка ЦИАН не найдена уверенно. Оригинал сохранён; выберите отступ вручную.'}
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
    return {'status':'detected','margins':margins,'rect':rect,'boxes':boxes,'method':'normalized-ocr-edge','retainedArea':round((rect[2]-rect[0])*(rect[3]-rect[1])/(width*height),4)}


def crop_photo(directory,digest,margins=None):
    content,_,original=asset(directory,digest)
    if original.get('kind')!='original':raise ValueError('Обрезка выполняется только из оригинала')
    image=open_photo(content)
    try:
        engine=None;boxes=[];allow_id_footer=bool(re.match(r'https://(?:[\w-]+\.)*(?:cian\.ru|cdn-cian\.ru|ciancdn\.ru)/',original.get('sourceUrl','')))
        if margins is None:
            try: boxes,engine=detect_photo_marks(image,directory,allow_id_footer)
            except (OSError,ValueError,subprocess.SubprocessError) as error:return {'status':'review','message':str(error),'originalId':digest}
            proposal=propose_crop(image.width,image.height,boxes)
            if proposal['status']!='detected':return {**proposal,'ocr':engine,'originalId':digest}
            margins=proposal['margins']
        if not isinstance(margins,dict):raise ValueError('Неверные отступы кадра')
        rect=crop_rect(image.width,image.height,margins);cropped=image.crop(rect).convert('RGB')
        verification='manual';remaining=[]
        if engine:
            try: rows,_=recognize(cropped,directory);remaining=watermark_boxes(rows,allow_id_footer);verification='mark-remains' if remaining else 'ocr-checked'
            except (OSError,ValueError,subprocess.SubprocessError):verification='not-checked'
        buffer=io.BytesIO();cropped.save(buffer,'JPEG',quality=95,subsampling=0)
        metadata=_save(directory,buffer.getvalue(),'image/jpeg',{'kind':'crop','originalId':digest,'sourceUrl':original['sourceUrl'],'width':cropped.width,'height':cropped.height,'margins':margins,'rect':rect,'ocr':engine,'verification':verification,'cropMethod':'normalized-ocr-edge' if engine else 'manual','retainedArea':round(cropped.width*cropped.height/(image.width*image.height),4)})
        cropped.close()
        return {**metadata,'status':'review' if verification in ('mark-remains','not-checked') else 'cropped','boxes':boxes,'remaining':remaining,'message': 'В копии ещё распознаётся метка: проверьте кадр.' if remaining else 'Копия кадрирована; оригинал сохранён.'}
    finally:image.close()


def analyze_photo(directory,digest):
    """Analyze the verified original; suggested objects never become facts here."""
    import vision_photo
    content,_,original=asset(directory,digest)
    if original.get('kind')!='original':raise ValueError('Распознавание выполняется по оригиналу')
    folder=Path(directory)/'vision';folder.mkdir(parents=True,exist_ok=True)
    model=vision_photo.os.environ.get('REDCN_VISION_MODEL','').strip()
    source_hash=hashlib.sha256(Path(vision_photo.__file__).read_bytes()+Path(__file__).with_name('classify-photo.swift').read_bytes()+model.encode()).hexdigest()[:16]
    cache=folder/(digest+'-'+source_hash+'.json')
    if cache.exists():
        try:return {**json.loads(cache.read_text()),'cached':True}
        except (OSError,ValueError):pass
    image=open_photo(content)
    try:result={**vision_photo.analyze_image(image,directory),'originalId':digest,'sourceUrl':original.get('sourceUrl'),'width':image.width,'height':image.height,'cached':False}
    finally:image.close()
    with tempfile.NamedTemporaryFile(mode='w',dir=folder,encoding='utf-8',delete=False) as stream:
        json.dump(result,stream,ensure_ascii=False);temp=Path(stream.name)
    temp.replace(cache)
    return result
