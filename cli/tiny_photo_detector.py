"""Isolated YOLOX Nano/Tiny pilot. Never imports server modules or publishes facts.

Uses the official raw-output ONNX, 416x416, CPU only and two threads. Pillow
bilinear resize replaces upstream OpenCV interpolation, so calibration must use
this exact preprocessing. Detection scores are not calibrated probabilities.
"""
import argparse
import hashlib
import io
import json
import math
from pathlib import Path
import re
import time
import warnings

MODEL_NAME = 'yolox-nano-0.1.1rc0'
MODEL_SOURCE = 'https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_nano.onnx'
VARIANTS = {
    'nano': {'name': MODEL_NAME, 'limit': 8_000_000,
             'sha256': 'c789161ed43c8269fcd4e67c67eeeb4e80c622da2eb296a20bc6007bd18a0b7d'},
    'tiny': {'name': 'yolox-tiny-0.1.1rc0', 'limit': 25_000_000,
             'sha256': '427cc366d34e27ff7a03e2899b5e3671425c262ea2291f88bb942bc1cc70b0f7'},
}
SIZE = 416
ROWS = sum((SIZE // stride) ** 2 for stride in (8, 16, 32))
CLASSES = {
    56: ('chairs', 'Стул'), 57: ('sofa', 'Диван'), 59: ('bedAny', 'Кровать'),
    60: ('table', 'Обеденный стол'), 62: ('tv', 'Телевизор'),
    63: ('computer', 'Ноутбук'), 68: ('microwave', 'Микроволновая печь'),
    69: ('oven', 'Духовой шкаф'), 71: ('sink', 'Раковина / мойка'),
    72: ('fridge', 'Холодильник'),
}
UNSUPPORTED = ['wardrobe', 'desk', 'washer', 'dryer', 'dishwasher', 'stove',
               'hood', 'piano', 'projector', 'router', 'kettle']


def _bytes(path, limit):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= limit:
        raise ValueError('Нужен обычный файл допустимого размера')
    with path.open('rb') as source:
        value = source.read(limit + 1)
    if not 0 < len(value) <= limit:
        raise ValueError('Размер файла превышен')
    return value


def _iou(a, b):
    intersection = max(0, min(a[0]+a[2], b[0]+b[2])-max(a[0], b[0])) * max(0, min(a[1]+a[3], b[1]+b[3])-max(a[1], b[1]))
    union = a[2]*a[3]+b[2]*b[3]-intersection
    return intersection / union if union > 0 else 0


def _model_bytes(path, variant, expected_sha=None):
    if variant not in VARIANTS:
        raise ValueError('Вариант модели: nano или tiny')
    if expected_sha is not None and (not isinstance(expected_sha, str) or not re.fullmatch(r'[0-9a-f]{64}', expected_sha)):
        raise ValueError('Нужен SHA-256 модели из 64 шестнадцатеричных символов')
    spec = VARIANTS[variant]
    if expected_sha is not None and expected_sha != spec['sha256']:
        raise ValueError('SHA-256 не соответствует выбранному официальному варианту')
    expected_sha = spec['sha256']
    content = _bytes(path, spec['limit'])
    digest = hashlib.sha256(content).hexdigest()
    if expected_sha is not None and digest != expected_sha:
        raise ValueError('SHA-256 модели не совпадает с ожидаемым')
    return content, spec['name'], expected_sha is not None


def decode(rows, width, height, *, threshold=.35, nms=.45):
    """Decode official raw YOLOX output; return review-only normalized boxes."""
    if type(width) is not int or type(height) is not int or min(width, height) <= 0:
        raise ValueError('Неверные размеры фотографии')
    if type(threshold) not in (int, float) or not .05 <= threshold <= 1 or not 0 < nms < 1:
        raise ValueError('Неверные пороги детектора')
    if len(rows) != ROWS:
        raise ValueError('Нужен raw-output YOLOX 416: 3549×85')
    ratio = min(SIZE/width, SIZE/height)
    found, index = [], 0
    for stride in (8, 16, 32):
        for gy in range(SIZE // stride):
            for gx in range(SIZE // stride):
                row = rows[index]
                index += 1
                if len(row) != 85 or any(type(v) not in (float, int) or not math.isfinite(v) for v in row):
                    raise ValueError('Неверные значения raw-output')
                if not all(0 <= v <= 1 for v in row[4:]):
                    raise ValueError('Неверные оценки raw-output')
                class_id = max(range(80), key=lambda k: row[k+5])
                score = row[4] * row[class_id+5]
                if class_id not in CLASSES or score < threshold:
                    continue
                if abs(row[2]) > 20 or abs(row[3]) > 20:
                    continue
                cx, cy = (row[0]+gx)*stride/ratio, (row[1]+gy)*stride/ratio
                bw, bh = math.exp(row[2])*stride/ratio, math.exp(row[3])*stride/ratio
                x1, y1 = max(0, min(width, cx-bw/2)), max(0, min(height, cy-bh/2))
                x2, y2 = max(0, min(width, cx+bw/2)), max(0, min(height, cy+bh/2))
                if x2 <= x1 or y2 <= y1:
                    continue
                region = [x1/width, y1/height, (x2-x1)/width, (y2-y1)/height]
                found.append({'classId': class_id, 'score': score, 'region': region})
    kept = []
    for row in sorted(found, key=lambda item: -item['score']):
        if not any(previous['classId'] == row['classId'] and _iou(previous['region'], row['region']) > nms for previous in kept):
            kept.append(row)
        if len(kept) >= 100:
            break
    groups = {}
    for row in kept:
        feature, label = CLASSES[row['classId']]
        candidate = groups.setdefault(feature, {'id': feature, 'label': label,
            'status': 'candidate', 'autoEligible': False, 'autoCountEligible': False,
            'count': None, 'score': row['score'], 'evidence': []})
        candidate['evidence'].append({'kind': 'object-detection', 'classId': row['classId'],
            'score': row['score'], 'region': row['region']})
    return list(groups.values())


def analyze(model_path, image_path, *, repeat=1, threshold=.35, variant='nano', expected_sha=None):
    """Local files only; explicit invocation, no install/download or config writes."""
    if type(repeat) is not int or not 1 <= repeat <= 20:
        raise ValueError('Повторы: от 1 до 20')
    started = time.perf_counter()
    model, model_name, hash_verified = _model_bytes(model_path, variant, expected_sha)
    photo = _bytes(image_path, 12_000_000)
    import numpy as np
    from PIL import Image, ImageOps
    import onnxruntime as ort
    with warnings.catch_warnings():
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(photo)) as original:
            if original.format not in ('JPEG', 'PNG', 'WEBP') or getattr(original, 'n_frames', 1) != 1 or original.width*original.height > 40_000_000 or min(original.size) < 32:
                raise ValueError('Нужна одиночная фотография JPEG/PNG/WebP до 40 Мп')
            image = ImageOps.exif_transpose(original).convert('RGB')
    width, height = image.size
    ratio = min(SIZE/width, SIZE/height)
    resized = image.resize((int(width*ratio), int(height*ratio)), Image.Resampling.BILINEAR)
    array = np.full((SIZE, SIZE, 3), 114, dtype=np.float32)
    array[:resized.height, :resized.width] = np.asarray(resized)[:, :, ::-1]
    tensor = np.ascontiguousarray(array.transpose(2, 0, 1)[None])
    image.close(); resized.close()
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    load_started = time.perf_counter()
    session = ort.InferenceSession(model, sess_options=options, providers=['CPUExecutionProvider'])
    inputs = session.get_inputs()
    if len(inputs) != 1 or inputs[0].shape != [1, 3, SIZE, SIZE] or inputs[0].type != 'tensor(float)':
        raise ValueError('Неверный вход модели: нужен float32 1×3×416×416')
    load_ms = (time.perf_counter()-load_started)*1000
    durations, predictions = [], None
    for _ in range(repeat):
        inference_started = time.perf_counter()
        output = session.run(None, {inputs[0].name: tensor})
        durations.append((time.perf_counter()-inference_started)*1000)
        if len(output) != 1 or output[0].shape != (1, ROWS, 85):
            raise ValueError('Неверный выход модели: нужен raw 1×3549×85')
        predictions = output[0][0].tolist()
    candidates = decode(predictions, width, height, threshold=threshold)
    digest = hashlib.sha256(photo).hexdigest()
    for candidate in candidates:
        for evidence in candidate['evidence']:
            evidence.update({'photoId': digest, 'provider': 'yolox-onnx', 'model': model_name})
    return {'version': 1, 'status': 'review', 'provider': 'yolox-onnx', 'model': model_name,
        'variant': variant, 'modelHashVerified': hash_verified,
        'modelSha256': hashlib.sha256(model).hexdigest(), 'modelBytes': len(model),
        'originalId': digest, 'width': width, 'height': height, 'autoApply': False,
        'candidates': candidates, 'unknownIds': [feature for feature, _ in CLASSES.values() if feature not in {row['id'] for row in candidates}],
        'unsupportedIds': UNSUPPORTED, 'threshold': threshold, 'runtime': ort.__version__,
        'metrics': {'loadMs': load_ms, 'inferenceMs': durations, 'totalMs': (time.perf_counter()-started)*1000},
        'limitations': ['Оценки не являются вероятностями точности; все находки требуют проверки.',
            'Ненайденный предмет остаётся неизвестным, а не отсутствующим.',
            'Готовые COCO-классы не охватывают всю мебель и технику; количество между фото не суммируется.',
            'Раковина не определяет назначение комнаты; ноутбук не подтверждает стационарный компьютер.',
            'Размер кровати, исправность и доступность предметов для арендатора по фото не установлены.']}


def main():
    parser = argparse.ArgumentParser(description='Изолированный пилот YOLOX Nano/Tiny: только подсказки')
    parser.add_argument('--model', required=True)
    parser.add_argument('--photo', required=True)
    parser.add_argument('--repeat', type=int, default=1)
    parser.add_argument('--threshold', type=float, default=.35)
    parser.add_argument('--variant', choices=tuple(VARIANTS), default='nano')
    parser.add_argument('--expected-sha')
    args = parser.parse_args()
    try:
        print(json.dumps(analyze(args.model, args.photo, repeat=args.repeat, threshold=args.threshold,
            variant=args.variant, expected_sha=args.expected_sha), ensure_ascii=False, allow_nan=False))
    except (OSError, ValueError, ImportError) as error:
        print(json.dumps({'status': 'unavailable', 'autoApply': False, 'message': str(error)}, ensure_ascii=False))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
