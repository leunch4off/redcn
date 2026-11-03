import {chmod} from 'node:fs/promises';
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
const root=fileURLToPath(new URL('../',import.meta.url));
let existing='';try{existing=execFileSync('git',['config','--local','--get','core.hooksPath'],{cwd:root,encoding:'utf8',stdio:['ignore','pipe','ignore']}).trim();}catch(error){if(error.status!==1)throw error;}
if(existing&&existing!=='.githooks')throw Error('Existing hooksPath retained: integrate the redcn pre-push hook manually.');
await chmod(new URL('../.githooks/pre-push',import.meta.url),0o755);
execFileSync('git',['config','--local','core.hooksPath','.githooks'],{cwd:root});
console.log('Installed repository-local pre-push scan');
