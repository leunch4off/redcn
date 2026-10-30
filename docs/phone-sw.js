// A request owns its timer and abort listener, including response-body reads.
// Older Safari has AbortController but no static AbortSignal timeout/any APIs.
function rdAbortReason(){const error=Error('The request was cancelled.');error.name='AbortError';return error;}
function rdTimeoutReason(){const error=Error('The request timed out.');error.name='TimeoutError';return error;}

async function withRequestDeadline(milliseconds,operation,{signal:externalSignal}={}){
  if(typeof milliseconds!=='number'||!Number.isFinite(milliseconds)||milliseconds<0||milliseconds>2147483647)throw RangeError('Invalid request deadline.');
  if(typeof operation!=='function')throw TypeError('A request operation is required.');
  if(externalSignal!=null&&(typeof externalSignal.addEventListener!=='function'||typeof externalSignal.removeEventListener!=='function'))throw TypeError('Invalid cancellation signal.');
  let controller=null;try{if(typeof AbortController==='function')controller=new AbortController();}catch{}
  let timer=null,disposed=false,aborted=false,timedOut=false,reason,listening=false,rejectAbort;
  const cancellation=new Promise((_,reject)=>{rejectAbort=reject;});
  const detach=()=>{if(listening){externalSignal.removeEventListener('abort',onExternalAbort);listening=false;}};
  const clearTimer=()=>{if(timer!==null){clearTimeout(timer);timer=null;}};
  function abort(value,timeout=false){
    if(disposed||aborted)return;aborted=true;timedOut=timeout;reason=value;clearTimer();detach();
    // Reject our promise first: old WebKit may replace the controller's reason
    // with a generic AbortError while it aborts a fetch or response body.
    rejectAbort(reason);
    if(controller)try{controller.abort(reason);}catch{try{controller.abort();}catch{}}
  }
  function onExternalAbort(){abort(externalSignal.reason===undefined?rdAbortReason():externalSignal.reason);}
  const scope={signal:controller?.signal||externalSignal||undefined,get aborted(){return aborted;},get timedOut(){return timedOut;},get reason(){return reason;},throwIfAborted(){if(aborted)throw reason;}};
  try{
    if(externalSignal?.aborted)onExternalAbort();
    else{
      if(externalSignal){listening=true;externalSignal.addEventListener('abort',onExternalAbort,{once:true});if(externalSignal.aborted)onExternalAbort();}
      if(!aborted)timer=setTimeout(()=>abort(rdTimeoutReason(),true),Math.ceil(milliseconds));
    }
    const result=Promise.resolve().then(()=>{scope.throwIfAborted();return operation(scope.signal,scope);});
    return await Promise.race([cancellation,result]);
  }finally{disposed=true;clearTimer();detach();}
}

/* Phone scope only. Private cards, media, pairing and API responses are NEVER cached. */
const PHONE_PUBLIC_CACHE='redcn-phone-public-v18';
const PHONE_PUBLIC_ASSETS=new Set(['/phone/manifest.webmanifest','/phone/icons/icon-192.png','/phone/icons/icon-512.png','/phone/icons/apple-touch-icon.png']);
const PHONE_OFFLINE_PAGE=`<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="theme-color" content="#34543a"><title>МОСТ · нет связи</title><style>body{margin:0;background:#f5f5f0;color:#263d2b;font:16px/1.6 system-ui;padding:calc(60px + env(safe-area-inset-top)) 24px 40px}main{max-width:380px;margin:auto}h1{font-size:30px;line-height:1.15}p{color:#657260}button{width:100%;border:0;border-radius:14px;background:#34543a;color:white;font:600 16px system-ui;min-height:50px;padding:12px}</style><main><p>МОСТ · СВЯЗЬ С КОМПЬЮТЕРОМ</p><h1>Сейчас Mac недоступен</h1><p>Проверьте интернет и включённый сервер МОСТ на Mac. Для адреса 192.168… нужна домашняя сеть; внешняя HTTPS-ссылка работает через интернет.</p><p>Когда связь вернётся, готовые карточки и фото снова появятся здесь.</p><button onclick="location.reload()">Проверить связь</button></main></html>`;
self.addEventListener('install',event=>{event.waitUntil((async()=>{const cache=await caches.open(PHONE_PUBLIC_CACHE);await Promise.all([...PHONE_PUBLIC_ASSETS].map(async path=>{try{const response=await fetch(path,{credentials:'omit',cache:'reload'});if(response.ok&&response.type!=='opaque')await cache.put(path,response);}catch{}}));await self.skipWaiting();})());});
self.addEventListener('activate',event=>{event.waitUntil((async()=>{for(const key of await caches.keys())if(key.startsWith('redcn-phone-public-')&&key!==PHONE_PUBLIC_CACHE)await caches.delete(key);await self.clients.claim();})());});
self.addEventListener('fetch',event=>{
  const request=event.request,url=new URL(request.url);
  if(request.method!=='GET'||url.origin!==self.location.origin)return;
  if(PHONE_PUBLIC_ASSETS.has(url.pathname)&&!url.search){event.respondWith((async()=>{const cache=await caches.open(PHONE_PUBLIC_CACHE);try{const response=await fetch(request,{credentials:'omit',cache:'no-cache'});if(response.ok&&response.type!=='opaque')await cache.put(url.pathname,response.clone());return response;}catch{const cached=await cache.match(url.pathname);return cached||new Response('',{status:503});}})());return;}
  // Do not cache or replay a personalized HTML response. Offline markup is a
  // constant explanation and contains no session, source URL or listing facts.
  if(request.mode==='navigate'&&['/phone/','/phone/index.html'].includes(url.pathname))event.respondWith(withRequestDeadline(8000,async(signal,scope)=>{
    const response=await fetch(request,{signal});if(response.type==='opaqueredirect'||response.type==='opaque')return response;
    const body=response.body?await response.arrayBuffer():null;scope.throwIfAborted();
    const headers=new Headers(response.headers);headers.delete('Content-Encoding');headers.delete('Content-Length');
    return new Response(body,{status:response.status,statusText:response.statusText,headers});
  },{signal:request.signal}).catch(()=>new Response(PHONE_OFFLINE_PAGE,{status:503,headers:{'Content-Type':'text/html; charset=utf-8','Cache-Control':'no-store'}})));
});
