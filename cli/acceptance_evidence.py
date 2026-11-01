"""Private, append-only records of actual operator acceptance checks.

An authenticated report is evidence supplied by an operator. It never changes
the roadmap, proves a physical device from a user-agent, certifies a reviewer,
or sends a message. The HTTP entrypoints enforce the workspace/session first.
"""
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import secrets
import sqlite3
import time
from urllib.parse import urlsplit, urlunsplit

MAX_RECORDS = 2000
MAX_BYTES = 32_000
TASKS = {
    'RCN-170': {'title': 'Редактор и словарь с клавиатурой', 'checks': {
        'editor_keyboard': 'Ввод и исправление текста в редакторе',
        'dictionary_keyboard': 'Поиск и редактирование словаря с клавиатурой',
        'focus_visible': 'Поле и место ввода остаются видны',
        'no_horizontal_pan': 'Не требуется горизонтальная прокрутка'}},
    'RCN-224': {'title': 'Доступ к Mac через мобильную сеть', 'checks': {
        'external_https': 'Открыт доверенный внешний HTTPS-адрес',
        'cellular_access': 'Wi-Fi выключен; ответ получен через мобильную сеть',
        'session_authorization': 'Свои карточки доступны после входа; без входа закрыты'}},
    'RCN-225': {'title': 'Физический iPhone 14 Pro', 'checks': {
        'safari_install': 'Добавлено на экран «Домой» из Safari',
        'home_launch': 'Приложение открыто с установленной иконки',
        'system_photos': 'Проверено сохранение изображения в системное «Фото»',
        'keyboard': 'Проверен ввод текста с экранной клавиатурой',
        'local_mac_access': 'Проверен доступ к Mac в своей сети'}},
    'RCN-247': {'title': 'Тот же адрес после перезапуска Mac', 'checks': {
        'stable_origin': 'До перезапуска записан постоянный внешний origin',
        'mac_reboot': 'Выполнен именно перезапуск Mac, а не только сервера',
        'home_same_origin': 'После перезапуска прежняя иконка открыла тот же origin',
        'no_reinstall': 'Повторная установка приложения не понадобилась'}},
    'RCN-262': {'title': 'Независимая редактура носителем китайского', 'checks': {
        'real_samples': 'Проверены реальные объявления и переводы',
        'native_reviewer': 'Рецензент подтвердил владение китайским как родным',
        'independent_reviewer': 'Проверку выполнил отдельный человек',
        'facts_preserved': 'Правки сохраняют подтверждённые факты',
        'corrections_recorded': 'Ошибки и предложенные правки зафиксированы'}},
}


class EvidenceConflict(ValueError):
    pass


def _text(value, name, limit, *, required=False):
    if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 and c not in '\n\t' for c in value):
        raise ValueError('Неверное поле: ' + name)
    value = value.strip()
    if required and not value:
        raise ValueError('Заполните поле: ' + name)
    return value


def _origin(value):
    value = _text(value, 'origin', 300, required=True)
    try:
        url = urlsplit(value)
        valid = (url.scheme in ('http', 'https') and url.hostname and not url.username and not url.password
                 and url.path in ('', '/') and not url.query and not url.fragment
                 and not any(c.isspace() for c in value) and (url.port is None or 1 <= url.port <= 65535))
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise ValueError('Укажите origin без пути, кода, логина и параметров')
    return urlunsplit((url.scheme, url.netloc.lower(), '', '', ''))


def _client(value):
    allowed = {'origin', 'userAgent', 'standalone', 'secureContext', 'viewportWidth',
               'viewportHeight', 'visualViewportHeight', 'reducedViewportObserved',
               'shareAvailable', 'fileShareAvailable'}
    if not isinstance(value, dict) or set(value) != allowed:
        raise ValueError('Нужны сведения браузера без дополнительных полей')
    out = {'origin': _origin(value['origin']), 'userAgent': _text(value['userAgent'], 'браузер', 400)}
    for key in ('standalone', 'secureContext', 'reducedViewportObserved', 'shareAvailable', 'fileShareAvailable'):
        if type(value[key]) is not bool:
            raise ValueError('Неверное наблюдение браузера: ' + key)
        out[key] = value[key]
    for key in ('viewportWidth', 'viewportHeight', 'visualViewportHeight'):
        if type(value[key]) not in (int, float) or not 1 <= value[key] <= 20_000:
            raise ValueError('Неверный размер окна: ' + key)
        out[key] = round(value[key], 2)
    return out


def validate_report(data):
    required = {'action', 'eventId', 'taskId', 'device', 'client', 'checks', 'notes'}
    if not isinstance(data, dict) or set(data) - (required | {'reviewer'}) or not required <= set(data):
        raise ValueError('Неверные поля свидетельства проверки')
    if data['action'] != 'record':
        raise ValueError('Допустима только запись свидетельства')
    if not isinstance(data['eventId'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{12,80}', data['eventId']):
        raise ValueError('Нужен идентификатор повторяемого запроса')
    task_id = data['taskId']
    if not isinstance(task_id, str) or task_id not in TASKS:
        raise ValueError('Неизвестная проверка')
    if not isinstance(data['device'], dict) or set(data['device']) != {'model', 'browser', 'os', 'network'}:
        raise ValueError('Укажите устройство, браузер, ОС и сеть')
    names = {'model': 'модель устройства', 'browser': 'браузер', 'os': 'операционная система'}
    device = {key: _text(data['device'][key], names[key], 160, required=key == 'model')
              for key in ('model', 'browser', 'os')}
    if data['device']['network'] not in ('wifi', 'cellular', 'other', 'unknown'):
        raise ValueError('Выберите тип сети; браузер не определяет его надёжно')
    device['network'] = data['device']['network']
    checks = data['checks']
    if not isinstance(checks, dict) or set(checks) != set(TASKS[task_id]['checks']):
        raise ValueError('Заполните все строки выбранной проверки')
    if any(not isinstance(value, str) or value not in ('pass', 'fail', 'not-run') for value in checks.values()):
        raise ValueError('Результат: пройдено, ошибка или не проверено')
    if all(value == 'not-run' for value in checks.values()):
        raise ValueError('Сначала выполните хотя бы одну проверку')
    if task_id == 'RCN-224' and checks['cellular_access'] == 'pass' and device['network'] != 'cellular':
        raise ValueError('Для подтверждения мобильной сети явно выберите её в условиях проверки')
    result = {'eventId': data['eventId'], 'taskId': task_id, 'device': device,
              'client': _client(data['client']), 'checks': dict(checks),
              'notes': _text(data['notes'], 'наблюдения и ссылки на примеры', 4000, required=True)}
    if task_id == 'RCN-262':
        reviewer = data.get('reviewer')
        if not isinstance(reviewer, dict) or set(reviewer) != {'name', 'relationship', 'nativeSpeakerReported'}:
            raise ValueError('Укажите, кто выполнил редактуру')
        if reviewer['relationship'] not in ('independent-person', 'same-operator', 'unknown') or type(reviewer['nativeSpeakerReported']) is not bool:
            raise ValueError('Неверные сведения рецензента')
        result['reviewer'] = {'name': _text(reviewer['name'], 'имя или псевдоним рецензента', 160, required=True),
                              'relationship': reviewer['relationship'],
                              'nativeSpeakerReported': reviewer['nativeSpeakerReported']}
        if checks['native_reviewer'] == 'pass' and not reviewer['nativeSpeakerReported']:
            raise ValueError('Подтверждение родного языка рецензента не указано')
        if checks['independent_reviewer'] == 'pass' and reviewer['relationship'] != 'independent-person':
            raise ValueError('Независимая редактура требует отдельного человека')
    elif 'reviewer' in data:
        raise ValueError('Рецензент относится только к языковой проверке')
    return result


def _folder(directory):
    base = Path(directory)
    folder = base/'acceptance-evidence'
    if base.is_symlink() or folder.is_symlink() or (folder/'records.sqlite3').is_symlink():
        raise ValueError('Папка свидетельств не должна быть ссылкой')
    return folder


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _rows(directory):
    path = _folder(directory)/'records.sqlite3'
    if not path.exists():
        return []
    with sqlite3.connect(path.absolute().as_uri()+'?mode=ro', uri=True, timeout=10) as db:
        records = db.execute('SELECT id,created,actor_hash,payload,payload_sha FROM reports ORDER BY created DESC,rowid DESC LIMIT ?', (MAX_RECORDS,)).fetchall()
    output = []
    for record_id, created, author, raw, digest in records:
        if (not isinstance(record_id, str) or not re.fullmatch(r'[a-f0-9]{32}', record_id)
                or not isinstance(author, str) or not re.fullmatch(r'[a-f0-9]{64}', author)
                or type(created) not in (int, float) or not math.isfinite(created) or not 0 <= created <= 100_000_000_000
                or not isinstance(raw, str) or len(raw.encode()) > MAX_BYTES):
            raise ValueError('Неверная запись журнала; исходный файл сохранён')
        if hashlib.sha256(raw.encode()).hexdigest() != digest:
            raise ValueError('Нарушена целостность свидетельства; исходный файл сохранён')
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError('Неверное содержимое свидетельства')
        validate_report({'action': 'record', **value})
        output.append({'id': record_id, 'recordedAt': datetime.fromtimestamp(created, timezone.utc).isoformat(),
                       'actorHash': author, 'payloadSha256': digest, **value,
                       'source': 'authenticated-operator-report', 'verification': 'operator-reported',
                       'roadmapChanged': False})
    return output


def overview(directory, *, all_records=False):
    rows = _rows(directory)
    return {'schemaVersion': 1, 'tasks': TASKS, 'records': rows if all_records else rows[:30], 'total': len(rows),
            'roadmapChanged': False,
            'historicalOnlyOnAnotherWorkspace': True,
            'notice': 'Свидетельство оператора сохраняет фактические наблюдения. Оно не заменяет реальную проверку устройства или независимого человека и само не закрывает roadmap.'}


def record(directory, data, *, actor='Mac owner'):
    value = validate_report(data)
    actor = _text(actor, 'автор', 200, required=True)
    author = hashlib.sha256(actor.encode()).hexdigest()
    raw = _canonical(value)
    digest = hashlib.sha256(raw.encode()).hexdigest()
    folder = _folder(directory)
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = folder/'records.sqlite3'
    with sqlite3.connect(path, timeout=10) as db:
        path.chmod(0o600)
        db.execute('CREATE TABLE IF NOT EXISTS reports(id TEXT PRIMARY KEY, created REAL NOT NULL, actor_hash TEXT NOT NULL, event_id TEXT NOT NULL, payload TEXT NOT NULL, payload_sha TEXT NOT NULL, UNIQUE(actor_hash,event_id))')
        db.execute('BEGIN IMMEDIATE')
        old = db.execute('SELECT payload_sha FROM reports WHERE actor_hash=? AND event_id=?', (author, value['eventId'])).fetchone()
        if old:
            if old[0] != digest:
                raise EvidenceConflict('Этот запрос уже записан с другим содержимым')
        else:
            if db.execute('SELECT COUNT(*) FROM reports').fetchone()[0] >= MAX_RECORDS:
                raise ValueError('Достигнут предел свидетельств; экспортируйте журнал перед обслуживанием')
            db.execute('INSERT INTO reports VALUES (?,?,?,?,?,?)', (secrets.token_hex(16), time.time(), author, value['eventId'], raw, digest))
    return overview(directory)


def dispatch(directory, path, data=None, *, actor='Mac owner'):
    if path != 'acceptance-evidence':
        return None
    try:
        value = (overview(directory, all_records=data == {'action': 'export'})
                 if data is None or data == {'action': 'export'} else record(directory, data, actor=actor))
        return 200, value, 'application/json; charset=utf-8', {}
    except EvidenceConflict as error:
        return 409, {'error': str(error)}, 'application/json; charset=utf-8', {}
