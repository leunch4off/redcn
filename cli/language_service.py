"""Shared, authenticated-caller language routes and optimistic text updates.

Node rendering happens outside a SQLite transaction. A full payload comparison
protects concurrent text/photo edits; private dossiers are separate and untouched.
"""
import copy
from collections import OrderedDict
import hashlib
import json
from pathlib import Path
import platform
import re
import subprocess
import threading
import time

import redcn
from node_runtime import resolve_node


PARTS = ('title', 'body', 'tags')
LIMITS = {'title': 240, 'body': 20_000, 'tags': 3000}
MAX_SOURCE = 20_000
_capabilities = OrderedDict()
_capability_lock = threading.RLock()


class LanguageConflict(ValueError):
    pass


class MissingCard(ValueError):
    pass


def _text(value, limit, label, *, empty=False):
    if not isinstance(value, str) or len(value) > limit or '\x00' in value:
        raise ValueError(label + ': превышен размер или неверный текст')
    value = value.strip()
    if not value and not empty:
        raise ValueError(label + ': введите текст')
    return value


def _part(value, *, all_parts=False):
    allowed = (*PARTS, 'all') if all_parts else PARTS
    if value not in allowed:
        raise ValueError('Выберите заголовок, описание или теги')
    return value


def _profile(directory):
    from phone_relay import load_profile
    return load_profile(Path(directory))


def node_language(payload):
    encoded = json.dumps(payload, ensure_ascii=False)
    if len(encoded.encode('utf-8')) > 1_000_000:
        raise ValueError('Запрос перевода превышает ограничение размера')
    try:
        result = subprocess.run([resolve_node(), str(redcn.ROOT/'cli/language.mjs')],
            input=encoded, capture_output=True, text=True, encoding='utf-8',
            check=False, timeout=60)
    except subprocess.TimeoutExpired as error:
        raise ValueError('Обработка текста не завершилась за 60 секунд. Повторите запрос.') from error
    except OSError as error:
        raise ValueError('Не удалось запустить обработчик текста на Mac') from error
    if result.returncode:
        message = result.stderr.strip()
        raise ValueError(message[:600] or 'Не удалось обработать текст')
    if len(result.stdout.encode('utf-8')) > 3_000_000:
        raise ValueError('Ответ обработчика текста превышает ограничение размера')
    try:
        value = json.loads(result.stdout)
    except ValueError as error:
        raise ValueError('Обработчик текста вернул неверный ответ') from error
    if not isinstance(value, dict):
        raise ValueError('Обработчик текста вернул неверный ответ')
    return value


def capabilities(directory):
    profile = _profile(directory)
    watched = [redcn.ROOT/path for path in ('cli/language.mjs', 'web/engine.mjs', 'web/template-catalog.mjs',
        'web/community-phrases.mjs',
        'web/ru-zh-translator.mjs', 'web/rental-lexicon.mjs', 'data/ru-zh-open-lexicon.json',
        'data/moscow-universities.json', 'web/university-catalog.mjs')]
    signature = []
    for path in watched:
        try:
            stat = path.stat(); signature.append((str(path), stat.st_mtime_ns, stat.st_size))
        except OSError:
            signature.append((str(path), None))
    key = hashlib.sha256(json.dumps([str(Path(directory).resolve()), profile, signature], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    with _capability_lock:
        cached = _capabilities.get(key)
        if cached and time.monotonic()-cached[0] < 60:
            _capabilities.move_to_end(key)
            return copy.deepcopy(cached[1])
        value = node_language({'action': 'capabilities', 'rules': profile.get('rules', {})})
        import university_store
        value['universities'] = university_store.stats(directory)
        _capabilities[key] = (time.monotonic(), value)
        while len(_capabilities) > 8:
            _capabilities.popitem(last=False)
        return copy.deepcopy(value)


def translate(directory, data):
    text = _text(data.get('text'), MAX_SOURCE, 'Русский текст')
    part = data.get('part', 'body')
    _part(part)
    profile = _profile(directory)
    started = time.monotonic()
    preview = node_language({'action': 'translate', 'text': text, 'part': part,
        'mode': 'draft', 'rules': profile.get('rules', {})})
    if not preview.get('unknown'):
        return preview
    warnings = list(preview.get('warnings') or [])
    if len(text) > 5000:
        warnings.append('Для машинного перевода разбейте текст на фрагменты до 5000 символов. Неизвестные фразы требуют проверки.')
        return {**preview, 'warnings': warnings}
    remaining = min(52, 60-(time.monotonic()-started))
    if remaining <= 3:
        return {**preview, 'warnings': [*warnings, 'Время обработки истекло. Показан перевод знакомых фраз; проверьте остальной текст.']}
    # Canonical university names remain exact even when their Chinese label is
    # editorial. This preserves a proper name, not a claim that it was reviewed.
    glossary = {match['ru']: match['zh'] for match in preview.get('matches', [])
        if isinstance(match, dict) and (match.get('reviewed') is True or match.get('source')=='university-catalog')
        and isinstance(match.get('ru'), str) and isinstance(match.get('zh'), str)}
    glossary.update(profile.get('rules', {}))
    try:
        machine = machine_translate(text, glossary, remaining)
    except (ValueError, OSError, subprocess.TimeoutExpired):
        machine = None
        warnings.append('Машинный перевод не завершился. Показан перевод знакомых фраз; неизвестные фрагменты сохранены для проверки.')
    if machine is None:
        if not warnings or 'Машинный перевод не завершился' not in warnings[-1]:
            warnings.append('Полный машинный перевод пока не установлен на сервере. Неизвестные фрагменты оставлены на русском для проверки.')
        return {**preview, 'warnings': warnings, 'needsReview': True}
    output = machine.get('text')
    if not isinstance(output, str) or not output.strip() or len(output)>20_000:
        return {**preview, 'warnings': [*warnings, 'Машинный перевод вернул неполный ответ. Проверьте русский текст.'], 'needsReview': True}
    residual = list(dict.fromkeys(re.findall(r'[А-Яа-яЁё]+(?:[-’\'][А-Яа-яЁё]+)*', output)))
    return {**preview, **machine, 'text': output, 'unknown': residual,
        'sourceCoverage': preview.get('coverage'), 'coverage': machine.get('coverage'),
        'warnings': list(dict.fromkeys([*warnings, *(machine.get('warnings') or []),
            'Машинный перевод через английский: проверьте условия, числа и названия перед сохранением.'])),
        'backend': 'nmt-pivot', 'needsReview': True}


def machine_translate(text, rules, timeout):
    """Optional isolated model runtime; no package installation during requests."""
    runtime = redcn.ROOT/'.venv-translate/bin/python'
    worker = redcn.ROOT/'cli/nmt_worker.py'
    if not runtime.is_file() or not worker.is_file():
        return None
    # The optional macOS model runtime is installed as ARM64. An x86 launchd
    # parent must not choose the other slice of its universal Python executable.
    command = (['/usr/bin/arch', '-arm64'] if platform.system()=='Darwin' else [])+[str(runtime), str(worker)]
    started = time.monotonic()
    status = subprocess.run([*command, '--status'], capture_output=True,
        text=True, encoding='utf-8', check=False, timeout=min(3, timeout))
    if status.returncode or len(status.stdout)>100_000:
        return None
    try:
        state = json.loads(status.stdout)
    except ValueError:
        return None
    if not isinstance(state, dict) or not (state.get('ready') is True or state.get('available') is True):
        return None
    result = subprocess.run(command, input=json.dumps({'text': text, 'rules': rules}, ensure_ascii=False),
        capture_output=True, text=True, encoding='utf-8', check=False,
        timeout=max(1, timeout-(time.monotonic()-started)))
    if result.returncode or len(result.stdout)>500_000:
        raise ValueError('Модель не вернула полный перевод')
    value = json.loads(result.stdout)
    if not isinstance(value, dict):
        raise ValueError('Модель вернула неверный ответ')
    return value


def search(directory, data):
    query = _text(data.get('query'), 200, 'Поисковый запрос')
    scope = data.get('scope', 'all')
    if scope not in ('all', 'open', 'rental', 'geo', 'custom', 'universities'):
        raise ValueError('Неизвестная область словаря')
    limit, offset = data.get('limit', 40), data.get('offset', 0)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError('Размер страницы: от 1 до 100')
    if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 1_000_000:
        raise ValueError('Неверная позиция страницы')
    if scope=='universities':
        import university_store
        return university_store.search(directory, query, limit=limit, offset=offset)
    return node_language({'action': 'search', 'query': query, 'scope': scope,
        'limit': limit, 'offset': offset, 'rules': _profile(directory).get('rules', {})})


def _read(directory, card_id):
    if not isinstance(card_id, str) or not re.fullmatch(r'[a-f0-9]{32}', card_id):
        raise ValueError('Неверный ID карточки')
    connection = redcn.open_cache(Path(directory))
    try:
        connection.execute('CREATE TABLE IF NOT EXISTS results (id TEXT PRIMARY KEY, updated REAL, payload TEXT)')
        row = connection.execute('SELECT payload FROM results WHERE id=?', (card_id,)).fetchone()
    finally:
        connection.close()
    if not row:
        raise MissingCard('Карточка не найдена')
    try:
        card = json.loads(row[0])
    except ValueError as error:
        raise ValueError('Сохранённая карточка повреждена') from error
    if not isinstance(card, dict) or not isinstance(card.get('fields', {}), dict):
        raise ValueError('Сохранённая карточка повреждена')
    if card.get('fields', {}).get('isDemo') is True:
        raise MissingCard('Учебная карточка недоступна для этой операции')
    return card, row[0]


def _save(directory, card_id, original_payload, card):
    encoded = json.dumps(card, ensure_ascii=False)
    connection = redcn.open_cache(Path(directory))
    try:
        changed = connection.execute('UPDATE results SET payload=?, updated=max(?, '
            '(SELECT COALESCE(MAX(updated),0)+0.000001 FROM results)) WHERE id=? AND payload=?',
            (encoded, time.time(), card_id, original_payload)).rowcount
        if not changed:
            connection.rollback()
            raise LanguageConflict('Карточка изменилась во время обработки. Обновите её и повторите действие.')
        connection.commit()
    finally:
        connection.close()
    from phone_relay import public_card
    result = public_card(Path(directory), card_id)
    if result is None:
        raise MissingCard('Карточка не найдена')
    return result


def _variant(value):
    if isinstance(value, bool):
        return 0
    try:
        return max(0, min(2**31-1, int(value)))
    except (ValueError, TypeError, OverflowError):
        return 0


def rewrite(directory, card_id, data):
    part = _part(data.get('part', 'all'), all_parts=True)
    previous, original_payload = _read(directory, card_id)
    fields = copy.deepcopy(previous.get('fields', {}))
    old_info = previous.get('templateInfo') if isinstance(previous.get('templateInfo'), dict) else {}
    saved_indices = old_info.get('indices') if isinstance(old_info.get('indices'), dict) else {}
    old_title = _variant(fields.get('titleVariant'))
    indices = {'title': _variant(saved_indices.get('title', old_title)),
        'description': _variant(saved_indices.get('description', fields.get('copyVariant', old_title))),
        'tags': _variant(saved_indices.get('tags', fields.get('tagVariant', old_title)))}
    targets = PARTS if part == 'all' else (part,)
    index_names = {'title': 'title', 'body': 'description', 'tags': 'tags'}
    counters = {'title': 'titleVariant', 'body': 'copyVariant', 'tags': 'tagVariant'}
    for target in targets:
        name = index_names[target]
        indices[name] = (indices[name]+1) % (2**31)
    render_fields = {**fields, 'templateMode': 'catalog',
        'titleVariant': indices['title'], 'copyVariant': indices['description'], 'tagVariant': indices['tags']}
    profile = _profile(directory)
    generated = node_language({'action': 'generate', 'fields': render_fields,
        'rules': profile.get('rules', {}), 'prefs': profile.get('prefs', {})})
    if generated.get('problems'):
        raise ValueError(' · '.join(str(problem) for problem in generated['problems'])[:600])
    card = copy.deepcopy(previous)
    for target in targets:
        if target == 'title':
            titles = generated.get('titles')
            if not isinstance(titles, list) or not titles or not all(isinstance(title, str) for title in titles):
                raise ValueError('Генератор не вернул заголовки')
            card['titles'] = [_text(title, LIMITS['title'], 'Заголовок') for title in titles[:3]]
        else:
            card[target] = _text(generated.get(target), LIMITS[target], 'Описание' if target=='body' else 'Теги', empty=target=='tags')
        fields[counters[target]] = indices[index_names[target]]
    fields['templateMode'] = 'catalog'
    card['fields'] = fields
    new_info = generated.get('templateInfo') if isinstance(generated.get('templateInfo'), dict) else {}
    info = {**copy.deepcopy(old_info), 'indices': indices}
    for key in ('version', 'mode', 'counts'):
        if key in new_info:
            info[key] = new_info[key]
    id_names = {'title': ('titleId','titleIds'), 'body': ('descriptionId',), 'tags': ('tagId',)}
    for target in targets:
        for key in id_names[target]:
            if key in new_info:
                info[key] = new_info[key]
            else:
                info.pop(key, None)
    if isinstance(info.get('manualParts'), list):
        info['manualParts'] = [name for name in info['manualParts'] if name not in targets]
    card['templateInfo'] = info
    return _save(directory, card_id, original_payload, card)


def edit_text(directory, card_id, data):
    part = _part(data.get('part'))
    text = _text(data.get('text'), LIMITS[part], 'Текст карточки', empty=part=='tags')
    source = _text(data['sourceRu'], MAX_SOURCE, 'Русский черновик', empty=True) if 'sourceRu' in data else None
    previous, original_payload = _read(directory, card_id)
    card = copy.deepcopy(previous)
    if part == 'title':
        existing = card.get('titles') if isinstance(card.get('titles'), list) else []
        card['titles'] = [text, *[title for title in existing if isinstance(title,str) and title!=text]][:3]
    else:
        card[part] = text
    if part=='body' and source is not None:
        card.setdefault('fields', {})['description'] = source
    info = copy.deepcopy(card.get('templateInfo')) if isinstance(card.get('templateInfo'), dict) else {}
    for key in {'title': ('titleId','titleIds'), 'body': ('descriptionId',), 'tags': ('tagId',)}[part]:
        info.pop(key, None)
    manual = [name for name in info.get('manualParts', []) if name in PARTS] if isinstance(info.get('manualParts'), list) else []
    info['manualParts'] = list(dict.fromkeys([*manual, part]))
    card['templateInfo'] = info
    return _save(directory, card_id, original_payload, card)


def dispatch(directory, path, data=None):
    """Caller must already enforce desktop token or paired session plus CSRF."""
    catalog = re.fullmatch(r'language/catalog/(title|description|tags)', path)
    if path not in ('language', 'language/search', 'translate', 'universities', 'universities/search') and not catalog and not re.fullmatch(r'cards/[a-f0-9]{32}/(?:rewrite|edit-text)', path):
        return None
    try:
        if path=='universities' and data is None:
            import university_store
            result = university_store.stats(directory)
        elif path=='universities/search' and data is not None:
            import university_store
            result = university_store.search(directory, data.get('query'), kind=data.get('kind', 'all'),
                limit=data.get('limit', 40), offset=data.get('offset', 0))
        elif catalog and data is None:
            filename = catalog[1]+'.ndjson.gz'
            artifact = redcn.ROOT/'data/template-catalog'/filename
            if not artifact.is_file():
                raise MissingCard('Каталог шаблонов ещё не подготовлен')
            return 200, artifact.read_bytes(), 'application/gzip', {'Content-Disposition': 'attachment; filename="redcn-'+filename+'"'}
        elif path=='language' and data is None:
            result = capabilities(directory)
        elif path=='language/search' and data is not None:
            result = search(directory, data)
        elif path=='translate' and data is not None:
            result = translate(directory, data)
        else:
            match = re.fullmatch(r'cards/([a-f0-9]{32})/(rewrite|edit-text)', path)
            if not match or data is None:
                return None
            result = (rewrite if match[2]=='rewrite' else edit_text)(directory, match[1], data)
        return 200, result, 'application/json; charset=utf-8', {}
    except LanguageConflict as error:
        return 409, {'error': str(error)}, 'application/json; charset=utf-8', {}
    except MissingCard as error:
        return 404, {'error': str(error)}, 'application/json; charset=utf-8', {}
