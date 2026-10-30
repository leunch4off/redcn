"""Loopback-only editor, background imports and durable local listing links."""
import json
import base64
from datetime import datetime
import errno
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
from urllib.parse import urlsplit
import urllib.request
import webbrowser

import redcn
import photo_store


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


def save_result(directory, result, result_id=None):
    result_id = result_id or secrets.token_hex(16)
    if not re.fullmatch(r'[a-f0-9]{32}', result_id): raise ValueError('Неверный ID карточки')
    connection = redcn.open_cache(directory)
    try:
        connection.execute('CREATE TABLE IF NOT EXISTS results (id TEXT PRIMARY KEY, updated REAL, payload TEXT)')
        connection.execute('INSERT OR REPLACE INTO results VALUES (?,?,?)', (result_id, time.time(), json.dumps(result, ensure_ascii=False)))
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


def create_server(directory, port=8765, browser_channel='chrome'):
    if browser_channel not in ('chrome','chromium'): raise ValueError('Неверный браузер')
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    token, jobs, lock = secrets.token_urlsafe(32), {}, threading.Lock()
    identity = {'app':'redcn', 'protocol':4, 'storage':hashlib.sha256(str(directory.expanduser().resolve()).encode()).hexdigest(), 'browser_channel':browser_channel}

    def run_import(job_id, params, cancel):
        def report(message):
            with lock: jobs[job_id].update(message=message)
        connection = redcn.open_cache(directory)
        try:
            listing = redcn.load_listing(params['url'], connection, params.get('refresh', False), params.get('loader','auto'), params.get('timeout',180), report, cancel, params.get('browser_channel',browser_channel))
            if cancel.is_set(): raise ValueError('Импорт отменён')
            data = params.get('data', {})
            fields = {**data, **listing.get('fields',{}), 'titleVariant':redcn.next_variant(connection,listing['url']), 'url':listing['url'], 'source':listing['text']}
            result = {**redcn.generate(listing['text'],fields,params.get('rules'),params.get('prefs')), 'listing':listing}
            if cancel.is_set(): raise ValueError('Импорт отменён')
            result_id = save_result(directory, result)
            with lock: jobs[job_id].update(state='done', message='Готово', result_id=result_id)
        except (OSError, ValueError, redcn.subprocess.SubprocessError) as error:
            with lock: jobs[job_id].update(state='error', message=str(error))
        finally: connection.close()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args): pass

        def respond(self, status, value, content_type='application/json; charset=utf-8'):
            content = json.dumps(value, ensure_ascii=False).encode() if content_type.startswith('application/json') else value
            self.send_response(status)
            self.send_header('Content-Type',content_type)
            self.send_header('Content-Length',str(len(content)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Referrer-Policy','no-referrer')
            self.send_header('X-Frame-Options','DENY')
            self.end_headers();self.wfile.write(content)

        def permitted(self, api=False):
            host = f'127.0.0.1:{self.server.server_port}'
            if self.headers.get('Host') != host:
                self.respond(403,{'error':'Откройте сайт через http://'+host});return False
            origin = self.headers.get('Origin')
            if origin and origin != 'http://'+host:
                self.respond(403,{'error':'Неверный источник запроса'});return False
            if api and not secrets.compare_digest(self.headers.get('X-Redcn-Token',''), token):
                self.respond(403,{'error':'Обновите страницу локального сайта'});return False
            return True

        def do_GET(self):
            if not self.permitted(): return
            path = urlsplit(self.path).path
            if path == '/api/local/health':
                self.respond(200,identity);return
            if path.startswith('/api/local/'):
                if not self.permitted(api=True): return
                if path == '/api/local/history':
                    self.respond(200,{'entries':history_rows(directory)});return
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
                    with lock: job = {k:v for k,v in jobs.get(job_id,{}).items() if k not in ('cancel',)}
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
                page = page.replace('<meta charset="utf-8">','<meta charset="utf-8"><base href="/"><meta name="redcn-browser-channel" content="'+browser_channel+'"><meta name="redcn-token" content="'+token+'">')
                page = page.replace('GitHub Pages · данные локально','Локально · Chrome подключён')
                self.respond(200,page.encode(),'text/html; charset=utf-8');return
            files = {'/plan.json':redcn.ROOT/'plan.json', '/research.json':redcn.ROOT/'data/research.json', '/knowledge-seed.json':redcn.ROOT/'data/knowledge-seed.json'}
            if path in files:
                self.respond(200,json.loads(files[path].read_text(encoding='utf-8')));return
            self.respond(404,{'error':'Не найдено'})

        def do_POST(self):
            if not self.permitted(api=True): return
            try:
                if self.headers.get_content_type() != 'application/json': raise ValueError('Нужен JSON')
                size = int(self.headers.get('Content-Length','0'))
                limit=16_100_000 if urlsplit(self.path).path=='/api/local/photo' else 4_500_000 if urlsplit(self.path).path=='/api/local/history' else 1_000_000
                if not 1 <= size <= limit: raise ValueError('Превышен размер запроса')
                data = json.loads(self.rfile.read(size))
                if not isinstance(data,dict): raise ValueError('Нужен JSON-объект')
                path = urlsplit(self.path).path
                if path == '/api/local/photo':
                    if data.get('action','load')=='load':
                        result=photo_store.load_photo(directory,data.get('url',''),data.get('refresh') is True)
                    elif data.get('action')=='crop':
                        result=photo_store.crop_photo(directory,data.get('id'),data.get('margins'))
                    elif data.get('action')=='analyze':
                        result=photo_store.analyze_photo(directory,data.get('id'))
                    elif data.get('action')=='attach':
                        result=photo_store.attach_photo(directory,data.get('url',''),data.get('data'))
                    else: raise ValueError('Неверное действие с фотографией')
                    self.respond(200,result);return
                if path == '/api/local/history':
                    entry_id=store_history(directory,data.get('entry'))
                    self.respond(200,{'id':entry_id});return
                if path == '/api/local/import':
                    data['url'] = redcn.normalize_url(data.get('url',''))
                    if data.get('loader','auto') not in ('auto','http','chrome'): raise ValueError('Неверный режим загрузки')
                    if data.get('browser_channel',browser_channel) not in ('chrome','chromium'): raise ValueError('Неверный браузер')
                    if type(data.get('timeout',180)) is not int or not 15 <= data.get('timeout',180) <= 600: raise ValueError('Ожидание: 15–600 секунд')
                    for field in ('rules','prefs','data'):
                        if field in data and not isinstance(data[field],dict): raise ValueError('Неверный формат '+field)
                    if not all(isinstance(k,str) and isinstance(v,str) for k,v in data.get('rules',{}).items()): raise ValueError('Словарь: строки RU → CN')
                    with lock:
                        active = sum(j['state']=='running' for j in jobs.values())
                        if active: self.respond(409,{'error':'Дождитесь текущего импорта или отмените его'});return
                        # Keep a bounded session history, while results stay on disk.
                        while len(jobs) >= 32: jobs.pop(next(iter(jobs)))
                        job_id, cancel = secrets.token_hex(16), threading.Event()
                        jobs[job_id] = {'state':'running','message':'Начинаем загрузку…','cancel':cancel}
                    threading.Thread(target=run_import,args=(job_id,data,cancel),daemon=True).start()
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
    server.daemon_threads = True
    return server


def matching_server(directory, port, browser_channel):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *_args, **_kwargs): return None
    expected = {'app':'redcn','protocol':4,'storage':hashlib.sha256(str(Path(directory).expanduser().resolve()).encode()).hexdigest(),'browser_channel':browser_channel}
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
        with opener.open(f'http://127.0.0.1:{port}/api/local/health',timeout=1) as response:
            return json.loads(response.read(4097)) == expected
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


def serve(directory, port=8765, result_id=None, open_browser=True, browser_channel='chrome'):
    server, selected_port = bind_or_reuse(directory,port,browser_channel)
    link = f'http://127.0.0.1:{selected_port}'+('/listing/'+result_id if result_id else '/')
    if server is None:
        print('\nredcn уже работает: '+link+'\nОткрываем существующий сайт. Второй сервер не нужен.',flush=True)
        if open_browser: webbrowser.open(link)
        return 0
    if port and port != selected_port: print(f'Порт {port} занят. Выбран порт {selected_port}.',flush=True)
    print('\nЛокальный redcn: '+link+'\nОставьте терминал открытым. Остановка: Ctrl+C.',flush=True)
    if open_browser: webbrowser.open(link)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()
    return 0
