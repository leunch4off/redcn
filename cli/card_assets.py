"""Private card dossiers, reusable presentation templates and static HTML/PDF exports.

HTTP handlers must apply the existing desktop/paired-phone authentication before
calling this module. Marketing exports never include dossier attachments.
"""
import base64
import binascii
import errno
import hashlib
from html.parser import HTMLParser
import json
import math
import os
import signal
import subprocess
import sys
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
MAX_HTML = 32 * 1024 * 1024
MAX_TEMPLATE = 64 * 1024
MAX_TEMPLATE_V2 = 256 * 1024
MAX_DESIGN_IMAGE = 4 * 1024 * 1024
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
PDF_TOTAL_TIMEOUT = 90
PDF_CLEANUP_RESERVE = 1.5
_pdf_lock = threading.Lock()
_file_lock = threading.RLock()
_EXPECTED_UNSET = object()


class DesignConflict(ValueError):
    pass


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
    # Additive certification only: original exports and user files stay intact.
    connection.execute('CREATE TABLE IF NOT EXISTS export_publication_policy (export_id TEXT PRIMARY KEY, version TEXT NOT NULL, digest TEXT NOT NULL)')
    connection.execute('CREATE TABLE IF NOT EXISTS design_images (id TEXT PRIMARY KEY, role TEXT, name TEXT, mime TEXT, width INTEGER, height INTEGER, size INTEGER, created REAL, digest TEXT, original_digest TEXT, original_mime TEXT, original_size INTEGER)')
    connection.execute('CREATE TABLE IF NOT EXISTS restored_designs (card_id TEXT PRIMARY KEY, payload TEXT, created REAL)')
    connection.execute('CREATE TABLE IF NOT EXISTS restored_manifests (card_id TEXT PRIMARY KEY, payload TEXT, created REAL)')
    connection.execute('CREATE TABLE IF NOT EXISTS export_design_snapshots (export_id TEXT PRIMARY KEY, payload TEXT, created REAL)')
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


def _save_design_payload(directory, encoded, expected_updated, asset_ids=()):
    if expected_updated is not _EXPECTED_UNSET and expected_updated is not None and (type(expected_updated) not in (int, float) or not math.isfinite(expected_updated)):
        raise ValueError('Неверная версия сохранённого дизайна')
    with _db(directory) as connection:
        connection.execute('BEGIN IMMEDIATE')
        old = connection.execute('SELECT updated FROM designs WHERE id=1').fetchone()
        current = old[0] if old else None
        if expected_updated is not _EXPECTED_UNSET and expected_updated != current:
            raise DesignConflict('Дизайн изменён на другом устройстве. Загрузите актуальную версию перед сохранением.')
        for asset_id in filter(None, asset_ids):
            if not connection.execute('SELECT 1 FROM design_images WHERE id=?', (asset_id,)).fetchone():
                raise ValueError('Изображение бренда отсутствует. Загрузите его заново.')
        stamp = max(time.time(), (current or 0)+0.000001)
        connection.execute('INSERT OR REPLACE INTO designs VALUES (1,?,?)', (encoded, stamp))
    return {'template': json.loads(encoded), 'updated': stamp}


def save_design(directory, template, expected_updated=_EXPECTED_UNSET):
    if not isinstance(template, dict):
        raise ValueError('Шаблон должен быть JSON-объектом')
    if type(template.get('version')) is int and template['version'] == 2:
        from publication_schema import validate_publication_template
        validated = validate_publication_template(template)
        encoded = json.dumps(validated, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode('utf-8')) > MAX_TEMPLATE_V2:
            raise ValueError('Шаблон превышает 256 КБ')
        asset_ids = [validated['brand'].get(key) for key in ('logoAssetId', 'avatarAssetId')]
        return _save_design_payload(directory, encoded, expected_updated, asset_ids)
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
    return _save_design_payload(directory, encoded, expected_updated)


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
        refs = connection.execute('SELECT (SELECT COUNT(*) FROM attachments WHERE digest=?)+(SELECT COUNT(*) FROM exports WHERE digest=?)+(SELECT COUNT(*) FROM design_images WHERE digest=? OR original_digest=?)', (row[0], row[0], row[0], row[0])).fetchone()[0]
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
_ALLOWED_ATTRS = set('class id style title lang dir colspan rowspan width height alt src href charset name content role data-node-id data-bind'.split())
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


@_serialize_files
def add_design_image(directory, data):
    """Keep an original privately and expose only a bounded, metadata-free copy."""
    if not isinstance(data, dict) or set(data)-{'data', 'name', 'role'}:
        raise ValueError('Неверные поля изображения бренда')
    role = data.get('role')
    if role not in ('logo', 'avatar'):
        raise ValueError('Вид изображения: logo/avatar')
    name = _name(data.get('name'))
    value = data.get('data')
    if not isinstance(value, str) or len(value) > ((MAX_DESIGN_IMAGE+2)//3)*4+50:
        raise ValueError('Изображение бренда превышает 4 МБ')
    match = _IMAGE_PREFIX.fullmatch(value)
    if not match:
        raise ValueError('Нужен статичный PNG, JPEG или WebP')
    try:
        original = base64.b64decode(re.sub(r'\s+', '', match[2]), validate=True)
    except (ValueError, binascii.Error) as error:
        raise ValueError('Неверное изображение бренда') from error
    if not original or len(original)>MAX_DESIGN_IMAGE:
        raise ValueError('Изображение бренда превышает 4 МБ')
    # Signature and decoded format must agree; MIME text alone is untrusted.
    signature = ((match[1].lower()=='png' and original.startswith(b'\x89PNG\r\n\x1a\n')) or
                 (match[1].lower()=='jpeg' and original.startswith(b'\xff\xd8\xff')) or
                 (match[1].lower()=='webp' and original.startswith(b'RIFF') and original[8:12]==b'WEBP'))
    if not signature:
        raise ValueError('Формат не соответствует содержимому изображения')
    import photo_store
    try:
        image = photo_store.open_photo(original)
    except OSError as error:
        raise ValueError('Не удалось прочитать изображение бренда') from error
    try:
        from PIL import Image
        # A fresh image discards EXIF, location and ancillary source metadata.
        image.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
        mode = 'RGBA' if image.mode in ('RGBA','LA') or 'transparency' in image.info else 'RGB'
        clean = Image.new(mode, image.size)
        clean.paste(image.convert(mode))
        output = io.BytesIO()
        clean.save(output, format='PNG', optimize=True)
        content = output.getvalue()
        width, height = clean.size
        clean.close()
    finally:
        image.close()
    if len(content)>MAX_DESIGN_IMAGE:
        raise ValueError('Подготовленное изображение превышает 4 МБ')
    with _db(directory) as connection:
        if connection.execute('SELECT COUNT(*) FROM design_images').fetchone()[0]>=200:
            raise ValueError('До 200 изображений бренда')
        digest = _write(directory, content)
        original_digest = _write(directory, original)
        identifier = secrets.token_hex(16)
        stamp = time.time()
        connection.execute('INSERT INTO design_images VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
            (identifier, role, name, 'image/png', width, height, len(content), stamp,
             digest, original_digest, 'image/'+match[1].lower(), len(original)))
    return design_image(directory, identifier)


@_serialize_files
def design_image(directory, asset_id):
    _id(asset_id)
    with _db(directory) as connection:
        row = connection.execute('SELECT id,role,name,mime,width,height,size,created,digest FROM design_images WHERE id=?', (asset_id,)).fetchone()
    if not row:
        raise ValueError('Изображение бренда не найдено')
    content = _read(directory, row[8])
    return {**dict(zip(('id','role','name','mime','width','height','bytes','created'), row[:8])),
            'dataUrl':'data:image/png;base64,'+base64.b64encode(content).decode()}


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
        raise ValueError('HTML превышает 32 МБ')
    if not re.search(r'<html(?:\s|>)', html, re.I) or not re.search(r'<body(?:\s|>)', html, re.I):
        raise ValueError('Нужен полный HTML-документ')
    parser = _StaticHTML()
    try:
        parser.feed(html)
        parser.close()
    except (ValueError, RecursionError) as error:
        raise ValueError(str(error)) from error
    return html


class _PublicationText(HTMLParser):
    """Inspect composed text as well as attrs/CSS; inline tags cannot split a fee."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text = []
        self.metadata = []
        self.in_style = False

    def handle_starttag(self, tag, attrs):
        if tag in ('div','p','section','article','header','footer','li','br','h1','h2','h3','h4','h5','h6'):self.text.append('\n')
        if tag == 'style':self.in_style = True
        self.metadata.extend(value for key,value in attrs if value and not (key == 'src' and value.startswith('data:image/')))

    def handle_startendtag(self, tag, attrs):self.handle_starttag(tag,attrs)

    def handle_endtag(self, tag):
        if tag == 'style':self.in_style = False
        if tag in ('div','p','section','article','header','footer','li','h1','h2','h3','h4','h5','h6'):self.text.append('\n')

    def handle_data(self, value):
        (self.metadata if self.in_style else self.text).append(value)

    def handle_comment(self, value):self.metadata.append(value)


def _assert_public_html(html,card=None):
    from client_publication import assert_public_text,assert_card_services
    parser = _PublicationText()
    parser.feed(html);parser.close()
    # Check both natural spacing and exact composition across inline elements.
    assert_public_text(''.join(parser.text))
    assert_public_text(' '.join(parser.text))
    for value in parser.metadata:assert_public_text(value)
    assert_card_services(card or {},texts=[''.join(parser.text),*parser.metadata])


def _publication_card(directory, result_id, *, private=False):
    from client_publication import client_card
    _card(directory,result_id)
    with sqlite3.connect(Path(directory)/'listings.sqlite3', timeout=15) as connection:
        row=connection.execute('SELECT payload FROM results WHERE id=?',(result_id,)).fetchone()
    card=json.loads(row[0]);projected=client_card(card)
    return card if private else projected


def _pdf_worker_command(browser_channel):
    # Source HTML must never enter argv, a named file or a shared Chrome profile.
    return [sys.executable,str(Path(__file__).with_name('pdf_render_worker.py')),browser_channel]


def _pdf_linux_snapshot(deadline,pids=None,*,proc_root=Path('/proc')):
    """Translate procfs host IDs into this process's PID namespace.

    A container can expose the host /proc while getpid/Popen/kill use inner
    IDs. Calling ps -p on the inner PID would inspect an unrelated host task.
    Only PID/PPID/group/start ticks/state and namespace identity are read here.
    Linux proc_pid_status(5) defines the NSpid/NSpgid ordering used below.
    """
    try:
        own_namespace=os.readlink(proc_root/'self/ns/pid')
        own_status={line.split(':',1)[0]:line.split(':',1)[1].split() for line in (proc_root/'self/status').read_text().splitlines() if ':' in line}
        index=len(own_status['NSpid'])-1
    except (OSError,KeyError):return None
    records={}
    try:entries=list(proc_root.iterdir())
    except OSError:return None
    for entry in entries:
        if time.monotonic()>=deadline:return None
        if not entry.name.isdigit():continue
        pid=None
        try:
            fields={line.split(':',1)[0]:line.split(':',1)[1].split() for line in (entry/'status').read_text().splitlines() if ':' in line}
            ids=fields['NSpid']
            if len(ids)<=index:continue
            pid=int(ids[index]);parent=int(fields['PPid'][0]);host=int(fields['Pid'][0])
            namespace=os.readlink(entry/'ns/pid')
            if namespace!=own_namespace:continue
            stat=(entry/'stat').read_text().rsplit(')',1)[1].split()
            groups=fields.get('NSpgid',[])
            group=int(groups[index]) if len(groups)>index else None
            records[host]=(pid,parent,group,'linux:'+stat[19],stat[0],namespace,int(stat[2]))
        except OSError as error:
            if error.errno in (errno.ENOENT,errno.ESRCH):continue
            # Discovery may encounter unrelated privileged processes on a
            # normal non-root host. Only verification of selected, already
            # observed identities must fail closed on unreadable metadata.
            if pids is not None and (pid is None or pid in pids):return None
        except (KeyError,ValueError,IndexError):
            if pids is not None and (pid is None or pid in pids):return None
    # Only this exact namespace is eligible. In particular, a sibling may
    # reuse the same inner PID and must never replace an owned identity.
    visible={host for host,row in records.items() if row[5]==own_namespace}
    output={}
    for host in visible:
        pid,parent,group,birth,state,namespace,host_group=records[host]
        if pids is not None and pid not in pids:continue
        group=group if group is not None else records.get(host_group,(0,))[0]
        output[pid]=(records[parent][0] if parent in visible else 0,group,birth,state)
    return output


def _pdf_process_snapshot(timeout,pids=None):
    """Read only ownership identifiers and terminal state, never command lines."""
    if os.name!='posix':return {}
    if timeout<=0:return None
    if sys.platform.startswith('linux'):
        return _pdf_linux_snapshot(time.monotonic()+timeout,pids)
    try:
        command=['ps','-axo','pid=,ppid=,pgid=,stat=,lstart='] if pids is None else ['ps','-p',','.join(str(pid) for pid in pids),'-o','pid=,ppid=,pgid=,stat=,lstart=']
        rows=subprocess.run(command,capture_output=True,text=True,timeout=timeout)
    except (OSError,subprocess.TimeoutExpired):return None
    output={}
    if rows.returncode:
        if pids is None or rows.returncode!=1 or rows.stderr.strip():return None
        if not rows.stdout.strip():return {}
    for line in rows.stdout.splitlines():
        parts=line.split(None,4)
        if len(parts)!=5:return None
        try:pid,parent,group=map(int,parts[:3])
        except ValueError:return None
        output[pid]=(parent,group,' '.join(parts[4].split()),parts[3])
    return output


class _PdfOwnedProcesses:
    """Track only the new worker's descendants, including detached Chrome."""
    def __init__(self,process):
        self.process=process;self.identities={}

    def refresh(self,deadline,*,known_only=False):
        rows=_pdf_process_snapshot(max(0,min(.08,deadline-time.monotonic())),
                                   set(self.identities)|{self.process.pid} if known_only else None)
        if rows is None:return None
        root=rows.get(self.process.pid)
        if root and (self.process.pid in self.identities or self.process.poll() is None):
            if self.process.pid not in self.identities:self.identities[self.process.pid]=root[2]
        live={pid for pid,birth in self.identities.items() if pid in rows and rows[pid][2]==birth}
        changed=True
        while changed:
            changed=False
            for pid,row in rows.items():
                if pid not in live and row[0] in live:
                    self.identities[pid]=row[2];live.add(pid);changed=True
        return {pid:rows[pid] for pid in live}

    def signal(self,signum,deadline):
        if os.name=='posix':
            live=self.refresh(deadline,known_only=True)
            live={} if live is None else {pid:row for pid,row in live.items() if row[3][:1] not in ('Z','X')}
            groups={row[1] for pid,row in live.items() if row[1] in live}
            # Popen owns this session even before the first ps snapshot returns.
            if self.process.poll() is None:groups.add(self.process.pid)
            for group in groups:
                try:os.killpg(group,signum)
                except ProcessLookupError:pass
            for pid,row in live.items():
                if row[1] in groups:continue
                try:os.kill(pid,signum)
                except ProcessLookupError:pass
        elif os.name=='nt':
            remaining=deadline-time.monotonic()
            if remaining>0 and self.process.poll() is None:
                try:subprocess.run(['taskkill','/PID',str(self.process.pid),'/T','/F'],capture_output=True,timeout=min(remaining,.5))
                except (OSError,subprocess.TimeoutExpired):self.process.kill()
        elif self.process.poll() is None:self.process.kill()

    def stopped(self,deadline):
        owner_stopped=self.process.poll() is not None
        live=self.refresh(deadline,known_only=True)
        return owner_stopped and live is not None and not any(row[3][:1] not in ('Z','X') for row in live.values())

    def stop(self,deadline):
        self.refresh(deadline)
        self.signal(signal.SIGTERM,deadline)
        remaining=max(0,deadline-time.monotonic())
        grace=min(deadline-min(.2,remaining*.4),time.monotonic()+.3)
        while time.monotonic()<grace:
            if self.stopped(grace):return True
            time.sleep(min(.02,max(0,grace-time.monotonic())))
        self.signal(signal.SIGKILL if os.name=='posix' else signal.SIGTERM,deadline)
        # kill() delivery and owner exit do not prove that a detached renderer
        # has stopped executing. Confirm every tracked birth identity, not just
        # the direct worker, before releasing the global PDF slot.
        while time.monotonic()<deadline:
            if self.stopped(deadline):return True
            time.sleep(min(.01,max(0,deadline-time.monotonic())))
        return False



def _render_pdf(html, browser_channel, *, timeout=PDF_TOTAL_TIMEOUT):
    """One wall deadline includes the queue, Chrome, printing and cleanup."""
    if browser_channel not in ('chrome','chromium'):raise ValueError('Неверный браузер PDF')
    if isinstance(timeout,bool) or not isinstance(timeout,(int,float)) or not math.isfinite(timeout) or not 0<timeout<=120:
        raise ValueError('Общий срок PDF: больше нуля и до 120 секунд')
    deadline=time.monotonic()+timeout
    if not _pdf_lock.acquire(timeout=max(0,deadline-time.monotonic())):
        raise TimeoutError('Очередь PDF не освободилась в пределах общего срока. Повторите запрос.')
    process=None;tree=None
    try:
        reserve=min(PDF_CLEANUP_RESERVE,timeout*.4)
        execution_deadline=deadline-reserve
        if execution_deadline<=time.monotonic():raise TimeoutError('Общий срок PDF истёк в очереди. Повторите запрос.')
        environment=os.environ.copy()
        # Playwright debug output can contain set_content input; never enable it
        # for a private publication worker, even if the parent has DEBUG set.
        for key in ('DEBUG','PWDEBUG'):environment.pop(key,None)
        try:
            process=subprocess.Popen(_pdf_worker_command(browser_channel),stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                     start_new_session=os.name=='posix',env=environment,bufsize=0)
        except OSError:raise ValueError('Не удалось запустить изолированный процесс PDF') from None
        tree=_PdfOwnedProcesses(process)
        tree.refresh(execution_deadline)
        first=True;payload=html.encode('utf-8')
        while True:
            remaining=execution_deadline-time.monotonic()
            if remaining<=0:raise TimeoutError('Создание PDF превысило общий срок. Очистка Chrome может быть неполной; повторите запрос после проверки.')
            try:
                content,errors=process.communicate(input=payload if first else None,timeout=min(.15,remaining))
                break
            except subprocess.TimeoutExpired as pending:
                if pending.stdout and len(pending.stdout)>MAX_ATTACHMENT:raise ValueError('Созданный PDF превышает 40 МБ')
                if pending.stderr and len(pending.stderr)>65536:raise ValueError('Изолированный процесс PDF завершился с ошибкой')
                tree.refresh(execution_deadline)
            finally:first=False
        if process.returncode:
            try:code=json.loads(errors[-4096:]).get('error')
            except (ValueError,AttributeError):code=None
            messages={'dependency':'Для PDF установите .venv/bin/python -m pip install -r cli/requirements-browser.txt',
                      'validation':'Проверьте статический HTML перед созданием PDF',
                      'too_large':'Созданный PDF превышает 40 МБ; уменьшите число или размер фотографий',
                      'invalid_pdf':'Chrome не вернул PDF'}
            raise ValueError(messages.get(code,'Chrome не смог создать PDF. Повторите запрос или проверьте установленный браузер.')+' Очистка Chrome может быть неполной.')
        if not content.startswith(b'%PDF-'):raise ValueError('Chrome не вернул PDF')
        if len(content)>MAX_ATTACHMENT:raise ValueError('Созданный PDF превышает 40 МБ; уменьшите число или размер фотографий')
        try:completion=json.loads(errors.splitlines()[-1])
        except (ValueError,IndexError):completion={}
        if (not isinstance(completion,dict) or completion.get('protocol')!='redcn-pdf-v1' or completion.get('complete') is not True
                or type(completion.get('bytes')) is not int or completion['bytes']!=len(content)
                or completion.get('sha256')!=hashlib.sha256(content).hexdigest()):
            raise ValueError('Процесс PDF не подтвердил завершение печати и очистки. Очистка Chrome может быть неполной.')
        if time.monotonic()>=execution_deadline:raise TimeoutError('Создание PDF превысило общий срок. Очистка Chrome может быть неполной; повторите запрос после проверки.')
        return content
    finally:
        try:
            if tree is not None and not tree.stop(deadline):
                raise ValueError('Срок PDF истёк, но остановка собственного Chrome не подтверждена. Проверьте процессы перед повторным запросом.')
        finally:
            if process is not None:
                # Never call an unbounded communicate() in cleanup: an orphaned
                # renderer may retain a pipe even after its direct owner exits.
                for stream in (process.stdin,process.stdout,process.stderr):
                    if stream is not None:
                        try:stream.close()
                        except OSError:pass
            _pdf_lock.release()


@_serialize_files
def _persist_export(directory, result_id, content, kind, mime, *, policy_version=None, template=None):
    # The shared file lock protects only bytes + database references. Rendering
    # runs outside it so phones can still fetch contracts during a PDF job.
    digest = _write(directory, content)
    export_id = secrets.token_hex(16)
    stamp = time.time()
    name = 'ciliev-' + result_id[:8] + ('-' + kind + '.png' if kind in ('intro', 'outro') else '.' + kind)
    with _db(directory) as connection:
        connection.execute('INSERT INTO exports VALUES (?,?,?,?,?,?,?,?)', (export_id, result_id, kind, name, mime, len(content), stamp, digest))
        if policy_version:
            connection.execute('INSERT INTO export_publication_policy VALUES (?,?,?)',(export_id,policy_version,digest))
        if template is not None:
            connection.execute('INSERT INTO export_design_snapshots(export_id,payload,created) VALUES(?,?,?)',(export_id,json.dumps(template,ensure_ascii=False,allow_nan=False),stamp))
    return {'id': export_id, 'result_id': result_id, 'kind': kind, 'name': name, 'mime': mime, 'bytes': len(content), 'created': stamp,
            'publicationPolicyVersion':policy_version,'requiresRegeneration':not bool(policy_version)}


def _export_template(template):
    if template is None:return None
    from publication_schema import validate_publication_template
    value=validate_publication_template(template)
    if len(json.dumps(value,ensure_ascii=False,allow_nan=False).encode())>MAX_TEMPLATE_V2:raise ValueError('Шаблон превышает 256 КБ')
    return value


def create_export(directory, result_id, html, kind, browser_channel='chrome', *, template=None):
    from client_publication import PUBLICATION_POLICY_VERSION
    card=_publication_card(directory, result_id,private=True)
    template=_export_template(template)
    if kind not in ('html', 'pdf'):
        raise ValueError('Формат экспорта: html/pdf')
    validate_html(html)
    _assert_public_html(html,card)
    content = html.encode('utf-8') if kind == 'html' else _render_pdf(html, browser_channel)
    mime = 'text/html; charset=utf-8' if kind == 'html' else 'application/pdf'
    return _persist_export(directory, result_id, content, kind, mime, policy_version=PUBLICATION_POLICY_VERSION,template=template)


@_serialize_files
def export_asset(directory, export_id):
    _id(export_id)
    with _db(directory) as connection:
        row = connection.execute('SELECT card_id,digest,mime,name FROM exports WHERE id=?', (export_id,)).fetchone()
    if not row:
        raise ValueError('Экспорт не найден')
    _card(directory, row[0])
    from client_publication import PUBLICATION_POLICY_VERSION
    with _db(directory) as connection:
        policy=connection.execute('SELECT version,digest FROM export_publication_policy WHERE export_id=?',(export_id,)).fetchone()
    if not policy or policy[0] != PUBLICATION_POLICY_VERSION or policy[1] != row[1]:
        raise ValueError('Материал создан до действующего правила публикации. Создайте его заново в разделе «Дизайн»; исходный файл сохранён.')
    return _read(directory, row[1]), row[2], row[3]


def list_exports(directory, result_id):
    _card(directory, result_id)
    with _db(directory) as connection:
        rows = connection.execute('SELECT e.id,e.card_id,e.kind,e.name,e.mime,e.size,e.created,p.version,p.digest,e.digest FROM exports e LEFT JOIN export_publication_policy p ON p.export_id=e.id WHERE card_id=? ORDER BY created DESC,e.id DESC', (result_id,)).fetchall()
    from client_publication import PUBLICATION_POLICY_VERSION
    return [{**dict(zip(('id', 'result_id', 'kind', 'name', 'mime', 'bytes', 'created'), row[:7])),
             'publicationPolicyVersion':row[7],
             'requiresRegeneration':row[7] != PUBLICATION_POLICY_VERSION or row[8] != row[9]} for row in rows]


def create_image_export(directory, result_id, data, kind, *, publication_policy_version=None,template=None):
    from client_publication import PUBLICATION_POLICY_VERSION
    _publication_card(directory, result_id)
    template=_export_template(template)
    if publication_policy_version != PUBLICATION_POLICY_VERSION:
        raise ValueError('Обновите страницу «Дизайн» и создайте изображение заново по действующему правилу публикации.')
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
    try:
        # Canvas images normally have no text chunks. Drop any pasted PNG
        # comments/EXIF as well; they are not part of the client presentation.
        image.info.clear()
        output=io.BytesIO();image.save(output,format='PNG');content=output.getvalue()
    finally:image.close()
    return _persist_export(directory, result_id, content, kind, 'image/png', policy_version=PUBLICATION_POLICY_VERSION,template=template)
