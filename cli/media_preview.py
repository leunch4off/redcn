"""Small cover previews; verified originals remain byte-for-byte unchanged."""
import hashlib
import io
import json
from pathlib import Path
import re
import tempfile
import threading

import photo_store


MAX_PREVIEW_BYTES = 120 * 1024
_preview_lock = threading.Lock()


def _atomic(path, content):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as output:
            output.write(content)
            temporary = Path(output.name)
        temporary.chmod(0o600)
        temporary.replace(path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _signature(source):
    if source.is_symlink():
        raise ValueError('Недопустимый путь оригинала фотографии')
    stat = source.stat()
    return [stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]


def preview(directory, digest, width=480):
    """Return JPEG bytes, MIME and metadata, fitting width × 3/4 width.

    The source is decoded and its SHA-256 verified only when the derivative
    must be created. Subsequent requests read a small cached JPEG and validate
    source stat identity, database presence and the derivative checksum.
    """
    if not isinstance(digest, str) or not re.fullmatch(r'[a-f0-9]{64}', digest):
        raise ValueError('Неверный ID фотографии')
    if type(width) is not int or not 64 <= width <= 640:
        raise ValueError('Ширина превью: 64–640 px')
    directory = Path(directory)
    source = directory / 'media' / digest
    with _preview_lock:
        signature = _signature(source)
        with photo_store._connection(directory) as db:
            if not db.execute('SELECT 1 FROM assets WHERE id=?', (digest,)).fetchone():
                raise ValueError('Фотография ещё не загружена')
        folder = directory / 'media-previews'
        if folder.is_symlink():
            raise ValueError('Каталог превью не может быть символической ссылкой')
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        folder.chmod(0o700)
        path = folder / (digest + '-' + str(width) + '-v1.jpg')
        meta_path = path.with_suffix('.json')
        if path.is_symlink() or meta_path.is_symlink():
            raise ValueError('Недопустимый путь превью')
        try:
            saved = json.loads(meta_path.read_text())
            content = path.read_bytes()
            if (saved.get('sourceSignature') == signature and content.startswith(b'\xff\xd8\xff')
                    and len(content) <= MAX_PREVIEW_BYTES
                    and hashlib.sha256(content).hexdigest() == saved.get('digest')):
                return content, 'image/jpeg', saved['metadata']
        except (OSError, ValueError, TypeError, KeyError):
            pass
        content, _, _ = photo_store.asset(directory, digest)
        image = photo_store.open_photo(content)
        Image, _ = photo_store.pillow()
        try:
            image.thumbnail((width, round(width * .75)), Image.Resampling.LANCZOS)
            if image.mode in ('RGBA', 'LA') or 'transparency' in image.info:
                rgba = image.convert('RGBA')
                rgb = Image.new('RGB', image.size, 'white')
                rgb.paste(rgba, mask=rgba.getchannel('A'))
                rgba.close()
            else:
                rgb = image.convert('RGB')
            try:
                for quality in (72, 62, 52, 42):
                    output = io.BytesIO()
                    rgb.save(output, 'JPEG', quality=quality, optimize=True, progressive=True)
                    result = output.getvalue()
                    if len(result) <= MAX_PREVIEW_BYTES:
                        break
                if len(result) > MAX_PREVIEW_BYTES:
                    raise ValueError('Не удалось уменьшить превью фотографии')
                # Detect a source replacement racing generation before writing
                # the stat identity. Do not cache bytes for a different source.
                if _signature(source) != signature:
                    raise ValueError('Оригинал изменился. Повторите загрузку превью')
                metadata = {'sourceId': digest, 'width': rgb.width, 'height': rgb.height,
                            'bytes': len(result), 'kind': 'preview'}
            finally:
                rgb.close()
        finally:
            image.close()
        saved = {'sourceSignature': signature, 'digest': hashlib.sha256(result).hexdigest(), 'metadata': metadata}
        _atomic(path, result)
        _atomic(meta_path, json.dumps(saved, separators=(',', ':')).encode())
        return result, 'image/jpeg', metadata
