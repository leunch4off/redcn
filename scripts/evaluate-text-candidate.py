"""Offline paired text evaluation with one native process per model side.

Every completed raw answer is fsynced before the next request. Failed workers
retain their answers as diagnostic evidence and never satisfy a paired gate.
The parent holds the shared model lease and never imports MLX itself.
"""
import argparse
import importlib.metadata
from collections import defaultdict
from contextlib import contextmanager
import json
import os
from pathlib import Path
import platform
import signal
import socket
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'cli'))
from learning_scheduler import active_imports, model_resource, _read
from model_promotion import CASES, POLICY, REPORT_VERSION, capture_inputs, compare_rows, promote, record_evaluation
from advertising_evaluation import active_rules, assess, prepare_probes, request_for
from rental_heldout_quality import load_cases
from text_model_contract import content_sha, file_sha, expected_parameters, sampling_parameters
from text_quality import parse_card
from text_evaluation_audit import summarize_direct_report
from text_native_evaluation import measure_stream, runtime_error_kind
from text_evaluation_process import (atomic_record, read_record, journal_answer, inspect_side,
    validate_inherited_lease, WorkerLifetime)
from isolated_job_supervisor import IsolatedJobSupervisor, IsolatedJobInterrupted
from training_resources import ResourceGuard


@contextmanager
def offline():
    previous=socket.socket.connect
    def blocked(sock,address):
        if sock.family in (socket.AF_INET,socket.AF_INET6):
            raise OSError('Candidate evaluation is offline')
        return previous(sock,address)
    socket.socket.connect=blocked
    try:yield
    finally:socket.socket.connect=previous


def require_native():
    if platform.system()!='Darwin' or platform.machine()!='arm64':
        raise ValueError('Нужен нативный ARM64 Python на Mac')


def paths_for(args):
    target=Path(args.output).resolve()
    return target,target.with_name(target.name+'.attempt')


def current_inputs(args):
    return capture_inputs(Path(args.cache).resolve(),args.candidate,args.training_dataset,Path(args.cases).resolve())


def worker_side(args):
    require_native()
    cache=Path(args.cache).resolve(); _,attempt=paths_for(args)
    validate_inherited_lease(cache,args.lease_fd)
    spec=read_record(attempt/'inputs.json',maximum=3_000_000)
    if content_sha(spec)!=args.spec_sha:
        raise ValueError('Worker specification changed')
    inputs=spec['inputs'];input_sha=content_sha(inputs)
    if spec.get('inputsSha256')!=input_sha or current_inputs(args)!=inputs:
        raise ValueError('Worker inputs do not match the reserved comparison')
    side=args.model_side;folder=attempt/side
    if folder.is_symlink() or not folder.is_dir():raise ValueError('Private worker folder is required')
    packages={}
    for name in ('mlx','mlx-lm','transformers','tokenizers'):
        try:packages[name]=importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:packages[name]=None
    environment={'nativeArm64':True,'pythonVersion':platform.python_version(),'packages':packages}
    atomic_record(folder/'environment.json',{'version':1,**environment},new=True)
    corpus=load_cases(args.cases)
    prepared=prepare_probes(corpus,rules=active_rules(cache))
    planned=prepared[:spec['probeCount']]
    expected_ids=[row['id'] for row in planned]
    if expected_ids!=spec['expectedIds']:raise ValueError('Worker probe order changed')
    cancel=cache/'learning/training.cancel';interrupted=[False]
    for number in (signal.SIGTERM,signal.SIGINT):
        signal.signal(number,lambda *_:interrupted.__setitem__(0,True))
    os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1'
    rows=[];history=defaultdict(list);started=time.monotonic()
    def state(phase,**extra):
        atomic_record(folder/'state.json',{'version':1,'modelSide':side,'inputsSha256':input_sha,
            'phase':phase,'rowsCompleted':len(rows),'at':time.time(),'environment':environment,**extra})
    with WorkerLifetime(args.watch_fd,spec['deadlineMonotonic'],folder) as lifetime,offline():
        def stop_reason():
            if interrupted[0] or cancel.exists():return 'cancelled'
            if lifetime.stop_reason():return lifetime.reason
            if active_imports(cache)!=0 or _read(cache/'learning/foreground.json',{})!=spec['foreground']:
                return 'foreground-request'
            return None
        failure=None;peak=None
        try:
            if stop_reason():raise IsolatedJobInterrupted(stop_reason())
            expected_parameters(json.loads((Path(inputs['modelPath'])/'config.json').read_text()))
            state('loading')
            import mlx.core as mx
            from mlx_lm import load,stream_generate
            from mlx_lm.sample_utils import make_sampler
            mx.set_memory_limit(2_500_000_000);mx.set_cache_limit(64_000_000)
            identity=inputs[side]
            model,tokenizer=load(inputs['modelPath'],adapter_path=identity['path'] if identity else None)
            model.eval()
            for index,probe in enumerate(planned):
                if stop_reason():break
                state('generating',inProgressProbeId=probe['id'],index=index)
                seed=POLICY['seed']+index;mx.random.seed(seed)
                request=request_for(probe,history[probe['caseId']],seed,POLICY)
                prompt=tokenizer.apply_chat_template(request['messages'],tokenize=False,add_generation_prompt=True)
                if len(tokenizer.encode(prompt))>2048:
                    raise ValueError('Проверочный запрос превышает рабочее окно модели')
                measured=measure_stream(lambda:stream_generate(model,tokenizer,prompt=prompt,
                    max_tokens=request['maxTokens'],prefill_step_size=256,
                    sampler=make_sampler(**sampling_parameters(request['temperature'],top_p=request['topP']))),
                    maximum_tokens=request['maxTokens'],probe_seconds=POLICY['probeSeconds'],stop_reason=stop_reason)
                row={'id':probe['id'],'caseId':probe['caseId'],'task':probe['task'],**measured,
                    'seed':seed,'maxTokens':request['maxTokens'],'temperature':request['temperature'],
                    'topP':request['topP'],'backend':'mlx','fallback':False,
                    'checks':assess(probe,measured['text'])}
                journal_answer(folder,side,input_sha,index,row)
                rows.append(row)
                state('answer-saved',lastProbeId=probe['id'],index=index)
                if probe['task']=='listing' and row['status']=='measured' and row['checks']['passed']:
                    try:history[probe['caseId']].append(parse_card(row['text']))
                    except ValueError:pass
                print(json.dumps({'model':side,'probe':probe['id'],'status':row['status'],
                                  'passed':row['checks'].get('passed')},ensure_ascii=False),flush=True)
                if row['stopReason'] in ('memory-error','runtime-error'):
                    failure=row['stopReason'];break
            peak=mx.get_peak_memory()
        except (OSError,ValueError,TypeError,RuntimeError,MemoryError) as error:
            failure=(error.reason if isinstance(error,IsolatedJobInterrupted) else
                'memory-error' if isinstance(error,MemoryError) else runtime_error_kind(error))
            state('failed',errorType=type(error).__name__,stopReason=failure)
        try:fresh=current_inputs(args)==inputs
        except (OSError,ValueError,TypeError):fresh=False
        complete=not failure and stop_reason() is None and fresh and len(rows)==len(planned)
        terminal={'version':1,'modelSide':side,'inputsSha256':input_sha,
            'expectedIdsSha256':content_sha(expected_ids),'rowCount':len(rows),'rowsSha256':content_sha(rows),
            'complete':complete,'inputsStillFresh':fresh,'stopReason':failure or stop_reason() or 'finished',
            'peakMemoryBytes':peak,'nativeArm64':True,'environment':environment,'elapsedSeconds':time.monotonic()-started,
            'automaticPromotion':False}
        atomic_record(folder/'terminal.json',terminal,new=True)
        state('complete' if complete else 'failed',stopReason=terminal['stopReason'])
    # No second model is loaded here, and no explicit GC/Metal cache teardown
    # occurs between sides. A crash even after terminal.json still rejects this
    # side because the parent independently checks the actual process exit.
    return 0 if complete else 2


def launch_side(args,side,attempt,spec,lease_fd,supervisor):
    folder=attempt/side;folder.mkdir(mode=0o700)
    resources=ResourceGuard();process=None;reader=None;writer=None;reason=None
    started=time.monotonic();exit_code=None;last_poll=0;last_count=-1
    admission=resources.check(admission=True)
    try:
        if admission:
            reason=admission;supervisor.cancel(admission)
        else:
            reader,writer=os.pipe()
            command=['/usr/bin/arch','-arm64',sys.executable,str(Path(__file__).resolve()),
                '--cache',str(args.cache),'--candidate',str(args.candidate),
                '--training-dataset',str(args.training_dataset),'--cases',str(args.cases),
                '--output',str(args.output),'--max-seconds',str(args.max_seconds),
                '--model-side',side,'--lease-fd',str(lease_fd),'--watch-fd',str(reader),
                '--spec-sha',content_sha(spec)]
            if args.diagnostic:command.append('--diagnostic')
            env={**os.environ,'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1','PYTHONUNBUFFERED':'1'}
            log_descriptor=os.open(folder/'worker.log',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(log_descriptor,'wb') as log:
                process=supervisor.spawn(command,stdout=log,stderr=log,env=env,pass_fds=(lease_fd,reader))
                os.close(reader);reader=None
                def poll(child):
                    nonlocal last_poll,last_count
                    if time.monotonic()-last_poll<2:return
                    last_poll=time.monotonic()
                    resource_reason=resources.check(child.pid)
                    atomic_record(folder/'resources.json',resources.receipt())
                    if resource_reason:supervisor.cancel(resource_reason)
                    try:
                        state=read_record(folder/'state.json')
                        if state.get('rowsCompleted')!=last_count:
                            last_count=state.get('rowsCompleted')
                            print(json.dumps({'model':side,'savedAnswers':last_count,'phase':state.get('phase')},ensure_ascii=False),flush=True)
                    except (OSError,ValueError):pass
                supervisor.wait(process,poll_hook=poll)
                exit_code=process.returncode
    except IsolatedJobInterrupted as error:reason=error.reason
    except (OSError,ValueError,RuntimeError) as error:reason=type(error).__name__
    finally:
        try:supervisor.stop_owned(process)
        finally:
            if process is not None:exit_code=process.poll()
            for descriptor in (reader,writer):
                if descriptor is not None:os.close(descriptor)
            atomic_record(folder/'resources.json',resources.receipt())
    result=inspect_side(folder,side,spec['inputsSha256'],spec['expectedIds'],exit_code)
    try:state=read_record(folder/'state.json')
    except (OSError,ValueError):state={}
    metadata={key:value for key,value in result.items() if key!='rows'}
    metadata.update(modelSide=side,elapsedSeconds=time.monotonic()-started,
        stopReason=reason or ('native-signal-'+str(result['signal']) if result['signal'] else
            'finished' if result['admitted'] else 'incomplete-worker'),lastState=state,
        resources=resources.receipt(),qualityAccepted=False)
    atomic_record(folder/'attempt.json',metadata,new=True)
    return result['rows'],metadata


def run(args):
    require_native()
    cache=Path(args.cache).resolve();target,attempt=paths_for(args)
    if Path(args.output).is_symlink() or target.exists() or attempt.exists() or attempt.is_symlink():
        raise ValueError('Для проверки нужны новые выходной файл и каталог попытки')
    target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    cases_path=Path(args.cases).resolve();diagnostic=cases_path!=CASES.resolve()
    if diagnostic and (not args.diagnostic or args.promote):
        raise ValueError('A custom holdout requires --diagnostic and cannot promote weights')
    if args.probe_limit is not None and (not args.diagnostic or args.promote):
        raise ValueError('Probe-limited smoke runs require --diagnostic and cannot promote')
    corpus=load_cases(cases_path);started=time.monotonic();cancel=cache/'learning/training.cancel'
    with model_resource(cache,kind='background',timeout=0) as lease_fd,offline():
        if active_imports(cache)!=0:raise ValueError('Проверка ждёт завершения импорта объявлений')
        foreground=_read(cache/'learning/foreground.json',{})
        if cancel.is_symlink():raise ValueError('Маркер отмены не должен быть ссылкой')
        cancel.unlink(missing_ok=True)
        inputs=current_inputs(args);input_sha=content_sha(inputs)
        prepared=prepare_probes(corpus,rules=active_rules(cache))
        count=min(args.probe_limit or len(prepared),len(prepared))
        attempt.mkdir(mode=0o700)
        spec={'version':1,'inputs':inputs,'inputsSha256':input_sha,
            'expectedIds':[row['id'] for row in prepared[:count]],'probeCount':count,
            'fullProbeCount':len(prepared),'foreground':foreground,'deadlineMonotonic':started+args.max_seconds}
        atomic_record(attempt/'inputs.json',spec,new=True)
        report={'version':REPORT_VERSION,'startedAt':time.time(),'inputs':inputs,
            'nativeSpeakerReviewCompleted':False,'automaticPromotion':False,
            'acceptanceScope':'diagnostic-custom-heldout' if diagnostic or args.diagnostic else 'production-promotion-gate',
            'limits':{'memoryBytes':2_500_000_000,'seconds':args.max_seconds,'offline':True,
                      'foregroundPriority':True,'probesPerSide':count},
            'executionVersion':1,'modelProcessIsolation':True,'fullProbeSetRequested':count==len(prepared),
            'durableAnswers':'fsync-file-atomic-rename-per-answer','processAttempts':{},'recoveredAnswers':{}}
        atomic_record(attempt/'attempt.json',{'version':1,'state':'running','startedAt':report['startedAt'],
            'inputsSha256':input_sha,'probeCountPerSide':count,'fullProbeCount':len(prepared)},new=True)
        busy=lambda:(cancel.exists() or active_imports(cache)!=0
                     or _read(cache/'learning/foreground.json',{})!=foreground)
        supervisor=IsolatedJobSupervisor(busy=busy,seconds=max(.1,args.max_seconds-(time.monotonic()-started)),grace_seconds=2)
        failure=None
        try:
            with supervisor:
                for side in ('current','candidate'):
                    supervisor.check()
                    rows,metadata=launch_side(args,side,attempt,spec,lease_fd,supervisor)
                    report['processAttempts'][side]=metadata
                    if metadata['admitted'] and args.probe_limit is None:
                        report[side]=rows
                    else:
                        # Even a crash after the final answer cannot enter the
                        # gate. Raw measured rows remain intact for diagnosis.
                        report[side]=[];report['recoveredAnswers'][side]=rows
                        if not metadata['admitted']:
                            failure=metadata['stopReason'];break
        except IsolatedJobInterrupted as error:failure=error.reason
        except (OSError,ValueError,RuntimeError) as error:failure=type(error).__name__
        for side in ('current','candidate'):report.setdefault(side,[])
        try:fresh=current_inputs(args)==inputs
        except (OSError,ValueError,TypeError):fresh=False
        if (failure or not fresh) and report['candidate']:
            # The existing gate rechecks raw top-level sides, not a reported
            # decision. Keep an interrupted/stale attempt fail-closed even when
            # the interruption arrived just after its final child completed.
            report['recoveredAnswers']['candidate']=report['candidate']
            report['candidate']=[]
        decision=compare_rows(corpus,report['current'],report['candidate'],prepared=prepared)
        if not fresh:
            decision={'accepted':False,'reason':'changed-evaluation-inputs',
                      'message':'Данные, согласие, программа или настройки изменились; нужна новая проверка.'}
        report.update(finishedAt=time.time(),elapsedSeconds=time.monotonic()-started,
            inputsStillFresh=fresh,decision=decision,supervisor=supervisor.receipt(),
            attemptOutcome='failed' if failure or not fresh else 'completed',failureReason=failure,
            fullGateEligible=not failure and fresh and args.probe_limit is None and count==len(prepared)
                and all(report['processAttempts'].get(side,{}).get('admitted') for side in ('current','candidate')))
        report['directAnswerAudit']=summarize_direct_report(report)
        report['recoveredAnswerAudit']=summarize_direct_report({side:report['recoveredAnswers'].get(side,[])
            for side in ('current','candidate')})
        atomic_record(target,report,new=True)
        atomic_record(attempt/'attempt.json',{'version':1,'state':report['attemptOutcome'],
            'startedAt':report['startedAt'],'finishedAt':report['finishedAt'],'inputsSha256':input_sha,
            'reportSha256':file_sha(target),'fullGateEligible':report['fullGateEligible'],
            'failureReason':failure,'processAttempts':report['processAttempts'],
            'automaticPromotion':False})
        if fresh and args.probe_limit is None and count==len(prepared):
            if not diagnostic and not args.diagnostic:record_evaluation(cache,target)
            else:
                from model_history import record_evaluation as record_diagnostic
                receipt=json.loads((Path(inputs['candidate']['path'])/'trainreceipt.json').read_text())
                record_diagnostic(cache,report,decision,receipt,report_sha=file_sha(target),scope='diagnostic-only')
    result=report['decision']
    if args.promote and report['fullGateEligible']:
        result=promote(cache,target,automatic=args.automatic)
    print(json.dumps({'report':str(target),'attemptOutcome':report['attemptOutcome'],
        'fullGateEligible':report['fullGateEligible'],**result},ensure_ascii=False),flush=True)
    return 2 if report['attemptOutcome']=='failed' else 0


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('cache','candidate','training-dataset','output'):parser.add_argument('--'+name,required=True)
    parser.add_argument('--max-seconds',type=int,default=900)
    parser.add_argument('--cases',default=str(CASES))
    parser.add_argument('--diagnostic',action='store_true')
    parser.add_argument('--probe-limit',type=int,help='Diagnostic smoke only; never completes the full acceptance gate')
    parser.add_argument('--promote',action='store_true');parser.add_argument('--automatic',action='store_true')
    parser.add_argument('--model-side',choices=('current','candidate'),help=argparse.SUPPRESS)
    parser.add_argument('--lease-fd',type=int,help=argparse.SUPPRESS)
    parser.add_argument('--watch-fd',type=int,help=argparse.SUPPRESS)
    parser.add_argument('--spec-sha',help=argparse.SUPPRESS)
    args=parser.parse_args()
    if not 60<=args.max_seconds<=1800 or args.automatic and not args.promote:
        parser.error('Нужен предел 60–1800 секунд; --automatic требует --promote')
    if args.probe_limit is not None and not 1<=args.probe_limit<=1000:
        parser.error('Smoke probe count must be bounded')
    if args.model_side:
        if args.promote or args.automatic or args.probe_limit is not None or args.lease_fd is None or args.watch_fd is None or not args.spec_sha:
            parser.error('Internal worker requires inherited lease, parent pipe and immutable specification')
        sys.exit(worker_side(args))
    if args.lease_fd is not None or args.watch_fd is not None or args.spec_sha:
        parser.error('Worker capabilities are valid only in an internal model-side process')
    sys.exit(run(args))
