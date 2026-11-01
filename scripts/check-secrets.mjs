import {execFileSync} from 'node:child_process';
import {readFile,lstat} from 'node:fs/promises';
import {fileURLToPath,pathToFileURL} from 'node:url';
import path from 'node:path';

const secretRules=[
 ['Cloud recovery/capability key',/[\"']?(?:ownerKey|cipherKey|registrationKey)[\"']?\s*[:=]\s*[\"'][a-f0-9]{64}[\"']/g],
 ['GitHub token',/\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{50,})\b/g],
 ['OpenAI key',/\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{35,}\b/g],
 ['Telegram bot token',/\b\d{7,12}:[A-Za-z0-9_-]{30,}\b/g],
 ['Private key',/-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----/g],
 ['AWS access key',/\b(?:AKIA|ASIA)[A-Z0-9]{16}\b/g],
];
const forbiddenFile=/(?:^|\/)(?:\.env(?:\..*)?|cookies?(?:\.[^/]+)?|phone-access\.json|phone-profile\.json|cloud-backup-credentials\.json|redcn-cloud-recovery-key\.json|desktop-owner\.json|auth(?:entication)?[-_.][^/]+|[^/]+\.sqlite3(?:-wal|-shm)?|\.redcn-cache|\.venv)(?:\/|$)/i;
export function scanSecretContent(filename,content){
 const hits=[];
 if(forbiddenFile.test(filename))hits.push({file:filename,line:0,reason:'Runtime/private file is tracked'});
 if(typeof content!=='string')return hits;
 const lines=content.split(/\r?\n/);
 for(const [reason,pattern]of secretRules){pattern.lastIndex=0;for(const match of content.matchAll(pattern)){
  const line=content.slice(0,match.index).split('\n').length;
  if(/secret-scan-allow:\s*\S.{8,}/u.test(lines[line-1]))continue;
  // Never include the matching secret, even in a failing log.
  hits.push({file:filename,line,reason});
 }}
 return hits;
}
export async function checkTrackedSecrets(root){
 const names=execFileSync('git',['ls-files','-z'],{cwd:root,encoding:'utf8'}).split('\0').filter(Boolean),hits=[];
 for(const file of names){const absolute=path.resolve(root,file);if(!absolute.startsWith(path.resolve(root)+path.sep))throw Error('Invalid tracked path');
  let stat;try{stat=await lstat(absolute);}catch(error){if(error.code==='ENOENT')continue;throw error;}
  if(stat.isSymbolicLink()){hits.push({file,line:0,reason:'Tracked symlink requires explicit review'});continue;}
  if(!stat.isFile())continue;
  if(forbiddenFile.test(file)){hits.push({file,line:0,reason:'Runtime/private file is tracked'});continue;}
  const bytes=await readFile(absolute);if(bytes.includes(0))continue;
  hits.push(...scanSecretContent(file,bytes.toString('utf8')));
 }
 return hits;
}
if(process.argv[1]&&import.meta.url===pathToFileURL(path.resolve(process.argv[1])).href){
 const root=fileURLToPath(new URL('../',import.meta.url)),hits=await checkTrackedSecrets(root);
 if(hits.length){for(const hit of hits)process.stderr.write(`${hit.file}:${hit.line}: ${hit.reason}\n`);process.exitCode=1;}
 else console.log('Tracked files: no supported secret patterns or runtime-private filenames');
}
