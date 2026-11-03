"""Bind client materials to one saved card and its observed photo identities.

This is a revision check, not OCR or approval of arbitrary raster contents.
Old exports remain stored but cannot silently enter a current client package.
"""
import hashlib
import hmac
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import sqlite3
import secrets
import tempfile


class ClientRevisionConflict(ValueError):
    code='CLIENT_REVISION_CHANGED'


def _canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


def _revision_key(directory):
    # Public revision tokens must not act as a dictionary oracle for private
    # commission/notes fields that also participate in freshness checks.
    from workspace_ownership import owned_path
    folder=owned_path(directory,'card-assets');folder.mkdir(mode=0o700,parents=True,exist_ok=True)
    path=owned_path(directory,'card-assets/revision-key')
    if not path.exists():
        temporary=None
        try:
            with tempfile.NamedTemporaryFile(dir=folder,prefix='revision-key-',delete=False) as stream:
                temporary=Path(stream.name);temporary.chmod(0o600)
                stream.write(secrets.token_bytes(32));stream.flush();os.fsync(stream.fileno())
            try:os.link(temporary,path)
            except FileExistsError:pass
        finally:
            if temporary is not None:temporary.unlink(missing_ok=True)
    info=path.stat()
    if info.st_size!=32 or info.st_mode&0o077:raise ValueError('Недоступен приватный ключ ревизии материалов')
    return path.read_bytes()


@contextmanager
def _readonly(path):
    path=Path(path)
    if path.is_symlink() or not path.is_file():raise ValueError('Недоступна база карточки')
    db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=10)
    try:
        db.execute('PRAGMA query_only=ON')
        yield db
    finally:db.close()


def snapshot(directory,card_id,*,expected_card=None):
    if not isinstance(card_id,str) or not re.fullmatch(r'[a-f0-9]{32}',card_id):raise ValueError('Неверный ID карточки')
    root=Path(directory)
    with _readonly(root/'listings.sqlite3') as db:row=db.execute('SELECT payload FROM results WHERE id=?',(card_id,)).fetchone()
    if not row or len(row[0].encode())>8_000_000:raise ValueError('Карточка не найдена или слишком велика')
    card=json.loads(row[0])
    if expected_card is not None and _canonical(card)!=_canonical(expected_card):
        raise ClientRevisionConflict('Карточка изменилась после сохранения. Загрузите актуальную карточку и повторите экспорт.')
    urls=set();ids=set()
    def visit(value,depth=0):
        if depth>30:raise ValueError('Слишком глубокая карточка')
        if isinstance(value,dict):
            for key,item in value.items():
                if key in {'photoId','originalId','assetId'} and isinstance(item,str) and re.fullmatch(r'[a-f0-9]{64}',item):ids.add(item)
                visit(item,depth+1)
        elif isinstance(value,list):
            for item in value:visit(item,depth+1)
        elif isinstance(value,str) and value.startswith(('https://','http://')) and len(value)<=4096:urls.add(value)
    visit(card)
    if len(urls)>5000:raise ValueError('Слишком много ссылок в карточке')
    media=[];path=root/'media.sqlite3'
    if path.exists():
        with _readonly(path) as db:
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'photo_urls' in tables:
                for url in sorted(urls):
                    found=db.execute('SELECT id FROM photo_urls WHERE url=?',(url,)).fetchone()
                    if found:ids.add(found[0]);media.append(['url',url,found[0]])
            if 'assets' in tables and ids:
                for identifier,mime,raw in db.execute('SELECT id,mime,metadata FROM assets ORDER BY id'):
                    if len(raw)>100_000:raise ValueError('Слишком большие сведения фото')
                    metadata=json.loads(raw)
                    if identifier in ids or metadata.get('originalId') in ids:media.append(['asset',identifier,mime,raw])
            if 'crop_links' in tables and ids:
                for item in db.execute('SELECT original_id,asset_id,verification,metadata FROM crop_links ORDER BY original_id,asset_id,verification'):
                    if item[0] in ids:media.append(['crop',*item])
    return hmac.new(_revision_key(directory),_canonical({'card':card,'media':media}).encode(),hashlib.sha256).hexdigest()


def require_current(directory,card_id,expected):
    if not isinstance(expected,str) or not re.fullmatch(r'[a-f0-9]{64}',expected) or snapshot(directory,card_id)!=expected:
        raise ClientRevisionConflict('Карточка или фотографии изменились. Обновите макет и создайте материал заново; прежний файл сохранён.')


def source_receipt(directory,card_id,revision):
    """Issue only alongside a card payload read or saved under this revision.

    The export-list revision is a freshness hint, not proof of what a renderer
    loaded. This separate, domain-bound receipt follows the loaded/saved card
    through the browser's rendering boundary. It does not inspect arbitrary
    user-supplied HTML or raster pixels.
    """
    require_current(directory,card_id,revision)
    payload={'version':1,'cardId':card_id,'revisionSha256':revision}
    signature=hmac.new(_revision_key(directory),b'redcn-presentation-source-v1\0'+_canonical(payload).encode(),hashlib.sha256).hexdigest()
    return {**payload,'signature':signature}


def verify_source_receipt(directory,card_id,revision,receipt):
    if not isinstance(receipt,dict) or set(receipt)!={'version','cardId','revisionSha256','signature'}:
        raise ClientRevisionConflict('Загрузите карточку заново: экспорт не связан с загруженным содержимым.')
    signature=receipt.get('signature')
    if type(receipt.get('version')) is not int or receipt['version']!=1 or receipt.get('cardId')!=card_id or receipt.get('revisionSha256')!=revision or not isinstance(signature,str) or not re.fullmatch(r'[a-f0-9]{64}',signature):
        raise ClientRevisionConflict('Карточка изменилась. Загрузите её заново и повторите экспорт.')
    expected=source_receipt(directory,card_id,revision)
    if not hmac.compare_digest(signature,expected['signature']):
        raise ClientRevisionConflict('Загрузите карточку заново: подтверждение исходного содержимого недействительно.')


def ensure_table(db):
    db.execute('CREATE TABLE IF NOT EXISTS export_card_revisions (export_id TEXT PRIMARY KEY, card_id TEXT NOT NULL, revision_sha TEXT NOT NULL, artifact_sha TEXT NOT NULL)')


def certify(db,export_id,card_id,revision,artifact_sha):
    ensure_table(db)
    db.execute('INSERT INTO export_card_revisions VALUES (?,?,?,?)',(export_id,card_id,revision,artifact_sha))


def export_status(directory,export_id,card_id,artifact_sha,*,current=None):
    path=Path(directory)/'card-assets/dossiers.sqlite3'
    with _readonly(path) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='export_card_revisions'").fetchone():
            return {'revisionStatus':'unverified','revisionSha256':None,'requiresRegeneration':True}
        row=db.execute('SELECT card_id,revision_sha,artifact_sha FROM export_card_revisions WHERE export_id=?',(export_id,)).fetchone()
    if not row or row[0]!=card_id or row[2]!=artifact_sha:
        return {'revisionStatus':'unverified','revisionSha256':None,'requiresRegeneration':True}
    current=current or snapshot(directory,card_id)
    return {'revisionStatus':'current' if row[1]==current else 'stale','revisionSha256':row[1],
            'requiresRegeneration':row[1]!=current}
