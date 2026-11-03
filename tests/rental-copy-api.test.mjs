import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {generateListing} from '../web/engine.mjs';
import {rememberPublication,publicationDigest} from '../web/rental-copy.mjs';

const fields={url:'https://www.cian.ru/rent/flat/100001/',templateMode:'catalog',price:'70000',area:'40',rooms:'1',
  features:['fridge','sofa'],style:'compact',titleVariant:0,copyVariant:0,tagVariant:0};
function invoke(input){return spawnSync(process.execPath,['cli/language.mjs'],{cwd:new URL('../',import.meta.url),input:JSON.stringify(input),encoding:'utf8',timeout:10000});}

test('phone rewrite skips remembered output in one Node request and returns raw counters plus history',()=>{
  const previous=generateListing(fields),duplicate=generateListing({...fields,copyVariant:1});
  let history=rememberPublication({},'body',previous.body);history=rememberPublication(history,'body',duplicate.body);
  const result=invoke({action:'generate',fields:{...fields,copyVariant:1,copyHistory:history},copyRewrite:{parts:['body'],previous:{body:previous.body}}});
  assert.equal(result.status,0,result.stderr);
  const generated=JSON.parse(result.stdout);
  assert.equal(generated.copyCounters.body,2);
  assert.equal(generated.templateInfo.indices.description,2);
  assert.notEqual(generated.body,previous.body);assert.notEqual(generated.body,duplicate.body);
  assert(generated.copyHistory.body.includes(publicationDigest(generated.body)));
  assert(generated.copyHistory.body.includes(publicationDigest(previous.body)));
  assert.deepEqual(generated.titles,previous.titles);assert.equal(generated.tags,previous.tags);
});

test('fixed formula does not claim a duplicate title is a new generation',()=>{
  const fixed={...fields,titleFormula:'房源 {city}'};
  const previous=generateListing(fixed);
  const result=invoke({action:'generate',fields:fixed,copyRewrite:{parts:['title'],previous:{title:previous.titles[0]}}});
  assert.equal(result.status,2);assert.equal(result.stdout,'');assert.match(result.stderr,/Формула закрепляет/);
});
