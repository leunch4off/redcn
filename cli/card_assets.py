"""Private card dossiers, reusable presentation templates and static HTML/PDF exports.

HTTP handlers must apply the existing desktop/paired-phone authentication before
calling this module. Marketing exports never include dossier attachments.
"""
import base64
import binascii
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import secrets
import sqlite3
import tempfile
import threading
import time
from functools import wraps
import zipfile
import io

MAX_ATTACHMENT = 40 * 1024 * 1024
MAX_HTML = 10 * 1024 * 1024
MAX_TEMPLATE = 64 * 1024
_CATEGORIES = {'contract', 'floorplan', 'lidar', 'other'}
_STATUSES = {'active', 'rented', 'archived'}
_MIMES = {
    '.pdf': 'application/pdf', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
    '.png': 'image/png', '.webp': 'image/webp', '.txt': 'text/plain; charset=utf-8',
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.usdz': 'model/vnd.usdz+zip', '.obj': 'text/plain; charset=utf-8',
    '.glb': 'model/gltf-binary', '.gltf': 'model/gltf+json',
    '.ply': 'application/octet-stream', '.stl': 'model/stl', '.zip': 'application/zip',
    '.dxf': 'application/dxf', '.ifc': 'application/x-step',
}
_pdf_lock = threading.Lock()
_file_lock = threading.RLock()


def _serialize_files(function):
    @wraps(function)
    def guarded(*args, **kwargs):
        with _file_lock:
            return function(*args, **kwargs)
    return guarded


def _folder(directory):
    root = Path(directory).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    folder = root / 'card-assets'
    if folder.is_symlink():
        raise ValueError('Каталог вложений не может быть символической ссылкой')
    folder.mkdir(mode=0o700, exist_ok=True)
    folder.chmod(0o700)
    return folder


def _db(directory):
    path = _folder(directory) / 'dossiers.sqlite3'
    if path.is_symlink():
        raise ValueError('Недопустимый путь базы вложений')
    connection = sqlite3.connect(path, timeout=15)
    path.chmod(0o600)
    connection.execute('CREATE TABLE IF NOT EXISTS card_archive (id TEXT PRIMARY KEY, status TEXT, notes TEXT, updated REAL)')
    connection.execute('CREATE TABLE IF NOT EXISTS attachments (id TEXT PRIMARY KEY, card_id TEXT, name TEXT, category TEXT, mime TEXT, size INTEGER, created REAL, digest TEXT)')
    connection.execute('CREATE TABLE IF NOT EXISTS designs (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT, updated REAL)')
    connection.execute('CREATE TABLE IF NOT EXISTS exports (id TEXT PRIMARY KEY, card_id TEXT, kind TEXT, name TEXT, mime TEXT, size INTEGER, created REAL, digest TEXT)')
    return connection


def _card(directory, result_id):
    if not isinstance(result_id, str) or not re.fullmatch(r'[a-f0-9]{32}', result_id):
        raise ValueError('Неверный ID карточки')
    path = Path(directory) / 'listings.sqlite3'
    if not path.exists():
        raise ValueError('Карточка не найдена')
    with sqlite3.connect(path, timeout=15) as connection:
        try:
            found = connection.execute('SELECT 1 FROM results WHERE id=?', (result_id,)).fetchone()
        except sqlite3.OperationalError:
            found = None
    if not found:
        raise ValueError('Карточка не найдена')
    return result_id


def _id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{32}', value):
        raise ValueError('Неверный ID файла')
    return value


def _write(directory, content):
    folder = _folder(directory)
    digest = hashlib.sha256(content).hexdigest()
    path = folder / digest
    if path.is_symlink():
        raise ValueError('Недопустимый путь файла')
    if not path.exists():
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=folder, delete=False) as output:
                temporary = Path(output.name)
                output.write(content)
            temporary.chmod(0o600)
            temporary.replace(path)
        finally:
            if temporary and temporary.exists():
                temporary.unlink()
    return digest


def _read(directory, digest):
    if not re.fullmatch(r'[a-f0-9]{64}', digest or ''):
        raise ValueError('Неверный ID содержимого')
    path = _folder(directory) / digest
    if path.is_symlink():
        raise ValueError('Недопустимый путь файла')
    try:
        content = path.read_bytes()
    except OSError as error:
        raise ValueError('Файл отсутствует в архиве компьютера') from error
    if hashlib.sha256(content).hexdigest() != digest:
        raise ValueError('Сохранённый файл повреждён')
    return content


def load_design(directory):
    with _db(directory) as connection:
        row = connection.execute('SELECT payload,updated FROM designs WHERE id=1').fetchone()
    return {'template': json.loads(row[0]) if row else None, 'updated': row[1] if row else None}


def save_design(directory, template):
    if not isinstance(template, dict):
        raise ValueError('Шаблон должен быть JSON-объектом')
    allowed = {'version', 'preset', 'accent', 'background', 'foreground', 'font', 'paper', 'blocks', 'contact', 'footer', 'intro', 'outro'}
    if set(template) - allowed:
        raise ValueError('Неизвестное поле шаблона')
    try:
        encoded = json.dumps(template, ensure_ascii=False, allow_nan=False)
    except (ValueError, TypeError, RecursionError) as error:
        raise ValueError('Неверный шаблон') from error
    if len(encoded.encode()) > MAX_TEMPLATE:
        raise ValueError('Шаблон превышает 64 КБ')
    if type(template.get('version', 1)) is not int or template.get('version', 1) != 1:
        raise ValueError('Неподдерживаемая версия шаблона')
    for name in ('preset', 'accent', 'background', 'foreground', 'font', 'paper', 'contact', 'footer'):
        if name in template and (not isinstance(template[name], str) or len(template[name]) > 4000):
            raise ValueError('Неверное поле шаблона: ' + name)
    for name in ('intro', 'outro'):
        if name in template and not isinstance(template[name], dict):
            raise ValueError('Неверное поле шаблона: ' + name)
    for name, choices in (('preset', {'editorial', 'minimal', 'night'}), ('font', {'sans', 'serif'}), ('paper', {'A4', 'Letter'})):
        if name in template and template[name] not in choices:
            raise ValueError('Неверное поле шаблона: ' + name)
    for name in ('accent', 'background', 'foreground'):
        if name in template and not re.fullmatch(r'#[a-fA-F0-9]{6}', template[name]):
            raise ValueError('Цвет должен иметь формат #RRGGBB')
    for name in ('contact', 'footer'):
        if len(template.get(name, '')) > 500:
            raise ValueError('Контакт и подвал: до 500 символов')
    intro = template.get('intro', {})
    if set(intro) - {'headline', 'subtitle', 'layout', 'position', 'align', 'fontSize', 'overlay', 'photoIndex', 'showPrice', 'showMetro', 'showArea'}:
        raise ValueError('Неизвестное поле intro')
    outro = template.get('outro', {})
    if set(outro) - {'heading', 'note', 'showMap'}:
        raise ValueError('Неизвестное поле outro')
    for values, name, limit in ((intro, 'headline', 600), (intro, 'subtitle', 600), (outro, 'heading', 300), (outro, 'note', 1200)):
        if name in values and (not isinstance(values[name], str) or len(values[name]) > limit):
            raise ValueError('Неверный текст в дизайне: ' + name)
    for name, choices in (('layout', {'single', 'duo', 'trio'}), ('position', {'top', 'middle', 'bottom'}), ('align', {'left', 'center'})):
        if name in intro and (not isinstance(intro[name], str) or intro[name] not in choices):
            raise ValueError('Неверное поле intro: ' + name)
    for name, minimum, maximum in (('fontSize', 36, 100), ('overlay', 0, 95), ('photoIndex', 0, 29)):
        if name in intro and (type(intro[name]) not in (int, float) or not minimum <= intro[name] <= maximum or (name == 'photoIndex' and type(intro[name]) is not int)):
            raise ValueError('Неверное числовое поле intro: ' + name)
    for values, name in ((intro, 'showPrice'), (intro, 'showMetro'), (intro, 'showArea'), (outro, 'showMap')):
        if name in values and type(values[name]) is not bool:
            raise ValueError('Неверный флаг дизайна: ' + name)
    if 'blocks' in template:
        if not isinstance(template['blocks'], list) or len(template['blocks']) > 24:
            raise ValueError('Неверный список блоков')
        seen = set()
        for block in template['blocks']:
            if not isinstance(block, dict) or set(block) != {'id', 'enabled'} or not isinstance(block['id'], str) or block['id'] not in {'cover', 'title', 'facts', 'description', 'photos', 'location', 'contact'} or type(block['enabled']) is not bool or block['id'] in seen:
                raise ValueError('Неверный блок шаблона')
            seen.add(block['id'])
    stamp = time.time()
    with _db(directory) as connection:
        connection.execute('INSERT OR REPLACE INTO designs VALUES (1,?,?)', (encoded, stamp))
    return {'template': json.loads(encoded), 'updated': stamp}


def _attachment(row):
    return dict(zip(('id', 'name', 'category', 'mime', 'bytes', 'created'), row))


def archive(directory, result_id):
    _card(directory, result_id)
    with _db(directory) as connection:
        row = connection.execute('SELECT status,notes,updated FROM card_archive WHERE id=?', (result_id,)).fetchone()
        files = connection.execute('SELECT id,name,category,mime,size,created FROM attachments WHERE card_id=? ORDER BY created,id', (result_id,)).fetchall()
    return {'result_id': result_id, 'status': row[0] if row else 'active', 'notes': row[1] if row else '', 'updated': row[2] if row else None, 'attachments': [_attachment(item) for item in files]}


def update_archive(directory, result_id, values):
    _card(directory, result_id)
    if not isinstance(values, dict) or set(values) - {'status', 'notes'}:
        raise ValueError('Неверные поля архива')
    with _db(directory) as connection:
        connection.execute('BEGIN IMMEDIATE')
        old = connection.execute('SELECT status,notes FROM card_archive WHERE id=?', (result_id,)).fetchone() or ('active', '')
        status, notes = values.get('status', old[0]), values.get('notes', old[1])
        if not isinstance(status, str) or status not in _STATUSES or not isinstance(notes, str) or len(notes) > 20000 or '\x00' in notes:
            raise ValueError('Статус: active/rented/archived; заметка до 20 000 символов')
        connection.execute('INSERT OR REPLACE INTO card_archive VALUES (?,?,?,?)', (result_id, status, notes, time.time()))
    return archive(directory, result_id)


def _name(name):
    if not isinstance(name, str) or not name.strip() or len(name) > 180 or name in ('.', '..') or '/' in name or '\\' in name or any(ord(character) < 32 or ord(character) == 127 for character in name):
        raise ValueError('Неверное имя файла')
    # Controls and quotes must never enter Content-Disposition; the HTTP layer
    # uses RFC5987 for the original UTF-8 name and an ASCII fallback filename.
    if any(character in name for character in ('"', ';', '\x00')):
        raise ValueError('Недопустимый символ имени файла')
    return name.strip()


def _validate_file(name, content):
    extension = Path(name).suffix.lower()
    mime = _MIMES.get(extension)
    if not mime:
        raise ValueError('Формат не поддерживается. PDF, фото, DOCX, планировка или 3D-файл')
    if not content or len(content) > MAX_ATTACHMENT:
        raise ValueError('Файл должен быть от 1 байта до 40 МБ')
    if extension == '.pdf' and not content.startswith(b'%PDF-'):
        raise ValueError('Файл не является PDF')
    if extension in ('.png', '.jpg', '.jpeg', '.webp'):
        import photo_store
        image = photo_store.open_photo(content)
        image.close()
        signature = (extension == '.png' and content.startswith(b'\x89PNG\r\n\x1a\n')) or (extension in ('.jpg', '.jpeg') and content.startswith(b'\xff\xd8\xff')) or (extension == '.webp' and content.startswith(b'RIFF') and content[8:12] == b'WEBP')
        if not signature:
            raise ValueError('Расширение фото не соответствует содержимому')
    if extension in ('.docx', '.zip', '.usdz'):
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as bundle:
                entries = bundle.infolist()
                if len(entries) > 20000 or sum(item.file_size for item in entries) > 512 * 1024 * 1024:
                    raise ValueError('Архив слишком велик после распаковки')
                if extension == '.docx' and not {'[Content_Types].xml', 'word/document.xml'}.issubset(bundle.namelist()):
                    raise ValueError('Файл не является DOCX')
        except (zipfile.BadZipFile, RuntimeError) as error:
            raise ValueError('Повреждённый ZIP/DOCX/USDZ') from error
    if extension in ('.txt', '.obj', '.gltf'):
        try:
            text = content.decode('utf-8-sig')
        except UnicodeDecodeError as error:
            raise ValueError('Текстовый файл должен быть в UTF-8') from error
        if '\x00' in text:
            raise ValueError('Неверный текстовый файл')
        if extension == '.gltf':
            try:
                value = json.loads(text)
                if not isinstance(value, dict) or not isinstance(value.get('asset'), dict):
                    raise ValueError()
            except ValueError as error:
                raise ValueError('Неверный glTF JSON') from error
    if extension == '.glb' and (len(content) < 12 or content[:4] != b'glTF' or int.from_bytes(content[8:12], 'little') != len(content)):
        raise ValueError('Неверный GLB')
    if extension == '.ply' and not content.startswith(b'ply'):
        raise ValueError('Неверный PLY')
    return mime


@_serialize_files
def add_attachment(directory, result_id, data):
    _card(directory, result_id)
    if not isinstance(data, dict) or not isinstance(data.get('category'), str) or data.get('category') not in _CATEGORIES:
        raise ValueError('Категория: contract/floorplan/lidar/other')
    name = _name(data.get('name'))
    encoded = data.get('data')
    if not isinstance(encoded, str) or len(encoded) > ((MAX_ATTACHMENT + 2) // 3) * 4:
        raise ValueError('Файл превышает 40 МБ')
    try:
        content = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError('Неверное содержимое файла') from error
    mime = _validate_file(name, content)
    digest = _write(directory, content)
    attachment_id = secrets.token_hex(16)
    with _db(directory) as connection:
        if connection.execute('SELECT COUNT(*) FROM attachments WHERE card_id=?', (result_id,)).fetchone()[0] >= 100:
            raise ValueError('До 100 файлов на карточку')
        connection.execute('INSERT INTO attachments VALUES (?,?,?,?,?,?,?,?)', (attachment_id, result_id, name, data['category'], mime, len(content), time.time(), digest))
        connection.execute('INSERT INTO card_archive VALUES (?,\'active\',\'\',?) ON CONFLICT(id) DO UPDATE SET updated=excluded.updated', (result_id, time.time()))
    return archive(directory, result_id)


@_serialize_files
def remove_attachment(directory, result_id, attachment_id):
    _card(directory, result_id)
    _id(attachment_id)
    with _db(directory) as connection:
        row = connection.execute('SELECT digest FROM attachments WHERE card_id=? AND id=?', (result_id, attachment_id)).fetchone()
        if not row:
            raise ValueError('Вложение не найдено')
        connection.execute('DELETE FROM attachments WHERE card_id=? AND id=?', (result_id, attachment_id))
        connection.execute('UPDATE card_archive SET updated=? WHERE id=?', (time.time(), result_id))
        # Physical content is removed only once its final private reference is gone.
        refs = connection.execute('SELECT (SELECT COUNT(*) FROM attachments WHERE digest=?)+(SELECT COUNT(*) FROM exports WHERE digest=?)', (row[0], row[0])).fetchone()[0]
        if not refs:
            path = _folder(directory) / row[0]
            if not path.is_symlink():
                path.unlink(missing_ok=True)
    return archive(directory, result_id)


@_serialize_files
def attachment_asset(directory, result_id, attachment_id):
    _card(directory, result_id)
    _id(attachment_id)
    with _db(directory) as connection:
        row = connection.execute('SELECT digest,mime,name FROM attachments WHERE card_id=? AND id=?', (result_id, attachment_id)).fetchone()
    if not row:
        raise ValueError('Вложение не найдено')
    return _read(directory, row[0]), row[1], row[2]


_ALLOWED_TAGS = set('html head title meta body style main header footer section article div span p h1 h2 h3 h4 h5 h6 strong b em i small sup sub br hr img figure figcaption ul ol li dl dt dd table thead tbody tfoot tr td th colgroup col a'.split())
_ALLOWED_ATTRS = set('class id style title lang dir colspan rowspan width height alt src href charset name content role'.split())
_IMAGE_PREFIX = re.compile(r'^data:image/(png|jpeg|webp);base64,([A-Za-z0-9+/=\r\n]+)$', re.I)


def _image_data(value):
    match = _IMAGE_PREFIX.fullmatch(value)
    if not match:
        raise ValueError('В HTML разрешены только встроенные PNG/JPEG/WebP')
    try:
        content = base64.b64decode(re.sub(r'\s+', '', match[2]), validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError('Неверная встроенная фотография') from error
    if not content or len(content) > MAX_HTML:
        raise ValueError('Слишком большая встроенная фотография')
    signature = (match[1].lower() == 'png' and content.startswith(b'\x89PNG\r\n\x1a\n')) or (match[1].lower() == 'jpeg' and content.startswith(b'\xff\xd8\xff')) or (match[1].lower() == 'webp' and content.startswith(b'RIFF') and content[8:12] == b'WEBP')
    if not signature:
        raise ValueError('Неверный формат встроенной фотографии')
    import photo_store
    image = photo_store.open_photo(content)
    image.close()


def _css(value):
    # Reject escapes/comments before inspecting resource rules; this avoids
    # alternate CSS spellings of import/url. Browser network is also disabled.
    clean = re.sub(r'/\*.*?\*/', '', value, flags=re.S)
    if '\\' in clean or re.search(r'@(?:import|font-face|namespace)|expression\s*\(|(?:-webkit-)?image-set\s*\(|-moz-binding|behavior\s*:', clean, re.I):
        raise ValueError('В шаблоне запрещены внешние ресурсы и активный CSS')
    for match in re.finditer(r'url\s*\((.*?)\)', clean, re.S | re.I):
        _image_data(match[1].strip().strip('\"\''))
    if re.search(r'(?:https?|file|ftp|javascript)\s*:', re.sub(r'url\s*\(.*?\)', '', clean, flags=re.S | re.I), re.I):
        raise ValueError('В шаблоне запрещены внешние ресурсы')


class _StaticHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_style = False
        self.styles = []

    def handle_decl(self, decl):
        if decl.lower() != 'doctype html':
            raise ValueError('Неверный тип HTML')

    def handle_starttag(self, tag, attrs):
        if tag not in _ALLOWED_TAGS:
            raise ValueError('HTML содержит запрещённый элемент: ' + tag)
        attributes = dict(attrs)
        if len(attributes) != len(attrs):
            raise ValueError('Повторяющийся HTML-атрибут')
        for key, value in attrs:
            if key not in _ALLOWED_ATTRS and not key.startswith('aria-'):
                raise ValueError('HTML содержит запрещённый атрибут: ' + key)
            value = value or ''
            if key == 'style':
                _css(value)
            if key == 'src':
                if tag != 'img':
                    raise ValueError('Недопустимый источник HTML')
                _image_data(value)
            if key == 'href':
                if tag != 'a' or (not value.startswith(('https://', 'http://', '#'))):
                    raise ValueError('Недопустимая ссылка HTML')
            if key == 'charset' and value.lower().replace('-', '') != 'utf8':
                raise ValueError('Нужен UTF-8')
        if tag == 'meta' and (set(attributes) - {'charset', 'name', 'content'} or attributes.get('name', '').lower() not in ('', 'viewport', 'description')):
            raise ValueError('Неверный meta-элемент')
        if tag == 'style':
            self.in_style = True
            self.styles = []

    def handle_startendtag(self, tag, attrs):
        if tag not in {'meta', 'br', 'hr', 'img', 'col'}:
            raise ValueError('Недопустимый самозакрывающийся HTML-элемент')
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag not in _ALLOWED_TAGS:
            raise ValueError('HTML содержит запрещённый элемент: ' + tag)
        if tag == 'style':
            _css(''.join(self.styles))
            self.in_style = False

    def handle_comment(self, data):
        if self.in_style:
            self.styles.append(data)

    def handle_data(self, data):
        if self.in_style:
            self.styles.append(data)

    def close(self):
        super().close()
        if self.in_style:
            raise ValueError('Незакрытый стиль HTML')


def validate_html(html):
    if not isinstance(html, str) or not html.strip() or '\x00' in html:
        raise ValueError('Нужен HTML-документ')
    if len(html.encode()) > MAX_HTML:
        raise ValueError('HTML превышает 10 МБ')
    if not re.search(r'<html(?:\s|>)', html, re.I) or not re.search(r'<body(?:\s|>)', html, re.I):
        raise ValueError('Нужен полный HTML-документ')
    parser = _StaticHTML()
    try:
        parser.feed(html)
        parser.close()
    except (ValueError, RecursionError) as error:
        raise ValueError(str(error)) from error
    return html


def _render_pdf(html, browser_channel):
    if browser_channel not in ('chrome', 'chromium'):
        raise ValueError('Неверный браузер PDF')
    try:
        from playwright.sync_api import sync_playwright, Error
    except ImportError as error:
        raise ValueError('Для PDF установите .venv/bin/python -m pip install -r cli/requirements-browser.txt') from error
    with _pdf_lock:
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel='chrome' if browser_channel == 'chrome' else None, headless=True, timeout=45000)
                try:
                    context = browser.new_context(java_script_enabled=False, offline=True, service_workers='block')
                    context.route('**/*', lambda route: route.abort())
                    page = context.new_page()
                    page.set_content(html, wait_until='load', timeout=30000)
                    # This fixed Playwright expression only waits for decoded data
                    # images and installed fonts; page-authored JavaScript stays off.
                    page.evaluate('async () => { await document.fonts.ready; await Promise.all(Array.from(document.images, image => image.decode())); }')
                    content = page.pdf(format='A4', print_background=True, prefer_css_page_size=True)
                    context.close()
                finally:
                    browser.close()
        except Error as error:
            raise ValueError('Не удалось создать PDF в Chrome: ' + str(error)[:400]) from error
    if not content.startswith(b'%PDF-'):
        raise ValueError('Chrome не вернул PDF')
    if len(content) > MAX_ATTACHMENT:
        raise ValueError('Созданный PDF превышает 40 МБ; уменьшите число или размер фотографий')
    return content


@_serialize_files
def _persist_export(directory, result_id, content, kind, mime):
    # The shared file lock protects only bytes + database references. Rendering
    # runs outside it so phones can still fetch contracts during a PDF job.
    digest = _write(directory, content)
    export_id = secrets.token_hex(16)
    stamp = time.time()
    name = 'most-' + result_id[:8] + ('-' + kind + '.png' if kind in ('intro', 'outro') else '.' + kind)
    with _db(directory) as connection:
        connection.execute('INSERT INTO exports VALUES (?,?,?,?,?,?,?,?)', (export_id, result_id, kind, name, mime, len(content), stamp, digest))
    return {'id': export_id, 'result_id': result_id, 'kind': kind, 'name': name, 'mime': mime, 'bytes': len(content), 'created': stamp}


def create_export(directory, result_id, html, kind, browser_channel='chrome'):
    _card(directory, result_id)
    if kind not in ('html', 'pdf'):
        raise ValueError('Формат экспорта: html/pdf')
    validate_html(html)
    content = html.encode('utf-8') if kind == 'html' else _render_pdf(html, browser_channel)
    mime = 'text/html; charset=utf-8' if kind == 'html' else 'application/pdf'
    return _persist_export(directory, result_id, content, kind, mime)


@_serialize_files
def export_asset(directory, export_id):
    _id(export_id)
    with _db(directory) as connection:
        row = connection.execute('SELECT card_id,digest,mime,name FROM exports WHERE id=?', (export_id,)).fetchone()
    if not row:
        raise ValueError('Экспорт не найден')
    _card(directory, row[0])
    return _read(directory, row[1]), row[2], row[3]


def list_exports(directory, result_id):
    _card(directory, result_id)
    with _db(directory) as connection:
        rows = connection.execute('SELECT id,card_id,kind,name,mime,size,created FROM exports WHERE card_id=? ORDER BY created DESC,id DESC', (result_id,)).fetchall()
    return [dict(zip(('id', 'result_id', 'kind', 'name', 'mime', 'bytes', 'created'), row)) for row in rows]


def create_image_export(directory, result_id, data, kind):
    _card(directory, result_id)
    if kind not in ('intro', 'outro'):
        raise ValueError('Вид изображения: intro/outro')
    if not isinstance(data, str):
        raise ValueError('Нужен PNG в base64')
    if data.startswith('data:image/png;base64,'):
        data = data.split(',', 1)[1]
    if len(data) > ((8 * 1024 * 1024 + 2) // 3) * 4:
        raise ValueError('PNG превышает 8 МБ')
    try:
        content = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError('Неверный PNG') from error
    if not content.startswith(b'\x89PNG\r\n\x1a\n') or len(content) > 8 * 1024 * 1024:
        raise ValueError('Нужен PNG до 8 МБ')
    import photo_store
    image = photo_store.open_photo(content)
    image.close()
    return _persist_export(directory, result_id, content, kind, 'image/png')
