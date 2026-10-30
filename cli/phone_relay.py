"""Opt-in LAN companion: paired phones read Mac cards; imports stay on the Mac."""
import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import hmac
import io
import ipaddress
import json
import math
from pathlib import Path
import re
import secrets
import socket
import tempfile
import threading
import time
from urllib.parse import urlsplit, parse_qs
import zipfile

import redcn
import photo_store
import http_delivery

PAIR_TTL = 600
SESSION_TTL = 30 * 86400
COOKIE = 'redcn_phone'
MAX_ZIP_BYTES = 120_000_000
PAIR_ATTEMPT_WINDOW = 60
PAIR_ATTEMPTS_PER_PEER = 6
PAIR_ATTEMPTS_TOTAL = 30


class PairingThrottled(ValueError):
    def __init__(self,retry_after):
        self.retry_after=max(1,int(math.ceil(retry_after)))
        super().__init__(f'Слишком много попыток. Подождите {self.retry_after} сек. и введите актуальный код с Mac.')


def lan_names():
    names = {'127.0.0.1', 'localhost'}
    hostname = socket.gethostname().lower().rstrip('.')
    if re.fullmatch(r'[a-z0-9.-]+', hostname):
        names.add(hostname)
        names.add(hostname.split('.')[0]+'.local')
    try:
        for address in socket.getaddrinfo(hostname, None, socket.AF_INET):
            value=address[4][0]
            if ipaddress.ip_address(value).is_private:names.add(value)
    except OSError:pass
    # Resolving an outbound interface does not send a packet or access the service.
    try:
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
            sock.connect(('192.0.2.1',9));value=sock.getsockname()[0]
            if ipaddress.ip_address(value).is_private:names.add(value)
    except OSError:pass
    return names


class PairingState:
    def __init__(self,directory):
        self.path=Path(directory)/'phone-access.json';self.path.parent.mkdir(parents=True,exist_ok=True)
        self.lock=threading.RLock();self._pair_memory=None;self._pair_attempts=[]
        try:
            self.data=json.loads(self.path.read_text())
            if not isinstance(self.data.get('secret'),str) or not isinstance(self.data.get('sessions'),dict):raise ValueError()
        except (OSError,ValueError,TypeError):
            self.data={'secret':secrets.token_hex(32),'sessions':{},'pair':None}
            self._save()

    def _save(self):
        with tempfile.NamedTemporaryFile(mode='w',dir=self.path.parent,encoding='utf-8',delete=False) as output:
            json.dump(self.data,output);temporary=Path(output.name)
        temporary.chmod(0o600);temporary.replace(self.path)

    def issue(self):
        with self.lock:
            code=secrets.token_urlsafe(32);manual=f'{secrets.randbelow(100_000_000):08d}';expires=time.time()+PAIR_TTL
            self.data['pair']={'hash':hashlib.sha256(code.encode()).hexdigest(),'manualHash':hashlib.sha256(manual.encode()).hexdigest(),'expires':expires}
            # Only hashes go to disk; the visible code and QR stay in process memory.
            self._pair_memory=(code,manual,expires);self._save()
            return code,expires

    def current(self):
        """Privileged desktop status; never returned by a public phone endpoint."""
        with self.lock:
            item=self.data.get('pair') or {}
            if not self._pair_memory or item.get('expires',0)<=time.time() or not hmac.compare_digest(item.get('hash',''),hashlib.sha256(self._pair_memory[0].encode()).hexdigest()):
                self.issue()
            return self._pair_memory

    def pair(self,code,peer='local'):
        with self.lock:
            now=time.time();cutoff=now-PAIR_ATTEMPT_WINDOW
            self._pair_attempts=[entry for entry in self._pair_attempts if entry[0]>cutoff]
            peer_attempts=[stamp for stamp,address in self._pair_attempts if address==peer]
            if len(peer_attempts)>=PAIR_ATTEMPTS_PER_PEER:
                raise PairingThrottled(peer_attempts[0]+PAIR_ATTEMPT_WINDOW-now)
            if len(self._pair_attempts)>=PAIR_ATTEMPTS_TOTAL:
                raise PairingThrottled(self._pair_attempts[0][0]+PAIR_ATTEMPT_WINDOW-now)
            self._pair_attempts.append((now,peer))
            if not isinstance(code,str):raise ValueError('Введите 8 цифр с Mac или откройте новую QR-ссылку.')
            code=code.strip();manual=re.sub(r'[\s-]','',code)
            if re.fullmatch(r'[0-9]{8}',manual):value=manual;key='manualHash'
            elif re.fullmatch(r'[A-Za-z0-9_-]{40,80}',code):value=code;key='hash'
            else:raise ValueError('Введите 8 цифр с Mac или откройте новую QR-ссылку.')
            item=self.data.get('pair') or {}
            if item.get('expires',0)<=now or not hmac.compare_digest(item.get(key,''),hashlib.sha256(value.encode()).hexdigest()):raise ValueError('Код неверный, истёк или уже использован. На Mac нажмите «Телефон» и введите текущие 8 цифр.')
            self.data['pair']=None;self._pair_memory=None
            token=secrets.token_urlsafe(32);digest=hashlib.sha256(token.encode()).hexdigest()
            self.data['sessions']={key:value for key,value in self.data['sessions'].items() if value>now}
            self.data['sessions'][digest]=now+SESSION_TTL;self._save()
            return token

    def authorized(self,token):
        if not isinstance(token,str) or not re.fullmatch(r'[A-Za-z0-9_-]{40,80}',token):return False
        with self.lock:return self.data['sessions'].get(hashlib.sha256(token.encode()).hexdigest(),0)>time.time()

    def csrf(self,token):
        return hmac.new(self.data['secret'].encode(),('csrf:'+token).encode(),hashlib.sha256).hexdigest()

    def revoke(self,token=None):
        with self.lock:
            if token:self.data['sessions'].pop(hashlib.sha256(token.encode()).hexdigest(),None)
            else:self.data['sessions']={};self.data['pair']=None;self._pair_memory=None
            self._save()


PROFILE_DATA={'style','pickup','luggage','support','arrival','registration','registrationMonths','registrationPeople','audience'}

def load_profile(directory):
    try:
        profile=json.loads((Path(directory)/'phone-profile.json').read_text())
        return profile if isinstance(profile,dict) else {}
    except (OSError,ValueError):return {}


def save_profile(directory,profile):
    value={key:profile.get(key,{}) for key in ('rules','prefs','data')}
    if not all(isinstance(item,dict) for item in value.values()):raise ValueError('Настройки телефона должны быть объектами')
    if not all(isinstance(k,str) and k.strip() and len(k)<=200 and isinstance(v,str) and len(v)<=2000 for k,v in value['rules'].items()) or len(value['rules'])>2000:raise ValueError('Неверный словарь телефона')
    if not set(value['prefs'])<={'benefits','contact','brandTags'} or not all(isinstance(v,str) and len(v)<=20000 for v in value['prefs'].values()):raise ValueError('Неверные преимущества студии')
    if not set(value['data'])<=PROFILE_DATA:raise ValueError('Телефон наследует только настройки сервиса и подачи, не факты прошлой квартиры')
    if not all(isinstance(v,(str,bool,int,float)) and (not isinstance(v,str) or len(v)<=200) for v in value['data'].values()):raise ValueError('Неверные настройки подачи')
    encoded=json.dumps(value,ensure_ascii=False)
    if len(encoded.encode())>300000:raise ValueError('Настройки телефона превышают 300 КБ')
    folder=Path(directory);folder.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w',dir=folder,encoding='utf-8',delete=False) as output:output.write(encoded);temporary=Path(output.name)
    temporary.chmod(0o600);temporary.replace(folder/'phone-profile.json')
    return {'profileSaved':True}


def source_images(result):
    fields=result.get('fields',{}) if isinstance(result.get('fields'),dict) else {}
    listing=result.get('listing',{}) if isinstance(result.get('listing'),dict) else {}
    values=fields.get('photos') or listing.get('images') or []
    edits=fields.get('photoEdits',{}) if isinstance(fields.get('photoEdits'),dict) else {}
    excluded=set(value for value in edits.get('excluded',[]) if isinstance(value,str))
    if isinstance(edits.get('order'),list):values=[value for value in edits['order'] if value in values]+list(values)
    urls=[]
    for value in values if isinstance(values,list) else []:
        try:url=redcn.allowed_url(value,images=True,resolve=False)
        except (ValueError,TypeError):continue
        if url not in urls and url not in excluded:urls.append(url)
    return urls[:30]


def cache_photos(directory,result,report=None,cancel=None):
    from job_progress import emit
    urls=source_images(result)
    if cancel and cancel.is_set():raise ValueError('Загрузка отменена')
    def load(url):
        if cancel and cancel.is_set():return {'sourceUrl':url,'error':'Загрузка отменена'}
        try:return photo_store.load_photo(directory,url)
        except (OSError,ValueError,redcn.subprocess.SubprocessError) as error:return {'sourceUrl':url,'error':str(error)}
    emit(report,'Сохраняем фотографии на Mac: '+str(len(urls)),stage='photos',completed=0,total=len(urls))
    by_url={}
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures={pool.submit(load,url):url for url in urls}
        for future in as_completed(futures):
            by_url[futures[future]]=future.result()
            emit(report,'Фотографии: '+str(len(by_url))+' из '+str(len(urls))+' получено',stage='photos',completed=len(by_url),total=len(urls))
    media=[by_url[url] for url in urls]
    result['media']=media;result['mediaStatus']='partial' if any(row.get('error') for row in media) else 'ready'
    if cancel and cancel.is_set():
        result['mediaStatus']='cancelled';raise ValueError('Загрузка отменена')
    processing={'total':len(urls),'checked':0,'cropped':0,'manual':0,'review':0,'failed':0,'skipped':0,'downloadFailed':sum(bool(row.get('error')) for row in media)}
    end=time.monotonic()+90
    emit(report,'Проверяем края фотографий и готовим безопасные копии',stage='crop',completed=0,total=len(media))
    for index,row in enumerate(media,1):
        if cancel and cancel.is_set():
            result['mediaStatus']='cancelled';raise ValueError('Загрузка отменена')
        if row.get('error') or not row.get('id'):
            processing['skipped']+=1
        else:
            emit(report,'Проверяем фото '+str(index)+' из '+str(len(media))+': метка у края, затем повторный OCR',stage='crop',completed=index-1,total=len(media))
            try:
                decision=photo_store.auto_crop_photo(directory,row['id'],cancel=cancel,deadline=end)
                state=decision.get('autoStatus','unavailable')
                row['autoCrop']={key:decision[key] for key in ('autoStatus','cached','message','reason','id') if key in decision}
                if state in ('cropped','unchanged','review','manual'):processing['checked']+=1
                if state in ('cropped','review','manual','skipped'):processing[state]+=1
                elif state=='unavailable':processing['failed']+=1
            except (OSError,ValueError,redcn.subprocess.SubprocessError) as error:
                if cancel and cancel.is_set():
                    result['mediaStatus']='cancelled';raise ValueError('Загрузка отменена') from error
                processing['failed']+=1;row['autoCrop']={'autoStatus':'unavailable','message':'Оригинал сохранён; автоматическая проверка сейчас недоступна.'}
        emit(report,'Фотографии подготовлены: '+str(index)+' из '+str(len(media))+'; безопасно обрезано '+str(processing['cropped']),stage='crop',completed=index,total=len(media))
    result['photoProcessing']=processing
    return result


def original_metadata(directory):
    # Polling must read metadata, not re-hash every full-size image on every phone.
    output={}
    with photo_store._connection(directory) as db:
        for url,digest,raw in db.execute('SELECT p.url,a.id,a.metadata FROM photo_urls p JOIN assets a ON a.id=p.id'):
            try:meta=json.loads(raw)
            except ValueError:continue
            if meta.get('id')==digest and re.fullmatch(r'[a-f0-9]{64}',digest) and (Path(directory)/'media'/digest).is_file():output[url]=meta
    return output


def card_media(directory,result,include_crops=True,originals_by_url=None):
    metadata=original_metadata(directory) if originals_by_url is None else originals_by_url
    originals=[metadata[url] for url in source_images(result) if url in metadata]
    crops={};original_ids={row['id'] for row in originals}
    if include_crops and original_ids:
        with photo_store._connection(directory) as db:
            for raw, in db.execute('SELECT metadata FROM assets ORDER BY rowid DESC'):
                try:meta=json.loads(raw)
                except ValueError:continue
                original=meta.get('originalId')
                if meta.get('kind')=='crop' and original in original_ids and meta.get('verification') in ('manual','ocr-checked') and re.fullmatch(r'[a-f0-9]{64}',str(meta.get('id',''))) and (Path(directory)/'media'/meta['id']).is_file():
                    if original not in crops or (meta.get('verification')=='manual' and crops[original].get('verification')!='manual'):crops[original]=meta
    def public(row):
        return {**{k:row[k] for k in ('id','mime','width','height','bytes','sourceUrl','kind','verification') if k in row},'url':'/api/phone/media/'+row['id'],'downloadUrl':'/api/phone/media/'+row['id']+'?download=1'}
    output=[]
    for original in originals:
        item=public(original)
        if original['id'] in crops:item['crop']=public(crops[original['id']])
        output.append(item)
    return output


def parse_cursor(value):
    identifier='f'*32
    if isinstance(value,str) and ':' in value:
        value,identifier=value.split(':',1)
        if not re.fullmatch(r'[a-f0-9]{32}',identifier):raise ValueError('Неверный курсор карточек')
    try:stamp=float(value)
    except (TypeError,ValueError) as error:raise ValueError('Неверный курсор карточек') from error
    if not math.isfinite(stamp) or not 0<=stamp<=time.time()+60:raise ValueError('Неверный курсор карточек')
    return stamp,identifier


def list_cards(directory,after=0):
    stamp,identifier=parse_cursor(after);upper=time.time()
    db=redcn.open_cache(directory)
    try:
        db.execute('CREATE TABLE IF NOT EXISTS results (id TEXT PRIMARY KEY, updated REAL, payload TEXT)')
        rows=db.execute('SELECT id,updated,payload FROM results WHERE (updated>? OR (updated=? AND id>?)) AND updated<=? ORDER BY updated,id LIMIT 201',(stamp,stamp,identifier,upper)).fetchall()
    finally:db.close()
    has_more=len(rows)>200;rows=rows[:200];cards=[];metadata=original_metadata(directory) if rows else {}
    for result_id,updated,payload in rows:
        try:result=json.loads(payload)
        except ValueError:continue
        if result.get('fields',{}).get('isDemo') is True:continue
        fields=result.get('fields',{});media=card_media(directory,result,include_crops=False,originals_by_url=metadata)
        cards.append({'id':result_id,'updated':updated,'title':(result.get('titles') or [''])[0], 'price':fields.get('price',''),'metro':fields.get('metro',''),'area':fields.get('area',''),'photoCount':len(source_images(result)),'cachedPhotoCount':len(media),'coverUrl':'/api/phone/previews/'+media[0]['id'] if media else None,'mediaStatus':result.get('mediaStatus','ready' if len(media)==len(source_images(result)) else 'pending')})
    cursor=(repr(rows[-1][1])+':'+rows[-1][0]) if has_more and rows else (repr(upper)+':'+'f'*32)
    return {'cards':cards,'cursor':cursor,'serverTime':upper,'hasMore':has_more}


def public_card(directory,result_id):
    import local_server
    result=local_server.read_result(directory,result_id)
    if result is None or result.get('fields',{}).get('isDemo') is True:return None
    db=redcn.open_cache(directory)
    try:updated=db.execute('SELECT updated FROM results WHERE id=?',(result_id,)).fetchone()[0]
    finally:db.close()
    return {**result,'id':result_id,'updated':updated,'photoCount':len(source_images(result)),'media':card_media(directory,result)}


def card_zip(directory,result_id,variant='original'):
    card=public_card(directory,result_id)
    if not card:raise ValueError('Карточка не найдена')
    if variant not in ('original','crop','prepared'):raise ValueError('Неверный вариант фотографий')
    stream=io.BytesIO();total=0;manifest=[]
    with zipfile.ZipFile(stream,'w',compression=zipfile.ZIP_STORED) as archive:
        for n,item in enumerate(card['media'],1):
            if variant=='crop' and not item.get('crop'):continue
            selected=item.get('crop',item) if variant=='prepared' else item['crop'] if variant=='crop' else item
            data,mime,_=photo_store.asset(directory,selected['id']);total+=len(data)
            if total>MAX_ZIP_BYTES:raise ValueError('Пакет больше 120 МБ. Скачайте фотографии по отдельности.')
            extension={'image/jpeg':'jpg','image/png':'png','image/webp':'webp'}[mime];name=f'{n:02}.{extension}'
            archive.writestr(name,data);manifest.append({'name':name,'sourceUrl':item.get('sourceUrl'),'kind':selected.get('kind','original')})
        if not manifest:raise ValueError('Проверенные кадрированные копии ещё не готовы' if variant=='crop' else 'Фотографии ещё не сохранены на Mac')
        archive.writestr('post.txt','\n\n'.join([*(card.get('titles') or [])[:1],card.get('body',''),card.get('tags','')]))
        archive.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
    return stream.getvalue()


def create_phone_server(directory,controller,port=8767,bind='0.0.0.0',hosts=None):
    directory=Path(directory);state=PairingState(directory);allowed=set(hosts or lan_names())
    if not allowed:raise ValueError('Не найдены локальные адреса Mac')
    def latest_job_info():
        getter=getattr(controller,'latest_job',None)
        value=getter() if callable(getter) else None
        return value if isinstance(value,dict) and re.fullmatch(r'[a-f0-9]{32}',str(value.get('id',''))) else None
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*_args):pass
        def respond(self,status,value,content_type='application/json; charset=utf-8',headers=None):
            content=json.dumps(value,ensure_ascii=False).encode() if content_type.startswith('application/json') else value
            content,compression=http_delivery.response_bytes(content,content_type,self.headers.get('Accept-Encoding',''))
            self.send_response(status)
            for key,val in {'Content-Type':content_type,'Content-Length':str(len(content)),'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer','X-Frame-Options':'DENY',**compression,**(headers or {})}.items():self.send_header(key,val)
            self.end_headers()
            try:self.wfile.write(content)
            except (BrokenPipeError,ConnectionResetError):pass
        def permitted(self,auth=True,csrf=False):
            host=self.headers.get('Host','')
            self.secure=False
            try:
                parsed=urlsplit('http://'+host)
                if parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:raise ValueError()
                external='https://'+host
                with self.server.origins_lock:trusted=external in self.server.external_origins
                if trusted:
                    if not ipaddress.ip_address(self.client_address[0]).is_loopback:raise ValueError()
                    self.secure=True;origin=external
                else:
                    if parsed.hostname not in allowed or parsed.port!=self.server.server_port:raise ValueError()
                    origin='http://'+host
            except ValueError:self.respond(403,{'error':'Используйте адрес подключения с Mac'});return False
            if self.headers.get('Origin') not in (None,origin):self.respond(403,{'error':'Неверный источник запроса'});return False
            try:
                cookies=SimpleCookie();cookies.load(self.headers.get('Cookie',''));self.session=cookies[COOKIE].value if COOKIE in cookies else ''
            except Exception:self.session=''
            if auth and not state.authorized(self.session):self.respond(401,{'paired':False,'error':'Подключите телефон по ссылке с Mac'});return False
            if csrf and not hmac.compare_digest(self.headers.get('X-Phone-CSRF',''),state.csrf(self.session)):
                self.respond(403,{'error':'Обновите приложение телефона'});return False
            return True
        def do_GET(self):
            path=urlsplit(self.path).path
            public=path in ('/phone','/phone/','/phone/pair','/phone/manifest.webmanifest','/phone/sw.js','/phone/designer.mjs','/phone/routes.mjs','/phone/icon-192.png','/phone/icon-512.png','/phone/apple-touch-icon.png','/phone/icons/icon-192.png','/phone/icons/icon-512.png','/phone/icons/apple-touch-icon.png')
            if not self.permitted(auth=not public):return
            try:
                if public:
                    if path=='/phone':self.respond(302,b'','text/plain',{'Location':'/phone/'});return
                    files={'/phone/':('docs/phone.html','text/html; charset=utf-8'),'/phone/pair':('docs/phone.html','text/html; charset=utf-8'),'/phone/manifest.webmanifest':('docs/phone.webmanifest','application/manifest+json'),'/phone/sw.js':('docs/phone-sw.js','text/javascript'),'/phone/designer.mjs':('docs/phone-designer.mjs','text/javascript; charset=utf-8'),'/phone/routes.mjs':('docs/phone-routes.mjs','text/javascript; charset=utf-8'),'/phone/icon-192.png':('docs/icons/icon-192.png','image/png'),'/phone/icon-512.png':('docs/icons/icon-512.png','image/png'),'/phone/apple-touch-icon.png':('docs/icons/apple-touch-icon.png','image/png')}
                    for icon in ('icon-192.png','icon-512.png','apple-touch-icon.png'):files['/phone/icons/'+icon]=('docs/icons/'+icon,'image/png')
                    name,mime=files[path];file=redcn.ROOT/name
                    if not file.exists():self.respond(503,{'error':'Сначала соберите мобильную страницу: npm run build'});return
                    self.respond(200,file.read_bytes(),mime);return
                if path.startswith('/api/phone/'):
                    import card_api
                    import language_service
                    workspace=getattr(controller,'workspace_api',None)
                    document=workspace(path.removeprefix('/api/phone/'),namespace='phone',query=parse_qs(urlsplit(self.path).query)) if callable(workspace) else None
                    if document:self.respond(*document);return
                    document=language_service.dispatch(directory,path.removeprefix('/api/phone/'))
                    if document:self.respond(*document);return
                    document=card_api.dispatch(directory,path.removeprefix('/api/phone/'),namespace='phone',browser_channel=getattr(controller,'browser_channel','chrome'))
                    if document:self.respond(*document);return
                if path=='/api/phone/state':self.respond(200,{'paired':True,'csrfToken':state.csrf(self.session),'serverTime':time.time(),'version':2,'activeJob':latest_job_info()});return
                if path=='/api/phone/cards':
                    after=parse_qs(urlsplit(self.path).query).get('after',['0'])[0]
                    self.respond(200,list_cards(directory,after));return
                match=re.fullmatch(r'/api/phone/previews/([a-f0-9]{64})',path)
                if match:
                    from media_preview import preview
                    data,mime,_=preview(directory,match[1]);self.respond(200,data,mime);return
                match=re.fullmatch(r'/api/phone/cards/([a-f0-9]{32})(\.zip)?',path)
                if match:
                    result_id,download=match.groups()
                    if download:
                        variant=parse_qs(urlsplit(self.path).query).get('variant',['original'])[0]
                        self.respond(200,card_zip(directory,result_id,variant),'application/zip',{'Content-Disposition':f'attachment; filename="redcn-{result_id[:8]}.zip"'});return
                    card=public_card(directory,result_id)
                    self.respond(200 if card else 404,card or {'error':'Карточка не найдена'});return
                match=re.fullmatch(r'/api/phone/media/([a-f0-9]{64})',path)
                if match:
                    data,mime,meta=photo_store.asset(directory,match[1]);headers={}
                    if parse_qs(urlsplit(self.path).query).get('download')==['1']:
                        extension={'image/jpeg':'jpg','image/png':'png','image/webp':'webp'}[mime];headers['Content-Disposition']=f'attachment; filename="redcn-{match[1][:8]}.{extension}"'
                    self.respond(200,data,mime,headers);return
                match=re.fullmatch(r'/api/phone/jobs/([a-f0-9]{32})',path)
                if match:
                    job=controller.job_status(match[1]);self.respond(200 if job else 404,job or {'error':'Задача не найдена'});return
                self.respond(404,{'error':'Не найдено'})
            except (ValueError,TypeError,OSError) as error:self.respond(400,{'error':str(error)})
        def do_POST(self):
            path=urlsplit(self.path).path;pair=path=='/api/phone/pair'
            if not self.permitted(auth=not pair,csrf=not pair):return
            try:
                if self.headers.get_content_type()!='application/json':raise ValueError('Нужен JSON')
                size=int(self.headers.get('Content-Length','0'))
                import card_api
                import language_service
                relative=path.removeprefix('/api/phone/')
                language_route=relative=='translate' or re.fullmatch(r'cards/[a-f0-9]{32}/(?:rewrite|edit-text)',relative)
                limit=card_api.request_limit(relative) or (256_000 if language_route else 100_000)
                if not 1<=size<=limit:raise ValueError('Превышен размер запроса')
                data=json.loads(self.rfile.read(size))
                if not isinstance(data,dict):raise ValueError('Нужен JSON-объект')
                if pair:
                    token=state.pair(data.get('code'),self.client_address[0]);cookie=f'{COOKIE}={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_TTL}'
                    if self.secure:cookie+='; Secure'
                    self.respond(200,{'paired':True,'csrfToken':state.csrf(token),'activeJob':latest_job_info()},headers={'Set-Cookie':cookie});return
                workspace=getattr(controller,'workspace_api',None)
                document=workspace(relative,data,namespace='phone') if callable(workspace) else None
                if document:self.respond(*document);return
                document=language_service.dispatch(directory,relative,data)
                if document:self.respond(*document);return
                document=card_api.dispatch(directory,relative,data,namespace='phone',browser_channel=getattr(controller,'browser_channel','chrome'))
                if document:self.respond(*document);return
                if path=='/api/phone/logout':
                    state.revoke(self.session);self.respond(200,{'ok':True},headers={'Set-Cookie':f'{COOKIE}=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0'+('; Secure' if self.secure else '')});return
                if path=='/api/phone/import':
                    # Phone requests a source; browser selection, dictionary and arbitrary
                    # override data remain controlled by the Mac, not an untrusted payload.
                    params={**load_profile(directory),'url':data.get('url',''),'loader':data.get('loader','chrome'),'timeout':data.get('timeout',180)}
                    self.respond(202,{'job_id':controller.start_import(params)});return
                match=re.fullmatch(r'/api/phone/cards/([a-f0-9]{32})/prepare',path)
                if match:self.respond(202,{'job_id':controller.prepare_card(match[1])});return
                self.respond(404,{'error':'Не найдено'})
            except PairingThrottled as error:self.respond(429,{'error':str(error),'retryAfter':error.retry_after},headers={'Retry-After':str(error.retry_after)})
            except (ValueError,TypeError,OSError) as error:self.respond(400,{'error':str(error)})
    server=ThreadingHTTPServer((bind,port),Handler);server.daemon_threads=True;server.pairing=state;server.allowed_hosts=allowed
    server.external_origins=set();server.origins_lock=threading.RLock()
    return server


class RelayManager:
    def __init__(self,directory,controller):
        self.directory=directory;self.controller=controller;self.server=None;self.thread=None;self.lock=threading.RLock();self.pair=None
        from remote_access import RemoteAccess
        self.remote_preference=Path(directory)/'phone-remote.json'
        from external_origin import normalize_external_origin
        try:
            preference=json.loads(self.remote_preference.read_text())
            self.remote_enabled=preference.get('enabled') is True
            self.remote_provider='custom' if preference.get('provider')=='custom' else 'cloudflare'
            self.custom_origin=normalize_external_origin(preference.get('url')) if self.remote_provider=='custom' else None
        except (OSError,ValueError,AttributeError):
            self.remote_enabled=False;self.remote_provider='cloudflare';self.custom_origin=None
        self.remote=RemoteAccess(directory,on_url=self._allow_remote,on_stop=self._remove_remote,forbidden_ports=(8765,getattr(controller,'server_port',8765)))

    def _allow_remote(self,url):
        from remote_access import quick_tunnel_url
        if quick_tunnel_url(url)!=url:raise ValueError('Неверный внешний адрес')
        with self.lock:
            if self.server and self.remote_enabled and self.remote_provider=='cloudflare':
                with self.server.origins_lock:self.server.external_origins.add(url)

    def _remove_remote(self,url):
        with self.lock:
            # A late cloudflared callback must not revoke a new manual mapping
            # if an operator explicitly configured that same public hostname.
            if self.remote_provider=='custom' and self.remote_enabled and url==self.custom_origin:return
            if self.server:
                with self.server.origins_lock:self.server.external_origins.discard(url)

    def _save_remote_preference(self,enabled,provider=None,url=None):
        if self.remote_preference.is_symlink():raise ValueError('Неверный путь настройки внешнего доступа')
        self.remote_preference.parent.mkdir(parents=True,exist_ok=True)
        provider=provider or self.remote_provider
        custom=url if provider=='custom' and url is not None else self.custom_origin if provider=='custom' else None
        value={'enabled':enabled,'provider':provider}
        if custom:value['url']=custom
        with tempfile.NamedTemporaryFile(mode='w',dir=self.remote_preference.parent,encoding='utf-8',delete=False) as output:
            json.dump(value,output);temporary=Path(output.name)
        temporary.chmod(0o600);temporary.replace(self.remote_preference)
        self.remote_enabled=enabled;self.remote_provider=provider;self.custom_origin=custom

    def remote_status(self):
        if self.remote_provider=='custom':
            active=self.remote_enabled and self.server is not None
            return {'provider':'custom','state':'configured' if active else 'stopped',
                    'url':self.custom_origin if active else None,'configuredUrl':self.custom_origin,
                    'error':'','pid':None,'available':True,'temporary':False,
                    'managedExternally':True,'enabled':self.remote_enabled}
        return {**self.remote.status(),'provider':'cloudflare','enabled':self.remote_enabled}

    def _activate_custom(self):
        if self.server and self.remote_enabled and self.custom_origin:
            with self.server.origins_lock:self.server.external_origins.add(self.custom_origin)

    def set_external_origin(self,url):
        from external_origin import normalize_external_origin
        origin=normalize_external_origin(url,resolve=True)
        with self.lock:
            old_origin=self.custom_origin
            self._save_remote_preference(True,'custom',origin)
            self.remote.stop()
            if old_origin and old_origin!=origin:self._remove_remote(old_origin)
            if not self.server:self.enable()
            self._activate_custom()
            return self.status()

    def clear_external_origin(self):
        with self.lock:
            old_origin=self.custom_origin
            self._save_remote_preference(False,'cloudflare')
            if old_origin:self._remove_remote(old_origin)
            self.remote.stop()
            return self.status()

    def enable_remote(self):
        with self.lock:
            old_origin=self.custom_origin
            self._save_remote_preference(True,'cloudflare')
            if old_origin:self._remove_remote(old_origin)
            if not self.server:self.enable()
            self.remote.start(self.server.server_port)
            return self.status()

    def disable_remote(self):
        with self.lock:
            old_origin=self.custom_origin
            self._save_remote_preference(False);self.remote.stop()
            if old_origin:self._remove_remote(old_origin)
            return self.status()
    def status(self):
        with self.lock:
            result={'enabled':self.server is not None,'port':self.server.server_port if self.server else None,'urls':self.urls() if self.server else [],'pairExpires':None,'pairCode':None,'pairUrls':[],'remote':self.remote_status()}
            if self.server:
                code,manual,expires=self.server.pairing.current();self.pair=(code,expires)
                result.update(pairCode=manual,pairExpires=expires,pairUrls=[url+'pair#'+code for url in result['urls']],serverTime=time.time())
            return result
    def urls(self):
        names=self.server.allowed_hosts
        ordered=sorted([name for name in names if name not in ('127.0.0.1','localhost')],key=lambda name:(not name.endswith('.local'),name))
        remote=self.remote_status()
        return ([remote['url']+'/phone/'] if remote['state'] in ('ready','configured') and remote['url'] else [])+['http://'+host+':'+str(self.server.server_port)+'/phone/' for host in ordered]
    def enable(self,port=8767):
        with self.lock:
            if self.server is None:
                self.server=create_phone_server(self.directory,self.controller,port)
                self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
                if self.remote_enabled:
                    if self.remote_provider=='custom':self._activate_custom()
                    else:self.remote.start(self.server.server_port)
            return self.issue_pair()
    def issue_pair(self):
        with self.lock:
            if self.server is None:raise ValueError('Сначала включите телефонный доступ')
            self.server.pairing.issue()
            return self.status()
    def stop(self,revoke=False):
        with self.lock:
            if revoke:self._save_remote_preference(False)
            self.remote.stop()
            if self.server:
                if revoke:self.server.pairing.revoke()
                self.server.shutdown();self.server.server_close();self.server=None
                if self.thread:self.thread.join(timeout=3)
                self.thread=None;self.pair=None
            return self.status()

    def disable(self):return self.stop(revoke=True)

    def save_profile(self,profile):return save_profile(self.directory,profile)
