"""Idle learning yields to foreground work and cannot promote or repeat a model."""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'cli'))
import learning_scheduler as scheduler
from learning_memory import LearningMemory
from text_model_contract import validate_dataset


class DoneProcess:
    returncode = 0
    def poll(self): return self.returncode
    def wait(self, timeout=None): return self.returncode


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.memory = LearningMemory(self.directory)
        self.now = 1000
        self.calls = []

    def tearDown(self):
        self.temp.cleanup()

    def ready(self):
        # Independent lexical groups, deterministic split, sufficient holdout.
        for index in range(60):
            self.memory.record('translation', {'ru':'Стол номер '+str(index), 'zh':'桌子编号'+str(index)},
                status='approved', group_id='lexical-'+str(index))
        scheduler.save_config(self.directory, {'enabled':True, 'idleSeconds':60})
        trainer = scheduler.IdleTrainer(self.directory, clock=lambda:self.now, popen=self.completed)
        trainer.started = 0
        return trainer

    def completed(self, args, **kwargs):
        self.calls.append(args)
        dataset = Path(args[args.index('--dataset')+1])
        validate_dataset(json.loads(dataset.read_text()))
        output = Path(args[args.index('--output')+1])
        output.mkdir(parents=True, exist_ok=True)
        artifact = b'test-adapter'
        (output/'adapters.safetensors').write_bytes(artifact)
        (output/'optimizer.safetensors').write_bytes(b'test-optimizer')
        (output/'initial.safetensors').write_bytes(b'test-initial')
        (output/'trainreceipt.json').write_text(json.dumps({'status':'trained-unaccepted',
            'stepsCompleted':20, 'trainableParameters':540672,
            'adapterSha256':hashlib.sha256(artifact).hexdigest()}))
        return DoneProcess()

    def runtime(self, trainer):
        return patch.object(trainer, '_runtime', return_value=(['python','train.py'], self.directory/'model'))

    def test_default_disabled_never_spawns(self):
        trainer = scheduler.IdleTrainer(self.directory, popen=self.completed)
        self.assertEqual(trainer.tick()['phase'], 'disabled')
        self.assertFalse(self.calls)

    def test_pending_generated_data_is_ineligible(self):
        for index in range(25):
            self.memory.record('translation', {'ru':'Вещь '+str(index),'zh':'物品'+str(index)}, source='generated',status='approved')
        scheduler.save_config(self.directory, {'enabled':True})
        trainer = scheduler.IdleTrainer(self.directory, clock=lambda:self.now, popen=self.completed)
        trainer.started = 0
        self.assertEqual(trainer.tick()['phase'], 'collecting')
        self.assertFalse(self.calls)

    def test_existing_server_activity_prevents_training(self):
        trainer = self.ready()
        trainer.busy = lambda: True
        self.assertEqual(trainer.tick()['phase'], 'waiting-idle')
        self.assertFalse(self.calls)

    def test_recent_foreground_request_prevents_training(self):
        trainer = self.ready()
        scheduler._write(trainer.folder/'foreground.json', {'at':self.now-5})
        self.assertEqual(trainer.tick()['phase'], 'waiting-idle')
        self.assertFalse(self.calls)

    def test_read_only_activity_never_recovers_or_modifies_campaign(self):
        folder = self.directory/'import-diagnostics'
        folder.mkdir()
        path = folder/'campaigns.sqlite3'
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE attempts(finished REAL)')
            db.execute('INSERT INTO attempts VALUES(NULL)')
        before = path.read_bytes()
        self.assertEqual(scheduler.active_imports(self.directory), 1)
        self.assertEqual(path.read_bytes(), before)
        trainer = self.ready()
        self.assertEqual(trainer.tick()['phase'], 'waiting-idle')

    def test_missing_import_database_does_not_create_one(self):
        self.assertEqual(scheduler.active_imports(self.directory), 0)
        self.assertFalse((self.directory/'import-diagnostics').exists())

    def test_success_is_saved_as_candidate_once_no_auto_promotion(self):
        trainer = self.ready()
        with self.runtime(trainer):
            result = trainer.tick()
            self.assertEqual(result['phase'], 'ready-for-review')
            self.assertEqual(result['trainableParameters'], 540672)
            self.assertFalse(result['automaticPromotion'])
            trainer.tick()
        self.assertEqual(len(self.calls), 1)
        self.assertFalse((self.directory/'text-ai.json').exists())
        self.assertEqual(self.memory.checkpoints()[0]['state'], 'unaccepted')
        self.assertNotIn('sampleHashes', scheduler.status(self.directory))

    def test_auto_evaluation_starts_after_training_releases_shared_model_gate(self):
        trainer = self.ready()
        scheduler.save_config(self.directory, {'automaticPromotion': True})
        def evaluate(config):
            with scheduler.model_resource(self.directory, kind='background'):
                return {'phase': 'evaluating'}
        with self.runtime(trainer), patch.object(trainer, '_evaluate', side_effect=evaluate) as evaluator:
            self.assertEqual(trainer.tick()['phase'], 'evaluating')
            evaluator.assert_called_once()
        self.assertEqual(len(self.calls), 1)
        self.assertFalse((self.directory/'text-ai.json').exists())

    def test_turning_auto_accept_on_evaluates_saved_candidate_without_retraining(self):
        trainer = self.ready()
        with self.runtime(trainer):
            trainer.tick()
        scheduler.save_config(self.directory, {'automaticPromotion': True})
        with patch.object(trainer, '_evaluate', return_value={'phase': 'evaluating'}) as evaluator:
            self.assertEqual(trainer.tick()['phase'], 'evaluating')
            evaluator.assert_called_once()
        self.assertEqual(len(self.calls), 1)

    def test_blocked_quality_does_not_repeat_evaluation_or_training_without_new_data(self):
        trainer = self.ready()
        with self.runtime(trainer):
            trainer.tick()
        trainer.state.update(qualityOutcome='blocked', qualityMessage='Текущие веса сохранены')
        scheduler.save_config(self.directory, {'automaticPromotion': True})
        with patch.object(trainer, '_evaluate') as evaluator:
            self.assertEqual(trainer.tick()['phase'], 'quality-blocked')
            evaluator.assert_not_called()
        self.assertEqual(len(self.calls), 1)

    def test_foreground_gate_signals_cancel_and_cannot_overlap_training(self):
        with scheduler.model_resource(self.directory, kind='background'):
            with self.assertRaises(scheduler.ModelBusy):
                with scheduler.model_resource(self.directory, timeout=0):
                    self.fail('foreground entered occupied model resource')
            self.assertTrue((self.directory/'learning/training.cancel').is_file())
        with scheduler.model_resource(self.directory):
            pass

    def test_tampered_adapter_receipt_is_not_reported_trained(self):
        trainer = self.ready()
        normal = trainer.popen
        def bad(args, **kwargs):
            process = normal(args, **kwargs)
            output = Path(args[args.index('--output')+1])
            (output/'adapters.safetensors').write_bytes(b'mismatch')
            return process
        trainer.popen = bad
        with self.runtime(trainer):
            result = trainer.tick()
        self.assertEqual(result['phase'], 'failed')
        self.assertEqual(result['trainableParameters'], 0)

    def test_completed_dataset_is_not_retrained_after_restart(self):
        trainer = self.ready()
        with self.runtime(trainer): trainer.tick()
        restarted = scheduler.IdleTrainer(self.directory, clock=lambda:self.now+10000, popen=self.completed)
        restarted.started = 0
        with self.runtime(restarted): restarted.tick()
        self.assertEqual(len(self.calls), 1)

    def test_import_interrupts_training_and_saved_adapter_can_resume(self):
        trainer = self.ready()
        active = [False]
        trainer.busy = lambda:active[0]
        normal = trainer.popen
        class RunningProcess:
            returncode = None
            def poll(self): return self.returncode
            def wait(self, timeout=None):
                self.returncode = 0
                return 0
        def running(args, **kwargs):
            normal(args, **kwargs)
            active[0] = True
            return RunningProcess()
        trainer.popen = running
        with self.runtime(trainer):
            result = trainer.tick()
        self.assertEqual(result['phase'], 'paused')
        self.assertEqual(self.memory.checkpoints()[0]['state'], 'paused')
        previous_job = result['jobId']
        active[0] = False
        self.now += 4000
        trainer.popen = normal
        with self.runtime(trainer):
            resumed = trainer.tick()
        self.assertEqual(resumed['jobId'], previous_job)
        self.assertEqual(resumed['phase'], 'ready-for-review')
        self.assertIn('--resume-adapter', self.calls[-1])

    def test_new_approved_batch_trains_fresh_candidate_after_previous_review(self):
        trainer = self.ready()
        with self.runtime(trainer): first = trainer.tick()
        for index in range(20):
            self.memory.record('translation', {'ru':'Кресло номер '+str(index), 'zh':'扶手椅编号'+str(index)},
                status='approved', group_id='new-lexical-'+str(index))
        with self.runtime(trainer): second = trainer.tick()
        self.assertEqual(len(self.calls), 2)
        self.assertNotEqual(first['jobId'], second['jobId'])

    def test_settings_bound_training_budget_and_reject_unknown_fields(self):
        for update in ({'maxSeconds':601},{'maxSteps':201},{'minNewExamples':1},{'command':'sh'}, {'enabled':1}):
            with self.assertRaises(ValueError): scheduler.save_config(self.directory, update)
        self.assertEqual(scheduler.load_config(self.directory), scheduler.DEFAULTS)

    def test_training_batch_prefers_recent_rows_and_obeys_byte_and_count_limits(self):
        path = self.directory/'rows.jsonl'
        values = [{'id':str(index), 'messages':[{'role':'user','content':'Переведи стол'},
            {'role':'assistant','content':'桌子'}]} for index in range(8)]
        values.append({'id':'long', 'messages':[{'role':'user','content':'a'*6001}]})
        path.write_text(''.join(json.dumps(row,ensure_ascii=False)+'\n' for row in values))
        rows = scheduler._training_rows(path, 3, 10000)
        self.assertEqual([row['id'] for row in rows], ['5','6','7'])
        self.assertTrue(all(row['status']=='approved' for row in rows))
        self.assertLessEqual(sum(len(json.dumps(row,ensure_ascii=False).encode()) for row in scheduler._training_rows(path, 3, 200)), 200)

    def test_server_close_joins_writer_before_cache_can_be_removed(self):
        import local_server
        entered, release, closed = threading.Event(), threading.Event(), threading.Event()
        original = scheduler.IdleTrainer._state
        def delayed_state(instance, *args, **kwargs):
            entered.set()
            release.wait(timeout=2)
            return original(instance, *args, **kwargs)
        with patch.object(scheduler.IdleTrainer, '_state', delayed_state):
            server = local_server.create_server(self.directory, 0)
            worker = server.learning_scheduler.thread
            self.assertTrue(entered.wait(timeout=1))
            errors = []
            def close():
                try:server.server_close()
                except Exception as error:errors.append(error)
                finally:closed.set()
            closer = threading.Thread(target=close)
            closer.start()
            self.assertFalse(closed.wait(timeout=.03))
            release.set()
            closer.join(timeout=2)
        self.assertTrue(closed.is_set())
        self.assertFalse(errors)
        self.assertFalse(worker.is_alive())
        self.assertNotIn(str(self.directory), scheduler._THREADS)
        self.temp.cleanup()
        time.sleep(.03)
        self.assertFalse(self.directory.exists())

    def test_shutdown_does_not_leave_running_state_or_allow_late_writes(self):
        trainer = self.ready()
        trainer._state('training', 'test', lastOutcome='running', jobId='idle-test')
        trainer.stop()
        saved = scheduler.status(self.directory)
        self.assertEqual(saved['phase'], 'paused')
        self.assertEqual(saved['lastOutcome'], 'paused')
        trainer._state('training', 'late writer')
        self.assertEqual(scheduler.status(self.directory)['phase'], 'paused')


if __name__ == '__main__':
    unittest.main()
