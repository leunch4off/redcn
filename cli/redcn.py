#!/usr/bin/env python3
"""Public listing import, local cache and shared JS generation. Python 3.10+, Node 22+."""
import argparse
import hashlib
import html
from html.parser import HTMLParser
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import socket
import ssl
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
LISTING_HOSTS = ('cian.ru', 'avito.ru')
IMAGE_HOSTS = LISTING_HOSTS + ('cdn-cian.ru', 'ciancdn.ru', 'avito.st')
MAX_HTML = 8_000_000
MAX_IMAGE = 12_000_000


class ChallengeError(ValueError):
    pass


def normalize_url(raw):
    """Accept a pasted Markdown link or terminal line, without weakening host checks."""
    # Raw query parameters such as &region must not be decoded as the legacy
    # HTML entity &reg (®). Decode only complete escaped ampersands from paste.
    raw = re.sub(r'&(?:amp|#0*38|#x0*26);', '&', str(raw).strip(), flags=re.I)
    markdown = re.search(r'\[[^\]]*\]\((https://[^\s)]+)\)', raw)
    match = markdown or re.search(r'https://[^\s<>\[\]"\u201c\u201d]+', raw)
    if not match:
        raise ValueError('Вставьте HTTPS-ссылку ЦИАН или Авито')
    url = match.group(1) if markdown else match.group(0)
    url = re.split(r'Ошибка|Error:|<urlopen', url, maxsplit=1)[0].rstrip(')\\.,:;')
    return allowed_url(url, resolve=False)


def allowed_url(url, images=False, resolve=True):
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or '').lower()
    allowed = IMAGE_HOSTS if images else LISTING_HOSTS
    if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError('Разрешены только HTTPS-ссылки без учётных данных и нестандартных портов')
    if not any(host == h or host.endswith('.' + h) for h in allowed):
        raise ValueError('Неподдерживаемый домен: ' + host)
    if resolve:
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
            raise ValueError('Непубличный сетевой адрес запрещён')
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ''))


class CheckedRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, images=False):
        self.images = images

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        allowed_url(newurl, images=self.images)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_bytes(url, images=False):
    url = allowed_url(url, images=images)
    request = urllib.request.Request(url, headers={'User-Agent': 'redcn/3.0 (single listing import)', 'Accept': 'image/*' if images else 'text/html'})
    # Python.org macOS installs may have no root bundle. Keep TLS verification;
    # use the maintained certifi CA bundle when installed with media dependencies.
    try:
        import certifi
        context=ssl.create_default_context(cafile=certifi.where())
    except ImportError: context=ssl.create_default_context()
    opener = urllib.request.build_opener(CheckedRedirect(images),urllib.request.HTTPSHandler(context=context))
    for attempt in range(2):
        try:
            with opener.open(request, timeout=15) as response:
                content_type = response.headers.get_content_type()
                if images and content_type not in ('image/jpeg', 'image/png', 'image/webp'):
                    raise ValueError('Неподдерживаемый формат фото: ' + content_type)
                if not images and content_type not in ('text/html', 'application/xhtml+xml'):
                    raise ValueError('Источник вернул не HTML')
                limit = MAX_IMAGE if images else MAX_HTML
                content = response.read(limit + 1)
                if len(content) > limit:
                    raise ValueError('Файл превышает ограничение размера')
                return content, content_type
        except (TimeoutError,urllib.error.URLError) as error:
            if attempt == 0 and (isinstance(error,TimeoutError) or isinstance(getattr(error,'reason',None),TimeoutError)):
                continue
            raise



class ListingHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.images, self.ld = [], [], []
        self.script, self.style, self.ld_mode = 0, 0, False
        self.ld_buffer = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'script':
            self.script += 1
            self.ld_mode = attrs.get('type', '').lower() == 'application/ld+json'
            self.ld_buffer = []
        if tag == 'style':
            self.style += 1
        if tag in ('p', 'div', 'br', 'li', 'h1', 'h2'):
            self.parts.append('\n')
        if tag == 'meta':
            name = attrs.get('property', attrs.get('name', '')).lower()
            if name in ('og:image', 'twitter:image'):
                self.images.append(attrs.get('content', ''))
            if name in ('description', 'og:description', 'og:title'):
                self.parts.append(attrs.get('content', '') + '\n')

    def handle_endtag(self, tag):
        if tag == 'script':
            if self.ld_mode:
                try:
                    self.ld.append(json.loads(''.join(self.ld_buffer)))
                except (ValueError, TypeError):
                    pass
            self.script = max(0, self.script - 1)
            self.ld_mode = False
        if tag == 'style':
            self.style = max(0, self.style - 1)

    def handle_data(self, data):
        if self.script:
            if self.ld_mode:
                self.ld_buffer.append(data)
        elif not self.style:
            self.parts.append(data)


def parse_html(markup, base_url):
    if len(markup.encode('utf-8')) > MAX_HTML: raise ValueError('HTML превышает 8 МБ')
    if re.search(r'data-name=["\x27](?:Description|OfferTitleNew|OfferSummaryInfoItem)|_cianConfig\[["\x27]frontend-offer-card', markup):
        from node_runtime import resolve_node, NodeRuntimeError
        try: node = resolve_node()
        except NodeRuntimeError: node = None
        if node:
            parsed = subprocess.run([node,str(ROOT/'cli/analyze-source.mjs')],input=markup,text=True,encoding='utf-8',capture_output=True,timeout=30)
            if parsed.returncode: raise ValueError('Ошибка разбора HTML: '+parsed.stderr[:300])
            result = json.loads(parsed.stdout)
            if result.get('text'):
                result['url'] = result.get('url') or base_url
                return result
    parser = ListingHTML()
    parser.feed(markup)
    # A script named captcha.js is not evidence of a visible challenge.
    if re.search(r'verify you are human|проверка браузера|доступ ограничен|checking your browser|подтвердите[, ]+что вы|пройдите проверку|вы не робот|проверяем.{0,30}браузер', ''.join(parser.parts)[:12000], re.I):
        raise ChallengeError('Источник запросил проверку браузера. Пройдите её в окне Chrome; автоматически защита не обходится.')
    descriptions = []
    def walk(value):
        if isinstance(value, list):
            for child in value:
                walk(child)
        elif isinstance(value, dict):
            if isinstance(value.get('description'), str):
                descriptions.append(value['description'])
            image = value.get('image', [])
            if isinstance(image, (str, dict)):
                image = [image]
            if isinstance(image, list):
                for item in image:
                    url = item if isinstance(item, str) else item.get('url', item.get('contentUrl', '')) if isinstance(item, dict) else ''
                    if url:
                        parser.images.append(url)
            for child in value.values():
                if isinstance(child, (list, dict)):
                    walk(child)
    for item in parser.ld:
        walk(item)
    # Keep description first, without treating unrelated recommendation widgets as apartment facts.
    text = '\n'.join(dict.fromkeys(descriptions)) if descriptions else ''.join(parser.parts)
    text = re.sub(r'[ \t\r]+', ' ', html.unescape(text)).strip()[:40000]
    if len(text) < 20:
        raise ValueError('На странице не найдено описание. Используйте --input с текстом.')
    images = []
    for candidate in parser.images:
        try:
            url = allowed_url(urllib.parse.urljoin(base_url, candidate), images=True, resolve=False)
            if url not in images:
                images.append(url)
        except ValueError:
            continue
    return {'url': base_url, 'text': text, 'images': images[:30]}


def open_cache(directory):
    directory.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(directory / 'listings.sqlite3')
    connection.execute('CREATE TABLE IF NOT EXISTS cache (url TEXT PRIMARY KEY, fetched REAL NOT NULL, payload TEXT NOT NULL)')
    connection.execute('CREATE TABLE IF NOT EXISTS seen (url TEXT PRIMARY KEY, processed REAL NOT NULL)')
    return connection


def load_listing(url, connection, refresh=False, loader='auto', browser_timeout=180, status=None, cancel=None, browser_channel='chrome', browser_scroll='sections'):
    from job_progress import emit
    if browser_scroll not in ('sections', 'page'):
        raise ValueError('Режим прокрутки Chrome: sections или page')
    # Resolve only on a network miss; cached pages remain available offline.
    url = normalize_url(url)
    row = connection.execute('SELECT fetched,payload FROM cache WHERE url=?', (url,)).fetchone()
    if row and not refresh and time.time() - row[0] < 86400:
        emit(status,'Берём уже прочитанное объявление из кеша Mac; Chrome не требуется.',stage='extract')
        return {**json.loads(row[1]), 'cached': True}
    result = None
    if loader != 'chrome':
        try:
            emit(status,'Загружаем исходную страницу объявления по HTTP…',stage='navigate',maxTotalSeconds=30)
            content, _ = fetch_bytes(url)
            emit(status,'Страница получена. Читаем описание, цену, площадь и фотографии…',stage='extract')
            result = parse_html(content.decode('utf-8', errors='replace'), url)
            if not re.search(r'\d[\d\s\xa0,.]*\s*(?:м²|м2|кв\.?\s*м|㎡)', result['text'], re.I) or not re.search(r'\d[\d\s\xa0]*\s*(?:₽|руб)', result['text'], re.I):
                result = None
                raise ValueError('HTTP-страница не содержит площади и цены; возможно, нужен JavaScript. Используйте Chrome или --input.')
        except (OSError, ValueError) as error:
            if loader == 'http': raise
            emit(status,'HTTP не загрузил карточку. Открываем Chrome: '+str(error)[:180],stage='browser')
    if result is None:
        from browser_import import load_browser
        result = load_browser(url, Path(connection.execute('PRAGMA database_list').fetchone()[2]).parent / (browser_channel+'-profile'), parse_html, allowed_url, browser_timeout, status, cancel, browser_channel, scroll_strategy=browser_scroll)
    connection.execute('INSERT OR REPLACE INTO cache VALUES (?,?,?)', (url, time.time(), json.dumps(result, ensure_ascii=False)))
    connection.commit()
    return {**result, 'cached': False}


def next_variant(connection, key):
    connection.execute('CREATE TABLE IF NOT EXISTS variants (key TEXT PRIMARY KEY, value INTEGER NOT NULL)')
    row = connection.execute('SELECT value FROM variants WHERE key=?', (key,)).fetchone()
    value = row[0] + 1 if row else 0
    connection.execute('INSERT OR REPLACE INTO variants VALUES (?,?)', (key, value));connection.commit()
    return value


def eligible(seller, commission):
    if seller == 'owner':
        return True
    try:
        return seller == 'agent' and commission is not None and 0 <= float(commission) <= 50
    except (TypeError, ValueError):
        return False


def generate(text, data=None, rules=None, prefs=None):
    from node_runtime import resolve_node
    node = resolve_node()
    try:
        result = subprocess.run([node, str(ROOT / 'cli/generate.mjs')], input=json.dumps({'text': text, 'data': data or {}, 'rules': rules or {}, 'prefs': prefs or {}}, ensure_ascii=False), text=True, encoding='utf-8', capture_output=True, check=False, timeout=30)
    except subprocess.TimeoutExpired:
        raise ValueError('Генератор не ответил за 30 секунд. Повторите подготовку объявления.') from None
    except OSError:
        raise ValueError('Не удалось запустить найденный Node.js. Проверьте его установку и перезапустите сервер.') from None
    if result.returncode:
        raise ValueError(result.stderr.strip() or 'Ошибка генерации')
    try: return json.loads(result.stdout)
    except ValueError:
        raise ValueError('Генератор вернул некорректный ответ. Повторите подготовку объявления.') from None


def download_photos(urls, directory, crop=False, crop_bottom=None, cache_dir=None):
    directory.mkdir(parents=True, exist_ok=True)
    results = []
    for index, url in enumerate(urls[:30], 1):
        try:
            content, kind = fetch_bytes(url, images=True)
            valid = (kind == 'image/jpeg' and content.startswith(b'\xff\xd8\xff')) or (kind == 'image/png' and content.startswith(b'\x89PNG\r\n\x1a\n')) or (kind == 'image/webp' and content.startswith(b'RIFF') and content[8:12] == b'WEBP')
            if not valid:
                raise ValueError('Сигнатура файла не соответствует формату')
            extension = {'image/jpeg': 'jpg', 'image/png': 'png', 'image/webp': 'webp'}[kind]
            digest = hashlib.sha256(content).hexdigest()
            path = directory / f'{index:02d}-{digest[:12]}.{extension}'
            if not path.exists():
                path.write_bytes(content)
            row={'url': url, 'path': str(path), 'sha256': digest}
            if crop or crop_bottom is not None:
                import base64
                import photo_store
                media_dir=cache_dir or directory/'.redcn-media'
                original=photo_store.attach_photo(media_dir,url,'data:'+kind+';base64,'+base64.b64encode(content).decode())
                edited=photo_store.crop_photo(media_dir,original['id'],{'bottom':crop_bottom} if crop_bottom is not None else None)
                row['crop']=edited
                if edited.get('kind')=='crop':
                    copy_path=directory/f'{index:02d}-crop-{edited["id"][:12]}.jpg';copy_path.write_bytes(photo_store.asset(media_dir,edited['id'])[0]);row['crop_path']=str(copy_path)
            results.append(row)
        except (OSError, ValueError) as error:
            results.append({'url': url, 'error': str(error)})
    (directory / 'manifest.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description='redcn: ссылка / текст → китайские заголовки, описание и теги')
    parser.add_argument('url', nargs='?')
    parser.add_argument('--input', type=Path, help='Текст объявления UTF-8')
    parser.add_argument('--html', type=Path, help='Сохранённая HTML-страница ЦИАН / Авито, до 8 МБ')
    parser.add_argument('--data', type=Path, help='Проверенные поля карточки JSON')
    parser.add_argument('--rules', type=Path, help='Словарь RU → CN JSON')
    parser.add_argument('--prefs', type=Path, help='Преимущества и теги JSON')
    parser.add_argument('--variant', type=int, help='Фиксированный вариант; без флага подача меняется при каждом запуске')
    parser.add_argument('--loader', choices=['auto', 'http', 'chrome'], default='auto', help='auto: HTTP, затем видимый Chrome при ошибке')
    parser.add_argument('--browser', action='store_true', help='Сразу открыть видимый Google Chrome')
    parser.add_argument('--browser-channel', choices=['chrome','chromium'], default='chrome', help='Google Chrome или совместимый браузер Playwright Chromium')
    parser.add_argument('--browser-timeout', type=int, default=180, help='Ожидание страницы / ручной проверки, 15–600 секунд')
    parser.add_argument('--browser-scroll', choices=['sections','page'], default='sections', help='sections: сразу фото, описание и характеристики; page: последовательная прокрутка всей страницы')
    parser.add_argument('--serve', action='store_true', help='Запустить локальный сайт и открыть его в браузере')
    parser.add_argument('--open', action='store_true', help='После генерации открыть сохранённую карточку на локальном сайте')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--phone',action='store_true',help='Разрешить подключение телефона в локальной сети по одноразовой ссылке')
    parser.add_argument('--phone-port',type=int,default=8767)
    parser.add_argument('--json', action='store_true', help='Машиночитаемый вывод')
    parser.add_argument('--output', type=Path, help='Сохранить результат в файл')
    parser.add_argument('--cache-dir', type=Path, default=Path.home() / '.cache' / 'redcn')
    parser.add_argument('--refresh', action='store_true')
    parser.add_argument('--download-photos', type=Path, metavar='DIRECTORY')
    parser.add_argument('--crop-photos', action='store_true', help='OCR метки ЦИАН и кадрированные копии; нужен --download-photos')
    parser.add_argument('--crop-bottom', type=float, metavar='PERCENT', help='Явный нижний отступ 0–40%% для каждого фото; нужен --download-photos')
    parser.add_argument('--batch', type=Path, help='JSON-манифест новых ссылок с seller / commission')
    args = parser.parse_args(argv)
    if (args.crop_photos or args.crop_bottom is not None) and not args.download_photos:parser.error('Кадрирование требует --download-photos DIRECTORY')
    if args.crop_bottom is not None and not 0 <= args.crop_bottom <= 40:parser.error('--crop-bottom: 0–40 процентов')
    if not 15 <= args.browser_timeout <= 600: parser.error('--browser-timeout: 15–600 секунд')
    if not 1<=args.phone_port<=65535:parser.error('--phone-port: 1–65535')
    if args.serve:
        from local_server import serve
        return serve(args.cache_dir, args.port, browser_channel=args.browser_channel,phone=args.phone,phone_port=args.phone_port)
    read_json = lambda p: json.loads(p.read_text(encoding='utf-8')) if p else {}
    data, rules, prefs = read_json(args.data), read_json(args.rules), read_json(args.prefs)
    if not isinstance(data, dict) or not isinstance(rules, dict) or not isinstance(prefs, dict):
        raise ValueError('Поля, словарь и настройки должны быть JSON-объектами')
    if args.variant is not None: data['titleVariant'] = args.variant
    loader = 'chrome' if args.browser else args.loader
    progress = lambda message: print(message, file=sys.stderr, flush=True)
    connection = open_cache(args.cache_dir)
    try:
        if args.batch:
            items = read_json(args.batch)
            if not isinstance(items, list) or len(items) > 100:
                raise ValueError('Пакет должен содержать не более 100 записей')
            result = []
            for item in items:
                if not isinstance(item, dict) or not item.get('url'):
                    raise ValueError('Каждая запись: {url, seller, commission}')
                url = normalize_url(item['url'])
                if not eligible(item.get('seller'), item.get('commission')):
                    result.append({'url': url, 'status': 'filtered', 'reason': 'Нужен собственник или посредник с известной комиссией 0–50%'});continue
                if connection.execute('SELECT 1 FROM seen WHERE url=?', (url,)).fetchone():
                    result.append({'url': url, 'status': 'seen'});continue
                try:
                    listing = load_listing(url, connection, args.refresh, loader, args.browser_timeout, progress, browser_channel=args.browser_channel, browser_scroll=args.browser_scroll)
                    generated = generate(listing['text'], {**listing.get('fields',{}), 'titleVariant':next_variant(connection,url), **data, **item.get('data', {}), 'url':url, 'source':listing['text']}, rules, prefs)
                    row = {'url': url, 'status': 'new', 'seller': item['seller'], 'commission': item.get('commission'), 'listing': listing, **generated}
                    if args.download_photos:
                        row['photos'] = download_photos(listing['images'], args.download_photos / hashlib.sha256(url.encode()).hexdigest()[:12],args.crop_photos,args.crop_bottom,args.cache_dir)
                    result.append(row)
                    connection.execute('INSERT OR REPLACE INTO seen VALUES (?,?)', (url, time.time()));connection.commit()
                except (OSError, ValueError) as error:
                    result.append({'url': url, 'status': 'error', 'error': str(error)})
            output = json.dumps(result, ensure_ascii=False, indent=2)
        else:
            listing = None
            if args.html or args.input and args.input.suffix.lower() in ('.html','.htm'):
                path = args.html or args.input
                if path.stat().st_size > MAX_HTML: raise ValueError('HTML превышает 8 МБ')
                listing = parse_html(path.read_text(encoding='utf-8'), args.url or 'https://www.cian.ru/')
                text = listing['text']
            elif args.input:
                text = args.input.read_text(encoding='utf-8')
            else:
                url = args.url or input('Ссылка на объявление ЦИАН / Авито: ').strip()
                listing = load_listing(url, connection, args.refresh, loader, args.browser_timeout, progress, browser_channel=args.browser_channel, browser_scroll=args.browser_scroll)
                text = listing['text']
            key = listing.get('url') if listing else hashlib.sha256(text.encode()).hexdigest()
            result = generate(text, {**(listing.get('fields',{}) if listing else {}), 'titleVariant':next_variant(connection,key), **data, 'source':text, **({'url':key} if listing else {})}, rules, prefs)
            if listing:
                result['listing'] = listing
                if args.download_photos:
                    result['photos'] = download_photos(listing['images'], args.download_photos,args.crop_photos,args.crop_bottom,args.cache_dir)
            output = json.dumps(result, ensure_ascii=False, indent=2) if args.json else '\n'.join(['ЗАГОЛОВКИ:', *[f'{i+1}. {t}' for i, t in enumerate(result['titles'])], '', 'ОПИСАНИЕ:', result['body'], '', result['tags'], '', 'Непереведённые фразы: '+', '.join(result['unknown'])])
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True);args.output.write_text(output, encoding='utf-8')
        else:
            print(output)
        if args.open and not args.batch:
            from local_server import save_result, serve
            result_id = save_result(args.cache_dir, result)
            connection.close()
            connection = None
            return serve(args.cache_dir, args.port, result_id, browser_channel=args.browser_channel,phone=args.phone,phone_port=args.phone_port)
    finally:
        if connection: connection.close()
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print('Ошибка: '+str(error), file=sys.stderr)
        raise SystemExit(2)
