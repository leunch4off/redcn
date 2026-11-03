#!/usr/bin/env python3
"""Print non-secret release attestation after actual local Mac verification.

The operator/root posts this as their own GitHub PR comment. The cloud task
must independently check that authenticated GitHub comment creator identity.
This does not post, fetch a source credential, or publish anything itself.
"""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys
import time
import urllib.request
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
from release_evidence import ATTESTATION_PREFIX,PROJECT,REPO,BRANCH,SHA40

def read(path):
 p=Path(path)
 if p.is_symlink() or not p.is_file() or not 0<p.stat().st_size<200000:raise ValueError('Use a bounded verification receipt')
 data=p.read_bytes();return data,json.loads(data)
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--repository-root',required=True);p.add_argument('--source-sha',required=True);p.add_argument('--source-tree',required=True);p.add_argument('--run-id',type=int,required=True);p.add_argument('--deployment-receipt',required=True);p.add_argument('--phone-receipt',required=True);a=p.parse_args()
 if platform.system()!='Darwin' or not SHA40.fullmatch(a.source_sha) or not SHA40.fullmatch(a.source_tree) or a.run_id<1:raise ValueError('Actual Mac and exact commit/tree/run are required')
 def git(*args):return subprocess.check_output(['git',*args],cwd=a.repository_root,text=True).strip()
 if git('rev-parse','HEAD')!=a.source_sha or git('rev-parse','HEAD^{tree}')!=a.source_tree or git('status','--porcelain'):raise ValueError('The installed repository is not this clean verified source')
 dep_raw,dep=read(a.deployment_receipt);phone_raw,phone=read(a.phone_receipt)
 if dep.get('commit')!=a.source_sha or dep.get('tree')!=a.source_tree or dep.get('ciRun')!=a.run_id or dep.get('activeJobs')!=0 or type(dep.get('serverPIDAfter'))is not int or dep.get('serverPIDBefore')==dep.get('serverPIDAfter'):raise ValueError('Deployment receipt does not confirm this local restart/source')
 if phone.get('commit')!=a.source_sha or phone.get('localActualServer')is not True or phone.get('pageErrors')!=[] or phone.get('result',{}).get('loadResult')!='code':raise ValueError('The actual local phone browser check has not passed for this source')
 health=json.load(urllib.request.urlopen('http://127.0.0.1:8765/api/local/health',timeout=5))
 if health.get('release')!=dep.get('release') or health.get('activeJobs')!=0:raise ValueError('Current local server state does not match the installed release')
 value={'version':1,'projectId':PROJECT,'repository':REPO,'branch':BRANCH,'sourceSha':a.source_sha,'sourceTree':a.source_tree,'ciRunId':a.run_id,
  'macProofSha256':hashlib.sha256(dep_raw+b'\n'+phone_raw).hexdigest(),'mac':{'system':'Darwin','gitClean':True,'activeJobs':0,'localPhonePassed':True,'pageErrors':0},'verifiedAt':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())}
 print(ATTESTATION_PREFIX+json.dumps(value,sort_keys=True))
if __name__=='__main__':main()
