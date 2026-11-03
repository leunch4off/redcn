import test from 'node:test';
import assert from 'node:assert/strict';
import {individualCopyRequest,individualCopyCandidate,individualCopyMetadata,startIndividualTextJob} from '../web/individual-copy.mjs';

const accepted={status:'review',modelAccepted:true,fallback:false,generationMode:'individual-model-prose',title:'莫斯科公寓',body:'完整的全新说明',tags:'#莫斯科租房'};
test('individual request uses current unsaved fields and preserves requested part',()=>{
 const fields={price:'95000',sourceDescription:'Свежая ручная правка',features:['oven']};
 const request=individualCopyRequest(fields,{title:'手工标题',body:'手工正文'},'current-editor','body');
 assert.equal(request.fields,fields);assert.equal(request.styleMode,'individual');assert.equal(request.part,'body');
 assert.equal(request.output.title,'手工标题');assert.equal(request.groupId,'current-editor');
 assert.deepEqual(individualCopyCandidate(accepted,'body'),{body:accepted.body});
 assert.deepEqual(individualCopyCandidate(accepted,'title'),{title:accepted.title});
});
test('a template fallback or rejected model cannot become a successful rewrite',()=>{
 for(const value of [{...accepted,fallback:true},{...accepted,modelAccepted:false},{...accepted,status:'blocked'},{...accepted,generationMode:'model-structure-verified-renderer'},{}]){
  assert.throws(()=>individualCopyCandidate(value));
 }
 assert.deepEqual(individualCopyCandidate(accepted),{title:accepted.title,body:accepted.body,tags:accepted.tags});
});
test('persisted model metadata excludes raw text, prompt and private evidence',()=>{
 const metadata=individualCopyMetadata({...accepted,rawText:'raw',messages:[{content:'private'}],photoEvidence:['private'],factsHash:'a'.repeat(64),inventoryCount:25});
 assert.equal(metadata.inventoryCount,25);assert.equal(metadata.generationMode,'individual-model-prose');
 for(const key of ['rawText','messages','photoEvidence','title','body','tags'])assert.equal(Object.hasOwn(metadata,key),false);
});
test('text jobs expose measured per-request progress before returning a draft',async()=>{
 const id='b'.repeat(32),seen=[],calls=[];let polls=0;
 const api=async(path,data)=>{calls.push({path,data});if(path==='text-jobs')return {job_id:id,state:'running'};
  if(++polls===1)return {id,state:'running',progress:{ai:{generatedTokens:12,tokensPerSecond:3.4,phase:'generating'}}};
  return {id,state:'done',resultType:'draft',result:accepted};};
 const pending=startIndividualTextJob(api,{action:'preview',fields:{price:'85000'},part:'body'},{pollInterval:0,onProgress:job=>seen.push(job)});
 const result=await pending.completion;assert.equal(result.result,accepted);assert.equal(result.resultType,'draft');
 assert.equal(seen[0].progress.ai.generatedTokens,12);assert.equal(seen[0].progress.ai.tokensPerSecond,3.4);
 assert.match(calls[0].data.requestId,/^[A-Za-z0-9_-]{12,80}$/);assert.equal(calls[1].path,'text-jobs/'+id);
});
test('cancel uses the owned job endpoint and a cancelled job cannot return a draft',async()=>{
 const id='c'.repeat(32),calls=[];let release,announced;
 const started=new Promise(resolve=>{announced=resolve;}),waiting=new Promise(resolve=>{release=resolve;});
 const api=async(path,data)=>{calls.push(path);if(path==='text-jobs')return {job_id:id};
  if(path.endsWith('/cancel')){release({state:'cancelled',message:'Отменено'});return {canCancel:false,cancelRequested:true};}
  announced();return waiting;};
 const pending=startIndividualTextJob(api,{action:'preview',fields:{},part:'body'},{pollInterval:0});
 await started;await pending.cancel();await assert.rejects(pending.completion,/Отменено/);
 assert.equal(pending.cancellationRequested,true);assert.equal(calls.filter(path=>path.endsWith('/cancel')).length,1);
});
test('a save already inside its commit window is reported as completed, not undone',async()=>{
 const id='d'.repeat(32);let release,announced;
 const started=new Promise(resolve=>{announced=resolve;}),waiting=new Promise(resolve=>{release=resolve;});
 const card={id:'e'.repeat(32),body:'已经保存'};
 const api=async(path,data)=>{if(path==='text-jobs')return {job_id:id};if(path.endsWith('/cancel')){release({state:'done',resultType:'card',result:card});return {canCancel:false};}announced();return waiting;};
 const pending=startIndividualTextJob(api,{action:'rewrite',cardId:card.id,part:'body'});
 await started;await pending.cancel();const completed=await pending.completion;
 assert.equal(completed.result,card);assert.equal(completed.cancellationRequested,true);
});
