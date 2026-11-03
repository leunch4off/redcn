#!/usr/bin/env node
// Read-only replay of supplied private saved cards. Output is aggregate: no
// listing IDs, source URLs, addresses or full descriptions are published.
import {readFile,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {resolve} from 'node:path';
import {generateListing,DICTIONARY,DEFAULT_PREFS} from '../web/engine.mjs';
import {publicationFingerprint} from '../web/rental-copy.mjs';

const options={};
for(let i=2;i<process.argv.length;i+=2){const key=process.argv[i],value=process.argv[i+1];if(!['--cards','--output','--recent','--variants'].includes(key)||!value)throw Error('Usage: --cards PRIVATE_JSON --output REPORT_JSON [--recent 65] [--variants 1]');options[key.slice(2)]=value;}
if(!options.cards||!options.output)throw Error('Private card input and output report are required');
const recent=Number(options.recent||65),variants=Number(options.variants||1);
if(!Number.isInteger(recent)||recent<1||recent>500||!Number.isInteger(variants)||variants<1||variants>50)throw Error('Bounded recent count1..500 and variants1..50 required');
const bytes=await readFile(options.cards),data=JSON.parse(bytes),rows=Array.isArray(data)?data:data.cards;
if(!Array.isArray(rows)||rows.length>5000)throw Error('Expected saved-card array, at most5000 records');
const selected=rows.slice().sort((a,b)=>Number(b.updated)-Number(a.updated)).slice(0,recent);
const report={version:1,createdAt:new Date().toISOString(),inputSha256:createHash('sha256').update(bytes).digest('hex'),
  inputCards:rows.length,selectedMostRecentCards:selected.length,variantsPerCard:variants,
  beforeFamilies:{},afterFamilies:{},leadFrames:{},generatorErrors:0,normalizedRepeatedBodies:0,
  cardsWithUnknownTranslationFragments:0,minDistinctBodies:null,modelAuthored:false,
  qualityScope:'structural-and-repeat-regression; not independent Chinese quality or model training acceptance'};
const bump=(map,key)=>map[key]=(map[key]||0)+1,started=performance.now();
for(const row of selected){
  const previous=row.payload||row,fields={...previous.fields,templateMode:'catalog'};
  bump(report.beforeFamilies,previous.templateInfo?.editorial?.descriptionStyle?.family||'unrecorded');
  const rendered=generateListing(fields,DICTIONARY,DEFAULT_PREFS);
  if(rendered.problems.length){report.generatorErrors++;continue;}
  bump(report.afterFamilies,rendered.templateInfo.editorial.descriptionStyle.family);
  bump(report.leadFrames,rendered.templateInfo.copyEngine.leadFrame||'none');
  if(rendered.unknown.length)report.cardsWithUnknownTranslationFragments++;
  const bodies=new Set();
  for(let offset=0;offset<variants;offset++){
    const candidate=offset?generateListing({...fields,copyVariant:(Number(fields.copyVariant)||0)+offset},DICTIONARY,DEFAULT_PREFS):rendered;
    if(candidate.problems.length){report.generatorErrors++;continue;}
    const fingerprint=publicationFingerprint(candidate.body);
    if(bodies.has(fingerprint))report.normalizedRepeatedBodies++;
    bodies.add(fingerprint);
  }
  report.minDistinctBodies=report.minDistinctBodies===null?bodies.size:Math.min(report.minDistinctBodies,bodies.size);
}
report.elapsedSeconds=Number(((performance.now()-started)/1000).toFixed(3));
report.distinctInitialFamilies=Object.keys(report.afterFamilies).length;
report.distinctInitialLeadFrames=Object.keys(report.leadFrames).length;
report.passed=report.generatorErrors===0&&report.normalizedRepeatedBodies===0;
await writeFile(resolve(options.output),JSON.stringify(report,null,2)+'\n',{mode:0o600});
console.log(JSON.stringify(report));
if(!report.passed)process.exitCode=1;
