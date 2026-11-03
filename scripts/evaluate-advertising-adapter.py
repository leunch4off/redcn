"""Offline paired native evaluation of the fact-bound 4B advertising lane.

One child owns one model side. Raw measured answers are persisted separately;
failed, cancelled, partial and smoke attempts cannot select weights.
"""
import argparse
from contextlib import contextmanager
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import signal
import socket
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'cli'))
from advertising_promotion import (REPORT_VERSION, POLICY, capture_inputs, prepare_probes,
    assess, compare_rows, record_evaluation, promote)
from learning_scheduler import active_imports, model_resource, _read
from text_model_contract import (content_sha, file_sha, load_dataset, training_contract,
    inference_sampling_parameters, inference_memory_limit)
from text_native_evaluation import measure_stream, runtime_error_kind
from text_evaluation_process import (atomic_record, read_record, journal_answer, inspect_side,
    validate_inherited_lease, WorkerLifetime)
from isolated_job_supervisor import IsolatedJobSupervisor, IsolatedJobInterrupted
from training_resources import ResourceGuard


@contextmanager
def offline():
    previous = socket.socket.connect
    def blocked(sock,address):
        if sock.family in (socket.AF_INET,socket.AF_INET6): raise OSError('Evaluation is offline')
        return previous(sock,address)
    socket.socket.connect = blocked
    try: yield
    finally: socket.socket.connect = previous


def require_native():
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        raise ValueError('Нужен нативный ARM64 Python на Mac')


def paths_for(args):
    path = Path(args.output).resolve()
    return path,path.with_name(path.name+'.attempt')


def current_inputs(args):
    return capture_inputs(Path(args.cache).resolve(),args.candidate,args.training_dataset)


def resource_cache(args):
    # A candidate may live in an isolated cache while sharing the same physical
    # Mac with the running app. Its model lease and foreground signals must
    # still belong to that app's resource directory.
    return Path(args.resource_cache or args.cache).resolve()


def activity(directory):
    return {'foreground': _read(directory/'learning/foreground.json',{}),
            'cancel': _read(directory/'learning/training.cancel',{})}


def worker_side(args):
    require_native()
    cache = Path(args.cache).resolve(); resource = resource_cache(args); _,attempt = paths_for(args)
    validate_inherited_lease(resource,args.lease_fd)
    spec = read_record(attempt/'inputs.json',maximum=3_000_000)
    inputs = spec['inputs']; input_sha = content_sha(inputs)
    if (content_sha(spec)!=args.spec_sha or input_sha!=spec['inputsSha256']
            or current_inputs(args)!=inputs or spec['resourceCache']!=str(resource)):
        raise ValueError('Worker specification changed')
    side = args.model_side; folder = attempt/side
    planned = prepare_probes(load_dataset(args.training_dataset))[:spec['probeCount']]
    expected = [probe['id'] for probe in planned]
    if expected!=spec['expectedIds']: raise ValueError('Worker probe order changed')
    packages = {}
    for name in ('mlx','mlx-lm','transformers','tokenizers'):
        try: packages[name]=importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError: packages[name]=None
    environment = {'nativeArm64':True,'pythonVersion':platform.python_version(),'packages':packages}
    atomic_record(folder/'environment.json',environment,new=True)
    interrupted = [False]
    for number in (signal.SIGINT,signal.SIGTERM):
        signal.signal(number,lambda *_:interrupted.__setitem__(0,True))
    os.environ['HF_HUB_OFFLINE']='1'; os.environ['TRANSFORMERS_OFFLINE']='1'
    rows=[]; started=time.monotonic(); failure=None; peak=None
    def state(phase,**extra):
        atomic_record(folder/'state.json',{'version':1,'modelSide':side,'inputsSha256':input_sha,
            'phase':phase,'rowsCompleted':len(rows),'at':time.time(),**extra})
    with WorkerLifetime(args.watch_fd,spec['deadlineMonotonic'],folder) as lifetime,offline():
        def stop_reason():
            if interrupted[0]: return 'cancelled'
            if lifetime.stop_reason(): return lifetime.reason
            if active_imports(resource)!=0 or activity(resource)!=spec['activity']:
                return 'foreground-request'
            return None
        try:
            if stop_reason(): raise IsolatedJobInterrupted(stop_reason())
            contract = training_contract(json.loads((Path(inputs['modelPath'])/'config.json').read_text()),'advertising')
            state('loading')
            import mlx.core as mx
            from mlx_lm import load,stream_generate
            from mlx_lm.sample_utils import make_sampler
            mx.set_memory_limit(inference_memory_limit(contract['baseModel'])); mx.set_cache_limit(64_000_000)
            identity = inputs[side]
            model,tokenizer = load(inputs['modelPath'],adapter_path=identity['path'] if identity else None)
            model.eval()
            for index,probe in enumerate(planned):
                if stop_reason(): break
                state('generating',inProgressProbeId=probe['id'],index=index)
                request = probe['request']; mx.random.seed(request['seed'])
                prompt = tokenizer.apply_chat_template(request['messages'],tokenize=False,
                    add_generation_prompt=True,enable_thinking=False)
                if len(tokenizer.encode(prompt))>request['maxPromptTokens']:
                    raise ValueError('Проверочный запрос превышает рабочий контекст')
                sampling = inference_sampling_parameters(contract['baseModel'],request['temperature'])
                measured = measure_stream(lambda:stream_generate(model,tokenizer,prompt=prompt,
                    max_tokens=request['maxTokens'],sampler=make_sampler(**sampling)),
                    maximum_tokens=request['maxTokens'],probe_seconds=POLICY['probeSeconds'],stop_reason=stop_reason)
                row = {'id':probe['id'],'caseId':probe['caseId'],'task':probe['task'],**measured,
                    'backend':'mlx','fallback':False,'seed':request['seed'],'maxTokens':request['maxTokens'],
                    'sampling':sampling,'requestSha256':content_sha(request),'checks':assess(probe,measured['text'])}
                journal_answer(folder,side,input_sha,index,row); rows.append(row)
                state('answer-saved',lastProbeId=probe['id'],index=index)
                print(json.dumps({'model':side,'probe':index+1,'status':row['status'],
                    'passed':row['checks']['passed']},ensure_ascii=False),flush=True)
                if row['stopReason'] in ('memory-error','runtime-error'):
                    failure=row['stopReason']; break
            peak=mx.get_peak_memory()
        except (OSError,ValueError,TypeError,RuntimeError,MemoryError) as error:
            failure=error.reason if isinstance(error,IsolatedJobInterrupted) else runtime_error_kind(error)
            state('failed',errorType=type(error).__name__,stopReason=failure)
        try: fresh=current_inputs(args)==inputs
        except (OSError,ValueError,TypeError): fresh=False
        complete=not failure and not stop_reason() and fresh and len(rows)==len(planned)
        atomic_record(folder/'terminal.json',{'version':1,'modelSide':side,'inputsSha256':input_sha,
            'expectedIdsSha256':content_sha(expected),'rowsSha256':content_sha(rows),'rowCount':len(rows),
            'complete':complete,'inputsStillFresh':fresh,'stopReason':failure or stop_reason() or 'finished',
            'nativeArm64':True,'environment':environment,'peakMemoryBytes':peak,
            'elapsedSeconds':time.monotonic()-started,'automaticPromotion':False},new=True)
        state('complete' if complete else 'failed')
    return 0 if complete else 2


def launch_side(args,side,attempt,spec,lease_fd,supervisor):
    folder=attempt/side; folder.mkdir(mode=0o700)
    resources=ResourceGuard(limits={'maxProcessGroupRssBytes':4_500_000_000,
        'minimumAvailableEstimateBytes':768*1024**2,'maxSwapGrowthBytes':512*1024**2})
    process=None; reader=None; writer=None; reason=None; last_poll=0
    try:
        admission=resources.check(admission=True)
        if admission: supervisor.cancel(admission)
        supervisor.check()
        reader,writer=os.pipe()
        command=['/usr/bin/arch','-arm64',sys.executable,str(Path(__file__).resolve()),
            '--cache',str(args.cache),'--candidate',str(args.candidate),'--training-dataset',str(args.training_dataset),
            '--resource-cache',str(resource_cache(args)),
            '--output',str(args.output),'--max-seconds',str(args.max_seconds),'--model-side',side,
            '--lease-fd',str(lease_fd),'--watch-fd',str(reader),'--spec-sha',content_sha(spec)]
        descriptor=os.open(folder/'worker.log',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(descriptor,'wb') as log:
            process=supervisor.spawn(command,stdout=log,stderr=log,pass_fds=(lease_fd,reader),
                env={**os.environ,'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1','PYTHONUNBUFFERED':'1'})
            os.close(reader); reader=None
            def poll(child):
                nonlocal last_poll
                if time.monotonic()-last_poll<2: return
                last_poll=time.monotonic()
                reason=resources.check(child.pid)
                atomic_record(folder/'resources.json',resources.receipt())
                if reason: supervisor.cancel(reason)
            supervisor.wait(process,poll_hook=poll)
    except IsolatedJobInterrupted as error: reason=error.reason
    except (OSError,ValueError,RuntimeError) as error: reason=type(error).__name__
    finally:
        try: supervisor.stop_owned(process)
        finally:
            for descriptor in (reader,writer):
                if descriptor is not None: os.close(descriptor)
            atomic_record(folder/'resources.json',resources.receipt())
    result=inspect_side(folder,side,spec['inputsSha256'],spec['expectedIds'],process.poll() if process else None)
    metadata={key:value for key,value in result.items() if key!='rows'}
    metadata.update(stopReason=reason or ('finished' if result['admitted'] else 'incomplete-worker'),
        resources=resources.receipt(),qualityAccepted=False)
    atomic_record(folder/'attempt.json',metadata,new=True)
    return result['rows'],metadata


def run(args):
    require_native(); cache=Path(args.cache).resolve(); target,attempt=paths_for(args)
    if Path(args.output).is_symlink() or target.exists() or attempt.exists():
        raise ValueError('Для проверки нужен новый выходной файл')
    target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    started=time.monotonic(); resource=resource_cache(args)
    with model_resource(resource,kind='background',timeout=0) as lease_fd,offline():
        if active_imports(resource)!=0: raise ValueError('Проверка ждёт завершения импорта')
        initial_activity=activity(resource)
        inputs=current_inputs(args); data=load_dataset(args.training_dataset); prepared=prepare_probes(data)
        count=min(args.probe_limit or len(prepared),len(prepared)); attempt.mkdir(mode=0o700)
        spec={'version':1,'inputs':inputs,'inputsSha256':content_sha(inputs),
            'expectedIds':[row['id'] for row in prepared[:count]],'probeCount':count,
            'resourceCache':str(resource),'activity':initial_activity,'deadlineMonotonic':started+args.max_seconds}
        atomic_record(attempt/'inputs.json',spec,new=True)
        report={'version':REPORT_VERSION,'lane':'advertising','startedAt':time.time(),'inputs':inputs,
            'automaticPromotion':False,'nativeSpeakerReviewCompleted':False,'modelProcessIsolation':True,
            'processAttempts':{},'recoveredAnswers':{},'limits':{'seconds':args.max_seconds,'offline':True,
            'probesPerSide':count,'foregroundPriority':True}}
        supervisor=IsolatedJobSupervisor(seconds=max(.1,args.max_seconds-(time.monotonic()-started)),grace_seconds=2,
            busy=lambda:active_imports(resource)!=0 or activity(resource)!=initial_activity)
        failure=None
        try:
            with supervisor:
                for side in ('current','candidate'):
                    rows,metadata=launch_side(args,side,attempt,spec,lease_fd,supervisor)
                    report['processAttempts'][side]=metadata
                    if metadata['admitted'] and args.probe_limit is None: report[side]=rows
                    else:
                        report[side]=[]; report['recoveredAnswers'][side]=rows
                        if not metadata['admitted']: failure=metadata['stopReason']; break
        except IsolatedJobInterrupted as error: failure=error.reason
        for side in ('current','candidate'): report.setdefault(side,[])
        try: fresh=current_inputs(args)==inputs
        except (ValueError,OSError,TypeError): fresh=False
        eligible=not failure and fresh and args.probe_limit is None and all(
            report['processAttempts'].get(side,{}).get('admitted') for side in ('current','candidate'))
        if not eligible and report['candidate']:
            report['recoveredAnswers']['candidate']=report['candidate']; report['candidate']=[]
        decision=compare_rows(data,report['current'],report['candidate'],prepared=prepared)
        report.update(finishedAt=time.time(),elapsedSeconds=time.monotonic()-started,inputsStillFresh=fresh,
            fullGateEligible=eligible,decision=decision,supervisor=supervisor.receipt(),
            attemptOutcome='failed' if failure or not fresh else 'completed',failureReason=failure)
        atomic_record(target,report,new=True)
        if fresh and args.probe_limit is None: record_evaluation(cache,target)
    if args.promote and eligible: decision=promote(cache,target,automatic=args.automatic)
    print(json.dumps({'report':str(target),'fullGateEligible':eligible,**decision},ensure_ascii=False),flush=True)
    return 2 if failure or not fresh else 0


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('cache','candidate','training-dataset','output'): parser.add_argument('--'+name,required=True)
    parser.add_argument('--resource-cache',help='Shared physical model lease/foreground state; defaults to --cache')
    parser.add_argument('--max-seconds',type=int,default=900)
    parser.add_argument('--probe-limit',type=int,help='Smoke only; never satisfies quality acceptance')
    parser.add_argument('--promote',action='store_true'); parser.add_argument('--automatic',action='store_true')
    parser.add_argument('--model-side',choices=('current','candidate'),help=argparse.SUPPRESS)
    parser.add_argument('--lease-fd',type=int,help=argparse.SUPPRESS)
    parser.add_argument('--watch-fd',type=int,help=argparse.SUPPRESS)
    parser.add_argument('--spec-sha',help=argparse.SUPPRESS)
    args=parser.parse_args()
    if not 60<=args.max_seconds<=1800 or args.automatic and not args.promote:
        parser.error('Нужен предел 60–1800 секунд; --automatic требует --promote')
    if args.probe_limit is not None and (not 1<=args.probe_limit<=1000 or args.promote or args.model_side):
        parser.error('Smoke не может выбирать веса или запускаться как внутренний worker')
    if args.model_side:
        if args.promote or args.automatic or args.lease_fd is None or args.watch_fd is None or not args.spec_sha:
            parser.error('Worker требует унаследованную блокировку, pipe и SHA спецификации')
        sys.exit(worker_side(args))
    if args.lease_fd is not None or args.watch_fd is not None or args.spec_sha:
        parser.error('Worker capabilities are valid only in a model-side process')
    sys.exit(run(args))
