#!/usr/bin/env python3
"""Expose a completed diagnostic teacher candidate in local model history.

Append-only candidate copying and history recording never select weights. The
24 short translation probes do not replace the full production quality gate.
"""
import argparse
import json
from pathlib import Path
import re
import shutil
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'cli'))
from text_model_contract import file_sha
from model_history import record_evaluation
from training_job_state import activity


def register(cache, workspace):
    cache,workspace=Path(cache).resolve(),Path(workspace).resolve()
    report_path=workspace/'experiment.json'
    report=json.loads(report_path.read_text())
    source=workspace/'candidate'
    receipt=json.loads((source/'trainreceipt.json').read_text())
    weights=file_sha(source/'adapters.safetensors')
    if (report.get('phase')!='complete-unaccepted' or report.get('qualityAccepted') is not False
            or receipt.get('status')!='trained-unaccepted' or receipt.get('stepsCompleted')!=240
            or receipt.get('stoppedEarly') is not False or report.get('adapterSha256')!=weights
            or receipt.get('adapterSha256')!=weights or activity(source)['active']
            or report.get('datasetSha256')!=receipt.get('datasetSha256')):
        raise ValueError('Incomplete or inconsistent teacher candidate')
    before=json.loads((workspace/'baseline.json').read_text())
    after=json.loads((workspace/'candidate-evaluation.json').read_text())
    expected=json.loads((ROOT/'data/quality/teacher-rental-heldout-v30.json').read_text())['cases']
    ids={row['id'] for row in expected}
    for measured in (before,after):
        if (measured.get('heldoutSha256')!=report.get('heldoutSha256')
                or len(measured.get('rows',[]))!=len(ids)
                or {row['id'] for row in measured['rows']}!=ids):
            raise ValueError('Diagnostic probes are incomplete or from different questions')
    if after.get('adapterSha256')!=weights:
        raise ValueError('Candidate diagnostic evaluated other weights')
    name=workspace.name+'-candidate'
    if not re.fullmatch('[a-zA-Z0-9-]{1,100}',name):
        raise ValueError('Use a bounded candidate directory name')
    destination=cache/'tools/text-ai/runs'/name
    files=('adapters.safetensors','adapter_config.json','optimizer.safetensors','initial.safetensors','trainreceipt.json')
    if destination.exists():
        if any(file_sha(destination/name)!=file_sha(source/name) for name in files):
            raise ValueError('Candidate history location already has different files')
    else:
        destination.mkdir(mode=0o700,parents=True)
        for name in files:
            path=source/name
            if path.is_symlink() or not path.is_file():raise ValueError('Candidate must contain regular artifacts')
            shutil.copyfile(path,destination/name)
            (destination/name).chmod(0o600)
    passed=lambda measured:sum(row.get('status')=='measured' and row.get('automaticFactsPassed') is True for row in measured['rows'])
    current_passed,candidate_passed=passed(before),passed(after)
    decision={'accepted':False,'reason':'candidate-factual-errors' if candidate_passed<len(ids) else 'quality-blocked',
        'requested':len(ids),'currentPassed':current_passed,'candidatePassed':candidate_passed,
        'regressions':[row['id'] for row in after['rows'] if not row.get('automaticFactsPassed') and next(old for old in before['rows'] if old['id']==row['id']).get('automaticFactsPassed')],
        'candidateFailures':[row['id'] for row in after['rows'] if row.get('status')!='measured' or not row.get('automaticFactsPassed')]}
    history=record_evaluation(cache,{'finishedAt':report['finishedAt'],'inputs':{
        'candidate':{'weightsSha256':weights},'datasetSha256':report['datasetSha256'],
        'casesSha256':report['heldoutSha256'],'checkers':{'text_quality.py':file_sha(ROOT/'cli/text_quality.py')}}},
        decision,receipt,report_sha=file_sha(report_path),scope='diagnostic-only')
    return {'candidateDirectory':str(destination),'weightsSha256':weights,'stepsCompleted':240,
        'history':history,'selectedWeightsChanged':False,'qualityAccepted':False}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache',required=True,type=Path)
    parser.add_argument('--workspace',required=True,type=Path)
    args=parser.parse_args()
    print(json.dumps(register(args.cache,args.workspace),ensure_ascii=False))
