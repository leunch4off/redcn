#!/usr/bin/env python3
"""Verify actual saved MLX weights reproduce receipt loss and direct answers."""
import argparse
import json
import math
import os
from pathlib import Path
import platform
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'cli'))
from text_model_contract import answer_loss_mask, encode_record, file_sha, load_dataset, sampling_parameters
from text_native_evaluation import measure_stream


def compare_reload_loss(expected, losses):
    if type(expected) not in (float,int) or not math.isfinite(expected):
        raise ValueError('Receipt must contain a finite measured validation loss')
    if not losses or any(type(value) not in (float,int) for value in losses):
        raise ValueError('Reload must measure the same nonempty validation sample')
    reload_loss=sum(losses)/len(losses)
    finite=math.isfinite(reload_loss)
    difference=abs(reload_loss-expected) if finite else None
    return {'receiptValidationLoss':expected,'reloadedValidationLoss':reload_loss if finite else None,
            'absoluteDifference':difference,'lossReproduced':finite and difference<=1e-5}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('model','adapter','dataset','output'):parser.add_argument('--'+name,required=True)
    args=parser.parse_args()
    if platform.system()!='Darwin' or platform.machine()!='arm64':raise ValueError('Native ARM64 required')
    target=Path(args.output)
    if target.exists() or target.is_symlink():raise ValueError('New output required')
    receipt=json.loads((Path(args.adapter)/'trainreceipt.json').read_text())
    if receipt.get('datasetSha256')!=file_sha(args.dataset):raise ValueError('Dataset changed')
    if receipt.get('adapterSha256')!=file_sha(Path(args.adapter)/'adapters.safetensors'):raise ValueError('Weights changed')
    os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1'
    import mlx.core as mx
    import mlx.nn as nn
    from mlx_lm import load, stream_generate
    from mlx_lm.sample_utils import make_sampler
    mx.set_memory_limit(2500000000);mx.set_cache_limit(64000000)
    begin=time.monotonic();model,tokenizer=load(args.model,adapter_path=args.adapter);model.eval()
    data=load_dataset(args.dataset);rows={row['id']:row for row in data['splits']['validation']};losses=[]
    for identity in receipt['validationExampleIds']:
        row=encode_record(tokenizer,rows[identity],receipt['limits']['maxSequenceLength'])
        if row is None:raise ValueError('Reload sample unexpectedly exceeds length')
        tokens=mx.array([row['tokens']]);mask=mx.array(answer_loss_mask(tokens.shape[1],row['answerStart']))
        losses_at_tokens=nn.losses.cross_entropy(model(tokens[:,:-1]),tokens[:,1:])
        loss=(losses_at_tokens.astype(mx.float32)*mask).sum()/mask.sum();mx.eval(loss);losses.append(float(loss.item()))
    loss_comparison=compare_reload_loss(receipt['finalValidationLoss'],losses)
    samples=[]
    for split in ('train','validation'):
        for row in data['splits'][split][:2]:
            mx.random.seed(receipt['seed']);prompt=tokenizer.apply_chat_template(row['messages'][:-1],tokenize=False,add_generation_prompt=True)
            response_measurements=measure_stream(lambda: stream_generate(model,tokenizer,prompt=prompt,max_tokens=512,
                    sampler=make_sampler(**sampling_parameters(0)),prefill_step_size=256),
                    maximum_tokens=512,probe_seconds=55)
            samples.append({'split':split,'exampleId':row['id'],**response_measurements,
                'complete':response_measurements['status']=='measured'})
    result={'version':1,'adapterSha256':receipt['adapterSha256'],'datasetSha256':receipt['datasetSha256'],
        **loss_comparison,
        'validationExamples':len(losses),'directSamples':samples,'nativeArm64':True,
        'peakMemoryBytes':mx.get_peak_memory(),'elapsedSeconds':time.monotonic()-begin,
        'qualityAccepted':False,'directSamplesAreDiagnosticOnly':True}
    target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    descriptor=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(descriptor,'w') as stream:json.dump(result,stream,ensure_ascii=False,indent=2);stream.write('\n')
    print(json.dumps({key:value for key,value in result.items() if key!='directSamples'}))
    if not result['lossReproduced']:raise SystemExit(2)


if __name__=='__main__':main()
