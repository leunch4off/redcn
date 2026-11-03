"""Human-verified Open Images labels: missing labels never mean absence."""
import csv
import math
from pathlib import Path
import re

HUMAN_SOURCES = frozenset(('verification', 'crowdsource-verification'))
EXTRA_CATEGORIES = {'hood': ('Kitchen hood', 'Range hood', 'Exhaust hood'),
                    'computer': ('Computer', 'Personal computer')}
SCENE_NAMES = ('Kitchen', 'Bedroom', 'Living room', 'Home office')


def class_names(path):
    result = {}
    with Path(path).open(encoding='utf-8-sig', newline='') as source:
        for row in csv.reader(source):
            if len(row) != 2:
                raise ValueError('Неверный официальный справочник классов')
            if row[1] in result and result[row[1]] != row[0]:
                raise ValueError('Неоднозначное имя класса')
            result[row[1]] = row[0]
    return result


def target_mids(classes, names):
    result = {row['id']: (row['openImagesId'],) for row in classes}
    for category, alternatives in EXTRA_CATEGORIES.items():
        mids = tuple(dict.fromkeys(names[name] for name in alternatives if name in names))
        if mids:
            result[category] = mids
    return result


def human_labels(path, wanted_mids, image_ids=None):
    labels, conflicts, ignored = {}, set(), 0
    with Path(path).open(encoding='utf-8-sig', newline='') as source:
        for row in csv.DictReader(source):
            identity, mid = row.get('ImageID', ''), row.get('LabelName', '')
            if mid not in wanted_mids or image_ids is not None and identity not in image_ids:
                continue
            if not re.fullmatch(r'[a-f0-9]{16}', identity):
                raise ValueError('Неверный ID официальной фотографии')
            if row.get('Source') not in HUMAN_SOURCES or row.get('Confidence') not in ('0', '1', '0.0', '1.0'):
                ignored += 1
                continue
            pair = (identity, mid)
            value = row['Confidence'] in ('1', '1.0')
            if pair in labels and labels[pair] != value:
                conflicts.add(pair)
            labels[pair] = value
    for pair in conflicts:
        labels.pop(pair, None)
    return labels, conflicts, ignored


def category_truth(labels, identity, mids):
    values = [labels.get((identity, mid)) for mid in mids]
    if any(value is True for value in values):
        return True
    if values and all(value is False for value in values):
        return False
    return None


def presence_metrics(records, targets, supported, threshold=.35):
    for row in records:
        if any(value is not None and type(value) is not bool for value in row['truth'].values()):
            raise ValueError('Нужны проверенные положительные, отрицательные или неизвестные метки')
        if any(value['classId'] not in supported or type(value['score']) not in (int, float)
               or not math.isfinite(value['score']) or not 0 <= value['score'] <= 1 for value in row['predictions']):
            raise ValueError('Выход модели содержит неизвестную категорию или неверную оценку')
    metrics = {}
    for category in targets:
        tp = fp = fn = tn = unknown = 0
        for row in records:
            truth = row['truth'].get(category)
            if truth is None:
                unknown += 1
                continue
            predicted = any(value['classId'] == category and value['score'] >= threshold for value in row['predictions'])
            tp += int(truth and predicted)
            fp += int(not truth and predicted)
            fn += int(truth and not predicted)
            tn += int(not truth and not predicted)
        metrics[category] = {'tp': tp, 'fp': fp, 'fn': fn, 'tn': tn, 'unknownImages': unknown,
                             'verifiedPositiveImages': tp+fn, 'verifiedNegativeImages': fp+tn,
                             'precision': tp/(tp+fp) if tp+fp else None,
                             'recall': tp/(tp+fn) if tp+fn else None,
                             'supportedByActiveVocabulary': category in supported,
                             'evaluationMode': 'inferred-image-presence' if category in supported else 'unrepresentable-output-category',
                             'missingPredictionMeansAbsence': False}
    return metrics
