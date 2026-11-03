#!/usr/bin/env python3
"""Measure explicit expected decisions without rewriting the control cases."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'cli'))
from text_evaluation_audit import validator_audit

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases',default=str(ROOT/'data/quality/text-validator-v29.json'))
    parser.add_argument('--output',required=True)
    args=parser.parse_args();target=Path(args.output)
    if target.exists() or target.is_symlink():raise ValueError('New output required')
    result=validator_audit(args.cases)
    target.parent.mkdir(parents=True,exist_ok=True)
    with target.open('x') as stream:json.dump(result,stream,ensure_ascii=False,indent=2);stream.write('\n')
    target.chmod(0o600)
    print(json.dumps({key:value for key,value in result.items() if key!='results'},ensure_ascii=False))
    raise SystemExit(0 if result['allExpectedDecisionsMatched'] else 2)
