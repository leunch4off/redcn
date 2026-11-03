"""Bounded public Avito search-card HTML, not private/account API endpoints.

The selectors are an explicit fixture-tested contract. Unknown/challenge pages
are never a successful empty baseline; no cookies, page scripts or photos run.
"""
from datetime import datetime, timezone
from html.parser import HTMLParser
import math
import re
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit


MAX_BYTES=8_000_000
MAX_ITEMS=800
_PATH=re.compile(r'^/[^/?#]+/(?:kvartiry|komnaty)/[^/?#]*_(\d{1,20})/?$')
_CHALLENGE=re.compile(r'доступ ограничен|проверка браузера|проверка безопасности|подтвердите.{0,20}что вы|вы не робот|captcha|verify you are human',re.I)


class SearchRows(list):
    def __init__(self,rows,confirmed_empty=False):super().__init__(rows);self.confirmed_empty=confirmed_empty


def normalize_search_url(raw):
    if not isinstance(raw,str) or len(raw)>4096:raise ValueError('Нужна HTTPS-ссылка поиска Авито до 4096 символов')
    match=re.search(r'https://[^\s<>\[\]"\u201c\u201d]+',raw.strip())
    if not match:raise ValueError('Вставьте HTTPS-ссылку поиска Авито')
    parsed=urlsplit(match[0].rstrip(')\\.,:;').replace('&amp;','&'))
    if parsed.scheme!='https' or parsed.hostname not in ('avito.ru','www.avito.ru') or parsed.username or parsed.password or parsed.port not in (None,443):raise ValueError('Разрешён только обычный HTTPS-поиск Авито')
    if not re.fullmatch(r'/[^/?#]+/(?:kvartiry|komnaty)/?',parsed.path):raise ValueError('Вставьте поиск квартир/комнат Авито, а не объявление или личный кабинет')
    values=[(key,value) for key,value in parse_qsl(parsed.query,keep_blank_values=True) if key not in ('p','page','s','sort') and not key.lower().startswith('utm_')]
    if len(values)>100:raise ValueError('Слишком много фильтров поиска')
    values.extend((('s','104'),('p','1')))
    return urlunsplit(('https',parsed.netloc.lower(),parsed.path,urlencode(sorted(values)),''))


def canonical_offer(raw):
    if not isinstance(raw,str):return None
    parsed=urlsplit(urljoin('https://www.avito.ru/',raw))
    if parsed.scheme!='https' or parsed.hostname not in ('avito.ru','www.avito.ru') or parsed.username or parsed.password or parsed.port not in (None,443):return None
    match=_PATH.fullmatch(parsed.path)
    if not match:return None
    return 'avito:'+match[1],'https://www.avito.ru'+parsed.path.rstrip('/')


def publication(raw):
    if not isinstance(raw,str):return None,''
    label=re.sub(r'\s+',' ',raw).strip()[:160]
    try:
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}',label):return datetime.strptime(label,'%Y-%m-%d').date().isoformat(),label
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}T[0-9:.]+(?:Z|[+-]\d{2}:\d{2})',label):return datetime.fromisoformat(label.replace('Z','+00:00')).astimezone(timezone.utc).isoformat().replace('+00:00','Z'),label
    except ValueError:pass
    return None,label


def number(raw):
    try:value=float(str(raw).replace('\xa0','').replace('\u202f','').replace(' ','').replace(',','.'))
    except (ValueError,TypeError):return None
    return value if math.isfinite(value) and 0<=value<=1_000_000_000 else None


class SearchHTML(HTMLParser):
    def __init__(self,base):super().__init__(convert_charrefs=True);self.base=base;self.stack=[];self.card=None;self.depth=None;self.rows=[];self.visible=[];self.suppressed=0;self.empty=False
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs);marker=attrs.get('data-marker','');excluded=any(row[2] for row in self.stack) or bool(re.search(r'recommend|similar|suggest|vip-items',marker,re.I));suppressed=tag in ('script','style','noscript') or any(row[3] for row in self.stack)
        if marker in ('search/no-results','catalog-serp/no-results'):self.empty=True
        if self.card is None and not excluded and (marker=='item' or tag=='article' and attrs.get('data-item-id')):
            self.card={'parts':[],'url':None,'title':[],'price':None,'priceText':[],'address':[],'district':[],'dates':[]};self.depth=len(self.stack)
        if self.card is not None and not excluded and not suppressed:
            if tag=='a' and attrs.get('href') and canonical_offer(urljoin(self.base,attrs['href'])):self.card['url']=self.card['url'] or urljoin(self.base,attrs['href'])
            if attrs.get('itemprop')=='price' and attrs.get('content'):self.card['price']=number(attrs['content'])
            if attrs.get('itemprop') in ('datePublished','datePosted') or marker in ('item-date','item-date/text'):
                value=attrs.get('datetime') or attrs.get('content') or attrs.get('data-date')
                if value:self.card['dates'].append(value)
        if tag not in ('br','meta','link','img','input','source','hr','area','base','embed','param','track','wbr'):self.stack.append((tag,marker,excluded,suppressed,attrs))
    def handle_data(self,text):
        if any(row[3] for row in self.stack):return
        self.visible.append(text)
        if self.card is None or any(row[2] for row in self.stack):return
        self.card['parts'].append(text)
        markers=[row[1] for row in self.stack]
        for marker,target in [('item-title','title'),('item-price','priceText'),('item-address','address'),('item-address/district','district'),('item-date','dates'),('item-date/text','dates')]:
            if marker in markers:self.card[target].append(text)
    def handle_endtag(self,tag):
        index=next((i for i in range(len(self.stack)-1,-1,-1) if self.stack[i][0]==tag),None)
        if index is None:return
        if self.card is not None and index<=self.depth:
            row=self.finish(self.card)
            if row:self.rows.append(row)
            self.card=None;self.depth=None
        del self.stack[index:]
    def finish(self,card):
        identity=canonical_offer(card['url'])
        if not identity:return None
        text=lambda parts:re.sub(r'\s+',' ',' '.join(parts)).strip()
        title=text(card['title']) or text(card['parts'])[:240];price_text=text(card['priceText']);content=text(card['parts'])
        price=card['price']
        if price is None:
            amount=re.search(r'(\d[\d\s\xa0\u202f]*(?:[.,]\d+)?)\s*(?:₽|руб)',price_text)
            if amount:price=number(amount[1])
        currency='RUB' if '₽' in price_text or re.search(r'руб',price_text,re.I) else None
        period='day' if re.search(r'сут|посуточ|/\s*день',price_text,re.I) else 'month' if re.search(r'месяц|мес',price_text,re.I) else 'unknown'
        area_match=re.search(r'(\d+(?:[.,]\d+)?)\s*м[²2]',title);room_match=re.search(r'(\d{1,2})\s*-?\s*(?:к\.|комнат)',title,re.I)
        rooms=number(room_match[1]) if room_match else 0 if re.search(r'студи',title,re.I) else None
        date=next((publication(raw) for raw in card['dates'] if publication(raw)[0]),publication(text(card['dates'])))
        inactive=bool(re.search(r'объявление (?:снято|закрыто|удалено)|срок публикации ист[её]к',content,re.I))
        return {'id':identity[0],'url':identity[1],'platform':'avito','title':title[:400],'price':price,'currency':currency,'pricePeriod':period,'area':number(area_match[1]) if area_match else None,'rooms':rooms,'address':text(card['address'])[:400],'district':text(card['district'])[:150],'metro':'','publishedAt':date[0],'publishedLabel':date[1],'publicationDateSource':'Avito item-date' if card['dates'] else None,'publicationStatus':'removed' if inactive else 'active'}


def parse_search(content,source_url,mime='text/html'):
    if 'html' not in mime:raise ValueError('Для Авито поддержан публичный HTML-поиск; частный API не используется')
    if not isinstance(content,(str,bytes)):raise ValueError('Некорректный ответ поиска Авито')
    markup=content.decode('utf8','replace') if isinstance(content,bytes) else content
    if len(markup.encode())>MAX_BYTES:raise ValueError('Поисковый ответ превышает 8 МБ')
    parser=SearchHTML(source_url);parser.feed(markup)
    if _CHALLENGE.search(' '.join(parser.visible)[:12000]):raise ValueError('SEARCH_NEEDS_ATTENTION: Авито требует ручной проверки; обход не выполняется')
    rows={}
    for row in parser.rows[:MAX_ITEMS]:rows.setdefault(row['id'],row)
    return SearchRows(list(rows.values()),confirmed_empty=parser.empty and not rows)
