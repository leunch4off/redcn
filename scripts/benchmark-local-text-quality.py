"""Paired public factual/format checks for local text candidates and Argos.

No downloads, private listings, state changes, training or promotion. All
versions receive the same frozen prompts; Argos supports only RU→ZH so its
comparable denominator is explicitly seven probes, not all eighteen.
"""
import argparse
import json
from pathlib import Path
import statistics
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'cli'))
from text_model_contract import content_sha, file_sha, inference_model
from text_quality import parse_card, translation_checks
from text_quality_probes import probes
from local_content import validate_draft


def rss_measurements():
    import resource
    scale=1 if sys.platform=='darwin' else 1024
    return {'method':'getrusage.ru_maxrss','platform':sys.platform,
        'nativeUnit':'bytes' if sys.platform=='darwin' else 'KiB',
        'selfPeakRssBytes':int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)*scale,
        'childrenPeakRssBytes':int(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)*scale,
        'simultaneousProcessTreePeakMeasured':False}


def assess(probe,text):
    if probe['task'] != 'listing':
        return translation_checks(probe['source'],text,probe['task'])
    try:
        draft=parse_card(text)
        issues=validate_draft(draft,{'titles':[probe['reference']],'body':probe['reference']},probe['fields'])
        return {'passed':not issues,'issues':issues,'jsonShapeCorrect':True,'automaticSemanticVerification':False}
    except (ValueError,TypeError):
        return {'passed':False,'issues':['Не получен корректный JSON title/body'],
            'jsonShapeCorrect':False,'automaticSemanticVerification':False}


def summary(rows):
    result={}
    for group in ('all','ru-zh','zh-ru','listing'):
        eligible=[row for row in rows if (group=='all' or row['task']==group) and row['status']!='unsupported']
        measured=[row for row in eligible if row['status']=='measured']
        durations=[row['seconds'] for row in measured]
        result[group]={'applicable':len(eligible),'measured':len(measured),
            'checksPassed':sum(row.get('checks',{}).get('passed') is True for row in measured),
            'unavailable':sum(row['status']=='unavailable' for row in eligible),
            'incomplete':sum(row['status']=='incomplete' for row in eligible),
            'medianSeconds':statistics.median(durations) if durations else None,
            'maxSeconds':max(durations) if durations else None}
    return result


def style_diagnostics(rows,selected):
    """Copying known facts may be safe, but does not prove original writing."""
    import re
    by_id={row['id']:row for row in selected};titles=[];copies=[]
    normalize=lambda value:re.sub(r'[^\w\u3400-\u9fff]','',value.casefold())
    for row in rows:
        if row['task']!='listing' or not isinstance(row.get('text'),str):continue
        try:draft=parse_card(row['text'])
        except ValueError:continue
        titles.append(normalize(draft['title']))
        body=normalize(draft['body']);reference=normalize(by_id[row['id']]['reference'])
        if len(body)>=10 and body in reference:copies.append(row['id'])
    return {'jsonCards':len(titles),'uniqueNormalizedTitles':len(set(titles)),
        'repeatedTitleCount':len(titles)-len(set(titles)),
        'bodyCopiedVerbatimFromInputIds':copies,'originalityAutomaticallyEstablished':False}


def run_mlx(args,selected):
    begin=time.monotonic()
    import mlx.core as mx
    from mlx_lm import load,stream_generate
    from mlx_lm.sample_utils import make_sampler
    model_path=Path(args.model)
    config=json.loads((model_path/'config.json').read_text())
    identity=inference_model(config)
    mx.set_memory_limit(2_500_000_000);mx.set_cache_limit(64_000_000)
    load_started=time.monotonic()
    model,tokenizer=load(str(model_path),adapter_path=args.adapter)
    loaded=time.monotonic()
    model.eval();rows=[]
    for probe in selected:
        if time.monotonic()-begin>=args.max_seconds:
            rows.append({'id':probe['id'],'task':probe['task'],'status':'incomplete','reason':'run-time-limit'})
            continue
        start=time.monotonic();pieces=[];count=0;timed_out=False
        prompt=tokenizer.apply_chat_template(probe['messages'],tokenize=False,add_generation_prompt=True)
        stream=stream_generate(model,tokenizer,prompt=prompt,max_tokens=args.max_tokens,
            sampler=make_sampler(temp=0),prefill_step_size=256)
        try:
            for chunk in stream:
                pieces.append(chunk.text);count+=1
                if time.monotonic()-begin>=args.max_seconds:
                    timed_out=True;break
        finally:
            stream.close()
        text=''.join(pieces)
        limited=count>=args.max_tokens
        checks=assess(probe,text)
        if timed_out or limited:
            checks['passed']=False
            checks['issues'].append('Достигнут предел времени или токенов; полный ответ не подтверждён')
        rows.append({'id':probe['id'],'task':probe['task'],'status':'incomplete' if timed_out or limited else 'measured',
            'text':text,'seconds':time.monotonic()-start,'generatedChunks':count,
            'tokenBudgetReached':limited,'checks':checks})
        print(json.dumps({'id':probe['id'],'status':rows[-1]['status'],'passed':checks['passed'],
            'seconds':rows[-1]['seconds']},ensure_ascii=False),flush=True)
    return rows,{'model':identity,'baseConfigSha256':file_sha(model_path/'config.json'),
        'baseArtifacts':{path.name:file_sha(path) for path in sorted(model_path.glob('*.safetensors'))
            if path.is_file() and not path.is_symlink()},
        'adapterSha256':file_sha(Path(args.adapter)/'adapters.safetensors') if args.adapter else None,
        'peakMemoryBytes':mx.get_peak_memory(),'rss':rss_measurements(),
        'nativeImportSeconds':load_started-begin,'modelLoadSeconds':loaded-load_started,
        'freshProcessStartupSeconds':loaded-begin,'modelReusedAcrossProbes':True,
        'firstProbeSeconds':rows[0].get('seconds') if rows else None,
        'warmProbeMedianSeconds':statistics.median(row['seconds'] for row in rows[1:] if row.get('seconds') is not None) if any(row.get('seconds') is not None for row in rows[1:]) else None,
        'elapsedSeconds':time.monotonic()-begin}


def run_argos(args,selected):
    command=([str(Path(args.python))] if not sys.platform=='darwin' else ['/usr/bin/arch','-arm64',str(Path(args.python))])
    command+=[str(ROOT/'cli'/'nmt_worker.py')]
    if args.models:command+=['--models',str(Path(args.models))]
    rows=[];begin=time.monotonic()
    for probe in selected:
        if probe['task']!='ru-zh':
            rows.append({'id':probe['id'],'task':probe['task'],'status':'unsupported','reason':'Existing Argos route supports RU→EN→ZH only'})
            continue
        remaining=args.max_seconds-(time.monotonic()-begin)
        if remaining<=0:
            rows.append({'id':probe['id'],'task':probe['task'],'status':'incomplete','reason':'run-time-limit'})
            continue
        start=time.monotonic()
        try:
            process=subprocess.run(command,input=json.dumps({'text':probe['source'],'rules':{}},ensure_ascii=False),
                capture_output=True,text=True,timeout=min(55,remaining))
            value=json.loads(process.stdout) if len(process.stdout)<=500_000 else {}
            if process.returncode or not isinstance(value.get('text'),str):
                raise ValueError('Existing Argos runtime/models unavailable')
            text=value['text'];checks=assess(probe,text)
            rows.append({'id':probe['id'],'task':probe['task'],'status':'measured','text':text,
                'seconds':time.monotonic()-start,'workerReportedSeconds':value.get('elapsedSeconds'),'checks':checks})
        except (OSError,ValueError,subprocess.TimeoutExpired):
            rows.append({'id':probe['id'],'task':probe['task'],'status':'unavailable',
                'reason':'Existing isolated Argos runtime did not return a complete translation'})
        print(json.dumps({'id':probe['id'],'status':rows[-1]['status'],
            'passed':rows[-1].get('checks',{}).get('passed')},ensure_ascii=False),flush=True)
    return rows,{'model':'existing-argos-ru-en-zh','elapsedSeconds':time.monotonic()-begin,
        'runtimePath':str(Path(args.python)),'peakMemoryBytes':None,'rss':rss_measurements(),
        'modelReusedAcrossProbes':False,'warmProbeMedianSeconds':None,
        'coldWorkerMedianSeconds':statistics.median(row['seconds'] for row in rows if row.get('seconds') is not None) if any(row.get('seconds') is not None for row in rows) else None}


def run(args):
    target=Path(args.output)
    if target.exists():raise ValueError('Use a new output file to preserve previous measurements')
    all_probes=probes();selected=all_probes[:args.probe_limit]
    rows,metadata=(run_mlx if args.backend=='mlx' else run_argos)(args,selected)
    result={'version':1,'probeVersion':'authored-heldout-v1-18','probeSha256':content_sha(all_probes),
        'selectedProbeIds':[row['id'] for row in selected],'backend':args.backend,**metadata,
        'limits':{'seconds':args.max_seconds,'tokens':args.max_tokens},'rows':rows,'summary':summary(rows),
        'checkerSha256':{path.name:file_sha(path) for path in (ROOT/'cli/text_quality.py',ROOT/'cli/local_content.py')},
        'styleDiagnostics':style_diagnostics(rows,selected),
        'automaticPromotion':False,'nativeLanguageReviewCompleted':False,
        'limitations':['These are development probes, not unseen native-reviewed benchmarks.',
            'Known concept, number, language and JSON checks do not establish all semantic accuracy.',
            'Compare the same probeSha256 and task subgroup; Argos does not support this project\'s reverse direction or JSON writing.']}
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'probeSha256':result['probeSha256'],'summary':result['summary']},ensure_ascii=False),flush=True)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend',choices=('mlx','argos'),required=True)
    parser.add_argument('--model');parser.add_argument('--adapter');parser.add_argument('--python');parser.add_argument('--models')
    parser.add_argument('--output',required=True);parser.add_argument('--max-seconds',type=int,default=600)
    parser.add_argument('--max-tokens',type=int,default=200);parser.add_argument('--probe-limit',type=int,default=18)
    args=parser.parse_args()
    if not 30<=args.max_seconds<=1800 or not 32<=args.max_tokens<=512 or not 1<=args.probe_limit<=18:
        parser.error('Use bounded benchmark limits')
    if args.backend=='mlx' and not args.model:parser.error('--model is required for MLX')
    if args.backend=='argos' and not args.python:parser.error('--python is required for isolated Argos')
    run(args)
