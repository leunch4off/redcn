#!/usr/bin/env python3
"""Run real-time mixed workload in a new isolated directory; never fake elapsed time."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
from project_soak import atomic, canonical, memory_snapshot, read_object, run_phase, summary, validate_recipe


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--recipe',required=True,type=Path)
    parser.add_argument('--workspace',required=True,type=Path)
    parser.add_argument('--production-cache',required=True,type=Path)
    parser.add_argument('--seconds',type=float,default=7200)
    parser.add_argument('--interval-seconds',type=float,default=15)
    parser.add_argument('--rss-limit-bytes',type=int,default=3_000_000_000)
    args=parser.parse_args()
    if not 1 <= args.seconds <= 14400 or not 0 <= args.interval_seconds <= 60 or not 256_000_000 <= args.rss_limit_bytes <= 6_000_000_000:
        parser.error('Bounded duration, interval and RSS limit required')
    source=Path(__file__).resolve().parents[1]
    workspace=args.workspace.absolute();production=args.production_cache.resolve()
    if workspace.exists() or workspace.is_symlink() or workspace==production or production in workspace.parents or workspace in production.parents:
        parser.error('Workspace must be a new directory outside the production cache')
    os.umask(0o077);workspace.mkdir(parents=False,mode=0o700)
    recipe=validate_recipe(read_object(args.recipe),workspace,source)
    recipe_sha=hashlib.sha256(canonical(recipe).encode()).hexdigest()
    try:source_sha=subprocess.check_output(['git','rev-parse','HEAD'],cwd=source,text=True,timeout=5).strip()
    except (OSError,subprocess.SubprocessError):source_sha='unversioned'
    source_files={str(p.relative_to(source)):hashlib.sha256(p.read_bytes()).hexdigest() for folder in ('cli','scripts','web','docs','data/learning') for p in (source/folder).rglob('*') if p.is_file() and not p.is_symlink() and p.suffix in ('.py','.mjs','.js','.html','.json')}
    atomic(workspace/'protocol.json',{'version':1,'sourceSha':source_sha,'sourceFiles':source_files,'recipeSha256':recipe_sha,'recipe':recipe,'requestedSeconds':args.seconds,'rssLimitBytes':args.rss_limit_bytes,'physicalSleepWake':'not-requested'})
    cancelled=[False]
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,lambda *_:cancelled.__setitem__(0,True))
    rows=[];memory=[{'elapsedSeconds':0,**memory_snapshot()}];start=time.monotonic();deadline=start+args.seconds;iteration=0;index=0
    try:
        while not cancelled[0] and time.monotonic()<deadline:
            row=run_phase(recipe['phases'][index],workspace,source,production,iteration,lambda:cancelled[0],deadline,args.rss_limit_bytes)
            rows.append(row)
            with (workspace/'events.jsonl').open('a') as out:out.write(canonical(row)+'\n')
            if len(rows)>20000:raise ValueError('Event limit reached')
            elapsed=time.monotonic()-start
            if elapsed-memory[-1]['elapsedSeconds']>=60:memory.append({'elapsedSeconds':round(elapsed,3),**memory_snapshot()})
            atomic(workspace/'summary.json',summary(rows,elapsed,args.seconds,cancelled=cancelled[0],memory=memory,source_sha=source_sha,recipe_sha=recipe_sha))
            print(canonical({'phase':row['phase'],'state':row['state'],'seconds':row['seconds'],'elapsedSeconds':round(elapsed,3)}),flush=True)
            index=(index+1)%len(recipe['phases']);iteration+=1
            until=min(deadline,time.monotonic()+args.interval_seconds)
            while not cancelled[0] and time.monotonic()<until:time.sleep(min(.2,until-time.monotonic()))
    finally:
        elapsed=time.monotonic()-start;result=summary(rows,elapsed,args.seconds,cancelled=cancelled[0],memory=memory,source_sha=source_sha,recipe_sha=recipe_sha)
        result['sourceFilesUnchanged']=all(p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest()==digest for name,digest in source_files.items() for p in [source/name])
        result['finished']=True;atomic(workspace/'summary.json',result);print(canonical(result),flush=True)
    return 0 if not cancelled[0] and result['sourceFilesUnchanged'] and result['acceptance']['requestedDurationCompleted'] and result['acceptance']['allFourRealPhases'] and result['acceptance']['noPhaseErrors'] else 1


if __name__=='__main__':raise SystemExit(main())
