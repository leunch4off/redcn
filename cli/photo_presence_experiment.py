"""Private, explicitly assistant-labelled photo presence experiment utilities.

An unmentioned class is unknown. Image-level witnesses are not bounding boxes,
operator confirmation, exhaustive labels or evidence for automatic publication.
"""
import hashlib
import io
import json
import math
from pathlib import Path
import re
import time

from trained_photo_detector import CLASS_IDS, CLASS_ALIASES

SHA = re.compile(r'[a-f0-9]{64}\Z')
SUPPORTED_IDS = frozenset(CLASS_ALIASES.get(key, key) for key in CLASS_IDS)


def read_manifest(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 10_000_000:
        raise ValueError('A bounded local assistant presence manifest is required')
    raw = path.read_bytes(); data = json.loads(raw)
    if data.get('humanVerified') is not False or data.get('exhaustive') is not False:
        raise ValueError('Assistant provenance and incomplete labels must be explicit')
    rows = data.get('photos')
    if not isinstance(rows, list) or not 1 <= len(rows) <= 3000:
        raise ValueError('Expected 1–3000 explicitly identified photo records')
    merged = {}
    for row in rows:
        digest = row.get('originalId')
        group = row.get('listingGroup')
        positive = row.get('positiveIds')
        negative = row.get('absentIds', [])
        if (not isinstance(digest, str) or not SHA.fullmatch(digest)
                or not isinstance(group, str) or not 1 <= len(group) <= 200
                or not isinstance(positive, list) or not isinstance(negative, list)
                or any(not isinstance(key, str) or len(key)>80 for key in positive+negative)
                or set(positive) & set(negative)):
            raise ValueError('Malformed or contradictory image-level labels')
        item = merged.setdefault(digest, {'originalId': digest, 'groups': set(),
            'positiveIds': set(), 'absentIds': set()})
        item['groups'].add(group); item['positiveIds'].update(positive); item['absentIds'].update(negative)
        if item['positiveIds'] & item['absentIds']:
            raise ValueError('The same photograph has contradictory witnesses')
    # Duplicate bytes join apartment groups before the split. A repeated photo
    # cannot be in both training and evaluation through another listing URL.
    parent = {}
    def find(key):
        parent.setdefault(key, key)
        if parent[key] != key: parent[key] = find(parent[key])
        return parent[key]
    for row in merged.values():
        groups = sorted(row['groups'])
        for group in groups[1:]: parent[find(group)] = find(groups[0])
    components = {}
    for group in list(parent): components.setdefault(find(group), []).append(group)
    def component(group):
        root = find(group)
        return min(components.get(root, [group]))
    prepared = []
    for digest, row in sorted(merged.items()):
        group = component(min(row['groups']))
        bucket = int(hashlib.sha256(('redcn-photo-v30|'+group).encode()).hexdigest()[:8], 16)%10
        split = 'test' if bucket < 2 else 'validation' if bucket == 2 else 'train'
        prepared.append({**row, 'groups': sorted(row['groups']), 'listingGroup': group,
            'split': split, 'positiveIds': sorted(row['positiveIds']), 'absentIds': sorted(row['absentIds'])})
    return prepared, hashlib.sha256(raw).hexdigest()


def content_input(content, engine):
    import numpy as np
    from PIL import Image, ImageOps
    with Image.open(io.BytesIO(content)) as original:
        if (original.format not in ('JPEG', 'PNG', 'WEBP') or original.width*original.height>40_000_000
                or min(original.size)<32 or getattr(original, 'n_frames', 1)!=1):
            raise ValueError('Invalid single source photograph')
        with ImageOps.exif_transpose(original) as oriented: image = oriented.convert('RGB')
    width, height = image.size
    size = 416 if engine == 'baseline' else 640
    ratio = min(size/width, size/height)
    if engine == 'baseline':
        resized=image.resize((max(1,int(width*ratio)), max(1,int(height*ratio))),Image.Resampling.BILINEAR)
        array=np.full((size,size,3),114,dtype=np.float32)
        array[:resized.height,:resized.width]=np.asarray(resized)[:,:,::-1]
        tensor=np.ascontiguousarray(array.transpose(2,0,1)[None]); resized.close();pad=(0,0)
    else:
        # Match the existing official-export pilot's centered OpenCV letterbox.
        import cv2
        scaled=(round(width*ratio),round(height*ratio));array=np.asarray(image)
        if scaled!=(width,height):array=cv2.resize(array,scaled,interpolation=cv2.INTER_LINEAR)
        dw=(size-scaled[0])/2;dh=(size-scaled[1])/2
        top,bottom=round(dh-.1),round(dh+.1);left,right=round(dw-.1),round(dw+.1)
        array=cv2.copyMakeBorder(array,top,bottom,left,right,cv2.BORDER_CONSTANT,value=(114,114,114))
        tensor=np.ascontiguousarray(array.transpose(2,0,1)[None]).astype(np.float32)/255.;pad=(left,top)
    image.close()
    return tensor,width,height,ratio,pad


def presence_summary(records, thresholds=(.35,.5,.7,.85)):
    result = {}
    for threshold in thresholds:
        totals={'positiveWitnesses':0,'recoveredPositiveWitnesses':0,'explicitNegativeWitnesses':0,
            'negativeWitnessViolations':0,'unlabelledPredictions':0,'unsupportedPositiveWitnesses':0}
        by_class={};by_split={}
        for record in records:
            predicted={key for key,value in record['scores'].items() if value>=threshold}
            positive=set(record['positiveIds']);negative=set(record.get('absentIds',[]))
            totals['unlabelledPredictions']+=len(predicted-positive-negative)
            totals['unsupportedPositiveWitnesses']+=len(positive-SUPPORTED_IDS)
            for key in positive | negative:
                if key not in SUPPORTED_IDS: continue
                row=by_class.setdefault(key,{'positiveWitnesses':0,'recoveredPositiveWitnesses':0,
                    'explicitNegativeWitnesses':0,'negativeWitnessViolations':0})
                split=by_split.setdefault(record['split'],{'positiveWitnesses':0,'recoveredPositiveWitnesses':0,
                    'explicitNegativeWitnesses':0,'negativeWitnessViolations':0})
                keys=(['positiveWitnesses']+(['recoveredPositiveWitnesses'] if key in predicted else [])) if key in positive else (['explicitNegativeWitnesses']+(['negativeWitnessViolations'] if key in predicted else []))
                for stat in keys: totals[stat]+=1;row[stat]+=1;split[stat]+=1
        result[str(threshold)]={'totals':totals,'byClass':by_class,'bySplit':by_split}
    return result
