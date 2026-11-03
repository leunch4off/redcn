"""Bounded native photo pipeline probe using actual stored bytes and MIME.

No card, model setting, annotation or inference-cache writes. The normal shared
foreground model lease is used, so run after any explicit training experiment.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'cli'))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache',type=Path,required=True);parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--public-output',type=Path)
    parser.add_argument('--count',type=int,default=6);parser.add_argument('--original-id',action='append',default=[])
    parser.add_argument('--model',choices=('redcn-furniture-v1','redcn-furniture-world-v2'),
        help='Probe this pinned provider explicitly without changing settings; omit to exercise configured vision routing')
    parser.add_argument('--max-seconds',type=int,default=240);args=parser.parse_args()
    if not 1<=args.count<=10 or not 60<=args.max_seconds<=600:parser.error('Use 1–10 photos and 60–600 seconds')
    for path in (args.output,args.public_output):
        if path is not None and (path.exists() or path.is_symlink()):parser.error('Receipt outputs must be new')
    from photo_presence_experiment import read_manifest
    from photo_store import asset,open_photo
    from vision_photo import analyze_image
    from photo_ai_settings import load_config
    import trained_photo_detector
    rows,manifest_sha=read_manifest(args.manifest)
    valid={row['originalId'] for row in rows}
    if any(digest not in valid for digest in args.original_id):parser.error('Requested original must occur in the reviewed manifest')
    selected=list(dict.fromkeys(args.original_id+[row['originalId'] for row in rows]))[:args.count]
    config=load_config(args.cache)
    selected_model=args.model or config.get('model')
    if selected_model not in ('redcn-furniture-v1','redcn-furniture-world-v2') or (not args.model and not config.get('enabled')):
        parser.error('This probe measures a configured or explicitly selected pinned local provider')
    selected_detector=trained_photo_detector
    if selected_model=='redcn-furniture-world-v2':
        import world_photo_detector
        selected_detector=world_photo_detector
    report={'version':1,'kind':'native-photo-runtime-v30-probe','manifestSha256':manifest_sha,
        'scriptSha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'detectorSourceSha256':hashlib.sha256(Path(selected_detector.__file__).read_bytes()).hexdigest(),
        'model':selected_model,'modelSha256':selected_detector.MODEL_SHA,
        'route':'explicit-native-provider' if args.model else 'configured-vision-pipeline',
        'cardsWritten':False,'cacheWritten':False,'settingsChanged':False,'weightsChanged':False,
        'photos':[],'limitations':['A warm filesystem or OS page cache is not a controlled cold-boot experiment.',
            'Success validates runtime and image processing; suggestions do not establish detector accuracy.']}
    began=time.monotonic();end=began+args.max_seconds
    try:
        for digest in selected:
            if time.monotonic()>=end:raise TimeoutError('Whole probe budget reached')
            started=time.monotonic();content,mime,metadata=asset(args.cache,digest)
            if metadata.get('kind')!='original' or hashlib.sha256(content).hexdigest()!=digest:raise ValueError('Original byte identity mismatch')
            image=open_photo(content)
            try:
                if args.model:
                    from learning_scheduler import model_resource
                    from photo_auto_policy import trained_policy
                    with model_resource(args.cache,kind='foreground',timeout=3):
                        result=trained_policy(selected_detector.analyze(content,mime,args.cache,
                            deadline=min(end,time.monotonic()+60)),requested=config.get('autoApply') is True)
                else:
                    result=analyze_image(image,args.cache,content=content,mime=mime,deadline=min(end,time.monotonic()+60))
            finally:image.close()
            row={'originalId':digest,'actualMime':mime,'seconds':time.monotonic()-started,
                'status':result.get('status'),'errorCode':result.get('errorCode'),'failurePhase':result.get('failurePhase'),
                'provider':result.get('provider'),'model':result.get('model'),
                'runtime':result.get('runtime'),'diagnostics':result.get('diagnostics'),
                'candidateIds':[x.get('id') for x in result.get('candidates',[])],
                'automaticFacts':(result.get('autoApplySummary') or {}).get('automaticFacts',0)}
            report['photos'].append(row)
            print(json.dumps({'stage':'photo','number':len(report['photos']),'status':row['status'],
                'seconds':row['seconds'],'errorCode':row['errorCode'],'diagnostics':row['diagnostics']},ensure_ascii=False),flush=True)
    except BaseException as error:
        report.update(status='failed',failureType=type(error).__name__)
        raise
    finally:
        report['seconds']=time.monotonic()-began
        report['processed']=len(report['photos'])
        report['successful']=sum(row['status']=='review' and row['model']==selected_model for row in report['photos'])
        report['nativeArmVerified']=sum((row.get('runtime') or {}).get('selection')=='native-arm'
            and (row.get('runtime') or {}).get('status')=='verified'
            and (row.get('runtime') or {}).get('workerArchitecture')=='arm64' for row in report['photos'])
        report.setdefault('status','passed' if report['processed']==len(selected)==report['successful']==report['nativeArmVerified'] else 'failed')
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,ensure_ascii=False,allow_nan=False)+'\n');args.output.chmod(0o600)
        if args.public_output is not None:
            public={key:value for key,value in report.items() if key!='photos'}
            public['receiptSha256']=hashlib.sha256(args.output.read_bytes()).hexdigest()
            public['photos']=[{key:value for key,value in row.items() if key not in ('originalId','candidateIds')} for row in report['photos']]
            args.public_output.parent.mkdir(parents=True,exist_ok=True)
            args.public_output.write_text(json.dumps(public,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
        print(json.dumps({key:report[key] for key in ('status','processed','successful','nativeArmVerified','seconds')}),flush=True)


if __name__=='__main__':main()
