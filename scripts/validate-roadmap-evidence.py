#!/usr/bin/env python3
"""Validate exact roadmap statuses and evidence; do not rewrite the plan.

By default legacy prose-only acceptance is reported as a gap, not promoted to
proof. --strict additionally fails on every unverified done task or missing
site/issue comparison. The JSON always exposes each dimension separately.
"""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
from roadmap_evidence import audit

parser=argparse.ArgumentParser()
parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
parser.add_argument('--issue',type=Path,help='Saved GitHub issue JSON with body, or Markdown')
parser.add_argument('--receipts',type=Path)
parser.add_argument('--ci-receipt',type=Path)
parser.add_argument('--previous-plan',type=Path)
parser.add_argument('--output',type=Path)
parser.add_argument('--strict',action='store_true')
args=parser.parse_args();root=args.root.resolve()
issue=None
if args.issue:
    raw=args.issue.read_text(encoding='utf-8')
    issue=json.loads(raw) if args.issue.suffix=='.json' else raw
result=audit(root,roadmap=root/'ROADMAP.md' if (root/'ROADMAP.md').is_file() else None,
    site_plan=root/'docs/plan.json' if (root/'docs/plan.json').is_file() else None,issue=issue,
    receipts=args.receipts,ci_receipt=args.ci_receipt,previous_plan=args.previous_plan)
if args.output:
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({key:result[key] for key in ('commit','structuralOk','proofIntegrityOk','acceptanceCompletenessOk','summary','allRepresentationsChecked')},ensure_ascii=False))
okay=result['structuralOk'] and result['proofIntegrityOk']
if args.strict:okay=okay and result['acceptanceCompletenessOk'] and result['allRepresentationsChecked'] and result['ciReceipt']['matchesCommit']
sys.exit(0 if okay else 1)
