"""Read-only release identity and an explicit, aggregate-only diagnostic ZIP.

No controller, learning memory writer, model runtime or import campaign is
constructed. The archive is assembled in memory; it never sends itself or
copies a card, prompt, image, credential, raw exception or private pathname.
"""
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import re
import sqlite3
import subprocess
import zipfile


VERSION = 1
MAX_PACKAGE_BYTES = 256_000
MAX_SOURCE_BYTES = 60_000_000
SHA = re.compile(r'[a-f0-9]{64}')
COMMIT = re.compile(r'[a-f0-9]{40,64}')
DICTIONARIES = ('web/rental-lexicon.mjs', 'web/community-phrases.mjs', 'web/metro-names.mjs',
                'data/metro-published-names.json','data/moscow-geo-translations.json',
                'data/metro-official-evidence.json','data/metro-zh-supplement.json','data/moscow-geo.json')
STAGES = frozenset(('preflight','browser','navigate','challenge','scroll','extract',
                    'generate','photos','crop','vision','save'))
OUTCOMES = frozenset(('success','error','cancelled','interrupted','running'))
TRAINING_PHASES = frozenset(('idle','waiting','training','evaluating','paused',
    'quality-paused','ready','error','disabled','cooldown','prepared','waiting-for-idle'))
CANDIDATE_STATES = frozenset(('invalid-receipt','paused','trained-unaccepted','unfinished'))


def _canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def _digest(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _number(value, *, integer=False):
    return value if type(value) in ((int,) if integer else (int,float)) and math.isfinite(value) and 0 <= value <= 10**15 else None


def _sha(value):
    return value if isinstance(value,str) and SHA.fullmatch(value) else None


def _safe_file(path, root):
    path, root = Path(path), Path(root).resolve()
    try:
        if not path.absolute().is_relative_to(root) or not path.resolve().is_relative_to(root):return False
        relative=path.absolute().relative_to(root)
        cursor=root
        for part in relative.parts:
            cursor=cursor/part
            if cursor.is_symlink():return False
        return path.is_file()
    except (OSError,ValueError):return False


def _read(path, root, maximum=64_000):
    if not _safe_file(path,root):return {}
    try:
        if not 0 < path.stat().st_size <= maximum:return {}
        value=json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value,dict) else {}
    except (OSError,ValueError,UnicodeError):return {}


def _file_sha(path, root, maximum=MAX_SOURCE_BYTES):
    path=Path(path)
    if not _safe_file(path,root):return None
    try:
        if not 0 <= path.stat().st_size <= maximum:return None
        total=0;digest=hashlib.sha256()
        with path.open('rb') as stream:
            while block:=stream.read(256_000):
                total+=len(block)
                if total>maximum:return None
                digest.update(block)
        return digest.hexdigest()
    except OSError:return None


def source_identity(root):
    """Hash source components, excluding generated bundles and this manifest."""
    root=Path(root).resolve();groups={};total=0
    for label,folder,extensions in (('site','web',{'.js','.mjs','.html','.css'}),('server','cli',{'.py'})):
        rows={}
        for path in sorted((root/folder).rglob('*')):
            if path.suffix not in extensions or not path.is_file() or '__pycache__' in path.parts:continue
            if len(rows)>=2000 or not _safe_file(path,root):raise ValueError('Состав исходников сборки недоступен')
            total+=path.stat().st_size
            if total>MAX_SOURCE_BYTES:raise ValueError('Исходники превышают предел диагностики')
            digest=_file_sha(path,root)
            if not digest:raise ValueError('Не удалось проверить исходники сборки')
            rows[path.relative_to(root).as_posix()]=digest
        groups[label]={'sha256':_digest(rows),'files':len(rows)}
    resources={name:_file_sha(root/name,root) for name in DICTIONARIES}
    groups['dictionary']={'sha256':_digest(resources),'files':sum(value is not None for value in resources.values())}
    build={name:_file_sha(root/name,root) for name in ('scripts/build.mjs','scripts/build.sh','package.json')}
    groups['builder']={'sha256':_digest(build),'files':sum(value is not None for value in build.values())}
    return {'version':VERSION,'buildId':_digest(groups),'components':groups}


def _git(root):
    try:
        result=subprocess.run(['git','--no-optional-locks','-C',str(root),'rev-parse','HEAD'],capture_output=True,text=True,timeout=3)
        commit=result.stdout.strip()
        if result.returncode or not COMMIT.fullmatch(commit):return {'commit':None,'dirty':None}
        status=subprocess.run(['git','--no-optional-locks','-C',str(root),'status','--porcelain','--untracked-files=normal'],capture_output=True,text=True,timeout=3)
        return {'commit':commit,'dirty':bool(status.stdout.strip()) if status.returncode==0 else None}
    except (OSError,subprocess.SubprocessError):return {'commit':None,'dirty':None}


def write_build_identity(root):
    root=Path(root).resolve();identity=source_identity(root)
    identity['sourceCommitAtBuild']=_git(root)['commit']
    identity['createdAt']=datetime.now(timezone.utc).isoformat()
    path=root/'data/build-identity.json'
    if path.is_symlink() or path.parent.is_symlink():raise ValueError('Путь квитанции сборки недоступен')
    path.parent.mkdir(exist_ok=True)
    temporary=path.with_name('.build-identity.tmp')
    if temporary.exists():raise ValueError('Осталась незавершённая квитанция сборки')
    try:
        with temporary.open('xb') as stream:stream.write(_canonical(identity)+b'\n')
        temporary.replace(path)
    finally:temporary.unlink(missing_ok=True)
    return identity


def _model_versions(directory):
    root=Path(directory).resolve();text=_read(root/'text-ai.json',root)
    photo=_read(root/'photo-ai.json',root)
    base=text.get('modelPath',str(root/'tools/text-ai/model'))
    adapter=text.get('adapterPath')
    base=Path(base).expanduser() if isinstance(base,str) and len(base)<=4096 else root/'missing-model'
    adapter=Path(adapter).expanduser() if isinstance(adapter,str) and len(adapter)<=4096 else None
    adapter_sha=_file_sha(adapter/'adapters.safetensors',root,10_000_000) if adapter else None
    config_sha=_file_sha(base/'config.json',root,1_000_000)
    known_photo={'redcn-furniture-v1','qwen3-vl:2b-instruct-q4_K_M'}
    model=photo.get('model','qwen3-vl:2b-instruct-q4_K_M')
    native_sha=None
    if model=='redcn-furniture-v1':
        from photo_native_runtime import MODEL_PATH
        native_sha=_file_sha(root/MODEL_PATH,root,60_000_000)
    return {'text':{'configurationSha256':config_sha,'baseWeightsVerified':False,
                    'adapterSelected':text.get('useCandidateAdapter') is True,
                    'adapterWeightsSha256':adapter_sha,
                    'adapterVerified':bool(adapter_sha)},
            'photo':{'model':model if model in known_photo else 'configured-external-model',
                     'weightsSha256':native_sha,'weightsVerified':bool(native_sha)}}


def _dictionary_version(directory,root,bundled_sha):
    profile=_read(Path(directory)/'phone-profile.json',directory,1_000_000)
    rules=profile.get('rules')
    valid=isinstance(rules,dict) and len(rules)<=20_000 and all(isinstance(k,str) and isinstance(v,str) for k,v in rules.items())
    return {'bundledSha256':bundled_sha,
            'personalEntries':len(rules) if valid else 0,'personalSha256':_digest(rules) if valid else None}


def _shell_build(root,name):
    path=Path(root)/name
    if not _safe_file(path,root):return None
    try:
        with path.open('r',encoding='utf-8') as stream:head=stream.read(32_000)
        found=re.search(r'<meta\s+name=["\']redcn-build["\']\s+content=["\']([a-f0-9]{64})["\']',head)
        return found[1] if found else None
    except (OSError,UnicodeError):return None


def runtime_identity(root):
    """Capture the code tree once when this process starts serving requests."""
    return {'source':source_identity(root),'git':_git(root)}


def versions(directory,root,*,server_release='unknown',client_build=None,server_runtime=None):
    root=Path(root).resolve();directory=Path(directory).resolve()
    current=source_identity(root);manifest=_read(root/'data/build-identity.json',root)
    declared=_sha(manifest.get('buildId'));mismatches=[]
    declared_components=manifest.get('components',{}) if isinstance(manifest.get('components'),dict) else {}
    if not declared:mismatches.append('build-manifest-missing')
    for part in ('site','server','dictionary','builder'):
        recorded=declared_components.get(part,{})
        if declared and (not isinstance(recorded,dict) or recorded.get('sha256')!=current['components'][part]['sha256']):mismatches.append(part+'-source-changed')
    shells={'desktop':_shell_build(root,'docs/index.html'),'phone':_shell_build(root,'docs/phone.html')}
    for kind,digest in shells.items():
        if digest is None:mismatches.append(kind+'-shell-unversioned')
        elif digest!=declared:mismatches.append(kind+'-shell-outdated')
    client=_sha(client_build)
    if client_build is not None and not client:mismatches.append('client-unversioned')
    elif client and client!=declared:mismatches.append('client-tab-outdated')
    runtime_source=server_runtime.get('source',{}) if isinstance(server_runtime,dict) else {}
    runtime_build=_sha(runtime_source.get('buildId')) if isinstance(runtime_source,dict) else None
    if server_runtime is not None:
        if runtime_build is None:mismatches.append('server-process-unversioned')
        elif runtime_build!=current['buildId']:mismatches.append('server-process-outdated')
    runtime_git=server_runtime.get('git',{}) if isinstance(server_runtime,dict) else {}
    runtime_commit=runtime_git.get('commit') if isinstance(runtime_git,dict) else None
    runtime_commit=runtime_commit if isinstance(runtime_commit,str) and COMMIT.fullmatch(runtime_commit) else None
    release=server_release if isinstance(server_release,str) and re.fullmatch(r'v[0-9]+(?:\.[0-9]+){0,3}',server_release) else 'unknown'
    return {'version':VERSION,'readOnly':True,'serverRelease':release,
            'serverBuildId':runtime_build,'sourceOnDiskBuildId':current['buildId'],
            'runtimeIdentityAvailable':runtime_build is not None,
            'serverGitAtStart':{'commit':runtime_commit,'dirty':runtime_git.get('dirty') if isinstance(runtime_git,dict) and type(runtime_git.get('dirty')) is bool else None},
            'declaredBuildId':declared,'clientBuildId':client,
            'shells':shells,'components':current['components'],'git':_git(root),
            'models':_model_versions(directory),'dictionary':_dictionary_version(directory,root,current['components']['dictionary']['sha256']),
            'compatible':not mismatches,'mismatches':mismatches,
            'modelStatusIsQualityAcceptance':False}


@contextmanager
def _readonly(path):
    db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=.3)
    try:
        db.execute('PRAGMA query_only=ON')
        yield db
    finally:db.close()


def _quantiles(values):
    values=sorted(value for value in values if _number(value) is not None)
    def at(q):
        if not values:return None
        index=(len(values)-1)*q;low=int(index);high=min(len(values)-1,low+1)
        return round(values[low]+(values[high]-values[low])*(index-low),3)
    return {'count':len(values),'p50Seconds':at(.5),'p95Seconds':at(.95),'maxSeconds':round(values[-1],3) if values else None}


def operation_aggregates(directory):
    root=Path(directory).resolve();out={'readOnly':True,'imports':{'available':False},'training':{}}
    path=root/'import-diagnostics/campaigns.sqlite3'
    if _safe_file(path,root):
        try:
            with _readonly(path) as db:
                rows=db.execute('SELECT substr(outcome,1,16),elapsed,substr(error_class,1,80) FROM attempts ORDER BY started DESC LIMIT 201').fetchall()
                events=db.execute('SELECT attempt_id,substr(payload,1,16001) FROM events ORDER BY attempt_id,sequence LIMIT 10001').fetchall()
            outcomes=Counter();errors=Counter();stages=Counter();durations=defaultdict(list);previous={}
            known_errors={'TimeoutError','ValueError','RuntimeError','OSError','ModelBusy','CancelledError','InterruptedError'}
            for outcome,elapsed,error in rows[:200]:
                outcomes[outcome if outcome in OUTCOMES else 'unknown']+=1
                if error:errors[error if error in known_errors else 'other']+=1
            for attempt,raw in events[:10000]:
                if not isinstance(raw,str) or len(raw)>16_000:continue
                try:value=json.loads(raw)
                except ValueError:continue
                if not isinstance(value,dict):continue
                stage=value.get('stage');elapsed=_number(value.get('elapsedSeconds'))
                if stage not in STAGES or elapsed is None:continue
                stages[stage]+=1
                old=previous.get(attempt)
                if old and elapsed>=old[1]:durations[old[0]].append(elapsed-old[1])
                previous[attempt]=(stage,elapsed)
            out['imports']={'available':True,'sampleLimit':200,'truncated':len(rows)>200 or len(events)>10000,
                'outcomes':dict(outcomes),'errors':dict(errors),'duration':_quantiles([row[1] for row in rows[:200]]),
                'recordedRunning':outcomes.get('running',0),'recordedRunningIsLiveProof':False,
                'stageEvents':dict(stages),'stageIntervals':{key:_quantiles(values) for key,values in durations.items()}}
        except (sqlite3.Error,OSError,ValueError):out['imports']={'available':False,'error':'unavailable'}
    config=_read(root/'learning/scheduler.json',root)
    state=_read(root/'learning/scheduler-state.json',root)
    phase=state.get('phase','idle')
    out['training']={'configuredEnabled':config.get('enabled') is True,
        'phase':phase if phase in TRAINING_PHASES else 'unknown','phaseIsLiveProof':False,
        'stepsCompleted':_number(state.get('stepsCompleted'),integer=True),
        'elapsedSeconds':_number(state.get('elapsedSeconds'))}
    jobs=root/'import-jobs/jobs.sqlite3'
    out['recoveryQueue']={'available':False,'recordedRunningIsLiveProof':False}
    if _safe_file(jobs,root):
        try:
            with _readonly(jobs) as db:rows=db.execute('SELECT substr(state,1,16),COUNT(*) FROM jobs GROUP BY state LIMIT 20').fetchall()
            known={'running','done','error','cancelled','interrupted'};counts=Counter()
            for state,count in rows:counts[state if state in known else 'unknown']+=count
            out['recoveryQueue']={'available':True,'states':dict(counts),'recordedRunningIsLiveProof':False}
        except (OSError,ValueError,sqlite3.Error):out['recoveryQueue']['error']='unavailable'
    return out


def _history(directory, selected_run=None):
    from model_history import public_history
    try:raw=public_history(directory,limit=25)
    except (OSError,ValueError,sqlite3.Error):return {'available':False,'candidates':[],'error':'unavailable'}
    rows=[]
    for row in raw.get('candidates',[])[:25]:
        if not isinstance(row,dict) or not isinstance(row.get('id'),str) or not re.fullmatch(r'[a-f0-9]{20}',row['id']):continue
        if selected_run and row['id']!=selected_run:continue
        clean={'id':row['id'],'selected':row.get('selected') is True,
            'state':row.get('state') if row.get('state') in CANDIDATE_STATES else 'unknown',
            'weightsSha256':_sha(row.get('weightsSha256')),'datasetSha256':_sha(row.get('datasetSha256')),
            'receiptMatchesWeights':row.get('receiptMatchesWeights') is True,
            'stepsCompleted':_number(row.get('stepsCompleted'),integer=True),
            'seconds':_number(row.get('seconds')),'peakMlxBytes':_number(row.get('peakMlxBytes'),integer=True),
            'validationLoss':_number(row.get('validationLoss'))}
        examples=row.get('examples',{})
        clean['examples']={key:_number(examples.get(key),integer=True) for key in ('train','validation')} if isinstance(examples,dict) else {}
        report=row.get('historicalEvaluation')
        if isinstance(report,dict):
            decision=report.get('decision',{})
            clean['historicalEvaluation']={'reportSha256':_sha(report.get('reportSha256')),
                'casesSha256':_sha(report.get('casesSha256')),'checkersSha256':_sha(report.get('checkersSha256')),
                'acceptedThen':decision.get('accepted') is True if isinstance(decision,dict) else False,
                'freshValidationRequiredForSelection':True}
        rows.append(clean)
    if selected_run and not rows:raise ValueError('Выбранный запуск недоступен. Обновите список.')
    return {'available':True,'candidates':rows,'total':_number(raw.get('total'),integer=True),'limit':25}


def package_preview(directory,root,*,server_release='unknown',client_build=None,selected_run=None,server_runtime=None):
    if selected_run is not None and (not isinstance(selected_run,str) or not re.fullmatch(r'[a-f0-9]{20}',selected_run)):
        raise ValueError('Неверный идентификатор запуска')
    files={'versions.json':versions(directory,root,server_release=server_release,client_build=client_build,server_runtime=server_runtime),
           'operations.json':operation_aggregates(directory),'training.json':_history(directory,selected_run)}
    encoded={name:_canonical(value)+b'\n' for name,value in files.items()}
    if sum(map(len,encoded.values()))>MAX_PACKAGE_BYTES:raise ValueError('Диагностика превышает предел 256 КБ')
    inventory=[{'name':name,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()} for name,data in sorted(encoded.items())]
    public={'version':VERSION,'previewSha256':_digest(inventory),'files':inventory,'maximumBytes':MAX_PACKAGE_BYTES,
        'selectedRun':selected_run,'candidates':[{'id':row['id'],'state':row['state'],'selected':row['selected']} for row in files['training.json']['candidates']],
        'excluded':['cards','prompts','answers','photos','addresses','contacts','credentials','private-paths','raw-errors'],
        'externalSend':False,'temporaryFilesCreated':False}
    return public,encoded


def package_bytes(directory,root,*,expected_preview,server_release='unknown',client_build=None,selected_run=None,server_runtime=None):
    if not _sha(expected_preview):raise ValueError('Сначала откройте состав диагностического пакета')
    public,files=package_preview(directory,root,server_release=server_release,client_build=client_build,selected_run=selected_run,server_runtime=server_runtime)
    if public['previewSha256']!=expected_preview:raise ValueError('Данные диагностики изменились. Обновите состав пакета перед скачиванием.')
    manifest={**public,'createdAt':datetime.now(timezone.utc).isoformat()}
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('manifest.json',_canonical(manifest)+b'\n')
        for name,data in sorted(files.items()):archive.writestr(name,data)
    if stream.tell()>MAX_PACKAGE_BYTES:raise ValueError('Архив диагностики превышает предел 256 КБ')
    return stream.getvalue()


def dispatch(directory,root,path,data=None,*,server_release='unknown',server_runtime=None):
    """Owner-authenticated callers only. No public/viewer endpoint is intended."""
    if path!='release-diagnostics':return None
    value={} if data is None else data
    if not isinstance(value,dict) or set(value)-{'action','clientBuild','selectedRun','expectedPreview'}:raise ValueError('Неверные поля диагностики')
    action=value.get('action','status')
    kwargs={'server_release':server_release,'client_build':value.get('clientBuild'),'server_runtime':server_runtime}
    if action=='status':return 200,versions(directory,root,**kwargs),'application/json; charset=utf-8',{}
    kwargs['selected_run']=value.get('selectedRun')
    if action=='preview':return 200,package_preview(directory,root,**kwargs)[0],'application/json; charset=utf-8',{}
    if action=='download':return 200,package_bytes(directory,root,expected_preview=value.get('expectedPreview'),**kwargs),'application/zip',{'Content-Disposition':'attachment; filename="redcn-diagnostics.zip"'}
    raise ValueError('Неизвестное действие диагностики')
