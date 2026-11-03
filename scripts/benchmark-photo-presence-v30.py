"""Compare existing local ONNX detectors on assistant-reviewed original photos.

Read-only original assets, no network, no card/settings writes, CPU 2 threads.
Unknown labels do not count as false positives or verified negatives.
"""
import argparse
from collections import Counter
import hashlib
import importlib.util
import json
from pathlib import Path
import resource
import socket
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'cli'))
from photo_presence_experiment import read_manifest,content_input,presence_summary
from trained_photo_detector import CLASS_IDS,CLASS_ALIASES,MODEL_SHA

WORLD_SHA='eb56e6e04067350e6bb4c9603a4664707ec7620f3a3dda81c1ddd8fd9d580bba'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache',type=Path,required=True);parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--max-seconds',type=int,default=1200)
    parser.add_argument('--engines',nargs='+',choices=['baseline','world'],default=['baseline','world'])
    args=parser.parse_args()
    if args.output.exists() or args.output.is_symlink() or not 60<=args.max_seconds<=1800:parser.error('Use a new output and 60–1800 seconds')
    start=time.monotonic();rows,manifest_sha=read_manifest(args.manifest)
    import numpy as np
    import onnxruntime as ort
    from photo_store import asset
    spec=importlib.util.spec_from_file_location('photo_pilot_v30',ROOT/'scripts/pilot-open-vocabulary-furniture.py')
    pilot=importlib.util.module_from_spec(spec);spec.loader.exec_module(pilot);metric=pilot.metrics_module()
    models={'baseline':(args.cache/'tools/photo-training/20261101-first-pass/run-01/model.onnx',MODEL_SHA),
        'world':(args.cache/'tools/yoloe-pilot/world-v2/export-v1/furniture-21-worldv2.onnx',WORLD_SHA)}
    report={'version':1,'kind':'assistant-witness-detector-comparison','manifestSha256':manifest_sha,
        'humanVerified':False,'exhaustive':False,'weightsChanged':False,'autoAccepted':False,
        'images':len(rows),'splits':dict(Counter(row['split'] for row in rows)),
        'scriptSha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'engines':{},
        'limitations':['Image-level assistant witnesses are not exhaustive boxes or human acceptance.',
          'Unknown classes remain unknown; unlabelled predictions are not false positives.',
          'Shared image bytes join groups before splitting; unseen property aliases may remain.',
          'Presence coverage does not measure bounding-box localization or publication precision.']}
    from learning_scheduler import model_resource
    with model_resource(args.cache,kind='background',timeout=3),pilot.offline_network() as blocked:
        marker=args.cache/'learning/training.cancel'
        if marker.is_symlink():raise ValueError('Foreground marker must not be a symlink')
        stamp=marker.stat().st_mtime_ns if marker.exists() else None
        for engine in args.engines:
            model,sha=models[engine];pilot.verified_file(model,sha,60_000_000)
            options=ort.SessionOptions();options.intra_op_num_threads=2;options.inter_op_num_threads=1
            options.execution_mode=ort.ExecutionMode.ORT_SEQUENTIAL
            loaded=time.monotonic();session=ort.InferenceSession(str(model),sess_options=options,providers=['CPUExecutionProvider'])
            load_seconds=time.monotonic()-loaded;name=session.get_inputs()[0].name;records=[];timings=[]
            for i,row in enumerate(rows):
                if time.monotonic()-start>args.max_seconds:raise TimeoutError('Experiment deadline reached')
                if marker.is_symlink() or (marker.stat().st_mtime_ns if marker.exists() else None)!=stamp:
                    raise TimeoutError('Foreground inference requested the model lease')
                content,mime,metadata=asset(args.cache,row['originalId'])
                if metadata.get('kind')!='original' or hashlib.sha256(content).hexdigest()!=row['originalId']:raise ValueError('Original changed')
                began=time.monotonic();tensor,width,height,ratio,pad=content_input(content,engine)
                outputs=session.run(None,{name:tensor})
                found=pilot.decode_baseline(outputs[0],width,height,ratio,metric) if engine=='baseline' else pilot.decode_world(outputs[0],width,height,ratio,pad,metric)
                scores={};boxes=[]
                for prediction in found:
                    raw=CLASS_IDS[prediction['classIndex']];key=CLASS_ALIASES.get(raw,raw)
                    scores[key]=max(scores.get(key,0),prediction['score'])
                    if prediction['score']>=.35: boxes.append({'id':key,'rawClassId':raw,'confidence':prediction['score'],'xyxy':prediction['xyxy']})
                timings.append(time.monotonic()-began)
                records.append({**row,'scores':scores,'predictions':boxes})
                if i%50==0:print(json.dumps({'phase':'evaluate','engine':engine,'images':i+1,'total':len(rows),'seconds':time.monotonic()-start}),flush=True)
            report['engines'][engine]={'modelSha256':sha,'modelLoadSeconds':load_seconds,
                'inferenceTotalSeconds':sum(timings),'images':len(records),'metrics':presence_summary(records),'records':records}
            del session
        report['networkAttemptsBlocked']=len(blocked)
    report['seconds']=time.monotonic()-start
    rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    report['processMaxRssBytes']=rss if sys.platform=='darwin' else rss*1024
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x') as stream:json.dump(report,stream,ensure_ascii=False,allow_nan=False);stream.write('\n')
    args.output.chmod(0o600)
    print(json.dumps({'phase':'complete','seconds':report['seconds'],'images':len(rows),
        'metrics':{key:value['metrics']['0.35']['totals'] for key,value in report['engines'].items()}},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
