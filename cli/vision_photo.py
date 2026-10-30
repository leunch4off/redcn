"""Local visual evidence. Classifier suggestions require human confirmation."""
import base64
from contextlib import contextmanager
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

_compile_lock = threading.Lock()
ANALYSIS_VERSION = 2
LABELS = {
    'wardrobe': ('Шкаф', ('wardrobe', 'armoire')),
    'desk': ('Рабочий стол', ('desk', 'writing desk')),
    'table': ('Стол', ('dining table', 'dining_table', 'table')),
    'chairs': ('Стулья', ('chair', 'dining chair', 'dining_chair')),
    'sofa': ('Диван', ('sofa', 'couch', 'settee')),
    'armchair': ('Кресло', ('armchair', 'recliner')),
    'bedAny': ('Кровать', ('bed', 'bedstead')),
    'hood': ('Вытяжка', ('range hood', 'range_hood', 'extractor hood')),
    'oven': ('Духовой шкаф', ('oven', 'wall oven', 'kitchen oven')),
    'stove': ('Варочная панель', ('stove', 'cooktop', 'hob', 'gas stove')),
    'fridge': ('Холодильник', ('refrigerator', 'fridge', 'icebox')),
    'microwave': ('Микроволновая печь', ('microwave', 'microwave oven')),
    'dishwasher': ('Посудомоечная машина', ('dishwasher', 'dishwashing machine')),
    'washer': ('Стиральная машина', ('washing machine', 'washer')),
    'tv': ('Телевизор', ('television', 'television set', 'tv', 'tv monitor')),
    'computer': ('Компьютер', ('computer', 'desktop computer', 'laptop', 'notebook computer')),
    'piano': ('Пианино', ('piano', 'grand piano', 'upright piano')),
    'sink': ('Раковина', ('sink', 'washbasin', 'kitchen sink')),
    'closet': ('Гардеробная', ('walk-in closet', 'walk in closet')),
}


def valid_region(raw):
    if not isinstance(raw, list) or len(raw) != 4:
        return None
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in raw):
        return None
    x, y, width, height = raw
    return raw if 0 <= x < 1 and 0 <= y < 1 and 0 < width <= 1-x+.00001 and 0 < height <= 1-y+.00001 else None


def classifier_candidates(rows):
    """Merge tile labels by feature; overlapping tiles never inflate counts."""
    found = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        confidence = row.get('confidence')
        if type(confidence) not in (int, float) or not math.isfinite(confidence) or not .42 <= confidence <= 1:
            continue
        label = str(row.get('label', '')).strip().lower().replace('_', ' ')
        # Composite identifiers can include synonyms, but broad labels like
        # furniture/appliance/kitchen cannot prove a particular object.
        synonyms = {s.strip() for s in label.split(',')}
        region = valid_region(row.get('region'))
        if region is None:
            continue
        for feature, (name, aliases) in LABELS.items():
            if not synonyms.intersection(a.replace('_', ' ') for a in aliases):
                continue
            evidence = {'label': row['label'], 'confidence': confidence, 'region': region, 'kind': 'classification-region'}
            current = found.setdefault(feature, {'id': feature, 'label': name, 'confidence': confidence, 'count': None, 'status': 'candidate', 'evidence': []})
            current['confidence'] = max(current['confidence'], confidence)
            if evidence not in current['evidence']:
                current['evidence'].append(evidence)
    return sorted(found.values(), key=lambda row: (-row['confidence'], row['id']))


def object_candidates(raw):
    """Validate a local multimodal model response; never trust unknown IDs."""
    found = {}
    for row in raw.get('objects', []) if isinstance(raw, dict) and isinstance(raw.get('objects'), list) else []:
        if not isinstance(row, dict) or row.get('id') not in LABELS or row.get('visible') is not True:
            continue
        confidence = row.get('confidence')
        if type(confidence) not in (float, int) or not math.isfinite(confidence) or not .65 <= confidence <= 1:
            continue
        count = row.get('count')
        if type(count) is not int or not 1 <= count <= 12:
            count = None
        feature = row['id']
        evidence = str(row.get('evidence', ''))[:300].strip()
        if not evidence:
            continue
        current = found.get(feature)
        if current and current['confidence'] >= confidence:
            continue
        found[feature] = {'id': feature, 'label': LABELS[feature][0], 'confidence': confidence, 'count': count, 'status': 'candidate', 'evidence': [{'text': evidence, 'kind': 'model-visual-observation'}]}
    return list(found.values())


@contextmanager
def _native_compile_guard(deadline=None, cancel=None):
    acquired=False
    try:
        while not acquired:
            if cancel is not None and cancel.is_set():raise ValueError('Анализ отменён')
            if deadline is not None and deadline<=time.monotonic():raise ValueError('Время анализа фото истекло')
            acquired=_compile_lock.acquire(timeout=.05)
        if cancel is not None and cancel.is_set():raise ValueError('Анализ отменён')
        if deadline is not None and deadline<=time.monotonic():raise ValueError('Время анализа фото истекло')
        yield
    finally:
        if acquired:_compile_lock.release()


def _native_classify(image, directory, *, deadline=None, cancel=None):
    if sys.platform != 'darwin' or not Path('/usr/bin/swiftc').exists():
        raise ValueError('Локальное распознавание доступно на Mac с Apple Vision. Можно настроить локальную модель REDCN_VISION_MODEL в Ollama.')
    folder = Path(directory) / 'vision'
    folder.mkdir(parents=True, exist_ok=True)
    source = Path(__file__).with_name('classify-photo.swift')
    binary = folder / ('classifier-' + hashlib.sha256(source.read_bytes()).hexdigest()[:16])
    with _native_compile_guard(deadline,cancel):
        if not binary.exists():
            if cancel is not None and cancel.is_set():raise ValueError('Анализ отменён')
            remaining=120 if deadline is None else max(.01,min(120,deadline-time.monotonic()))
            result = subprocess.run(['/usr/bin/swiftc', str(source), '-o', str(binary)], capture_output=True, timeout=remaining)
            if result.returncode:
                raise ValueError('Не удалось собрать Apple Vision. Проверьте Command Line Tools.')
    with tempfile.TemporaryDirectory(dir=folder) as temp:
        path = Path(temp) / 'photo.jpg'
        preview = image.convert('RGB');preview.thumbnail((1600,1600));preview.save(path, 'JPEG', quality=92);preview.close()
        if cancel is not None and cancel.is_set():raise ValueError('Анализ отменён')
        if deadline is not None and deadline<=time.monotonic():raise ValueError('Время анализа фото истекло')
        remaining=60 if deadline is None else min(60,deadline-time.monotonic())
        result = subprocess.run([str(binary), str(path)], capture_output=True, text=True, timeout=remaining)
        if result.returncode:
            raise ValueError('Apple Vision не смог обработать фото')
        return classifier_candidates(json.loads(result.stdout)), 'Apple Vision · локально'



def native_classify(image, directory, **options):
    try:return _native_classify(image, directory, **options)
    except subprocess.TimeoutExpired as error:
        raise ValueError('Apple Vision не завершил анализ вовремя. Повторите для одного фото.') from error
    except (subprocess.SubprocessError, json.JSONDecodeError) as error:
        raise ValueError('Apple Vision вернул некорректный результат анализа.') from error


def ollama_classify(image, model):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}', model):
        raise ValueError('Неверное имя локальной модели REDCN_VISION_MODEL')
    preview = image.convert('RGB');preview.thumbnail((1280,1280));buffer = io.BytesIO();preview.save(buffer,'JPEG',quality=90);preview.close()
    prompt = ('Inspect ONLY visibly identifiable furniture and appliances in this rental photo. '
        'Do not infer hidden appliances from an oven or kitchen cabinetry. Do not infer height, room count, availability for tenancy or property permissions. '
        'Return JSON with objects [{id, visible:true, confidence:0..1, count:integer or null, evidence:short Russian explanation of visible identifying detail}]. '
        'Count only clearly distinct visible objects in THIS photo; no guessed counts. Omit uncertain or hidden items. Allowed IDs: ' + ', '.join(LABELS))
    body = json.dumps({'model':model,'stream':False,'format':'json','prompt':prompt,'images':[base64.b64encode(buffer.getvalue()).decode()], 'options': {'temperature':0}, 'keep_alive':'2m'}).encode()
    request = urllib.request.Request('http://127.0.0.1:11434/api/generate', data=body, headers={'Content-Type':'application/json'})
    try:
        # Fixed loopback endpoint; redirects and environment proxying disabled.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs): return None
        with urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open(request, timeout=120) as response:
            raw = response.read(1_000_001)
        if len(raw)>1_000_000:raise ValueError('Ответ локальной модели слишком большой')
        payload=json.loads(raw);objects=json.loads(payload.get('response',''))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, TypeError, AttributeError) as error:
        raise ValueError('Локальная модель недоступна или вернула неверный JSON. Убедитесь, что Ollama запущена и модель с поддержкой изображений уже установлена.') from error
    return object_candidates(objects), 'Ollama · '+model+' · локально'


def analyze_image(image, directory, *, content=None, mime='image/jpeg', cancel=None, deadline=None):
    from photo_ai_settings import load_config
    if cancel is not None and cancel.is_set():raise ValueError('Анализ отменён')
    if deadline is not None and deadline<=time.monotonic():raise ValueError('Время анализа фото истекло')
    config=load_config(directory)
    fallback=None
    if config['enabled']:
        from photo_ai_runtime import ensure_started
        ensure_started(directory,config,deadline=deadline,cancel=cancel)
        import local_photo_ai
        if content is None:
            buffer=io.BytesIO();image.convert('RGB').save(buffer,'JPEG',quality=92);content=buffer.getvalue();mime='image/jpeg'
        outcome=local_photo_ai.analyze_photo(content,mime,model=config['model'],endpoint=config['endpoint'],
            timeout=120,deadline=deadline,cancel=cancel)
        if cancel is not None and cancel.is_set():raise ValueError('Анализ отменён')
        if outcome['status']=='ok':
            return {**outcome,'version':ANALYSIS_VERSION,'status':'review',
                'autoApply':config['autoApply'],
                'message':'Предметы с прямым визуальным подтверждением добавляются как видимые на фото. Сомнительные находки и количество требуют проверки; отсутствие предметов не определяется.',
                'limitations':['Оценка модели не является гарантией точности.', 'Работоспособность, размеры, интернет и условия аренды не определяются по фото.']}
        fallback=outcome
        if cancel is not None and cancel.is_set():raise ValueError('Анализ отменён')
    try:
        if deadline is not None and deadline<=time.monotonic():raise ValueError('Время анализа фото истекло')
        candidates,engine=native_classify(image,directory,deadline=deadline,cancel=cancel)
        return {'version':ANALYSIS_VERSION,'status':'review','provider':'apple-vision','engine':engine,'candidates':candidates,
            'autoApply':False,'fallback':bool(fallback),
            **({'fallbackReason':fallback['message'],'fallbackStatus':fallback['status']} if fallback else {}),
            'message':'Классификатор Apple предложил предметы для проверки. Эти подсказки ещё не добавлены в описание; отсутствие предметов не определяется.',
            'limitations':['Классификация области не гарантирует наличие предмета.', 'Духовой шкаф не подтверждает скрытую плиту, холодильник или СВЧ.']}
    except (ValueError,OSError) as error:
        if cancel is not None and cancel.is_set():raise ValueError('Анализ отменён') from error
        return {'version':ANALYSIS_VERSION,'status':'unavailable','provider':'none','engine':'Распознавание недоступно',
            'candidates':[],'autoApply':False,'fallback':bool(fallback),
            **({'fallbackReason':fallback['message'],'fallbackStatus':fallback['status']} if fallback else {}),
            'message':fallback['message'] if fallback else str(error),
            'limitations':['Предметы не проверены. Это не означает, что их нет.']}
