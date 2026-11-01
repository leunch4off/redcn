"""Loopback editor plus an opt-in, paired LAN companion for the Mac."""
import json
import base64
from datetime import datetime
import errno
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie, CookieError
from pathlib import Path
import re
import secrets
import signal
import sqlite3
import threading
import time
from urllib.parse import urlsplit, parse_qs
import urllib.request
import webbrowser

import redcn
import photo_store
import card_api
import language_service
import learning_service
import http_delivery
from workspace_ownership import WorkspaceOwner, DesktopOwnerAccess, OwnershipError
from fact_ledger import validate_result_fields, record_fact_revision
from source_readiness import source_readiness


def store_history(directory, entry):
    if not isinstance(entry,dict) or not re.fullmatch(r'[a-zA-Z0-9-]{8,80}',str(entry.get('id',''))): raise ValueError('Неверный ID истории')
    if entry.get('kind') not in ('generate','save','copy-title','copy-body','copy-all','restore','import'): raise ValueError('Неверное событие')
    if not all(isinstance(entry.get(k),str) for k in ('title','body','tags','at')) or not isinstance(entry.get('fields'),dict): raise ValueError('Неверный снимок истории')
    try: stamp=datetime.fromisoformat(entry['at'].replace('Z','+00:00')).timestamp()
    except (ValueError,OverflowError) as error: raise ValueError('Неверная дата истории') from error
    item=dict(entry);image=item.pop('png',None);png=None
    if image is not None:
        if not isinstance(image,str) or not image.startswith('data:image/png;base64,'): raise ValueError('Нужен PNG')
        try: png=base64.b64decode(image.split(',',1)[1],validate=True)
        except ValueError as error: raise ValueError('Неверный PNG') from error
        if len(png)>3_000_000 or not png.startswith(b'\x89PNG\r\n\x1a\n'): raise ValueError('Неверный PNG или файл больше 3 МБ')
    connection=redcn.open_cache(directory)
    try:
        connection.execute('CREATE TABLE IF NOT EXISTS history (id TEXT PRIMARY KEY, stamp REAL, payload TEXT, png BLOB)')
        connection.execute('INSERT OR REPLACE INTO history VALUES (?,?,?,?)',(item['id'],stamp,json.dumps(item,ensure_ascii=False),png))
        connection.execute('DELETE FROM history WHERE id NOT IN (SELECT id FROM history ORDER BY stamp DESC LIMIT 200)')
        connection.commit()
    finally: connection.close()
    return item['id']


def history_rows(directory, entry_id=None, image=False):
    connection=redcn.open_cache(directory)
    try:
        connection.execute('CREATE TABLE IF NOT EXISTS history (id TEXT PRIMARY KEY, stamp REAL, payload TEXT, png BLOB)')
        if entry_id:
            row=connection.execute('SELECT payload,png FROM history WHERE id=?',(entry_id,)).fetchone()
            if not row:return None
            return row[1] if image else json.loads(row[0])
        result=[]
        for payload,has_image in connection.execute('SELECT payload,png IS NOT NULL FROM history ORDER BY stamp DESC LIMIT 200'):
            e=json.loads(payload);result.append({**{k:e[k] for k in ('id','kind','at','title') if k in e},'hasSnapshot':bool(has_image),'url':e.get('fields',{}).get('url','')})
        return result
    finally: connection.close()


def save_result(directory, result, result_id=None, *, actor='operator', kind='save'):
    validate_result_fields(result)
    result_id = result_id or secrets.token_hex(16)
    if not re.fullmatch(r'[a-f0-9]{32}', result_id): raise ValueError('Неверный ID карточки')
    connection = redcn.open_cache(directory)
    try:
        connection.execute('CREATE TABLE IF NOT EXISTS results (id TEXT PRIMARY KEY, updated REAL, payload TEXT)')
        connection.execute('INSERT OR REPLACE INTO results VALUES (?,max(?,(SELECT COALESCE(MAX(updated),0)+0.000001 FROM results)),?)', (result_id, time.time(), json.dumps(result, ensure_ascii=False)))
        record_fact_revision(connection, result_id, result.get('fields', {}), actor=actor, kind=kind)
        connection.commit()
    finally: connection.close()
    return result_id


def read_result(directory, result_id):
    connection = redcn.open_cache(directory)
    try:
        connection.execute('CREATE TABLE IF NOT EXISTS results (id TEXT PRIMARY KEY, updated REAL, payload TEXT)')
        row = connection.execute('SELECT payload FROM results WHERE id=?', (result_id,)).fetchone()
        return json.loads(row[0]) if row else None
    finally: connection.close()


def merge_prepared_media(directory,result_id,prepared,*,photo_baseline=None,photo_generated=None):
    from phone_relay import source_images
    # Lock the read/merge/write so the completed download cannot save an old text snapshot.
    connection=redcn.open_cache(directory)
    try:
        connection.execute('BEGIN IMMEDIATE')
        row=connection.execute('SELECT payload FROM results WHERE id=?',(result_id,)).fetchone()
        current=json.loads(row[0]) if row else None
        if not current or source_images(current)!=source_images(prepared):raise ValueError('Фотографии карточки изменились. Повторите загрузку.')
        current.update(media=prepared.get('media',[]),mediaStatus=prepared.get('mediaStatus','ready'))
        if isinstance(prepared.get('photoProcessing'),dict):current['photoProcessing']=prepared['photoProcessing']
        if isinstance(prepared.get('photoInventory'),dict):
            current['photoInventory']=prepared['photoInventory']
        if isinstance(photo_generated,dict) and isinstance(photo_baseline,dict):
            # Do not replace a manual edit made while the model was running.
            unchanged=all(current.get(key)==photo_baseline.get(key) for key in ('fields','body','titles','tags'))
            if unchanged:
                for key in ('fields','body','titles','tags','tagSuggestions','templateInfo','translationWarnings','unknown','problems','warnings','aiAdvertisement'):
                    if key in photo_generated:current[key]=photo_generated[key]
            if 'photoInventory' in current:
                current['photoInventory']['textUpdated']=unchanged
                if not unchanged:
                    current['photoInventory']['message']='Карточка изменена во время анализа. Ваш текст сохранён; находки доступны для следующей генерации.'
        connection.execute('UPDATE results SET updated=max(?,(SELECT COALESCE(MAX(updated),0)+0.000001 FROM results)),payload=? WHERE id=?',(time.time(),json.dumps(current,ensure_ascii=False),result_id))
        connection.commit();return current
    except BaseException:
        connection.rollback();raise
    finally:connection.close()


def create_server(directory, port=8765, browser_channel='chrome'):
    if browser_channel not in ('chrome','chromium'): raise ValueError('Неверный браузер')
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    owner_scope = WorkspaceOwner(directory)
    desktop_access = DesktopOwnerAccess(owner_scope)
    token, jobs, lock = desktop_access.token, {}, threading.Lock()
    audit_tokens = {}
    from job_progress import DurationHistory, JobProgress
    timing_history=DurationHistory(directory)
    identity = {'app':'redcn', 'protocol':7, 'release':'v27', 'storage':hashlib.sha256(str(directory.expanduser().resolve()).encode()).hexdigest(), 'browser_channel':browser_channel}

    def phone_status():
        return {**server.phone_relay.status(), 'diagnostics': server.connection_diagnostics.status()}

    def photo_outcome(result):
        processing=result.get('photoProcessing') if isinstance(result.get('photoProcessing'),dict) else {}
        status=result.get('mediaStatus','pending')
        download_failed=processing.get('downloadFailed',0)
        failed=processing.get('failed',0)
        skipped=processing.get('deferred',processing.get('skipped',0))
        warnings=[]
        inventory=result.get('photoInventory') or {}
        if inventory.get('state')=='partial':
            warnings.append('Фото обработаны; подсказки обученной модели требуют подтверждения перед добавлением в описание.'
                if inventory.get('reviewRequired') and inventory.get('coverageComplete')
                else 'Мебель и техника проверены не на всех фото. Неопознанные предметы остаются неизвестными.')
        if status!='ready' or download_failed:
            warnings.append('Часть фотографий не загрузилась на Mac'+(': '+str(download_failed) if download_failed else '')+'.')
        if failed:warnings.append('Не удалось автоматически обработать фото: '+str(failed)+'.')
        if skipped:warnings.append('Не все фото успели пройти автоматическую проверку: '+str(skipped)+'.')
        learn=status=='ready' and not any((download_failed,failed,skipped)) and inventory.get('state')!='partial'
        return learn,{'mediaStatus':status,'photoProcessing':processing,'partial':not learn,'warnings':warnings}

    def run_import(job_id, params, cancel, actor):
        attempt_started, attempt_clock = time.time(), time.monotonic()
        audit_ai_signature = None
        def report(message, **fields):
            nonlocal audit_ai_signature
            with lock:
                jobs[job_id].update(message=message)
                jobs[job_id]['tracker'].update(message,**fields)
            # Token samples stay in bounded live state. Repeated samples must
            # not exhaust the separate diagnostic campaign's event budget.
            ai = fields.get('ai')
            if isinstance(ai, dict):
                signature = (fields.get('stage'), ai.get('backend'), ai.get('phase'), ai.get('photoIndex'))
                if signature == audit_ai_signature:
                    return
                audit_ai_signature = signature
            try:
                owner_scope.check()
                workspace.diagnostics.record(audit_tokens.get(job_id), message, **fields)
            except (OSError, ValueError, sqlite3.Error):pass
        connection = None
        listing = None
        failure = None
        result_id=None
        success=False
        learn=False
        try:
            owner_scope.check()
            report('Проверяем общий генератор Node.js до запуска Chrome…',stage='preflight')
            from node_runtime import resolve_node
            resolve_node()
            report('Генератор доступен. Проверяем библиотеку обработки фотографий на Mac…',stage='preflight')
            photo_store.pillow()
            if cancel.is_set():raise ValueError('Импорт отменён')
            connection = redcn.open_cache(directory)
            listing = redcn.load_listing(params['url'], connection, params.get('refresh', False), params.get('loader','auto'), params.get('timeout',180), report, cancel, params.get('browser_channel',browser_channel))
            listing['sourceReadiness'] = source_readiness(listing)
            with lock:
                readiness=listing['sourceReadiness']
                jobs[job_id]['tracker'].mark_stage_quality('scroll',
                    'complete' if readiness['completed'] else readiness['state'],
                    [{'section':row['name'],'reason':row['reason']} for row in readiness['sections']])
            with lock:
                from photo_ai_settings import load_config
                jobs[job_id]['tracker'].set_profile('import:vision' if load_config(directory)['enabled'] else 'import:cache' if listing.get('cached') else
                    'import:chrome' if listing.get('loader')=='chrome' else 'import:'+params.get('loader','auto'))
            if cancel.is_set(): raise ValueError('Импорт отменён')
            report('Объявление прочитано. Формируем китайский заголовок и описание…',stage='generate')
            data = params.get('data', {})
            fields = {**data, **listing.get('fields',{}), 'titleVariant':redcn.next_variant(connection,listing['url']), 'url':listing['url'], 'source':listing['text']}
            result = {**redcn.generate(listing['text'],fields,params.get('rules'),params.get('prefs')), 'listing':listing}
            photo_baseline=json.loads(json.dumps(result,ensure_ascii=False))
            if cancel.is_set(): raise ValueError('Импорт отменён')
            from phone_relay import cache_photos
            result['mediaStatus']='loading'
            owner_scope.check()
            result_id = save_result(directory, result, actor=actor, kind='import')
            cache_photos(directory,result,report,cancel)
            from photo_generation import photo_revision
            photo_generated=photo_revision(directory,result,text=listing['text'],rules=params.get('rules'),prefs=params.get('prefs'),report=report,cancel=cancel)
            from local_text_model import settings as text_model_settings
            try:
                advertising_enabled=text_model_settings(directory).get('advertisingEnabled') is True
            except (OSError,ValueError,TypeError):
                advertising_enabled=False
                report('Настройки текстового ИИ недоступны; сохраняем описание по проверенным фактам.',
                    stage='generate',ai={'backend':'mlx','operation':'text','phase':'unavailable'})
            if advertising_enabled:
                try:
                    from local_content import generate_import_advertisement
                    photo_generated=generate_import_advertisement(directory,
                        {**result, **(photo_generated or {})}, status=report, cancel=cancel)
                except (OSError,ValueError,TypeError):
                    report('ИИ не завершил рекламное описание; сохраняем описание по проверенным фактам.',
                        stage='generate',ai={'backend':'mlx','operation':'text','phase':'unavailable'})
            if cancel.is_set():raise ValueError('Импорт отменён')
            report('Сохраняем карточку и подготовленные фотографии…',stage='save')
            owner_scope.check()
            merge_prepared_media(directory,result_id,result,photo_baseline=photo_baseline,photo_generated=photo_generated)
            learn,outcome=photo_outcome(result)
            readiness=listing['sourceReadiness']
            outcome.update(photoPartial=outcome['partial'],sourcePartial=readiness['partial'],sourceReadiness=readiness)
            outcome['partial']=outcome['partial'] or readiness['partial']
            outcome['warnings']=[*readiness['warnings'],*outcome['warnings']]
            learn=learn and not readiness['partial']
            success=True
            message=('Карточка сохранена. Полнота разделов источника не подтверждена.' if readiness['partial'] else
                     'Готово' if learn else 'Текст готов, подготовка фотографий завершена не полностью.')
            with lock: jobs[job_id].update(state='done',message=message,result_id=result_id,**outcome)
        except Exception as error:
            failure = error
            if result_id:
                try:
                    owner_scope.check()
                    current=read_result(directory,result_id)
                    if current and current.get('mediaStatus')=='loading':
                        current['mediaStatus']='cancelled' if cancel.is_set() else 'error'
                        save_result(directory,current,result_id,actor=actor,kind='import')
                except (OSError,ValueError,sqlite3.Error):pass
            with lock: jobs[job_id].update(state='error', message=str(error).strip() or 'Обработка прервана: '+type(error).__name__)
        finally:
            with lock:
                jobs[job_id]['tracker'].finish(success,learn=learn)
                measured = jobs[job_id]['tracker'].snapshot()
                audit_token = audit_tokens.pop(job_id, None)
            try:
                if audit_token:
                    owner_scope.check()
                    workspace.diagnostics.complete(audit_token,
                        'success' if success else 'cancelled' if cancel.is_set() else 'error',
                        progress=measured, capture=(listing or {}).get('capture'), error=failure)
            except (OSError, ValueError, sqlite3.Error):pass
            try:
                owner_scope.check()
                from activity_log import record_import_attempt
                stages = {row['id']:row.get('elapsedSeconds', 0) for row in measured.get('stages', []) if isinstance(row,dict) and isinstance(row.get('id'),str)}
                outcome = 'cancelled' if cancel.is_set() else 'partial' if success and not learn else 'done' if success else 'needs_attention' if failure and type(failure).__name__ == 'NeedsAttention' else 'error'
                record_import_attempt(directory,source_url=params['url'],started=attempt_started,elapsed=min(7200,time.monotonic()-attempt_clock),outcome=outcome,stages=stages,error=failure)
            except (OSError, ValueError, sqlite3.Error):pass
            if connection is not None:connection.close()

    def start_import(params, *, actor='Mac owner'):
        owner_scope.check()
        from learning_scheduler import touch_foreground
        touch_foreground(directory)
        if not isinstance(actor,str) or not actor.strip() or len(actor)>100:
            raise ValueError('У импорта должен быть проверенный автор до 100 знаков')
        params=dict(params)
        params['url']=redcn.normalize_url(params.get('url',''))
        if params.get('loader','auto') not in ('auto','http','chrome'):raise ValueError('Неверный режим загрузки')
        if params.get('browser_channel',browser_channel) not in ('chrome','chromium'):raise ValueError('Неверный браузер')
        if type(params.get('timeout',180)) is not int or not 15<=params.get('timeout',180)<=600:raise ValueError('Ожидание: 15–600 секунд')
        for field in ('rules','prefs','data'):
            if field in params and not isinstance(params[field],dict):raise ValueError('Неверный формат '+field)
        from photo_ai_settings import load_config
        profile_key='import:vision' if load_config(directory)['enabled'] else 'import:'+params.get('loader','auto')
        if not all(isinstance(k,str) and isinstance(v,str) for k,v in params.get('rules',{}).items()):raise ValueError('Словарь: строки RU → CN')
        with lock:
            if any(job['state']=='running' for job in jobs.values()):raise ValueError('Дождитесь текущего импорта или отмените его')
            while len(jobs)>=32:jobs.pop(next(iter(jobs)))
            job_id,cancel=secrets.token_hex(16),threading.Event()
            jobs[job_id]={'state':'running','message':'Начинаем загрузку…','cancel':cancel,
                         'kind':'import','sourceUrl':params['url'],
                         'tracker':JobProgress(timing_history,profile_key)}
            try:audit_tokens[job_id] = workspace.diagnostics.begin(job_id, params['url'], manual=True)
            except (OSError, ValueError, sqlite3.Error):pass
        threading.Thread(target=run_import,args=(job_id,params,cancel,actor),daemon=True).start()
        return job_id

    def job_status(job_id):
        with lock:
            job=jobs.get(job_id)
            if not job:return {}
            return {**{k:v for k,v in job.items() if k not in ('cancel','tracker')},
                    'progress':job['tracker'].snapshot()}

    def latest_job():
        with lock:
            if not jobs:return None
            selected=next((job_id for job_id,job in reversed(list(jobs.items())) if job['state']=='running'),next(reversed(jobs)))
        return {'id':selected,**job_status(selected)}

    def prepare_card(result_id,*,analyze=False):
        owner_scope.check()
        from learning_scheduler import touch_foreground
        touch_foreground(directory)
        from phone_relay import cache_photos
        result=read_result(directory,result_id)
        if not result or result.get('fields',{}).get('isDemo') is True:raise ValueError('Карточка не найдена')
        if analyze:
            from photo_ai_settings import load_config
            if not load_config(directory)['enabled']:
                raise ValueError('Автоматический анализ фото пока не включён на Mac. Текст карточки сохранён; мебель можно уточнить вручную в редакторе.')
        with lock:
            if any(job['state']=='running' for job in jobs.values()):raise ValueError('Дождитесь текущей загрузки')
            while len(jobs)>=32:jobs.pop(next(iter(jobs)))
            job_id,cancel=secrets.token_hex(16),threading.Event()
            jobs[job_id]={'state':'running','message':'Сохраняем фотографии на Mac…','cancel':cancel,
                         'kind':'prepare','cardId':result_id,
                         'tracker':JobProgress(timing_history,'prepare:vision' if analyze else 'prepare')}
        def run():
            success=False
            learn=False
            def report(message, **fields):
                with lock:
                    jobs[job_id].update(message=message)
                    jobs[job_id]['tracker'].update(message,**fields)
            try:
                report('Проверяем библиотеку обработки фотографий на Mac…',stage='photos')
                photo_store.pillow()
                cache_photos(directory,result,report=report,cancel=cancel)
                baseline=json.loads(json.dumps(result,ensure_ascii=False))
                generated=None
                if analyze:
                    from photo_generation import photo_revision
                    from phone_relay import load_profile
                    profile=load_profile(directory)
                    generated=photo_revision(directory,result,rules=profile.get('rules'),prefs=profile.get('prefs'),report=report,cancel=cancel)
                if cancel.is_set():raise ValueError('Загрузка отменена')
                report('Сохраняем подготовленные фотографии…',stage='save')
                owner_scope.check()
                merge_prepared_media(directory,result_id,result,photo_baseline=baseline,photo_generated=generated)
                learn,outcome=photo_outcome(result)
                success=True
                with lock:jobs[job_id].update(state='done',message='Фотографии готовы' if learn else 'Подготовка фотографий завершена не полностью.',result_id=result_id,**outcome)
            except Exception as error:
                with lock:jobs[job_id].update(state='error',message=str(error).strip() or 'Подготовка фото прервана: '+type(error).__name__)
            finally:
                with lock:jobs[job_id]['tracker'].finish(success,learn=learn)
        threading.Thread(target=run,daemon=True).start();return job_id

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args): pass

        def respond(self, status, value, content_type='application/json; charset=utf-8', headers=None):
            content = json.dumps(value, ensure_ascii=False).encode() if content_type.startswith('application/json') else value
            content,compression=http_delivery.response_bytes(content,content_type,self.headers.get('Accept-Encoding',''))
            self.send_response(status)
            self.send_header('Content-Type',content_type)
            self.send_header('Content-Length',str(len(content)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Referrer-Policy','no-referrer')
            self.send_header('X-Frame-Options','DENY')
            for key,val in compression.items():self.send_header(key,val)
            for key,val in (headers or {}).items():self.send_header(key,val)
            self.end_headers()
            try:self.wfile.write(content)
            except (BrokenPipeError,ConnectionResetError):pass

        def permitted(self, api=False):
            host = f'127.0.0.1:{self.server.server_port}'
            if self.headers.get('Host') != host:
                self.respond(403,{'error':'Откройте сайт через http://'+host});return False
            origin = self.headers.get('Origin')
            if origin and origin != 'http://'+host:
                self.respond(403,{'error':'Неверный источник запроса'});return False
            try:
                owner_scope.check()
            except (OwnershipError, OSError):
                self.respond(403,{'error':'Недоступно рабочее пространство владельца','code':'WORKSPACE_OWNER_MISMATCH'});return False
            if api and not desktop_access.authorized(self.headers.get('X-Redcn-Token','')):
                self.respond(403,{'error':'Обновите страницу локального сайта'});return False
            return True

        def desktop_owner(self):
            try:
                cookies=SimpleCookie();cookies.load(self.headers.get('Cookie',''))
                item=cookies.get(desktop_access.cookie_name(self.server.server_port))
                return bool(item and desktop_access.authorized_cookie(item.value,self.server.server_port))
            except (ValueError, TypeError, CookieError):
                return False

        def do_GET(self):
            if not self.permitted(): return
            path = urlsplit(self.path).path
            if path == '/api/local/health':
                # Read-only aggregate for safe release checks; no job identifiers,
                # source URLs, pairing material or card contents leave this route.
                with lock: active_jobs=sum(job.get('state')=='running' for job in jobs.values())
                self.respond(200,{**identity,'activeJobs':active_jobs});return
            if path == '/api/local/owner-proof':
                query=parse_qs(urlsplit(self.path).query)
                try:
                    if set(query)!={'challenge'} or len(query['challenge'])!=1:raise ValueError()
                    proof=desktop_access.proof(self.server.server_port,query['challenge'][0])
                except (ValueError,TypeError):self.respond(400,{'error':'Неверный запрос подтверждения владельца'});return
                self.respond(200,{'proof':proof});return
            if path == desktop_access.session_path(self.server.server_port):
                if not self.desktop_owner():self.respond(403,{'error':'Откройте редактор от владельца'});return
                self.respond(200,{'token':token});return
            if path.startswith('/api/local/'):
                if not self.permitted(api=True): return
                if path == '/api/local/phone-sessions':
                    from access_roles import dispatch_sessions
                    relay = self.server.phone_relay.server
                    if relay is None:self.respond(409,{'error':'Сначала включите телефонный доступ на Mac'});return
                    self.respond(*dispatch_sessions(relay.pairing));return
                try:
                    document=workspace.dispatch(path.removeprefix('/api/local/'), query=parse_qs(urlsplit(self.path).query))
                    if document:self.respond(*document);return
                    document=language_service.dispatch(directory,path.removeprefix('/api/local/'))
                    if document:self.respond(*document);return
                    document=learning_service.dispatch(directory,path.removeprefix('/api/local/'))
                    if document:self.respond(*document);return
                    document=card_api.dispatch(directory,path.removeprefix('/api/local/'),browser_channel=browser_channel)
                    if document:self.respond(*document);return
                except (ValueError,TypeError,OSError) as error:
                    self.respond(400,{'error':str(error)});return
                if path == '/api/local/phone':
                    self.respond(200,phone_status());return
                if path == '/api/local/history':
                    self.respond(200,{'entries':history_rows(directory)});return
                if path == '/api/local/cards':
                    from phone_relay import list_cards
                    after=parse_qs(urlsplit(self.path).query).get('after',['0'])[0]
                    self.respond(200,list_cards(directory,after));return
                if path.startswith('/api/local/media/'):
                    try:
                        content,mime,_=photo_store.asset(directory,path.rsplit('/',1)[-1])
                        self.respond(200,content,mime)
                    except (OSError,ValueError) as error:self.respond(404,{'error':str(error)})
                    return
                if path.startswith('/api/local/history/'):
                    entry=history_rows(directory,path.rsplit('/',1)[-1])
                    self.respond(200 if entry else 404,{'entry':entry} if entry else {'error':'Запись истории не найдена'});return
                if path.startswith('/api/local/history-image/'):
                    png=history_rows(directory,path.rsplit('/',1)[-1],image=True)
                    if png:self.respond(200,png,'image/png')
                    else:self.respond(404,{'error':'PNG не найден'})
                    return
                if path.startswith('/api/local/jobs/'):
                    job_id = path.rsplit('/',1)[-1]
                    job = job_status(job_id)
                    self.respond(200 if job else 404,job or {'error':'Задача не найдена'});return
                if path.startswith('/api/local/results/'):
                    result = read_result(directory,path.rsplit('/',1)[-1])
                    self.respond(200 if result else 404,result or {'error':'Карточка не найдена'});return
                self.respond(404,{'error':'Не найдено'});return
            if path == '/' or re.fullmatch(r'/listing/[a-f0-9]{32}',path):
                page_path = redcn.ROOT / 'docs/index.html'
                if not page_path.exists():
                    self.respond(503,{'error':'Сначала выполните npm run build'});return
                page = page_path.read_text(encoding='utf-8').replace('name="redcn-hosting" content="github-pages"','name="redcn-hosting" content="local"')
                visible_token=''
                page = page.replace('<meta charset="utf-8">','<meta charset="utf-8"><base href="/"><meta name="redcn-browser-channel" content="'+browser_channel+'"><meta name="redcn-workspace" content="'+owner_scope.owner_id+'"><meta name="redcn-token" content="'+visible_token+'">',1)
                page = page.replace('GitHub Pages · данные локально','Локально · Chrome подключён')
                self.respond(200,page.encode(),'text/html; charset=utf-8');return
            files = {'/plan.json':redcn.ROOT/'plan.json', '/research.json':redcn.ROOT/'data/research.json', '/knowledge-seed.json':redcn.ROOT/'data/knowledge-seed.json'}
            if path in files:
                self.respond(200,json.loads(files[path].read_text(encoding='utf-8')));return
            self.respond(404,{'error':'Не найдено'})

        def do_POST(self):
            if urlsplit(self.path).path == '/api/local/desktop-access':
                if not self.permitted():return
                host='http://127.0.0.1:'+str(self.server.server_port)
                if self.headers.get('Origin')!=host:
                    self.respond(403,{'error':'Откройте локальный редактор от владельца'});return
                try:
                    size=int(self.headers.get('Content-Length','0'))
                    if self.headers.get_content_type()!='application/json' or not 1<=size<=256:raise ValueError()
                    data=json.loads(self.rfile.read(size))
                    if not isinstance(data,dict) or set(data)!={'token'} or not desktop_access.authorized(data['token']):raise ValueError()
                except (ValueError, TypeError):
                    self.respond(403,{'error':'Откройте локальный редактор от владельца'});return
                self.respond(200,{'ok':True,'sessionPath':desktop_access.session_path(self.server.server_port)},headers={'Set-Cookie':desktop_access.cookie(self.server.server_port)});return
            if not self.permitted(api=True): return
            try:
                if self.headers.get_content_type() != 'application/json': raise ValueError('Нужен JSON')
                size = int(self.headers.get('Content-Length','0'))
                relative=urlsplit(self.path).path.removeprefix('/api/local/')
                limit=workspace.request_limit(relative) or card_api.request_limit(relative) or (16_100_000 if relative=='photo' else 8_100_000 if relative=='table-export' else 4_500_000 if relative=='history' else 1_000_000)
                if not 1 <= size <= limit: raise ValueError('Превышен размер запроса')
                data = json.loads(self.rfile.read(size))
                if not isinstance(data,dict): raise ValueError('Нужен JSON-объект')
                path = urlsplit(self.path).path
                if relative == 'phone-sessions':
                    from access_roles import dispatch_sessions
                    relay = self.server.phone_relay.server
                    if relay is None:self.respond(409,{'error':'Сначала включите телефонный доступ на Mac'});return
                    self.respond(*dispatch_sessions(relay.pairing,data));return
                if relative == 'import-cache':
                    with lock:
                        if any(job['state']=='running' for job in jobs.values()):
                            self.respond(409,{'error':'Дождитесь завершения текущего импорта перед очисткой кеша'});return
                        document=workspace.dispatch(relative,data)
                    if document:self.respond(*document);return
                document=workspace.dispatch(relative,data)
                if document:self.respond(*document);return
                document=language_service.dispatch(directory,relative,data)
                if document:self.respond(*document);return
                document=learning_service.dispatch(directory,relative,data)
                if document:self.respond(*document);return
                document=card_api.dispatch(directory,relative,data,browser_channel=browser_channel)
                if document:self.respond(*document);return
                if path == '/api/local/photo':
                    if data.get('action','load')=='load':
                        result=photo_store.load_photo(directory,data.get('url',''),data.get('refresh') is True,**({'source':data['source']} if data.get('source') is not None else {}))
                    elif data.get('action')=='crop':
                        result=photo_store.crop_photo(directory,data.get('id'),data.get('margins'))
                    elif data.get('action')=='analyze':
                        result=photo_store.analyze_photo(directory,data.get('id'))
                    elif data.get('action')=='attach':
                        result=photo_store.attach_photo(directory,data.get('url',''),data.get('data'))
                    elif data.get('action')=='accepted':
                        result=photo_store.accepted_photo_for_url(directory,data.get('url',''),source=data.get('source'),allow_unscoped_variants=data.get('allowUnscopedVariants') is True)
                    else: raise ValueError('Неверное действие с фотографией')
                    self.respond(200,result);return
                if path == '/api/local/history':
                    entry_id=store_history(directory,data.get('entry'))
                    self.respond(200,{'id':entry_id});return
                if path == '/api/local/phone':
                    action=data.get('action')
                    if action=='enable':
                        if any(key in data for key in ('rules','prefs','data')):self.server.phone_relay.save_profile(data)
                        port=data.get('port',8767)
                        if type(port) is not int or not 1<=port<=65535:raise ValueError('Порт телефона: 1–65535')
                        result=self.server.phone_relay.enable(port)
                    elif action=='profile':result=self.server.phone_relay.save_profile(data)
                    elif action=='pair':result=self.server.phone_relay.issue_pair()
                    elif action=='disable':result=self.server.phone_relay.disable()
                    elif action=='remote-start':result=self.server.phone_relay.enable_remote()
                    elif action=='remote-stop':result=self.server.phone_relay.disable_remote()
                    elif action=='external-set':result=self.server.phone_relay.set_external_origin(data.get('url'))
                    elif action=='external-clear':result=self.server.phone_relay.clear_external_origin()
                    elif action=='diagnose':
                        self.server.connection_diagnostics.start(self.server.phone_relay.status())
                        result=phone_status()
                    else:raise ValueError('Неверное действие телефонного доступа')
                    self.respond(200,result);return
                if path == '/api/local/import':
                    try:job_id=start_import(data)
                    except ValueError as error:
                        if 'Дождитесь' in str(error):self.respond(409,{'error':str(error)});return
                        raise
                    self.respond(202,{'job_id':job_id});return
                if path == '/api/local/cancel':
                    with lock:
                        job = jobs.get(data.get('job_id'))
                        if job: job['cancel'].set()
                    self.respond(200,{'ok':True});return
                if path == '/api/local/save':
                    result = data.get('result')
                    if not isinstance(result,dict) or not isinstance(result.get('fields'),dict) or not isinstance(result.get('titles'),list) or not isinstance(result.get('body'),str) or not isinstance(result.get('tags'),str): raise ValueError('Неверная карточка')
                    result_id = save_result(directory,result,data.get('result_id'))
                    self.respond(200,{'result_id':result_id});return
                self.respond(404,{'error':'Не найдено'})
            except (ValueError,TypeError,OSError,redcn.subprocess.SubprocessError) as error:
                self.respond(400,{'error':str(error)})

    server = ThreadingHTTPServer(('127.0.0.1',port),Handler)
    server.workspace_owner=owner_scope
    server.desktop_access=desktop_access
    from photo_ai_runtime import start_background
    start_background(directory)
    from learning_scheduler import start_background as start_learning_background
    def learning_busy():
        with lock:return any(job['state']=='running' for job in jobs.values())
    server.learning_scheduler=start_learning_background(directory,busy=learning_busy)
    server.daemon_threads = True
    server.start_import=start_import;server.job_status=job_status;server.prepare_card=prepare_card;server.latest_job=latest_job
    server.browser_channel=browser_channel
    from workspace_api import WorkspaceAPI
    workspace = WorkspaceAPI(directory, browser_channel=browser_channel)
    server.workspace_api=workspace.dispatch
    server.listing_watch=workspace.watch
    server.import_diagnostics=workspace.diagnostics
    from phone_relay import RelayManager
    server.release=identity['release']
    server.phone_relay=RelayManager(directory,server)
    from connection_diagnostics import ConnectionDiagnostics
    server.connection_diagnostics=ConnectionDiagnostics()
    close=server.server_close
    def close_all():
        server.learning_scheduler.stop()
        workspace.close();server.connection_diagnostics.close();server.phone_relay.stop();close()
    server.server_close=close_all
    return server


def matching_server(directory, port, browser_channel):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *_args, **_kwargs): return None
    expected = {'app':'redcn','protocol':7,'storage':hashlib.sha256(str(Path(directory).expanduser().resolve()).encode()).hexdigest(),'browser_channel':browser_channel}
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
        with opener.open(f'http://127.0.0.1:{port}/api/local/health',timeout=1) as response:
            observed=json.loads(response.read(4097))
            # Optional health metadata (for example the visible release) does
            # not change controller ownership. Starting another controller for
            # the same workspace could recover active jobs in the first one.
            return isinstance(observed,dict) and all(observed.get(key)==value for key,value in expected.items())
    except (OSError,ValueError): return False


def bind_or_reuse(directory, port, browser_channel='chrome'):
    if type(port) is not int or not 0 <= port <= 65535: raise ValueError('Порт должен быть от 0 до 65535')
    for candidate in ([0] if port==0 else range(port,min(port+20,65536))):
        try:
            server = create_server(directory,candidate,browser_channel)
            return server,server.server_port
        except OSError as error:
            if error.errno != errno.EADDRINUSE: raise
            if matching_server(directory,candidate,browser_channel): return None,candidate
    raise ValueError('Не найден свободный порт. Используйте --port 0 для автоматического выбора.')


def owner_launch_url(directory, port, result_id=None):
    """Owner CLI only: bootstrap from its private credential, never HTTP HTML."""
    scope=WorkspaceOwner(directory)
    access=DesktopOwnerAccess(scope,create=False)
    verify_existing_owner(access,port)
    return access.launch_url(port,result_id)


def verify_existing_owner(access,port):
    """A public health identity alone cannot authenticate a localhost listener."""
    if type(port) is not int or not 1<=port<=65535:raise ValueError('Неверный порт локального редактора')
    challenge=secrets.token_hex(16)
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*_args,**_kwargs):return None
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    with opener.open('http://127.0.0.1:'+str(port)+'/api/local/owner-proof?challenge='+challenge,timeout=3) as response:
        proof=json.loads(response.read(1025))
    if not isinstance(proof,dict) or not secrets.compare_digest(str(proof.get('proof','')),access.proof(port,challenge)):
        raise OwnershipError('Работающий сервер не подтвердил владельца рабочего пространства')


def open_existing(directory, port=8765, result_id=None):
    """Open a verified existing controller without starting another one."""
    if not any(matching_server(directory,port,channel) for channel in ('chrome','chromium')):
        raise ValueError('Локальный redcn этого владельца не запущен')
    if not webbrowser.open(owner_launch_url(directory,port,result_id)):
        raise ValueError('Не удалось открыть браузер владельца на Mac')
    return 0


def enable_existing_phone(port,phone_port,directory=None):
    base='http://127.0.0.1:'+str(port)
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    root=Path(directory) if directory is not None else Path.home()/'.cache'/'redcn'
    if not any(matching_server(root,port,channel) for channel in ('chrome','chromium')):raise ValueError('Локальный redcn принадлежит другому рабочему пространству')
    access=DesktopOwnerAccess(WorkspaceOwner(root),create=False)
    verify_existing_owner(access,port)
    request=urllib.request.Request(base+'/api/local/phone',json.dumps({'action':'enable','port':phone_port}).encode(),headers={'Content-Type':'application/json','Origin':base,'X-Redcn-Token':access.token})
    with opener.open(request,timeout=5) as response:return json.loads(response.read(100_000))


def serve(directory, port=8765, result_id=None, open_browser=True, browser_channel='chrome', phone=False, phone_port=8767):
    server, selected_port = bind_or_reuse(directory,port,browser_channel)
    link = f'http://127.0.0.1:{selected_port}'+('/listing/'+result_id if result_id else '/')
    if server is None:
        print('\nredcn уже работает: '+link+'\nОткрываем существующий сайт. Второй сервер не нужен.',flush=True)
        if phone:
            access=enable_existing_phone(selected_port,phone_port,directory)
            for pair_url in access['pairUrls']:print(pair_url,flush=True)
        if open_browser: webbrowser.open(owner_launch_url(directory,selected_port,result_id))
        return 0
    if phone:
        access=server.phone_relay.enable(phone_port)
        print('Телефон в одной Wi-Fi сети · одноразовая ссылка на 10 минут:',flush=True)
        for pair_url in access['pairUrls']:print(pair_url,flush=True)
    if port and port != selected_port: print(f'Порт {port} занят. Выбран порт {selected_port}.',flush=True)
    print('\nЛокальный redcn: '+link+'\nОставьте терминал открытым. Остановка: Ctrl+C.',flush=True)
    if open_browser: webbrowser.open(server.desktop_access.launch_url(selected_port,result_id))
    previous_term=None
    if threading.current_thread() is threading.main_thread():
        previous_term=signal.signal(signal.SIGTERM,lambda *_:threading.Thread(target=server.shutdown,daemon=True).start())
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally:
        server.server_close()
        if previous_term is not None:signal.signal(signal.SIGTERM,previous_term)
    return 0
