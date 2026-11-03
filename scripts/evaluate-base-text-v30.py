#!/usr/bin/env python3
"""Compare an installed base model on the frozen diagnostic, without an adapter.

No network, installation, training, or model selection occurs. An optional
installed model path allows a measured 1.5B feasibility probe on the same Mac.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'cli'))
from text_model_contract import file_sha, inference_model, sampling_parameters


def write(path,value):
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    temporary.chmod(0o600);temporary.replace(path)


def worker(args):
    import mlx.core as mx
    from mlx_lm import load,stream_generate
    from mlx_lm.sample_utils import make_sampler
    from text_native_evaluation import measure_stream
    from text_quality import translation_checks
    mx.set_memory_limit(2_500_000_000);mx.set_cache_limit(64_000_000)
    identity=inference_model(json.loads((args.model/'config.json').read_text()))
    model,tokenizer=load(str(args.model))
    data=json.loads(args.heldout.read_text())
    result={'model':identity,'baseWithoutAdapter':True,'adapterSha256':None,
        'heldoutSha256':file_sha(args.heldout),'modelConfigSha256':file_sha(args.model/'config.json'),
        'sourceHashes':{str(path.relative_to(ROOT)):file_sha(path) for path in [Path(__file__),ROOT/'cli/text_quality.py',ROOT/'cli/text_native_evaluation.py']},
        'requested':len(data['cases']),'rows':[],'qualityAccepted':False,'selectedWeightsChanged':False}
    start=time.monotonic()
    for row in data['cases']:
        mx.random.seed(20261103)
        prompt=tokenizer.apply_chat_template(row['messages'][:-1],tokenize=False,add_generation_prompt=True)
        measured=measure_stream(lambda:stream_generate(model,tokenizer,prompt=prompt,max_tokens=160,
            sampler=make_sampler(**sampling_parameters(0))),maximum_tokens=160,probe_seconds=30)
        source=json.loads(row['messages'][-2]['content'])['text']
        direction=row['task'].removeprefix('translation-')
        checks=translation_checks(source,measured['text'],direction)
        result['rows'].append({'id':row['id'],'source':source,'reference':row['messages'][-1]['content'],
            'direction':direction,**measured,'automaticChecks':checks,'automaticFactsPassed':checks['passed']})
        result.update(completed=sum(r['status']=='measured' for r in result['rows']),
            automaticFactsPassed=sum(r['status']=='measured' and r['automaticFactsPassed'] for r in result['rows']),
            elapsedSeconds=time.monotonic()-start,peakMemoryBytes=int(mx.get_peak_memory()))
        write(args.output,result)
    print(json.dumps({k:v for k,v in result.items() if k!='rows'},ensure_ascii=False),flush=True)


def run(args):
    from learning_scheduler import model_resource,active_imports
    from local_text_model import settings
    from isolated_job_supervisor import IsolatedJobSupervisor
    from training_resources import ResourceGuard
    config=settings(args.cache)
    args.model=args.model or Path(config['modelPath'])
    if args.output.exists():raise ValueError('Use a new output file')
    args.output.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    resources=ResourceGuard()
    if resources.check(admission=True):raise ValueError('Memory admission blocked the base probe')
    foreground=args.cache/'learning/foreground.json'
    before=file_sha(foreground) if foreground.is_file() else None
    protected=file_sha(args.cache/'text-ai.json')
    def busy():return active_imports(args.cache)!=0 or (file_sha(foreground) if foreground.is_file() else None)!=before
    supervisor=IsolatedJobSupervisor(busy=busy,seconds=600)
    last=0
    def poll(process):
        nonlocal last
        if time.monotonic()-last>=2:
            last=time.monotonic();reason=resources.check(process.pid)
            if reason:supervisor.cancel('resources-'+reason)
    env={**os.environ,'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1','TOKENIZERS_PARALLELISM':'false'}
    with model_resource(args.cache,kind='background',timeout=0) as fd,supervisor:
        with args.output.with_suffix('.log').open('xb') as log:
            process=supervisor.spawn(['/usr/bin/arch','-arm64',config['pythonPath'],str(Path(__file__)),'--worker',
                '--model',str(args.model),'--heldout',str(args.heldout),'--output',str(args.output)],
                stdout=log,stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL,env=env,pass_fds=(fd,))
            supervisor.wait(process,timeout=580,poll_hook=poll)
        if process.returncode:raise ValueError('Native base probe failed; see private log')
    result=json.loads(args.output.read_text())
    result.update(resources=resources.receipt(),supervisor=supervisor.receipt(),
        protectedSettingsUnchanged=file_sha(args.cache/'text-ai.json')==protected)
    write(args.output,result)
    print(json.dumps({k:v for k,v in result.items() if k not in {'rows','resources'}},ensure_ascii=False),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker',action='store_true')
    parser.add_argument('--cache',type=Path)
    parser.add_argument('--model',type=Path)
    parser.add_argument('--heldout',type=Path,default=ROOT/'data/quality/teacher-rental-heldout-v30.json')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.worker:worker(args)
    elif args.cache:run(args)
    else:parser.error('Cache is required for a supervised local probe')
