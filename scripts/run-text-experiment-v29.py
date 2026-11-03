#!/usr/bin/env python3
"""Run the frozen two-mixture protocol once, offline, with foreground priority."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'cli'))
from learning_scheduler import model_resource,active_imports,_write,valid_checkpoint
from learning_memory import LearningMemory
from local_text_model import settings
from training_resources import ResourceGuard
from isolated_job_supervisor import IsolatedJobSupervisor,IsolatedJobInterrupted


def digest(path):
    if path.is_symlink() or not path.is_file():raise ValueError('Expected a regular frozen artifact')
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024**2),b''):h.update(block)
    return h.hexdigest()


def optional_digest(path):return digest(path) if path.exists() else None


def completed_receipt(output,dataset,steps,exit_code):
    """A successful process exit alone is not a completed experiment."""
    path=output/'trainreceipt.json'
    try:
        if path.is_symlink() or not path.is_file() or not 0<path.stat().st_size<=2_000_000:return {},False
        receipt=json.loads(path.read_text())
        if not isinstance(receipt,dict):return {},False
        complete=(exit_code==0 and receipt.get('status')=='trained-unaccepted'
            and type(receipt.get('stepsCompleted')) is int and receipt['stepsCompleted']==steps
            and receipt.get('stoppedEarly') is False and receipt.get('trainableParameters')==540672
            and valid_checkpoint(output,dataset))
        return receipt,complete
    except (OSError,ValueError,TypeError):return {},False


def run(args):
    cache=args.cache.resolve();workspace=args.workspace.absolute()
    if workspace.exists() or not re.fullmatch(r'v29-[a-z0-9-]{4,60}',args.run_label):raise ValueError('Use a new experiment workspace and a v29 run label')
    config=settings(cache)
    protocol_path=ROOT/'data/quality/text-experiment-v29.json'
    protocol=json.loads(protocol_path.read_text())
    for name,sha in protocol['artifacts'].items():
        if digest(ROOT/name)!=sha:raise ValueError('Frozen corpus changed: '+name)
    model=Path(config['modelPath']);adapter=Path(config.get('adapterPath') or '')
    if digest(model/'config.json')!=protocol['baseConfigSha256'] or digest(adapter/'adapters.safetensors')!=protocol['initialAdapterSha256']:
        raise ValueError('The experiment requires its declared balanced initial adapter/base')
    outputs={name:cache/'tools/text-ai/runs'/(args.run_label+'-'+name) for name in ('advertising-only','translation-retention')}
    if any(path.exists() for path in outputs.values()):raise ValueError('The experiment never overwrites a prior candidate')
    workspace.mkdir(mode=0o700,parents=True)
    protected={name:optional_digest(cache/name) for name in ('text-ai.json','photo-ai.json','learning/scheduler.json')}
    foreground=optional_digest(cache/'learning/foreground.json')
    manifest={'version':1,'startedAt':time.time(),'outcome':'running','protocolSha256':digest(protocol_path),'protocol':protocol,
        'sourceHashes':{name:digest(ROOT/name) for name in ('scripts/train-local-text-model.py','cli/text_model_contract.py',
            'scripts/run-text-experiment-v29.py','scripts/audit-text-objective-v29.py','cli/training_resources.py',
            'cli/isolated_job_supervisor.py')},
        'runs':[],'automaticPromotion':False}
    _write(workspace/'experiment.json',manifest)
    prefix=['/usr/bin/arch','-arm64',config['pythonPath']]
    env={**os.environ,'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1','TOKENIZERS_PARALLELISM':'false'}
    def interrupted():return active_imports(cache)!=0 or optional_digest(cache/'learning/foreground.json')!=foreground
    recipe=protocol['recipe']
    supervisor=IsolatedJobSupervisor(busy=interrupted,seconds=210+2*(recipe['maxSecondsPerRun']+30))
    try:
        # Cleanup must finish before the shared production lease is released.
        with model_resource(cache,kind='background',timeout=30) as fd:
            with supervisor:
                supervisor.check()
                with (workspace/'token-audit.log').open('xb') as log:
                    audit=supervisor.spawn([*prefix,str(ROOT/'scripts/audit-text-objective-v29.py'),
                        '--model',str(model),'--dataset',str(ROOT/'data/learning/text-v29-translation-retention.json'),
                        '--output',str(workspace/'token-audit.json'),'--max-seq-length','1024'],
                        stdout=log,stderr=subprocess.STDOUT,env=env,pass_fds=(fd,))
                    supervisor.wait(audit,timeout=180)
                if audit.returncode:raise ValueError('Native token objective audit failed; training did not start')
                manifest['tokenAuditSha256']=digest(workspace/'token-audit.json')
                _write(workspace/'experiment.json',manifest)
                for name,output in outputs.items():
                    supervisor.check()
                    dataset=ROOT/('data/learning/text-v29-'+name+'.json')
                    resources=ResourceGuard()
                    if resources.check(admission=True):raise ValueError('Memory admission denied')
                    cancel=workspace/(name+'.cancel')
                    command=[*prefix,str(ROOT/'scripts/train-local-text-model.py'),'--model',str(model),
                        '--dataset',str(dataset),'--output',str(output),'--resume-adapter',str(adapter/'adapters.safetensors'),
                        '--steps',str(recipe['steps']),'--max-seconds',str(recipe['maxSecondsPerRun']),
                        '--max-seq-length',str(recipe['maxSequenceLength']),'--eval-examples',str(recipe['evalExamples']),
                        '--learning-rate',str(recipe['learningRate']),'--seed',str(recipe['seed']),
                        '--sampling',recipe['sampling'],'--cancel-file',str(cancel)]
                    start=time.monotonic();process=None;last_resource=0
                    print('Starting frozen regime:',name,flush=True)
                    def resource_poll(child):
                        nonlocal last_resource
                        if time.monotonic()-last_resource<2:return
                        last_resource=time.monotonic()
                        reason=resources.check(child.pid)
                        _write(workspace/(name+'-resources.json'),resources.receipt())
                        if reason:
                            _write(cancel,{'reason':reason,'at':time.time()});supervisor.cancel(reason)
                    try:
                        with (workspace/(name+'.log')).open('xb') as log:
                            process=supervisor.spawn(command,stdout=log,stderr=subprocess.STDOUT,env=env,pass_fds=(fd,))
                            supervisor.wait(process,timeout=recipe['maxSecondsPerRun']+30,poll_hook=resource_poll)
                    finally:
                        if supervisor.cancelled.is_set():_write(cancel,{'reason':supervisor.reason,'at':time.time()})
                        supervisor.stop_owned(process)
                        _write(workspace/(name+'-resources.json'),resources.receipt())
                        receipt_path=output/'trainreceipt.json'
                        receipt,complete=completed_receipt(output,dataset,recipe['steps'],process.returncode if process else None)
                        complete=complete and not supervisor.cancelled.is_set()
                        record={'regime':name,'exitCode':process.returncode if process else None,
                            'stoppedBy':supervisor.reason,'completeDeclaredSteps':complete,
                            'elapsedSeconds':time.monotonic()-start,'receiptSha256':digest(receipt_path) if receipt_path.is_file() and not receipt_path.is_symlink() else None,
                            'adapterSha256':receipt.get('adapterSha256'),'resourceReceiptSha256':digest(workspace/(name+'-resources.json'))}
                        manifest['runs'].append(record);_write(workspace/'experiment.json',manifest)
                    print(json.dumps(record,ensure_ascii=False),flush=True)
                    if not complete:raise ValueError('Declared training regime did not complete: '+name)
                    LearningMemory(cache).register_candidate(receipt['adapterSha256'],dataset)
        if len(manifest['runs'])!=2 or not all(row['completeDeclaredSteps'] for row in manifest['runs']):
            raise ValueError('Both declared regimes must complete before reporting success')
        manifest['outcome']='completed'
    except BaseException as error:
        manifest['outcome']='interrupted' if supervisor.cancelled.is_set() or isinstance(error,IsolatedJobInterrupted) else 'failed'
        manifest['errorType']=type(error).__name__
        raise
    finally:
        try:unchanged=protected=={name:optional_digest(cache/name) for name in protected}
        except (OSError,ValueError):unchanged=False
        try:source_unchanged=all(digest(ROOT/name)==sha for name,sha in manifest['sourceHashes'].items()) and digest(protocol_path)==manifest['protocolSha256']
        except (OSError,ValueError):source_unchanged=False
        manifest.update(finishedAt=time.time(),supervisor=supervisor.receipt(),productionSettingsUnchanged=unchanged,sourceFilesUnchanged=source_unchanged)
        if not unchanged or not source_unchanged:manifest['outcome']='failed'
        manifest['completeDeclaredExperiment']=manifest['outcome']=='completed'
        _write(workspace/'experiment.json',manifest)
    if not manifest['completeDeclaredExperiment']:raise ValueError('Experiment did not complete with unchanged settings and sources')
    print('Both frozen regimes completed with verified receipts; selected weights unchanged',flush=True)
    return manifest


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache',type=Path,required=True)
    parser.add_argument('--workspace',type=Path,required=True)
    parser.add_argument('--run-label',default='v29-20261102')
    run(parser.parse_args())
