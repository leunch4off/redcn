"""Pinned local YOLO-World rental detector. Every finding remains a suggestion."""
import base64
import hashlib
import io
import json
import math
from pathlib import Path
import sys
import time
import warnings

if __name__=='__main__' and sys.flags.isolated:
    sys.path.insert(0,str(Path(__file__).resolve().parent))
from local_photo_ai import FEATURE_LABELS
from trained_photo_detector import CLASS_IDS,CLASS_ALIASES,_read,_iou
from ai_telemetry import sanitize_ai

MODEL='redcn-furniture-world-v2'
PROVIDER='onnx-world'
MODEL_SHA='eb56e6e04067350e6bb4c9603a4664707ec7620f3a3dda81c1ddd8fd9d580bba'
SOURCE_CHECKPOINT_SHA='9b2c17ab6124a913e9b3a5c170617920d91b0f01111a8479da69f00e2cf27792'
ARTIFACT=Path('tools/yoloe-pilot/world-v2')
MODEL_PATH=ARTIFACT/'export-v1/furniture-21-worldv2.onnx'
SIZE=640
ROWS=8400
MAX_BYTES=12_000_000
PROMPTS=['sofa','bed','chair','television','refrigerator','oven','microwave oven','sink',
    'dining table','desk','wardrobe','washing machine','dishwasher','gas stove','piano',
    'cabinetry','cupboard','coffee maker','mirror','curtain','coffee table']
DECODER_VERSION='world-pinned-centered-cv2-v2'


def load_artifact(directory):
    folder=Path(directory)/ARTIFACT
    if folder.is_symlink() or (folder/'export-v1').is_symlink():raise ValueError('Каталог модели не должен быть ссылкой')
    raw=_read(folder/'export-receipt.json',2_000_000);receipt=json.loads(raw)
    if (not isinstance(receipt,dict) or receipt.get('sourceCheckpointSha256')!=SOURCE_CHECKPOINT_SHA
            or receipt.get('onnxSha256')!=MODEL_SHA or receipt.get('classIds')!=CLASS_IDS
            or receipt.get('prompts')!=PROMPTS or receipt.get('status')!='exported-unaccepted'):
        raise ValueError('Паспорт YOLO-World не соответствует закреплённой модели')
    model=_read(Path(directory)/MODEL_PATH,60_000_000)
    if hashlib.sha256(model).hexdigest()!=MODEL_SHA:raise ValueError('Контрольная сумма YOLO-World не совпадает')
    return model,hashlib.sha256(raw).hexdigest()


def cache_identity(directory):
    try:
        from photo_native_runtime import cache_identity as runtime_identity
        _,receipt=load_artifact(directory)
        return DECODER_VERSION+':'+MODEL_SHA+':'+receipt+':'+runtime_identity(directory,model=MODEL)
    except (OSError,ValueError,TypeError,AttributeError):return 'unavailable'


def decode(rows,width,height,ratio,pad,digest):
    """Channel-transposed model rows, same centered coordinates as measured pilot."""
    if len(rows)!=ROWS or min(width,height)<=0 or not 0<ratio<=20:raise ValueError('Неверный размер результата YOLO-World')
    found=[]
    for row in rows:
        if (len(row)!=25 or any(type(value) not in (int,float) or not math.isfinite(value) for value in row)
                or not all(0<=value<=1 for value in row[4:])):raise ValueError('Некорректные оценки YOLO-World')
        index=max(range(21),key=lambda i:row[4+i]);score=row[4+index]
        if score<.35:continue
        raw=CLASS_IDS[index];feature=CLASS_ALIASES.get(raw,raw)
        if feature not in FEATURE_LABELS:continue
        cx,cy,bw,bh=row[:4]
        if bw<=0 or bh<=0:continue
        x1=max(0.,min(1.,(cx-bw/2-pad[0])/ratio/width));y1=max(0.,min(1.,(cy-bh/2-pad[1])/ratio/height))
        x2=max(0.,min(1.,(cx+bw/2-pad[0])/ratio/width));y2=max(0.,min(1.,(cy+bh/2-pad[1])/ratio/height))
        if x2<=x1 or y2<=y1:continue
        found.append({'id':feature,'rawClassId':raw,'confidence':score,'region':[x1,y1,x2-x1,y2-y1]})
    kept=[]
    for row in sorted(found,key=lambda item:-item['confidence']):
        # Match the measured pilot's raw-head NMS and 200-box ceiling before
        # collapsing semantically broader UI aliases. Scores below .35 cannot
        # displace any retained >=.35 item in the descending pilot ranking.
        if any(old['rawClassId']==row['rawClassId'] and _iou(old['region'],row['region'])>.45 for old in kept):continue
        kept.append(row)
        if len(kept)>=200:break
    canonical=[]
    for row in kept:
        if any(old['id']==row['id'] and _iou(old['region'],row['region'])>.45 for old in canonical):continue
        canonical.append(row)
    groups={}
    for row in canonical:
        candidate=groups.setdefault(row['id'],{'id':row['id'],'label':FEATURE_LABELS[row['id']],
            'confidence':row['confidence'],'count':None,'status':'candidate','autoEligible':False,
            'autoCountEligible':False,'evidence':[]})
        if len(candidate['evidence'])<8:candidate['evidence'].append({'kind':'object-detection','region':row['region'],
            'confidence':row['confidence'],'photoId':digest,'originalId':digest,'provider':PROVIDER,
            'model':MODEL,'source':'local-photo','rawClassId':row['rawClassId']})
    return list(groups.values())


def _infer(directory,content,mime):
    started=time.monotonic();model,_=load_artifact(directory)
    timings={'artifactReadSeconds':time.monotonic()-started};imported=time.monotonic()
    import numpy as np
    import onnxruntime as ort
    import cv2
    from PIL import Image,ImageOps
    timings['runtimeImportSeconds']=time.monotonic()-imported
    if not isinstance(content,bytes) or not 0<len(content)<=MAX_BYTES:raise ValueError('Нужна фотография до12МБ')
    prepared=time.monotonic();expected={'image/jpeg':'JPEG','image/png':'PNG','image/webp':'WEBP'}.get(mime)
    with warnings.catch_warnings():
        warnings.simplefilter('error',Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(content)) as original:
            if (expected is None or original.format!=expected or getattr(original,'n_frames',1)!=1
                    or original.width*original.height>40_000_000 or min(original.size)<32):raise ValueError('Неверное изображение или MIME')
            with ImageOps.exif_transpose(original) as oriented:image=oriented.convert('RGB')
    width,height=image.size;ratio=min(SIZE/width,SIZE/height);scaled=(round(width*ratio),round(height*ratio))
    array=np.asarray(image)
    if scaled!=(width,height):array=cv2.resize(array,scaled,interpolation=cv2.INTER_LINEAR)
    dw=(SIZE-scaled[0])/2;dh=(SIZE-scaled[1])/2
    top,bottom=round(dh-.1),round(dh+.1);left,right=round(dw-.1),round(dw+.1)
    array=cv2.copyMakeBorder(array,top,bottom,left,right,cv2.BORDER_CONSTANT,value=(114,114,114))
    tensor=np.ascontiguousarray(array.transpose(2,0,1)[None]).astype(np.float32)/255.;image.close()
    timings['imagePrepareSeconds']=time.monotonic()-prepared
    options=ort.SessionOptions();options.intra_op_num_threads=2;options.inter_op_num_threads=1
    options.execution_mode=ort.ExecutionMode.ORT_SEQUENTIAL
    loaded=time.monotonic();session=ort.InferenceSession(model,sess_options=options,providers=['CPUExecutionProvider'])
    timings['sessionLoadSeconds']=time.monotonic()-loaded
    inputs=session.get_inputs()
    if len(inputs)!=1 or inputs[0].shape!=[1,3,SIZE,SIZE] or inputs[0].type!='tensor(float)':raise ValueError('Неверный вход YOLO-World')
    before=time.monotonic();outputs=session.run(None,{inputs[0].name:tensor});seconds=time.monotonic()-before
    if len(outputs)!=1 or outputs[0].shape!=(1,25,ROWS):raise ValueError('Неверный выход YOLO-World')
    digest=hashlib.sha256(content).hexdigest()
    candidates=decode(outputs[0][0].T.tolist(),width,height,ratio,(left,top),digest)
    import platform
    return {'status':'review','provider':PROVIDER,'model':MODEL,'runtimeArchitecture':platform.machine(),
        'engine':'YOLO-World · локально · проверка','originalId':digest,'candidates':candidates,'autoApply':False,
        'diagnostics':{**timings,'inferenceSeconds':seconds,'workerTotalSeconds':time.monotonic()-started,'decoderVersion':DECODER_VERSION},
        'ai':sanitize_ai({'backend':PROVIDER,'model':MODEL,'operation':'furniture','phase':'complete',
            'tokenBased':False,'cached':False,'processedImages':1,'inferenceSeconds':seconds,
            **({'imagesPerSecond':1/seconds} if seconds>0 else {})}),
        'unknownIds':[key for key in FEATURE_LABELS if key!='bed' and key not in {row['id'] for row in candidates}],
        'message':'YOLO-World нашла предметы для проверки. Автоматическое подтверждение категорий не включено.',
        'limitations':['Категории не прошли независимый допуск автоматической публикации.',
            'Не найденный предмет остаётся неизвестным; высокий score не гарантирует точность.',
            'Количество, размеры, исправность и скрытая техника не определяются.']}


def _unavailable(code='PHOTO_WORKER_UNAVAILABLE',phase='unknown'):
    return {'status':'unavailable','provider':PROVIDER,'model':MODEL,'engine':'YOLO-World · локально · проверка',
        'candidates':[],'autoApply':False,'errorCode':code,'failurePhase':phase,
        'message':'YOLO-World не завершила проверку фото. Это не означает, что предметов нет.',
        'limitations':['Неполученный ответ не подтверждает отсутствие предметов.']}


def analyze(content,mime,directory,**kwargs):
    from trained_photo_detector import analyze as bounded_analyze
    return bounded_analyze(content,mime,directory,model=MODEL,**kwargs)


if __name__=='__main__':
    try:
        if len(sys.argv)!=3 or sys.argv[1]!='--worker':raise ValueError('Worker arguments')
        request=json.loads(sys.stdin.buffer.read(17_000_001))
        content=base64.b64decode(request['content'],validate=True)
        print(json.dumps(_infer(sys.argv[2],content,request['mime']),ensure_ascii=False,allow_nan=False))
    except ValueError:
        print(json.dumps(_unavailable('PHOTO_IMAGE_INVALID','image-or-model-validation'),ensure_ascii=False));raise SystemExit(1)
    except Exception:
        print(json.dumps(_unavailable('PHOTO_WORKER_FAILED','runtime-or-inference'),ensure_ascii=False));raise SystemExit(1)
