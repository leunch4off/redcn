"""Read-only status of configured local AI, without loading model weights.

No application controller, database, pairing file or learning payload is read.
Configuration, installed files and execution are deliberately distinct facts.
"""
import json
from pathlib import Path
import urllib.error
import urllib.request


REVIEW_ONLY_PHOTO_MODELS = {
    'redcn-furniture-v1': ('yolox-trained', 'Мебель v1 · локальные предложения'),
    'redcn-furniture-world-v2': ('onnx-world', 'YOLO-World v2 · локальные предложения'),
}


def _read(path, warnings, *, max_bytes=32_000):
    if path.is_symlink():
        warnings.append(path.name + ': symbolic link rejected')
        return None
    try:
        if path.stat().st_size > max_bytes:
            raise ValueError('configuration is too large')
        value = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(value, dict):
            raise ValueError('configuration must be an object')
        return value
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, UnicodeError):
        warnings.append(path.name + ': cannot read valid configuration')
        return None


def _enabled(value):
    if value is None:
        return None
    flag = value.get('enabled', False)
    return flag if type(flag) is bool else None


def _file(value):
    if not isinstance(value, (str, Path)):
        return False
    name = str(value)
    if not name or len(name) > 4096:
        return False
    try:
        return Path(name).expanduser().is_file()
    except (OSError, ValueError):
        return False


def _text_profile(model_path, python_path, warnings, *, label):
    """Inspect bounded architecture metadata; never open model weights."""
    from text_model_contract import inference_model
    result = {'model': None, 'modelSupported': False, 'modelConfigPresent': False,
              'runtimePresent': _file(python_path)}
    if not isinstance(model_path, (str, Path)) or not str(model_path) or len(str(model_path)) > 4096:
        warnings.append(label + ': invalid model path')
        return result
    try:
        path = Path(model_path).expanduser() / 'config.json'
        result['modelConfigPresent'] = _file(path)
        config = _read(path, warnings, max_bytes=1_000_000)
        if result['modelConfigPresent'] and config is not None:
            result['model'] = inference_model(config)
            result['modelSupported'] = True
    except (OSError, ValueError, TypeError):
        warnings.append(label + ': unsupported model configuration')
    return result


def _photo_settings(root, warnings):
    # Share the runtime validator and its environment fallback, without
    # starting a worker or creating the workspace. A partial/invalid JSON file
    # must not be reported as enabled when the runtime rejects that file.
    from photo_ai_settings import load_config, validate_config
    path = root / 'photo-ai.json'
    value = _read(path, warnings, max_bytes=16_384)
    if value is None:
        return None, 'invalid'
    if not path.exists():
        value = load_config(root)
        return value, 'environment' if value['enabled'] else 'defaults'
    try:
        return validate_config(value), 'file'
    except ValueError:
        warnings.append('photo-ai.json: runtime rejects this configuration')
        return None, 'invalid'


def inspect_ai(directory):
    root = Path(directory).expanduser()
    warnings = []
    if root.is_symlink() or (root / 'learning').is_symlink():
        raise ValueError('AI status does not follow linked workspace directories')
    text = _read(root / 'text-ai.json', warnings, max_bytes=16_000)
    photo, photo_source = _photo_settings(root, warnings)
    training = _read(root / 'learning' / 'scheduler.json', warnings)
    state = _read(root / 'learning' / 'scheduler-state.json', warnings)
    defaults = root / 'tools' / 'text-ai'
    text_model = (text or {}).get('modelPath', str(defaults / 'model'))
    text_runtime = (text or {}).get('pythonPath', str(defaults / 'venv' / 'bin' / 'python'))
    text_profile = _text_profile(text_model, text_runtime, warnings, label='text model')
    adapter_selected = ((text or {}).get('useCandidateAdapter') is True
                        and bool((text or {}).get('adapterPath')))
    separate_advertising = bool((text or {}).get('advertisingModelPath'))
    advertising = (_text_profile((text or {})['advertisingModelPath'],
                   (text or {}).get('advertisingPythonPath') or text_runtime,
                   warnings, label='advertising model') if separate_advertising else dict(text_profile))
    advertising.update(enabled=_enabled(text),
        automaticGenerationEnabled=(text or {}).get('advertisingEnabled') is True,
        separateProfile=separate_advertising,
        candidateAdapterSelected=False if separate_advertising else adapter_selected,
        modelSource='separate-inference-profile' if separate_advertising else 'shared-text-profile',
        weightsTrainingInspected=False)
    auto_apply = (photo or {}).get('autoApply', True) if photo is not None else None
    if type(auto_apply) is not bool:
        auto_apply = None
    phase = (state or {}).get('phase', 'idle')
    if not isinstance(phase, str) or len(phase) > 80:
        phase = 'unknown'
    model = (photo or {}).get('model', 'qwen3-vl:2b-instruct-q4_K_M')
    if not isinstance(model, str) or len(model) > 128:
        model = 'unknown'
    review_model = REVIEW_ONLY_PHOTO_MODELS.get(model)
    automatic_policy = review_model is None
    photo_enabled = _enabled(photo)
    review_capture = photo_enabled is True and auto_apply is True and review_model is not None
    effective_mode = ('unknown' if photo_enabled is None or auto_apply is None else
                      'suggestions-only' if review_capture else
                      'direct-observations' if photo_enabled is True and auto_apply is True and automatic_policy else 'off')
    return {
        'readOnly': True,
        'text': {**text_profile, 'enabled': _enabled(text),
                 'candidateAdapterSelected': adapter_selected,
                 'automaticApplication': False, 'advertising': advertising},
        'photo': {'enabled': photo_enabled, 'model': model,
                  'provider': review_model[0] if review_model else None,
                  'displayName': review_model[1] if review_model else model,
                  'reviewRequired': review_model is not None if photo is not None else None,
                  'acceptedClasses': 0 if review_model and photo is not None else None,
                  'automaticFacts': 0 if review_model and photo is not None else None,
                  'configurationSource': photo_source,
                  'autoApplyConfigured': auto_apply,
                  'automaticReviewCapture': review_capture,
                  'effectiveMode': effective_mode,
                  'modelAllowsAutomaticApplication': automatic_policy if photo is not None else None,
                  'automaticApplication': (auto_apply and automatic_policy) if photo_enabled is True
                                          else False if photo_enabled is False else None},
        'idleTraining': {'enabled': _enabled(training), 'recordedPhase': phase,
                         'automaticPromotion': (training or {}).get('automaticPromotion') is True,
                         'phaseIsLiveProcessProof': False},
        'processActivity': 'not-inspected',
        'warnings': warnings,
        'notes': ['Enabled is configuration, not proof of a running worker or accepted quality.',
                  'Models start for requests and release memory when their worker exits.'],
    }


def server_status(port=8765, *, opener=urllib.request.urlopen):
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('Invalid local server port')
    try:
        with opener('http://127.0.0.1:' + str(port) + '/api/local/health', timeout=2) as response:
            raw = response.read(16_001)
            if len(raw) > 16_000:
                raise ValueError('Health response is too large')
            value = json.loads(raw)
        if not isinstance(value, dict) or value.get('app') != 'redcn':
            raise ValueError('Another service is listening')
        release = value.get('release')
        return {'reachable': True, 'release': release if isinstance(release, str) and len(release) <= 40 else 'unknown',
                'activeJobs': value.get('activeJobs') if type(value.get('activeJobs')) is int else None}
    except (OSError, ValueError):
        return {'reachable': False, 'release': None, 'activeJobs': None}


def russian_status(value):
    flag = lambda v: 'ВКЛЮЧЕНО' if v is True else 'ВЫКЛЮЧЕНО' if v is False else 'ОШИБКА НАСТРОЕК'
    text, photo, training = value['text'], value['photo'], value['idleTraining']
    lines = ['Текстовый ИИ: ' + flag(text['enabled']) + (' · '+text['model'] if text.get('model') else ''),
             'Файлы текстовой модели: ' + ('ЕСТЬ' if text['modelConfigPresent'] else 'НЕ НАЙДЕНЫ'),
             'Среда текстовой модели: ' + ('ЕСТЬ' if text['runtimePresent'] else 'НЕ НАЙДЕНА')]
    advertising = text.get('advertising')
    if advertising:
        lines.extend(['Рекламный ИИ: ' + flag(advertising['enabled']) + ' · ' +
                      (advertising['model'] or 'модель не определена по конфигурации') +
                      (' · отдельный профиль' if advertising['separateProfile'] else ' · общий текстовый профиль'),
                      'Конфигурация рекламной модели: ' + ('ЕСТЬ' if advertising['modelConfigPresent'] else 'НЕ НАЙДЕНА') +
                      ' · среда: ' + ('ЕСТЬ' if advertising['runtimePresent'] else 'НЕ НАЙДЕНА'),
                      'Автоматическое создание описаний: ' + flag(advertising['automaticGenerationEnabled']),
                      'Выбор рекламной модели не означает обучение новых весов.'])
    lines.append('Анализ фото: ' + flag(photo['enabled']) + ' · ' + photo['model'])
    if photo.get('reviewRequired') is True:
        lines.extend([photo['displayName'] + ' · требуется проверка найденных предметов',
                      'Категорий для автоподтверждения: 0 · автоматических фактов: 0'])
    lines.extend(['Автодобавление в журнал проверки: ' + flag(photo.get('automaticReviewCapture', False)),
             'Автоподтверждение для описания: ' + flag(photo['automaticApplication']) +
             (' — модель ещё не прошла приёмку' if photo.get('reviewRequired') is True else ''),
             'Обучение в простое: ' + flag(training['enabled']),
             'Последний записанный этап обучения: ' + training['recordedPhase'],
             'Автоматическое принятие новых весов: ' + flag(training['automaticPromotion']) +
             ' · только после независимого сравнения качества'])
    if 'server' in value:
        server = value['server']
        lines.append('Сервер: ' + (server['release'] or 'версия неизвестна') if server['reachable'] else 'Сервер: НЕ ОТВЕЧАЕТ')
    lines.extend('Настройки: ' + warning for warning in value['warnings'])
    lines.append('Включение, наличие файлов и работающий процесс — разные состояния. В простое модель может не быть в памяти.')
    return '\n'.join(lines)
