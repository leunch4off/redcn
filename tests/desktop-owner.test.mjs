import test from 'node:test';
import assert from 'node:assert/strict';
import {bootstrapDesktopOwner} from '../web/desktop-owner.mjs';

function fixture({hosting='local',token='',hash='',sessionPath=''}={}){
 const elements=[],events=[],meta={content:token},memory=new Map();
 if(sessionPath)memory.set('redcn.owner-session.'+'a'.repeat(64),sessionPath);
 const doc={querySelector:q=>q.includes('hosting')?{content:hosting}:q.includes('workspace')?{content:'a'.repeat(64)}:meta,
  getElementById:id=>elements.find(e=>e.id===id),body:{append:e=>elements.push(e)},
  createElement:tag=>({tag,style:{},children:[],setAttribute(){},append(...v){this.children.push(...v);}})};
 return {document:doc,location:{hash,pathname:'/listing/'+'a'.repeat(32),search:''},
  history:{replaceState(...args){events.push(['clear',...args]);}},
  storage:{getItem:k=>memory.get(k)||null,setItem:(k,v)=>memory.set(k,v),removeItem:k=>memory.delete(k)},memory,elements,events,meta};
}

test('static deployments never exchange local owner credentials',async()=>{
 const f=fixture({hosting:'github-pages',hash:'#redcn-owner='+'t'.repeat(43)});
 assert.equal(await bootstrapDesktopOwner({...f,fetch:()=>{throw Error('Must not contact an API');}}),true);
 assert.equal(f.events[0][0],'clear');
});

test('an existing owner cookie needs no extra exchange and still clears fragments',async()=>{
 const f=fixture({token:'fixture-only',hash:'#redcn-owner='+'t'.repeat(43)});
 assert.equal(await bootstrapDesktopOwner({...f,fetch:()=>{throw Error('Must not exchange again');}}),true);
 assert.equal(f.events.length,1);assert.equal(f.meta.content,'fixture-only');
});

test('fragment is cleared before the bounded same-origin claim and private initialization',async()=>{
 const value='t'.repeat(43),f=fixture({hash:'#redcn-owner='+value});
 assert.equal(await bootstrapDesktopOwner({...f,fetch:async(path,options)=>{
  assert.equal(f.events[0][0],'clear');assert.equal(options.credentials,'same-origin');assert.ok(options.signal);
  if(path==='/api/local/desktop-access'){
   assert.equal(options.method,'POST');assert.deepEqual(JSON.parse(options.body),{token:value});
   return{ok:true,json:async()=>({ok:true,sessionPath:'/owner/'+'b'.repeat(32)+'/access'})};
  }
  assert.equal(path,'/owner/'+'b'.repeat(32)+'/access');assert.equal(options.method,undefined);
  return{ok:true,json:async()=>({token:value})};
 }}),true);
 assert.equal(f.meta.content,value);assert.equal(f.elements.length,0);
 assert.ok([...f.memory.values()].every(item=>!item.includes(value)));
});

test('plain reload resumes only through the private cookie path, not a stored credential',async()=>{
 const path='/owner/'+'b'.repeat(32)+'/access',f=fixture({sessionPath:path});
 assert.equal(await bootstrapDesktopOwner({...f,fetch:async(url,options)=>{
  assert.equal(url,path);assert.equal(options.credentials,'same-origin');assert.equal(options.body,undefined);
  return{ok:true,json:async()=>({token:'t'.repeat(43)})};
 }}),true);
 assert.equal(f.meta.content,'t'.repeat(43));assert.deepEqual([...f.memory.values()],[path]);
});

test('missing and invalid owner access shows a completed local gate without private requests',async()=>{
 for(const hash of ['','#redcn-owner=invalid']){
  const f=fixture({hash});bootstrapDesktopOwner({...f,fetch:()=>{throw Error('No credential');}});
  assert.equal(f.elements[0].id,'desktopOwnerGate');
  const text=f.elements[0].children.map(x=>x.textContent).join(' ');
  assert.match(text,/open-local-workspace.py/);assert.doesNotMatch(text,/invalid/);
 }
});

test('a rejected claim never exposes a credential or the server error in the access screen',async()=>{
 const secret='s'.repeat(43),f=fixture({hash:'#redcn-owner='+secret});
 bootstrapDesktopOwner({...f,fetch:async()=>({ok:false})});
 await new Promise(resolve=>setImmediate(resolve));
 assert.equal(f.meta.content,'');assert.equal(f.elements[0].id,'desktopOwnerGate');
 assert.ok(!f.elements[0].children.map(x=>x.textContent).join(' ').includes(secret));
});
