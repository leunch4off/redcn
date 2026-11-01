import copy
import io
import contextlib
import importlib.util
import json
import os
import subprocess
import tempfile
from pathlib import Path
import sys
import tarfile
import unittest
from unittest.mock import patch
import zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
import release_evidence as e
SHA='a'*40
HOSTING=json.dumps({'project_id':e.PROJECT,'d1':None,'r2':'BUCKET'}).encode()
WORKER=b'export default {fetch(){return new Response("fixture")}};'
TREE=e.git_tree({'.openai/hosting.json':HOSTING,'worker/index.js':WORKER})

def tar(files):
 s=io.BytesIO()
 with tarfile.open(fileobj=s,mode='w:gz') as z:
  for name,data in files.items():
   r=tarfile.TarInfo(name);r.size=len(data);z.addfile(r,io.BytesIO(data))
 return s.getvalue()
def payload(*,extra_source=None,build_mutation=False):
 hosting=HOSTING;worker=WORKER
 source={'.openai/hosting.json':hosting,'worker/index.js':worker,**(extra_source or {})}
 build={'.openai/hosting.json':hosting,'dist/.openai/hosting.json':hosting,'dist/server/index.js':worker+b'bad' if build_mutation else worker}
 files={'source.tar.gz':tar(source),'build.tar.gz':tar(build)}
 manifest={'version':1,'type':'redcn-sites-release','repository':e.REPO,'projectId':e.PROJECT,'sourceSha':SHA,'sourceTree':TREE,'ciRunId':123,'localMacVerificationRequired':True,'files':{k:{'sha256':e.digest(v),'bytes':len(v)} for k,v in files.items()}}
 files['release.json']=json.dumps(manifest).encode();s=io.BytesIO()
 with zipfile.ZipFile(s,'w') as z:
  for k,v in files.items():z.writestr(k,v)
 return s.getvalue()
def evidence(data):
 ci={'id':123,'status':'completed','conclusion':'success','head_sha':SHA,'head_branch':e.BRANCH,'event':'pull_request','repository':{'full_name':e.REPO},'head_repository':{'full_name':e.REPO},'path':'.github/workflows/verification.yml'}
 jobs=[{'name':n,'status':'completed','conclusion':'success','run_id':123} for n in e.REQUIRED_JOBS]
 artifact={'id':456,'expired':False,'name':'redcn-sites-'+SHA,'workflow_run':{'id':123,'head_sha':SHA},'digest':'sha256:'+e.digest(data),'size_in_bytes':len(data)}
 proof={'version':1,'projectId':e.PROJECT,'repository':e.REPO,'branch':e.BRANCH,'sourceSha':SHA,'sourceTree':TREE,'ciRunId':123,'macProofSha256':'c'*64,'mac':{'system':'Darwin','gitClean':True,'activeJobs':0,'localPhonePassed':True,'pageErrors':0},'verifiedAt':'2026-11-01T18:00:00Z'}
 comment={'id':789,'user':{'login':e.ACTOR},'issue_url':e.ATTESTATION_ISSUE_URL,'body':e.ATTESTATION_PREFIX+json.dumps(proof)}
 return {'ci':ci,'commit':{'sha':SHA,'tree':{'sha':TREE}},'jobs':jobs,'artifact':artifact,'comment':comment,'source_sha':SHA,'source_tree':TREE}
class ReleaseEvidenceTests(unittest.TestCase):
 def test_exact_verified_ci_local_mac_owner_attestation_and_artifact_bind_together(self):
  data=payload();result=e.verify_authorized_artifact(data,**evidence(data));self.assertTrue(result['authorization']['localMacFirst']);self.assertEqual(result['authorization']['publishAudience'],'owner-private')
 def test_no_local_install_owner_comment_means_no_cloud_publication(self):
  data=payload();base=evidence(data)
  for change in ('missing','other-actor','wrong-sha','dirty'):
   v=copy.deepcopy(base)
   if change=='missing':v['comment']={}
   elif change=='other-actor':v['comment']['user']['login']='someone-else'
   else:
    p=json.loads(v['comment']['body'][len(e.ATTESTATION_PREFIX):])
    if change=='wrong-sha':p['sourceSha']='d'*40
    else:p['mac']['gitClean']=False
    v['comment']['body']=e.ATTESTATION_PREFIX+json.dumps(p)
   with self.subTest(change=change),self.assertRaises(ValueError):e.verify_authorized_artifact(data,**v)
 def test_fork_wrong_workflow_pending_job_expiry_and_unbound_digest_reject(self):
  data=payload();base=evidence(data)
  changes=[lambda v:v['commit']['tree'].update(sha='0'*40),lambda v:v['ci']['head_repository'].update(full_name='other/redcn'),lambda v:v['ci'].update(path='.github/workflows/other.yml'),lambda v:v['jobs'][0].update(conclusion='failure'),lambda v:v['artifact'].update(expired=True),lambda v:v['artifact'].update(digest='sha256:'+'0'*64)]
  for change in changes:
   v=copy.deepcopy(base);change(v)
   with self.assertRaises(ValueError):e.verify_authorized_artifact(data,**v)
 def test_successful_jobs_from_a_different_run_cannot_authorize_this_artifact(self):
  data=payload();base=evidence(data)
  for run in (None,True,124):
   value=copy.deepcopy(base);value['jobs'][0]['run_id']=run
   with self.subTest(run=run),self.assertRaisesRegex(ValueError,'jobs must succeed'):
    e.verify_authorized_artifact(data,**value)
 def test_owner_comment_must_belong_to_the_configured_repository_and_pull_request(self):
  data=payload();base=evidence(data)
  for issue in (None,'https://api.github.com/repos/leunch4off/redcn/issues/1','https://api.github.com/repos/other/redcn/issues/6'):
   value=copy.deepcopy(base);value['comment']['issue_url']=issue
   with self.subTest(issue=issue),self.assertRaisesRegex(ValueError,'PR #6'):
    e.verify_authorized_artifact(data,**value)
 def test_artifact_size_is_bound_to_the_downloaded_zip(self):
  data=payload();value=evidence(data);value['artifact']['size_in_bytes']+=1
  with self.assertRaisesRegex(ValueError,'size mismatch'):e.verify_authorized_artifact(data,**value)
 def test_source_archive_credentials_and_changed_built_worker_reject(self):
  for data in [payload(extra_source={'phone-access.json':b'not-a-real-secret'}),payload(extra_source={'../outside':b'bad'}),payload(build_mutation=True)]:
   with self.assertRaises(ValueError):e.verify_authorized_artifact(data,**evidence(data))
 def test_git_tree_matches_git_itself_and_preserves_executable_bits(self):
  with tempfile.TemporaryDirectory() as root:
   path=Path(root);(path/'scripts').mkdir();(path/'README').write_bytes(b'generated');(path/'scripts/run.sh').write_bytes(b'#!/bin/sh\nexit 0\n');(path/'scripts/run.sh').chmod(0o755)
   subprocess.run(['git','init','-q',root],check=True);subprocess.run(['git','add','.'],cwd=root,check=True)
   expected=subprocess.check_output(['git','write-tree'],cwd=root,text=True).strip()
   files={'README':b'generated','scripts/run.sh':b'#!/bin/sh\nexit 0\n'}
   self.assertEqual(e.git_tree(files,{'scripts/run.sh':0o755}),expected);self.assertNotEqual(e.git_tree(files),expected)
 def test_actual_packager_requires_a_clean_exact_checkout_and_binds_its_build(self):
  script=Path(__file__).resolve().parents[1]/'scripts/sites-release-artifact.py'
  spec=importlib.util.spec_from_file_location('sites_release_packager_test',script);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp)/'source';root.mkdir();(root/'.openai').mkdir();(root/'worker').mkdir();(root/'dist/server').mkdir(parents=True);(root/'dist/.openai').mkdir()
   (root/'.gitignore').write_text('dist/\n');(root/'.openai/hosting.json').write_bytes(HOSTING);(root/'worker/index.js').write_bytes(WORKER);(root/'dist/.openai/hosting.json').write_bytes(HOSTING);(root/'dist/server/index.js').write_bytes(WORKER)
   subprocess.run(['git','init','-q',str(root)],check=True);subprocess.run(['git','add','.'],cwd=root,check=True);subprocess.run(['git','-c','user.name=Fixture','-c','user.email=fixture@example.invalid','-c','commit.gpgsign=false','commit','-qm','generated fixture'],cwd=root,check=True)
   sha=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip();out=Path(temp)/'release'
   args=['pack','--source-sha',sha,'--run-id','123','--output',str(out)]
   with patch.object(module,'ROOT',root),patch.object(sys,'argv',args),contextlib.redirect_stdout(io.StringIO()):module.main()
   files={name:(out/name).read_bytes() for name in ('release.json','source.tar.gz','build.tar.gz')}
   self.assertEqual(e.validate_release_files(files,sha)[0]['sourceSha'],sha)
   (root/'worker/index.js').write_bytes(WORKER+b'changed')
   with patch.object(module,'ROOT',root),patch.object(sys,'argv',[*args[:-1],str(Path(temp)/'second')]),self.assertRaisesRegex(ValueError,'clean tested'):module.main()
 def test_source_tar_symlinks_are_not_extracted(self):
  stream=io.BytesIO()
  with tarfile.open(fileobj=stream,mode='w:gz') as z:
   r=tarfile.TarInfo('link');r.type=tarfile.SYMTYPE;r.linkname='/etc/passwd';z.addfile(r)
  with self.assertRaises(ValueError):e.read_tar(stream.getvalue())
if __name__=='__main__':unittest.main()
