#!/usr/bin/env python3
"""Package exact tracked, tested source and build; never copies a runtime cache."""
import argparse
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
from release_evidence import PROJECT,REPO,digest,validate_release_files
ROOT=Path(__file__).resolve().parents[1]

def git(*args):return subprocess.check_output(['git',*args],cwd=ROOT)
def packed(entries):
 stream=io.BytesIO()
 with tarfile.open(fileobj=stream,mode='w:gz') as archive:
  for name,value in entries.items():
   item=tarfile.TarInfo(name);item.size=len(value);item.mode=0o644;item.mtime=0;archive.addfile(item,io.BytesIO(value))
 return stream.getvalue()
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',required=True);p.add_argument('--source-sha',required=True);p.add_argument('--run-id',required=True,type=int);a=p.parse_args()
 sha=git('rev-parse','HEAD').decode().strip();tree=git('rev-parse','HEAD^{tree}').decode().strip()
 if sha!=a.source_sha or git('status','--porcelain').strip():raise ValueError('Release requires the exact clean tested checkout')
 if a.run_id<1:raise ValueError('Missing CI run identity')
 source=git('archive','--format=tar.gz','HEAD')
 build=packed({'.openai/hosting.json':(ROOT/'.openai/hosting.json').read_bytes(),
   'dist/.openai/hosting.json':(ROOT/'dist/.openai/hosting.json').read_bytes(),'dist/server/index.js':(ROOT/'dist/server/index.js').read_bytes()})
 manifest={'version':1,'type':'redcn-sites-release','repository':REPO,'projectId':PROJECT,'sourceSha':sha,'sourceTree':tree,'ciRunId':a.run_id,
  'files':{name:{'sha256':digest(data),'bytes':len(data)} for name,data in [('source.tar.gz',source),('build.tar.gz',build)]},'localMacVerificationRequired':True}
 files={'source.tar.gz':source,'build.tar.gz':build,'release.json':json.dumps(manifest,sort_keys=True).encode()}
 validate_release_files(files,sha,tree)
 out=Path(a.output)
 if out.exists():raise FileExistsError('Release output already exists')
 out.mkdir(parents=True)
 for name,value in files.items():(out/name).write_bytes(value)
 print(json.dumps(manifest))
if __name__=='__main__':main()
