"""Produce a public aggregate receipt without rental images, addresses or IDs."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def summarize(comparison=None,training=None):
    out={'version':1,'kind':'photo-presence-v30-public-summary','humanVerified':False,
        'exhaustiveLabels':False,'automaticAcceptance':False,
        'limitations':['Labels come from assistant visual review, not a human annotation team.',
            'Only explicitly labelled positives and negatives are evaluated; other classes remain unknown.',
            'Image-level presence coverage is not global accuracy, localization accuracy or approval for automatic facts.',
            'No private image, listing ID, address or source URL is included.']}
    if comparison:
        value=json.loads(Path(comparison).read_text())
        out['comparison']={'receiptSha256':digest(comparison),'manifestSha256':value['manifestSha256'],
            'scriptSha256':value['scriptSha256'],'images':value['images'],'splits':value['splits'],
            'seconds':value['seconds'],'processMaxRssBytes':value['processMaxRssBytes'],
            'weightsChanged':False,'engines':{key:{field:row[field] for field in ('modelSha256',
                'modelLoadSeconds','inferenceTotalSeconds','images','metrics')} for key,row in value['engines'].items()}}
    if training:
        value=json.loads(Path(training).read_text())
        fields=('kind','status','runtimeArchitecture','manifestSha256','publicManifestSha256','checkpointSha256','scriptSha256',
            'trainingImages','heldoutImages','publicTrainingImages','publicValidationImages','seed','learningRate',
            'maxSteps','maxSeconds','trainableParameters','trainedLayers','objectnessTrained','backboneTrained',
            'completedSteps','distinctPrivateImagesSeen','distinctPublicImagesSeen','changedTensors','weightsSha256',
            'seconds','processMaxRssBytes','evaluationCoversSavedWeights','publicComparison','onnx',
            'stopReason','failureType','networkAttemptsBlocked')
        report={field:value[field] for field in fields if field in value}
        report.update(receiptSha256=digest(training),promoted=False,automaticFacts=0,
            seenStepOrigins=dict(Counter(row['origin'] for row in value.get('steps',[]))))
        for phase in ('baselinePresence','finalPresence'):
            if phase in value:report[phase]=value[phase]['metrics']
        report['loss']={'first':value.get('steps',[{}])[0].get('loss') if value.get('steps') else None,
            'last':value.get('steps',[{}])[-1].get('loss') if value.get('steps') else None,
            'interpretation':'Training objective only; not evidence of accepted detection accuracy.'}
        out['training']=report
    return out


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--comparison',type=Path);parser.add_argument('--training',type=Path)
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    if not args.comparison and not args.training:parser.error('At least one actual receipt is required')
    if args.output.exists() or args.output.is_symlink():parser.error('Use a new output')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(summarize(args.comparison,args.training),ensure_ascii=False,indent=2,allow_nan=False)+'\n')


if __name__=='__main__':main()
