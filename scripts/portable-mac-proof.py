#!/usr/bin/env python3
"""Generate only public synthetic data, then verify a fresh Mac restore."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
from portability_proof import generate_fixture,verify_fixture

def main():
 p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='action',required=True)
 create=sub.add_parser('export');create.add_argument('--output',required=True);create.add_argument('--require-macos',action='store_true');create.add_argument('--origin',choices=('development-test','operator-mac'),default='development-test')
 create.add_argument('--with-learning',action='store_true')
 verify=sub.add_parser('verify');verify.add_argument('--fixture',required=True);verify.add_argument('--require-github-macos',action='store_true');verify.add_argument('--receipt-output')
 a=p.parse_args()
 if a.action=='export':
  root=Path(a.output)
  if root.exists():raise FileExistsError('Choose a new fixture directory')
  data,receipt=generate_fixture(require_macos=a.require_macos,origin=a.origin,with_learning=a.with_learning);root.mkdir(parents=True,mode=0o700)
  (root/'workspace.zip').write_bytes(data);(root/'source.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
  print(json.dumps({k:v for k,v in receipt.items() if k!='expected'},ensure_ascii=False))
 else:
  root=Path(a.fixture);result=verify_fixture((root/'workspace.zip').read_bytes(),json.loads((root/'source.json').read_text()),require_github_macos=a.require_github_macos)
  if a.receipt_output:Path(a.receipt_output).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
  print(json.dumps(result,ensure_ascii=False))
if __name__=='__main__':main()
