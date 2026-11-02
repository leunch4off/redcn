"""Audit roadmap structure, rendered statuses and typed acceptance receipts.

The auditor never marks tasks done. A source file, a plan, a synthetic test and
a physical acceptance report remain different evidence kinds. Legacy prose
references are checked for broken local links but do not become typed proof.
"""
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import subprocess


KINDS=frozenset(('plan','source-audit','unit-test','browser-emulation','native-runtime',
                 'physical-device','physical-sleep-wake','independent-review','data-evaluation'))
ORIGINS=frozenset(('synthetic','anonymized-capture','operator-report','production-observation','not-applicable'))
STATUSES=frozenset(('planned','in_progress','blocked','done'))
ID=re.compile(r'RCN-[0-9]{1,5}')
SHA=re.compile(r'[a-f0-9]{64}')
COMMIT=re.compile(r'[a-f0-9]{40,64}')
REFERENCE=re.compile(r'(?<![\w:/])((?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.(?:jsonl|json|mjs|js|md|py|html|png|pdf|yaml|yml|txt|csv))(?![\w.])(?:#([\w\-.:]+))?')
CHECKBOX=re.compile(r'^\s*-\s+\[([ xX])\]\s+(?:\*\*)?(RCN-[0-9]{1,5})\b',re.M)
REQUIRED_KINDS={'RCN-225':{'physical-device'},'RCN-247':{'physical-device'},
                'RCN-262':{'independent-review'},'RCN-355':{'native-runtime','physical-device'},
                'RCN-356':{'native-runtime','physical-sleep-wake'},'RCN-362':{'physical-device'}}


def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def criterion_sha(task):return hashlib.sha256(canonical({'id':task['id'],'acceptance':task['acceptance']})).hexdigest()


def _read_json(path,limit=8_000_000):
    path=Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size>limit:raise ValueError('Missing or oversized JSON input')
    return json.loads(path.read_text(encoding='utf-8'))


def _path(root,name):
    if not isinstance(name,str) or len(name)>1000 or '\\' in name or not name:raise ValueError('Invalid artifact path')
    path=Path(name)
    if path.is_absolute() or any(part in ('..','.') for part in path.parts):raise ValueError('Artifact path must stay in repository')
    target=root/path;cursor=root
    for part in path.parts:
        cursor=cursor/part
        if cursor.is_symlink():raise ValueError('Linked evidence is not accepted')
    if not target.resolve().is_relative_to(root.resolve()):raise ValueError('Artifact escapes repository')
    return target


def _anchors(path):
    text=path.read_text(encoding='utf-8');anchors=set();used=Counter()
    for match in re.finditer(r'^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$',text,re.M):
        heading=re.sub(r'<[^>]+>','',match[1]);heading=re.sub(r'\[([^]]+)\]\([^)]*\)',r'\1',heading)
        slug=re.sub(r'[^\w\- ]','',heading.lower()).replace(' ','-')
        count=used[slug];used[slug]+=1;anchors.add(slug+('-'+str(count) if count else ''))
    anchors.update(re.findall(r'\b(?:id|name)=["\']([^"\']+)["\']',text))
    return anchors


def _git(root):
    try:
        result=subprocess.run(['git','--no-optional-locks','-C',str(root),'rev-parse','HEAD'],capture_output=True,text=True,timeout=3)
        value=result.stdout.strip()
        if result.returncode or not COMMIT.fullmatch(value):return None
        return value
    except (OSError,subprocess.SubprocessError):return None


def _rendered(text):
    rows=CHECKBOX.findall(text);counts=Counter(identifier for mark,identifier in rows)
    return {identifier:'done' if mark.lower()=='x' else 'planned' for mark,identifier in rows},sorted(identifier for identifier,count in counts.items() if count>1)


def audit(root,*,plan=None,roadmap=None,site_plan=None,issue=None,receipts=None,previous_plan=None,commit=None,ci_receipt=None):
    root=Path(root).resolve();plan=plan if plan is not None else _read_json(root/'plan.json')
    errors=[];warnings=[];proof_errors=[];gaps=[];links=[];tasks=[]
    def error(code,task=None,detail=None):errors.append({key:value for key,value in {'code':code,'taskId':task,'detail':detail}.items() if value is not None})
    if not isinstance(plan,dict) or not isinstance(plan.get('tasks'),list):raise ValueError('Plan requires a tasks array')
    if len(plan['tasks'])>5000:raise ValueError('Too many roadmap tasks')
    counts=Counter();by_id={}
    for task in plan['tasks']:
        if not isinstance(task,dict) or not isinstance(task.get('id'),str) or not ID.fullmatch(task['id']):error('invalid-task-id');continue
        identifier=task['id'];counts[identifier]+=1;by_id[identifier]=task;tasks.append(task)
        if not isinstance(task.get('title'),str) or not task['title'].strip():error('missing-title',identifier)
        if not isinstance(task.get('acceptance'),str) or not task['acceptance'].strip():error('missing-acceptance',identifier)
        if not isinstance(task.get('priority'),str) or task['priority'] not in {'P0','P1','P2'}:error('invalid-priority',identifier)
        if not isinstance(task.get('status'),str) or task['status'] not in STATUSES:error('invalid-status',identifier)
        if not isinstance(task.get('depends_on'),list) or any(not isinstance(v,str) or not ID.fullmatch(v) for v in task.get('depends_on',[])):
            error('invalid-dependencies',identifier)
        if not isinstance(task.get('evidence'),list) or any(not isinstance(v,str) or len(v)>16000 for v in task.get('evidence',[])):
            error('invalid-evidence-list',identifier)
    if plan.get('total')!=len(plan['tasks']):error('total-mismatch')
    for identifier,count in counts.items():
        if count>1:error('duplicate-id',identifier)
    for task in tasks:
        dependencies=task.get('depends_on',[]) if isinstance(task.get('depends_on'),list) else []
        if len(set(v for v in dependencies if isinstance(v,str)))!=len(dependencies):error('duplicate-dependency',task['id'])
        for dependency in dependencies:
            if not isinstance(dependency,str) or dependency not in by_id:error('missing-dependency',task['id'],str(dependency)[:100])
    visiting=set();visited=set();cycles=set()
    def visit(identifier,trail):
        if identifier in visiting:
            cycle=trail[trail.index(identifier):]+[identifier]
            key=tuple(sorted(set(cycle)))
            if key not in cycles:cycles.add(key);error('dependency-cycle',identifier,cycle)
            return
        if identifier in visited:return
        visiting.add(identifier)
        for dependency in by_id[identifier].get('depends_on',[]) if isinstance(by_id[identifier].get('depends_on'),list) else []:
            if isinstance(dependency,str) and dependency in by_id:visit(dependency,trail+[identifier])
        visiting.remove(identifier);visited.add(identifier)
    for identifier in by_id:visit(identifier,[])
    if previous_plan is not None:
        old=previous_plan if isinstance(previous_plan,dict) else _read_json(previous_plan)
        for task in old.get('tasks',[]):
            if task.get('id') not in by_id:error('removed-existing-id',task.get('id'))

    anchor_cache={}
    for task in tasks:
        refs=task.get('evidence',[]) if isinstance(task.get('evidence'),list) else []
        if task.get('status')=='done' and not refs:gaps.append({'taskId':task['id'],'code':'done-without-evidence'})
        for prose in refs:
            if not isinstance(prose,str):continue
            # Remote references remain unverified; the auditor does not guess a
            # GitHub CI outcome from prose or fetch arbitrary external URLs.
            if re.search(r'https?://',prose):warnings.append({'taskId':task['id'],'code':'external-reference-not-fetched'})
            without_urls=re.sub(r'https?://\S+','',prose)
            for name,anchor in REFERENCE.findall(without_urls):
                item={'taskId':task['id'],'path':name,'anchor':anchor or None,'exists':False,'anchorExists':None}
                try:
                    target=_path(root,name);item['exists']=target.is_file()
                    if not item['exists'] and '/' not in name:
                        warnings.append({'taskId':task['id'],'code':'unresolved-bare-filename','name':name})
                    elif not item['exists']:error('missing-evidence-file',task['id'],name)
                    elif anchor:
                        if target.suffix.lower() in {'.md','.html'}:
                            if name not in anchor_cache:anchor_cache[name]=_anchors(target)
                            item['anchorExists']=anchor in anchor_cache[name]
                        elif re.fullmatch(r'L\d+(?:-L\d+)?',anchor):
                            maximum=max(map(int,re.findall(r'\d+',anchor)));item['anchorExists']=maximum<=len(target.read_text(encoding='utf-8').splitlines())
                        else:item['anchorExists']=False
                        if not item['anchorExists']:error('missing-evidence-anchor',task['id'],name+'#'+anchor)
                except (OSError,ValueError,UnicodeError):error('unreadable-evidence',task['id'],name)
                links.append(item)

    representations={}
    expected={identifier:('done' if task.get('status')=='done' else 'planned') for identifier,task in by_id.items()}
    for label,value in (('roadmap',roadmap),('issue',issue)):
        if value is None:
            representations[label]={'checked':False};continue
        if isinstance(value,Path):value=value.read_text(encoding='utf-8')
        if isinstance(value,dict):value=value.get('body','')
        if not isinstance(value,str):error('invalid-rendered-input',detail=label);continue
        actual,duplicates=_rendered(value)
        mismatch=sorted(identifier for identifier in set(expected)|set(actual) if expected.get(identifier)!=actual.get(identifier))
        for identifier in duplicates:error('duplicate-rendered-id',identifier,label)
        for identifier in mismatch:error('rendered-status-mismatch',identifier,label)
        representations[label]={'checked':True,'matches':not mismatch and not duplicates,'tasks':len(actual)}
    if site_plan is not None:
        site=site_plan if isinstance(site_plan,dict) else _read_json(site_plan)
        matches=canonical(site)==canonical(plan)
        representations['site']={'checked':True,'matches':matches}
        if not matches:error('site-plan-mismatch')
    else:representations['site']={'checked':False}

    current=commit if commit is not None else _git(root)
    if current is not None and (not isinstance(current,str) or not COMMIT.fullmatch(current)):raise ValueError('Invalid checkout commit')
    registry={'version':1,'receipts':[]} if receipts is None else receipts if isinstance(receipts,dict) else _read_json(receipts)
    if not isinstance(registry,dict) or registry.get('version')!=1 or not isinstance(registry.get('receipts'),list):raise ValueError('Typed receipt registry requires version 1')
    if len(registry['receipts'])>10000:raise ValueError('Too many typed receipts')
    valid=defaultdict(list);proofs=[];seen_receipts=set()
    for raw in registry['receipts']:
        problems=[]
        if not isinstance(raw,dict):proof_errors.append({'code':'invalid-receipt'});continue
        identifier=raw.get('taskId');task=by_id.get(identifier) if isinstance(identifier,str) else None
        if not task:problems.append('unknown-task')
        kind=raw.get('kind') if isinstance(raw.get('kind'),str) else None
        origin=raw.get('origin') if isinstance(raw.get('origin'),str) else None
        if kind not in KINDS:problems.append('invalid-kind')
        if origin not in ORIGINS:problems.append('invalid-origin')
        if kind in {'physical-device','physical-sleep-wake','independent-review'} and origin in {'synthetic','not-applicable'}:problems.append('synthetic-is-not-physical-evidence')
        if type(raw.get('passed')) is not bool or type(raw.get('coversAcceptance')) is not bool:problems.append('missing-result-or-coverage')
        if task and isinstance(task.get('acceptance'),str) and raw.get('criterionSha256')!=criterion_sha(task):problems.append('acceptance-changed')
        if not current or raw.get('commit')!=current:problems.append('different-or-unknown-commit')
        digest=raw.get('sha256')
        if not isinstance(digest,str) or not SHA.fullmatch(digest):problems.append('invalid-artifact-hash')
        try:
            artifact=_path(root,raw.get('artifact'))
            if not artifact.is_file() or artifact.stat().st_size>20_000_000:problems.append('missing-or-oversized-artifact')
            elif hashlib.sha256(artifact.read_bytes()).hexdigest()!=digest:problems.append('artifact-hash-mismatch')
        except (OSError,ValueError):problems.append('invalid-artifact')
        key=tuple(value if isinstance(value,str) else None for value in (identifier,kind,raw.get('artifact'),digest))
        if key in seen_receipts:problems.append('duplicate-receipt')
        seen_receipts.add(key)
        if identifier=='RCN-356' and raw.get('kind')=='native-runtime':
            duration=raw.get('actualDurationSeconds')
            if type(duration) not in (int,float) or not 7200<=duration<=86400:problems.append('soak-shorter-than-two-hours')
        proof={'taskId':identifier,'kind':raw.get('kind'),'origin':raw.get('origin'),'artifact':raw.get('artifact'),
               'valid':not problems,'problems':problems,'passed':raw.get('passed') is True,'coversAcceptance':raw.get('coversAcceptance') is True}
        proofs.append(proof)
        if problems:proof_errors.append({'taskId':identifier,'code':'invalid-typed-evidence','problems':problems})
        elif proof['passed'] and proof['coversAcceptance'] and proof['kind']!='plan':valid[identifier].append(proof)

    verified=[];claimed=[]
    for task in tasks:
        if task.get('status')!='done':continue
        identifier=task['id'];claimed.append(identifier);issues=[]
        if not valid[identifier]:issues.append('no-current-complete-typed-proof')
        required=REQUIRED_KINDS.get(identifier,set());available={row['kind'] for row in valid[identifier]}
        if required-available:issues.append('missing-required-evidence-kind')
        if any(by_id.get(dep,{}).get('status')!='done' for dep in task.get('depends_on',[]) if isinstance(dep,str)):issues.append('unfinished-dependency')
        if issues:gaps.append({'taskId':identifier,'code':'acceptance-not-verified','reasons':issues,'missingKinds':sorted(required-available)})
        else:verified.append(identifier)

    ci={'checked':False,'matchesCommit':False}
    if ci_receipt is not None:
        receipt=ci_receipt if isinstance(ci_receipt,dict) else _read_json(ci_receipt)
        url=receipt.get('runUrl','') if isinstance(receipt,dict) else ''
        safe_url=isinstance(url,str) and re.fullmatch(r'https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/actions/runs/[0-9]+(?:/attempts/[0-9]+)?',url)
        matches=isinstance(receipt,dict) and receipt.get('commit')==current and receipt.get('conclusion')=='success' and bool(safe_url)
        ci={'checked':True,'matchesCommit':matches,'remoteOutcomeIndependentlyFetched':False,'runUrl':url if safe_url else None}
        if not matches:proof_errors.append({'code':'ci-receipt-does-not-match-checkout'})
    return {'version':1,'readOnly':True,'commit':current,'planSha256':hashlib.sha256(canonical(plan)).hexdigest(),
        'structuralOk':not errors,'proofIntegrityOk':not proof_errors,'acceptanceCompletenessOk':not gaps,
        'summary':{'tasks':len(tasks),'claimedDone':len(claimed),'typedVerifiedDone':len(verified),
                   'unverifiedDone':len(set(claimed)-set(verified)),'plannedPercent':round(100*len(claimed)/max(1,len(tasks)),2)},
        'representations':representations,'allRepresentationsChecked':all(row.get('checked') for row in representations.values()),
        'ciReceipt':ci,'errors':errors,'warnings':warnings,'proofErrors':proof_errors,'evidenceGaps':gaps,
        'links':links,'typedEvidence':proofs,'roadmapChanged':False}
