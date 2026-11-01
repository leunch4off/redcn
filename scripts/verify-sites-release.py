#!/usr/bin/env python3
"""Verify connector evidence and safely prepare exact source/build for Sites.

This has no network, Git push, credentials or publication operation. A cloud
runner must fetch fresh API evidence and preserve owner-private access first.
"""
import argparse
import json
import os
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
from release_evidence import read_tar,verify_authorized_artifact

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--evidence',required=True);p.add_argument('--artifact',required=True);p.add_argument('--output',required=True);a=p.parse_args()
 evidence=json.loads(Path(a.evidence).read_text());data=Path(a.artifact).read_bytes();checked=verify_authorized_artifact(data,**evidence)
 root=Path(a.output)
 if root.exists() or root.is_symlink():raise FileExistsError('Choose a new checked release folder')
 root.mkdir(mode=0o700,parents=True);source=root/'source';source.mkdir()
 _,modes=read_tar(checked['files']['source.tar.gz'],with_modes=True)
 for name,content in checked['source'].items():
  path=source/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(content);path.chmod(modes[name])
 (root/'site-deploy.tar.gz').write_bytes(checked['files']['build.tar.gz'])
 (root/'authorization.json').write_text(json.dumps(checked['authorization'],indent=2)+'\n')
 print(json.dumps({'verified':True,**checked['authorization'],'sourceDirectory':str(source),'archive':str(root/'site-deploy.tar.gz')}))
if __name__=='__main__':main()
