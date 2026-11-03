"""Project-authored Chinese advertising structures; never a source of property facts.

Styles deliberately contain no district, equipment, price or rental promise. The
same private SQLite store as reviewed examples keeps rotation across restarts.
"""
import hashlib
import secrets
import unicodedata

from learning_memory import LearningMemory
from text_quality import LISTING_SYSTEM


STYLES = (
    ('budget', '先写预算，再写面积与配置；短句，账目清楚', 'Бюджет и условия'),
    ('transit', '先写已核实的车站与出行，再写房屋和租金', 'Метро и дорога'),
    ('space', '先写面积与已知布局，再写配置和费用', 'Планировка и пространство'),
    ('equipment', '先写已确认的家具家电，再写空间和费用', 'Мебель и техника'),
    ('conditions', '先写租金押金与明确的限制，再介绍房屋', 'Условия аренды'),
    ('notebook', '像认真介绍房源的笔记，条理清楚，少用表情', 'Спокойная заметка'),
    ('checklist', '用疏朗的短行清单；不增加事实，最多四个表情', 'Короткий список'),
    ('compact', '紧凑的房源卡片，标题具体，正文不写空泛夸赞', 'Краткая карточка'),
    ('tour', '从已知空间写到已知配置，像带人看房；最后写费用', 'Знакомство с квартирой'),
    ('questions', '用预算、空间、出行的小标题组织；不虚构回答', 'Ответы на вопросы арендатора'),
    ('editorial', '克制的编辑推荐口吻，突出一个真实亮点，不夸大', 'Сдержанная рекламная подача'),
    ('friendly', '亲切自然地介绍已知信息，长短句交替，不承诺未知条件', 'Дружелюбное описание'),
)

ADVERTISING_SYSTEM = LISTING_SYSTEM.replace('正文最多150字', '正文最多280字') + (
    ' 按指定广告风格组织事实。标题与正文都要有新措辞和结构，不能只换表情或标点。'
    ' 不得虚构全套设备、人数、距离、采光、景观、装修品质、入住日期或免费服务。'
    ' 限制、费用另付和待确认条件保留原句。'
    ' 不引用其他中介身份，不写佣金。不能照抄历史版本。')


def next_style(directory, group_id):
    """Atomic per-property rotation, with global offset for consecutive imports."""
    memory = LearningMemory(directory)
    key = hashlib.sha256(str(group_id).encode('utf-8')).hexdigest()
    with memory._db() as db:
        db.execute('CREATE TABLE IF NOT EXISTS advertising_rotation '
                   '(group_key TEXT PRIMARY KEY, sequence INTEGER NOT NULL, offset INTEGER NOT NULL)')
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT sequence,offset FROM advertising_rotation WHERE group_key=?', (key,)).fetchone()
        if row:
            sequence, offset = int(row['sequence']), int(row['offset'])
            db.execute('UPDATE advertising_rotation SET sequence=? WHERE group_key=?', (sequence + 1, key))
        else:
            sequence = 0
            offset = db.execute('SELECT COUNT(*) FROM advertising_rotation').fetchone()[0] % len(STYLES)
            db.execute('INSERT INTO advertising_rotation VALUES (?,?,?)', (key, 1, offset))
    style = STYLES[(sequence + offset) % len(STYLES)]
    return {'id': style[0], 'instruction': style[1], 'label': style[2],
            'sequence': sequence, 'seed': secrets.randbits(32)}


def style_by_id(style_id):
    return next((row for row in STYLES if row[0] == style_id), STYLES[0])


def variant_fingerprint(value):
    normalized=''.join(c for c in unicodedata.normalize('NFKC',value).casefold() if c.isalnum())
    return hashlib.sha256(normalized.encode()).hexdigest()


def used_variant_fingerprints(directory, group_id):
    """Read the durable novelty index, including variants older than prompts."""
    key=hashlib.sha256(str(group_id).encode()).hexdigest();result={'title':set(),'description':set()}
    with LearningMemory(directory)._db() as db:
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='advertising_history'").fetchone():
            for row in db.execute('SELECT kind,fingerprint FROM advertising_history WHERE group_key=?',(key,)):
                if row['kind'] in result:result[row['kind']].add(row['fingerprint'])
    return result


def claim_variant(directory, group_id, title, body):
    """Reserve both normalized texts atomically; punctuation is not novelty.

    The durable index covers older variants too, beyond the bounded prompt
    history. Concurrent requests cannot both claim an identical description.
    """
    key = hashlib.sha256(str(group_id).encode('utf-8')).hexdigest()
    entries = [('title', variant_fingerprint(title)), ('description', variant_fingerprint(body))]
    memory = LearningMemory(directory)
    with memory._db() as db:
        db.execute('CREATE TABLE IF NOT EXISTS advertising_history '
                   '(group_key TEXT NOT NULL, kind TEXT NOT NULL, fingerprint TEXT NOT NULL, '
                   'PRIMARY KEY(group_key,kind,fingerprint))')
        db.execute('BEGIN IMMEDIATE')
        for kind, digest in entries:
            if db.execute('SELECT 1 FROM advertising_history WHERE group_key=? AND kind=? AND fingerprint=?',
                          (key, kind, digest)).fetchone():
                return False
        db.executemany('INSERT INTO advertising_history VALUES (?,?,?)',
                       [(key, kind, digest) for kind, digest in entries])
    return True
