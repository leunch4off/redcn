"""Private configuration for local image inference; never stores API secrets."""
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit

DEFAULT_MODEL = 'qwen3-vl:2b-instruct-q4_K_M'
DEFAULTS = {'enabled': False, 'model': DEFAULT_MODEL,
            'endpoint': 'http://127.0.0.1:11434', 'autoApply': True, 'maxSeconds': 600}


def validate_config(value):
    if not isinstance(value, dict) or set(value) != set(DEFAULTS):
        raise ValueError('Неверные настройки локального анализа фото')
    if type(value['enabled']) is not bool or type(value['autoApply']) is not bool:
        raise ValueError('Включение анализа задаётся переключателем')
    model = value['model']
    if (not isinstance(model, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}', model)
            or 'cloud' in model.lower() or '://' in model):
        raise ValueError('Выберите установленную локальную модель с изображениями')
    endpoint = value['endpoint']
    try:
        url = urlsplit(endpoint)
        valid = (isinstance(endpoint, str) and len(endpoint) <= 200
                 and url.scheme == 'http' and url.hostname in ('127.0.0.1', '::1')
                 and url.port is not None and 1 <= url.port <= 65535
                 and url.username is None and url.password is None and url.path in ('', '/')
                 and not url.query and not url.fragment and not any(ord(c) <= 32 for c in endpoint))
    except (ValueError, TypeError, AttributeError):
        valid = False
    if not valid:
        raise ValueError('Модель должна работать только на локальном IP компьютера')
    if type(value['maxSeconds']) is not int or not 60 <= value['maxSeconds'] <= 1800:
        raise ValueError('Предел анализа фотографий: от 60 до 1800 секунд')
    return {**value, 'endpoint': endpoint.rstrip('/')}


def load_config(directory):
    path = Path(directory) / 'photo-ai.json'
    if path.is_symlink():
        return {**DEFAULTS, 'enabled': False}
    try:
        if path.stat().st_size > 16384:
            return {**DEFAULTS, 'enabled': False}
        return validate_config(json.loads(path.read_text(encoding='utf-8')))
    except FileNotFoundError:
        model = os.environ.get('REDCN_VISION_MODEL', '').strip()
        if model:
            try:
                return validate_config({**DEFAULTS, 'enabled': True, 'model': model,
                    'endpoint': os.environ.get('REDCN_VISION_ENDPOINT', DEFAULTS['endpoint']).strip()})
            except ValueError:
                pass
    except (OSError, ValueError, UnicodeError):
        pass
    return dict(DEFAULTS)


def save_config(directory, updates):
    if not isinstance(updates, dict) or not updates or not set(updates) <= set(DEFAULTS):
        raise ValueError('Неверные поля настроек локальной модели')
    config = validate_config({**load_config(directory), **updates})
    folder = Path(directory)
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = folder / 'photo-ai.json'
    if path.is_symlink():
        raise ValueError('Настройки не должны быть символической ссылкой')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=folder, delete=False) as output:
            temporary = Path(output.name)
            temporary.chmod(0o600)
            json.dump(config, output, ensure_ascii=False)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return config
