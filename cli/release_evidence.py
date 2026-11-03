"""Bounded release data validation. No artifact is executed or published here."""
import hashlib
import io
import json
from pathlib import PurePosixPath
import re
import tarfile
import zipfile

SHA40=re.compile(r'[a-f0-9]{40}')
SHA64=re.compile(r'[a-f0-9]{64}')
REPO='leunch4off/redcn'
PROJECT='appgprj_6ae36aadbaac819184b0a832deae799e'
ACTOR='leunch4off'
BRANCH='feature/rcn-v8-knowledge-photos'
ATTESTATION_ISSUE_URL='https://api.github.com/repos/'+REPO+'/issues/6'
REQUIRED_JOBS={'verify','python (0)','python (1)','python (2)','python (3)','mac-portability','sites-release-artifact'}
MAX_ARCHIVE=32*1024*1024
MAX_EXPANDED=192*1024*1024
MAX_FILES=5000
ATTESTATION_PREFIX='redcn-local-verification-v1\n'


def digest(data):return hashlib.sha256(data).hexdigest()


def _path(name):
 path=PurePosixPath(name)
 if path.is_absolute() or '..' in path.parts or '\\' in name or not name or '\x00' in name:raise ValueError('Unsafe release archive path')
 return str(path).removeprefix('./')


def read_tar(data,*,with_modes=False):
 if not isinstance(data,bytes) or not 0<len(data)<=MAX_ARCHIVE:raise ValueError('Release archive exceeds 32 MiB')
 files={};modes={};total=0
 try:
  with tarfile.open(fileobj=io.BytesIO(data),mode='r:gz') as archive:
   for member in archive:
    name=_path(member.name)
    if member.isdir():continue
    if not member.isfile() or member.issym() or member.islnk() or name in files:raise ValueError('Release links/duplicates/devices are not accepted')
    total+=member.size
    if len(files)>=MAX_FILES or member.size>32*1024*1024 or total>MAX_EXPANDED:raise ValueError('Release expanded budget exceeded')
    files[name]=archive.extractfile(member).read();modes[name]=0o755 if member.mode&0o111 else 0o644
 except (tarfile.TarError,EOFError) as error:raise ValueError('Invalid release tar archive') from error
 return (files,modes) if with_modes else files


def git_tree(files,modes=None):
 modes=modes or {};root={}
 for path,data in files.items():
  parts=_path(path).split('/');node=root
  for part in parts[:-1]:
   if part in node and not isinstance(node[part],dict):raise ValueError('Source path collision')
   node=node.setdefault(part,{})
  if parts[-1] in node:raise ValueError('Duplicate source path')
  node[parts[-1]]=(data,modes.get(path,0o644))
 def hashed(kind,data):return hashlib.sha1((kind+' '+str(len(data))+'\0').encode()+data).digest()
 def tree(node):
  entries=[]
  for name,value in node.items():
   directory=isinstance(value,dict);mode=b'40000' if directory else b'100755' if value[1]&0o111 else b'100644'
   sha=tree(value) if directory else hashed('blob',value[0]);raw=name.encode()
   entries.append((raw+(b'/' if directory else b''),mode+b' '+raw+b'\0'+sha))
  return hashed('tree',b''.join(value for _,value in sorted(entries)))
 return tree(root).hex()


def validate_release_files(files,expected_sha,expected_tree=None):
 if set(files)!={'release.json','source.tar.gz','build.tar.gz'}:raise ValueError('Unexpected release artifact members')
 try:manifest=json.loads(files['release.json'])
 except (ValueError,UnicodeError) as error:raise ValueError('Invalid release manifest') from error
 if not isinstance(manifest,dict) or manifest.get('version')!=1 or manifest.get('type')!='redcn-sites-release':raise ValueError('Unknown release artifact')
 if not SHA40.fullmatch(expected_sha) or manifest.get('sourceSha')!=expected_sha or manifest.get('repository')!=REPO or manifest.get('projectId')!=PROJECT:
  raise ValueError('Release source/project identity mismatch')
 if not SHA40.fullmatch(str(manifest.get('sourceTree',''))) or expected_tree and manifest['sourceTree']!=expected_tree:raise ValueError('Source tree mismatch')
 for name in ('source.tar.gz','build.tar.gz'):
  if manifest.get('files',{}).get(name)!={'sha256':digest(files[name]),'bytes':len(files[name])}:raise ValueError('Release internal hash mismatch')
 source,modes=read_tar(files['source.tar.gz'],with_modes=True);build=read_tar(files['build.tar.gz'])
 if git_tree(source,modes)!=manifest['sourceTree']:raise ValueError('Source archive does not match the verified Git tree')
 try:hosting=json.loads(source['.openai/hosting.json'])
 except (KeyError,ValueError,UnicodeError) as error:raise ValueError('Missing source Site identity') from error
 if hosting.get('project_id')!=PROJECT or hosting.get('static') is not None:raise ValueError('Wrong Site identity/build type')
 if set(build)!={'.openai/hosting.json','dist/.openai/hosting.json','dist/server/index.js'}:raise ValueError('Unexpected deploy archive files')
 if build['.openai/hosting.json']!=source['.openai/hosting.json'] or build['dist/.openai/hosting.json']!=source['.openai/hosting.json']:
  raise ValueError('Built Site identity differs from source')
 if build['dist/server/index.js']!=source.get('worker/index.js'):raise ValueError('Built Worker differs from verified source')
 forbidden=re.compile(r'(?:^|/)(?:\.git|\.env(?:\..*)?|node_modules|\.venv|phone-access\.json|cloud-backup-credentials\.json|redcn-cloud-recovery-key\.json|cookies\.json|[^/]+\.sqlite3)(?:/|$)')
 if any(forbidden.search(name) for name in source):raise ValueError('Runtime/private file in source archive')
 return manifest,source,build


def inspect_artifact_zip(data,expected_sha,*, expected_tree=None,artifact_digest=None):
 if not isinstance(data,bytes) or not 0<len(data)<=MAX_ARCHIVE:raise ValueError('Artifact ZIP exceeds 32 MiB')
 if artifact_digest is not None and artifact_digest!='sha256:'+digest(data):raise ValueError('GitHub artifact digest mismatch')
 try:
  with zipfile.ZipFile(io.BytesIO(data)) as archive:
   members=archive.infolist()
   if len(members)!=3 or len({m.filename for m in members})!=3 or any(m.is_dir() or m.flag_bits&1 or m.file_size>MAX_ARCHIVE for m in members):raise ValueError('Invalid artifact ZIP members')
   if sum(m.file_size for m in members)>MAX_ARCHIVE:raise ValueError('Artifact ZIP expanded budget exceeded')
   files={_path(m.filename):archive.read(m) for m in members}
 except zipfile.BadZipFile as error:raise ValueError('Invalid artifact ZIP') from error
 manifest,source,build=validate_release_files(files,expected_sha,expected_tree)
 return {'manifest':manifest,'source':source,'build':build,'files':files}


def authorize_release(*,ci,commit,jobs,artifact,comment,source_sha,source_tree,branch=BRANCH):
 """Validate fresh connector/API responses, never values trusted from an artifact."""
 if not SHA40.fullmatch(str(source_sha)) or not SHA40.fullmatch(str(source_tree)):raise ValueError('Invalid exact commit/tree')
 if (not isinstance(ci,dict) or ci.get('status')!='completed' or ci.get('conclusion')!='success' or ci.get('head_sha')!=source_sha
     or ci.get('head_branch')!=branch or ci.get('event') not in ('push','pull_request')
     or ci.get('repository',{}).get('full_name')!=REPO or ci.get('head_repository',{}).get('full_name')!=REPO
     or ci.get('path')!='.github/workflows/verification.yml'):
  raise ValueError('Only the trusted exact-source successful verification workflow is accepted')
 if type(ci.get('id')) is not int:raise ValueError('Missing verified run identity')
 if not isinstance(commit,dict) or commit.get('sha')!=source_sha or commit.get('tree',{}).get('sha')!=source_tree:raise ValueError('Fresh GitHub commit metadata must bind the source tree')
 if (not isinstance(jobs,list) or any(not isinstance(j,dict) or j.get('status')!='completed' or j.get('conclusion')!='success'
     or type(j.get('run_id')) is not int or j['run_id']!=ci['id'] for j in jobs)
     or not REQUIRED_JOBS.issubset({j.get('name') for j in jobs})):
  raise ValueError('All verification, Mac-portability and release-artifact jobs must succeed')
 if (not isinstance(artifact,dict) or artifact.get('expired') is not False or artifact.get('name')!='redcn-sites-'+source_sha
     or type(artifact.get('id')) is not int or artifact.get('workflow_run',{}).get('id')!=ci['id']
     or artifact.get('workflow_run',{}).get('head_sha')!=source_sha
     or not re.fullmatch(r'sha256:[a-f0-9]{64}',str(artifact.get('digest','')))
     or type(artifact.get('size_in_bytes')) is not int or not 0<artifact['size_in_bytes']<=MAX_ARCHIVE):
  raise ValueError('Artifact is expired, foreign, unbound or oversized')
 if (not isinstance(comment,dict) or comment.get('user',{}).get('login')!=ACTOR
     or type(comment.get('id')) is not int or not isinstance(comment.get('body'),str)
     or comment.get('issue_url')!=ATTESTATION_ISSUE_URL
     or not comment['body'].startswith(ATTESTATION_PREFIX)):
  raise ValueError('A repository-owner local Mac verification attestation in PR #6 is required')
 try:proof=json.loads(comment['body'][len(ATTESTATION_PREFIX):])
 except ValueError as error:raise ValueError('Invalid local Mac attestation JSON') from error
 if set(proof)!={'version','projectId','repository','branch','sourceSha','sourceTree','ciRunId','macProofSha256','mac','verifiedAt'}:
  raise ValueError('Unexpected local verification fields')
 if (proof.get('version')!=1 or proof.get('projectId')!=PROJECT or proof.get('repository')!=REPO or proof.get('branch')!=branch
     or proof.get('sourceSha')!=source_sha or proof.get('sourceTree')!=source_tree or proof.get('ciRunId')!=ci['id']
     or not SHA64.fullmatch(str(proof.get('macProofSha256','')))
     or proof.get('mac')!={'system':'Darwin','gitClean':True,'activeJobs':0,'localPhonePassed':True,'pageErrors':0}
     or not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ',str(proof.get('verifiedAt','')))):
  raise ValueError('Mac proof does not authorize this exact verified source')
 return {'authorized':True,'projectId':PROJECT,'sourceSha':source_sha,'sourceTree':source_tree,'ciRunId':ci['id'],'artifactId':artifact['id'],
         'artifactDigest':artifact['digest'],'localAttestationCommentId':comment['id'],'localMacFirst':True,'publishAudience':'owner-private'}


def verify_authorized_artifact(data,*,ci,commit,jobs,artifact,comment,source_sha,source_tree,branch=BRANCH):
 authorization=authorize_release(ci=ci,commit=commit,jobs=jobs,artifact=artifact,comment=comment,source_sha=source_sha,source_tree=source_tree,branch=branch)
 if len(data)!=artifact['size_in_bytes']:raise ValueError('GitHub artifact size mismatch')
 checked=inspect_artifact_zip(data,source_sha,expected_tree=source_tree,artifact_digest=authorization['artifactDigest'])
 if checked['manifest'].get('ciRunId')!=authorization['ciRunId'] or checked['manifest'].get('localMacVerificationRequired') is not True:
  raise ValueError('Release manifest is not bound to the verified workflow/local-first policy')
 return {**checked,'authorization':authorization}
