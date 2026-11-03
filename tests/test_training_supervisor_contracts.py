"""Supervisor failure/receipt contracts; these fixtures do not train a model."""
from contextlib import nullcontext
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'cli'))


def module(name,file):
    spec=importlib.util.spec_from_file_location(name,ROOT/'scripts'/file)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value


experiment=module('experiment_supervisor_test','run-text-experiment-v29.py')
native=module('native_supervisor_test','native-training-acceptance.py')


def receipt(output,dataset,steps=2):
    output.mkdir(parents=True,exist_ok=True)
    value={'status':'trained-unaccepted','stepsCompleted':steps,'stoppedEarly':False,'trainableParameters':540672,
        'datasetSha256':hashlib.sha256(dataset.read_bytes()).hexdigest()}
    for name,key in (('adapters.safetensors','adapterSha256'),('optimizer.safetensors','optimizerSha256'),('initial.safetensors','initialSha256')):
        data=('synthetic-'+name).encode();(output/name).write_bytes(data);value[key]=hashlib.sha256(data).hexdigest()
    (output/'trainreceipt.json').write_text(json.dumps(value));return value


class TrainingSupervisorContracts(unittest.TestCase):
    def test_exact_steps_explicit_termination_and_all_checkpoint_hashes_are_required(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);dataset=root/'dataset.json';dataset.write_text('{}');output=root/'candidate'
            value=receipt(output,dataset)
            self.assertTrue(experiment.completed_receipt(output,dataset,2,0)[1])
            for altered in ({**value,'stepsCompleted':1},{key:item for key,item in value.items() if key!='stoppedEarly'},
                            {**value,'stoppedEarly':True},{**value,'status':'training'}):
                (output/'trainreceipt.json').write_text(json.dumps(altered))
                self.assertFalse(experiment.completed_receipt(output,dataset,2,0)[1])
            (output/'trainreceipt.json').write_text(json.dumps(value));(output/'optimizer.safetensors').write_bytes(b'partial')
            self.assertFalse(experiment.completed_receipt(output,dataset,2,0)[1])

    def test_exit_zero_with_short_receipt_finalizes_failed_experiment_and_does_not_continue(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);source=root/'source';cache=root/'cache';work=root/'work'
            (source/'data/learning').mkdir(parents=True);(source/'data/quality').mkdir();(source/'scripts').mkdir();(source/'cli').mkdir()
            cache.mkdir();model=cache/'model';model.mkdir();(model/'config.json').write_text('{}')
            initial=cache/'initial';initial.mkdir();(initial/'adapters.safetensors').write_bytes(b'initial')
            artifacts={}
            for regime in ('advertising-only','translation-retention'):
                path=source/('data/learning/text-v29-'+regime+'.json');path.write_text('{}')
                artifacts[str(path.relative_to(source))]=experiment.digest(path)
            for path in ('scripts/train-local-text-model.py','cli/text_model_contract.py','scripts/run-text-experiment-v29.py',
                         'scripts/audit-text-objective-v29.py','cli/training_resources.py','cli/isolated_job_supervisor.py'):(source/path).write_text('fixture')
            protocol={'artifacts':artifacts,'baseConfigSha256':experiment.digest(model/'config.json'),
                'initialAdapterSha256':experiment.digest(initial/'adapters.safetensors'),
                'recipe':{'steps':2,'maxSecondsPerRun':30,'maxSequenceLength':64,'evalExamples':2,'learningRate':.00002,'seed':1,'sampling':'shuffled'}}
            (source/'data/quality/text-experiment-v29.json').write_text(json.dumps(protocol))
            calls=[]
            class FakeOwner:
                cancelled=threading.Event();reason=None
                def __init__(self,**_):pass
                def __enter__(self):return self
                def __exit__(self,*_):return False
                def check(self):pass
                def stop_owned(self,_):pass
                def receipt(self):return {'interrupted':False}
                def wait(self,process,**_):return process.returncode
                def spawn(self,command,**_):
                    output=Path(command[command.index('--output')+1])
                    if '--steps' not in command:output.write_text('{}')
                    else:
                        calls.append(command);dataset=Path(command[command.index('--dataset')+1]);receipt(output,dataset,steps=1)
                    return SimpleNamespace(returncode=0)
            config={'modelPath':str(model),'adapterPath':str(initial),'pythonPath':'not-executed'}
            memory=Mock();resources=Mock();resources.check.return_value=None;resources.receipt.return_value={}
            with patch.object(experiment,'ROOT',source),patch.object(experiment,'settings',return_value=config),\
                 patch.object(experiment,'model_resource',return_value=nullcontext(9)),\
                 patch.object(experiment,'IsolatedJobSupervisor',FakeOwner),\
                 patch.object(experiment,'ResourceGuard',return_value=resources),\
                 patch.object(experiment,'LearningMemory',return_value=memory):
                with self.assertRaisesRegex(ValueError,'did not complete'):
                    experiment.run(SimpleNamespace(cache=cache,workspace=work,run_label='v29-test-failure'))
            saved=json.loads((work/'experiment.json').read_text())
            self.assertEqual(saved['outcome'],'failed');self.assertFalse(saved['completeDeclaredExperiment'])
            self.assertIn('finishedAt',saved);self.assertTrue(saved['productionSettingsUnchanged'])
            self.assertEqual(len(calls),1);self.assertEqual(len(saved['runs']),1)
            self.assertFalse(saved['runs'][0]['completeDeclaredSteps']);memory.register_candidate.assert_not_called()

    def test_old_checkpoint_and_joined_thread_cannot_prove_active_server_stop(self):
        with tempfile.TemporaryDirectory() as name:
            app=Path(name);worker=Mock();worker.is_alive.return_value=False
            trainer=SimpleNamespace(_process=None,state={'phase':'ready-for-review','jobId':'idle-old'},stop=Mock())
            with self.assertRaisesRegex(RuntimeError,'No active owned'):
                native.stop_active_training(trainer,worker,app)
            trainer.stop.assert_not_called()

    def test_stop_checks_current_owned_job_checkpoint_instead_of_any_prior_receipt(self):
        with tempfile.TemporaryDirectory() as name:
            app=Path(name);old=app/'learning/training-runs/idle-old';old.mkdir(parents=True)
            (old/'dataset.json').write_text('{}');receipt(old/'candidate',old/'dataset.json')
            process=SimpleNamespace(returncode=None);process.poll=lambda:process.returncode
            worker=Mock();worker.is_alive.side_effect=[True,False]
            def stop():
                process.returncode=-15
                (app/'learning/scheduler-state.json').write_text(json.dumps({'phase':'paused'}))
            trainer=SimpleNamespace(_process=process,state={'phase':'training','jobId':'idle-new'},stop=stop)
            result=native.stop_active_training(trainer,worker,app)
            self.assertTrue(result['ownedProcessObservedAlive']);self.assertTrue(result['workerJoined'])
            self.assertTrue(result['ownedProcessExited']);self.assertFalse(result['checkpointValid'])
            self.assertEqual(result['jobIdSha256'],hashlib.sha256(b'idle-new').hexdigest())


if __name__=='__main__':unittest.main()
