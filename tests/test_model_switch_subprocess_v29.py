"""Fresh child processes exercise real replacement and OS lease recovery."""
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/'scripts/verify-model-switch-v29.py'


class NativeSwitchProcessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.output=Path(cls.temp.name)/'private-acceptance.json'
        cls.process=subprocess.run([sys.executable,str(SCRIPT),'--output',str(cls.output)],
            cwd=ROOT,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=180)
        cls.result=json.loads(cls.process.stdout)

    def test_switch_restart_and_rollback_are_separate_processes_with_real_locks(self):
        self.assertEqual(self.process.returncode,0,self.result)
        self.assertTrue(self.result['controlFlowPassed'])
        result=self.result['scenarios']['switchRestartRollback']
        self.assertTrue(result['passed'],result)
        for key in ('switchCompleted','restartReadsCandidate','rollbackCompleted',
                    'originalConfigRestored','restartReadsPrevious','switchedLeasesReleased',
                    'finalLeasesReleased','distinctChildProcesses','candidateReceiptUnchanged'):
            self.assertTrue(result['checks'][key],key)

    def test_hard_exit_before_and_after_atomic_replace_preserves_recovery(self):
        for name in ('crashBeforeAtomicReplace','crashAfterAtomicReplace'):
            with self.subTest(scenario=name):
                result=self.result['scenarios'][name]
                self.assertTrue(result['passed'],result)
                for key in ('crashedAtBoundary','emittedNoPrivateOutput','completeConfigAfterRestart',
                            'expectedSelectionAfterCrash','preparedJournalSurvives','crashedProcessLeasesReleased',
                            'rollbackCompleted','originalConfigRestored'):
                    self.assertTrue(result['checks'][key],key)
        self.assertTrue(self.result['scenarios']['crashBeforeAtomicReplace']['checks']['retrySwitchCompleted'])
        self.assertTrue(self.result['scenarios']['crashAfterAtomicReplace']['checks']['rollbackAvailableAfterCrash'])

    def test_corrupt_weights_and_stale_report_are_rejected_without_changing_active_config(self):
        for name in ('corruptWeightsRejected','staleReportRejected'):
            with self.subTest(scenario=name):
                result=self.result['scenarios'][name]
                self.assertTrue(result['passed'],result)
                for key in ('rejected','activeSelectionPreserved','originalConfigRestored','finalLeasesReleased'):
                    self.assertTrue(result['checks'][key],key)

    def test_receipt_is_private_path_free_and_explicitly_not_real_model_acceptance(self):
        self.assertEqual(json.loads(self.output.read_text()),self.result)
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode),0o600)
        for flag in ('qualityEvidenceSynthetic','noActualModelApproval','freshTemporaryCachesOnly'):
            self.assertTrue(self.result[flag])
        for flag in ('actualModelInferenceMeasured','actualModelQualityMeasured','activeUserWorkspaceTouched','powerLossDurabilityMeasured'):
            self.assertFalse(self.result[flag])
        for private in (str(ROOT),self.temp.name,str(Path.home()),'Стол','桌子','adapterPath','datasetPath'):
            self.assertNotIn(private,self.process.stdout)
        self.assertEqual(self.process.stderr,'')
        for value in self.result['artifacts'].values():self.assertRegex(value,r'^[0-9a-f]{64}$')
        before=self.output.read_bytes()
        repeated=subprocess.run([sys.executable,str(SCRIPT),'--output',str(self.output)],
            cwd=ROOT,text=True,capture_output=True,timeout=10)
        self.assertEqual(repeated.returncode,2)
        self.assertEqual(self.output.read_bytes(),before)

    def test_worker_rejects_unmarked_cache_without_mutating_it(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'text-ai.json';original=b'{"syntheticUnmarked":true}';path.write_bytes(original)
            response=subprocess.run([sys.executable,str(SCRIPT),'--worker','promote'],
                input=json.dumps({'cache':folder,'nonce':'a'*64}),cwd=ROOT,text=True,capture_output=True,timeout=10)
            self.assertEqual(response.returncode,2)
            self.assertFalse(json.loads(response.stdout)['ok'])
            self.assertEqual(path.read_bytes(),original)
            self.assertEqual(list(Path(folder).iterdir()),[path])
            self.assertNotIn(folder,response.stdout+response.stderr)


if __name__=='__main__':unittest.main()
