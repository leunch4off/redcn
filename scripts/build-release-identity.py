#!/usr/bin/env python3
"""Run before build.mjs; inject returned buildId into both shell meta tags."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
from release_diagnostics import write_build_identity

parser=argparse.ArgumentParser()
parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
args=parser.parse_args()
result=write_build_identity(args.root)
print(json.dumps({'buildId':result['buildId'],'components':result['components']},sort_keys=True))
