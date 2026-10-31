#!/usr/bin/env node
// Exhaustively checks compositions without shipping 786432 expanded texts in
// the opening page. Optional --export-dir creates reproducible NDJSON.gz files.
import {createHash} from 'node:crypto';
import {createWriteStream} from 'node:fs';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {join,resolve} from 'node:path';
import {createGzip} from 'node:zlib';
import {once} from 'node:events';
import {catalogStats,getTemplate,renderTemplate,TEMPLATE_KIND_COUNT,TEMPLATE_AUDIT_CONTEXT} from '../web/template-catalog.mjs';

const args=process.argv.slice(2),exportAt=args.indexOf('--export-dir');
if(args.length&&!(args.length===2&&args[0]==='--export-dir'&&args[1]))throw Error('Usage: node scripts/audit-template-catalog.mjs [--export-dir PATH]');
const output=exportAt>=0?resolve(args[exportAt+1]||''):null;
if(exportAt>=0&&!args[exportAt+1])throw Error('--export-dir requires PATH');
if(output)await mkdir(output,{recursive:true});
const checksum=value=>createHash('sha256').update(value).digest('hex');
const semantic=value=>value.replace(/[^\p{L}\p{N}{}]/gu,'');
const report={formatVersion:1,...catalogStats(),sourceSha256:checksum(await readFile(new URL('../web/template-catalog.mjs',import.meta.url))),auditContext:'complete-confirmed-fixture',
  qualification:'Counts are composed skeletons; sparse listings can have fewer different renderings.',kinds:{}};
for(const kind of ['title','description','tags']){
  const skeletons=new Set(),texts=new Set(),whole=createHash('sha256');
  let totalBytes=0,min=Infinity,max=0;
  let gzip,file;
  if(output){gzip=createGzip({level:6});file=createWriteStream(join(output,kind+'.ndjson.gz'));gzip.pipe(file);}
  for(let index=0;index<TEMPLATE_KIND_COUNT;index++){
    const template=getTemplate(kind,index),text=renderTemplate(kind,index,TEMPLATE_AUDIT_CONTEXT);
    const normalized=kind==='tags'?text.split(/\s+/).sort().join(' '):semantic(text);
    const skeletonKey=checksum(kind==='tags'?template.skeleton.split(/\s+/).sort().join(' '):semantic(template.skeleton));
    const textKey=checksum(normalized);
    if(skeletons.has(skeletonKey))throw Error(kind+' duplicate skeleton at '+index);
    if(texts.has(textKey))throw Error(kind+' duplicate substantive rendered text at '+index);
    skeletons.add(skeletonKey);texts.add(textKey);
    if(!text||/\{\{|\}\}|\[\[|\]\]/.test(text))throw Error('Unresolved template at '+kind+':'+index);
    if(kind==='title'&&(!text.includes('54㎡')||!text.includes('8W/月')))throw Error('Facts omitted at '+index);
    if(kind==='description'&&(!text.includes(TEMPLATE_AUDIT_CONTEXT.service)||!text.includes(TEMPLATE_AUDIT_CONTEXT.conditions)))throw Error('Conditions lost at '+index);
    const length=Array.from(text).length;min=Math.min(min,length);max=Math.max(max,length);
    const line=JSON.stringify({id:template.id,kind,index,skeleton:template.skeleton})+'\n';
    whole.update(line);totalBytes+=Buffer.byteLength(line);
    if(gzip&&!gzip.write(line))await once(gzip,'drain');
  }
  let compressed={};
  if(gzip){gzip.end();await once(file,'finish');const bytes=await readFile(join(output,kind+'.ndjson.gz'));compressed={file:kind+'.ndjson.gz',gzipBytes:bytes.length,gzipSha256:checksum(bytes)};}
  report.kinds[kind]={skeletons:skeletons.size,distinctNormalizedFixtureTexts:texts.size,
    expandedNdjsonBytes:totalBytes,sha256:whole.digest('hex'),fixtureLength:{min,max},
    ...compressed};
  console.log(kind+': '+skeletons.size+' skeletons / '+texts.size+' substantively different fixture texts');
}
if(output)await writeFile(join(output,'manifest.json'),JSON.stringify(report,null,2)+'\n');
console.log(JSON.stringify(report,null,2));
