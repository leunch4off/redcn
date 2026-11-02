#!/usr/bin/env python3
"""Audit explicit authored/private corpora without changing them or training."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
from text_corpus_audit import audit_corpora

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',action='append',required=True)
    parser.add_argument('--heldout',action='append',default=[])
    parser.add_argument('--output',required=True)
    args=parser.parse_args();target=Path(args.output)
    if target.exists() or target.is_symlink():raise ValueError('Use a new output file')
    result=audit_corpora(args.dataset,heldouts=args.heldout)
    target.parent.mkdir(parents=True,exist_ok=True)
    with target.open('x') as stream:json.dump(result,stream,ensure_ascii=False,indent=2);stream.write('\n')
    target.chmod(0o600)
    print(json.dumps({k:v for k,v in result.items() if k not in ('nearDuplicates','exactLeaks')},ensure_ascii=False))
    raise SystemExit(0 if result['ready'] else 2)
