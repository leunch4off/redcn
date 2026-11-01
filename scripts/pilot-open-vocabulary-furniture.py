"""Explicit, offline YOLO-World candidate on the pinned public holdout.

Never opens an application controller, changes a provider, trains weights or
publishes furniture facts. Download/install preparation is intentionally outside
this script. All image reads are hash-verified Open Images validation records.
Export uses the hash-pinned official checkpoint; evaluation loads ONNX only.
"""
import argparse
from contextlib import contextmanager
import gc
import hashlib
import importlib.metadata
import importlib.util
import json
import math
import os
from pathlib import Path
import platform
import resource
import socket
import sqlite3
import statistics
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'cli'))
SOURCE_MANIFEST_SHA='33b741b0f9162379ae58c9cd23dd03ee73c9fc9c5a875abf22f7dcfaf0416e0c'
READY_MANIFEST_SHA='dc53ecb5e38e0a87e2fe6084c1e6738ab33c3c7f5016b9b272e7f047b49151ee'
WEIGHTS_SHA='9b2c17ab6124a913e9b3a5c170617920d91b0f01111a8479da69f00e2cf27792'
ENCODER_SHA='40d365715913c9da98579312b702a82c18be219cc2a73407c4526f58eba950af'
CLIP_COMMIT='d05afc436d78f1c48dc0dbf8e5980a9d471f35f6'
BASELINE_SHA='650c6d96cd8b9e0c0422174467590b6ec377bc7fefa6c6ff01b0546db5392de5'
CLASS_IDS=['sofa','bedAny','chairs','tv','fridge','oven','microwave','sink','table','desk',
    'wardrobe','washer','dishwasher','gasStove','piano','cabinetry','cupboard',
    'coffeeMachine','mirror','curtains','coffeeTable']
PROMPTS=['sofa','bed','chair','television','refrigerator','oven','microwave oven','sink',
    'dining table','desk','wardrobe','washing machine','dishwasher','gas stove','piano',
    'cabinetry','cupboard','coffee maker','mirror','curtain','coffee table']
SCORE=.35
IOU=.5
START=time.monotonic()


def digest(path):
    value=hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda:source.read(1024*1024),b''):value.update(chunk)
    return value.hexdigest()


def verified_file(path,sha,limit):
    path=Path(path)
    if path.is_symlink() or not path.is_file() or not 0<path.stat().st_size<=limit or digest(path)!=sha:
        raise ValueError('Pinned experiment artifact is missing or changed: '+path.name)
    return path


def active_imports(cache):
    path=Path(cache)/'import-diagnostics/campaigns.sqlite3'
    if path.is_symlink() or not path.is_file():raise ValueError('Read-only import-activity guard unavailable')
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=2) as db:
        db.execute('PRAGMA query_only=ON')
        return db.execute('SELECT COUNT(*) FROM attempts WHERE finished IS NULL').fetchone()[0]


def guard(args):
    if active_imports(args.cache):raise ValueError('A real import is active; experiment stops without modifying it')
    if time.monotonic()-START>args.max_seconds:raise TimeoutError('Bounded experiment time limit reached')


@contextmanager
def offline_network():
    """No incidental telemetry, model URL or future asset retrieval is allowed."""
    old_connect=socket.socket.connect
    blocked=[]
    def connect(sock,address):
        if sock.family in (socket.AF_INET,socket.AF_INET6):
            blocked.append(str(address[0])[:100] if isinstance(address,tuple) else 'network')
            raise OSError('This public-image experiment is offline')
        return old_connect(sock,address)
    socket.socket.connect=connect
    try:yield blocked
    finally:socket.socket.connect=old_connect


def public_dataset(path):
    path=verified_file(path,READY_MANIFEST_SHA,2_000_000)
    value=json.loads(path.read_text())
    if value.get('source')!='Open Images V7 boxes' or [row['id'] for row in value['classes']]!=CLASS_IDS:
        raise ValueError('Wrong public dataset or class order')
    root=path.parent.resolve();rows=value['splits']['validation']
    if len(rows)!=80:raise ValueError('The frozen holdout must contain exactly 80 public images')
    seen=set();hashes=set();prepared=[]
    train_ids={row['imageId'] for row in value['splits']['train']}
    train_hashes={row['sha256'] for row in value['splits']['train']}
    for row in rows:
        image_id=row['imageId']
        import re
        if not re.fullmatch('[0-9a-f]{16}',image_id) or image_id in seen or image_id in train_ids:
            raise ValueError('Invalid, repeated or training-overlapping public image ID')
        relative='images/validation/'+image_id+'.jpg'
        if (row.get('path')!=relative or row.get('sourceSplit')!='validation'
                or row.get('sourceUrl')!='https://open-images-dataset.s3.amazonaws.com/validation/'+image_id+'.jpg'
                or row.get('status')!='downloaded'
                or row.get('attribution',{}).get('License')!='https://creativecommons.org/licenses/by/2.0/'):
            raise ValueError('Image lacks the frozen public validation provenance')
        target=root/relative
        if root not in target.resolve().parents or any(part.is_symlink() for part in (target,target.parent,target.parent.parent)):
            raise ValueError('Public image path escaped its declared dataset')
        verified_file(target,row['sha256'],12_000_000)
        if target.stat().st_size!=row['bytes'] or row['sha256'] in hashes or row['sha256'] in train_hashes:
            raise ValueError('Image content changed or overlaps training')
        for box in row['boxes']:
            if type(box.get('classIndex')) is not int or not 0<=box['classIndex']<21:
                raise ValueError('Unknown annotation class')
            p=box['xyxy']
            if len(p)!=4 or any(type(x) not in (int,float) or not math.isfinite(x) for x in p) or not 0<=p[0]<p[2]<=1 or not 0<=p[1]<p[3]<=1:
                raise ValueError('Invalid public annotation')
        verified=row['verifiedClassIndices']
        if not isinstance(verified,list) or any(type(x) is not int or not 0<=x<21 for x in verified):
            raise ValueError('Unknown verified class')
        seen.add(image_id);hashes.add(row['sha256']);prepared.append({**row,'resolvedPath':str(target)})
    return value['classes'],prepared


def metrics_module():
    path=ROOT/'scripts/train-tiny-photo-detector.py'
    spec=importlib.util.spec_from_file_location('redcn_public_detection_metrics',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def packages(names):
    result={}
    for name in names:
        try:result[name]=importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:result[name]=None
    return result


def verify_clip_package():
    """A same-named fork must not silently replace the approved MIT encoder code."""
    value=json.loads(importlib.metadata.distribution('clip').read_text('direct_url.json') or '{}')
    if value.get('url')!='https://github.com/openai/CLIP.git' or value.get('vcs_info',{}).get('commit_id')!=CLIP_COMMIT:
        raise ValueError('Expected the pinned official OpenAI CLIP source, not a same-named fork')
    return value


def memory():
    maximum=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {'processMaxRssBytes':maximum if sys.platform=='darwin' else maximum*1024,
        'source':'getrusage(RUSAGE_SELF); Darwin bytes, Linux KiB converted; not unified-memory total'}


def write_report(path,value):
    path=Path(path)
    if path.exists() or path.is_symlink():raise ValueError('Use a new report path; previous measurements are immutable')
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x') as destination:json.dump(value,destination,ensure_ascii=False,indent=2,allow_nan=False);destination.write('\n')
    path.chmod(0o600)


def export_candidate(args,rows,blocked):
    pilot=Path(args.pilot_root).resolve()
    if pilot.is_symlink():raise ValueError('Experiment directory cannot be a symlink')
    weights=verified_file(pilot/'yolov8s-worldv2.pt',WEIGHTS_SHA,40_000_000)
    encoder=verified_file(pilot/'ViT-B-32.pt',ENCODER_SHA,450_000_000)
    target=pilot/'export-v1'
    target.mkdir(mode=0o700)
    old=os.getcwd();os.chdir(pilot)
    try:
        imports=time.monotonic()
        import torch
        torch.set_num_threads(2)
        clip_source=verify_clip_package()
        import clip
        from ultralytics import YOLOWorld,settings
        settings.update({'sync':False})
        if importlib.metadata.version('ultralytics')!='8.4.197':raise ValueError('Wrong pinned Ultralytics package')
        imports=time.monotonic()-imports;guard(args)
        load_start=time.monotonic();model=YOLOWorld(str(weights));load_seconds=time.monotonic()-load_start
        # Explicitly bind the upstream MIT CLIP checkpoint; neither a default
        # user cache nor an encoder URL is consulted during this offline run.
        original_load=clip.load
        def local_encoder(name,*positional,**keyword):
            if name!='ViT-B/32':raise ValueError('Unexpected text encoder request')
            keyword['download_root']=str(pilot)
            return original_load(str(encoder),*positional,**keyword)
        clip.load=local_encoder
        prompt_start=time.monotonic()
        try:model.set_classes(PROMPTS)
        finally:clip.load=original_load
        prompt_seconds=time.monotonic()-prompt_start
        import numpy as np
        profile=target/'furniture-21.npz'
        pe=model.model.txt_feats.detach().cpu().float().numpy()
        if pe.shape!=(1,21,512) or not np.isfinite(pe).all():raise ValueError('Unexpected fixed prompt embeddings')
        np.savez_compressed(profile,embeddings=pe,names=np.asarray(PROMPTS,dtype=np.str_))
        count=sum(parameter.numel() for parameter in model.model.parameters())
        names=model.model.names
        if (list(names.values()) if isinstance(names,dict) else list(names))!=PROMPTS:raise ValueError('Prompt class order changed')
        if any('clip_model' in name for name,_ in model.model.named_modules()):raise ValueError('Detector still contains a text encoder')
        checkpoint=target/'furniture-21-worldv2.pt';model.save(str(checkpoint))
        del model;gc.collect();guard(args)
        detector=YOLOWorld(str(checkpoint))
        guard(args);export_start=time.monotonic()
        exported=Path(detector.export(format='onnx',imgsz=640,batch=1,dynamic=False,device='cpu',simplify=False,opset=17,nms=False))
        if not exported.is_file() or exported.is_symlink() or target not in exported.resolve().parents:
            raise ValueError('Unexpected candidate export path')
        return {'kind':'redcn-isolated-yoloworld-export','status':'exported-unaccepted','weightsOptimized':False,
            'sourceCheckpointSha256':WEIGHTS_SHA,'textEncoderSha256':ENCODER_SHA,
            'textEncoderCode':clip_source,
            'ultralyticsVersion':'8.4.197','license':'Author YOLO-World GPL-3.0; Ultralytics implementation AGPL-3.0 / Enterprise; OpenAI CLIP MIT; no production approval',
            'classIds':CLASS_IDS,'prompts':PROMPTS,'promptSha256':digest(profile),'modelParameters':count,
            'onnxFile':str(exported),'onnxSha256':digest(exported),'onnxBytes':exported.stat().st_size,
            'phaseSeconds':{'imports':imports,'loadCheckpoint':load_seconds,'encodePromptsOnce':prompt_seconds,
                'onnxExport':time.monotonic()-export_start},'packages':packages(['torch','torchvision','numpy','onnx','ultralytics','clip']),
            **memory(),'networkAttemptsBlocked':len(blocked),'automaticPromotion':False}
    finally:os.chdir(old)


def image_input(path,engine):
    import numpy as np
    from PIL import Image
    with Image.open(path) as raw:
        if raw.format!='JPEG' or getattr(raw,'n_frames',1)!=1 or raw.width*raw.height>40_000_000 or raw.getexif().get(274,1) not in (1,None):
            raise ValueError('Unexpected public image format or EXIF transform')
        image=raw.convert('RGB');width,height=image.size
    if engine=='baseline':
        size=416;ratio=min(size/width,size/height)
        resized=image.resize((max(1,int(width*ratio)),max(1,int(height*ratio))),Image.Resampling.BILINEAR)
        array=np.full((size,size,3),114,dtype=np.float32)
        array[:resized.height,:resized.width]=np.asarray(resized)[:,:,::-1]
        tensor=np.ascontiguousarray(array.transpose(2,0,1)[None]);resized.close();pad=(0,0)
    else:
        import cv2
        size=640;ratio=min(size/width,size/height);scaled=(round(width*ratio),round(height*ratio))
        array=np.asarray(image)
        if scaled!=(width,height):array=cv2.resize(array,scaled,interpolation=cv2.INTER_LINEAR)
        dw=(size-scaled[0])/2;dh=(size-scaled[1])/2
        top,bottom=round(dh-.1),round(dh+.1);left,right=round(dw-.1),round(dw+.1)
        array=cv2.copyMakeBorder(array,top,bottom,left,right,cv2.BORDER_CONSTANT,value=(114,114,114))
        tensor=np.ascontiguousarray(array.transpose(2,0,1)[None]).astype(np.float32)/255.;pad=(left,top)
    image.close()
    return tensor,width,height,ratio,pad


def decode_world(output,width,height,ratio,pad,metric):
    if output.shape!=(1,25,8400):
        raise ValueError('Expected fixed 21-class YOLO-World output [1, 25, 8400]')
    candidates=[]
    for row in output[0].T.tolist():
        if len(row)!=25 or any(not math.isfinite(x) for x in row) or any(not 0<=x<=1 for x in row[4:]):
            raise ValueError('Malformed candidate output')
        cx,cy,bw,bh=row[:4];class_index=max(range(21),key=lambda index:row[4+index]);score=row[4+class_index]
        if score<.05:continue
        x1,y1,x2,y2=cx-bw/2,cy-bh/2,cx+bw/2,cy+bh/2
        point=[max(0.,min(1.,(x1-pad[0])/ratio/width)),max(0.,min(1.,(y1-pad[1])/ratio/height)),
            max(0.,min(1.,(x2-pad[0])/ratio/width)),max(0.,min(1.,(y2-pad[1])/ratio/height))]
        if point[0]<point[2] and point[1]<point[3]:candidates.append({'classIndex':class_index,'score':score,'xyxy':point})
    kept=[]
    for row in sorted(candidates,key=lambda row:-row['score']):
        if not any(old['classIndex']==row['classIndex'] and metric.iou(old['xyxy'],row['xyxy'])>.45 for old in kept):kept.append(row)
        if len(kept)>=200:break
    return kept


def decode_baseline(output,width,height,ratio,metric):
    if output.shape!=(1,3549,26):raise ValueError('Wrong pinned YOLOX raw output')
    candidates=[];index=0
    for stride in (8,16,32):
        for gy in range(416//stride):
            for gx in range(416//stride):
                row=output[0,index].tolist();index+=1
                if any(not math.isfinite(x) for x in row) or any(not 0<=x<=1 for x in row[4:]):raise ValueError('Malformed baseline output')
                cls=max(range(21),key=lambda i:row[5+i]);score=row[4]*row[5+cls]
                if score<.05 or abs(row[2])>20 or abs(row[3])>20:continue
                cx,cy=(row[0]+gx)*stride/ratio,(row[1]+gy)*stride/ratio
                bw,bh=math.exp(row[2])*stride/ratio,math.exp(row[3])*stride/ratio
                p=[max(0.,min(1.,(cx-bw/2)/width)),max(0.,min(1.,(cy-bh/2)/height)),max(0.,min(1.,(cx+bw/2)/width)),max(0.,min(1.,(cy+bh/2)/height))]
                if p[0]<p[2] and p[1]<p[3]:candidates.append({'classIndex':cls,'score':score,'xyxy':p})
    kept=[]
    for row in sorted(candidates,key=lambda row:-row['score']):
        if not any(old['classIndex']==row['classIndex'] and metric.iou(old['xyxy'],row['xyxy'])>.45 for old in kept):kept.append(row)
        if len(kept)>=200:break
    return kept


def evaluate(args,classes,rows,blocked):
    begin=time.monotonic()
    import numpy as np
    import onnxruntime as ort
    metric=metrics_module();imports=time.monotonic()-begin
    if args.engine=='baseline':model=verified_file(Path(args.cache)/'tools/photo-training/20261101-first-pass/run-01/model.onnx',BASELINE_SHA,8_000_000)
    else:
        receipt=json.loads((Path(args.pilot_root)/'export-receipt.json').read_text())
        if receipt['sourceCheckpointSha256']!=WEIGHTS_SHA or receipt['classIds']!=CLASS_IDS or receipt['prompts']!=PROMPTS:raise ValueError('Candidate receipt changed')
        model=verified_file(receipt['onnxFile'],receipt['onnxSha256'],100_000_000)
    options=ort.SessionOptions();options.intra_op_num_threads=2;options.inter_op_num_threads=1;options.execution_mode=ort.ExecutionMode.ORT_SEQUENTIAL
    load=time.monotonic();session=ort.InferenceSession(str(model),sess_options=options,providers=['CPUExecutionProvider']);load=time.monotonic()-load
    inp=session.get_inputs()
    size=416 if args.engine=='baseline' else 640
    if len(inp)!=1 or inp[0].type!='tensor(float)' or inp[0].shape!=[1,3,size,size]:raise ValueError('Unexpected image-only model input')
    records=[];timings=[];per_image=[]
    for row in rows:
        guard(args);start=time.monotonic();tensor,width,height,ratio,pad=image_input(row['resolvedPath'],args.engine)
        outputs=session.run(None,{inp[0].name:tensor})
        if len(outputs)!=1:raise ValueError('Unexpected number of model outputs')
        predictions=decode_baseline(outputs[0],width,height,ratio,metric) if args.engine=='baseline' else decode_world(outputs[0],width,height,ratio,pad,metric)
        seconds=time.monotonic()-start;timings.append(seconds)
        records.append({'imageId':row['imageId'],'boxes':row['boxes'],'verifiedClassIndices':row['verifiedClassIndices'],'predictions':predictions})
        per_image.append({'imageId':row['imageId'],'imageSha256':row['sha256'],'seconds':seconds,
            'verifiedPositiveClassIds':[CLASS_IDS[i] for i in row['verifiedClassIndices']],
            'predictedAboveThreshold':sum(pred['score']>=SCORE for pred in predictions)})
    totals=metric.detection_metrics(records,21,SCORE)
    warm=[]
    for _ in range(5):
        guard(args);start=time.monotonic();tensor,*_=image_input(rows[0]['resolvedPath'],args.engine);session.run(None,{inp[0].name:tensor});warm.append(time.monotonic()-start)
    ordered=sorted(timings);p95=ordered[math.ceil(.95*len(ordered))-1]
    return {'kind':'redcn-isolated-public-furniture-evaluation','status':'measured-unaccepted','engine':args.engine,
        'modelSha256':digest(model),'inputShape':inp[0].shape,'outputShape':list(outputs[0].shape),'providers':session.get_providers(),
        'images':len(records),'scoreThreshold':SCORE,'matchingIoU':IOU,'classIds':CLASS_IDS,'prompts':PROMPTS if args.engine=='world' else None,
        'totals':{key:sum(row[key] for row in totals) for key in ('tp','fp','fn','verifiedNegativeImages')},
        'classMetrics':[{'id':cls['id'],**row} for cls,row in zip(classes,totals)],'perImage':per_image,
        'phaseSeconds':{'runtimeImports':imports,'modelSessionLoad':load,'firstImage':timings[0],
            'imageP50':statistics.median(timings),'imageP95':p95,'imageMax':max(timings),'fiveWarmRepeatP50':statistics.median(warm)},
        'torchImported':'torch' in sys.modules,'clipImported':'clip' in sys.modules,
        'packages':packages(['numpy','onnxruntime','Pillow','opencv-python']),**memory(),
        'networkAttemptsBlocked':len(blocked),'automaticPromotion':False,'autoApply':False,
        'limitations':['Only explicitly verified positive image/class pairs count; unknown classes are not negatives.',
            'No independently verified negative image/class pairs; precision is conditional, not global accuracy.',
            'These same 80 public validation images were used for the prior first-pass model; this is not a new blind corpus.',
            'Images have dataset metadata declaring CC BY 2.0, not a new individual-license audit.',
            'Candidate 640px centered OpenCV RGB/255 letterbox; baseline 416px top-left Pillow BGR 0..255 preprocessing.',
            'Range hood, computer, watermark, hidden equipment, working condition and cross-view counts are not evaluated.']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase',choices=('export','evaluate'),required=True)
    parser.add_argument('--engine',choices=('world','baseline'),default='world')
    parser.add_argument('--manifest',type=Path,required=True);parser.add_argument('--pilot-root',type=Path,required=True)
    parser.add_argument('--cache',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--max-seconds',type=int,default=300)
    args=parser.parse_args()
    if not 30<=args.max_seconds<=600:parser.error('Use 30–600 seconds')
    if args.output.exists() or args.output.is_symlink():parser.error('The output report must be new')
    os.environ.update(YOLO_CONFIG_DIR=str(args.pilot_root/'config'),YOLO_AUTOINSTALL='false',YOLO_OFFLINE='true',NETRC='/dev/null',OMP_NUM_THREADS='2')
    classes,rows=public_dataset(args.manifest);guard(args)
    from learning_scheduler import model_resource
    with model_resource(args.cache,kind='foreground',timeout=3),offline_network() as blocked:
        guard(args)
        report=export_candidate(args,rows,blocked) if args.phase=='export' else evaluate(args,classes,rows,blocked)
    report.update(version=1,scriptSha256=digest(__file__),readyManifestSha256=READY_MANIFEST_SHA,
        originalTrainingManifestSha256=SOURCE_MANIFEST_SHA,platform={'system':platform.system(),'machine':platform.machine(),'python':sys.version},
        processSeconds=time.monotonic()-START,privateDataRead=False,activeProviderChanged=False)
    write_report(args.output,report)
    print(json.dumps({key:report.get(key) for key in ('status','engine','images','totals','phaseSeconds','processMaxRssBytes','processSeconds','onnxFile','onnxSha256','networkAttemptsBlocked')},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
