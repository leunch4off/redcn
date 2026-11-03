"""Freeze class thresholds on calibration data; evaluate untouched test groups.

This module measures explicit predictions and labels. It does not run, install
or promote weights, infer missing labels, or certify the supplied human labels.
"""
from collections import defaultdict
import hashlib
import json
import math
import re

SHA = re.compile(r'[a-f0-9]{64}\Z')
POLICY = {'version': 1, 'iouThreshold': .5, 'minimumTruePositives': 75,
          'minimumVerifiedImages': 20, 'minimumVerifiedNegativeImages': 20,
          'minimumPrecisionWilsonLower95': .95, 'minimumRecall': .5}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode()).hexdigest()


def wilson(tp, fp):
    n = tp+fp
    if not n: return 0.
    z = 1.959963984540054; p = tp/n
    return (p+z*z/(2*n)-z*math.sqrt(p*(1-p)/n+z*z/(4*n*n)))/(1+z*z/n)


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _box(value):
    if (not isinstance(value, list) or len(value) != 4 or any(not _finite(x) for x in value)
            or not (0 <= value[0] < value[2] <= 1 and 0 <= value[1] < value[3] <= 1)):
        raise ValueError('Неверная нормализованная рамка xyxy')
    return value


def _ids(value, n):
    if not isinstance(value, list) or any(type(i) is not int or not 0 <= i < n for i in value) or len(set(value)) != len(value):
        raise ValueError('Неверный список проверенных категорий')
    return set(value)


def validate_corpus(corpus):
    if not isinstance(corpus, dict): raise ValueError('Нужен объект корпуса')
    classes = corpus.get('classes', [])
    names = [row.get('id') if isinstance(row, dict) else None for row in classes] if isinstance(classes, list) else []
    if (not 1 <= len(names) <= 80 or any(not isinstance(x, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', x) for x in names)
            or len(set(names)) != len(names)):
        raise ValueError('Нужен уникальный словарь классов')
    splits = corpus.get('splits')
    if not isinstance(splits, dict) or set(splits) != {'train', 'calibration', 'test'}:
        raise ValueError('Нужны отдельные train, calibration и test')
    groups, photos = defaultdict(set), set()
    for split, rows in splits.items():
        if not isinstance(rows, list) or not 1 <= len(rows) <= 10000:
            raise ValueError('Каждая часть должна содержать от 1 до 10000 изображений')
        for row in rows:
            if (not isinstance(row, dict) or not isinstance(row.get('sha256'), str)
                    or not SHA.fullmatch(row['sha256']) or row.get('imageId') != row['sha256']
                    or row.get('sourceSplit') != split or not isinstance(row.get('groupId'), str) or not row['groupId']):
                raise ValueError('Нужны исходный SHA, группа и согласованная часть корпуса')
            if row['sha256'] in photos: raise ValueError('Оригинал повторяется в корпусе')
            photos.add(row['sha256']); groups[row['groupId']].add(split)
            if row.get('domain') != 'rental-apartment' or row.get('individualLicenseVerified') is not True:
                raise ValueError('Нужны квартирные изображения с разрешением использования')
            verified = _ids(row.get('verifiedClassIndices'), len(names))
            negatives = _ids(row.get('negativeClassIndices'), len(names))
            boxes = row.get('boxes')
            if not isinstance(boxes, list) or len(boxes) > 1000:
                raise ValueError('Неверная разметка рамок')
            positive = set()
            for box in boxes:
                if not isinstance(box, dict) or type(box.get('classIndex')) is not int or not 0 <= box['classIndex'] < len(names):
                    raise ValueError('Неверная категория рамки')
                _box(box.get('xyxy')); positive.add(box['classIndex'])
            if negatives - verified or negatives & positive or verified-positive != negatives:
                raise ValueError('Проверенное отсутствие должно быть явным и согласованным с рамками')
    if any(len(parts) != 1 for parts in groups.values()):
        raise ValueError('Одна квартира пересекает train/calibration/test')
    return names


def _predictions(rows, predictions, names):
    if not isinstance(predictions, dict) or set(predictions) != {row['imageId'] for row in rows}:
        raise ValueError('Нужна явная выдача для каждого оригинала только выбранной части')
    for values in predictions.values():
        if not isinstance(values, list) or len(values) > 1000: raise ValueError('Слишком много предсказаний')
        for pred in values:
            if (not isinstance(pred, dict) or set(pred) != {'category','score','xyxy'} or pred['category'] not in names
                    or not _finite(pred['score']) or not 0 <= pred['score'] <= 1):
                raise ValueError('Неверное предсказание детектора')
            _box(pred['xyxy'])


def _iou(a, b):
    intersection = max(0, min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))
    return intersection/((a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-intersection)


def metrics(rows, predictions, names, category, threshold):
    _predictions(rows, predictions, names)
    index = names.index(category)
    result = {'tp':0,'fp':0,'fn':0,'verifiedImages':0,'verifiedNegativeImages':0,'unknownImages':0}
    for row in rows:
        if index not in row['verifiedClassIndices']:
            result['unknownImages'] += 1; continue
        result['verifiedImages'] += 1
        result['verifiedNegativeImages'] += int(index in row['negativeClassIndices'])
        targets = [box['xyxy'] for box in row['boxes'] if box['classIndex'] == index]
        candidates = sorted((p for p in predictions[row['imageId']] if p['category']==category and p['score']>=threshold), key=lambda p:-p['score'])
        matched = set()
        for pred in candidates:
            choices = [(_iou(pred['xyxy'], box), i) for i,box in enumerate(targets) if i not in matched]
            best = max(choices, default=(0,-1))
            if best[0] >= POLICY['iouThreshold']:
                result['tp'] += 1; matched.add(best[1])
            else: result['fp'] += 1
        result['fn'] += len(targets)-len(matched)
    tp,fp,fn = result['tp'],result['fp'],result['fn']
    result.update(precision=tp/(tp+fp) if tp+fp else None,
                  recall=tp/(tp+fn) if tp+fn else None,
                  precisionWilsonLower95=wilson(tp,fp))
    result['statisticalGatePassed'] = (tp>=POLICY['minimumTruePositives'] and
        result['verifiedImages']>=POLICY['minimumVerifiedImages'] and
        result['verifiedNegativeImages']>=POLICY['minimumVerifiedNegativeImages'] and
        result['precisionWilsonLower95']>=POLICY['minimumPrecisionWilsonLower95'] and
        (result['recall'] or 0)>=POLICY['minimumRecall'])
    return result


def freeze(corpus, predictions, *, model_sha, thresholds=(.5,.6,.7,.8,.85,.9,.95)):
    names = validate_corpus(corpus)
    if not isinstance(model_sha,str) or not SHA.fullmatch(model_sha): raise ValueError('Нужен SHA весов')
    if (not isinstance(thresholds,(list,tuple)) or not 1<=len(thresholds)<=30
            or any(not _finite(x) or not 0<x<=1 for x in thresholds)):
        raise ValueError('Нужна заранее заданная сетка до 30 порогов')
    rows = corpus['splits']['calibration']; _predictions(rows,predictions,names)
    selected = {}
    for category in names:
        trials = [{'threshold':t,**metrics(rows,predictions,names,category,t)} for t in sorted(set(thresholds))]
        eligible = [row for row in trials if row['statisticalGatePassed']]
        # No supported threshold means that this class remains review-only.
        chosen = max(eligible,key=lambda r:(r['recall'],r['precisionWilsonLower95'],r['threshold'])) if eligible else None
        selected[category] = {'threshold':chosen['threshold'] if chosen else None,
                              'calibration':chosen,'trials':trials}
    result = {'version':1,'kind':'frozen-photo-calibration','corpusSha256':digest(corpus),
              'modelSha256':model_sha,'predictionsSha256':digest(predictions),'policy':POLICY,
              'calibrationOnly':True,'classes':selected,'automaticAcceptance':False}
    result['sha256'] = digest(result)
    return result


def evaluate_test(corpus, predictions, frozen, *, model_sha):
    names=validate_corpus(corpus)
    if (not isinstance(frozen,dict) or frozen.get('kind')!='frozen-photo-calibration'
            or frozen.get('sha256')!=digest({k:v for k,v in frozen.items() if k!='sha256'})
            or frozen.get('corpusSha256')!=digest(corpus) or frozen.get('modelSha256')!=model_sha
            or frozen.get('policy')!=POLICY or set(frozen.get('classes',{}))!=set(names)):
        raise ValueError('Корпус, модель или замороженные пороги изменились')
    rows=corpus['splits']['test']; _predictions(rows,predictions,names)
    results={}
    for category in names:
        threshold=frozen['classes'][category]['threshold']
        if threshold is None:
            results[category]={'status':'calibration-insufficient','statisticalGatePassed':False};continue
        if not _finite(threshold) or not 0<threshold<=1:
            raise ValueError('Неверный замороженный порог')
        results[category]={'status':'measured','threshold':threshold,**metrics(rows,predictions,names,category,threshold)}
    return {'version':1,'kind':'heldout-photo-evaluation','corpusSha256':digest(corpus),
            'modelSha256':model_sha,'calibrationSha256':frozen['sha256'],
            'predictionsSha256':digest(predictions),'policy':POLICY,'classes':results,
            'statisticallyPassingClasses':[key for key,row in results.items() if row['statisticalGatePassed']],
            'automaticAcceptance':False,'requiresIndependentDataAndRuntimeAcceptance':True,
            'limitations':['Measured against supplied annotations; label correctness is not certified.',
                           'Weights are not installed or promoted by this report.']}
