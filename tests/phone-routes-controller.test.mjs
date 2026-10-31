import test from 'node:test';
import assert from 'node:assert/strict';
import {createPhoneRouteDraftStore} from '../web/phone-routes-controller.mjs';

const A='a'.repeat(32),B='b'.repeat(32),card=(id,updated,routes=[])=>({id,updated,title:'用户标题',body:'用户正文',fields:{latitude:55.7,longitude:37.5,universityRoutes:routes}});
const route=name=>({name,transport:'Автобус 261',stops:[]});

test('dirty routes retain their original CAS version through server polling',()=>{
  const s=createPhoneRouteDraftStore();s.receive(card(A,10,[route('МГУ')]));s.edit(A,[route('РУДН')]);
  s.receive(card(A,11,[route('ВШЭ')]));const entry=s.get(A);
  assert.equal(entry.baseUpdated,10);assert.equal(entry.conflict,true);assert.equal(s.fields(entry).universityRoutes[0].name,'РУДН');
  const snapshot=s.begin(A,s.fields(entry).universityRoutes);assert.equal(snapshot.expectedUpdated,10);
});

test('a save acknowledgement retains edits made while that save was in flight',()=>{
  const s=createPhoneRouteDraftStore();s.receive(card(A,10));s.edit(A,[route('МГУ')]);const sent=s.begin(A,[route('МГУ')]);
  s.edit(A,[route('РУДН')]);const e=s.accept(sent,card(A,11,sent.routes));
  assert.equal(e.baseUpdated,11);assert.equal(e.dirty,true);assert.equal(e.draft[0].name,'РУДН');
  assert.equal(s.begin(A,e.draft).expectedUpdated,11);
});

test('an unchanged saved draft becomes clean without rewriting listing copy',()=>{
  const s=createPhoneRouteDraftStore();s.receive(card(A,10));s.edit(A,[]);const sent=s.begin(A,[]);const e=s.accept(sent,card(A,11,[]));
  assert.equal(e.dirty,false);assert.equal(e.draft,null);assert.equal(e.card.title,'用户标题');assert.equal(e.card.body,'用户正文');assert.deepEqual(e.card.fields.universityRoutes,[]);
});

test('drafts and asynchronous acknowledgements stay with their own card',()=>{
  const s=createPhoneRouteDraftStore();s.receive(card(A,10));s.edit(A,[route('МГУ')]);const sent=s.begin(A,[route('МГУ')]);
  s.receive(card(B,50));s.edit(B,[route('ВШЭ')]);s.accept(sent,card(A,11,sent.routes));
  assert.equal(s.get(B).draft[0].name,'ВШЭ');assert.equal(s.get(B).baseUpdated,50);assert.equal(s.get(A).dirty,false);
  s.receive(card(A,9,[route('Старые данные')]));assert.equal(s.get(A).baseUpdated,11);
});

test('409 and explicit rebase preserve the draft and never perform a save themselves',()=>{
  const s=createPhoneRouteDraftStore();s.receive(card(A,10));s.edit(A,[route('МГУ')]);const sent=s.begin(A,[route('МГУ')]);
  s.fail(sent,Object.assign(Error('Conflict'),{status:409}));assert.equal(s.get(A).dirty,true);assert.equal(s.get(A).baseUpdated,10);assert.equal(s.get(A).conflict,true);
  s.rebase(A,card(A,12,[route('РУДН')]));assert.equal(s.get(A).dirty,true);assert.equal(s.get(A).draft[0].name,'МГУ');assert.equal(s.get(A).baseUpdated,12);assert.equal(s.get(A).saving,false);assert.equal(s.get(A).conflict,false);
});

test('clearing authentication rejects a late save even if the next user opens the same ID',()=>{
  const s=createPhoneRouteDraftStore();s.receive(card(A,10));s.edit(A,[route('МГУ')]);const sent=s.begin(A,[route('МГУ')]);
  s.clear();s.receive(card(A,20,[route('РУДН')]));assert.equal(s.accept(sent,card(A,11,sent.routes)),null);assert.equal(s.get(A).baseUpdated,20);assert.equal(s.get(A).dirty,false);
});

test('newer polled server data is not replaced by an older in-flight save response',()=>{
  const s=createPhoneRouteDraftStore();s.receive(card(A,10));s.edit(A,[route('МГУ')]);const sent=s.begin(A,[route('МГУ')]);s.receive(card(A,12,[route('ВШЭ')]));
  const clean=s.accept(sent,card(A,11,sent.routes));assert.equal(clean.baseUpdated,12);assert.equal(clean.card.fields.universityRoutes[0].name,'ВШЭ');
  s.edit(A,[route('РУДН')]);const again=s.begin(A,s.get(A).draft);s.edit(A,[route('МГИМО')]);s.receive(card(A,14,[route('ВШЭ')]));const dirty=s.accept(again,card(A,13,again.routes));
  assert.equal(dirty.baseUpdated,13);assert.equal(dirty.conflict,true);assert.equal(dirty.draft[0].name,'МГИМО');assert.equal(dirty.card.updated,14);
});
