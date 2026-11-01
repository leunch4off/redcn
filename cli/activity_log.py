"""Bounded, typed activity receipts. Never stores raw exceptions, HTTP or DOM."""
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import urlsplit


STAGES=frozenset(('preflight','fetch','store','navigate','navigation','browser','challenge','scroll','extract','generate','photos','vision','crop','save','source','total'))


def safe_error(error):
    kind=type(error).__name__ if isinstance(error,BaseException) else str(error or '')
    text=str(error or '')
    match=re.search(r'\bHTTP\s+(\d{3})\b',text,re.I)
    if match:return 'http_'+match[1],'Источник вернул HTTP '+match[1]+'. Проверьте доступ; автоматический обход не выполняется.'
    if 'NeedsAttention' in kind or 'SEARCH_NEEDS_ATTENTION' in text:return 'manual_required','Источник требует ручной проверки; автоматический обход не выполняется.'
    if 'Timeout' in kind:return 'timeout','Истёк срок ожидания источника.'
    if re.search(r'лимит|предел',text,re.I):return 'capacity','База достигла лимита сохранённых записей; автоматического удаления нет.'
    if 'Chrome занят' in text:return 'browser_busy','Chrome занят ручной операцией. Проверка будет повторена позже.'
    if re.search(r'остановлен|cancelled',text,re.I):return 'cancelled','Операция остановлена.'
    if re.search(r'URLError|Connection|OSError|SSLError|gaierror',kind):return 'network','Не удалось получить ответ по сети.'
    if isinstance(error,ValueError):return 'invalid_response','Источник вернул неподтверждённый или некорректный ответ. Проверьте разметку и настройки.'
    return 'operation_error','Операция не завершена. Подробности требуют отдельной приватной диагностики.'


def _db(directory):
    root=Path(directory);root.mkdir(parents=True,exist_ok=True);path=root/'activity-log.sqlite3'
    if path.is_symlink():raise ValueError('Журнал не может быть ссылкой')
    db=sqlite3.connect(path,timeout=10);db.row_factory=sqlite3.Row;path.chmod(0o600)
    db.execute('''CREATE TABLE IF NOT EXISTS activity (
      id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, kind TEXT NOT NULL,
      source TEXT NOT NULL, correlation TEXT NOT NULL, outcome TEXT NOT NULL,
      elapsed REAL NOT NULL, stages TEXT NOT NULL, error_code TEXT NOT NULL, reason TEXT NOT NULL)''')
    return db


def record_attempt(directory,*,kind,source_url='',started=None,elapsed=0,outcome='done',stages=None,error=None):
    if kind not in ('import','watch') or outcome not in ('done','error','needs_attention','cancelled','partial'):raise ValueError('Неверный тип технической записи')
    if type(elapsed) not in (int,float) or not math.isfinite(elapsed) or not 0<=elapsed<=7200:raise ValueError('Неверная длительность записи')
    at=time.time() if started is None else started
    if type(at) not in (int,float) or not math.isfinite(at) or not 0<=at<=10_000_000_000_000:raise ValueError('Неверное время записи')
    try:
        parsed=urlsplit(source_url);host=(parsed.hostname or '').lower()
        source='cian' if host=='cian.ru' or host.endswith('.cian.ru') else 'avito' if host in ('avito.ru','www.avito.ru') else 'unknown'
        canonical=host+parsed.path if source!='unknown' and parsed.scheme=='https' and not parsed.username and not parsed.password else ''
    except (TypeError,ValueError):source='unknown';canonical=''
    correlation=hashlib.sha256(canonical.encode()).hexdigest()[:20] if canonical else ''
    timings={}
    for key,value in (stages.items() if isinstance(stages,dict) else []):
        if key in STAGES and type(value) in (int,float) and math.isfinite(value) and 0<=value<=7200:timings[key]=round(value,6)
    code,reason=safe_error(error) if error is not None else ('','')
    with _db(directory) as db:
        cursor=db.execute('INSERT INTO activity(at,kind,source,correlation,outcome,elapsed,stages,error_code,reason) VALUES (?,?,?,?,?,?,?,?,?)',(at,kind,source,correlation,outcome,round(elapsed,6),json.dumps(timings),code,reason))
        return cursor.lastrowid


def record_import_attempt(directory,**kwargs):
    return record_attempt(directory,kind='import',**kwargs)


def list_attempts(directory,*,kind='all',limit=50,before=None):
    if kind not in ('all','import','watch') or isinstance(limit,bool):raise ValueError('Неверный фильтр журнала')
    try:limit=int(limit);before=int(before) if before is not None else None
    except (TypeError,ValueError):raise ValueError('Неверная страница журнала') from None
    if not 1<=limit<=100 or before is not None and before<1:raise ValueError('Неверная страница журнала')
    where=['1=1'];args=[]
    if kind!='all':where.append('kind=?');args.append(kind)
    if before is not None:where.append('id<?');args.append(before)
    with _db(directory) as db:rows=db.execute('SELECT * FROM activity WHERE '+' AND '.join(where)+' ORDER BY id DESC LIMIT ?',(*args,limit+1)).fetchall()
    more=len(rows)>limit;rows=rows[:limit];items=[]
    for row in rows:items.append({**dict(row),'stages':json.loads(row['stages'])})
    return {'items':items,'hasMore':more,'nextBefore':rows[-1]['id'] if more and rows else None}
