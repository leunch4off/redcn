import test from 'node:test';
import assert from 'node:assert/strict';
import {probePageBuild,createBuildMonitor,setupBuildNotice,buildEntryPoint} from '../web/build-notice.mjs';

const oldBuild='a'.repeat(64),newBuild='b'.repeat(64),origin='https://example.test';
const shell=build=>'<!doctype html><html><head><meta charset="utf-8"><meta name="redcn-build" content="'+build+'">';
const clean=()=>({known:true,dirty:false,busy:false});
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
function streamResponse(parts){
 let reads=0,cancelled=0;
 const stream=new ReadableStream({pull(controller){if(reads>=parts.length){controller.close();return;}controller.enqueue(new TextEncoder().encode(parts[reads++]));},cancel(){cancelled++;}},{highWaterMark:0});
 return {response:new Response(stream,{headers:{'Content-Type':'text/html; charset=utf-8'}}),get reads(){return reads;},get cancelled(){return cancelled;}};
}

test('same-origin build probe reads only the head and cancels before private body bytes',async()=>{
 const response=streamResponse([shell(newBuild),'<body>PRIVATE_CARD_DATA</body>']);let request;
 const result=await probePageBuild({url:origin+'/phone/#pair=PRIVATE',origin,fetch:async(url,options)=>{request={url,options};return response.response;}});
 assert.deepEqual(result,{buildId:newBuild});assert.equal(response.reads,1);assert.equal(response.cancelled,1);
 assert.equal(request.url,origin+'/phone/');assert.equal(request.options.cache,'no-store');assert.equal(request.options.credentials,'same-origin');assert.equal(request.options.redirect,'error');
 assert.doesNotMatch(JSON.stringify(result),/PRIVATE/);
});

test('split metadata and reordered single-quoted attributes are recognized without reading the body',async()=>{
 const response=streamResponse(["<head><meta content='"+newBuild.slice(0,30),newBuild.slice(30)+"' name='redcn-build'></head>",'PRIVATE_BODY']);
 assert.deepEqual(await probePageBuild({url:'/',origin,fetch:async()=>response.response}),{buildId:newBuild});
 assert.equal(response.reads,2);assert.equal(response.cancelled,1);
});

test('a build tag beyond the 4 KiB head limit does not get read as a version',async()=>{
 const response=streamResponse([' '.repeat(5000)+shell(newBuild),'PRIVATE_BODY']);
 await assert.rejects(probePageBuild({url:'/',origin,fetch:async()=>response.response}),/не сообщает свою версию/);
 assert.equal(response.reads,1);assert.equal(response.cancelled,1);
});

test('cross-origin URLs, credentials, redirects, JSON and unversioned login HTML are rejected',async()=>{
 let calls=0;
 for(const url of ['https://other.test/','https://user:password@example.test/','file:///page.html'])await assert.rejects(probePageBuild({url,origin,fetch:async()=>{calls++;}}),/Неверный адрес/);
 assert.equal(calls,0);
 const redirected=streamResponse([shell(newBuild)]);Object.defineProperty(redirected.response,'redirected',{value:true});
 await assert.rejects(probePageBuild({url:'/',origin,fetch:async()=>redirected.response}),/недоступна/);
 assert.equal(redirected.cancelled,1);assert.equal(redirected.reads,0);
 const foreign=streamResponse([shell(newBuild)]);Object.defineProperty(foreign.response,'url',{value:'https://other.test/login'});
 await assert.rejects(probePageBuild({url:'/',origin,fetch:async()=>foreign.response}),/недоступна/);
 await assert.rejects(probePageBuild({url:'/',origin,fetch:async()=>new Response(JSON.stringify({buildId:newBuild}),{headers:{'Content-Type':'application/json'}})}),/недоступна/);
 await assert.rejects(probePageBuild({url:'/',origin,fetch:async()=>new Response('<html><head><title>Sign in</title></head></html>',{headers:{'Content-Type':'text/html'}})}),/не сообщает свою версию/);
});

test('the probe deadline includes a stalled response body and aborts its fetch',async()=>{
 let aborted=false;
 await assert.rejects(probePageBuild({url:'/',origin,timeout:15,fetch:async(_url,{signal})=>{
  const stream=new ReadableStream({start(controller){signal.addEventListener('abort',()=>{aborted=true;controller.error(Error('aborted'));});}});
  return new Response(stream,{headers:{'Content-Type':'text/html'}});
 }}),error=>error.name==='TimeoutError');
 assert.equal(aborted,true);
});

test('non-streaming responses cannot silently download a multi-megabyte shell',async()=>{
 let reads=0;
 const response={ok:true,headers:new Headers({'Content-Type':'text/html','Content-Length':'5000000'}),text:async()=>{reads++;return shell(newBuild);}};
 await assert.rejects(probePageBuild({url:'/',origin,fetch:async()=>response}),/не смог проверить/);assert.equal(reads,0);
 response.headers.set('Content-Length',String(shell(newBuild).length));
 assert.deepEqual(await probePageBuild({url:'/',origin,fetch:async()=>response}),{buildId:newBuild});assert.equal(reads,1);
});

test('detecting a different build only notifies; explicit safe reload checkpoints the draft',async()=>{
 let navigation=0,checkpoints=0;const observed=[];
 const monitor=createBuildMonitor({currentBuild:oldBuild,probe:async()=>({buildId:newBuild}),getActivity:clean,onBeforeReload:()=>{checkpoints++;return true;},reload:()=>{navigation++;},onState:value=>observed.push(value)});
 await monitor.check();assert.equal(monitor.snapshot().outdated,true);assert.equal(monitor.snapshot().currentBuild,oldBuild);assert.equal(navigation,0);assert.equal(checkpoints,0);
 assert.equal(await monitor.reload(),true);assert.equal(navigation,1);assert.equal(checkpoints,1);
 assert(observed.some(value=>value.status==='outdated'));
});

test('dirty, busy, unknown and throwing activity readers fail closed without checkpointing',async()=>{
 for(const getActivity of [undefined,()=>null,()=>({dirty:false,busy:false}),()=>({known:true,dirty:false}),()=>({known:true,dirty:true,busy:false}),()=>({known:true,dirty:false,busy:true}),()=>{throw Error('Not initialized');}]){
  let checkpoints=0,navigation=0;
  const monitor=createBuildMonitor({currentBuild:oldBuild,probe:async()=>({buildId:newBuild}),getActivity,onBeforeReload:()=>{checkpoints++;return true;},reload:()=>{navigation++;}});
  await monitor.check();assert.equal(await monitor.reload(),false);assert.equal(checkpoints,0);assert.equal(navigation,0);
 }
});

test('a new edit after an enabled button was rendered still blocks its reload action',async()=>{
 let dirty=false,navigation=0;
 const monitor=createBuildMonitor({currentBuild:oldBuild,probe:async()=>({buildId:newBuild}),getActivity:()=>({...clean(),dirty}),reload:()=>{navigation++;}});
 await monitor.check();assert.equal(monitor.snapshot().canReload,true);dirty=true;
 assert.equal(await monitor.reload(),false);assert.equal(navigation,0);
});

test('editing during an asynchronous draft checkpoint cancels reload and preserves the tab',async()=>{
 const checkpoint=deferred();let dirty=false,navigation=0;
 const monitor=createBuildMonitor({currentBuild:oldBuild,probe:async()=>({buildId:newBuild}),getActivity:()=>({...clean(),dirty}),onBeforeReload:()=>checkpoint.promise,reload:()=>{navigation++;}});
 await monitor.check();const pending=monitor.reload();assert.equal(monitor.snapshot().reloading,true);
 dirty=true;checkpoint.resolve(true);assert.equal(await pending,false);assert.equal(navigation,0);assert.equal(monitor.snapshot().outdated,true);
});

test('a failed or unconfirmed draft checkpoint never navigates',async()=>{
 for(const onBeforeReload of [()=>false,()=>undefined,async()=>{throw Error('Quota exceeded');}]){
  let navigation=0;const monitor=createBuildMonitor({currentBuild:oldBuild,probe:async()=>({buildId:newBuild}),getActivity:clean,onBeforeReload,reload:()=>{navigation++;}});
  await monitor.check();assert.equal(await monitor.reload(),false);assert.equal(navigation,0);assert.equal(monitor.snapshot().checkpointFailed,true);
 }
});

test('concurrent resume checks share one request and repeated events are throttled',async()=>{
 const response=deferred();let calls=0,clock=100;
 const monitor=createBuildMonitor({currentBuild:oldBuild,getActivity:clean,now:()=>clock,probe:()=>{calls++;return response.promise;}});
 const first=monitor.check(),second=monitor.check({force:true});assert.equal(first,second);
 await Promise.resolve();assert.equal(calls,1);response.resolve({buildId:oldBuild});await first;
 clock=1000;await monitor.check();assert.equal(calls,1);
 clock=30101;await monitor.check();assert.equal(calls,2);
});

test('an offline or malformed later probe cannot erase an observed update or leak raw errors',async()=>{
 let phase=0;
 const monitor=createBuildMonitor({currentBuild:oldBuild,getActivity:clean,probe:async()=>{if(phase===0)return {buildId:newBuild};if(phase===1)throw Error('PRIVATE_HOST_OR_TOKEN');return {buildId:'invalid'};}});
 await monitor.check();for(phase=1;phase<=2;phase++){
  await monitor.check({force:true});const state=monitor.snapshot();assert.equal(state.outdated,true);assert.equal(state.availableBuild,newBuild);assert.equal(state.failed,true);assert.doesNotMatch(JSON.stringify(state),/PRIVATE/);
 }
});

test('destroy aborts the in-flight check and ignores its late result',async()=>{
 const response=deferred();let changes=0,signal,navigation=0;
 const monitor=createBuildMonitor({currentBuild:oldBuild,getActivity:clean,probe:value=>{signal=value.signal;return response.promise;},onState:()=>{changes++;},reload:()=>{navigation++;}});
 const pending=monitor.check();await Promise.resolve();monitor.destroy();assert.equal(signal.aborted,true);
 const before=changes;response.resolve({buildId:newBuild});await pending;assert.equal(changes,before);assert.equal(await monitor.reload(),false);assert.equal(navigation,0);
});

test('a verified rollback to the loaded build clears the update notice; missing client identity does not',async()=>{
 let available=newBuild;
 const monitor=createBuildMonitor({currentBuild:oldBuild,getActivity:clean,probe:async()=>({buildId:available})});
 await monitor.check();assert.equal(monitor.snapshot().outdated,true);available=oldBuild;await monitor.check({force:true});assert.equal(monitor.snapshot().outdated,false);assert.equal(monitor.snapshot().canReload,false);
 const unversioned=createBuildMonitor({currentBuild:'',getActivity:clean,probe:async()=>({buildId:oldBuild})});await unversioned.check();assert.equal(unversioned.snapshot().outdated,true);
});

test('entrypoint diagnostics distinguish a disconnected phone and both separate template editors',()=>{
 for(const hosting of ['github-pages','sites']){
  const entry=buildEntryPoint({hosting});assert.match(entry.description,/по шаблонам/);assert.match(entry.description,/Mac не подключены/);
 }
 assert.match(buildEntryPoint({pathname:'/phone/pair',connected:false}).description,/пока не подключён/);
 assert.match(buildEntryPoint({pathname:'/phone/',connected:true}).description,/Телефон подключён/);
 assert.equal(buildEntryPoint({hosting:'local'}).kind,'desktop');assert.equal(buildEntryPoint({hosting:'unknown'}).kind,'unknown');
});

class TestElement extends EventTarget{
 constructor(tag){super();this.tagName=tag.toUpperCase();this.dataset={};this.children=[];this.attributes={};this.textContent='';}
 append(...children){for(const child of children){child.parent=this;this.children.push(child);}}
 setAttribute(key,value){this.attributes[key]=value;}
 remove(){if(this.parent)this.parent.children=this.parent.children.filter(child=>child!==this);}
}
function noticeHarness(){
 const document=new EventTarget(),window=new EventTarget(),mount=new TestElement('main'),head=new TestElement('head');
 const timers=new Map();let sequence=0,clock=0,navigation=0,requests=0,available=oldBuild,dirty=true;
 document.head=head;document.hidden=false;document.createElement=tag=>new TestElement(tag);document.getElementById=id=>head.children.find(row=>row.id===id)||null;
 document.querySelector=selector=>selector.includes('redcn-build')?{content:oldBuild}:null;
 window.setTimeout=(callback,delay)=>{const id=sequence++;timers.set(id,{callback,delay});return id;};window.clearTimeout=id=>timers.delete(id);
 const location={origin,href:origin+'/phone/pair?attempt=1#PRIVATE_PAIR',pathname:'/phone/pair',reload(){navigation++;}};
 const notice=setupBuildNotice({mount,document,window,location,mode:'phone',now:()=>clock,getActivity:()=>({...clean(),dirty,connected:true}),onBeforeReload:()=>true,fetch:async(url)=>{requests++;assert.equal(url,origin+'/phone/');return streamResponse([shell(available),'PRIVATE_BODY']).response;}});
 const panel=mount.children[0],warning=panel.children[0],button=warning.children[2];
 return {notice,document,window,mount,timers,panel,warning,button,get navigation(){return navigation;},get requests(){return requests;},set clock(value){clock=value;},set available(value){available=value;},set dirty(value){dirty=value;}};
}

test('resuming a real notice after a server update warns without losing unsaved phone edits',async()=>{
 const env=noticeHarness();await env.notice.check();assert.equal(env.warning.hidden,true);assert.equal(env.requests,1);
 env.document.hidden=true;env.available=newBuild;env.clock=31000;env.window.dispatchEvent(new Event('focus'));await Promise.resolve();assert.equal(env.requests,1);
 env.document.hidden=false;env.document.dispatchEvent(new Event('visibilitychange'));env.window.dispatchEvent(new Event('pageshow'));env.window.dispatchEvent(new Event('focus'));await env.notice.check();
 assert.equal(env.requests,2);assert.equal(env.warning.hidden,false);assert.equal(env.warning.children[0].textContent,'Доступно обновление');assert.equal(env.button.disabled,true);assert.equal(env.navigation,0);
 assert.equal(await env.button.onclick(),false);env.dirty=false;env.document.dispatchEvent(new Event('input'));await Promise.resolve();assert.equal(env.button.disabled,false);
 assert.equal(await env.button.onclick(),true);assert.equal(env.navigation,1);env.notice.destroy();assert.equal(env.timers.size,0);assert.equal(env.mount.children.length,0);
});

test('a hidden tab performs no periodic request and disposed notice listeners do nothing',async()=>{
 const env=noticeHarness();await env.notice.check();env.document.hidden=true;env.clock=61000;
 const [id,timer]=[...env.timers][0];env.timers.delete(id);timer.callback();await Promise.resolve();assert.equal(env.requests,1);
 env.notice.destroy();env.document.hidden=false;env.window.dispatchEvent(new Event('online'));env.document.dispatchEvent(new Event('visibilitychange'));await Promise.resolve();assert.equal(env.requests,1);assert.equal(env.timers.size,0);
});
