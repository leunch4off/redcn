import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
import roadmap_evidence as audit


class RoadmapEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        (self.root/'docs').mkdir();(self.root/'docs/proof.md').write_text('# Actual check\n\n## Result\nMeasured fixture.\n')
        self.task={'id':'RCN-1','title':'Test','acceptance':'Complete the measured flow','status':'done','priority':'P0','depends_on':[],'evidence':['docs/proof.md#result']}
        self.plan={'total':1,'tasks':[self.task]};self.commit='a'*40
        self.receipt={'taskId':'RCN-1','kind':'unit-test','origin':'synthetic','artifact':'docs/proof.md',
            'sha256':hashlib.sha256((self.root/'docs/proof.md').read_bytes()).hexdigest(),
            'criterionSha256':audit.criterion_sha(self.task),'commit':self.commit,'passed':True,'coversAcceptance':True}
    def tearDown(self):self.temp.cleanup()
    def run_audit(self,**kwargs):return audit.audit(self.root,plan=self.plan,commit=self.commit,**kwargs)

    def test_same_plan_site_issue_and_bound_receipt_pass_separate_dimensions(self):
        rendered='- [x] **RCN-1 · Test**'
        result=self.run_audit(roadmap=rendered,site_plan=self.plan,issue={'body':rendered},receipts={'version':1,'receipts':[self.receipt]},
            ci_receipt={'commit':self.commit,'conclusion':'success','runUrl':'https://github.com/owner/repository/actions/runs/123'})
        self.assertTrue(result['structuralOk']);self.assertTrue(result['proofIntegrityOk']);self.assertTrue(result['acceptanceCompletenessOk'])
        self.assertTrue(result['allRepresentationsChecked']);self.assertTrue(result['ciReceipt']['matchesCommit'])
        self.assertEqual(result['summary']['typedVerifiedDone'],1);self.assertFalse(result['roadmapChanged'])

    def test_prose_source_and_plan_never_become_completed_acceptance(self):
        result=self.run_audit();self.assertTrue(result['structuralOk'])
        self.assertFalse(result['acceptanceCompletenessOk']);self.assertEqual(result['summary']['typedVerifiedDone'],0)
        self.receipt['kind']='plan'
        result=self.run_audit(receipts={'version':1,'receipts':[self.receipt]})
        self.assertFalse(result['acceptanceCompletenessOk'])

    def test_missing_links_changed_criteria_and_wrong_commit_are_detected(self):
        self.task['evidence']=['docs/missing.md','docs/proof.md#removed']
        self.task['acceptance']='A different acceptance'
        self.receipt['commit']='b'*40
        result=self.run_audit(receipts={'version':1,'receipts':[self.receipt]})
        self.assertEqual({row['code'] for row in result['errors']},{'missing-evidence-file','missing-evidence-anchor'})
        self.assertIn('acceptance-changed',result['typedEvidence'][0]['problems'])
        self.assertIn('different-or-unknown-commit',result['typedEvidence'][0]['problems'])

    def test_duplicate_cycle_deleted_id_and_rendered_status_disagreement(self):
        second={**self.task,'id':'RCN-2','depends_on':['RCN-1']}
        self.task['depends_on']=['RCN-2'];self.plan.update(total=3,tasks=[self.task,second,dict(second)])
        result=self.run_audit(roadmap='- [ ] RCN-1\n- [x] RCN-2\n- [x] RCN-2',previous_plan={'tasks':[{'id':'RCN-3'}]})
        codes={row['code'] for row in result['errors']}
        self.assertTrue({'duplicate-id','dependency-cycle','removed-existing-id','rendered-status-mismatch','duplicate-rendered-id'}<=codes)

    def test_synthetic_physical_proof_and_short_soak_cannot_close_acceptance(self):
        self.task['id']='RCN-356';self.receipt.update(taskId='RCN-356',criterionSha256=audit.criterion_sha(self.task),kind='native-runtime',actualDurationSeconds=120)
        result=self.run_audit(receipts={'version':1,'receipts':[self.receipt]})
        self.assertIn('soak-shorter-than-two-hours',result['typedEvidence'][0]['problems'])
        self.receipt.update(kind='physical-sleep-wake',actualDurationSeconds=7200)
        result=self.run_audit(receipts={'version':1,'receipts':[self.receipt]})
        self.assertIn('synthetic-is-not-physical-evidence',result['typedEvidence'][0]['problems'])
        self.assertEqual(result['summary']['typedVerifiedDone'],0)


if __name__=='__main__':unittest.main()
