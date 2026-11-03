"""Optional saved-post review by a different authenticated workspace actor.

This ledger never posts to a service or edits a card. The current client
projection is hashed inside the same database transaction as each review.
"""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3


class PublicationReviewConflict(ValueError):
    pass


def _now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00','Z')


def _actor(actor):
    if not isinstance(actor,str) or not actor.strip() or len(actor)>200 or re.search(r'[\x00-\x1f\x7f]',actor):
        raise ValueError('Сервер не определил участника проверки')
    return actor.strip()


def _connect(directory):
    path=Path(directory)/'listings.sqlite3'
    if not path.is_file() or path.is_symlink():raise ValueError('Сохранённая карточка не найдена')
    db=sqlite3.connect(path,timeout=15);db.row_factory=sqlite3.Row
    db.executescript('''
      CREATE TABLE IF NOT EXISTS publication_reviews (
        card_id TEXT PRIMARY KEY, revision INTEGER NOT NULL, state TEXT NOT NULL,
        post_hash TEXT NOT NULL, submitted_by TEXT NOT NULL, submitted_at TEXT NOT NULL,
        reviewed_by TEXT, reviewed_at TEXT, comment TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS publication_review_events (
        card_id TEXT NOT NULL, revision INTEGER NOT NULL, at TEXT NOT NULL,
        actor TEXT NOT NULL, action TEXT NOT NULL, post_hash TEXT NOT NULL,
        comment TEXT NOT NULL, PRIMARY KEY(card_id,revision)
      );
    ''');db.commit();return db


def _current(db,card_id):
    if not isinstance(card_id,str) or not re.fullmatch(r'[a-f0-9]{32}',card_id):raise ValueError('Неверный ID карточки')
    try:row=db.execute('SELECT updated,payload FROM results WHERE id=?',(card_id,)).fetchone()
    except sqlite3.OperationalError as error:raise ValueError('Сохранённая карточка не найдена') from error
    if not row:raise ValueError('Сохранённая карточка не найдена')
    try:card=json.loads(row['payload'])
    except (ValueError,TypeError) as error:raise ValueError('Сохранённая карточка повреждена') from error
    if not isinstance(card,dict) or not isinstance(card.get('fields'),dict) or card['fields'].get('isDemo') is True:raise ValueError('Нужна настоящая сохранённая карточка')
    from client_publication import client_card
    projected=client_card(card)
    # Transport and persistence metadata are not the saved post's content.
    for key in ('id','updated','mediaStatus','photoProcessing'):projected.pop(key,None)
    encoded=json.dumps(projected,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
    return row['updated'],hashlib.sha256(encoded).hexdigest()


def _snapshot(db,card_id,actor):
    updated,current_hash=_current(db,card_id)
    row=db.execute('SELECT * FROM publication_reviews WHERE card_id=?',(card_id,)).fetchone()
    entry=dict(row) if row else None
    state='not-submitted' if not row else row['state'] if row['post_hash']==current_hash else 'stale'
    return {'version':1,'cardId':card_id,'revision':row['revision'] if row else 0,
        'savedUpdated':updated,'currentHash':current_hash,'state':state,
        'review':entry,'canReview':bool(row and row['state']=='pending' and state=='pending' and row['submitted_by']!=actor),
        'history':[dict(item) for item in db.execute('SELECT revision,at,actor,action,post_hash,comment FROM publication_review_events WHERE card_id=? ORDER BY revision DESC LIMIT 100',(card_id,))]}


def snapshot(directory,card_id,*,actor):
    actor=_actor(actor);db=_connect(directory)
    try:return _snapshot(db,card_id,actor)
    finally:db.close()


def mutate(directory,card_id,data,*,actor):
    actor=_actor(actor)
    if not isinstance(data,dict) or set(data)-{'action','expectedRevision','expectedUpdated','comment'}:raise ValueError('Нужны действие, версия проверки и версия сохранённой карточки')
    action=data.get('action');revision=data.get('expectedRevision');updated=data.get('expectedUpdated');comment=data.get('comment','')
    if action not in ('submit','approve','reject') or type(revision) is not int or revision<0:raise ValueError('Неизвестное действие или версия проверки')
    if type(updated) not in (int,float) or not 0<=updated<=10_000_000_000_000:raise ValueError('Обновите версию сохранённой карточки')
    if not isinstance(comment,str) or len(comment)>1000 or re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]',comment):raise ValueError('Комментарий: до 1000 символов')
    comment=comment.strip()
    if action=='reject' and not comment:raise ValueError('Укажите причину возврата на доработку')
    db=_connect(directory)
    try:
        db.execute('BEGIN IMMEDIATE');before=_snapshot(db,card_id,actor)
        if before['revision']!=revision or before['savedUpdated']!=updated:raise PublicationReviewConflict('Карточка или проверка изменились. Сначала обновите состояние.')
        now=_now();next_revision=revision+1;entry=before['review']
        if action=='submit':
            db.execute('INSERT OR REPLACE INTO publication_reviews VALUES (?,?,?,?,?,?,?,?,?)',(card_id,next_revision,'pending',before['currentHash'],actor,now,None,None,comment))
        else:
            if not entry or entry['state']!='pending':raise PublicationReviewConflict('Сначала отправьте сохранённый пост на проверку')
            if entry['submitted_by']==actor:raise ValueError('Свой пост проверяет другой подключённый коллега')
            if before['state']=='stale':raise PublicationReviewConflict('Содержание поста изменилось. Автор должен повторно отправить текущую версию.')
            db.execute('UPDATE publication_reviews SET revision=?,state=?,reviewed_by=?,reviewed_at=?,comment=? WHERE card_id=?',(next_revision,'approved' if action=='approve' else 'rejected',actor,now,comment,card_id))
        db.execute('INSERT INTO publication_review_events VALUES (?,?,?,?,?,?,?)',(card_id,next_revision,now,actor,action,before['currentHash'],comment));db.commit()
        return _snapshot(db,card_id,actor)
    except Exception:
        db.rollback();raise
    finally:db.close()


def dispatch(directory,path,data=None,*,actor):
    match=re.fullmatch(r'publication-review/([a-f0-9]{32})',path)
    if not match:return None
    try:
        result=snapshot(directory,match[1],actor=actor) if data is None else mutate(directory,match[1],data,actor=actor)
        return 200,result,'application/json; charset=utf-8',{}
    except PublicationReviewConflict as error:return 409,{'error':str(error)},'application/json; charset=utf-8',{}
    except (ValueError,TypeError) as error:return 400,{'error':str(error)},'application/json; charset=utf-8',{}
