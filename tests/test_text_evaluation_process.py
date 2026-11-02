"""Process durability/admission tests use fixture workers, never real MLX."""
from contextlib import ExitStack,redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'cli'))
from learning_scheduler import model_resource,ModelBusy
from text_model_contract import content_sha
from text_evaluation_process import (atomic_record,inspect_side,journal_answer,
    validate_inherited_lease,WorkerLifetime)
from isolated_job_supervisor import IsolatedJobSupervisor


def evaluator():
    spec=importlib.util.spec_from_file_location('isolated_evaluator',ROOT/'scripts/evaluate-text-candidate.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def finish(folder,side,digest,ids,rows):
    atomic_record(Path(folder)/'terminal.json',{'version':1,'modelSide':side,'inputsSha256':digest,
        'expectedIdsSha256':content_sha(ids),'rowCount':len(rows),'rowsSha256':content_sha(rows),
        'complete':True,'inputsStillFresh':True},new=True)


class DurableTextEvaluationTests(unittest.TestCase):
    def test_hash_order_and_terminal_are_required_without_repair(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);rows=[{'id':'case-one','text':'fixture answer','status':'measured'}]
            digest='a'*64
            journal_answer(folder,'current',digest,0,rows[0])
            result=inspect_side(folder,'current',digest,['case-one'],0)
            self.assertFalse(result['admitted']);self.assertEqual(result['rows'],rows)
            finish(folder,'current',digest,['case-one'],rows)
            self.assertTrue(inspect_side(folder,'current',digest,['case-one'],0)['admitted'])
            self.assertFalse(inspect_side(folder,'candidate',digest,['case-one'],0)['admitted'])
            self.assertFalse(inspect_side(folder,'current','b'*64,['case-one'],0)['admitted'])
            self.assertFalse(inspect_side(folder,'current',digest,['wrong-case'],0)['admitted'])
            path=folder/'rows/00000.json';value=json.loads(path.read_text());value['row']['text']='tampered'
            atomic_record(path,value)
            self.assertFalse(inspect_side(folder,'current',digest,['case-one'],0)['admitted'])

    def test_real_crash_after_terminal_preserves_raw_but_never_admits(self):
        with tempfile.TemporaryDirectory() as tmp:
            code='''import os,sys,signal,resource
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from text_evaluation_process import journal_answer,atomic_record
from text_model_contract import content_sha
resource.setrlimit(resource.RLIMIT_CORE,(0,0))
folder=Path(sys.argv[2]);row={'id':'probe-one','text':'raw fixture saved before signal','status':'measured'}
journal_answer(folder,'candidate','a'*64,0,row)
atomic_record(folder/'terminal.json',{'version':1,'modelSide':'candidate','inputsSha256':'a'*64,
'expectedIdsSha256':content_sha(['probe-one']),'rowCount':1,'rowsSha256':content_sha([row]),'complete':True,'inputsStillFresh':True},new=True)
os.kill(os.getpid(),signal.SIGSEGV)
'''
            child=subprocess.run([sys.executable,'-c',code,str(ROOT/'cli'),tmp],cwd=tmp,timeout=5)
            result=inspect_side(tmp,'candidate','a'*64,['probe-one'],child.returncode)
            self.assertEqual(child.returncode,-signal.SIGSEGV)
            self.assertFalse(result['admitted']);self.assertEqual(result['signal'],signal.SIGSEGV)
            self.assertEqual(result['rows'][0]['text'],'raw fixture saved before signal')
            self.assertEqual((Path(tmp)/'rows/00000.json').stat().st_mode & 0o777,0o600)

    def test_inherited_lease_requires_actual_shared_owner_and_survives_parent_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            child=None
            with model_resource(tmp,kind='background') as fd:
                validate_inherited_lease(tmp,fd)
                other=os.open(Path(tmp)/'learning/model-resource.lock',os.O_RDWR)
                try:
                    with self.assertRaises(ValueError):validate_inherited_lease(tmp,other)
                finally:os.close(other)
                code='''import sys
sys.path.insert(0,sys.argv[1])
from text_evaluation_process import validate_inherited_lease
validate_inherited_lease(sys.argv[2],int(sys.argv[3]))
print('ready',flush=True);sys.stdin.read()
'''
                child=subprocess.Popen([sys.executable,'-c',code,str(ROOT/'cli'),tmp,str(fd)],
                    stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True,pass_fds=(fd,))
                self.assertEqual(child.stdout.readline().strip(),'ready')
            try:
                with self.assertRaises(ModelBusy):
                    with model_resource(tmp,kind='background'):pass
            finally:child.communicate(input='',timeout=5)
            fd=os.open(Path(tmp)/'learning/model-resource.lock',os.O_RDWR)
            try:
                with self.assertRaisesRegex(ValueError,'already held'):validate_inherited_lease(tmp,fd)
            finally:os.close(fd)

    def test_parent_pipe_loss_ends_only_owned_worker_with_durable_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            reader,writer=os.pipe()
            code='''import sys,time
sys.path.insert(0,sys.argv[1])
from text_evaluation_process import WorkerLifetime
with WorkerLifetime(int(sys.argv[2]),time.monotonic()+30,sys.argv[3],grace=.1):
 print('ready',flush=True);time.sleep(30)
'''
            child=subprocess.Popen([sys.executable,'-c',code,str(ROOT/'cli'),str(reader),tmp],
                stdout=subprocess.PIPE,text=True,pass_fds=(reader,))
            os.close(reader)
            try:
                self.assertEqual(child.stdout.readline().strip(),'ready')
                os.close(writer);writer=None
                self.assertEqual(child.wait(timeout=5),75)
                self.assertEqual(json.loads((Path(tmp)/'lifetime-stop.json').read_text())['reason'],'parent-disconnected')
            finally:
                if writer is not None:os.close(writer)
                if child.poll() is None:child.kill();child.wait(timeout=3)
                child.stdout.close()

    def test_parent_excludes_crashed_complete_side_and_smoke_never_enters_gate(self):
        module=evaluator()
        fixture='''import os,sys,json,signal,resource
from pathlib import Path
sys.path.insert(0,sys.argv[1]);args=sys.argv[2:]
def arg(name):return args[args.index(name)+1]
from text_evaluation_process import validate_inherited_lease,read_record,journal_answer,atomic_record
from text_model_contract import content_sha
validate_inherited_lease(arg('--cache'),int(arg('--lease-fd')))
target=Path(arg('--output'));attempt=target.with_name(target.name+'.attempt');side=arg('--model-side');folder=attempt/side
spec=read_record(attempt/'inputs.json',maximum=3000000);rows=[]
for index,identity in enumerate(spec['expectedIds']):
 row={'id':identity,'caseId':'fixture','task':'ru-zh','text':'fixture, not real model evidence','status':'measured','backend':'fixture','fallback':False}
 journal_answer(folder,side,spec['inputsSha256'],index,row);rows.append(row)
atomic_record(folder/'terminal.json',{'version':1,'modelSide':side,'inputsSha256':spec['inputsSha256'],
'expectedIdsSha256':content_sha(spec['expectedIds']),'rowCount':len(rows),'rowsSha256':content_sha(rows),'complete':True,'inputsStillFresh':True},new=True)
if os.environ.get('FIXTURE_CRASH_SIDE')==side:
 resource.setrlimit(resource.RLIMIT_CORE,(0,0));os.kill(os.getpid(),signal.SIGSEGV)
'''
        for probe_limit,crash,changed in ((None,'candidate',False),(1000,'',False),(None,'',True)):
            with self.subTest(probe_limit=probe_limit,changed=changed),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);candidate=root/'candidate';candidate.mkdir();(candidate/'trainreceipt.json').write_text('{}')
                args=SimpleNamespace(cache=tmp,candidate=str(candidate),training_dataset=str(root/'data.json'),
                    cases=str(module.CASES),output=str(root/'report.json'),max_seconds=60,
                    diagnostic=True,promote=False,automatic=False,probe_limit=probe_limit)
                inputs={'candidate':{'path':str(candidate)},'current':None}
                native_spawn=IsolatedJobSupervisor.spawn
                def fixture_spawn(owner,command,**kwargs):
                    # Replace only native execution; real process groups, FDs,
                    # journals, RSS sampling and parent merging are exercised.
                    return native_spawn(owner,[sys.executable,'-c',fixture,str(ROOT/'cli'),*command[4:]],**kwargs)
                with ExitStack() as stack:
                    stack.enter_context(patch.object(module,'require_native'))
                    stack.enter_context(patch.object(module,'current_inputs',
                        **({'side_effect':[inputs,{**inputs,'changed':True}]} if changed else {'return_value':inputs})))
                    stack.enter_context(patch.object(module,'load_cases',return_value={'cases':[{'id':'fixture'}]}))
                    stack.enter_context(patch.object(module,'active_rules',return_value={}))
                    stack.enter_context(patch.object(module,'prepare_probes',return_value=[{'id':'fixture-ru-zh','caseId':'fixture','task':'ru-zh'}]))
                    stack.enter_context(patch.object(IsolatedJobSupervisor,'spawn',fixture_spawn))
                    stack.enter_context(patch('model_history.record_evaluation'))
                    stack.enter_context(patch.dict(os.environ,{'FIXTURE_CRASH_SIDE':crash}))
                    stack.enter_context(redirect_stdout(io.StringIO()))
                    exit_code=module.run(args)
                report=json.loads(Path(args.output).read_text())
                self.assertFalse(report['decision']['accepted']);self.assertFalse(report['fullGateEligible'])
                self.assertEqual(report['candidate'],[])
                self.assertEqual(len(report['recoveredAnswers']['candidate']),1)
                if changed:
                    self.assertEqual(exit_code,2);self.assertFalse(report['inputsStillFresh'])
                    self.assertEqual(report['decision']['reason'],'changed-evaluation-inputs')
                    self.assertEqual(len(report['current']),1)
                elif crash:
                    self.assertEqual(exit_code,2);self.assertEqual(report['processAttempts']['candidate']['signal'],signal.SIGSEGV)
                    self.assertEqual(len(report['current']),1)
                else:
                    self.assertEqual(exit_code,0);self.assertEqual(report['current'],[])
                    self.assertEqual(len(report['recoveredAnswers']['current']),1)
                self.assertTrue((root/'report.json.attempt/attempt.json').is_file())


if __name__=='__main__':unittest.main()
