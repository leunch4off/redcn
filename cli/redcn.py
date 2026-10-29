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
MAX_HTML = 2_000_000
MAX_IMAGE = 12_000_000


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
    opener = urllib.request.build_opener(CheckedRedirect(images))
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
    if re.search(r'captcha|verify you are human|проверка браузера|доступ ограничен|checking your browser', markup[:12000], re.I):
        raise ValueError('Источник запросил проверку браузера. Вставьте текст вручную; защита не обходится.')
    parser = ListingHTML()
    parser.feed(markup)
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


def load_listing(url, connection, refresh=False):
    # Resolve only on a network miss; cached pages remain available offline.
    url = allowed_url(url, resolve=False)
    row = connection.execute('SELECT fetched,payload FROM cache WHERE url=?', (url,)).fetchone()
    if row and not refresh and time.time() - row[0] < 86400:
        return {**json.loads(row[1]), 'cached': True}
    content, _ = fetch_bytes(url)
    result = parse_html(content.decode('utf-8', errors='replace'), url)
    connection.execute('INSERT OR REPLACE INTO cache VALUES (?,?,?)', (url, time.time(), json.dumps(result, ensure_ascii=False)))
    connection.commit()
    return {**result, 'cached': False}


def eligible(seller, commission):
    if seller == 'owner':
        return True
    try:
        return seller == 'agent' and commission is not None and 0 <= float(commission) <= 50
    except (TypeError, ValueError):
        return False


def generate(text, data=None, rules=None, prefs=None):
    node = shutil.which('node')
    if not node:
        raise ValueError('Нужен Node.js 22+: сайт и CLI используют один генератор, без расхождения правил.')
    result = subprocess.run([node, str(ROOT / 'cli/generate.mjs')], input=json.dumps({'text': text, 'data': data or {}, 'rules': rules or {}, 'prefs': prefs or {}}, ensure_ascii=False), text=True, encoding='utf-8', capture_output=True, check=False)
    if result.returncode:
        raise ValueError(result.stderr.strip() or 'Ошибка генерации')
    return json.loads(result.stdout)


def download_photos(urls, directory):
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
            results.append({'url': url, 'path': str(path), 'sha256': digest})
        except (OSError, ValueError) as error:
            results.append({'url': url, 'error': str(error)})
    (directory / 'manifest.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description='redcn: ссылка / текст → китайские заголовки, описание и теги')
    parser.add_argument('url', nargs='?')
    parser.add_argument('--input', type=Path, help='Текст объявления UTF-8')
    parser.add_argument('--data', type=Path, help='Проверенные поля карточки JSON')
    parser.add_argument('--rules', type=Path, help='Словарь RU → CN JSON')
    parser.add_argument('--prefs', type=Path, help='Преимущества и теги JSON')
    parser.add_argument('--variant', type=int, default=0, help='Номер варианта заголовков и тегов')
    parser.add_argument('--json', action='store_true', help='Машиночитаемый вывод')
    parser.add_argument('--output', type=Path, help='Сохранить результат в файл')
    parser.add_argument('--cache-dir', type=Path, default=Path.home() / '.cache' / 'redcn')
    parser.add_argument('--refresh', action='store_true')
    parser.add_argument('--download-photos', type=Path, metavar='DIRECTORY')
    parser.add_argument('--batch', type=Path, help='JSON-манифест новых ссылок с seller / commission')
    args = parser.parse_args(argv)
    read_json = lambda p: json.loads(p.read_text(encoding='utf-8')) if p else {}
    data, rules, prefs = read_json(args.data), read_json(args.rules), read_json(args.prefs)
    if not isinstance(data, dict) or not isinstance(rules, dict) or not isinstance(prefs, dict):
        raise ValueError('Поля, словарь и настройки должны быть JSON-объектами')
    data['titleVariant'] = args.variant
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
                url = allowed_url(item['url'], resolve=False)
                if not eligible(item.get('seller'), item.get('commission')):
                    result.append({'url': url, 'status': 'filtered', 'reason': 'Нужен собственник или посредник с известной комиссией 0–50%'});continue
                if connection.execute('SELECT 1 FROM seen WHERE url=?', (url,)).fetchone():
                    result.append({'url': url, 'status': 'seen'});continue
                try:
                    listing = load_listing(url, connection, args.refresh)
                    generated = generate(listing['text'], {**data, **item.get('data', {})}, rules, prefs)
                    row = {'url': url, 'status': 'new', 'seller': item['seller'], 'commission': item.get('commission'), 'listing': listing, **generated}
                    if args.download_photos:
                        row['photos'] = download_photos(listing['images'], args.download_photos / hashlib.sha256(url.encode()).hexdigest()[:12])
                    result.append(row)
                    connection.execute('INSERT OR REPLACE INTO seen VALUES (?,?)', (url, time.time()));connection.commit()
                except (OSError, ValueError) as error:
                    result.append({'url': url, 'status': 'error', 'error': str(error)})
            output = json.dumps(result, ensure_ascii=False, indent=2)
        else:
            listing = None
            if args.input:
                text = args.input.read_text(encoding='utf-8')
            else:
                url = args.url or input('Ссылка на объявление ЦИАН / Авито: ').strip()
                listing = load_listing(url, connection, args.refresh)
                text = listing['text']
            result = generate(text, data, rules, prefs)
            if listing:
                result['listing'] = listing
                if args.download_photos:
                    result['photos'] = download_photos(listing['images'], args.download_photos)
            output = json.dumps(result, ensure_ascii=False, indent=2) if args.json else '\n'.join(['ЗАГОЛОВКИ:', *[f'{i+1}. {t}' for i, t in enumerate(result['titles'])], '', 'ОПИСАНИЕ:', result['body'], '', result['tags'], '', 'Непереведённые фразы: '+', '.join(result['unknown'])])
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True);args.output.write_text(output, encoding='utf-8')
        else:
            print(output)
    finally:
        connection.close()
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print('Ошибка: '+str(error), file=sys.stderr)
        raise SystemExit(2)
