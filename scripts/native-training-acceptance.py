#!/usr/bin/env python3
"""Bounded real Mac resource/recovery drill in a new synthetic workspace.

This measures runtime cooperation. It does not approve any trained model or
use an operator card as a training example. A deliberately raised admission
threshold checks the low-memory branch without allocating pressure memory.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import threading
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'cli'))
from learning_memory import LearningMemory
from learning_scheduler import IdleTrainer,model_resource,_write,save_config,active_imports,valid_checkpoint
from local_text_model import settings,generate
from training_resources import ResourceGuard,sample,LIMITS
from isolated_job_supervisor import IsolatedJobSupervisor


def sha(path):
    if path.is_symlink():raise ValueError('A regular private artifact is required')
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def read_receipt(path):
    if path.is_symlink() or not path.is_file() or not 0<path.stat().st_size<=2_000_000:return {}
    try:
        value=json.loads(path.read_text())
        return value if isinstance(value,dict) else {}
    except (ValueError,OSError):return {}


def stop_active_training(trainer,worker,app):
    """A prior checkpoint or a joined idle thread is not a stop experiment."""
    process=trainer._process;job=trainer.state.get('jobId')
    if (not worker.is_alive() or process is None or process.poll() is not None
            or trainer.state.get('phase')!='training' or not isinstance(job,str)
            or not job.startswith('idle-') or not job[5:].isalnum()):
        raise RuntimeError('No active owned training process was observed before stop')
    checkpoint=app/'learning/training-runs'/job/'candidate'
    before=read_receipt(checkpoint/'trainreceipt.json')
    before_sha=sha(checkpoint/'trainreceipt.json')
    stopped=time.monotonic();trainer.stop();worker.join(timeout=8)
    after=read_receipt(checkpoint/'trainreceipt.json')
    return {'seconds':time.monotonic()-stopped,'workerJoined':not worker.is_alive(),
        'ownedProcessObservedAlive':True,'ownedProcessExited':process.poll() is not None,
        'jobIdSha256':hashlib.sha256(job.encode()).hexdigest(),
        'state':read_receipt(app/'learning/scheduler-state.json').get('phase'),
        'checkpointBeforeSha256':before_sha,'checkpointAfterSha256':sha(checkpoint/'trainreceipt.json'),
        'stepsBeforeStop':before.get('stepsCompleted'),'stepsAfterStop':after.get('stepsCompleted'),
        'checkpointValid':valid_checkpoint(checkpoint,checkpoint.parent/'dataset.json')}


def run(args):
    if platform.system()!='Darwin':raise ValueError('This drill requires the actual Mac')
    cache=args.production_cache.resolve();work=args.workspace.absolute()
    if work.exists() or work.is_relative_to(cache) or cache.is_relative_to(work):raise ValueError('Use a new isolated workspace outside the production cache')
    config=settings(cache)
    if not config['enabled'] or not config.get('adapterPath'):raise ValueError('A configured local model is required')
    work.mkdir(mode=0o700,parents=True)
    app=work/'application';app.mkdir(mode=0o700)
    protocol={'version':2,'task':'RCN-346','syntheticDataOnly':True,'maxSeconds':300,
        'limits':LIMITS,'trainingSteps':200,'sequenceLength':256,'nativeQualityAccepted':False,
        'lowMemoryCheck':'real host sample with a deliberately raised admission threshold; no pressure allocation',
        'physicalSleepWake':'not-measured','foregroundWatcher':'all phases including blocking calls',
        'cleanupGraceSeconds':5}
    _write(work/'protocol.json',protocol)
    source_hashes={name:sha(ROOT/name) for name in ('scripts/native-training-acceptance.py',
        'scripts/soak-project-phase.py','scripts/train-local-text-model.py','cli/learning_scheduler.py',
        'cli/training_resources.py','cli/isolated_job_supervisor.py','cli/local_text_model.py')}
    protected=[cache/name for name in ('text-ai.json','photo-ai.json','learning/scheduler.json')]
    before={str(path.relative_to(cache)):sha(path) for path in protected}
    foreground=sha(cache/'learning/foreground.json')
    memory=LearningMemory(app)
    for i in range(60):memory.record('translation',{'ru':f'Вымышленный стол номер {i} на кухне.','zh':f'厨房里有编号{i}的虚构桌子。'},status='approved',group_id='resource-fixture-'+str(i),actor='synthetic-resource-drill')
    save_config(app,{'enabled':True,'idleSeconds':60,'maxSteps':200,'maxSeconds':180,'maxSequenceLength':256})
    _write(app/'text-ai.json',{'enabled':True,'modelPath':config['modelPath'],'pythonPath':config['pythonPath'],
        'adapterPath':config['adapterPath'],'useCandidateAdapter':True,'maxSeconds':55})
    samples=[];stop=threading.Event();trainer=None;editor=None;worker=None;observer=None
    outcomes={};errors=[]
    def production_busy():return active_imports(cache)!=0 or sha(cache/'learning/foreground.json')!=foreground
    supervisor=IsolatedJobSupervisor(busy=production_busy,seconds=protocol['maxSeconds'])
    try:
        # The outer gate remains held until every supervised child has exited.
        with model_resource(cache,kind='background',timeout=30) as production_fd:
            with supervisor:
                supervisor.check()
                def popen(command,**kwargs):
                    kwargs['pass_fds']=tuple(dict.fromkeys((*kwargs.get('pass_fds',()),production_fd)))
                    return supervisor.spawn(command,**kwargs)
                trainer=IdleTrainer(app,busy=lambda:supervisor.cancelled.is_set() or production_busy(),popen=popen)
                trainer.started=0
                trainer._runtime=lambda: (['/usr/bin/arch','-arm64',config['pythonPath'],str(ROOT/'scripts/train-local-text-model.py')],Path(config['modelPath']))
                def monitor():
                    while not stop.is_set():
                        process=trainer._process
                        samples.append(sample(getattr(process,'pid',None)))
                        if len(samples)>720:del samples[:-720]
                        stop.wait(1)
                observer=threading.Thread(target=monitor,daemon=True);observer.start()
                def train():
                    try:outcomes['training']=trainer.tick()
                    except BaseException as error:errors.append(type(error).__name__)
                worker=threading.Thread(target=train);worker.start()
                try:
                    deadline=min(supervisor.deadline,time.monotonic()+80)
                    while time.monotonic()<deadline:
                        supervisor.check()
                        receipts=list((app/'learning/training-runs').glob('*/candidate/trainreceipt.json'))
                        if receipts and any(read_receipt(path).get('stepsCompleted',0)>=10 for path in receipts):break
                        if not worker.is_alive():break
                        supervisor.cancelled.wait(.2)
                    supervisor.check()
                    env={**os.environ,'REDCN_SOAK_WORKSPACE':str(work)}
                    with (work/'browser.log').open('wb') as editor_log:
                        editor=supervisor.spawn([sys.executable,str(ROOT/'scripts/soak-project-phase.py'),
                            '--kind','editor','--workspace',str(work)],stdout=editor_log,stderr=subprocess.STDOUT,
                            env=env,pass_fds=(production_fd,))
                        requested=time.monotonic();requests=[]
                        for attempt in range(4):
                            supervisor.check()
                            result=generate({'messages':[{'role':'user','content':'Переведи на китайский: Есть стол.'}],
                                'maxTokens':48,'temperature':0,'seed':20261102},app,
                                cancel=supervisor.cancelled,deadline=supervisor.deadline)
                            requests.append({key:result.get(key) for key in ('status','elapsedSeconds','ai')})
                            supervisor.check()
                            if result.get('status')!='busy':break
                            supervisor.cancelled.wait(.3)
                        outcomes['foreground']={'attempts':requests,'secondsFromFirstRequest':time.monotonic()-requested,
                            'completed':result.get('status')=='review'}
                        worker.join(timeout=10);supervisor.check()
                        if worker.is_alive():raise RuntimeError('Owned training worker did not yield')
                        env.update(REDCN_SOAK_RESOURCE_HELD='1')
                        with (work/'photo.log').open('wb') as photo_log:
                            photo=supervisor.spawn(['/usr/bin/arch','-arm64',str(cache/'tools/photo-ai-arm/venv/bin/python'),
                                str(ROOT/'scripts/soak-project-phase.py'),'--kind','photo','--workspace',str(work),'--model-cache',str(cache)],
                                stdout=photo_log,stderr=subprocess.STDOUT,env=env,pass_fds=(production_fd,))
                            supervisor.wait(photo,timeout=45)
                            outcomes['photo']={'exitCode':photo.returncode,'measured':read_receipt(work/'photo.json')}
                        supervisor.wait(editor,timeout=50)
                        outcomes['browser']={'exitCode':editor.returncode,'measured':read_receipt(work/'editor.json')}
                    supervisor.check()
                    real=sample()
                    guard=ResourceGuard(lambda _:real,{'minimumAvailableEstimateBytes':real['physicalBytes']+1})
                    outcomes['lowMemoryAdmission']={'controlledThreshold':True,'actualSample':real,
                        'reason':guard.check(admission=True),'allocatedPressureBytes':0}
                    # Require a genuinely live resumed worker before stopping
                    # it; a prior receipt is insufficient proof of this phase.
                    trainer.state['retryAfter']=0
                    _write(app/'learning/scheduler-state.json',trainer.state)
                    (app/'learning/foreground.json').unlink(missing_ok=True)
                    trainer.started=0
                    outcomes['firstTraining']=outcomes.pop('training',None)
                    first_receipts=list((app/'learning/training-runs').glob('*/candidate/trainreceipt.json'))
                    outcomes['firstTrainingReceipts']=[{key:read_receipt(path).get(key) for key in
                        ('stepsCompleted','adapterSha256','optimizerSha256','peakMemoryBytes','status','stoppedEarly')} for path in first_receipts]
                    worker=threading.Thread(target=train);worker.start()
                    end=min(supervisor.deadline,time.monotonic()+25)
                    while trainer._process is None and worker.is_alive() and time.monotonic()<end:
                        supervisor.check();supervisor.cancelled.wait(.1)
                    supervisor.cancelled.wait(2);supervisor.check()
                    outcomes['serverStop']=stop_active_training(trainer,worker,app)
                    supervisor.check()
                finally:
                    trainer.stop()
                    if worker:
                        worker.join(timeout=8)
                        if worker.is_alive():errors.append('OwnedTrainingThreadNotJoined')
                    supervisor.stop_owned(editor)
                    stop.set()
                    if observer:observer.join(timeout=5)
    except BaseException as error:
        errors.append(type(error).__name__)
    finally:
        stop.set()
        if observer:observer.join(timeout=5)
        try:after={str(path.relative_to(cache)):sha(path) for path in protected}
        except (OSError,ValueError):after={};errors.append('ProductionSettingsUnreadable')
        receipts=[]
        for path in (app/'learning/training-runs').glob('*/candidate/trainreceipt.json'):
            value=read_receipt(path)
            receipts.append({key:value.get(key) for key in ('status','stepsCompleted','stoppedEarly','peakMemoryBytes','adapterSha256','datasetSha256')})
        runtime=supervisor.receipt()
        try:sources_unchanged=all(sha(ROOT/name)==digest for name,digest in source_hashes.items())
        except (OSError,ValueError):sources_unchanged=False
        summary={'version':2,'protocolSha256':sha(work/'protocol.json'),'outcomes':outcomes,'errors':errors,
            'memorySamples':samples,'trainingReceipts':receipts,'productionSettingsUnchanged':before==after,
            'sourceHashes':source_hashes,'sourceFilesUnchanged':sources_unchanged,
            'supervisor':runtime,'nativeModelQualityAccepted':False,'physicalSleepWake':'not-measured'}
        def measured(kind):
            item=outcomes.get(kind,{})
            return item.get('exitCode')==0 and item.get('measured',{}).get('ok') is True and item['measured'].get('mocked') is False
        summary['checks']={'foregroundCompleted':outcomes.get('foreground',{}).get('completed') is True,
            'photoCompleted':measured('photo'),'browserCompleted':measured('browser'),
            'lowMemoryBlocked':outcomes.get('lowMemoryAdmission',{}).get('reason')=='available-memory-low',
            'ownedTrainingObservedAlive':outcomes.get('serverStop',{}).get('ownedProcessObservedAlive') is True,
            'ownedTrainingExited':outcomes.get('serverStop',{}).get('ownedProcessExited') is True,
            'serverWorkerJoined':outcomes.get('serverStop',{}).get('workerJoined') is True,
            'checkpointSurvivesStop':outcomes.get('serverStop',{}).get('checkpointValid') is True,
            'mlxPeakMeasured':any(type(row.get('peakMemoryBytes')) is int and row['peakMemoryBytes']>0 for row in outcomes.get('firstTrainingReceipts',[])),
            'withinTotalDeadline':runtime['actualDurationSeconds']<=protocol['maxSeconds'],
            'notInterrupted':not runtime['interrupted'],'productionSettingsUnchanged':before==after,
            'sourceFilesUnchanged':sources_unchanged}
        summary['passed']=not errors and all(summary['checks'].values())
        summary['outcome']='completed' if summary['passed'] else 'interrupted' if runtime['interrupted'] else 'failed'
        _write(work/'summary.json',summary)
    print(json.dumps({key:value for key,value in summary.items() if key not in ('memorySamples','outcomes')},ensure_ascii=False))
    if not summary['passed']:raise RuntimeError('Resource acceptance needs investigation')
    return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--production-cache',type=Path,required=True)
    parser.add_argument('--workspace',type=Path,required=True)
    run(parser.parse_args())
