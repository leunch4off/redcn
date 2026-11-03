#!/usr/bin/env python3
"""Run one reproducible teacher experiment without selecting its new weights."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'cli'))
from text_model_contract import file_sha, load_dataset, sampling_parameters


def write(path, value):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')
    temporary.chmod(0o600)
    temporary.replace(path)


def fold(value):
    return re.sub(r'[\s。，、；：！？,.!?;:]+', '', value).casefold()


def worker(args):
    import mlx.core as mx
    from mlx_lm import load, stream_generate
    from mlx_lm.sample_utils import make_sampler
    from text_native_evaluation import measure_stream
    from text_quality import translation_checks
    mx.set_memory_limit(2_500_000_000)
    mx.set_cache_limit(64_000_000)
    rows = json.loads(args.heldout.read_text())['cases']
    model, tokenizer = load(str(args.model), adapter_path=str(args.adapter))
    output = {'heldoutSha256':file_sha(args.heldout),'adapterSha256':file_sha(args.adapter/'adapters.safetensors'),
        'modelConfigSha256':file_sha(args.model/'config.json'),'requested':len(rows),'rows':[],
        'humanLanguageAcceptance':False,'automaticPromotion':False}
    started = time.monotonic()
    for row in rows:
        mx.random.seed(20261103)
        prompt = tokenizer.apply_chat_template(row['messages'][:-1], tokenize=False, add_generation_prompt=True)
        measured = measure_stream(lambda:stream_generate(model,tokenizer,prompt=prompt,max_tokens=160,
            sampler=make_sampler(**sampling_parameters(0))),maximum_tokens=160,probe_seconds=30)
        source = json.loads(row['messages'][-2]['content'])['text']
        direction = row['task'].removeprefix('translation-')
        quality = translation_checks(source, measured['text'], direction)
        output['rows'].append({'id':row['id'],'direction':direction,'source':source,
            'reference':row['messages'][-1]['content'],**measured,'automaticFactsPassed':quality['passed'],
            'automaticChecks':quality,'exactReferenceMatch':fold(measured['text'])==fold(row['messages'][-1]['content'])})
        output.update(completed=sum(r['status']=='measured' for r in output['rows']),
            automaticFactsPassed=sum(r['status']=='measured' and r['automaticFactsPassed'] for r in output['rows']),
            exactReferenceMatches=sum(r['exactReferenceMatch'] for r in output['rows']),
            elapsedSeconds=time.monotonic()-started,peakMemoryBytes=int(mx.get_peak_memory()))
        write(args.output, output)
    print(json.dumps({k:v for k,v in output.items() if k!='rows'},ensure_ascii=False),flush=True)


def run(args):
    from learning_scheduler import model_resource, active_imports, valid_checkpoint
    from local_text_model import settings
    from isolated_job_supervisor import IsolatedJobSupervisor
    from training_resources import ResourceGuard
    cache, workspace = args.cache.resolve(), args.workspace.resolve()
    if workspace.exists():
        raise ValueError('Use a new private experiment directory')
    config = settings(cache)
    model = Path(config['modelPath'])
    adapter = Path(config.get('adapterPath') or '')
    if not (adapter/'adapters.safetensors').is_file():
        raise ValueError('The current selected adapter is required for the paired baseline')
    dataset = ROOT/'data/learning/teacher-rental-v30.json'
    heldout = ROOT/'data/quality/teacher-rental-heldout-v30.json'
    data = load_dataset(dataset)
    probes = json.loads(heldout.read_text())['cases']
    train_groups = {row['groupId'] for rows in data['splits'].values() for row in rows}
    if train_groups & {row['groupId'] for row in probes}:
        raise ValueError('Holdout groups overlap training')
    train_inputs = {json.dumps(row['messages'][:-1],ensure_ascii=False,sort_keys=True) for rows in data['splits'].values() for row in rows}
    if train_inputs & {json.dumps(row['messages'][:-1],ensure_ascii=False,sort_keys=True) for row in probes}:
        raise ValueError('Holdout questions overlap training')
    workspace.mkdir(mode=0o700,parents=True)
    protected = {name:file_sha(cache/name) if (cache/name).is_file() else None for name in ['text-ai.json','photo-ai.json','learning/scheduler.json']}
    foreground = cache/'learning/foreground.json'
    foreground_sha = file_sha(foreground) if foreground.is_file() else None
    def busy():
        return active_imports(cache)!=0 or (file_sha(foreground) if foreground.is_file() else None)!=foreground_sha
    outcome = {'version':1,'kind':'assistant-teacher-v30','startedAt':time.time(),'phase':'starting',
        'datasetSha256':file_sha(dataset),'heldoutSha256':file_sha(heldout),
        'sourceHashes':{str(p.relative_to(ROOT)):file_sha(p) for p in [Path(__file__),ROOT/'scripts/train-local-text-model.py',ROOT/'cli/text_model_contract.py']},
        'counts':{split:len(rows) for split,rows in data['splits'].items()},'heldoutQueries':len(probes),
        'teacherAuthorship':'AI assistant, explicitly not human/native-editor reviewed',
        'selectedInitialAdapterSha256':file_sha(adapter/'adapters.safetensors'),
        'requestedSteps':240,'recipe':{'seed':20261103,'sampling':'shuffled','learningRate':.00002,'maxSequenceLength':512},
        'automaticPromotion':False,'qualityAccepted':False}
    write(workspace/'experiment.json',outcome)
    prefix = ['/usr/bin/arch','-arm64',config['pythonPath']]
    env={**os.environ,'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1','TOKENIZERS_PARALLELISM':'false'}
    candidate = workspace/'candidate'
    resources = ResourceGuard()
    if resources.check(admission=True):
        raise ValueError('Memory admission did not allow this experiment')
    supervisor = IsolatedJobSupervisor(busy=busy,seconds=1500)
    last_sample = 0
    def poll(child):
        nonlocal last_sample
        if time.monotonic()-last_sample>=2:
            last_sample=time.monotonic()
            reason=resources.check(child.pid)
            write(workspace/'resource-receipt.json',resources.receipt())
            if reason:supervisor.cancel('resources-'+reason)
    try:
        with model_resource(cache,kind='background',timeout=0) as fd, supervisor:
            phases = [
                ('token-audit',[*prefix,str(ROOT/'scripts/audit-text-objective-v29.py'),'--model',str(model),
                    '--dataset',str(dataset),'--output',str(workspace/'token-audit.json'),'--max-seq-length','512'],180),
                ('baseline',[*prefix,str(Path(__file__)),'--worker','--model',str(model),'--adapter',str(adapter),
                    '--heldout',str(heldout),'--output',str(workspace/'baseline.json')],420),
                ('training',[*prefix,str(ROOT/'scripts/train-local-text-model.py'),'--model',str(model),
                    '--dataset',str(dataset),'--output',str(candidate),'--resume-adapter',str(adapter/'adapters.safetensors'),
                    '--steps','240','--max-seconds','600','--max-seq-length','512','--eval-examples','30',
                    '--learning-rate','0.00002','--seed','20261103','--sampling','shuffled','--cancel-file',str(workspace/'training.cancel')],630),
                ('candidate-evaluation',[*prefix,str(Path(__file__)),'--worker','--model',str(model),'--adapter',str(candidate),
                    '--heldout',str(heldout),'--output',str(workspace/'candidate-evaluation.json')],420)]
            for phase,command,seconds in phases:
                outcome['phase']=phase;write(workspace/'experiment.json',outcome)
                print('Teacher experiment phase: '+phase,flush=True)
                with (workspace/(phase+'.log')).open('xb') as log:
                    child=supervisor.spawn(command,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,env=env,pass_fds=(fd,))
                    supervisor.wait(child,timeout=seconds,poll_hook=poll)
                if child.returncode:
                    raise ValueError(phase+' failed with exit '+str(child.returncode))
                if phase=='training':
                    receipt=json.loads((candidate/'trainreceipt.json').read_text())
                    if not valid_checkpoint(candidate,dataset) or receipt.get('stepsCompleted')!=240 or receipt.get('stoppedEarly') is not False:
                        raise ValueError('Training did not complete the frozen 240-step recipe')
            before=json.loads((workspace/'baseline.json').read_text())
            after=json.loads((workspace/'candidate-evaluation.json').read_text())
            receipt=json.loads((candidate/'trainreceipt.json').read_text())
            outcome.update(phase='complete-unaccepted',stepsCompleted=receipt['stepsCompleted'],
                adapterSha256=receipt['adapterSha256'],baseline={k:v for k,v in before.items() if k!='rows'},
                candidate={k:v for k,v in after.items() if k!='rows'},
                selectedWeightsUnchanged=file_sha(adapter/'adapters.safetensors')==outcome['selectedInitialAdapterSha256'])
    except Exception as error:
        outcome.update(phase='interrupted-or-failed',errorType=type(error).__name__,message=str(error))
        raise
    finally:
        outcome['protectedSettingsUnchanged']=all((file_sha(cache/name) if (cache/name).is_file() else None)==sha for name,sha in protected.items())
        outcome['resources']=resources.receipt();outcome['supervisor']=supervisor.receipt();outcome['finishedAt']=time.time()
        write(workspace/'experiment.json',outcome)
    print(json.dumps({k:v for k,v in outcome.items() if k!='resources'},ensure_ascii=False),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker',action='store_true')
    parser.add_argument('--cache',type=Path)
    parser.add_argument('--workspace',type=Path)
    parser.add_argument('--model',type=Path)
    parser.add_argument('--adapter',type=Path)
    parser.add_argument('--heldout',type=Path)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if args.worker:
        if not all([args.model,args.adapter,args.heldout,args.output]):parser.error('Worker paths required')
        worker(args)
    else:
        if not args.cache or not args.workspace:parser.error('Cache and new workspace required')
        run(args)


if __name__=='__main__':
    main()
