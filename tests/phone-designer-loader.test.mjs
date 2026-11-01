import test from 'node:test';
import assert from 'node:assert/strict';
import {createPhoneDesignerLoader} from '../web/phone-designer-loader.mjs';

const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};

test('prewarming and opening share one public module load within the same auth epoch',async()=>{
 const pending=deferred(),module={setupPresentationStudio(){}};let calls=0;
 const state={epoch:1,paired:true,canEdit:true};
 const loader=createPhoneDesignerLoader({getState:()=>state,importModule:()=>{calls++;return pending.promise;}});
 const warm=loader.prewarm(),first=loader.load(),second=loader.load();assert.equal(first,second);
 await Promise.resolve();assert.equal(calls,1);pending.resolve(module);
 assert.equal(await first,module);assert.equal(await warm,module);assert.equal(await loader.load(),module);assert.equal(calls,1);
});

test('Save-Data, signed-out and viewer sessions do not prewarm; an explicit allowed open still works',async()=>{
 let calls=0;const state={epoch:1,paired:false,canEdit:true,saveData:false};
 const loader=createPhoneDesignerLoader({getState:()=>state,importModule:()=>{calls++;return {version:1};}});
 assert.equal(await loader.prewarm(),null);state.paired=true;state.canEdit=false;assert.equal(await loader.prewarm(),null);
 state.canEdit=true;state.saveData=true;assert.equal(await loader.prewarm(),null);assert.equal(calls,0);
 assert.deepEqual(await loader.load(),{version:1});assert.equal(calls,1);
});

test('late module completion from a revoked session cannot initialize a replacement session',async()=>{
 const old=deferred(),fresh=deferred();let calls=0;const state={epoch:1,paired:true,canEdit:true};
 const loader=createPhoneDesignerLoader({getState:()=>state,importModule:()=>++calls===1?old.promise:fresh.promise});
 const first=loader.load();await Promise.resolve();state.paired=false;loader.invalidate();state.epoch=2;state.paired=true;
 const second=loader.load();await Promise.resolve();old.resolve({old:true});assert.equal(await first,null);
 fresh.resolve({fresh:true});assert.deepEqual(await second,{fresh:true});assert.equal(calls,2);
});

test('role loss invalidates even when a grant later returns with the same auth epoch',async()=>{
 const pending=deferred();let calls=0;const state={epoch:1,paired:true,canEdit:true};
 const loader=createPhoneDesignerLoader({getState:()=>state,importModule:()=>++calls===1?pending.promise:{fresh:true}});
 const first=loader.load();await Promise.resolve();state.canEdit=false;loader.invalidate();state.canEdit=true;
 pending.resolve({old:true});assert.equal(await first,null);assert.deepEqual(await loader.load(),{fresh:true});
});

test('a failed prewarm is handled and clears its failed promise for a later explicit retry',async()=>{
 let calls=0;const state={epoch:1,paired:true,canEdit:true};
 const loader=createPhoneDesignerLoader({getState:()=>state,importModule:()=>++calls===1?Promise.reject(Error('network')):{ready:true}});
 assert.equal(await loader.prewarm(),null);assert.deepEqual(await loader.load(),{ready:true});assert.equal(calls,2);
});
