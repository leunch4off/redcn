"""Metrics for real, explicitly annotated watermark frames; no pixels retained."""
import itertools
import math


def region(value):
    if not isinstance(value, list) or len(value) != 4 or any(
            type(v) not in (int, float) or not math.isfinite(v) for v in value):
        raise ValueError('Нужна нормализованная область [x,y,width,height]')
    x, y, w, h = value
    if not (0 <= x < 1 and 0 <= y < 1 and 0 < w <= 1-x+.000001 and 0 < h <= 1-y+.000001):
        raise ValueError('Область выходит за кадр')
    return value


def intersection(left, right):
    return max(0, min(left[2], right[2])-max(left[0], right[0])) * max(
        0, min(left[3], right[3])-max(left[1], right[1]))


def pixels(value, width, height):
    x, y, w, h = region(value)
    return (math.floor(x*width), math.floor(y*height), math.ceil((x+w)*width), math.ceil((y+h)*height))


def minimal_edge_crop(width, height, marks, padding=2):
    """Best retained rectangle excluding the annotated regions, under crop rules.

    This is a geometric oracle for these approximate annotations, not proof
    that a new image is visually clean or its composition is acceptable.
    """
    boxes = [pixels(value, width, height) for value in marks]
    if not boxes:
        return {'rect': [0, 0, width, height], 'retainedArea': 1.0}
    if len(boxes) > 6:
        raise ValueError('Слишком много областей для ограниченного геометрического сравнения')
    options = ({0, *(min(width, b[2]+padding) for b in boxes)},
               {0, *(min(height, b[3]+padding) for b in boxes)},
               {width, *(max(0, b[0]-padding) for b in boxes)},
               {height, *(max(0, b[1]-padding) for b in boxes)})
    choices = []
    for left, top, right, bottom in itertools.product(*options):
        area = (right-left)*(bottom-top)
        if right-left < 32 or bottom-top < 32 or area < .55*width*height:
            continue
        if max(left/width, top/height, (width-right)/width, (height-bottom)/height) > .40:
            continue
        rect = (left, top, right, bottom)
        if any(intersection(rect, box) > 0 for box in boxes):
            continue
        choices.append((area, rect))
    if not choices:
        return None
    area, rect = max(choices)
    return {'rect': list(rect), 'retainedArea': area/(width*height)}


def crop_measurements(width, height, rect, marks, important_regions):
    if not (isinstance(rect, (tuple, list)) and len(rect) == 4 and
            all(type(v) is int for v in rect) and 0 <= rect[0] < rect[2] <= width
            and 0 <= rect[1] < rect[3] <= height):
        raise ValueError('Неверная граница предложенной подрезки')
    retained = (rect[2]-rect[0])*(rect[3]-rect[1])/(width*height)
    remaining = sum(intersection(rect, pixels(value, width, height)) > 0 for value in marks)
    important = []
    for row in important_regions:
        box = pixels(row['region'], width, height)
        area = (box[2]-box[0])*(box[3]-box[1])
        important.append({'label': row['label'], 'retainedFraction': intersection(rect, box)/area})
    oracle = minimal_edge_crop(width, height, marks)
    return {'retainedArea': retained, 'areaLoss': 1-retained,
            'annotatedMarkRegionsRemaining': remaining,
            'importantVisibleRegions': important,
            'importantRegionCut': any(row['retainedFraction'] < .999 for row in important),
            'minimalAnnotatedEdgeCrop': oracle,
            'extraAreaLossVsAnnotatedOracle': max(0, oracle['retainedArea']-retained) if oracle else None,
            'compositionAutomaticallyAccepted': False, 'cleanlinessConfirmed': False}


def presence_counts(records):
    tp = fp = fn = tn = errors = unknown = 0
    for row in records:
        truth = row['watermarkPresent']
        if truth not in (True, False, None) or type(truth) not in (bool, type(None)):
            raise ValueError('Неизвестная метка watermark')
        if row.get('detectorError'):
            errors += 1
            continue
        predicted = row.get('detected')
        if type(predicted) is not bool:
            raise ValueError('Нужен результат детектора или явная ошибка')
        if truth is None:
            unknown += 1
            continue
        tp += int(truth and predicted)
        fn += int(truth and not predicted)
        fp += int(not truth and predicted)
        tn += int(not truth and not predicted)
    return {'tp': tp, 'fp': fp, 'fn': fn, 'tn': tn, 'detectorErrors': errors,
            'unknownTruthExcluded': unknown,
            'verifiedPositiveFrames': tp+fn, 'verifiedNegativeFrames': fp+tn,
            'precision': tp/(tp+fp) if tp+fp else None,
            'recall': tp/(tp+fn) if tp+fn else None,
            'falsePositiveRate': fp/(fp+tn) if fp+tn else None,
            'falseNegativeRate': fn/(tp+fn) if tp+fn else None,
            'noDetectionProvesClean': False}
