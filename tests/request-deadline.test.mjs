import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFile} from 'node:fs/promises';
import {photoSourceUrl} from '../web/media-core.mjs';
import {localRequestTimeout} from '../web/request-deadline.mjs';

const deadlineSource=(await readFile(new URL('../web/request-deadline.mjs',import.meta.url),'utf8')).replace(/^export /gm,'');
function harness(extra={}){
  const timers=new Map();let sequence=0;
  const context=vm.createContext({AbortController,AbortSignal:{},URL,Headers,Request,Response,TextDecoder,Uint8Array,
    setTimeout(callback){const id=sequence++;timers.set(id,callback);return id;},clearTimeout(id){timers.delete(id);},...extra});
  vm.runInContext(deadlineSource+'\nglobalThis.deadline=withRequestDeadline;',context);
  return {context,timers,run:context.deadline,fire(){for(const [id,callback]of [...timers]){timers.delete(id);callback();}}};
}
function observedExternal(){
  const controller=new AbortController(),signal=controller.signal,added=new Set();
  const add=signal.addEventListener.bind(signal),remove=signal.removeEventListener.bind(signal);
  signal.addEventListener=(type,listener,...rest)=>{if(type==='abort')added.add(listener);return add(type,listener,...rest);};
  signal.removeEventListener=(type,listener,...rest)=>{if(type==='abort')added.delete(listener);return remove(type,listener,...rest);};
  return {controller,signal,added};
}

test('legacy AbortSignal without static methods works without installing a global polyfill',async()=>{
  const env=harness({AbortSignal:Object.freeze({})});let captured;
  assert.equal(await env.run(8000,signal=>{captured=signal;return 'paired';}),'paired');
  assert.equal(captured.aborted,false);assert.equal(env.context.AbortSignal.timeout,undefined);assert.equal(env.context.AbortSignal.any,undefined);assert.equal(env.timers.size,0);
});

test('successful request body completion clears timer zero and detaches external abort',async()=>{
  const env=harness(),external=observedExternal();let finish,scope,oldTimer;
  const pending=env.run(25,(signal,current)=>{scope=current;return new Promise(resolve=>{finish=resolve;});},{signal:external.signal});
  await Promise.resolve();assert.equal(env.timers.size,1);assert.equal(external.added.size,1);oldTimer=[...env.timers.values()][0];
  finish('decoded JSON');assert.equal(await pending,'decoded JSON');assert.equal(env.timers.size,0);assert.equal(external.added.size,0);
  oldTimer();external.controller.abort();assert.equal(scope.aborted,false);assert.equal(scope.signal.aborted,false);
});

test('deadline remains active while headers have arrived but response JSON is still pending',async()=>{
  const env=harness();let scope,entered;
  const ready=new Promise(resolve=>{entered=resolve;});
  const pending=env.run(30,async(signal,current)=>{scope=current;const response={json:()=>new Promise(()=>{})};entered();return response.json();});
  await ready;assert.equal(env.timers.size,1);env.fire();await assert.rejects(pending,error=>error.name==='TimeoutError');
  assert.equal(scope.timedOut,true);assert.equal(scope.aborted,true);assert.equal(scope.signal.aborted,true);assert.equal(env.timers.size,0);
});

test('an already cancelled external request never starts a fetch and preserves its reason',async()=>{
  const env=harness(),external=observedExternal(),reason=Error('User cancelled');external.controller.abort(reason);let calls=0;
  await assert.rejects(env.run(30,()=>{calls++;},{signal:external.signal}),error=>error===reason);
  assert.equal(calls,0);assert.equal(env.timers.size,0);assert.equal(external.added.size,0);
});

test('external cancellation propagates without AbortSignal.any and wins over a later deadline',async()=>{
  const env=harness(),external=observedExternal();let signal;
  const pending=env.run(30,current=>{signal=current;return new Promise(()=>{});},{signal:external.signal});await Promise.resolve();
  const reason=Error('Navigation replaced');external.controller.abort(reason);await assert.rejects(pending,error=>error===reason);
  assert.equal(signal.aborted,true);assert.equal(env.timers.size,0);assert.equal(external.added.size,0);
});

test('synchronous and asynchronous failures always release their request resources',async()=>{
  for(const operation of [()=>{throw Error('Bad headers');},async()=>{throw Error('Bad body');}]){
    const env=harness(),external=observedExternal();await assert.rejects(env.run(30,operation,{signal:external.signal}),/Bad/);
    assert.equal(env.timers.size,0);assert.equal(external.added.size,0);
  }
});

test('without AbortController the UI still has a deadline and existing external signals pass through',async()=>{
  const env=harness({AbortController:undefined});const pending=env.run(30,()=>new Promise(()=>{}));env.fire();await assert.rejects(pending,error=>error.name==='TimeoutError');assert.equal(env.timers.size,0);
  const external=observedExternal();let actual;await env.run(30,signal=>{actual=signal;return 1;},{signal:external.signal});assert.equal(actual,external.signal);assert.equal(external.added.size,0);
});

test('invalid timeouts fail before creating timers',async()=>{
  const env=harness();for(const milliseconds of [-1,NaN,Infinity,2147483648,'8000'])await assert.rejects(env.run(milliseconds,()=>1),error=>error.name==='RangeError');
  assert.equal(env.timers.size,0);
});

test('only synchronous photo analysis gets the model deadline plus transport margin',()=>{
  assert.equal(localRequestTimeout('photo',{action:'analyze'}),125000);
  for(const data of [undefined,{}, {action:'load'}, {action:'crop'}, {action:'accepted'}, {action:'attach'}])assert.equal(localRequestTimeout('photo',data),15000);
  for(const path of ['import','jobs/abc','cards/abc/analyze','photo/analyze','save'])assert.equal(localRequestTimeout(path,{action:'analyze'}),15000);
  assert.equal(localRequestTimeout('translate'),65000);
  assert.equal(localRequestTimeout('learning',{action:'generate-preview'}),65000);
  assert.equal(localRequestTimeout('learning',{action:'translate-preview'}),65000);
  assert.equal(localRequestTimeout('learning',{action:'save'}),15000);
  for(const path of ['card-export','dossier-import','listing-map','workspace-backup/inspect','cloud-backup','cloud-backup/upload','cloud-backup/restore','cloud-backup/list'])assert.equal(localRequestTimeout(path),120000);
});

test('actual Mac photo analysis client survives a 60s response and keeps the epoch guard',async()=>{
  const app=await readFile(new URL('../web/app.js',import.meta.url),'utf8');
  const localApi=app.split('\n').find(line=>line.startsWith('async function localApi('));
  assert.match(localApi,/withRequestDeadline\(localRequestTimeout\(path,data\),/);
  assert.match(app,/import \{[^}]*localRequestTimeout[^}]*\} from '\.\/request-deadline\.mjs'/);
  const photo=await readFile(new URL('../web/photo-ui.mjs',import.meta.url),'utf8');
  assert.match(photo,/const analysis=await api\('photo',\{action:'analyze',id:originalId\}\);\s*if\(epoch!==token\)break;/);
  let now=0,sequence=0,requestSignal,settled=false;const timers=new Map();
  const schedule=(callback,delay)=>{const id=sequence++;timers.set(id,{at:now+delay,callback});return id;};
  const advance=milliseconds=>{now=milliseconds;for(const [id,timer]of [...timers].sort((a,b)=>a[1].at-b[1].at))if(timer.at<=now&&timers.has(id)){timers.delete(id);timer.callback();}};
  const env=harness({setTimeout:schedule,clearTimeout:id=>timers.delete(id),
    document:{querySelector:()=>null},fetch:async(url,options)=>{assert.equal(url,'/api/local/photo');assert.equal(JSON.parse(options.body).action,'analyze');requestSignal=options.signal;return {ok:true,json:()=>new Promise(resolve=>schedule(()=>resolve({status:'review'}),60000))};}});
  vm.runInContext(localApi+'\nglobalThis.photoCall=localApi;',env.context);
  const pending=env.context.photoCall('photo',{action:'analyze',id:'a'.repeat(64)});pending.then(()=>{settled=true;});
  for(let n=0;n<8;n++)await Promise.resolve();
  assert([...timers.values()].some(timer=>timer.at===125000));
  advance(15000);for(let n=0;n<4;n++)await Promise.resolve();
  assert.equal(settled,false);assert.equal(requestSignal.aborted,false);
  advance(60000);assert.equal((await pending).status,'review');
  assert.equal(requestSignal.aborted,false);assert.equal(timers.size,0);
});

async function serviceWorker(fetch){
  const handlers=new Map();let cacheReads=0;
  const env=harness({self:{location:{origin:'https://app.test'},addEventListener(type,handler){handlers.set(type,handler);}},fetch,caches:{open(){cacheReads++;throw Error('Navigation must not read private caches');}}});
  vm.runInContext(await readFile(new URL('../web/sw.js',import.meta.url),'utf8'),env.context);
  return {...env,handlers,get cacheReads(){return cacheReads;}};
}
function navigate(env){let response;env.handlers.get('fetch')({request:{method:'GET',url:'https://app.test/phone/',mode:'navigate',signal:new AbortController().signal},respondWith(value){response=value;}});return response;}

test('service worker navigation works without timeout static API, consumes body and never caches it',async()=>{
  const env=await serviceWorker(async(_request,options)=>{assert.equal(typeof options.signal.addEventListener,'function');return new Response('PRIVATE PHONE HTML',{headers:{'Content-Type':'text/html','Content-Encoding':'gzip','Content-Length':'999','Cache-Control':'no-store'}});});
  const response=await navigate(env);assert.equal(await response.text(),'PRIVATE PHONE HTML');assert.equal(response.status,200);assert.equal(response.headers.get('cache-control'),'no-store');assert.equal(response.headers.get('content-encoding'),null);assert.equal(response.headers.get('content-length'),null);assert.equal(env.cacheReads,0);assert.equal(env.timers.size,0);
});

test('service worker timeout returns only the constant offline page and clears its timer',async()=>{
  const env=await serviceWorker(()=>new Promise(()=>{})),pending=navigate(env);env.fire();const response=await pending;
  assert.equal(response.status,503);assert.equal(response.headers.get('cache-control'),'no-store');assert.match(await response.text(),/Сейчас Mac недоступен/);assert.equal(env.cacheReads,0);assert.equal(env.timers.size,0);
});

test('worker photo endpoint uses the same compatible body deadline and preserves image bytes',async()=>{
  const bytes=new Uint8Array([137,80,78,71,13,10,26,10,0]);
  const env=harness({photoSourceUrl,fetch:async(_target,options)=>{assert.equal(typeof options.signal.addEventListener,'function');return new Response(bytes,{headers:{'Content-Type':'image/png'}});}});
  const cloud=(await readFile(new URL('../worker/cloud-backup.mjs',import.meta.url),'utf8')).replace(/^export /gm,'');
  const worker=(await readFile(new URL('../worker/server.js',import.meta.url),'utf8')).replace('export default','globalThis.workerApi=');vm.runInContext(cloud+'\n'+worker,env.context);
  const response=await env.context.workerApi.fetch(new Request('https://app.test/api/photo?url='+encodeURIComponent('https://images.cdn-cian.ru/images/123.jpg')));
  assert.equal(response.status,200);assert.deepEqual(new Uint8Array(await response.arrayBuffer()),bytes);assert.equal(env.timers.size,0);
});

test('all shipped request consumers avoid unguarded AbortSignal static calls',async()=>{
  for(const file of ['web/phone.mjs','web/photo-ui.mjs','web/sw.js','worker/server.js'])assert(!/AbortSignal\.(?:timeout|any)\s*\(/.test(await readFile(new URL('../'+file,import.meta.url),'utf8')),file);
});
