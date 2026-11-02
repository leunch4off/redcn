"""Crash ownership, partial checkpoints and admission are independent gates."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'cli'))
import learning_scheduler as scheduler
from model_history import public_history


class RecoveryTests(unittest.TestCase):
    def setUp(self):self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()

    def test_child_retains_model_lease_after_supervisor_crash_then_releases_it(self):
        parent=r'''
import os,sys,subprocess,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from learning_scheduler import model_resource
root=Path(sys.argv[2])
child="import sys,time;from pathlib import Path;p=Path(sys.argv[1]);(p/'ready').write_text('yes');deadline=time.monotonic()+6\nwhile not (p/'finish').exists() and time.monotonic()<deadline: time.sleep(.03)"
with model_resource(root,kind='background') as fd:
 process=subprocess.Popen([sys.executable,'-c',child,str(root)],pass_fds=(fd,),start_new_session=True,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 end=time.monotonic()+3
 while not (root/'ready').exists() and time.monotonic()<end:time.sleep(.02)
 os._exit(23)
'''
        process=subprocess.Popen([sys.executable,'-c',parent,str(ROOT/'cli'),str(self.root)])
        try:
            self.assertEqual(process.wait(timeout=5),23)
            self.assertTrue((self.root/'ready').exists())
            with self.assertRaises(scheduler.ModelBusy):
                with scheduler.model_resource(self.root,kind='background'):pass
        finally:
            (self.root/'finish').write_text('stop owned fixture')
            if process.poll() is None:process.kill();process.wait(timeout=2)
        with scheduler.model_resource(self.root,kind='background',timeout=3):pass

    def test_restarted_scheduler_does_not_claim_stale_running_state(self):
        folder=self.root/'learning';folder.mkdir()
        scheduler._write(folder/'scheduler-state.json',{'phase':'training','lastOutcome':'running','jobId':'idle-fixture'})
        scheduler.IdleTrainer(self.root)
        self.assertEqual(scheduler.status(self.root)['phase'],'recovery-pending')
        self.assertFalse(public_history(self.root)['activeTraining'])

    def test_live_child_retains_lease_after_normal_parent_context_exit(self):
        process=None
        try:
            with scheduler.model_resource(self.root,kind='background') as descriptor:
                process=subprocess.Popen([sys.executable,'-u','-c',
                    "import sys;print('ready',flush=True);sys.stdin.read()"],
                    stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                    pass_fds=(descriptor,),text=True)
                self.assertEqual(process.stdout.readline().strip(),'ready')
            self.assertIsNone(process.poll())
            with self.assertRaises(scheduler.ModelBusy):
                with scheduler.model_resource(self.root,kind='background'):pass
        finally:
            if process is not None:
                process.communicate('',timeout=3)
                if process.poll() is None:process.kill();process.wait(timeout=2)
        with scheduler.model_resource(self.root,kind='background',timeout=1):pass

    def test_new_job_resets_old_candidate_metrics_but_resume_preserves_its_steps(self):
        trainer=scheduler.IdleTrainer(self.root)
        trainer.state.update(jobId='idle-old',stepsCompleted=87,adapterReady=True,trainableParameters=540672)
        trainer._state('training','New candidate',jobId='idle-new',lastOutcome='running')
        self.assertEqual(trainer.state['stepsCompleted'],0)
        self.assertFalse(trainer.state['adapterReady'])
        trainer.state['stepsCompleted']=10
        trainer._state('training','Resume same candidate',jobId='idle-new',lastOutcome='running')
        self.assertEqual(trainer.state['stepsCompleted'],10)

    def test_interrupted_multifile_checkpoint_is_not_resumed(self):
        folder=self.root/'candidate';folder.mkdir()
        dataset=self.root/'dataset.json';dataset.write_text('{}')
        receipt={'stepsCompleted':10,'datasetSha256':hashlib.sha256(dataset.read_bytes()).hexdigest()}
        for name,key in (('adapters.safetensors','adapterSha256'),('optimizer.safetensors','optimizerSha256'),('initial.safetensors','initialSha256')):
            (folder/name).write_bytes(name.encode());receipt[key]=hashlib.sha256(name.encode()).hexdigest()
        (folder/'trainreceipt.json').write_text(json.dumps(receipt))
        self.assertTrue(scheduler.valid_checkpoint(folder,dataset))
        (folder/'optimizer.safetensors').write_bytes(b'half-new-optimizer')
        self.assertFalse(scheduler.valid_checkpoint(folder,dataset))
        self.assertTrue((folder/'optimizer.safetensors').exists())


if __name__=='__main__':unittest.main()
