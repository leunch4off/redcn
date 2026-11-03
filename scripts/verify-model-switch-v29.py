#!/usr/bin/env python3
"""Process/crash acceptance of model selection, using disposable fixture bytes.

This is not an evaluation of model outputs or permission to activate real
weights. No cache argument is accepted by the public command. Each scenario
uses the promotion tests' fresh TemporaryDirectory and deletes it afterwards.
Run the same script on the Mac to measure its OS/process behavior there.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import secrets
import stat
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=Path(__file__).resolve()
sys.path.insert(0,str(ROOT/'cli'))
MARKER='model-switch-synthetic-fixture.json'
FLAGS={'qualityEvidenceSynthetic':True,'noActualModelApproval':True,
       'actualModelInferenceMeasured':False,'actualModelQualityMeasured':False}
CRASH_BEFORE=91
CRASH_AFTER=92


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


def digest(value):
    return hashlib.sha256(value if isinstance(value,bytes) else canonical(value).encode()).hexdigest()


def private_json(path,value):
    descriptor=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
    with os.fdopen(descriptor,'w',encoding='utf-8') as stream:
        stream.write(canonical(value)+'\n');stream.flush();os.fsync(stream.fileno())


def assert_fixture(request):
    """A worker refuses an unmarked cache or any fixture identity outside it."""
    if (not isinstance(request,dict) or set(request)!={'cache','nonce'}
            or not isinstance(request['cache'],str) or not isinstance(request['nonce'],str)):
        raise ValueError('isolated-fixture-required')
    source=Path(request['cache'])
    cache=source.resolve()
    if (source.is_symlink() or cache.parent!=Path(tempfile.gettempdir()).resolve()
            or not cache.is_dir() or cache.stat().st_uid!=os.getuid()
            or stat.S_IMODE(cache.stat().st_mode)&0o077):
        raise ValueError('isolated-fixture-required')
    marker=cache/MARKER
    if marker.is_symlink() or not marker.is_file() or marker.stat().st_size>4096:
        raise ValueError('isolated-fixture-required')
    value=json.loads(marker.read_text())
    if value!={'nonce':request['nonce'],**FLAGS} or len(request['nonce'])!=64:
        raise ValueError('isolated-fixture-required')
    for relative,content in (
        ('tools/text-ai/model/model.safetensors',b'test-frozen-base'),
        ('tools/text-ai/runs/previous/adapters.safetensors',b'previous'),
        ('tools/text-ai/runs/candidate/adapters.safetensors',b'candidate')):
        path=cache/relative
        if (path.is_symlink() or not path.resolve().is_relative_to(cache)
                or not path.is_file() or path.read_bytes()!=content):
            raise ValueError('isolated-fixture-required')
    config_path=cache/'text-ai.json'
    if config_path.is_symlink() or not config_path.is_file() or config_path.stat().st_size>16000:
        raise ValueError('isolated-fixture-required')
    config=json.loads(config_path.read_text())
    permitted={cache/'tools/text-ai/runs/previous',cache/'tools/text-ai/runs/candidate'}
    if (not isinstance(config,dict) or Path(config.get('modelPath','')).resolve()!=cache/'tools/text-ai/model'
            or Path(config.get('adapterPath','')).resolve() not in permitted):
        raise ValueError('isolated-fixture-required')
    report_path=cache/'paired-report.json'
    if report_path.is_symlink() or not report_path.is_file() or report_path.stat().st_size>8_000_000:
        raise ValueError('isolated-fixture-required')
    report=json.loads(report_path.read_text());inputs=report.get('inputs',{})
    if (report.get('qualityEvidenceSynthetic') is not True or report.get('noActualModelApproval') is not True
            or Path(inputs.get('datasetPath','')).resolve()!=cache/'training.json'
            or Path(inputs.get('candidate',{}).get('path','')).resolve()!=cache/'tools/text-ai/runs/candidate'
            or Path(inputs.get('modelPath','')).resolve()!=cache/'tools/text-ai/model'):
        raise ValueError('isolated-fixture-required')
    return cache


def read_selection(cache):
    import local_text_model
    import model_promotion as promotion
    import learning_scheduler
    raw=promotion.raw_config(cache)['value']
    loaded=local_text_model.settings(cache)
    selected=Path(loaded['adapterPath']).resolve()
    with promotion._selection_lock(cache),learning_scheduler.model_resource(cache,kind='background'):
        leases_free=True
    journal_path=cache/'learning/model-promotion.json'
    journal=json.loads(journal_path.read_text()) if journal_path.is_file() else {}
    return {'configReadSucceeded':True,'rawAndRuntimeSelectionAgree':selected==Path(raw['adapterPath']).resolve(),
        'configSha256':digest(raw),'selectedPrevious':selected==cache/'tools/text-ai/runs/previous',
        'selectedCandidate':selected==cache/'tools/text-ai/runs/candidate',
        'selectedWeightsSha256':digest((selected/'adapters.safetensors').read_bytes()),
        'leasesReleased':leases_free,'journalPrepared':journal.get('state')=='prepared',
        'rollbackAvailable':promotion.public_status(cache)['rollbackAvailable']}


def worker(action):
    raw=sys.stdin.buffer.read(16385)
    if len(raw)>16384:raise ValueError('isolated-fixture-required')
    request=json.loads(raw);cache=assert_fixture(request)
    import model_promotion as promotion
    report=cache/'paired-report.json'
    result={'ok':True,'processSha256':digest((request['nonce']+str(os.getpid())).encode()),**FLAGS}
    if action=='read':result.update(read_selection(cache))
    elif action=='rollback':result['fixtureRolledBack']=promotion.rollback(cache).get('rolledBack') is True
    elif action in ('promote','crash-before-replace','crash-after-replace'):
        actual=Path.replace
        def boundary(source,target):
            hit=Path(target).resolve()==cache/'text-ai.json'
            if hit and action=='crash-before-replace':os._exit(CRASH_BEFORE)
            value=actual(source,target)
            if hit and action=='crash-after-replace':os._exit(CRASH_AFTER)
            return value
        with patch.object(Path,'replace',boundary):
            value=promotion.promote(cache,report)
        result['fixtureSelected']=value.get('selected') is True
    elif action in ('corrupt-weights','stale-report'):
        # Prove this unmodified synthetic report qualifies before changing the
        # one condition under test; unrelated failures must not count as a pass.
        _,decision=promotion.validate_report(cache,report)
        if decision.get('accepted') is not True:raise RuntimeError('fixture-checks-failed')
        before=digest(promotion.raw_config(cache)['value'])
        target=cache/'tools/text-ai/runs/candidate/adapters.safetensors' if action=='corrupt-weights' else report
        original=target.read_bytes()
        try:
            if action=='corrupt-weights':target.write_bytes(b'corrupted-synthetic-fixture')
            else:
                value=json.loads(original);value['finishedAt']=time.time()-promotion.POLICY['maximumAgeSeconds']-2
                target.write_text(canonical(value))
            rejected=False
            try:promotion.promote(cache,report)
            except ValueError:rejected=True
            result.update(rejected=rejected,activeSelectionPreserved=before==digest(promotion.raw_config(cache)['value']))
        finally:target.write_bytes(original)
    else:raise ValueError('unknown-worker-action')
    print(canonical(result),flush=True)


def invoke(cache,nonce,action,processes):
    request=canonical({'cache':str(cache),'nonce':nonce})
    completed=subprocess.run([sys.executable,str(SCRIPT),'--worker',action],input=request,
        cwd=ROOT,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=45)
    if action in ('crash-before-replace','crash-after-replace'):
        expected=CRASH_BEFORE if action=='crash-before-replace' else CRASH_AFTER
        return {'crashedAtBoundary':completed.returncode==expected,'emittedNoPrivateOutput':not completed.stdout and not completed.stderr}
    if completed.returncode!=0:raise RuntimeError('worker-failed-'+action)
    value=json.loads(completed.stdout)
    if value.get('ok') is not True or any(value.get(key)!=item for key,item in FLAGS.items()):
        raise RuntimeError('worker-contract-failed')
    processes.append(value['processSha256'])
    return value


def scenario(kind,fixture_type):
    fixture=fixture_type('test_atomic_selection_keeps_training_receipt_and_restores_previous_config')
    fixture.setUp()
    try:
        cache=fixture.cache.resolve();nonce=secrets.token_hex(32)
        report_path,report=fixture.report()
        report.update(qualityEvidenceSynthetic=True,noActualModelApproval=True)
        report_path.write_text(canonical(report));report_path.chmod(0o600)
        private_json(cache/MARKER,{'nonce':nonce,**FLAGS})
        original=digest(fixture.config)
        receipt_before=digest((fixture.candidate/'trainreceipt.json').read_bytes())
        processes=[]
        call=lambda action:invoke(cache,nonce,action,processes)
        checks={}
        if kind=='switchRestartRollback':
            checks['switchCompleted']=call('promote')['fixtureSelected']
            selected=call('read')
            checks.update(restartReadsCandidate=selected['selectedCandidate'],
                completeConfigAfterRestart=selected['configReadSucceeded'] and selected['rawAndRuntimeSelectionAgree'],
                switchedLeasesReleased=selected['leasesReleased'],rollbackAvailable=selected['rollbackAvailable'])
            checks['rollbackCompleted']=call('rollback')['fixtureRolledBack']
        elif kind in ('crashBeforeAtomicReplace','crashAfterAtomicReplace'):
            before=kind=='crashBeforeAtomicReplace'
            crash=call('crash-before-replace' if before else 'crash-after-replace')
            checks.update(crash)
            selected=call('read')
            checks.update(completeConfigAfterRestart=selected['configReadSucceeded'] and selected['rawAndRuntimeSelectionAgree'],
                expectedSelectionAfterCrash=selected['selectedPrevious'] if before else selected['selectedCandidate'],
                preparedJournalSurvives=selected['journalPrepared'],crashedProcessLeasesReleased=selected['leasesReleased'])
            if before:
                checks['originalConfigPreserved']=selected['configSha256']==original
                checks['noRollbackNeeded']=not selected['rollbackAvailable']
                checks['retrySwitchCompleted']=call('promote')['fixtureSelected']
            else:checks['rollbackAvailableAfterCrash']=selected['rollbackAvailable']
            checks['rollbackCompleted']=call('rollback')['fixtureRolledBack']
        else:
            rejected=call('corrupt-weights' if kind=='corruptWeightsRejected' else 'stale-report')
            checks.update(rejected=rejected['rejected'],activeSelectionPreserved=rejected['activeSelectionPreserved'])
        final=call('read')
        checks.update(originalConfigRestored=final['configSha256']==original,
            restartReadsPrevious=final['selectedPrevious'],finalLeasesReleased=final['leasesReleased'],
            candidateReceiptUnchanged=digest((fixture.candidate/'trainreceipt.json').read_bytes())==receipt_before,
            distinctChildProcesses=len(processes)==len(set(processes)) and len(processes)>=2)
        return {'passed':all(checks.values()),'checks':checks,
            'syntheticReportSha256':digest(report_path.read_bytes()),'originalConfigSha256':original}
    finally:fixture.doCleanups()


def verify():
    sys.path.insert(0,str(ROOT/'tests'))
    from test_model_promotion import PromotionTests
    PromotionTests.setUpClass()
    scenarios={}
    for name in ('switchRestartRollback','crashBeforeAtomicReplace','crashAfterAtomicReplace',
                 'corruptWeightsRejected','staleReportRejected'):
        try:scenarios[name]=scenario(name,PromotionTests)
        except Exception as error:
            # A diagnostic must not echo paths, raw fixture answers or arbitrary
            # exception text from a child. Failed control flow remains visible.
            scenarios[name]={'passed':False,'failureTypeSha256':digest(type(error).__name__.encode())}
    artifacts={key:digest((ROOT/relative).read_bytes()) for key,relative in {
        'verifierSha256':'scripts/verify-model-switch-v29.py',
        'fixtureInfrastructureSha256':'tests/test_model_promotion.py',
        'promotionImplementationSha256':'cli/model_promotion.py',
        'schedulerImplementationSha256':'cli/learning_scheduler.py',
        'runtimeSettingsImplementationSha256':'cli/local_text_model.py'}.items()}
    return {'version':1,**FLAGS,'nativeMac':platform.system()=='Darwin',
        'nativeAppleSilicon':platform.system()=='Darwin' and platform.machine()=='arm64',
        'freshTemporaryCachesOnly':True,'activeUserWorkspaceTouched':False,
        'controlFlowPassed':all(row['passed'] for row in scenarios.values()),
        'powerLossDurabilityMeasured':False,'scenarios':scenarios,'artifacts':artifacts}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,help='New private JSON result; existing files are never replaced')
    parser.add_argument('--worker',choices=('read','promote','rollback','crash-before-replace',
        'crash-after-replace','corrupt-weights','stale-report'),help=argparse.SUPPRESS)
    args=parser.parse_args()
    if args.worker:
        try:worker(args.worker);return 0
        except Exception:
            print(canonical({'ok':False,'code':'isolated-worker-failed',**FLAGS}));return 2
    if args.output and (args.output.exists() or args.output.is_symlink()):
        print(canonical({'ok':False,'code':'output-already-exists',**FLAGS}));return 2
    try:
        result=verify()
        if args.output:private_json(args.output,result)
        print(canonical(result))
        return 0 if result['controlFlowPassed'] else 1
    except Exception:
        print(canonical({'ok':False,'code':'verification-failed',**FLAGS}));return 2


if __name__=='__main__':raise SystemExit(main())
