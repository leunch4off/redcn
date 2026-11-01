"""Cian saved-search parsing contract, kept separate from Avito selectors.

Known JSON fields and inert public HTML only. No scripts are evaluated and
recommendation/account responses are excluded. Challenge pages need attention.
"""
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
import math
import re
import time
from urllib.parse import parse_qs, urljoin, urlsplit

import redcn

MAX_BYTES=8_000_000
MAX_ITEMS=800
_OFFER_PATH=re.compile(r'^/(rent|sale)/flat/(\d{1,18})/?$')
_CHALLENGE=re.compile(r'verify you are human|доступ ограничен|проверка браузера|подтвердите.{0,20}что вы|вы не робот|checking your browser|пройдите проверку',re.I)

class NeedsAttention(ValueError):
    pass

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
    addresses=geo.get('address') if isinstance(geo.get('address'),list) else []
    district=next((_text(part.get('fullName') or part.get('name'),150) for part in addresses if isinstance(part,dict) and part.get('type')=='raion'),'')
    period=str(bargain.get('paymentPeriod') or '').lower();period='month' if period in ('monthly','month','p1m') else 'day' if period in ('day','daily','p1d') else 'unknown'
    status=str(row.get('status') or 'active').lower();status=status if status in ('active','removed','archived','inactive') else 'unknown'
    return {'id':identity[0], 'url':identity[1], 'platform':'cian','title':_text(row.get('title') or row.get('formattedFullAddress') or row.get('seoTitle') or 'Объявление ЦИАН'), 'price':price,'currency':'RUB' if price is not None else None,'pricePeriod':period, 'area':_number(row.get('totalArea')),'rooms':_number(row.get('roomsCount')),'district':district,'address':_text(row.get('formattedFullAddress')), 'metro':metro, 'publishedAt':published, 'publishedLabel':label, 'publicationDateSource':field,'publicationStatus':status}


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
                    text=' '.join(self.card['text']);price=re.search(r'(\d[\d\s\xa0\u202f]*(?:[.,]\d+)?)\s*(?:₽|руб)',text,re.I);area=re.search(r'(\d+(?:[.,]\d+)?)\s*м[²2]',text);rooms=re.search(r'(\d{1,2})\s*-?\s*(?:к\.|комнат)',text,re.I)
                    if price:field['bargainTerms']={'priceRur':_number(re.sub(r'\s+','',price[1])),'paymentPeriod':'monthly' if re.search(r'месяц|мес',text,re.I) else 'daily' if re.search(r'сут|посуточ',text,re.I) else ''}
                    if area:field['totalArea']=area[1]
                    if rooms:field['roomsCount']=rooms[1]
                    elif re.search(r'студи',text,re.I):field['roomsCount']=0
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
