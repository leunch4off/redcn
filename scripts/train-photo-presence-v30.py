"""Bounded teacher-presence adaptation of the existing local YOLOX classifier.

Only explicit positive/negative image-level labels contribute supervised loss.
Unknown classes receive frozen-model consistency, never invented absence labels.
No boxes are invented. Six classifier tensors change; proposals stay frozen.
Weights are candidates and are never activated by this offline experiment.
"""
import argparse
import copy
import hashlib
import importlib.util
import json
import math
import platform
from pathlib import Path
import random
import resource
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'cli'))
from photo_presence_experiment import read_manifest,content_input,presence_summary
from trained_photo_detector import CLASS_IDS,CLASS_ALIASES
CHECKPOINT_SHA='269d620af048697aa0580dd4615e262bf4073c1b6a7efdfbb0c497dd2580d61d'
_RUN_STATE={}


def module_at(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def hash_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def positive_indices(row):
    # Private broad cabinet/stove witnesses cannot teach a narrower raw class.
    # Public raw-class annotations retain their explicitly supplied taxonomy.
    if row.get('origin')=='public':return row['positiveRawIndices'],row['negativeRawIndices']
    positives=[i for i,key in enumerate(CLASS_IDS) if key in row['positiveIds'] and key not in CLASS_ALIASES]
    negatives=[i for i,key in enumerate(CLASS_IDS) if key in row.get('absentIds',[]) and key not in CLASS_ALIASES]
    return positives,negatives


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache',type=Path,required=True);parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--public-manifest',type=Path,required=True);parser.add_argument('--yolox-source',type=Path,required=True)
    parser.add_argument('--checkpoint',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--steps',type=int,default=240);parser.add_argument('--max-seconds',type=int,default=900)
    parser.add_argument('--learning-rate',type=float,default=0.00003);parser.add_argument('--seed',type=int,default=20261103)
    parser.add_argument('--cancel-file',type=Path);parser.add_argument('--export-onnx',action='store_true')
    args=parser.parse_args()
    if args.output.exists() or args.output.is_symlink():parser.error('Output must be a new experiment directory')
    if not 1<=args.steps<=1000 or not 60<=args.max_seconds<=1800 or not 0<args.learning_rate<=.001:parser.error('Invalid bounded settings')
    if args.checkpoint.is_symlink() or hash_file(args.checkpoint)!=CHECKPOINT_SHA:parser.error('Expected pinned first-pass weights; never silently reinitialize')
    rows,manifest_sha=read_manifest(args.manifest)
    train_private=[{**row,'origin':'assistant'} for row in rows if row['split']=='train' and any(positive_indices(row))]
    holdout=[row for row in rows if row['split']!='train']
    if not train_private or not holdout:parser.error('Need disjoint apartment groups for training and held-out evaluation')
    trainmod=module_at('photo_training_v30',ROOT/'scripts/train-tiny-photo-detector.py')
    pilot=module_at('photo_offline_v30',ROOT/'scripts/pilot-open-vocabulary-furniture.py')
    public=trainmod.validate_manifest(json.loads(args.public_manifest.read_text()),args.public_manifest.parent)
    if [row['id'] for row in public['classes']]!=CLASS_IDS:parser.error('Public and checkpoint taxonomy differ')
    # Current rental originals must not overlap attributed public train/holdout.
    if {row['originalId'] for row in rows}&{row['sha256'] for split in public['splits'].values() for row in split}:parser.error('Private/public content leakage')
    train_public=[]
    for row in public['splits']['train']:
        present=sorted({box['classIndex'] for box in row['boxes']})
        train_public.append({**row,'origin':'public','positiveRawIndices':present,
            'negativeRawIndices':sorted(set(row['verifiedClassIndices'])-set(present))})
    start=time.monotonic();random.seed(args.seed)
    import numpy as np
    import torch
    torch.set_num_threads(2);torch.manual_seed(args.seed);np.random.seed(args.seed)
    sys.path.insert(0,str(args.yolox_source.resolve()))
    from yolox.models import YOLOX,YOLOPAFPN,YOLOXHead
    channels=[256,512,1024]
    model=YOLOX(YOLOPAFPN(.33,.25,in_channels=channels,depthwise=True),YOLOXHead(21,.25,in_channels=channels,depthwise=True))
    for layer in model.modules():
        if isinstance(layer,torch.nn.BatchNorm2d):layer.eps=1e-3;layer.momentum=.03
    checkpoint=torch.load(args.checkpoint,map_location='cpu',weights_only=True)
    model.load_state_dict(checkpoint['model'],strict=True);model.eval()
    for name,param in model.named_parameters():param.requires_grad_(name.startswith('head.cls_preds.'))
    teacher=copy.deepcopy(model).eval()
    for param in teacher.parameters():param.requires_grad_(False)
    initial={name:param.detach().clone() for name,param in model.named_parameters() if param.requires_grad}
    optimizer=torch.optim.AdamW([p for p in model.parameters() if p.requires_grad],lr=args.learning_rate,weight_decay=.0001)
    from photo_store import asset
    args.output.mkdir(parents=True,mode=0o700)
    receipt={'version':1,'kind':'masked-assistant-presence-classifier-adaptation','status':'running',
        'runtimeArchitecture':platform.machine(),'device':'cpu','torchVersion':torch.__version__,
        'humanVerified':False,'exhaustive':False,'manifestSha256':manifest_sha,
        'publicManifestSha256':hash_file(args.public_manifest),'checkpointSha256':CHECKPOINT_SHA,
        'scriptSha256':hash_file(__file__),'trainingImages':len(train_private),'heldoutImages':len(holdout),
        'publicTrainingImages':len(train_public),'publicValidationImages':len(public['splits']['validation']),
        'seed':args.seed,'learningRate':args.learning_rate,'maxSteps':args.steps,'maxSeconds':args.max_seconds,
        'trainableParameters':sum(p.numel() for p in model.parameters() if p.requires_grad),
        'trainedLayers':['head.cls_preds.'],'objectnessTrained':False,'backboneTrained':False,
        'promoted':False,'automaticFacts':0,'steps':[],
        'limitations':['Assistant image-level positive witnesses are weak labels, not human bounding boxes.',
            'Unknown labels are masked from supervised loss; frozen-teacher consistency is not a negative label.',
            'Image-level maximum-score learning cannot validate box localization.',
            'Frozen proposals/objectness still limit recall for new categories.',
            'Held-out presence coverage alone cannot establish precision or automatic acceptance.',
            'The 80 public validation images have been used in previous experiments, not a new blind test.']}
    _RUN_STATE.update(receipt=receipt, output=args.output, started=start)
    def checkpoint_receipt():
        receipt['seconds']=time.monotonic()-start
        target=args.output/'trainreceipt.json';temporary=args.output/'trainreceipt.tmp'
        temporary.write_text(json.dumps(receipt,ensure_ascii=False,allow_nan=False)+'\n');temporary.chmod(0o600);temporary.replace(target)
    checkpoint_receipt()
    marker=args.cache/'learning/training.cancel'
    foreground_stamp=None
    resource_started=False
    def marker_stamp():
        if marker.is_symlink():raise ValueError('Foreground marker must not be a symlink')
        try:return marker.stat().st_mtime_ns
        except FileNotFoundError:return None
    def guard():
        if resource_started and marker_stamp()!=foreground_stamp:
            raise trainmod.TrainingCancelled('Foreground inference requested the model lease')
        if args.cancel_file is not None and (args.cancel_file.exists() or args.cancel_file.is_symlink()):raise trainmod.TrainingCancelled('Cancellation marker')
        if time.monotonic()-start>args.max_seconds:raise TimeoutError('Photo training budget exhausted')
    def tensor_for(row):
        if row.get('origin')=='public':content=Path(row['resolvedPath']).read_bytes()
        else:
            content,_,meta=asset(args.cache,row['originalId'])
            if meta.get('kind')!='original' or hashlib.sha256(content).hexdigest()!=row['originalId']:raise ValueError('Original changed')
        tensor,width,height,_,_=content_input(content,'baseline')
        return torch.from_numpy(tensor),width,height
    def evaluate_presence():
        records=[]
        with torch.no_grad():
            for row in holdout:
                guard();tensor,width,height=tensor_for(row)
                prediction=model(tensor)[0].cpu().tolist()
                decoded=trainmod.predictions_from_output(prediction,width,height,416)
                scores={}
                for item in decoded:
                    raw=CLASS_IDS[item['classIndex']];key=CLASS_ALIASES.get(raw,raw)
                    scores[key]=max(scores.get(key,0),item['score'])
                records.append({**row,'scores':scores})
        return {'metrics':presence_summary(records),'records':records}
    def public_eval():
        records=[]
        with torch.no_grad():
            for row in public['splits']['validation']:
                guard();tensor,_,width,height=trainmod.prepare_image(row,416)
                output=model(tensor[None])[0].cpu().tolist()
                records.append({'imageId':row['imageId'],'boxes':row['boxes'],
                    'verifiedClassIndices':row['verifiedClassIndices'],
                    'predictions':trainmod.predictions_from_output(output,width,height,416)})
        return {str(threshold):trainmod.detection_metrics(records,21,threshold) for threshold in trainmod.THRESHOLDS}
    from learning_scheduler import model_resource
    with model_resource(args.cache,kind='background',timeout=3),pilot.offline_network() as blocked:
        foreground_stamp=marker_stamp();resource_started=True
        print(json.dumps({'stage':'baseline','privateHeldout':len(holdout),'publicHeldout':len(public['splits']['validation'])}),flush=True)
        receipt['baselinePresence']=evaluate_presence();receipt['baselinePublic']=public_eval();checkpoint_receipt()
        schedule=list(train_private)+train_public
        rng=random.Random(args.seed);rng.shuffle(schedule)
        seen_private=set();seen_public=set();stopped=False
        try:
            for step in range(args.steps):
                guard()
                if step and step%len(schedule)==0:rng.shuffle(schedule)
                row=schedule[step%len(schedule)];positive,negative=positive_indices(row)
                tensor,_,_=tensor_for(row)
                with torch.no_grad():target=teacher(tensor)
                prediction=model(tensor)
                # Confidence is objectness * conditional class score. Pooling is
                # only a differentiable presence objective, never a box label.
                pooled=(prediction[:,:,4:5]*prediction[:,:,5:]).amax(dim=1)[0]
                zero=prediction.sum()*0
                pos=-torch.log(pooled[positive].clamp(min=1e-6)).mean() if positive else zero
                neg=-torch.log((1-pooled[negative]).clamp(min=1e-6)).mean() if negative else zero
                unknown=[i for i in range(21) if i not in positive and i not in negative]
                consistency=((prediction[:,:,[5+i for i in unknown]]-target[:,:,[5+i for i in unknown]])**2).mean() if unknown else zero
                # Weight anchoring prevents unconstrained positive-only drift.
                anchor=sum(((p-initial[name])**2).mean() for name,p in model.named_parameters() if name in initial)
                loss=pos+neg+10*consistency+.1*anchor
                if not torch.isfinite(loss):raise ValueError('Non-finite training loss')
                optimizer.zero_grad(set_to_none=True);loss.backward()
                torch.nn.utils.clip_grad_norm_([p for p in model.parameters() if p.requires_grad],5.)
                optimizer.step()
                if row['origin']=='assistant':seen_private.add(row['originalId'])
                else:seen_public.add(row['imageId'])
                info={'step':step+1,'loss':float(loss.detach()),'positiveLoss':float(pos.detach()),
                    'explicitNegativeLoss':float(neg.detach()),'consistencyLoss':float(consistency.detach()),'origin':row['origin']}
                receipt['steps'].append(info)
                if step==0 or (step+1)%20==0:
                    checkpoint_receipt()
                    print(json.dumps({'stage':'training',**info,'seconds':time.monotonic()-start}),flush=True)
        except (TimeoutError,trainmod.TrainingCancelled) as error:
            stopped=True;receipt['stopReason']=type(error).__name__
        receipt['completedSteps']=len(receipt['steps']);receipt['distinctPrivateImagesSeen']=len(seen_private)
        receipt['distinctPublicImagesSeen']=len(seen_public)
        receipt['changedTensors']=[name for name,p in model.named_parameters() if name in initial and not torch.equal(p.detach(),initial[name])]
        weights=args.output/'weights.pt';torch.save({'model':model.state_dict(),'classes':public['classes'],
            'inputSize':416,'autoApprovedIds':[],'labelOrigin':'assistant-positive-witnesses'},weights)
        receipt['weightsSha256']=hash_file(weights)
        receipt['status']='stopped-unaccepted' if stopped else 'trained-unaccepted'
        if not stopped:
            print(json.dumps({'stage':'heldout-evaluation','steps':receipt['completedSteps']}),flush=True)
            receipt['finalPresence']=evaluate_presence();receipt['finalPublic']=public_eval()
            receipt['publicComparison']=trainmod.comparison_metrics(receipt['baselinePublic'],receipt['finalPublic'],public['classes'])
            receipt['evaluationCoversSavedWeights']=True
            if args.export_onnx:
                guard();model.head.decode_in_inference=False
                target=args.output/'model.onnx'
                torch.onnx.export(model,torch.zeros(1,3,416,416),str(target),input_names=['images'],output_names=['output'],opset_version=11)
                receipt['onnx']={'path':target.name,'sha256':hash_file(target),'bytes':target.stat().st_size,
                    'inputShape':[1,3,416,416],'classIds':CLASS_IDS,'outputEncoding':'raw-yolox'}
        receipt['networkAttemptsBlocked']=len(blocked)
    receipt['seconds']=time.monotonic()-start
    rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss;receipt['processMaxRssBytes']=rss if sys.platform=='darwin' else rss*1024
    out=args.output/'trainreceipt.json';out.write_text(json.dumps(receipt,ensure_ascii=False,allow_nan=False)+'\n');out.chmod(0o600)
    print(json.dumps({'stage':'complete','status':receipt['status'],'steps':receipt['completedSteps'],
        'changedTensors':receipt['changedTensors'],'seconds':receipt['seconds'],
        'baselinePresence':receipt['baselinePresence']['metrics']['0.35']['totals'],
        'finalPresence':receipt.get('finalPresence',{}).get('metrics',{}).get('0.35',{}).get('totals'),
        'publicAt035':receipt.get('publicComparison',{}).get('0.35'),'promoted':False}),flush=True)


if __name__=='__main__':
    try:main()
    except BaseException as error:
        if _RUN_STATE:
            receipt=_RUN_STATE['receipt'];receipt.update(status='failed-unaccepted',failureType=type(error).__name__,
                completedSteps=len(receipt['steps']),seconds=time.monotonic()-_RUN_STATE['started'],promoted=False)
            out=_RUN_STATE['output']/'trainreceipt.json'
            out.write_text(json.dumps(receipt,ensure_ascii=False,allow_nan=False)+'\n');out.chmod(0o600)
        raise
