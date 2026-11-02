"""No operator library, credentials, photos or raw pages are accessed."""
import copy
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
import portability_proof as proof

class PortabilityProofTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):cls.content,cls.receipt=proof.generate_fixture()
 def test_complete_synthetic_workspace_roundtrip_checks_relationships_and_exact_bytes(self):
  result=proof.verify_fixture(self.content,self.receipt)
  self.assertTrue(result['verified']);self.assertEqual((result['cards'],result['attachments'],result['versions'],result['photos']),(2,3,2,1))
  self.assertIn('original-and-cropped-photo-bytes',result['verifiedComponents'])
  self.assertIn('approved-dictionary',result['verifiedComponents'])
  self.assertTrue(result['devicesMustPairAgain']);self.assertTrue(result['clientMaterialsRequireRegeneration'])
 def test_replaced_source_archive_and_metadata_are_rejected(self):
  with self.assertRaisesRegex(ValueError,'hash/size'):proof.verify_fixture(self.content+b'x',self.receipt)
  fake=copy.deepcopy(self.receipt);fake['synthetic']=False
  with self.assertRaisesRegex(ValueError,'generated public'):proof.verify_fixture(self.content,fake)
 def test_semantic_corruption_is_detected_after_valid_archive_inspection(self):
  fake=copy.deepcopy(self.receipt);fake['expected']['originalSha256']='0'*64
  with self.assertRaisesRegex(ValueError,'differs'):proof.verify_fixture(self.content,fake)
 def test_original_v26_mac_archive_defaults_only_its_absent_withdrawn_counter(self):
  fixture=Path(__file__).parent/'fixtures/macos-portability'
  content=(fixture/'workspace.zip').read_bytes();receipt=json.loads((fixture/'source.json').read_text())
  before=copy.deepcopy(receipt)
  self.assertNotIn('withdrawn',receipt['expected']['learning'])
  self.assertTrue(proof.verify_fixture(content,receipt)['verified'])
  self.assertEqual(receipt,before,'Compatibility must not rewrite the historical source receipt')
  for field,value in [('approved',2),('withdrawn',1)]:
   changed=copy.deepcopy(receipt);changed['expected']['learning'][field]=value
   with self.subTest(field=field),self.assertRaisesRegex(ValueError,'differs'):
    proof.verify_fixture(content,changed)
 def test_new_learning_transfer_counts_cannot_be_hidden_by_legacy_compatibility(self):
  content,receipt=proof.generate_fixture(with_learning=True)
  self.assertEqual(receipt['expected']['learning']['withdrawn'],1)
  self.assertTrue(proof.verify_fixture(content,receipt)['verified'])
  for legacy_marker in (False,True):
   changed=copy.deepcopy(receipt);del changed['expected']['learning']['withdrawn']
   if legacy_marker:changed.pop('learning')
   with self.subTest(legacy_marker=legacy_marker),self.assertRaisesRegex(ValueError,'differs'):
    proof.verify_fixture(content,changed)
 def test_linux_run_cannot_claim_mac_to_mac_or_operator_export(self):
  with patch.object(proof.platform,'system',return_value='Linux'):
   with self.assertRaisesRegex(ValueError,'macOS'):proof.generate_fixture(require_macos=True,origin='operator-mac')
   with patch.dict(os.environ,{'GITHUB_ACTIONS':'true','RUNNER_OS':'macOS','RUNNER_ENVIRONMENT':'github-hosted'}):
    with self.assertRaisesRegex(ValueError,'real GitHub-hosted'):proof.verify_fixture(self.content,self.receipt,require_github_macos=True)
 def test_github_mac_cannot_impersonate_the_separate_operator_source(self):
  with patch.object(proof.platform,'system',return_value='Darwin'),patch.dict(os.environ,{'GITHUB_ACTIONS':'true'}):
   with self.assertRaisesRegex(ValueError,'non-GitHub'):proof.generate_fixture(require_macos=True,origin='operator-mac')
 def test_fresh_macos_verifier_requires_a_source_exported_on_operator_mac(self):
  with patch.object(proof.platform,'system',return_value='Darwin'),patch.dict(os.environ,{'GITHUB_ACTIONS':'true','RUNNER_OS':'macOS','RUNNER_ENVIRONMENT':'github-hosted'}):
   with self.assertRaisesRegex(ValueError,'separately exported'):proof.verify_fixture(self.content,self.receipt,require_github_macos=True)
 def test_self_hosted_mac_cannot_be_reported_as_a_separate_github_hosted_mac(self):
  receipt=copy.deepcopy(self.receipt);receipt['source'].update(system='Darwin',origin='operator-mac')
  with patch.object(proof.platform,'system',return_value='Darwin'),patch.dict(os.environ,{'GITHUB_ACTIONS':'true','RUNNER_OS':'macOS','RUNNER_ENVIRONMENT':'self-hosted'}):
   with self.assertRaisesRegex(ValueError,'real GitHub-hosted'):proof.verify_fixture(self.content,receipt,require_github_macos=True)
   self.assertFalse(proof.verify_fixture(self.content,receipt)['destination']['githubHosted'])
if __name__=='__main__':unittest.main()
