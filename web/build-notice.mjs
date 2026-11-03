import {withRequestDeadline} from './request-deadline.mjs';

const bnBuildId=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value)?value:null;
const bnHeadLimit=4096;

function bnHeadBuild(head){
 for(const tag of head.match(/<meta\b[^>]*>/gi)||[]){
  const attributes=Object.create(null);
  for(const match of tag.matchAll(/([\w-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+))/g))attributes[match[1].toLowerCase()]=match[2]??match[3]??match[4];
  if(attributes.name?.toLowerCase()==='redcn-build')return bnBuildId(attributes.content);
 }
 return null;
}

// Only a small, public HTML prefix is read. The rest of the page, including
// any workspace metadata, is neither retained nor exposed by this probe.
export async function probePageBuild({url,origin=globalThis.location?.origin,fetch:fetcher=globalThis.fetch,signal,timeout=5000}={}){
 const target=new URL(url,origin);
 if(!/^https?:$/.test(target.protocol)||target.origin!==origin||target.username||target.password)throw Error('Неверный адрес проверки версии.');
 target.hash='';
 return withRequestDeadline(timeout,async(requestSignal,scope)=>{
  const response=await fetcher(target.href,{method:'GET',credentials:'same-origin',cache:'no-store',redirect:'error',headers:{Accept:'text/html'},signal:requestSignal});
  if(!response.ok||response.redirected||response.url&&new URL(response.url,target).origin!==target.origin||!/^text\/html(?:\s*;|$)/i.test(response.headers.get('Content-Type')||'')){
   try{await response.body?.cancel();}catch{}
   throw Error('Версия страницы недоступна.');
  }
  let head='';
  if(response.body&&typeof response.body.getReader==='function'){
   const reader=response.body.getReader(),decoder=typeof TextDecoder==='function'?new TextDecoder():null;
   let bytes=0;
   try{
    while(bytes<bnHeadLimit){
     const chunk=await reader.read();scope.throwIfAborted();if(chunk.done)break;
     const part=chunk.value.subarray(0,bnHeadLimit-bytes);bytes+=part.byteLength;
     if(decoder)head+=decoder.decode(part,{stream:true});else for(const byte of part)head+=String.fromCharCode(byte);
     const buildId=bnHeadBuild(head);if(buildId)return {buildId};
     if(/<\/head\s*>/i.test(head))break;
    }
   }finally{try{await reader.cancel();}catch{}}
  }else{
   // An older response implementation may lack streams. Never read a large
   // shell in that case merely to check its version.
   const length=Number(response.headers.get('Content-Length'));
   if(!Number.isInteger(length)||length<=0||length>bnHeadLimit)throw Error('Этот браузер не смог проверить версию страницы.');
   head=(await response.text()).slice(0,bnHeadLimit);scope.throwIfAborted();
  }
  const buildId=bnHeadBuild(head);if(!buildId)throw Error('Страница не сообщает свою версию.');
  return {buildId};
 },{signal});
}

function bnActivity(read){
 try{
  const value=read();
  return {known:value?.known===true&&typeof value.dirty==='boolean'&&typeof value.busy==='boolean',dirty:value?.dirty!==false,busy:value?.busy!==false,connected:typeof value?.connected==='boolean'?value.connected:null};
 }catch{return {known:false,dirty:true,busy:true,connected:null};}
}

// A newly detected build never reloads the tab. Even an explicit click checks
// live activity again after the optional draft checkpoint has completed.
export function createBuildMonitor({currentBuild,probe,getActivity=()=>({known:false}),onBeforeReload=()=>true,reload=()=>{},onState=()=>{},now=()=>Date.now(),minimumInterval=30000}={}){
 const loaded=bnBuildId(currentBuild);
 let available=null,checkedAt=null,lastAttempt=null,checking=false,reloading=false,destroyed=false,failed=false,checkpointFailed=false,pending=null,controller=null;
 const state=()=>{
  const activity=bnActivity(getActivity),outdated=!!available&&available!==loaded;
  return {currentBuild:loaded,availableBuild:available,checkedAt,checking,reloading,failed,checkpointFailed,outdated,status:outdated?'outdated':failed?'unavailable':available?'current':'unchecked',activity,canReload:outdated&&!checking&&!reloading&&activity.known&&!activity.dirty&&!activity.busy};
 };
 const publish=()=>{const value=state();if(!destroyed)onState(value);return value;};
 function check({force=false}={}){
  if(destroyed)return Promise.resolve(state());
  if(pending)return pending;
  if(!force&&lastAttempt!==null&&now()-lastAttempt<minimumInterval)return Promise.resolve(publish());
  lastAttempt=now();checking=true;
  try{controller=typeof AbortController==='function'?new AbortController():null;}catch{controller=null;}
  const request=controller;
  let task;
  task=Promise.resolve().then(()=>probe({signal:request?.signal})).then(value=>{
   if(destroyed)return;
   const buildId=bnBuildId(value?.buildId);if(!buildId)throw Error('Missing build identity');
   available=buildId;checkedAt=now();failed=false;checkpointFailed=false;
  }).catch(()=>{if(!destroyed)failed=true;}).finally(()=>{
   if(pending===task)pending=null;
   if(controller===request)controller=null;
   checking=false;publish();
  }).then(()=>state());
  pending=task;publish();return task;
 }
 async function requestReload(){
  if(destroyed||!state().canReload){publish();return false;}
  reloading=true;checkpointFailed=false;publish();
  try{
   if(await onBeforeReload()!==true){checkpointFailed=true;return false;}
   if(destroyed)return false;
   const activity=bnActivity(getActivity);
   if(!available||available===loaded||!activity.known||activity.dirty||activity.busy)return false;
   reload();return true;
  }catch{checkpointFailed=true;return false;}
  finally{reloading=false;publish();}
 }
 return {check,reload:requestReload,updateState:publish,snapshot:state,destroy(){destroyed=true;controller?.abort();}};
}

export function buildEntryPoint({mode,hosting='',pathname='/',connected=null}={}){
 const kind=mode||(/^\/phone(?:\/|$)/.test(pathname)?'phone':hosting==='local'?'desktop':hosting==='github-pages'?'pages':hosting==='sites'?'sites':'unknown');
 const entries={
  desktop:{label:'Редактор на Mac',description:'Объявления сохраняются на этом Mac. Индивидуальный текст создаёт настроенная на нём модель.'},
  phone:{label:'Телефон',description:connected===true?'Телефон подключён к объявлениям на Mac. Создание текста и анализ фото выполняются на компьютере.':connected===false?'Телефон пока не подключён к Mac. Откройте ссылку подключения на компьютере.':'Это страница телефона. Подключение к Mac проверяется при входе.'},
  pages:{label:'Веб-редактор · GitHub Pages',description:'Здесь текст собирается по шаблонам. Модель и объявления с Mac не подключены; для индивидуального текста и анализа фото откройте редактор на Mac или подключённый телефон.'},
  sites:{label:'Веб-редактор · Sites',description:'Здесь текст собирается по шаблонам. Модель и объявления с Mac не подключены; для индивидуального текста и анализа фото откройте редактор на Mac или подключённый телефон.'},
  unknown:{label:'Отдельная страница',description:'Подключение этой страницы к объявлениям и модели на Mac не подтверждено.'}
 };
 return {kind:entries[kind]?kind:'unknown',...(entries[kind]||entries.unknown)};
}

export function setupBuildNotice({mount,mode,getActivity,onBeforeReload,document:doc=globalThis.document,window:win=globalThis.window,location:loc=globalThis.location,fetch:fetcher=globalThis.fetch,interval=60000,now}={}){
 if(!mount||!doc||!win)return null;
 const make=(tag,text='')=>{const node=doc.createElement(tag);node.textContent=text;return node;};
 const panel=make('section'),notice=make('div'),title=make('strong','Доступно обновление'),message=make('p'),reload=make('button','Обновить страницу'),details=make('details'),summary=make('summary'),context=make('p'),version=make('p'),check=make('button','Проверить обновление');
 panel.dataset.buildNotice='1';panel.className='build-notice';notice.dataset.buildUpdate='1';notice.setAttribute('role','status');notice.setAttribute('aria-live','polite');
 for(const button of [reload,check]){button.type='button';button.className='btn';}
 notice.append(title,message,reload);details.append(summary,context,version,check);panel.append(notice,details);mount.append(panel);
 if(!doc.getElementById('redcn-build-notice-style')){
  const style=make('style');style.id='redcn-build-notice-style';style.textContent='.build-notice{box-sizing:border-box;margin:12px 0;padding:12px 14px;border:1px solid #cbd2c6;border-radius:12px;background:#f8faf5;color:#304032;font:14px/1.5 system-ui;overflow-wrap:anywhere}.build-notice [data-build-update]{padding:2px 0 12px}.build-notice [data-build-update][hidden]{display:none}.build-notice p{margin:8px 0}.build-notice summary{cursor:pointer}.build-notice button{max-width:100%;min-height:44px;white-space:normal}.build-notice button:disabled{opacity:.6;cursor:default}';doc.head.append(style);
 }
 const hosting=doc.querySelector('meta[name="redcn-hosting"]')?.content||'',entry=buildEntryPoint({mode,hosting,pathname:loc.pathname});
 const target=new URL(entry.kind==='phone'?'/phone/':entry.kind==='desktop'?'/':'.',loc.href);target.search='';target.hash='';
 let timer=null,disposed=false;
 const monitor=createBuildMonitor({currentBuild:doc.querySelector('meta[name="redcn-build"]')?.content,getActivity,onBeforeReload,now,
  probe:({signal})=>probePageBuild({url:target.href,origin:loc.origin,fetch:fetcher,signal}),reload:()=>loc.reload(),onState(value){
   const where=buildEntryPoint({mode:entry.kind,connected:value.activity.connected});summary.textContent=where.label;context.textContent=where.description;
   panel.dataset.buildNoticeState=value.status;notice.hidden=!value.outdated;reload.disabled=!value.canReload;check.disabled=value.checking||value.reloading;
   message.textContent=value.checkpointFailed?'Не удалось сохранить черновик. Страница оставлена открытой.':!value.activity.known?'Сохранение правок пока не подтверждено. Завершите редактирование перед обновлением.':value.activity.dirty?'Сначала сохраните изменения в объявлении. После этого можно обновить страницу.':value.activity.busy?'Дождитесь завершения текущей операции. После этого можно обновить страницу.':'Для этой страницы доступна новая версия. Обновите её, когда будете готовы.';
   const short=value.currentBuild?value.currentBuild.slice(0,12):'не определена';
   version.textContent='Версия открытой страницы: '+short+'. '+(value.checking?'Проверяем обновление…':value.failed?'Проверка временно недоступна.':value.outdated?'Доступна другая версия.':value.availableBuild?'Страница соответствует доступной версии.':'Версия ещё не проверена.');
  }});
 const activeCheck=()=>{if(!disposed&&!doc.hidden)void monitor.check();};
 const changed=()=>{void Promise.resolve().then(()=>{if(!disposed)monitor.updateState();});};
 const resume=()=>{changed();activeCheck();};
 const visibility=()=>{if(!doc.hidden)resume();};
 const tick=()=>{if(disposed)return;activeCheck();timer=win.setTimeout(tick,Math.max(30000,interval));};
 for(const event of ['pageshow','focus','online'])win.addEventListener(event,resume);
 doc.addEventListener('visibilitychange',visibility);doc.addEventListener('input',changed,true);doc.addEventListener('change',changed,true);
 check.onclick=()=>monitor.check({force:true});reload.onclick=()=>monitor.reload();
 monitor.updateState();activeCheck();timer=win.setTimeout(tick,Math.max(30000,interval));
 return {check:monitor.check,updateState:monitor.updateState,destroy(){disposed=true;if(timer!==null)win.clearTimeout(timer);monitor.destroy();for(const event of ['pageshow','focus','online'])win.removeEventListener(event,resume);doc.removeEventListener('visibilitychange',visibility);doc.removeEventListener('input',changed,true);doc.removeEventListener('change',changed,true);panel.remove();}};
}
