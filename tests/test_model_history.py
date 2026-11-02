"""Candidate receipts never become an implicit authorization to switch models."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
from learning_memory import LearningMemory
import model_history


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.memory=LearningMemory(self.root)

    def tearDown(self):self.temp.cleanup()

    def candidate(self,name,content=b'fixture'):
        folder=self.root/'tools/text-ai/runs'/name
        folder.mkdir(parents=True)
        (folder/'adapters.safetensors').write_bytes(content)
        digest=hashlib.sha256(content).hexdigest()
        receipt={'adapterSha256':digest,'status':'trained-unaccepted','stepsCompleted':20,
                 'examples':{'train':24,'validation':6},'datasetSha256':'d'*64,
                 'elapsedSeconds':19,'peakMemoryBytes':1234}
        (folder/'trainreceipt.json').write_text(json.dumps(receipt))
        return folder,receipt

    def test_live_selection_is_not_inferred_from_latest_training_receipt(self):
        first,_=self.candidate('first',b'first')
        second,_=self.candidate('later',b'later')
        (self.root/'text-ai.json').write_text(json.dumps({'adapterPath':str(first),'useCandidateAdapter':True}))
        value=model_history.public_history(self.root)
        selected=[row for row in value['candidates'] if row['selected']]
        self.assertEqual(len(selected),1)
        self.assertEqual(selected[0]['weightsSha256'],hashlib.sha256(b'first').hexdigest())
        self.assertNotIn(str(self.root),json.dumps(value))
        self.assertTrue(all(row['freshValidationRequiredForSelection'] for row in value['candidates']))

    def test_evaluation_bound_to_weight_hash_and_all_private_text_removed(self):
        folder,receipt=self.candidate('first')
        report={'finishedAt':100,'inputs':{'candidate':{'weightsSha256':receipt['adapterSha256']},
                'datasetSha256':'d'*64,'casesSha256':'c'*64,'checkers':{'private/path':'e'*64}}}
        decision={'accepted':False,'reason':'candidate-factual-errors','requested':30,
                  'candidatePassed':4,'regressions':['secret-address'],'candidateFailures':['secret-prompt']}
        model_history.record_evaluation(self.root,report,decision,receipt,report_sha='a'*64)
        result=model_history.public_history(self.root)
        encoded=json.dumps(result)
        for secret in ('secret-address','secret-prompt','private/path',str(self.root)):
            self.assertNotIn(secret,encoded)
        evaluation=result['candidates'][0]['historicalEvaluation']
        self.assertEqual(evaluation['decision']['regressionCount'],1)
        self.assertTrue(evaluation['freshValidationRequiredForSelection'])
        (folder/'adapters.safetensors').write_bytes(b'corrupt')
        changed=model_history.public_history(self.root)['candidates'][0]
        self.assertEqual(changed['state'],'invalid-receipt')
        self.assertIsNone(changed['historicalEvaluation'])

    def test_restored_history_is_untrusted_allowlisted_input(self):
        folder,receipt=self.candidate('first')
        with self.memory._db() as db:
            db.execute('INSERT INTO learning_model_history(report_sha,weights_sha,payload,recorded) VALUES(?,?,?,?)',
                ('a'*64,receipt['adapterSha256'],json.dumps({'private':'session-token',
                 'weightsSha256':receipt['adapterSha256'],'reportSha256':'a'*64,'recordedAt':float('nan'),
                 'decision':{'reason':'private-address','accepted':'yes','candidatePassed':'/home/private'}}),1))
        evaluation=model_history.public_history(self.root)['candidates'][0]['historicalEvaluation']
        self.assertEqual(evaluation['reportSha256'],'a'*64)
        self.assertIsNone(evaluation['recordedAt'])
        self.assertFalse(evaluation['decision']['accepted'])
        self.assertNotIn('private',json.dumps(evaluation))

    def test_foreign_weights_or_report_hash_cannot_be_attached_via_database_column(self):
        folder,receipt=self.candidate('first')
        for forged in ({'weightsSha256':'b'*64,'reportSha256':'a'*64},
                       {'weightsSha256':receipt['adapterSha256'],'reportSha256':'c'*64}):
            with self.memory._db() as db:
                db.execute('DELETE FROM learning_model_history')
                db.execute('INSERT INTO learning_model_history(report_sha,weights_sha,payload,recorded) VALUES(?,?,?,?)',
                    ('a'*64,receipt['adapterSha256'],json.dumps({**forged,'decision':{'accepted':True,'reason':'accepted'}}),1))
            self.assertIsNone(model_history.public_history(self.root)['candidates'][0]['historicalEvaluation'])

    def test_symlinked_model_and_receipt_are_not_read(self):
        folder,_=self.candidate('first')
        artifact=folder/'adapters.safetensors';artifact.unlink()
        secret=self.root/'secret';secret.write_bytes(b'secret')
        artifact.symlink_to(secret)
        value=model_history.public_history(self.root)
        self.assertEqual(value['candidates'][0]['state'],'invalid-receipt')
        self.assertNotIn(hashlib.sha256(b'secret').hexdigest(),json.dumps(value))

    def test_candidate_lease_exposes_actual_activity_even_before_first_checkpoint(self):
        from training_job_state import candidate_lease
        folder=self.root/'tools/text-ai/runs/loading'
        with candidate_lease(folder):
            value=model_history.public_history(self.root)
            self.assertTrue(value['activeTraining'])
            self.assertEqual(value['candidates'][0]['state'],'training')
            self.assertFalse(value['candidates'][0]['receiptMatchesWeights'])
            with self.assertRaises(ValueError):
                with candidate_lease(folder):pass
        self.assertFalse(model_history.public_history(self.root)['activeTraining'])


if __name__=='__main__':unittest.main()
