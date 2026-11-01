import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

// Exercise the actual phone handler with a bounded transport fixture. The
// complete phone bootstrap belongs to native browser acceptance separately.
const phonePhotoSource=await readFile(new URL('../web/phone.mjs',import.meta.url),'utf8');
const phonePhotoFunction=phonePhotoSource.slice(phonePhotoSource.indexOf('async function phonePresentationPhotos(){'),phonePhotoSource.indexOf('\nasync function phoneEnsurePresentationCard'));
function phonePhotoFixture({fetcher,decode=async blob=>'decoded:'+blob,rows=8}={}){
 const phoneState={detailId:'card-a',authEpoch:1,detail:{media:Array.from({length:rows},(_,i)=>({url:'/api/phone/media/'+i}))}},mount={dataset:{}},marks=[],requests=[],budgets=[];
 const fetch=async(path,options)=>{requests.push({path,options});return fetcher(path,options,phoneState);};
 const withRequestDeadline=async(ms,run)=>{budgets.push(ms);return run(new AbortController().signal,{throwIfAborted(){}});};
 const performance={now:()=>Date.now(),mark:name=>marks.push(name)};
 const run=Function('phoneState','ph','phoneAsset','fetch','cardPhotoDataUrl','withRequestDeadline','performance',phonePhotoFunction+'\nreturn phonePresentationPhotos;')(phoneState,()=>mount,value=>value.startsWith('/api/phone/')?value:'',fetch,decode,withRequestDeadline,performance);
 return {run,phoneState,mount,marks,requests,budgets};
}

test('phone image preparation runs at most three transfers and preserves the displayed order',async()=>{
 let active=0,peak=0;
 const fixture=phonePhotoFixture({fetcher:async path=>{active++;peak=Math.max(peak,active);await new Promise(resolve=>setTimeout(resolve,Number(path.at(-1))%3*2));active--;return {ok:true,blob:async()=>path};}});
 const photos=await fixture.run();assert.equal(peak,3);assert.deepEqual(photos.map(row=>row.dataUrl),Array.from({length:8},(_,i)=>'decoded:/api/phone/media/'+i));
 assert(fixture.requests.every(row=>row.options.credentials==='same-origin'&&row.options.cache==='no-store'&&row.options.signal));assert(fixture.budgets.every(value=>value===15000));
 assert.equal(fixture.mount.dataset.photosStage,'decoded');assert(fixture.marks.includes('redcn-designer-photo-fetch-ready'));
});

test('auth or card change while photos arrive prevents old image decoding and further queued requests',async()=>{
 let decoded=0;const fixture=phonePhotoFixture({decode:async()=>{decoded++;return 'must-not-publish';},fetcher:async(path,options,state)=>{await new Promise(resolve=>setTimeout(resolve,2));state.authEpoch++;return {ok:true,blob:async()=>path};}});
 await assert.rejects(fixture.run(),/другая карточка/);assert.equal(decoded,0);assert(fixture.requests.length<=3);assert(!fixture.marks.includes('redcn-designer-photo-fetch-ready'));
});

test('a failed media response stops the queue and reports preparation failure',async()=>{
 const fixture=phonePhotoFixture({fetcher:async()=>({ok:false})});await assert.rejects(fixture.run(),/недоступна/);
 assert.equal(fixture.mount.dataset.photosStage,'error');assert(fixture.requests.length<=3);assert(!fixture.marks.includes('redcn-designer-photo-fetch-ready'));
});
