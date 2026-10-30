"""Small persistent Cian search inbox; a discovery never imports a full card.

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

API_URL = 'https://api.cian.ru/search-offers/v2/search-offers-desktop/'
DEFAULT_SEARCH_URL = 'https://www.cian.ru/cat.php?deal_type=rent&engine_version=2&offer_type=flat&region=1&type=4&sort=creation_date_desc&p=1'
MAX_BYTES = 8_000_000
MAX_ITEMS = 100
MAX_SEEN = 50_000
MAX_PAGES = 2
_ID = re.compile(r'cian:\d{1,18}')
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
    ''')
    return db


def normalize_search_url(raw):
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


def api_query(url, page=1):
    """Only mapped filters are accepted; never silently broaden a saved search."""
    params = parse_qs(urlsplit(url).query)
    allowed = {'deal_type', 'offer_type', 'engine_version', 'region', 'type', 'for_day', 'sort', 'p', 'currency', 'minprice', 'maxprice'} | {'room'+str(i) for i in (0,1,2,3,4,5,6,7,9)}
    if set(params) - allowed or urlsplit(url).path != '/cat.php':
        raise ValueError('Внутренний API не сопоставляет все фильтры этой ссылки. Выберите HTML или Chrome.')
    if any(len(values)!=1 for key,values in params.items() if key!='region'):
        raise ValueError('API-режим не принимает неоднозначные повторные фильтры')
    if params.get('offer_type', ['flat'])[0] != 'flat' or params.get('deal_type', ['rent'])[0] not in ('rent', 'sale'):
        raise ValueError('API-режим поддерживает только квартиры в аренду или на продажу')
    if params.get('for_day',['0'])[0] not in ('0','1','!1') or any(params[key][0] not in ('0','1') for key in params if key.startswith('room')):
        raise ValueError('Неизвестное значение фильтра комнат или срока аренды')
    if params.get('engine_version',['2'])[0]!='2' or params.get('type',['4'])[0] not in ('1','4'):
        raise ValueError('API-режим не сопоставляет этот тип поиска. Выберите HTML или Chrome.')
    regions = ','.join(params.get('region', ['1'])).split(',')
    if not regions or len(regions) > 10 or any(not v.isdigit() or not 0 < int(v) <= 1_000_000 for v in regions):
        raise ValueError('Неверный регион поиска')
    if params.get('currency', ['2'])[0] != '2': raise ValueError('Для API-режима выберите рубли')
    query = {'_type':'flatrent' if params.get('deal_type', ['rent'])[0]=='rent' else 'flatsale', 'engine_version':{'type':'term','value':2}, 'region':{'type':'terms','value':list(map(int, regions))}, 'sort':{'type':'term','value':'creation_date_desc'}, 'page':{'type':'term','value':page}, 'currency':{'type':'term','value':2}}
    if query['_type'] == 'flatrent': query['for_day'] = {'type':'term','value':'1' if params.get('for_day', ['0'])[0]=='1' else '!1'}
    rooms = [i for i in (0,1,2,3,4,5,6,7,9) if params.get('room'+str(i), ['0'])[0]=='1']
    if rooms: query['room'] = {'type':'terms','value':rooms}
    price = {}
    for key, target in [('minprice','gte'), ('maxprice','lte')]:
        if key in params:
            raw = params[key][0]
            if not raw.isdigit() or not 0 <= int(raw) <= 1_000_000_000: raise ValueError('Неверная цена в фильтре')
            price[target] = int(raw)
    if 'gte' in price and 'lte' in price and price['gte'] > price['lte']: raise ValueError('Неверный диапазон цены')
    if price: query['price'] = {'type':'range','value':price}
    return {'jsonQuery':query}


def _text(value, limit=400):
    return re.sub(r'\s+', ' ', str(value)).strip()[:limit] if isinstance(value, (str, int, float)) and not isinstance(value, bool) else ''


def _number(value):
    if isinstance(value, bool): return None
    try: value = float(str(value).replace(',', '.'))
    except (ValueError, TypeError): return None
    return value if math.isfinite(value) and 0 <= value <= 1_000_000_000 else None


def _publication(value):
    """Keep timezone-less/relative labels as labels, not fabricated timestamps."""
    label = _text(value, 160)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        stamp = value / 1000 if value > 100_000_000_000 else value
        if 946684800 <= stamp <= time.time()+86400:
            return datetime.fromtimestamp(stamp, timezone.utc).isoformat().replace('+00:00', 'Z'), label
    if isinstance(value, str):
        try:
            if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
                return datetime.strptime(value, '%Y-%m-%d').date().isoformat(), value
            if re.fullmatch(r'\d{4}-\d{2}-\d{2}T[0-9:.]+(?:Z|[+-]\d{2}:\d{2})', value):
                parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
                return parsed.astimezone(timezone.utc).isoformat().replace('+00:00','Z'), value
        except (ValueError, OverflowError): pass
    return None, label


def _canonical_offer(url, cian_id=None,deal_type='rent'):
    if url:
        if not isinstance(url,str):return None
        if url.startswith('/') and not url.startswith('//'):url=urljoin('https://www.cian.ru/',url)
        parsed = urlsplit(redcn.allowed_url(url, resolve=False)); match = _OFFER_PATH.fullmatch(parsed.path)
        if not match or not (parsed.hostname == 'cian.ru' or (parsed.hostname or '').endswith('.cian.ru')): return None
        if cian_id is not None and str(cian_id) != match[2]: return None
        return 'cian:'+match[2], 'https://www.cian.ru/'+match[1]+'/flat/'+match[2]+'/'
    if cian_id is not None and re.fullmatch(r'\d{1,18}', str(cian_id)):
        return 'cian:'+str(cian_id), 'https://www.cian.ru/'+deal_type+'/flat/'+str(cian_id)+'/'
    return None


def _offer(row,deal_type='rent'):
    if not isinstance(row, dict): return None
    try: identity = _canonical_offer(row.get('fullUrl') or row.get('url'), row.get('cianId'),deal_type)
    except ValueError: return None
    if not identity: return None
    bargain = row.get('bargainTerms') if isinstance(row.get('bargainTerms'), dict) else {}
    metro = ''
    geo = row.get('geo') if isinstance(row.get('geo'), dict) else {}
    underground = geo.get('undergrounds')
    if isinstance(underground, list) and underground and isinstance(underground[0], dict): metro = _text(underground[0].get('name'))
    field = next((key for key in ('datePublished','publishedAt','publicationDate','creationDate','addedTimestamp') if row.get(key) not in (None,'')), None)
    published, label = _publication(row[field]) if field else (None, '')
    price=bargain.get('priceRur')
    if price is None and bargain.get('currency') in ('rur','rub','RUB',2):price=bargain.get('price')
    price=_number(price)
    return {'id':identity[0], 'url':identity[1], 'title':_text(row.get('title') or row.get('formattedFullAddress') or row.get('seoTitle') or 'Объявление ЦИАН'), 'price':price,'currency':'RUB' if price is not None else None, 'area':_number(row.get('totalArea')), 'metro':metro, 'publishedAt':published, 'publishedLabel':label, 'publicationDateSource':field}


def _json_offers(value,deal_type='rent'):
    output = []
    def walk(item, depth=0):
        if depth > 20: return
        if isinstance(item, dict):
            for key, children in item.items():
                if key == 'offersSerialized' or key == 'offers' and any(k in item for k in ('offersCount','offerCount','aggregatedCount')):
                    if isinstance(children, list): output.extend(filter(None, (_offer(row,deal_type) for row in children[:MAX_ITEMS])))
                elif key not in ('suggestOffersSerializedList','suggestOffers','recommendations','relatedOffers') and isinstance(children, (dict,list)): walk(children, depth+1)
        elif isinstance(item, list):
            for child in item[:1000]: walk(child, depth+1)
    walk(value); return output[:MAX_ITEMS]


def _unique_items(rows):
    output={}
    for row in rows:
        if row['id'] not in output:output[row['id']]=dict(row)
        else:
            for key,value in row.items():
                if output[row['id']].get(key) in (None,'') and value not in (None,''):output[row['id']][key]=value
    return list(output.values())[:MAX_ITEMS]


class _SearchRows(list):
    def __init__(self,rows,confirmed_empty=False):
        super().__init__(rows);self.confirmed_empty=confirmed_empty


def _confirmed_empty_api(value):
    if not isinstance(value,dict) or not isinstance(value.get('data'),dict):return False
    data=value['data']
    if data.get('offersSerialized')!=[] or not any(type(data.get(key)) is int and data[key]==0 for key in ('offerCount','offersCount')):return False
    for part in (value,data):
        if any(re.search(r'error|captcha|challenge|verification',key,re.I) and field for key,field in part.items()):return False
        if part.get('success') is False or part.get('status') in ('error','challenge','captcha','failed'):return False
        if _CHALLENGE.search(str(part.get('message',''))):return False
    return True


class _SearchHTML(HTMLParser):
    def __init__(self, base):
        super().__init__(convert_charrefs=True); self.base=base; self.stack=[]; self.card=None; self.card_depth=None; self.rows=[]; self.scripts=[]; self.script=None; self.visible=[]

    def handle_starttag(self, tag, attrs):
        attrs=dict(attrs); name=attrs.get('data-name', '')+' '+attrs.get('data-testid', '')
        unrelated=any(re.search(r'recommend|suggest|similar', n, re.I) for _,n in self.stack) or bool(re.search(r'recommend|suggest|similar', name,re.I))
        if self.card is None and not unrelated and re.search(r'\b(?:CardComponent|OfferCard|offer-card)\b',name):
            self.card={'text':[], 'urls':[], 'dates':[]}; self.card_depth=len(self.stack)
        if tag=='script': self.script=[]
        if self.card is not None and not unrelated:
            if tag=='a' and 'href' in attrs: self.card['urls'].append(urljoin(self.base,attrs['href']))
            if attrs.get('itemprop')=='datePublished' and (attrs.get('datetime') or attrs.get('content')): self.card['dates'].append(attrs.get('datetime') or attrs['content'])
        if tag not in ('br','meta','link','img','input','source','hr','area','base','embed','param','track','wbr'):
            self.stack.append((tag,name))

    def handle_data(self, value):
        if self.script is not None: self.script.append(value)
        else:
            self.visible.append(value)
            if self.card is not None: self.card['text'].append(value)

    def handle_endtag(self, tag):
        if tag=='script' and self.script is not None: self.scripts.append(''.join(self.script)); self.script=None
        at=next((i for i in range(len(self.stack)-1,-1,-1) if self.stack[i][0]==tag),None)
        if at is None: return
        if self.card is not None and at <= self.card_depth:
            row=None
            for url in self.card['urls']:
                try: identity=_canonical_offer(url)
                except ValueError: continue
                if identity:
                    field={'fullUrl':identity[1], 'title':_text(' '.join(self.card['text']),240)}
                    if self.card['dates']: field['datePublished']=self.card['dates'][0]
                    row=_offer(field); break
            if row:self.rows.append(row)
            self.card=None;self.card_depth=None
        del self.stack[at:]


def parse_search(content, source_url, mime='text/html'):
    if not isinstance(content,(bytes,str)): raise ValueError('Некорректный ответ поиска')
    markup=content.decode('utf-8','replace') if isinstance(content,bytes) else content
    deal_type='sale' if parse_qs(urlsplit(source_url).query).get('deal_type')==['sale'] or urlsplit(source_url).path.startswith('/kupit-') else 'rent'
    if len(markup.encode())>MAX_BYTES: raise ValueError('Поисковый ответ превышает 8 МБ')
    confirmed_empty=False
    if 'json' in mime:
        try:
            value=json.loads(markup);rows=_json_offers(value,deal_type);confirmed_empty=_confirmed_empty_api(value)
        except ValueError: raise ValueError('Внутренний API вернул некорректный JSON') from None
    else:
        parser=_SearchHTML(source_url);parser.feed(markup)
        if _CHALLENGE.search(' '.join(parser.visible)[:12000]): raise NeedsAttention('ЦИАН требует ручной проверки браузера. Автоматический обход не выполняется.')
        rows=[]
        decoder=json.JSONDecoder()
        for script in parser.scripts:
            try: rows.extend(_json_offers(json.loads(script),deal_type))
            except ValueError:
                # Parse JSON object literals in the known config assignment;
                # JavaScript is never evaluated by the HTTP adapter.
                if '_cianConfig' not in script: continue
                for marker in re.finditer(r'(?:\bpush\s*\(\s*|=\s*)([\[{])',script):
                    try: value,_=decoder.raw_decode(script[marker.start(1):])
                    except ValueError: continue
                    rows.extend(_json_offers(value,deal_type))
        if not rows: rows=parser.rows
    return _SearchRows(_unique_items(rows),confirmed_empty=confirmed_empty and not rows)


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


def _http_page(url, data=None):
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
        with opener.open(urllib.request.Request(url,data=payload,headers=headers),timeout=12) as response:
            # read1 returns available data rather than filling an entire buffer;
            # an indefinitely trickling body cannot reset a per-read timeout.
            end=time.monotonic()+12;parts=[];size=0
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
    """One saved search, at most two pages and never individual ad visits."""
    url=config['searchUrl'];rows=[];pages=0;adapter=config['adapter'];confirmed_empty=False
    if adapter=='chrome':
        from browser_import import _browser_lock
        try:from playwright.sync_api import sync_playwright
        except ImportError:raise ValueError('Для мониторинга Chrome установите зависимости cli/requirements-browser.txt') from None
        if not _browser_lock.acquire(False):raise ValueError('Chrome занят ручным импортом. Мониторинг повторит поиск позднее.')
        try:
            redcn.allowed_url(url,resolve=False);_addresses(urlsplit(url).hostname)
            profile=Path(directory)/(browser_channel+'-profile');profile.mkdir(parents=True,exist_ok=True,mode=0o700)
            deadline=time.monotonic()+35
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
                        try:
                            response=request.response()
                            if response is None:return
                            found=browser_response_items(response,current_page[0],url)
                            if found or getattr(found,'confirmed_empty',False):api_results[current_page[0]]=found
                        except Exception:pass
                    # Inspect only completed responses so reading JSON cannot
                    # turn a pending API request into an unbounded body wait.
                    page.on('requestfinished',response_seen)
                    for index in range(1,MAX_PAGES+1):
                        if cancel.is_set():raise ValueError('Мониторинг остановлен')
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
                            if not found and not getattr(found,'confirmed_empty',False):found=list(filter(None,(_offer(item) for item in snapshot['links'])))
                            if found or getattr(found,'confirmed_empty',False):break
                            page.wait_for_timeout(200)
                        pages+=1
                        confirmed_empty=confirmed_empty or getattr(found,'confirmed_empty',False)
                        if not found:break
                        rows.extend(found)
                        if time.monotonic()+2>=deadline:break
                finally:context.close()
        finally:_browser_lock.release()
    else:
        for index in range(1,MAX_PAGES+1):
            if cancel.is_set():raise ValueError('Мониторинг остановлен')
            content,mime=_http_page(API_URL,api_query(url,index)) if adapter=='api' else _http_page(_page_url(url,index))
            found=parse_search(content,url,mime);pages+=1
            confirmed_empty=confirmed_empty or getattr(found,'confirmed_empty',False)
            if not found:break
            rows.extend(found)
    return {'items':_unique_items(rows), 'pages':pages, 'coverage':'first-2-pages', 'adapter':adapter,'confirmedEmpty':confirmed_empty and not rows}


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
        if len(output)>1_000_000:raise ValueError('Поиск вернул слишком большой служебный ответ')
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
        value=json.loads(sys.stdin.read(20_001));config=value['config']
        config['searchUrl']=normalize_search_url(config['searchUrl'])
        if config.get('adapter') not in ('html','api','chrome'):raise ValueError('Неизвестный источник мониторинга')
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

    def _config(self,db):return json.loads(db.execute('SELECT payload FROM watch_config WHERE id=1').fetchone()[0])

    @staticmethod
    def _source(config):return hashlib.sha256(config['searchUrl'].encode()).hexdigest()[:24] if config['searchUrl'] else ''

    def configure(self,data):
        if not isinstance(data,dict) or set(data)-{'enabled','searchUrl','intervalMinutes','adapter'}:raise ValueError('Неверные параметры мониторинга')
        with self._lock,_db(self.directory) as db:
            config=self._config(db);config.update(data)
            if type(config['enabled']) is not bool:raise ValueError('Неверный флаг включения')
            if type(config['intervalMinutes']) is not int or not 60<=config['intervalMinutes']<=1440:raise ValueError('Интервал: от 60 до 1440 минут')
            if config['adapter'] not in ('html','api','chrome'):raise ValueError('Неизвестный источник мониторинга')
            config['searchUrl']=normalize_search_url(config['searchUrl']) if config['searchUrl'] else ''
            if config['enabled'] and not config['searchUrl']:raise ValueError('Сначала сохраните ссылку поиска ЦИАН')
            if config['adapter']=='api' and config['searchUrl']:api_query(config['searchUrl'])
            source=self._source(config)
            db.execute('UPDATE watch_config SET payload=? WHERE id=1',(json.dumps(config),))
            if source:
                db.execute('INSERT OR IGNORE INTO watch_sources(id,next_run) VALUES(?,?)',(source,self.now()))
                if config['enabled']:db.execute('UPDATE watch_sources SET next_run=COALESCE(next_run,?) WHERE id=?',(self.now(),source))
        self._wake.set();return self.status()

    def status(self):
        with self._lock,_db(self.directory) as db:
            config=self._config(db);source=self._source(config);row=db.execute('SELECT * FROM watch_sources WHERE id=?',(source,)).fetchone();info=dict(row) if row else {}
            counts=db.execute('SELECT COUNT(*),SUM(is_new),SUM(CASE WHEN is_new=1 AND read_at IS NULL THEN 1 ELSE 0 END) FROM watch_offers WHERE source_id=?',(source,)).fetchone()
            state='checking' if self._checking or self._force else 'disabled' if not config['enabled'] else 'needs_attention' if info.get('attention') else 'error' if info.get('error') else 'idle'
            return {'config':config,'state':state,'baselineComplete':info.get('baseline_at') is not None,'baselineAt':info.get('baseline_at'),'lastRun':info.get('last_run'),'lastSuccess':info.get('last_success'),'nextRun':info.get('next_run') if config['enabled'] else None,'error':info.get('error',''),'coverage':'first-2-pages','coverageNote':'Проверяются только первые две страницы по дате. Полный охват всех объявлений не гарантируется.','unreadCount':counts[2] or 0,'newCount':counts[1] or 0,'totalSeen':counts[0],'lastSeenCount':info.get('seen_count',0),'runCount':info.get('run_count',0),'consecutiveFailures':info.get('failures',0)}

    def request_check(self):
        with self._lock:
            if not self.status()['config']['searchUrl']:raise ValueError('Сначала сохраните поисковую ссылку ЦИАН')
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
            try:
                result=self.fetch(dict(config),self._stop)
                if self._stop.is_set():return self.status()
                if not isinstance(result,dict) or not isinstance(result.get('items'),list):raise ValueError('Источник вернул некорректный список')
                items=[];seen=set()
                for item in result['items'][:MAX_ITEMS]:
                    if not isinstance(item,dict):continue
                    identity=_canonical_offer(item.get('url'))
                    if not identity or item.get('id')!=identity[0] or identity[0] in seen:continue
                    seen.add(identity[0]);items.append({key:item.get(key) for key in ('id','url','title','price','currency','area','metro','publishedAt','publishedLabel','publicationDateSource')})
                if not items and result.get('confirmedEmpty') is not True:raise NeedsAttention('Поиск не дал распознанных объявлений. Базовый снимок не создан; проверьте фильтры, разметку или доступ в Chrome.')
                stamp=self.now();new_count=0
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
                            db.execute('INSERT INTO watch_offers VALUES(?,?,?,?,?,?,NULL)',(source,item['id'],json.dumps(item,ensure_ascii=False),stamp,stamp,int(is_new)));new_count+=int(is_new)
                        else:db.execute('UPDATE watch_offers SET payload=?,last_seen=? WHERE source_id=? AND id=?',(json.dumps(item,ensure_ascii=False),stamp,source,item['id']))
                    if self._stop.is_set():raise ValueError('Мониторинг остановлен')
                    db.execute('UPDATE watch_sources SET baseline_at=COALESCE(baseline_at,?),last_success=?,next_run=?,failures=0,error=\'\',attention=0,seen_count=?,new_count=?,run_count=run_count+1 WHERE id=?',(stamp,stamp,stamp+config['intervalMinutes']*60,len(items),new_count,source))
            except Exception as error:
                if not self._stop.is_set():
                    with self._lock,_db(self.directory) as db:
                        failures=info.get('failures',0)+1;delay=min(24*3600,config['intervalMinutes']*60*2**min(failures,4))
                        message=str(error)[:300] if isinstance(error,(ValueError,TimeoutError)) else 'Не удалось проверить источник: '+type(error).__name__
                        db.execute('UPDATE watch_sources SET failures=?,error=?,attention=?,next_run=?,run_count=run_count+1 WHERE id=?',(failures,message,int(isinstance(error,NeedsAttention)),self.now()+delay,source))
            finally:
                with self._lock:self._checking=False
        finally:
            with self._lock:self._owner=None
            self._run_lock.release()
        return self.status()

    def list_items(self,category='new',limit=50,before=None):
        if category not in ('new','all') or isinstance(limit,bool):raise ValueError('Неверная категория списка')
        try:limit=int(limit)
        except (ValueError,TypeError):raise ValueError('Неверный размер страницы') from None
        if not 1<=limit<=100:raise ValueError('Размер страницы: от 1 до 100')
        args=[];where='source_id=?'
        with self._lock,_db(self.directory) as db:
            args.append(self._source(self._config(db)))
            if category=='new':where+=' AND is_new=1'
            if before:
                match=re.fullmatch(r'(\d+(?:\.\d+)?):cian:(\d{1,18})',str(before))
                if not match:raise ValueError('Неверный курсор списка')
                stamp=float(match[1]);identity='cian:'+match[2];where+=' AND (first_seen<? OR (first_seen=? AND id<?))';args.extend([stamp,stamp,identity])
            rows=db.execute('SELECT * FROM watch_offers WHERE '+where+' ORDER BY first_seen DESC,id DESC LIMIT ?',(*args,limit+1)).fetchall();more=len(rows)>limit;rows=rows[:limit]
            items=[{**json.loads(row['payload']),'firstSeen':row['first_seen'],'lastSeen':row['last_seen'],'read':row['read_at'] is not None,'isNew':bool(row['is_new'])} for row in rows]
        cursor=repr(rows[-1]['first_seen'])+':'+rows[-1]['id'] if more and rows else None
        return {'items':items,'hasMore':more,'nextBefore':cursor,'category':category}

    def list_new(self,limit=50,before=None):return self.list_items('new',limit,before)

    def mark_read(self,ids):
        if not isinstance(ids,list) or len(ids)>100 or any(not isinstance(value,str) or not _ID.fullmatch(value) for value in ids):raise ValueError('Неверные ID объявлений')
        with self._lock,_db(self.directory) as db:
            source=self._source(self._config(db))
            for identity in set(ids):db.execute('UPDATE watch_offers SET read_at=? WHERE source_id=? AND id=?',(self.now(),source,identity))
        return self.status()

    def dispatch(self,path,data=None,query=None):
        query=query or {}
        def first(name,default=None):
            value=query.get(name,default);return value[0] if isinstance(value,list) and value else value
        if path=='listing-watch':value=self.status() if data is None else self.configure(data)
        elif path=='listing-watch/items' and data is None:value=self.list_items(first('category','new'),first('limit',50),first('before'))
        elif path=='listing-watch/check' and data is not None:value=self.request_check()
        elif path=='listing-watch/read' and data is not None:value=self.mark_read(data.get('ids'))
        else:return None
        return 200,value,'application/json; charset=utf-8',{}


if __name__=='__main__' and sys.argv[1:]==['--search-worker']:_search_worker()
