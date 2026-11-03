"""Persistent saved-search inbox; a discovery never imports a full card.

The caller authenticates HTTP requests. Public HTML, the undocumented search
endpoint and an ordinary Chrome session are distinct, explicit adapters. No
credential harvesting, challenge bypass, proxy rotation or gallery visits.
"""
from datetime import datetime, timezone
from html.parser import HTMLParser
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import signal
import socket
import sqlite3
import ssl
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qs, parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
import urllib.error
import urllib.request

import redcn
from search_adapters import platform_for_url
from search_adapters import avito as avito_search
from search_adapters import cian as cian_search
from activity_log import record_attempt, record_import_attempt, list_attempts, safe_error

API_URL = 'https://api.cian.ru/search-offers/v2/search-offers-desktop/'
DEFAULT_SEARCH_URL = 'https://www.cian.ru/cat.php?deal_type=rent&engine_version=2&offer_type=flat&region=1&type=4&sort=creation_date_desc&p=1'
MAX_BYTES = 8_000_000
MAX_ITEMS = 800
MAX_SEEN = 50_000
MAX_PAGES = 2
_ID = re.compile(r'(?:cian:\d{1,18}|avito:\d{1,20})')
_OFFER_PATH = re.compile(r'^/(rent|sale)/flat/(\d{1,18})/?$')
_CHALLENGE = re.compile(r'verify you are human|доступ ограничен|проверка браузера|подтвердите.{0,20}что вы|вы не робот|checking your browser|пройдите проверку', re.I)
_dns_lock = threading.Lock()
_dns_slot = None


class NeedsAttention(ValueError):
    pass


def _db(directory):
    root = Path(directory).expanduser(); root.mkdir(parents=True, exist_ok=True)
    path = root / 'listing-watch.sqlite3'
    if path.is_symlink(): raise ValueError('База мониторинга не может быть ссылкой')
    db = sqlite3.connect(path, timeout=10); db.row_factory = sqlite3.Row; path.chmod(0o600)
    db.executescript('''
        CREATE TABLE IF NOT EXISTS watch_config (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS watch_sources (id TEXT PRIMARY KEY, baseline_at REAL, last_run REAL, last_success REAL, next_run REAL, failures INTEGER DEFAULT 0, error TEXT DEFAULT '', attention INTEGER DEFAULT 0, seen_count INTEGER DEFAULT 0, new_count INTEGER DEFAULT 0, run_count INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS watch_offers (source_id TEXT, id TEXT, payload TEXT NOT NULL, first_seen REAL NOT NULL, last_seen REAL NOT NULL, is_new INTEGER NOT NULL, read_at REAL, PRIMARY KEY(source_id,id));
        CREATE INDEX IF NOT EXISTS watch_new ON watch_offers(source_id,is_new,first_seen,id);
        CREATE TABLE IF NOT EXISTS watch_notifications (id TEXT PRIMARY KEY, source_id TEXT NOT NULL, offer_id TEXT NOT NULL, created REAL NOT NULL, payload TEXT NOT NULL, read_at REAL);
        CREATE TABLE IF NOT EXISTS watch_receipts (source_id TEXT PRIMARY KEY,payload TEXT NOT NULL);
    ''')
    if 'decision' not in {row[1] for row in db.execute('PRAGMA table_info(watch_offers)')}:db.execute("ALTER TABLE watch_offers ADD COLUMN decision TEXT NOT NULL DEFAULT 'accepted'")
    return db


def normalize_search_url(raw):
    if isinstance(raw,str) and re.search(r'https://(?:www\.)?avito\.ru(?=/)',raw):return avito_search.normalize_search_url(raw)
    if not isinstance(raw, str) or len(raw) > 4096: raise ValueError('Нужна ссылка поиска ЦИАН до 4096 символов')
    # html.unescape(raw) would corrupt a literal &region= into ®ion=. A pasted
    # URL is not an HTML document; decode only the actual escaped ampersand.
    match=re.search(r'https://[^\s<>\[\]"\u201c\u201d]+',raw.strip())
    if not match:raise ValueError('Вставьте HTTPS-ссылку поиска ЦИАН')
    value=redcn.allowed_url(match[0].rstrip(')\\.,:;').replace('&amp;','&'),resolve=False);parsed=urlsplit(value)
    if not (parsed.hostname == 'cian.ru' or (parsed.hostname or '').endswith('.cian.ru')):
        raise ValueError('Мониторинг поддерживает поисковую ссылку ЦИАН')
    if parsed.path != '/cat.php' and not re.fullmatch(r'/(?:snyat|kupit)-[a-z0-9-]+/?', parsed.path):
        raise ValueError('Вставьте страницу поиска ЦИАН, а не отдельное объявление или API кабинета')
    values = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
              if k not in ('p', 'page', 'sort') and not k.lower().startswith('utm_')]
    if len(values) > 100: raise ValueError('Слишком много фильтров поиска')
    values += [('sort', 'creation_date_desc'), ('p', '1')]
    return urlunsplit(('https', parsed.netloc.lower(), parsed.path, urlencode(sorted(values)), ''))


def _page_url(url, page):
    parsed = urlsplit(url); values = [(k, v) for k, v in parse_qsl(parsed.query) if k != 'p']
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(values + [('p', str(page))]), ''))


def canonical_search_offer(raw):
    return avito_search.canonical_offer(raw) if platform_for_url(raw)=='avito' else _canonical_offer(raw)


def normalized_filters(value=None):
    value={} if value is None else value
    if not isinstance(value,dict) or set(value)-{'minRent','maxRent','minArea','maxArea','rooms','districts'}:raise ValueError('Неверные фильтры кандидатов')
    result={}
    for key in ('minRent','maxRent','minArea','maxArea'):
        raw=value.get(key)
        if raw in (None,''):continue
        number=_number(raw)
        if number is None:raise ValueError('Фильтр '+key+' должен быть неотрицательным числом')
        result[key]=number
    for low,high in (('minRent','maxRent'),('minArea','maxArea')):
        if low in result and high in result and result[low]>result[high]:raise ValueError('Минимальный фильтр больше максимального')
    rooms=value.get('rooms',[])
    if not isinstance(rooms,list) or len(rooms)>21 or any(type(room) is not int or not 0<=room<=20 for room in rooms):raise ValueError('Комнаты: список целых от 0 до 20')
    if rooms:result['rooms']=sorted(set(rooms))
    districts=value.get('districts',[])
    if not isinstance(districts,list) or len(districts)>20 or any(not isinstance(item,str) or not item.strip() or len(item)>150 or re.search(r'[\x00-\x1f]',item) for item in districts):raise ValueError('Районы: до 20 названий')
    if districts:result['districts']=list(dict.fromkeys(item.strip() for item in districts))
    return result


def candidate_decision(item,filters=None):
    filters=normalized_filters(filters);rejected=[];pending=[];passed=[]
    if item.get('publicationStatus') in ('archived','removed','inactive'):rejected.append('Объявление снято или находится в архиве')
    for field,low,high,label in (('price','minRent','maxRent','Месячная аренда'),('area','minArea','maxArea','Площадь')):
        if low not in filters and high not in filters:continue
        value=_number(item.get(field))
        if field=='price' and (item.get('currency') not in ('RUB','RUR') or item.get('pricePeriod')!='month'):
            pending.append('Период или валюта аренды не подтверждены для месячного рублёвого фильтра');continue
        if value is None:pending.append(label+' не указана');continue
        if low in filters and value<filters[low]:rejected.append(label+' ниже минимума '+str(filters[low]))
        elif high in filters and value>filters[high]:rejected.append(label+' выше максимума '+str(filters[high]))
        else:passed.append(label+' соответствует диапазону')
    if filters.get('rooms'):
        rooms=_number(item.get('rooms'))
        if rooms is None:pending.append('Количество комнат не указано')
        elif rooms not in filters['rooms']:rejected.append('Количество комнат не входит в выбранный список')
        else:passed.append('Количество комнат соответствует выбору')
    if filters.get('districts'):
        district=_text(item.get('district'),150)
        normalize=lambda text:re.sub(r'^(?:р-н|район)\s*','',text.strip().casefold())
        if not district:pending.append('Район источником не подтверждён')
        elif normalize(district) not in {normalize(text) for text in filters['districts']}:rejected.append('Район не входит в выбранный список')
        else:passed.append('Район соответствует выбору')
    state='rejected' if rejected else 'pending' if pending else 'accepted'
    return {'state':state,'reasons':rejected+pending+passed or ['Дополнительные фильтры не заданы; доступна ручная проверка ссылки']}


# Compatibility names for the public saved-search contract. Provider parsing
# lives in its own adapter rather than growing the shared scheduler.
api_query=cian_search.api_query
_text=cian_search._text
_number=cian_search._number
_publication=cian_search._publication
_canonical_offer=cian_search._canonical_offer
_offer=cian_search._offer
_unique_items=cian_search._unique_items


def parse_cian_search(content,source_url,mime='text/html'):
    try:return cian_search.parse_search(content,source_url,mime)
    except cian_search.NeedsAttention as error:raise NeedsAttention(str(error)) from None


def parse_search(content, source_url, mime='text/html'):
    if platform_for_url(source_url)=='avito':
        try:return avito_search.parse_search(content,source_url,mime)
        except ValueError as error:
            if str(error).startswith('SEARCH_NEEDS_ATTENTION:'):raise NeedsAttention('Авито требует ручной проверки; автоматический обход не выполняется.') from None
            raise
    return parse_cian_search(content,source_url,mime)


def _addresses(host):
    global _dns_slot
    with _dns_lock:
        if _dns_slot is None or _dns_slot['done'].is_set():
            slot={'host':host,'done':threading.Event(),'result':None,'error':False};_dns_slot=slot
            def resolve():
                try:slot['result']=socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)
                except OSError:slot['error']=True
                finally:slot['done'].set()
            threading.Thread(target=resolve,daemon=True).start()
        else:
            slot=_dns_slot
            if slot['host']!=host:raise ValueError('DNS ещё занят предыдущим запросом; повторите позже')
    if not slot['done'].wait(2):raise TimeoutError('DNS не ответил за 2 секунды')
    if slot['error'] or not slot['result']:raise ValueError('Не удалось разрешить адрес ЦИАН')
    addresses=list(dict.fromkeys(item[4][0] for item in slot['result']))
    if any(not ipaddress.ip_address(address).is_global for address in addresses): raise ValueError('Непубличный адрес поиска запрещён')
    return addresses


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


def _http_page(url, data=None, budget=12):
    end=time.monotonic()+max(.1,min(12,budget))
    redcn.allowed_url(url,resolve=False); host=urlsplit(url).hostname
    addresses=_addresses(host)
    try:
        import certifi
        context=ssl.create_default_context(cafile=certifi.where())
    except ImportError:context=ssl.create_default_context()
    from connection_diagnostics import _PinnedHTTPS
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),_PinnedHTTPS(addresses,context),_NoRedirect())
    headers={'User-Agent':'redcn-listing-watch/1.0 (bounded saved search)','Accept-Encoding':'identity','Accept':'application/json' if data else 'text/html'}
    payload=json.dumps(data).encode() if data is not None else None
    if payload is not None:headers['Content-Type']='application/json'
    try:
        remaining=end-time.monotonic()
        if remaining<=0:raise TimeoutError('Поисковый ответ не завершился в срок')
        with opener.open(urllib.request.Request(url,data=payload,headers=headers),timeout=remaining) as response:
            # read1 returns available data rather than filling an entire buffer;
            # an indefinitely trickling body cannot reset a per-read timeout.
            parts=[];size=0
            while size<=MAX_BYTES:
                remaining=end-time.monotonic()
                if remaining<=0:raise TimeoutError('Поисковый ответ не завершился за 12 секунд')
                try:response.fp.raw._sock.settimeout(remaining)
                except AttributeError:pass
                chunk=response.read1(min(65536,MAX_BYTES+1-size))
                if not chunk:break
                parts.append(chunk);size+=len(chunk)
            content=b''.join(parts);mime=response.headers.get_content_type()
    except urllib.error.HTTPError as error:
        if error.code in (401,403,429):raise NeedsAttention('ЦИАН вернул HTTP '+str(error.code)+'. Проверьте доступ вручную; мониторинг увеличил интервал повторной попытки.') from None
        if 300<=error.code<400:raise NeedsAttention('Поиск перенаправляет на другую страницу. Сохраните актуальную ссылку из браузера.') from None
        raise ValueError('ЦИАН вернул HTTP '+str(error.code)) from None
    if len(content)>MAX_BYTES:raise ValueError('Поисковый ответ превышает 8 МБ')
    if mime not in ('text/html','application/xhtml+xml','application/json'):raise ValueError('Неожиданный формат поисковой выдачи')
    return content,mime


_BROWSER_SNAPSHOT = r'''() => ({html:document.documentElement.outerHTML.slice(0,8000000), text:(document.body?.innerText||'').slice(0,12000), links:[...document.querySelectorAll('[data-name="CardComponent"] a[href],[data-name="OfferCard"] a[href],[data-testid="offer-card"] a[href]')].filter(e=>!e.closest('[data-name*="Recommend"],[data-name*="Suggest"],[data-name*="Similar"]')).map(e=>({fullUrl:e.href,title:e.closest('[data-name="CardComponent"],[data-name="OfferCard"],[data-testid="offer-card"]')?.innerText.slice(0,240)||e.innerText})).slice(0,100)})'''


def browser_response_items(response,index,source_url):
    """Observe the search response the ordinary page itself requested.

    No extra API request, headers or cookies are manufactured. Account,
    recommendation and late previous-page responses are ignored.
    """
    parsed=urlsplit(response.url)
    if parsed.scheme!='https' or parsed.hostname!='api.cian.ru' or parsed.path!='/search-offers/v2/search-offers-desktop/' or response.status!=200:return []
    try:
        query=response.request.post_data_json.get('jsonQuery',{})
        expected=query.get('page',{})
        if not isinstance(expected,dict) or expected.get('value')!=index:return []
        if query.get('sort',{}).get('value')!='creation_date_desc':return []
        length=response.headers.get('content-length','')
        if length.isdigit() and int(length)>MAX_BYTES:return []
        content=response.body()
        if len(content)>MAX_BYTES:return []
        return parse_search(content,source_url,'application/json')
    except (AttributeError,TypeError,ValueError):return []


def allowed_search_navigation(url,source_url,resolve=None):
    """Validate navigation before Chrome sends it, including redirects."""
    source=normalize_search_url(source_url);target=normalize_search_url(url)
    source_host=urlsplit(source).hostname;target_host=urlsplit(target).hostname
    hosts={source_host}
    if source_host in ('cian.ru','www.cian.ru'):hosts.update(('cian.ru','www.cian.ru'))
    if source_host in ('avito.ru','www.avito.ru'):hosts.update(('avito.ru','www.avito.ru'))
    if target_host not in hosts:raise ValueError('Переход на другой хост поиска запрещён')
    # Pagination and tracking are normalized away, while semantic filters must
    # remain the same. A redirect cannot silently replace the saved query.
    a=urlsplit(source);b=urlsplit(target)
    if (a.path,a.query)!=(b.path,b.query):raise ValueError('Переход изменил сохранённые фильтры поиска')
    (resolve or _addresses)(target_host)
    return redcn.allowed_url(url,resolve=False)


def guard_search_navigation(route,source_url,on_block=None):
    request=route.request
    if not request.is_navigation_request():route.continue_();return
    try:allowed_search_navigation(request.url,source_url)
    except (ValueError,TimeoutError,OSError):
        if on_block:
            try:is_main=request.frame.parent_frame is None
            except Exception:is_main=True
            on_block(is_main)
        route.abort();return
    route.continue_()


def fetch_search(config, directory, cancel, browser_channel='chrome'):
    """One saved search, explicitly bounded pages; never individual ad visits."""
    url=config['searchUrl'];rows=[];pages=0;adapter=config['adapter'];confirmed_empty=False
    platform=platform_for_url(url);max_pages=config.get('maxPages',MAX_PAGES)
    if type(max_pages) is not int or not 1<=max_pages<=8:raise ValueError('Охват поиска: от 1 до 8 страниц')
    if platform=='avito' and adapter=='api':raise ValueError('Для Авито поддержаны публичный HTML и обычный Chrome; доступного API выдачи не заявлено')
    started=time.monotonic();deadline=started+30;stop_reason='page-limit';known=set(config.get('knownIds',[]));cutoff=config.get('stopBeforePublished');old_pages=0;page_counts=[]
    def boundary(found):
        nonlocal old_pages
        old=bool(found) and bool(known) and all(item.get('id') in known for item in found)
        if old and type(cutoff) in (int,float):
            for item in found:
                value=item.get('publishedAt')
                try:stamp=datetime.fromisoformat(value.replace('Z','+00:00')).timestamp() if isinstance(value,str) and len(value)>10 and re.search(r'(?:Z|[+-]\d{2}:\d{2})$',value) else None
                except ValueError:stamp=None
                if stamp is None or stamp>cutoff:old=False;break
        else:old=False
        old_pages=old_pages+1 if old else 0
        return old_pages>=2
    if adapter=='chrome':
        from browser_import import _browser_lock
        try:from playwright.sync_api import sync_playwright
        except ImportError:raise ValueError('Для мониторинга Chrome установите зависимости cli/requirements-browser.txt') from None
        if not _browser_lock.acquire(False):raise ValueError('Chrome занят ручным импортом. Мониторинг повторит поиск позднее.')
        try:
            redcn.allowed_url(url,resolve=False);_addresses(urlsplit(url).hostname)
            profile=Path(directory)/(browser_channel+'-profile');profile.mkdir(parents=True,exist_ok=True,mode=0o700)
            with sync_playwright() as runtime:
                context=runtime.chromium.launch_persistent_context(str(profile),channel='chrome' if browser_channel=='chrome' else None,headless=False,accept_downloads=False,service_workers='block',timeout=20000)
                try:
                    if cancel.is_set():raise ValueError('Мониторинг остановлен')
                    blocked_main=[False]
                    def blocked(is_main):
                        if is_main:blocked_main[0]=True
                    context.route('**/*',lambda route:guard_search_navigation(route,url,blocked))
                    page=context.new_page();page.set_default_timeout(1500)
                    api_results={};current_page=[1]
                    def response_seen(request):
                        if platform!='cian':return
                        try:
                            response=request.response()
                            if response is None:return
                            found=browser_response_items(response,current_page[0],url)
                            if found or getattr(found,'confirmed_empty',False):api_results[current_page[0]]=found
                        except Exception:pass
                    # Inspect only completed responses so reading JSON cannot
                    # turn a pending API request into an unbounded body wait.
                    page.on('requestfinished',response_seen)
                    for index in range(1,max_pages+1):
                        if cancel.is_set():raise ValueError('Мониторинг остановлен')
                        if time.monotonic()+1>=deadline:stop_reason='time-budget';break
                        current_page[0]=index
                        try:response=page.goto(_page_url(url,index),wait_until='commit',timeout=max(1000,int(min(15,deadline-time.monotonic())*1000)))
                        except Exception:
                            if blocked_main[0]:raise NeedsAttention('Остановлен внешний переход или изменение фильтров. Сохраните прямую конечную ссылку поиска ЦИАН.') from None
                            raise
                        if response and response.status in (401,403,429):raise NeedsAttention('ЦИАН запросил ручную проверку; автоматический поиск приостановлен до следующей попытки.')
                        found=[]
                        while time.monotonic()<deadline and not cancel.is_set():
                            if blocked_main[0]:raise NeedsAttention('Остановлен внешний переход поисковой страницы. Сохраните прямую ссылку ЦИАН.')
                            redcn.allowed_url(page.url,resolve=False)
                            snapshot=page.evaluate(_BROWSER_SNAPSHOT)
                            if _CHALLENGE.search(snapshot['text']):raise NeedsAttention('В Chrome появилась проверка ЦИАН. Пройдите её вручную при следующей ручной загрузке; обход не выполняется.')
                            observed=api_results.get(index)
                            found=observed if observed is not None else parse_search(snapshot['html'],url)
                            if platform=='cian' and not found and not getattr(found,'confirmed_empty',False):found=list(filter(None,(_offer(item) for item in snapshot['links'])))
                            if found or getattr(found,'confirmed_empty',False):break
                            page.wait_for_timeout(200)
                        pages+=1
                        confirmed_empty=confirmed_empty or getattr(found,'confirmed_empty',False)
                        if not found:stop_reason='confirmed-empty' if getattr(found,'confirmed_empty',False) else 'unrecognized-page';break
                        rows.extend(found);page_counts.append(len(found))
                        if boundary(found):stop_reason='known-date-boundary';break
                        if time.monotonic()+2>=deadline:stop_reason='time-budget';break
                finally:context.close()
        finally:_browser_lock.release()
    else:
        for index in range(1,max_pages+1):
            if cancel.is_set():raise ValueError('Мониторинг остановлен')
            if time.monotonic()+1>=deadline:stop_reason='time-budget';break
            try:content,mime=_http_page(API_URL,api_query(url,index),budget=deadline-time.monotonic()) if adapter=='api' else _http_page(_page_url(url,index),budget=deadline-time.monotonic())
            except TimeoutError:
                if rows:stop_reason='time-budget';break
                raise
            found=parse_search(content,url,mime);pages+=1
            confirmed_empty=confirmed_empty or getattr(found,'confirmed_empty',False)
            if not found:stop_reason='confirmed-empty' if getattr(found,'confirmed_empty',False) else 'unrecognized-page';break
            rows.extend(found);page_counts.append(len(found))
            if boundary(found):stop_reason='known-date-boundary';break
    unique=_unique_items(rows)
    return {'items':unique, 'pages':pages, 'coverage':'first-2-pages' if max_pages==2 else 'bounded-pages', 'adapter':adapter,'platform':platform,'confirmedEmpty':confirmed_empty and not rows,
        'coverageInfo':{'requestedPages':max_pages,'visitedPages':pages,'stopReason':stop_reason,'partial':stop_reason not in ('confirmed-empty','known-date-boundary'),'seconds':round(time.monotonic()-started,6),'budgetSeconds':35,'pageItems':page_counts,'duplicateCount':len(rows)-len(unique),'unknownPublicationDates':sum(not row.get('publishedAt') for row in unique),'gapCount':None}}


def _source_command():
    return [sys.executable,str(Path(__file__).resolve()),'--search-worker']


def _stop_owned_process(process):
    """Only a process tree started here in its own session can be stopped."""
    if process.poll() is not None:return
    if os.name=='posix':
        try:os.killpg(process.pid,signal.SIGTERM)
        except ProcessLookupError:return
        try:process.wait(timeout=3)
        except subprocess.TimeoutExpired:pass
        # Also reap a renderer/driver that outlived its already stopped owner.
        try:os.killpg(process.pid,signal.SIGKILL)
        except ProcessLookupError:pass
    elif os.name=='nt':
        subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True,timeout=5)
    else:process.kill()
    try:process.wait(timeout=2)
    except subprocess.TimeoutExpired:raise RuntimeError('Не удалось завершить собственный процесс поиска') from None


def fetch_search_bounded(config,directory,cancel,browser_channel='chrome',timeout=35):
    """Wall deadline covers DNS, browser launch, evaluation and body reads.

    Ordinary Playwright DOM evaluation has no cancellation timeout. A separate
    owned process group provides the final limit without closing another user's
    Chrome or invoking Playwright from the controller thread.
    """
    browser_lock=None;process=None
    if config['adapter']=='chrome':
        from browser_import import _browser_lock
        if not _browser_lock.acquire(False):raise ValueError('Chrome занят ручным импортом. Мониторинг повторит поиск позднее.')
        browser_lock=_browser_lock
    try:
        if cancel.is_set():raise ValueError('Мониторинг остановлен')
        started=time.monotonic()
        process=subprocess.Popen(_source_command(),stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=os.name=='posix')
        payload=json.dumps({'config':config,'directory':str(Path(directory).resolve()),'browserChannel':browser_channel}).encode()
        first=True
        while True:
            if cancel.is_set():_stop_owned_process(process);raise ValueError('Мониторинг остановлен')
            remaining=timeout-(time.monotonic()-started)
            if remaining<=0:
                _stop_owned_process(process);raise TimeoutError('Поиск превысил 35 секунд; собственный процесс и окно Chrome завершены.')
            try:
                output,_stderr=process.communicate(input=payload if first else None,timeout=min(.2,remaining))
                break
            except subprocess.TimeoutExpired:first=False
        if len(output)>3_000_000:raise ValueError('Поиск вернул слишком большой служебный ответ')
        try:value=json.loads(output)
        except ValueError:raise ValueError('Собственный процесс поиска не вернул корректный результат') from None
        if value.get('error'):
            raise (NeedsAttention if value.get('attention') else ValueError)(str(value['error'])[:300])
        if process.returncode!=0 or not isinstance(value.get('items'),list):raise ValueError('Собственный процесс поиска завершился с ошибкой')
        return value
    finally:
        if process is not None:
            if process.poll() is None:_stop_owned_process(process)
            for stream in (process.stdin,process.stdout,process.stderr):
                if stream is not None:stream.close()
        if browser_lock:browser_lock.release()


def _search_worker():
    try:
        value=json.loads(sys.stdin.read(64_001));config=value['config']
        config['searchUrl']=normalize_search_url(config['searchUrl'])
        if config.get('adapter') not in ('html','api','chrome'):raise ValueError('Неизвестный источник мониторинга')
        if type(config.get('maxPages',2)) is not int or not 1<=config.get('maxPages',2)<=8:raise ValueError('Охват поиска: от 1 до 8 страниц')
        known=config.get('knownIds',[])
        if not isinstance(known,list) or len(known)>500 or any(not isinstance(item,str) or not _ID.fullmatch(item) for item in known):raise ValueError('Неверная контрольная граница поиска')
        channel=value.get('browserChannel','chrome')
        if channel not in ('chrome','chromium'):raise ValueError('Неверный браузер')
        # The first termination request unwinds the ordinary context's finally;
        # the owner retains a hard process-group fallback for a wedged renderer.
        def stop(*_):raise KeyboardInterrupt()
        signal.signal(signal.SIGTERM,stop)
        result=fetch_search(config,Path(value['directory']),threading.Event(),channel)
    except Exception as error:
        result={'error':str(error)[:300] if isinstance(error,(ValueError,TimeoutError)) else 'Поиск не завершён: '+type(error).__name__,'attention':isinstance(error,NeedsAttention)}
    print(json.dumps(result,ensure_ascii=False,allow_nan=False),flush=True)


class ListingWatch:
    def __init__(self,directory,fetch=None,now=time.time,start=True,browser_channel='chrome'):
        if browser_channel not in ('chrome','chromium'):raise ValueError('Неверный браузер мониторинга')
        self.directory=Path(directory);self.now=now;self.fetch=fetch or (lambda config,cancel:fetch_search_bounded(config,self.directory,cancel,browser_channel))
        self._lock=threading.RLock();self._run_lock=threading.Lock();self._wake=threading.Event();self._stop=threading.Event();self._force=False;self._checking=False;self._thread=None;self._owner=None
        with _db(self.directory) as db:
            if db.execute('SELECT 1 FROM watch_config').fetchone() is None:db.execute('INSERT INTO watch_config VALUES(1,?)',(json.dumps({'enabled':False,'searchUrl':'','adapter':'html','intervalMinutes':60}),))
            # An interrupted process did not finish a successful or failed run.
            # Resume that one due check; retain explicit error backoff unchanged.
            db.execute('UPDATE watch_sources SET next_run=? WHERE last_run IS NOT NULL AND last_run>COALESCE(last_success,0) AND error=\'\'',(self.now(),))
        if start:self.start()

    def start(self):
        with self._lock:
            if self._thread is None:
                self._thread=threading.Thread(target=self._loop,daemon=True,name='redcn-listing-watch');self._thread.start()

    def close(self):
        self._stop.set();self._wake.set()
        with self._lock:threads=set(filter(None,(self._thread,self._owner)))
        deadline=time.monotonic()+40
        for worker in threads:
            if worker is not threading.current_thread():worker.join(max(0,deadline-time.monotonic()))
        if any(worker.is_alive() and worker is not threading.current_thread() for worker in threads):
            raise RuntimeError('Собственная проверка поиска ещё завершает работу; повторите остановку позднее')

    def _config(self,db):
        saved=json.loads(db.execute('SELECT payload FROM watch_config WHERE id=1').fetchone()[0])
        return {'enabled':False,'searchUrl':'','adapter':'html','intervalMinutes':60,'maxPages':2,'filters':{},'notifications':{'enabled':False,'channel':'in-app'},**saved}

    @staticmethod
    def _source(config):return hashlib.sha256(config['searchUrl'].encode()).hexdigest()[:24] if config['searchUrl'] else ''

    def configure(self,data):
        if not isinstance(data,dict) or set(data)-{'enabled','searchUrl','intervalMinutes','adapter','maxPages','filters','notifications'}:raise ValueError('Неверные параметры мониторинга')
        with self._lock,_db(self.directory) as db:
            config=self._config(db);config.update(data)
            if type(config['enabled']) is not bool:raise ValueError('Неверный флаг включения')
            if type(config['intervalMinutes']) is not int or not 60<=config['intervalMinutes']<=1440:raise ValueError('Интервал: от 60 до 1440 минут')
            if config['adapter'] not in ('html','api','chrome'):raise ValueError('Неизвестный источник мониторинга')
            if type(config['maxPages']) is not int or not 1<=config['maxPages']<=8:raise ValueError('Охват: от 1 до 8 страниц; расширение задаётся явно')
            config['filters']=normalized_filters(config['filters'])
            notices=config['notifications']
            if not isinstance(notices,dict) or set(notices)-{'enabled','channel'} or type(notices.get('enabled')) is not bool or notices.get('channel')!='in-app':raise ValueError('Доступен только явно включаемый внутренний канал приложения; внешние адреса не принимаются')
            config['searchUrl']=normalize_search_url(config['searchUrl']) if config['searchUrl'] else ''
            if config['enabled'] and not config['searchUrl']:raise ValueError('Сначала сохраните ссылку поиска ЦИАН или Авито')
            if config['adapter']=='api' and platform_for_url(config['searchUrl'])=='avito':raise ValueError('API выдачи Авито не подключён. Выберите публичный HTML или обычный Chrome.')
            if config['adapter']=='api' and config['searchUrl']:api_query(config['searchUrl'])
            source=self._source(config)
            db.execute('UPDATE watch_config SET payload=? WHERE id=1',(json.dumps(config),))
            if source:
                db.execute('INSERT OR IGNORE INTO watch_sources(id,next_run) VALUES(?,?)',(source,self.now()))
                if config['enabled']:db.execute('UPDATE watch_sources SET next_run=COALESCE(next_run,?) WHERE id=?',(self.now(),source))
                for row in db.execute('SELECT id,payload FROM watch_offers WHERE source_id=?',(source,)).fetchall():
                    item=json.loads(row['payload']);decision=candidate_decision(item,config['filters']);item['decision']=decision['state'];item['decisionReasons']=decision['reasons'];db.execute('UPDATE watch_offers SET payload=?,decision=? WHERE source_id=? AND id=?',(json.dumps(item,ensure_ascii=False),decision['state'],source,row['id']))
        self._wake.set();return self.status()

    def status(self):
        with self._lock,_db(self.directory) as db:
            config=self._config(db);source=self._source(config);row=db.execute('SELECT * FROM watch_sources WHERE id=?',(source,)).fetchone();info=dict(row) if row else {}
            counts=db.execute("SELECT COUNT(*),SUM(CASE WHEN is_new=1 AND decision='accepted' THEN 1 ELSE 0 END),SUM(CASE WHEN is_new=1 AND read_at IS NULL AND decision='accepted' THEN 1 ELSE 0 END) FROM watch_offers WHERE source_id=?",(source,)).fetchone()
            receipt=db.execute('SELECT payload FROM watch_receipts WHERE source_id=?',(source,)).fetchone();coverage=json.loads(receipt[0]) if receipt else {}
            decision_counts={row[0]:row[1] for row in db.execute('SELECT decision,COUNT(*) FROM watch_offers WHERE source_id=? GROUP BY decision',(source,))}
            notification_count=db.execute('SELECT COUNT(*) FROM watch_notifications WHERE source_id=? AND read_at IS NULL',(source,)).fetchone()[0]
            state='checking' if self._checking or self._force else 'disabled' if not config['enabled'] else 'needs_attention' if info.get('attention') else 'error' if info.get('error') else 'idle'
            stop_titles={'page-limit':'достигнут заданный предел','time-budget':'истёк срок прохода','known-date-boundary':'достигнута граница известных публикаций','confirmed-empty':'источник подтвердил конец выдачи','unrecognized-page':'следующая страница не распознана','unknown':'причина не измерена'}
            note=('Последний сохранённый проход: '+str(coverage.get('visitedPages',0))+' из '+str(coverage.get('requestedPages',config['maxPages']))+' страниц; остановка: '+stop_titles.get(coverage.get('stopReason'),'причина не измерена')+'. ') if coverage else ('Заданный предел: '+str(config['maxPages'])+' страниц. ')
            return {'config':config,'state':state,'platform':platform_for_url(config['searchUrl']),'baselineComplete':info.get('baseline_at') is not None,'baselineAt':info.get('baseline_at'),'lastRun':info.get('last_run'),'lastSuccess':info.get('last_success'),'nextRun':info.get('next_run') if config['enabled'] else None,'error':info.get('error',''),'coverage':'first-2-pages' if config['maxPages']==2 else 'bounded-pages','coverageInfo':coverage,'coverageNote':note+'Проверяется ограниченная часть сохранённого поиска; полный каталог и отсутствие пропусков не гарантируются.','unreadCount':counts[2] or 0,'newCount':counts[1] or 0,'totalSeen':counts[0],'decisionCounts':decision_counts,'notificationsUnreadCount':notification_count,'lastSeenCount':info.get('seen_count',0),'runCount':info.get('run_count',0),'consecutiveFailures':info.get('failures',0)}

    def request_check(self):
        with self._lock:
            if not self.status()['config']['searchUrl']:raise ValueError('Сначала сохраните поисковую ссылку ЦИАН или Авито')
            if self._checking or self._force:return self.status()
            self._force=True;self.start();self._wake.set()
        return self.status()

    def _loop(self):
        while not self._stop.is_set():
            with self._lock:force=self._force;self._force=False
            try:self.tick(force=force)
            except (ValueError,OSError,sqlite3.Error):pass
            self._wake.wait(30);self._wake.clear()

    def tick(self,force=False):
        if self._stop.is_set() or not self._run_lock.acquire(False):return self.status()
        try:
            with self._lock,_db(self.directory) as db:
                if self._stop.is_set():return self.status()
                self._owner=threading.current_thread()
                config=self._config(db);source=self._source(config)
                row=db.execute('SELECT * FROM watch_sources WHERE id=?',(source,)).fetchone();info=dict(row) if row else {}
                now=self.now()
                if not source or not force and (not config['enabled'] or info.get('next_run',0)>now):return self.status()
                if force and info.get('last_run') is not None and now-info['last_run']<10:return self.status()
                self._checking=True
                db.execute('UPDATE watch_sources SET last_run=?,next_run=? WHERE id=?',(now,now+config['intervalMinutes']*60,source))
                known_ids=[row[0] for row in db.execute('SELECT id FROM watch_offers WHERE source_id=? ORDER BY last_seen DESC LIMIT 500',(source,))]
            operation_started=time.monotonic();fetch_seconds=None;store_seconds=None;attempt_error=None;attempt_outcome='error'
            try:
                fetch_config={**config,'knownIds':known_ids,'stopBeforePublished':info.get('last_success')}
                result=self.fetch(fetch_config,self._stop);fetch_seconds=time.monotonic()-operation_started
                if self._stop.is_set():return self.status()
                if not isinstance(result,dict) or not isinstance(result.get('items'),list):raise ValueError('Источник вернул некорректный список')
                items=[];seen=set()
                for item in result['items'][:MAX_ITEMS]:
                    if not isinstance(item,dict):continue
                    identity=canonical_search_offer(item.get('url'))
                    if not identity or item.get('id')!=identity[0] or identity[0] in seen:continue
                    if platform_for_url(identity[1])!=platform_for_url(config['searchUrl']):continue
                    seen.add(identity[0]);published,label=_publication(item.get('publishedAt'))
                    clean={'id':identity[0],'url':identity[1],'platform':platform_for_url(identity[1]),'title':_text(item.get('title'),400),'price':_number(item.get('price')),'currency':item.get('currency') if item.get('currency') in ('RUB','RUR','USD','EUR') else None,'pricePeriod':item.get('pricePeriod') if item.get('pricePeriod') in ('month','day','week','year','sale') else 'unknown','area':_number(item.get('area')),'rooms':_number(item.get('rooms')),'metro':_text(item.get('metro'),150),'district':_text(item.get('district'),150),'address':_text(item.get('address'),400),'publishedAt':published,'publishedLabel':_text(item.get('publishedLabel'),160) or label,'publicationDateSource':_text(item.get('publicationDateSource'),100),'publicationStatus':item.get('publicationStatus') if item.get('publicationStatus') in ('active','removed','archived','inactive') else 'unknown'}
                    decision=candidate_decision(clean,config['filters']);clean.update(decision=decision['state'],decisionReasons=decision['reasons']);items.append(clean)
                if not items and result.get('confirmedEmpty') is not True:raise NeedsAttention('Поиск не дал распознанных объявлений. Базовый снимок не создан; проверьте фильтры, разметку или доступ в Chrome.')
                stamp=self.now();new_count=0;store_started=time.monotonic()
                with self._lock,_db(self.directory) as db:
                    if self._stop.is_set():raise ValueError('Мониторинг остановлен')
                    baseline=info.get('baseline_at')
                    existing_ids={row[0] for row in db.execute('SELECT id FROM watch_offers WHERE source_id=? AND id IN ('+','.join('?' for _ in items)+')',(source,*(item['id'] for item in items)))} if items else set()
                    additions=sum(item['id'] not in existing_ids for item in items)
                    if db.execute('SELECT COUNT(*) FROM watch_offers').fetchone()[0]+additions>MAX_SEEN:raise ValueError('База мониторинга достигла лимита 50 000 записей. Нужен архив; старые ID не удаляются автоматически.')
                    for item in items:
                        if self._stop.is_set():raise ValueError('Мониторинг остановлен')
                        existing=item['id'] in existing_ids
                        is_new=baseline is not None
                        # Known historical ads surfacing after ranking changes are
                        # retained in All, without claiming a new publication.
                        published=item.get('publishedAt')
                        if is_new and isinstance(published,str):
                            try:
                                if len(published)==10:is_new=published>=datetime.fromtimestamp(baseline,timezone.utc).date().isoformat()
                                else:is_new=datetime.fromisoformat(published.replace('Z','+00:00')).timestamp()>baseline
                            except ValueError:pass
                        if not existing:
                            db.execute('INSERT INTO watch_offers(source_id,id,payload,first_seen,last_seen,is_new,read_at,decision) VALUES(?,?,?,?,?,?,NULL,?)',(source,item['id'],json.dumps(item,ensure_ascii=False),stamp,stamp,int(is_new),item['decision']));new_count+=int(is_new and item['decision']=='accepted')
                            if is_new and item['decision']=='accepted' and config['notifications']['enabled']:
                                notification_id=hashlib.sha256((source+'|'+item['id']).encode()).hexdigest()[:32]
                                payload={key:item[key] for key in ('id','url','title','platform')}
                                db.execute('INSERT OR IGNORE INTO watch_notifications VALUES(?,?,?,?,?,NULL)',(notification_id,source,item['id'],stamp,json.dumps(payload,ensure_ascii=False)))
                        else:db.execute('UPDATE watch_offers SET payload=?,last_seen=?,decision=? WHERE source_id=? AND id=?',(json.dumps(item,ensure_ascii=False),stamp,item['decision'],source,item['id']))
                    if self._stop.is_set():raise ValueError('Мониторинг остановлен')
                    db.execute('UPDATE watch_sources SET baseline_at=COALESCE(baseline_at,?),last_success=?,next_run=?,failures=0,error=\'\',attention=0,seen_count=?,new_count=?,run_count=run_count+1 WHERE id=?',(stamp,stamp,stamp+config['intervalMinutes']*60,len(items),new_count,source))
                    raw_coverage=result.get('coverageInfo') if isinstance(result.get('coverageInfo'),dict) else {}
                    pages=result.get('pages') if type(result.get('pages')) is int else raw_coverage.get('visitedPages',0);pages=max(0,min(config['maxPages'],pages)) if type(pages) is int else 0
                    reason=raw_coverage.get('stopReason');reason=reason if reason in ('page-limit','time-budget','known-date-boundary','confirmed-empty','unrecognized-page') else 'unknown'
                    page_counts=raw_coverage.get('pageItems',[]);page_counts=page_counts if isinstance(page_counts,list) and len(page_counts)<=8 and all(type(count) is int and 0<=count<=MAX_ITEMS for count in page_counts) else []
                    duplicate_count=raw_coverage.get('duplicateCount');duplicate_count=duplicate_count if type(duplicate_count) is int and 0<=duplicate_count<=MAX_ITEMS*8 else None
                    coverage={'requestedPages':config['maxPages'],'visitedPages':pages,'stopReason':reason,'partial':reason not in ('known-date-boundary','confirmed-empty'),'recognizedItems':len(items),'budgetSeconds':35,'pageItems':page_counts,'duplicateCount':duplicate_count,'unknownPublicationDates':sum(not item.get('publishedAt') for item in items),'gapCount':None}
                    db.execute('INSERT OR REPLACE INTO watch_receipts VALUES(?,?)',(source,json.dumps(coverage)))
                store_seconds=time.monotonic()-store_started;attempt_outcome='partial' if reason in ('time-budget','unrecognized-page') else 'done'
            except Exception as error:
                attempt_error=error;attempt_outcome='needs_attention' if isinstance(error,NeedsAttention) else 'error'
                if fetch_seconds is None:fetch_seconds=time.monotonic()-operation_started
                if not self._stop.is_set():
                    with self._lock,_db(self.directory) as db:
                        failures=info.get('failures',0)+1;delay=min(24*3600,config['intervalMinutes']*60*2**min(failures,4))
                        _code,message=safe_error(error)
                        db.execute('UPDATE watch_sources SET failures=?,error=?,attention=?,next_run=?,run_count=run_count+1 WHERE id=?',(failures,message,int(isinstance(error,NeedsAttention)),self.now()+delay,source))
            finally:
                with self._lock:self._checking=False
                try:record_attempt(self.directory,kind='watch',source_url=config['searchUrl'],started=now,elapsed=min(7200,time.monotonic()-operation_started),outcome='cancelled' if self._stop.is_set() else attempt_outcome,stages={key:value for key,value in (('fetch',fetch_seconds),('store',store_seconds)) if value is not None},error=attempt_error)
                except (OSError,ValueError,sqlite3.Error):pass
        finally:
            with self._lock:self._owner=None
            self._run_lock.release()
        return self.status()

    def list_items(self,category='new',limit=50,before=None):
        if category not in ('new','all','accepted','rejected','pending') or isinstance(limit,bool):raise ValueError('Неверная категория списка')
        try:limit=int(limit)
        except (ValueError,TypeError):raise ValueError('Неверный размер страницы') from None
        if not 1<=limit<=100:raise ValueError('Размер страницы: от 1 до 100')
        args=[];where='source_id=?'
        with self._lock,_db(self.directory) as db:
            args.append(self._source(self._config(db)))
            if category=='new':where+=" AND is_new=1 AND decision='accepted'"
            elif category in ('accepted','rejected','pending'):where+=' AND decision=?';args.append(category)
            if before:
                match=re.fullmatch(r'(\d+(?:\.\d+)?):((?:cian:\d{1,18}|avito:\d{1,20}))',str(before))
                if not match:raise ValueError('Неверный курсор списка')
                stamp=float(match[1]);identity=match[2];where+=' AND (first_seen<? OR (first_seen=? AND id<?))';args.extend([stamp,stamp,identity])
            rows=db.execute('SELECT * FROM watch_offers WHERE '+where+' ORDER BY first_seen DESC,id DESC LIMIT ?',(*args,limit+1)).fetchall();more=len(rows)>limit;rows=rows[:limit]
            items=[{**json.loads(row['payload']),'firstSeen':row['first_seen'],'lastSeen':row['last_seen'],'read':row['read_at'] is not None,'isNew':bool(row['is_new'])} for row in rows]
        cursor=repr(rows[-1]['first_seen'])+':'+rows[-1]['id'] if more and rows else None
        return {'items':items,'hasMore':more,'nextBefore':cursor,'category':category}

    def list_new(self,limit=50,before=None):return self.list_items('new',limit,before)

    def mark_read(self,ids):
        if not isinstance(ids,list) or len(ids)>100 or any(not isinstance(value,str) or not _ID.fullmatch(value) for value in ids):raise ValueError('Неверные ID объявлений')
        with self._lock,_db(self.directory) as db:
            source=self._source(self._config(db))
            for identity in set(ids):
                db.execute('UPDATE watch_offers SET read_at=? WHERE source_id=? AND id=?',(self.now(),source,identity))
                db.execute('UPDATE watch_notifications SET read_at=? WHERE source_id=? AND offer_id=?',(self.now(),source,identity))
        return self.status()

    def notifications(self):
        with self._lock,_db(self.directory) as db:
            source=self._source(self._config(db));rows=db.execute('SELECT id,created,payload,read_at FROM watch_notifications WHERE source_id=? ORDER BY created DESC,id DESC LIMIT 100',(source,)).fetchall()
        return {'channel':'in-app','items':[{'notificationId':row['id'],'created':row['created'],'read':row['read_at'] is not None,**json.loads(row['payload'])} for row in rows]}

    def dispatch(self,path,data=None,query=None):
        query=query or {}
        def first(name,default=None):
            value=query.get(name,default);return value[0] if isinstance(value,list) and value else value
        if path=='listing-watch':value=self.status() if data is None else self.configure(data)
        elif path=='listing-watch/items' and data is None:value=self.list_items(first('category','new'),first('limit',50),first('before'))
        elif path=='listing-watch/check' and data is not None:value=self.request_check()
        elif path=='listing-watch/read' and data is not None:value=self.mark_read(data.get('ids'))
        elif path=='listing-watch/notifications' and data is None:value=self.notifications()
        elif path=='activity-log' and data is None:value=list_attempts(self.directory,kind=first('kind','all'),limit=first('limit',50),before=first('before'))
        else:return None
        return 200,value,'application/json; charset=utf-8',{}


if __name__=='__main__' and sys.argv[1:]==['--search-worker']:_search_worker()
