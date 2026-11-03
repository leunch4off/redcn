import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,symlink,rm} from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {createPreviewResponder} from '../scripts/dev.mjs';

test('preview serves the entry and its complete frontend import graph, including v26 modules',async()=>{
 const respond=createPreviewResponder(),entry=await respond('/');
 assert.equal(entry.status,200);assert.match(entry.headers['Content-Type'],/text\/html/u);
 const script=entry.body.toString().match(/<script type="module" src="([^"]+)"/u)?.[1];assert.equal(script,'/app.js');
 const queue=[script],seen=new Set();
 while(queue.length){
  const url=queue.shift();if(seen.has(url))continue;seen.add(url);
  const result=await respond(url);assert.equal(result.status,200,'missing frontend import '+url);assert.match(result.headers['Content-Type'],/javascript/u);
  const code=result.body.toString();
  for(const match of code.matchAll(/(?:^|\n)\s*(?:import|export)\s+(?:[^;\n]*?\sfrom\s*)?['"]([^'"]+)['"]/gu)){
   const target=new URL(match[1],'http://localhost'+url);assert.equal(target.origin,'http://localhost');queue.push(target.pathname);
  }
 }
 for(const required of ['/desktop-owner.mjs','/photo-instance-editor.mjs','/source-adapters/avito.mjs','/tag-post-stats.mjs','/device-notifications.mjs'])assert.ok(seen.has(required),'graph covered '+required);
 assert.ok(seen.size>45,'walk actual transitive imports rather than a small hardcoded list');
 const css=await respond('/styles.css');assert.equal(css.status,200);assert.match(css.body.toString(),/keyboard/u);assert.match(css.body.toString(),/dossier/u);
 const head=await respond('/app.js','HEAD');assert.equal(head.status,200);assert.equal(head.body.length,0);assert.ok(Number(head.headers['Content-Length'])>0);
});

test('preview denies private repo paths, traversal, unknown files and state-changing methods',async()=>{
 const respond=createPreviewResponder();
 for(const url of ['/.git/config','/.env','/.env.js','/package.json','/cli/local_server.py','/scripts/dev.mjs','/data/quality/watermark-real-v26.json','/api/local/phone','/api/local/desktop-access','/../cli/local_server.py','/%2e%2e/cli/local_server.py','/source-adapters/%2e%2e/%2e%2e/cli/local_server.py','/source-adapters%2fhtml.mjs','/missing.mjs','/constructor','/__proto__']){
  const result=await respond(url);assert.equal(result.status,404,url);assert.doesNotMatch(result.body.toString(),/secret|token|password/iu);
 }
 assert.equal((await respond('/app.js','POST')).status,405);
 const unavailable=await respond('/api/import','POST');assert.equal(unavailable.status,422);assert.match(unavailable.body.toString(),/start-redcn/u);
});

test('preview rejects symlinks escaping public files or selected data aliases',async()=>{
 const directory=await mkdtemp(path.join(os.tmpdir(),'redcn-preview-'));
 try{
  const root=path.join(directory,'project');await mkdir(path.join(root,'web'),{recursive:true});await mkdir(path.join(root,'data'));
  const secret=path.join(directory,'private.txt');await writeFile(secret,'PRIVATE FIXTURE CONTENT');
  await symlink(secret,path.join(root,'web','leak.mjs'));
  await symlink(directory,path.join(root,'web','outside'));
  await symlink(secret,path.join(root,'data','moscow-universities.json'));
  const respond=createPreviewResponder({root});
  for(const url of ['/leak.mjs','/outside/private.js','/moscow-universities.json'])assert.equal((await respond(url)).status,404,url);
 }finally{await rm(directory,{recursive:true,force:true});}
});
