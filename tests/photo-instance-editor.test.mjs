import test from 'node:test';
import assert from 'node:assert/strict';
import {instanceRegion,instanceDraft} from '../web/photo-instance-editor.mjs';

test('drag rectangles are normalized in either direction and clamped to the original',()=>{
 assert.deepEqual(instanceRegion([.8,.7],[.2,.1]),[.2,.1,.6,.6]);
 assert.deepEqual(instanceRegion([-1,.2],[2,.8]),[0,.2,1,.6]);
});
test('manual same-identity observations preserve photo evidence without auto publication',()=>{
 const original={listingId:'a'.repeat(32),observations:[],countConfirmations:{chairs:2}};
 let next=instanceDraft(original,{action:'add',id:'one',photoId:'b'.repeat(64),feature:'chairs',instanceId:'chair-one',region:[.1,.1,.2,.3]});
 next=instanceDraft(next,{action:'add',id:'two',photoId:'c'.repeat(64),feature:'chairs',instanceId:'chair-one',region:[.4,.1,.2,.3]});
 assert.equal(next.observations.length,2);assert.equal(next.observations[0].instanceId,next.observations[1].instanceId);
 assert.deepEqual(next.countConfirmations,{});assert.equal(original.observations.length,0);
 assert.ok(next.observations.every(row=>row.status==='confirmed'&&row.linkConfirmed===true));
});
test('any link edit invalidates old count confirmations and zero-area is rejected',()=>{
 const original={observations:[{id:'one'}],countConfirmations:{chairs:2}};
 assert.deepEqual(instanceDraft(original,{action:'remove',id:'one'}),{observations:[],countConfirmations:{}});
 assert.throws(()=>instanceDraft(original,{action:'add',photoId:'b',feature:'chairs',instanceId:'c',region:[0,0,0,0]}));
 assert.throws(()=>instanceDraft(original,{action:'count',feature:'chairs',count:1.5}));
});
