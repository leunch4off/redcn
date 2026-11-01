import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

function harness(windows=[]){
  const listeners={},opened=[],focused=[];
  const self={location:{origin:'https://workspace.example'},addEventListener:(name,fn)=>listeners[name]=fn,
    clients:{matchAll:async()=>windows.map(url=>({url,focus:async()=>focused.push(url)})),openWindow:async url=>opened.push(url)}};
  vm.runInNewContext(readFileSync(new URL('../web/sw.js',import.meta.url),'utf8'),{self,URL});
  return {opened,focused,async click({data={redcnDeviceDelivery:1},tag='redcn-notice-'+ 'a'.repeat(16)+'-'+ 'b'.repeat(32)}={}){
    let work,closed=0;listeners.notificationclick({notification:{data,tag,close(){closed++;}},waitUntil(value){work=value;}});
    await work;return closed;
  }};
}
test('own device notice focuses an existing same-origin phone window',async()=>{
  const h=harness(['https://outside.example/phone/','https://workspace.example/phone/?from=home']);
  assert.equal(await h.click(),1);assert.deepEqual(h.focused,['https://workspace.example/phone/?from=home']);assert.deepEqual(h.opened,[]);
});
test('own notice opens only the fixed phone origin; external payload URLs are ignored',async()=>{
  const h=harness(['https://outside.example/phone/','https://workspace.example/other']);
  await h.click({data:{redcnDeviceDelivery:1,url:'https://outside.example/private-card'}});
  assert.deepEqual(h.opened,['https://workspace.example/phone/']);
});
test('unrelated or malformed notices are not handled',async()=>{
  const h=harness();assert.equal(await h.click({data:{}}),0);assert.equal(await h.click({tag:'untrusted'}),0);assert.deepEqual(h.opened,[]);
});
