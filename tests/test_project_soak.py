import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
from project_soak import phase_proof, summary, validate_recipe


class ProjectSoakTests(unittest.TestCase):
    def test_short_successful_run_never_claims_two_hours_or_sleep(self):
        rows=[{'kind':kind,'state':'complete','seconds':1,'peakProcessGroupRssBytes':123,'queueDepth':0} for kind in ('import','editor','photo','training')]
        result=summary(rows,12,12,cancelled=False,memory=[],source_sha='a'*40,recipe_sha='b'*64)
        self.assertTrue(result['acceptance']['allFourRealPhases'])
        self.assertFalse(result['acceptance']['twoHoursMeasured'])
        self.assertFalse(result['acceptance']['fullCriterionMet'])
        self.assertEqual(result['acceptance']['physicalSleepWake'],'not-measured')
        self.assertEqual(result['latencySeconds']['photo'],{'p50':1,'p95':1,'count':1})

    def test_queued_or_terminated_phase_never_counts_as_inference(self):
        rows=[{'kind':'photo','state':'yielded-busy','seconds':0,'queueDepth':1},
              {'kind':'training','state':'phase-timeout','seconds':20,'queueDepth':0}]
        result=summary(rows,7201,7200,cancelled=False,memory=[],source_sha='a'*40,recipe_sha='b'*64)
        self.assertTrue(result['acceptance']['twoHoursMeasured'])
        self.assertFalse(result['acceptance']['allFourRealPhases'])
        self.assertFalse(result['acceptance']['noPhaseErrors'])
        self.assertEqual(result['peakObservedQueueDepth'],1)

    def test_mocked_and_old_proof_are_not_native_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'proof.json'
            base={'kind':'training','ok':True,'isolated':True,'mocked':True,'backend':'mlx','completedSteps':2}
            path.write_text(json.dumps(base))
            self.assertFalse(phase_proof(path,'training',0)['verified'])
            base['mocked']=False;path.write_text(json.dumps(base))
            self.assertTrue(phase_proof(path,'training',0)['verified'])
            self.assertFalse(phase_proof(path,'training',time.time()+60)['verified'])

    def test_recipe_blocks_shell_traversal_and_missing_phase(self):
        phases=[{'id':kind,'kind':kind,'argv':['/usr/bin/python3','{source}/phase.py','{workspace}'],
                 'timeoutSeconds':10,'modelResource':kind in ('photo','training'),'proof':kind+'.json'}
                for kind in ('import','editor','photo','training')]
        value={'version':1,'description':'test','phases':phases}
        self.assertEqual(validate_recipe(json.loads(json.dumps(value)),Path('/isolated'),Path('/source'))['phases'][0]['argv'][2],'/isolated')
        bad=json.loads(json.dumps(value));bad['phases'][0]['argv'][0]='/bin/sh'
        with self.assertRaises(ValueError):validate_recipe(bad,Path('/isolated'),Path('/source'))
        bad=json.loads(json.dumps(value));bad['phases'][0]['proof']='../private.json'
        with self.assertRaises(ValueError):validate_recipe(bad,Path('/isolated'),Path('/source'))
        bad=json.loads(json.dumps(value));bad['phases'].pop()
        with self.assertRaises(ValueError):validate_recipe(bad,Path('/isolated'),Path('/source'))


if __name__=='__main__':unittest.main()
