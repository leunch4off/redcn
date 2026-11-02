import test from 'node:test';
import assert from 'node:assert/strict';
import {captureLoadedPresentationSource,loadedPresentationSource,savePresentationSource,beginPresentationExport,assertPresentationExportCurrent} from '../web/presentation-export-boundary.mjs';
import {presentationCardKey} from '../web/presentation-studio.mjs';

const id='a'.repeat(32),oldRevision='1'.repeat(64),newRevision='2'.repeat(64);
const receipt=revision=>({version:1,cardId:id,revisionSha256:revision,signature:'3'.repeat(64)});
const makeCard=()=>({id,fields:{price:'80000',photos:[]},titles:['公寓'],body:'月租80,000₽',tags:'',presentationReceipt:receipt(oldRevision)});
const deferred=()=>{let resolve;const promise=new Promise(done=>{resolve=done;});return {promise,resolve};};

test('phone export keeps the receipt delivered with its old detail even when export-list revision is newer',async()=>{
 const card=captureLoadedPresentationSource(makeCard()),latestFileList={revisionSha256:newRevision};
 const boundary=await beginPresentationExport({getCard:()=>card,cardKey:presentationCardKey,ensureCard:async options=>{assert.equal(options.exportSnapshot,true);return loadedPresentationSource(card);}});
 assert.equal(boundary.expectedRevision,oldRevision);assert.notEqual(boundary.expectedRevision,latestFileList.revisionSha256);
 assert.ok(boundary.sourceReceipt);assert.equal(boundary.sourceReceipt.revisionSha256,oldRevision);
});

test('mutating an authenticated phone card or copying its receipt into an unregistered object cannot renew its source',()=>{
 const card=captureLoadedPresentationSource(makeCard());
 assert.throws(()=>loadedPresentationSource({...card}),/Карточка изменилась/);
 card.body='月租81,000₽';assert.throws(()=>loadedPresentationSource(card),/Карточка изменилась/);
});

test('a manual edit while the desktop save request is pending aborts export before rendering',async()=>{
 let card=makeCard();const gate=deferred();
 const pending=savePresentationSource({getCard:()=>card,cardKey:presentationCardKey,saveCard:()=>gate.promise});
 card={...card,body:'手工修改',fields:{...card.fields,price:'81000'}};
 gate.resolve({result_id:id,presentationReceipt:receipt(oldRevision)});
 await assert.rejects(pending,error=>error.code==='CLIENT_REVISION_CHANGED');
});

test('the first desktop save may assign storage identity without changing rendered content',async()=>{
 let card={...makeCard(),id:null};
 const boundary=await savePresentationSource({getCard:()=>card,cardKey:presentationCardKey,saveCard:async()=>{card={...card,id,fields:{...card.fields,resultId:id}};return {result_id:id,presentationReceipt:receipt(oldRevision)};}});
 assert.equal(boundary.resultId,id);assert.equal(boundary.expectedRevision,oldRevision);
});

test('manual edits during image or PDF rendering prevent submission of the prior snapshot',async()=>{
 let card=captureLoadedPresentationSource(makeCard());
 const boundary=await beginPresentationExport({getCard:()=>card,cardKey:presentationCardKey,ensureCard:async()=>loadedPresentationSource(card)});
 const rendered={key:boundary.key,html:'old content'};
 card={...card,body:'新的正文'};
 assert.throws(()=>assertPresentationExportCurrent(rendered,boundary,{getCard:()=>card,cardKey:presentationCardKey}),error=>error.code==='CLIENT_REVISION_CHANGED');
});

test('an async ensure callback cannot silently switch the card or substitute latest list revision',async()=>{
 let card=captureLoadedPresentationSource(makeCard());const source=loadedPresentationSource(card),gate=deferred();
 const pending=beginPresentationExport({getCard:()=>card,cardKey:presentationCardKey,ensureCard:()=>gate.promise});
 card={...card,fields:{...card.fields,price:'81000'}};gate.resolve(source);
 await assert.rejects(pending,error=>error.code==='CLIENT_REVISION_CHANGED');
 await assert.rejects(beginPresentationExport({getCard:()=>card,cardKey:presentationCardKey,ensureCard:async()=>({...source,expectedRevision:newRevision})}),error=>error.code==='CLIENT_REVISION_CHANGED');
 await assert.rejects(beginPresentationExport({getCard:()=>card,cardKey:presentationCardKey,ensureCard:async()=>id}),/подтверждение исходного содержимого недоступно/);
});
