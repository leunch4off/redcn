"""Pinned first-pass furniture model: bounded local, review-only observations."""
import base64
import hashlib
import io
import json
import math
from pathlib import Path
import subprocess
import sys
import time
import warnings

if __name__ == '__main__' and sys.flags.isolated:
    # -I excludes PYTHONPATH/user-site and the working directory. The only
    # additional application code is this worker's own fixed CLI directory.
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from local_photo_ai import FEATURE_LABELS
from ai_telemetry import sanitize_ai
from job_progress import emit

MODEL = 'redcn-furniture-v1'
MODEL_SHA = '650c6d96cd8b9e0c0422174467590b6ec377bc7fefa6c6ff01b0546db5392de5'
CLASS_IDS = ['sofa', 'bedAny', 'chairs', 'tv', 'fridge', 'oven', 'microwave',
    'sink', 'table', 'desk', 'wardrobe', 'washer', 'dishwasher', 'gasStove',
    'piano', 'cabinetry', 'cupboard', 'coffeeMachine', 'mirror', 'curtains', 'coffeeTable']
SIZE = 416
ROWS = 3549
MAX_BYTES = 12_000_000
MAX_OUTPUT = 1_000_000
ARTIFACT = Path('tools/photo-training/20261101-first-pass/run-01')


def _read(path, limit):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= limit:
        raise ValueError('Файл локальной модели недоступен')
    with path.open('rb') as source:
        content = source.read(limit+1)
    if not 0 < len(content) <= limit:
        raise ValueError('Неверный размер файла локальной модели')
    return content


def load_artifact(directory):
    folder = Path(directory)/ARTIFACT
    if folder.is_symlink():
        raise ValueError('Каталог модели не должен быть ссылкой')
    receipt_bytes = _read(folder/'trainreceipt.json', 2_000_000)
    receipt = json.loads(receipt_bytes)
    onnx = receipt.get('onnx') if isinstance(receipt, dict) else None
    if (receipt.get('status') != 'trained-unaccepted' or not isinstance(onnx, dict)
            or onnx.get('sha256') != MODEL_SHA or onnx.get('path') != 'model.onnx'
            or onnx.get('inputShape') != [1, 3, SIZE, SIZE]
            or onnx.get('outputEncoding') != 'raw-yolox' or onnx.get('classIds') != CLASS_IDS):
        raise ValueError('Не совпадает паспорт обученной модели')
    model = _read(folder/'model.onnx', 8_000_000)
    if hashlib.sha256(model).hexdigest() != MODEL_SHA:
        raise ValueError('Контрольная сумма обученной модели не совпадает')
    return model, hashlib.sha256(receipt_bytes).hexdigest()


def cache_identity(directory):
    try:
        from photo_native_runtime import cache_identity as runtime_identity
        _, receipt_sha = load_artifact(directory)
        return MODEL_SHA+':'+receipt_sha+':'+runtime_identity(directory)
    except (OSError, ValueError, TypeError, AttributeError):
        return 'unavailable'


def _iou(a, b):
    intersection = max(0, min(a[0]+a[2], b[0]+b[2])-max(a[0], b[0])) * max(0, min(a[1]+a[3], b[1]+b[3])-max(a[1], b[1]))
    union = a[2]*a[3]+b[2]*b[3]-intersection
    return intersection/union if union > 0 else 0


def decode(rows, width, height, digest):
    if len(rows) != ROWS or min(width, height) <= 0:
        raise ValueError('Неверный размер результата модели')
    ratio = min(SIZE/width, SIZE/height)
    found, index = [], 0
    for stride in (8, 16, 32):
        for gy in range(SIZE//stride):
            for gx in range(SIZE//stride):
                row = rows[index]; index += 1
                if (len(row) != len(CLASS_IDS)+5
                        or any(type(v) not in (int, float) or not math.isfinite(v) for v in row)
                        or not all(0 <= v <= 1 for v in row[4:])):
                    raise ValueError('Неверные оценки локальной модели')
                class_index = max(range(len(CLASS_IDS)), key=lambda value: row[5+value])
                feature = CLASS_IDS[class_index]
                score = row[4]*row[5+class_index]
                if feature not in FEATURE_LABELS or score < .35 or abs(row[2]) > 20 or abs(row[3]) > 20:
                    continue
                cx, cy = (row[0]+gx)*stride/ratio, (row[1]+gy)*stride/ratio
                bw, bh = math.exp(row[2])*stride/ratio, math.exp(row[3])*stride/ratio
                x1, y1 = max(0, min(width, cx-bw/2)), max(0, min(height, cy-bh/2))
                x2, y2 = max(0, min(width, cx+bw/2)), max(0, min(height, cy+bh/2))
                if x2 <= x1 or y2 <= y1:
                    continue
                found.append({'id': feature, 'confidence': score,
                    'region': [x1/width, y1/height, (x2-x1)/width, (y2-y1)/height]})
    kept = []
    for row in sorted(found, key=lambda item: -item['confidence']):
        if not any(previous['id'] == row['id'] and _iou(previous['region'], row['region']) > .45 for previous in kept):
            kept.append(row)
        if len(kept) == 100:
            break
    groups = {}
    for row in kept:
        candidate = groups.setdefault(row['id'], {'id': row['id'], 'label': FEATURE_LABELS[row['id']],
            'confidence': row['confidence'], 'count': None, 'status': 'candidate',
            'autoEligible': False, 'autoCountEligible': False, 'evidence': []})
        if len(candidate['evidence']) < 8:
            candidate['evidence'].append({'kind': 'object-detection', 'region': row['region'],
                'confidence': row['confidence'], 'photoId': digest, 'originalId': digest,
                'provider': 'yolox-trained', 'model': MODEL, 'source': 'local-photo'})
    return list(groups.values())


def _infer(directory, content, mime):
    model, _ = load_artifact(directory)
    if not isinstance(content, bytes) or not 0 < len(content) <= MAX_BYTES:
        raise ValueError('Нужна фотография до 12 МБ')
    from PIL import Image
    import numpy as np
    import onnxruntime as ort
    expected = {'image/jpeg': 'JPEG', 'image/png': 'PNG', 'image/webp': 'WEBP'}.get(mime)
    with warnings.catch_warnings():
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(content)) as original:
            if (expected is None or original.format != expected or getattr(original, 'n_frames', 1) != 1
                    or original.width*original.height > 40_000_000 or min(original.size) < 32
                    or original.getexif().get(274, 1) not in (1, None)):
                raise ValueError('Нужна одиночная фотография без EXIF-поворота')
            image = original.convert('RGB')
    width, height = image.size
    ratio = min(SIZE/width, SIZE/height)
    resized = image.resize((max(1, int(width*ratio)), max(1, int(height*ratio))), Image.Resampling.BILINEAR)
    array = np.full((SIZE, SIZE, 3), 114, dtype=np.float32)
    array[:resized.height, :resized.width] = np.asarray(resized)[:, :, ::-1]
    image.close(); resized.close()
    tensor = np.ascontiguousarray(array.transpose(2, 0, 1)[None])
    options = ort.SessionOptions(); options.intra_op_num_threads = 2; options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    session = ort.InferenceSession(model, sess_options=options, providers=['CPUExecutionProvider'])
    inputs = session.get_inputs()
    if len(inputs) != 1 or inputs[0].shape != [1, 3, SIZE, SIZE] or inputs[0].type != 'tensor(float)':
        raise ValueError('Неверный вход обученной модели')
    inference_started = time.monotonic()
    outputs = session.run(None, {inputs[0].name: tensor})
    inference_seconds = time.monotonic() - inference_started
    if len(outputs) != 1 or outputs[0].shape != (1, ROWS, len(CLASS_IDS)+5):
        raise ValueError('Неверный выход обученной модели')
    digest = hashlib.sha256(content).hexdigest()
    candidates = decode(outputs[0][0].tolist(), width, height, digest)
    import platform
    return {'status': 'review', 'provider': 'yolox-trained', 'model': MODEL,
        'runtimeArchitecture': platform.machine(),
        'engine': 'redcn · обученная компактная модель · проверка', 'originalId': digest,
        'candidates': candidates, 'autoApply': False,
        'ai': sanitize_ai({'backend': 'yolox-trained', 'model': MODEL, 'operation': 'furniture',
            'phase': 'complete', 'tokenBased': False, 'cached': False, 'processedImages': 1,
            'inferenceSeconds': inference_seconds,
            **({'imagesPerSecond': 1 / inference_seconds} if inference_seconds > 0 else {})}),
        'unknownIds': [key for key in FEATURE_LABELS if key != 'bed' and key not in {row['id'] for row in candidates}],
        'message': 'Обученная модель предлагает предметы для проверки. Находки ещё не добавлены в описание.',
        'limitations': ['Первый обучающий прогон не прошёл приёмку точности.',
            'Ненайденный предмет остаётся неизвестным; оценки не являются вероятностями точности.',
            'Количество, размеры, исправность и наличие скрытых приборов не определяются.']}


def _unavailable():
    return {'status': 'unavailable', 'provider': 'yolox-trained', 'model': MODEL,
        'engine': 'redcn · обученная компактная модель', 'candidates': [], 'autoApply': False,
        'message': 'Обученная модель недоступна или не успела обработать фото. Предметы не проверены.',
        'limitations': ['Это не означает, что предметов нет.']}


def analyze(content, mime, directory, *, cancel=None, deadline=None, status=None):
    if cancel is not None and cancel.is_set():
        raise ValueError('Анализ отменён')
    started = time.monotonic()
    def progress(phase, message, **metrics):
        ai = sanitize_ai({'backend': 'yolox-trained', 'model': MODEL, 'operation': 'furniture',
            **metrics, 'phase': phase, 'tokenBased': False, 'cached': False,
            'elapsedSeconds': time.monotonic() - started})
        emit(status, message, stage='vision', ai=ai)
        return ai
    def completed(result):
        raw_metrics = result.get('ai') if isinstance(result.get('ai'), dict) else {}
        metrics = {key: raw_metrics[key] for key in ('inferenceSeconds', 'imagesPerSecond', 'processedImages') if key in raw_metrics}
        result['ai'] = progress('complete' if result.get('status') == 'review' else 'unavailable',
            result.get('message', 'Анализ фото завершён'), **metrics)
        return result
    remaining = 20 if deadline is None else min(20, deadline-started)
    if remaining <= 0:
        raise ValueError('Время анализа фото истекло')
    if not isinstance(content, bytes) or not 0 < len(content) <= MAX_BYTES:
        return completed(_unavailable())
    request = json.dumps({'content': base64.b64encode(content).decode(), 'mime': mime}).encode()
    process = None
    try:
        from photo_native_runtime import runtime_command, NativeRuntimeError
        try:
            if status is not None:
                progress('loading', 'Загружаем локальный детектор мебели и техники')
            command, runtime = runtime_command(directory, deadline=started+remaining)
        except NativeRuntimeError as error:
            return completed({**_unavailable(), 'message': str(error)+'; фото не проверено.',
                'errorCode': error.code, 'runtime': {'selection': 'native-arm', 'status': 'invalid'}})
        process = subprocess.Popen([*command, str(Path(__file__).resolve()), '--worker', str(directory)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
        finish = started+remaining
        pending = request
        if status is not None:
            progress('analyzing', 'Находим видимые предметы на фото; скорость измеряется в фото/с')
        while True:
            if cancel is not None and cancel.is_set():
                raise ValueError('Анализ отменён')
            budget = finish-time.monotonic()
            if budget <= 0:
                return completed(_unavailable())
            try:
                stdout, _ = process.communicate(input=pending, timeout=min(.1, budget))
                break
            except subprocess.TimeoutExpired:
                pending = None
        if process.returncode != 0 or len(stdout) > MAX_OUTPUT:
            return completed(_unavailable())
        result = json.loads(stdout)
        if not isinstance(result, dict) or result.get('provider') != 'yolox-trained' or result.get('model') != MODEL:
            return completed(_unavailable())
        if runtime.get('selection') == 'native-arm' and result.get('runtimeArchitecture') != 'arm64':
            return completed({**_unavailable(), 'message': 'ARM runtime вернул несовпадающую архитектуру; фото не проверено.',
                'errorCode': 'PHOTO_NATIVE_RUNTIME_ARCHITECTURE', 'runtime': {**runtime, 'status': 'invalid'}})
        result['runtime'] = {**runtime, 'workerArchitecture': result.get('runtimeArchitecture', 'unknown')}
        # Enforce publication policy even when loading an older worker/cache.
        result['autoApply'] = False
        for row in result.get('candidates', []):
            row.update(autoEligible=False, autoCountEligible=False, count=None)
        return completed(result)
    except (OSError, json.JSONDecodeError, TypeError, AttributeError):
        return completed(_unavailable())
    finally:
        if process is not None and process.poll() is None:
            try: process.terminate()
            except ProcessLookupError: pass
            try:
                process.wait(timeout=.5)
            except subprocess.TimeoutExpired:
                try: process.kill()
                except ProcessLookupError: pass
                try: process.wait(timeout=.5)
                except subprocess.TimeoutExpired: pass


if __name__ == '__main__':
    try:
        if len(sys.argv) != 3 or sys.argv[1] != '--worker':
            raise ValueError('Worker arguments')
        request = json.loads(sys.stdin.buffer.read(17_000_001))
        content = base64.b64decode(request['content'], validate=True)
        print(json.dumps(_infer(sys.argv[2], content, request['mime']), ensure_ascii=False, allow_nan=False))
    except Exception:
        print(json.dumps(_unavailable(), ensure_ascii=False))
        raise SystemExit(1)
