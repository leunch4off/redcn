import {setupCardArchive} from './card-archive.mjs';
import {setupCardFiles,cardPhotoDataUrl,cardDownloadBlob} from './card-files.mjs';
import {initializePwaInstall} from './pwa-install.mjs';
import {setupListingWatch} from './listing-watch.mjs';
import {setupImportDiagnostics} from './import-diagnostics.mjs';
import {setupPhoneUniversityRoutes} from './phone-routes-controller.mjs';

const ph=id=>document.getElementById(id);
let phoneArchiveUI=null,phoneFilesUI=null,phonePresentationUI=null,phoneDesignInitializing=null,phoneStarting=false;
let phoneWatchUI=null,phoneImportAuditUI=null,phoneRoutesUI=null;
const phoneFeedViews=new Map();let phoneFeedSignature='';
const phoneState={authEpoch:0,paired:false,csrf:'',cards:new Map(),cursor:null,hasMore:false,view:'cards',detail:null,detailId:null,media:[],photo:0,variant:'prepared',job:null,jobResult:null,lastSync:0,polling:false};
let phoneJobProgressTimer=0,phoneJobLatest=null,phoneJobLast=null;
const phoneWriting={revision:0,draftRevision:0,busy:false,capabilities:null,loading:null};
let phonePollTimer=0,phoneJobTimer=0,phoneToastTimer=0,phoneDetailRevision=0,phoneShareRevision=0,phoneShareFile=null,phoneSharePath='';
const phoneDate=value=>{if(!value)return null;const number=Number(value);const date=new Date(Number.isFinite(number)?number*(number<1e12?1000:1):value);return Number.isNaN(date.getTime())?null:date;};
const phoneTime=value=>phoneDate(value)?.toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit'})||'';
const phoneCardCount=count=>count+' '+(count%100>=11&&count%100<=14?'карточек':count%10===1?'карточка':count%10>=2&&count%10<=4?'карточки':'карточек');
const phoneMoney=value=>Number.isFinite(Number(value))&&Number(value)>0?Number(value).toLocaleString('ru-RU')+' ₽':'';
// This is a trace of completed requests, not a percentage estimate. It stays in
// memory and deliberately excludes pairing fragments and all listing contents.
const phoneLoad={revision:0,started:0,timer:0,finished:false};
function phoneLoadStep(stage,state,note){const row=ph('phoneLoad'+stage);row.dataset.state=state;row.querySelector('.load-mark').textContent={done:'✓',active:'…',waiting:'·',error:'!',action:'!'}[state];ph('phoneLoad'+stage+'Note').textContent=note;}
function phoneLoadClock(){ph('phoneLoadElapsed').textContent=((performance.now()-phoneLoad.started)/1000).toLocaleString('ru-RU',{minimumFractionDigits:1,maximumFractionDigits:1})+' с';}
function phoneLoadBegin(){
  const revision=++phoneLoad.revision;clearInterval(phoneLoad.timer);phoneLoad.started=performance.now();phoneLoad.finished=false;ph('phoneLoading').dataset.result='loading';ph('phoneLoadTitle').textContent='Проверяем подключение';ph('phoneLoadDetails').open=true;ph('phoneLoadRetry').classList.add('hidden');ph('phoneLoadRetry').disabled=true;
  ph('phoneLoadHost').textContent=(location.protocol==='https:'?'Внешний HTTPS-адрес: ':'Локальный адрес: ')+location.host;
  const navigation=performance.getEntriesByType('navigation')[0];if(navigation?.responseEnd>0)ph('phoneLoadPageNote').textContent='Получена за '+(navigation.responseEnd/1000).toLocaleString('ru-RU',{maximumFractionDigits:1})+' с после открытия адреса';
  phoneLoadStep('Connection','active','Ждём ответ сервера');phoneLoadStep('Session','waiting','Проверим действующую сессию или код');phoneLoadStep('Cards','waiting','Загрузим после входа');ph('phoneLoadHelp').textContent='Первое подключение проверяется до 9 секунд. Фотографии загружаются по мере просмотра.';phoneLoadClock();phoneLoad.timer=setInterval(phoneLoadClock,250);return revision;
}
function phoneLoadAccepted(revision){if(revision!==phoneLoad.revision||phoneLoad.finished)return;phoneLoadStep('Connection','done','Mac ответил на запрос');phoneLoadStep('Session','done','Вход подтверждён');phoneLoadStep('Cards','active','Получаем список с компьютера');ph('phoneLoadTitle').textContent='Загружаем карточки';}
function phoneLoadFinish(revision,result='ready',error=null,stage='Connection'){
  if(revision!==phoneLoad.revision||phoneLoad.finished)return;clearInterval(phoneLoad.timer);phoneLoadClock();phoneLoad.finished=true;ph('phoneLoading').dataset.result=result;ph('phoneLoadRetry').disabled=false;
  if(result==='ready'){phoneLoadStep('Cards','done',phoneCardCount(phoneState.cards.size)+' получено. Обложки загружаются по мере просмотра.');ph('phoneLoadTitle').textContent='Карточки готовы';ph('phoneLoadHelp').textContent='Данные получены с Mac. Список обновляется автоматически; полноразмерные фото открываются в карточке.';ph('phoneLoadDetails').open=false;return;}
  if(result==='code'){phoneLoadStep('Connection','done','Mac доступен');phoneLoadStep('Session','action','Нужны 8 цифр из раздела «Телефон» на Mac');phoneLoadStep('Cards','waiting','Доступны после ввода кода');ph('phoneLoadTitle').textContent='Mac отвечает · нужен код';ph('phoneLoadHelp').textContent='Введите код ниже. В установленном приложении вход может потребоваться отдельно.';ph('phoneLoadDetails').open=false;return;}
  if(error?.status&&stage==='Connection'&&!error.proxy){phoneLoadStep('Connection','done','Сервер ответил · HTTP '+error.status);stage='Session';}
  phoneLoadStep(stage,'error',error?.message||'Ответ не получен');ph('phoneLoadTitle').textContent=stage==='Cards'?'Не удалось получить карточки':'Не удалось подключиться';ph('phoneLoadHelp').textContent=navigator.onLine===false?'Телефон сейчас без интернета. Включите Wi-Fi или мобильные данные и повторите загрузку.':location.protocol==='https:'?'Проверьте интернет на телефоне, включённый Mac и внешний доступ в разделе «Телефон» на компьютере. При смене внешнего адреса откройте новую ссылку.':'Проверьте, что Mac включён, сервер запущен, а телефон подключён к той же сети. Для работы с улицы нужен внешний HTTPS-адрес.';ph('phoneLoadRetry').classList.remove('hidden');ph('phoneLoadDetails').open=true;
}
function phoneAsset(path){try{const url=new URL(path,location.origin);return url.origin===location.origin&&/^\/api\/phone\/(?:media\/|previews\/|cards\/)/.test(url.pathname)?url.pathname+url.search:null;}catch{return null;}}
function phoneToast(text){clearTimeout(phoneToastTimer);ph('phoneToast').textContent=text;ph('phoneToast').classList.remove('hidden');phoneToastTimer=setTimeout(()=>ph('phoneToast').classList.add('hidden'),5000);}
function phoneConnection(online){ph('phoneConnection').textContent=online?'Mac на связи':'Нет связи с Mac';ph('phoneConnection').classList.toggle('offline',!online);ph('phoneServerStatus').textContent=online?'Компьютер отвечает. Обновления приходят автоматически.':(location.protocol==='https:'?'Проверьте интернет на телефоне и внешний доступ на включённом Mac.':'Проверьте, что Mac включён и телефон находится в его локальной сети. Для работы с улицы откройте внешнюю HTTPS-ссылку.');if(!online&&phoneState.lastSync)ph('phoneSync').textContent='Нет связи · последнее обновление в '+phoneTime(phoneState.lastSync)+'. Показаны ранее полученные карточки.';}
function phoneAuth(paired){phoneState.paired=paired;ph('phonePair').classList.toggle('hidden',paired);ph('phoneWorkspace').classList.toggle('hidden',!paired);ph('phoneTabs').classList.toggle('hidden',!paired);if(!paired){phoneWatchUI?.destroy();phoneWatchUI=null;phoneImportAuditUI?.destroy();phoneImportAuditUI=null;phoneRoutesUI?.destroy();phoneRoutesUI=null;phoneResetWriting();phonePresentationUI?.destroy();phonePresentationUI=null;phoneDesignInitializing=null;ph('phonePairSubmit').disabled=false;phoneArchiveUI?.clear();phoneFilesUI?.clear();if(ph('phonePhotoViewer').open)ph('phonePhotoViewer').close();ph('phoneViewerImage').removeAttribute('src');phoneState.authEpoch++;phoneDetailRevision++;phoneState.job=null;phoneState.jobResult=null;phoneState.cursor=null;phoneState.polling=false;phoneState.csrf='';phoneState.cards.clear();phoneFeedViews.clear();phoneFeedSignature='';phoneState.detail=null;phoneState.detailId=null;phoneState.media=[];phoneShareFile=null;phoneShareRevision++;ph('phoneCards').replaceChildren();ph('phonePhotos').replaceChildren();ph('phoneDetail').classList.add('hidden');for(const id of ['phoneDetailTitle','phoneTitleCopy','phoneBody','phoneTags'])ph(id).textContent='';clearTimeout(phonePollTimer);clearTimeout(phoneJobTimer);clearInterval(phoneJobProgressTimer);phoneJobLatest=phoneJobLast=null;ph('phoneJob').classList.add('hidden');for(const id of ['phoneJobMessage','phoneJobSteps','phoneJobEstimate','phoneJobStageTime','phoneJobElapsed'])ph(id).textContent='';ph('phoneImportUrl').value='';}}
// The first usable feed has a shared budget; PDF and attachment work keep their
// longer operation deadlines and cannot make initial navigation wait for them.
async function phoneApi(path,data,{deadline=0}={}){
  const epoch=phoneState.authEpoch;
  const normalTimeout=path==='translate'||/^cards\/[a-f0-9]{32}\/(?:rewrite|edit-text)$/.test(path)?65000:path==='card-export'?120000:path==='listing-map'?60000:path.startsWith('card-archive/')?90000:8000;
  const timeout=deadline?Math.min(normalTimeout,Math.max(1,Math.ceil(deadline-performance.now()))):normalTimeout;
  const signal=AbortSignal.timeout(timeout);let response,value;
  try{response=await fetch('/api/phone/'+path,{method:data?'POST':'GET',credentials:'same-origin',cache:'no-store',headers:{...(data?{'Content-Type':'application/json','X-Phone-CSRF':phoneState.csrf}:{})},...(data?{body:JSON.stringify(data)}:{}),signal});try{value=await response.json();}catch(error){if(signal.aborted)throw error;value=null;}}
  catch{if(epoch===phoneState.authEpoch)phoneConnection(false);throw Error(signal.aborted?'Mac не ответил за несколько секунд. Нажмите «Обновить» или проверьте подключение.':navigator.onLine===false?'Телефон без интернета. Включите Wi-Fi или мобильные данные.':'Не удалось связаться с Mac. Проверьте интернет на телефоне и запущенную студию на компьютере.');}
  if(epoch!==phoneState.authEpoch){const error=Error('Подключение изменилось. Повторите действие.');error.status=401;error.staleAuth=true;throw error;}
  if(response.status===401){phoneAuth(false);ph('phoneConnection').textContent='Нужно подключение';ph('phoneReconnect').classList.add('hidden');ph('phonePairStatus').textContent='Для этого окна нужен вход. На Mac нажмите «Телефон» и введите показанные там 8 цифр. После установки на экран «Домой» подключитесь здесь ещё раз.';}
  if(!response.ok){const gateway=[502,503,504].includes(response.status),message=gateway?(location.protocol==='https:'?'Внешний адрес':'Сервер')+' вернул HTTP '+response.status+'. Соединение с приложением недоступно; проверьте сервер и внешний доступ на Mac.':typeof value?.error==='string'?value.error:response.status===401?'Для этого окна нужен вход. Введите текущие 8 цифр с Mac.':'Сервер вернул HTTP '+response.status+' без ответа приложения. Повторите запрос или проверьте внешний адрес.';const error=Error(message);error.status=response.status;error.proxy=gateway||(!value&&response.status!==401);if(error.proxy)phoneConnection(false);if(response.status===401)error.authEpoch=phoneState.authEpoch;throw error;}
  if(!value||typeof value!=='object'||Array.isArray(value)){const error=Error('Адрес вернул HTTP '+response.status+', но данные приложения не получены. Проверьте адрес и внешний доступ на Mac.');error.status=response.status;error.proxy=true;phoneConnection(false);throw error;}
  phoneConnection(true);return value;
}
function phoneView(view){phoneState.view=view;if(view==='watch'&&phoneState.paired)phoneInitializeWatch().refresh();if(view==='import'&&phoneState.paired)phoneInitializeAudit().refresh();if(view==='design')phoneInitializeDesigner().then(studio=>{if(studio&&phoneState.paired&&phoneState.view==='design')studio.refresh();}).catch(error=>phoneToast(error.message));for(const [key,id] of Object.entries({cards:'phoneCardsView',watch:'phoneWatchView',import:'phoneImportView',setup:'phoneSetupView',design:'phoneDesignView',detail:'phoneDetailView'}))ph(id).classList.toggle('hidden',key!==view);document.querySelectorAll('#phoneTabs [data-phone-view]').forEach(button=>{const selected=button.dataset.phoneView===(view==='detail'?'cards':view);button.classList.toggle('active',selected);button.setAttribute('aria-current',selected?'page':'false');});window.scrollTo({top:0,behavior:'instant'});}
document.querySelectorAll('[data-phone-view]').forEach(button=>button.addEventListener('click',()=>{phoneView(button.dataset.phoneView);if(button.dataset.phoneView==='cards')phoneRefreshCards().catch(error=>phoneToast(error.message));}));
function phoneCardView(card){
  let view=phoneFeedViews.get(card.id);
  if(!view){
    const button=document.createElement('button');button.type='button';button.className='listing-card';button.dataset.cardId=card.id;button.onclick=()=>phoneOpenCard(card.id);
    const cover=document.createElement('div');cover.className='listing-cover';const badge=document.createElement('span');badge.className='photo-badge';cover.append(badge);
    const info=document.createElement('div');info.className='listing-info';const price=document.createElement('div');price.className='listing-price';
    const title=document.createElement('h3'),meta=document.createElement('div'),stamp=document.createElement('div');meta.className='listing-meta';stamp.className='listing-updated';info.append(price,title,meta,stamp);button.append(cover,info);
    view={button,cover,badge,price,title,meta,stamp,source:undefined,image:null};phoneFeedViews.set(card.id,view);
  }
  const signature=JSON.stringify(card);if(view.signature===signature)return view.button;view.signature=signature;
  view.button.setAttribute('aria-label','Открыть '+(card.title||'карточку квартиры'));
  const source=phoneAsset(card.coverUrl);
  if(source!==view.source){
    if(view.image)view.image.remove();view.source=source;
    if(source){const img=document.createElement('img');img.src=source;img.alt='Фото квартиры';img.loading=phoneFeedViews.size===1?'eager':'lazy';img.decoding='async';img.width=400;img.height=210;if(phoneFeedViews.size===1)img.fetchPriority='high';view.image=img;}
    else{const empty=document.createElement('div');empty.className='cover-placeholder';empty.textContent='▧';view.image=empty;}
    view.cover.prepend(view.image);
  }
  view.badge.textContent=Number(card.photoCount)>0?card.photoCount+' фото':card.mediaStatus==='loading'?'Фото готовятся':'Без фотографий';
  view.price.textContent=phoneMoney(card.price)||'Цена в карточке';if(phoneMoney(card.price)){const period=document.createElement('small');period.textContent='/ месяц';view.price.append(period);}
  view.title.textContent=card.title||'Квартира в Москве';view.meta.replaceChildren();if(card.metro){const station=document.createElement('span');station.textContent='🚇 '+card.metro;view.meta.append(station);}
  view.stamp.textContent=phoneTime(card.updated)?'Обновлено в '+phoneTime(card.updated):'С компьютера';return view.button;
}
function phoneRenderCards(){
  const query=ph('phoneSearch').value.toLocaleLowerCase('ru').trim();
  const rows=[...phoneState.cards.values()].sort((a,b)=>(phoneDate(b.updated)?.getTime()||0)-(phoneDate(a.updated)?.getTime()||0)).filter(card=>[card.title,card.price,card.metro].join(' ').toLocaleLowerCase('ru').includes(query));
  const signature=JSON.stringify([query,phoneState.hasMore,rows]);if(signature===phoneFeedSignature)return;phoneFeedSignature=signature;
  ph('phoneEmpty').classList.toggle('hidden',phoneState.cards.size>0||!!query);ph('phoneMore').classList.toggle('hidden',!phoneState.hasMore);
  const container=ph('phoneCards');
  if(!rows.length){container.replaceChildren();if(query){const note=document.createElement('p');note.className='surface';note.textContent='По этому запросу карточек пока нет.';container.append(note);}return;}
  const nodes=rows.map(phoneCardView),wanted=new Set(nodes);
  for(const child of [...container.children])if(!wanted.has(child))child.remove();
  for(let index=0;index<nodes.length;index++)if(container.children[index]!==nodes[index])container.insertBefore(nodes[index],container.children[index]||null);
}
async function phoneRefreshCards({reset=false,deadline=0}={}){
  if(!phoneState.paired||phoneState.polling)return;const epoch=phoneState.authEpoch;phoneState.polling=true;ph('phoneRefresh').disabled=true;if(!phoneState.cards.size)ph('phoneSync').textContent='Загружаем карточки с Mac…';
  try{const after=!reset&&phoneState.cursor!==null?'?after='+encodeURIComponent(phoneState.cursor):'';const data=await phoneApi('cards'+after,undefined,{deadline});for(const card of data.cards||[]){if(/^[a-f0-9]{32}$/.test(card.id))phoneState.cards.set(card.id,card);}if(data.cursor!==undefined&&data.cursor!==null)phoneState.cursor=data.cursor;phoneState.hasMore=data.hasMore===true;phoneState.lastSync=data.serverTime||Date.now();ph('phoneSync').textContent=phoneCardCount(phoneState.cards.size)+' · обновлено в '+phoneTime(phoneState.lastSync);phoneRenderCards();const changed=(data.cards||[]).find(card=>card.id===phoneState.detailId&&String(card.updated)!==String(phoneState.detail?.updated));if(changed&&phoneState.view==='detail'&&phoneState.detail)await phoneOpenCard(changed.id,{keepScroll:true});if(phoneState.view==='detail'&&phoneState.detail){phoneArchiveUI?.refresh();phoneFilesUI?.refresh();}}
  catch(error){if(epoch===phoneState.authEpoch&&!phoneState.cards.size)ph('phoneSync').textContent=error.message;throw error;}
  finally{if(epoch===phoneState.authEpoch){phoneState.polling=false;ph('phoneRefresh').disabled=false;phoneSchedulePoll();}}
}
function phoneSchedulePoll(){clearTimeout(phonePollTimer);if(phoneState.paired&&!document.hidden)phonePollTimer=setTimeout(()=>phoneRefreshCards().catch(()=>{}),5000);}
ph('phoneRefresh').onclick=()=>phoneRefreshCards({reset:true}).catch(error=>phoneToast(error.message));ph('phoneMore').onclick=()=>phoneRefreshCards().catch(error=>phoneToast(error.message));ph('phoneSearch').oninput=phoneRenderCards;
async function phonePair(code,{deadline=performance.now()+9000,loading=0}={}){
  try{if(code.includes('#'))code=new URL(code).hash.slice(1);}catch{}
  const epoch=++phoneState.authEpoch,load=loading||phoneLoadBegin();
  ph('phonePairSubmit').disabled=true;ph('phonePairStatus').textContent='Соединяем телефон с Mac…';ph('phonePairStatus').classList.remove('error');ph('phoneReconnect').classList.add('hidden');
  try{const state=await phoneApi('pair',{code},{deadline});if(epoch!==phoneState.authEpoch)return;phoneLoadAccepted(load);ph('phonePairCode').value='';phoneState.csrf=state.csrfToken||'';phoneAuth(true);phoneView('cards');phoneState.cursor=null;await phoneRefreshCards({reset:true,deadline});phoneLoadFinish(load);phoneResumeJob(state.activeJob);ph('phonePairStatus').textContent='';}
  catch(error){if(epoch!==phoneState.authEpoch&&error.authEpoch!==phoneState.authEpoch)return;phoneLoadFinish(load,error.status===401?'code':'error',error,phoneState.paired?'Cards':'Connection');if(epoch===phoneState.authEpoch){ph('phonePairStatus').textContent=error.message;ph('phonePairStatus').classList.add('error');ph('phoneReconnect').classList.toggle('hidden',phoneState.paired);}}
  finally{if(epoch===phoneState.authEpoch)ph('phonePairSubmit').disabled=false;}
}
ph('phonePairForm').onsubmit=event=>{event.preventDefault();const value=ph('phonePairCode').value.trim();if(value)phonePair(value);};
async function phoneOpenCard(id,{keepScroll=false,deadline=0}={}){
  if(!/^[a-f0-9]{32}$/.test(id))return;const revision=++phoneDetailRevision,oldScroll=window.scrollY;if(phoneState.detailId!==id){phoneResetWriting();phoneArchiveUI?.clear();phoneFilesUI?.clear();}phoneState.detailId=id;if(!keepScroll){phoneState.photo=0;phoneState.variant='prepared';phoneView('detail');ph('phoneDetail').classList.add('hidden');ph('phoneDetailLoading').classList.remove('hidden');ph('phoneDetailLoading').textContent='Открываем карточку с Mac…';}phoneShareFile=null;phoneShareRevision++;
  try{const card=await phoneApi('cards/'+id,undefined,{deadline});if(revision!==phoneDetailRevision)return;phoneState.detail=card;phoneUpdateRouteTools();phoneInitializeCardTools();phoneWritingButtons();ph('phoneDetailTitle').textContent=card.titles?.[0]||card.fields?.name||'Квартира в Москве';ph('phoneTitleCopy').textContent=card.titles?.[0]||'';ph('phoneBody').textContent=card.body||'';ph('phoneTags').textContent=card.tags||'';ph('phoneDetailStamp').textContent='С Mac · '+phoneTime(card.updated);ph('phoneDetailFacts').replaceChildren();for(const text of [phoneMoney(card.fields?.price),card.fields?.area?card.fields.area+' м²':'',card.fields?.metro])if(text){const span=document.createElement('span');span.textContent=text;ph('phoneDetailFacts').append(span);}ph('phoneDetailLoading').classList.add('hidden');ph('phoneDetail').classList.remove('hidden');phoneRenderMedia();phoneArchiveUI?.refresh();phoneFilesUI?.refresh();window.scrollTo(0,keepScroll?oldScroll:0);}
  catch(error){if(revision===phoneDetailRevision)ph('phoneDetailLoading').textContent=error.message;}
}
ph('phoneBack').onclick=()=>{phoneDetailRevision++;phoneView('cards');};
function phoneRenderMedia(){
  const card=phoneState.detail;if(!card)return;
  const all=Array.isArray(card.media)?card.media:[],crops=all.filter(row=>row.crop&&phoneAsset(row.crop.url)),prepared=phoneState.variant==='prepared';
  phoneState.media=(prepared?all.map(row=>row.crop&&phoneAsset(row.crop.url)?{...row.crop,sourceUrl:row.sourceUrl}:row):all).filter(row=>phoneAsset(row.url));phoneState.photo=Math.min(phoneState.photo,Math.max(0,phoneState.media.length-1));
  ph('phonePhotoVariants').classList.toggle('hidden',!all.length);for(const [variant,id] of [['original','phoneOriginalVariant'],['prepared','phoneCropVariant']]){const selected=phoneState.variant===variant;ph(id).classList.toggle('active',selected);ph(id).setAttribute('aria-pressed',String(selected));}
  ph('phonePhotos').replaceChildren();for(const [index,row] of phoneState.media.entries()){const figure=document.createElement('figure');figure.className='phone-slide';const image=document.createElement('img');image.src=phoneAsset(row.url);image.alt='Фотография квартиры '+(index+1);image.loading=index?'lazy':'eager';figure.append(image);ph('phonePhotos').append(figure);}
  const count=phoneState.media.length,expected=Number(card.photoCount??card.fields?.photos?.length??count),processing=card.photoProcessing||{};
  ph('phonePhotoCount').textContent=count+' фото';ph('phonePreparePhotos').classList.toggle('hidden',expected===0||(count>=expected&&!(Number(processing.failed)>0||Number(processing.skipped)>0)));
  ph('phonePreparePhotos').textContent=count>=expected?'Повторить подготовку фото на Mac':'Подготовить фотографии на Mac';
  ph('phoneMediaStatus').textContent=!count?(expected===0?'В объявлении нет фотографий.':'Mac ещё готовит фотографии.'):(prepared?'Готовые фото: '+crops.length+' обрезано автоматически или проверено. Остальные сохранены целиком.':'Показаны исходные фотографии без изменений.')+(expected>count?' Загружено '+count+' из '+expected+'.':'')+(Number(processing.review)>0?' '+processing.review+' фото оставлено целиком: безопасная обрезка не подтверждена.':'')+(Number(processing.failed)>0?' Автообрезка пока недоступна для '+processing.failed+' фото; оригиналы сохранены.':'')+(Number(processing.skipped)>0?' Ещё '+processing.skipped+' фото сохранено без изменений по лимиту обработки.':'');
  const zip='/api/phone/cards/'+card.id+'.zip?variant='+phoneState.variant;ph('phoneDownloadZip').href=zip;ph('phoneDownloadZip').classList.toggle('hidden',!count);ph('phoneDownloadZip').download='most-'+card.id+'-'+phoneState.variant+'.zip';ph('phonePhotos').scrollLeft=ph('phonePhotos').clientWidth*phoneState.photo;phoneSelectPhoto(phoneState.photo,false);
}
function phoneSelectPhoto(index,scroll=true){
  const count=phoneState.media.length;phoneState.photo=count?Math.max(0,Math.min(count-1,index)):0;const row=phoneState.media[phoneState.photo],source=phoneAsset(row?.url),download=phoneAsset(row?.downloadUrl)||source;ph('phonePhotoPosition').textContent=count?(phoneState.photo+1)+' / '+count:'Нет фото';ph('phonePhotoPrev').disabled=count<2;ph('phonePhotoNext').disabled=count<2;ph('phoneDownloadPhoto').classList.toggle('hidden',!source);ph('phoneOpenPhoto').classList.toggle('hidden',!source);ph('phoneSharePhoto').disabled=!source;
  if(source){ph('phoneDownloadPhoto').href=download;ph('phoneDownloadPhoto').download='most-photo-'+(phoneState.photo+1)+'.'+(row.mime==='image/png'?'png':row.mime==='image/webp'?'webp':'jpg');ph('phoneOpenPhoto').href=source;if(scroll)ph('phonePhotos').scrollTo({left:ph('phonePhotos').clientWidth*phoneState.photo,behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth'});phonePrimeShare(source,row);}
  else{phoneShareFile=null;phoneShareRevision++;ph('phoneSharePhoto').textContent='Поделиться фото';}
}
ph('phonePhotoPrev').onclick=()=>phoneSelectPhoto((phoneState.photo-1+phoneState.media.length)%phoneState.media.length);ph('phonePhotoNext').onclick=()=>phoneSelectPhoto((phoneState.photo+1)%phoneState.media.length);
let phoneExportViewerUrl=null;ph('phonePhotoViewer').addEventListener('close',()=>{if(phoneExportViewerUrl){URL.revokeObjectURL(phoneExportViewerUrl);phoneExportViewerUrl=null;}ph('phoneViewerImage').removeAttribute('src');});
let phoneGalleryFrame=0;ph('phonePhotos').addEventListener('scroll',()=>{if(phoneGalleryFrame)return;phoneGalleryFrame=requestAnimationFrame(()=>{phoneGalleryFrame=0;const index=Math.round(ph('phonePhotos').scrollLeft/Math.max(1,ph('phonePhotos').clientWidth));if(index!==phoneState.photo)phoneSelectPhoto(index,false);});},{passive:true});
ph('phoneOriginalVariant').onclick=()=>{phoneState.variant='original';phoneState.photo=0;phoneRenderMedia();};ph('phoneCropVariant').onclick=()=>{phoneState.variant='prepared';phoneState.photo=0;phoneRenderMedia();};
async function phonePrimeShare(path,row){
  const supported=window.isSecureContext&&typeof navigator.share==='function'&&typeof navigator.canShare==='function';
  if(!supported){phoneShareFile=null;phoneSharePath=path;ph('phoneSharePhoto').textContent='Сохранить в «Фото»';ph('phoneSharePhoto').disabled=false;ph('phoneShareHelp').textContent='Откройте фото, удерживайте изображение и выберите сохранение. ZIP сохраняется в «Файлы».';return;}
  if(phoneSharePath===path&&phoneShareFile){ph('phoneSharePhoto').textContent='Поделиться фото';ph('phoneSharePhoto').disabled=false;return;}
  const revision=++phoneShareRevision;phoneSharePath=path;phoneShareFile=null;ph('phoneSharePhoto').textContent='Готовим фото…';ph('phoneSharePhoto').disabled=true;
  try{const response=await fetch(path,{credentials:'same-origin',cache:'no-store',signal:AbortSignal.timeout(25000)});if(!response.ok)throw Error('Фото недоступно');const blob=await response.blob();if(blob.size>12_000_000||!['image/jpeg','image/png','image/webp'].includes(blob.type))throw Error('Формат фото недоступен');const file=new File([blob],'most-photo.'+(row.mime==='image/png'?'png':row.mime==='image/webp'?'webp':'jpg'),{type:blob.type});if(!navigator.canShare({files:[file]}))throw Error('Обмен файлами недоступен');if(revision!==phoneShareRevision)return;phoneShareFile=file;ph('phoneSharePhoto').textContent='Поделиться фото';ph('phoneShareHelp').textContent='В системном меню выберите сохранение изображения в «Фото».';}
  catch{if(revision===phoneShareRevision){phoneShareFile=null;ph('phoneSharePhoto').textContent='Открыть для сохранения';ph('phoneShareHelp').textContent='Удерживайте открытое изображение и выберите сохранение в «Фото».';}}
  finally{if(revision===phoneShareRevision)ph('phoneSharePhoto').disabled=false;}
}
function phoneShowImage(){const source=phoneAsset(phoneState.media[phoneState.photo]?.url);if(!source)return;ph('phoneViewerImage').src=source;ph('phonePhotoViewer').showModal();}
ph('phoneOpenPhoto').onclick=event=>{event.preventDefault();phoneShowImage();};ph('phoneViewerClose').onclick=()=>ph('phonePhotoViewer').close();
ph('phoneSharePhoto').onclick=async()=>{const row=phoneState.media[phoneState.photo],source=phoneAsset(row?.url);if(!source)return;if(phoneShareFile){try{await navigator.share({files:[phoneShareFile]});}catch(error){if(error.name!=='AbortError')phoneToast('Не удалось открыть меню. Используйте «Открыть фото» для сохранения.');}}else{phoneShowImage();}};
function phoneWritingButtons(){
  const disabled=phoneWriting.busy||!phoneState.paired||!phoneState.detailId;
  for(const id of ['phoneRewriteTitle','phoneRewriteBody','phoneRewriteTags','phoneRewriteAll'])ph(id).disabled=disabled;
  ph('phoneTranslateDraft').disabled=disabled||!ph('phoneRussianDraft').value.trim();
  ph('phoneApplyDraft').disabled=disabled||!ph('phoneChineseDraft').value.trim();
}
function phoneResetWriting(){
  phoneWriting.revision++;phoneWriting.draftRevision++;phoneWriting.busy=false;if(!phoneState.paired){phoneWriting.capabilities=null;phoneWriting.loading=null;}
  ph('phoneRussianDraft').value='';ph('phoneChineseDraft').value='';ph('phoneChineseDraft').maxLength=20000;ph('phoneLanguagePart').value='body';ph('phoneLanguageStudio').open=false;
  ph('phoneTranslationReview').textContent='Неизвестные фразы будут показаны для проверки.';ph('phoneTranslationReview').dataset.state='';
  ph('phoneWritingStatus').textContent='';ph('phoneWritingStatus').dataset.state='';phoneWritingButtons();
}
function phoneWritingBegin(message){
  if(phoneWriting.busy||!phoneState.paired||!phoneState.detailId||!phoneState.detail)return null;
  const request={id:phoneState.detailId,epoch:phoneState.authEpoch,revision:++phoneWriting.revision};phoneWriting.busy=true;phoneWritingButtons();ph('phoneWritingStatus').textContent=message;ph('phoneWritingStatus').dataset.state='';return request;
}
function phoneWritingCurrent(request){return request&&request.epoch===phoneState.authEpoch&&request.id===phoneState.detailId&&request.revision===phoneWriting.revision;}
function phoneWritingEnd(request){if(request?.revision===phoneWriting.revision){phoneWriting.busy=false;phoneWritingButtons();}}
function phoneApplyWrittenCard(card,request){
  if(!phoneWritingCurrent(request))return false;if(card?.id!==request.id)throw Error('Ответ относится к другой карточке. Обновите объявление.');
  const oldScroll=window.scrollY;++phoneDetailRevision;phoneState.detail=card;phoneUpdateRouteTools();
  const title=card.titles?.[0]||card.title||card.fields?.name||'Квартира в Москве';ph('phoneDetailTitle').textContent=title;ph('phoneTitleCopy').textContent=card.titles?.[0]||card.title||'';ph('phoneBody').textContent=card.body||'';ph('phoneTags').textContent=card.tags||'';ph('phoneDetailStamp').textContent='С Mac · '+phoneTime(card.updated);
  const summary=phoneState.cards.get(card.id);if(summary){phoneState.cards.set(card.id,{...summary,title,updated:card.updated});phoneRenderCards();}
  if(phoneState.view==='detail')window.scrollTo(0,oldScroll);return true;
}
async function phoneRewrite(part){
  const request=phoneWritingBegin(part==='all'?'Создаём новую подачу по фактам квартиры…':'Подбираем новый вариант по фактам квартиры…');if(!request)return;
  try{const card=await phoneApi('cards/'+request.id+'/rewrite',{part});if(!phoneApplyWrittenCard(card,request))return;ph('phoneWritingStatus').textContent=part==='all'?'Новая подача сохранена в карточке.':'Новый вариант сохранён. Остальные части текста оставлены как были.';phoneToast('Текст обновлён.');}
  catch(error){if(phoneWritingCurrent(request)){ph('phoneWritingStatus').textContent=error.message;ph('phoneWritingStatus').dataset.state='error';}}
  finally{phoneWritingEnd(request);}
}
for(const [part,id] of [['title','phoneRewriteTitle'],['body','phoneRewriteBody'],['tags','phoneRewriteTags'],['all','phoneRewriteAll']])ph(id).onclick=()=>phoneRewrite(part);
function phoneLanguageCount(value){const number=typeof value==='number'?value:Number(value?.count??value?.total??value?.size);return Number.isFinite(number)&&number>=0?number.toLocaleString('ru-RU'):null;}
function phoneRenderLanguageStats(value){
  const catalog=value?.templates||{},dictionary=value?.dictionary||{};
  const title=phoneLanguageCount(catalog.title??catalog.titles??catalog.titleCount),body=phoneLanguageCount(catalog.body??catalog.descriptions??catalog.bodyCount),tags=phoneLanguageCount(catalog.tags??catalog.tagSets??catalog.tagCount),entries=phoneLanguageCount(dictionary.ruZhPairs??dictionary.entries??dictionary.entryCount);
  const lines=[];if(entries)lines.push('База перевода: '+entries+' записей.');if(title&&body&&tags)lines.push('Шаблоны: '+title+' заголовков, '+body+' описаний, '+tags+' наборов тегов.');
  lines.push('Композиции шаблонов и словарные записи считаются отдельно. Доступные варианты зависят от подтверждённых фактов квартиры.');ph('phoneLanguageStats').textContent=lines.join(' ');
}
async function phoneLoadLanguageStats(){
  if(phoneWriting.capabilities){phoneRenderLanguageStats(phoneWriting.capabilities);return;}
  if(phoneWriting.loading)return phoneWriting.loading;const epoch=phoneState.authEpoch;ph('phoneLanguageStats').textContent='Получаем сводку словаря и шаблонов…';
  const pending=phoneApi('language').then(value=>{if(epoch!==phoneState.authEpoch)return;phoneWriting.capabilities=value;phoneRenderLanguageStats(value);}).catch(error=>{if(epoch===phoneState.authEpoch)ph('phoneLanguageStats').textContent='Не удалось получить сводку базы. '+error.message;}).finally(()=>{if(phoneWriting.loading===pending)phoneWriting.loading=null;});phoneWriting.loading=pending;return pending;
}
ph('phoneLanguageStudio').addEventListener('toggle',()=>{if(ph('phoneLanguageStudio').open&&phoneState.paired)phoneLoadLanguageStats();});
for(const id of ['phoneRussianDraft','phoneChineseDraft'])ph(id).addEventListener('input',()=>{phoneWriting.draftRevision++;phoneWritingButtons();});
ph('phoneLanguagePart').onchange=()=>{phoneWriting.draftRevision++;ph('phoneChineseDraft').maxLength=ph('phoneLanguagePart').value==='title'?240:ph('phoneLanguagePart').value==='tags'?3000:20000;phoneWritingButtons();};
ph('phoneTranslateDraft').onclick=async()=>{
  const text=ph('phoneRussianDraft').value.trim();if(!text)return;const request=phoneWritingBegin('Переводим ваш русский текст…');if(!request)return;const draftRevision=phoneWriting.draftRevision;
  try{const result=await phoneApi('translate',{text,part:ph('phoneLanguagePart').value});if(!phoneWritingCurrent(request))return;if(draftRevision!==phoneWriting.draftRevision){ph('phoneWritingStatus').textContent='Черновик изменился во время перевода. Нажмите перевод ещё раз.';return;}if(typeof result.text!=='string')throw Error('Перевод не получен. Повторите запрос.');
    ph('phoneChineseDraft').value=result.text;const unknown=(Array.isArray(result.unknown)?result.unknown:[]).map(item=>typeof item==='string'?item:String(item?.text??item?.source??'')).filter(Boolean),warnings=(Array.isArray(result.warnings)?result.warnings:[]).filter(value=>typeof value==='string');
    const review=[];if(unknown.length)review.push('Требуют проверки: '+unknown.slice(0,12).join(' · ')+(unknown.length>12?' · ещё '+(unknown.length-12):''));if(warnings.length)review.push(warnings.slice(0,4).join(' '));
    ph('phoneTranslationReview').textContent=review.length?review.join('\n'):'Перевод готов. Проверьте формулировки и сохраните выбранную часть карточки.';ph('phoneTranslationReview').dataset.state=review.length?'review':'ready';ph('phoneWritingStatus').textContent='Перевод показан в черновике.';
  }catch(error){if(phoneWritingCurrent(request)){ph('phoneWritingStatus').textContent=error.message;ph('phoneWritingStatus').dataset.state='error';}}
  finally{phoneWritingEnd(request);}
};
ph('phoneApplyDraft').onclick=async()=>{
  const text=ph('phoneChineseDraft').value.trim(),part=ph('phoneLanguagePart').value,sourceRu=ph('phoneRussianDraft').value.trim();if(!text)return;const request=phoneWritingBegin('Сохраняем ваш текст в карточке…');if(!request)return;
  try{const card=await phoneApi('cards/'+request.id+'/edit-text',{part,text,...(sourceRu?{sourceRu}:{})});if(!phoneApplyWrittenCard(card,request))return;ph('phoneWritingStatus').textContent='Ваш текст сохранён в карточке.';phoneToast('Текст сохранён.');}
  catch(error){if(phoneWritingCurrent(request)){ph('phoneWritingStatus').textContent=error.message;ph('phoneWritingStatus').dataset.state='error';}}
  finally{phoneWritingEnd(request);}
};
async function phoneCopy(text){if(!text)return phoneToast('В этом поле пока нет текста.');try{if(navigator.clipboard&&window.isSecureContext){await navigator.clipboard.writeText(text);}else{const field=document.createElement('textarea');field.value=text;field.style.position='fixed';field.style.top='0';field.style.left='-10000px';document.body.append(field);field.focus();field.select();field.setSelectionRange(0,text.length);const success=document.execCommand('copy');field.remove();if(!success)throw Error('copy');}phoneToast('Скопировано. Можно вставить в публикацию.');}catch{phoneToast('Удерживайте текст, выберите «Выбрать всё», затем «Скопировать».');}}
ph('phoneCopyTitle').onclick=()=>phoneCopy(phoneState.detail?.titles?.[0]||'');ph('phoneCopyBody').onclick=()=>phoneCopy(phoneState.detail?.body||'');ph('phoneCopyTags').onclick=()=>phoneCopy(phoneState.detail?.tags||'');ph('phoneCopyAll').onclick=()=>phoneCopy([phoneState.detail?.titles?.[0],phoneState.detail?.body,phoneState.detail?.tags].filter(Boolean).join('\n\n'));
function phoneJobDuration(value){const total=Math.max(0,Math.round(Number(value)||0)),minutes=Math.floor(total/60),seconds=total%60;return minutes?minutes+' мин '+seconds+' с':seconds+' с';}
function phoneJobClock(){
  if(!phoneJobLatest)return;const {progress:p,state,received,partial}=phoneJobLatest,running=state==='running',delta=running?(performance.now()-received)/1000:0,elapsed=(Number(p.elapsedSeconds)||0)+delta,stageTime=(Number(p.stageElapsedSeconds)||0)+delta;
  ph('phoneJobElapsed').textContent=(running?'Прошло ':'Затрачено ')+phoneJobDuration(elapsed);ph('phoneJobStageTime').textContent=p.label&&p.stage!=='done'?p.label+' · '+phoneJobDuration(stageTime)+(running&&Number(p.maxTotalSeconds)>0?' · лимит '+phoneJobDuration(p.maxTotalSeconds):''):'';
  const samples=Number(p.sampleCount)||0,average=Number(p.estimateSeconds),hasAverage=p.estimateSource==='history'&&samples>0&&Number.isFinite(average)&&average>0;
  const profile=p.profile==='import:cache'?' для данных из кеша':p.profile==='prepare'?' для подготовки фото':'';
  if(p.awaitingUser){ph('phoneJobEstimate').textContent='Ожидаем вашу проверку в Chrome на Mac. Время ручного подтверждения предсказать нельзя.';return;}
  if(!hasAverage){ph('phoneJobEstimate').textContent=partial?'Среднее ещё не рассчитано. Неполная подготовка фото не включается в измерения.':'Среднее ещё не рассчитано'+profile+': собираем время завершённых операций. Текущий этап показан ниже.';return;}
  const range=Array.isArray(p.historyRangeSeconds)&&p.historyRangeSeconds.length===2?' Диапазон '+phoneJobDuration(p.historyRangeSeconds[0])+'–'+phoneJobDuration(p.historyRangeSeconds[1])+'.':'';
  const prefix='Среднее'+profile+': '+phoneJobDuration(average)+' по '+samples+' завершённым операциям.';
  ph('phoneJobEstimate').textContent=prefix+(partial?' Неполная подготовка фото не включена в среднее.':'')+(running?(elapsed>average?' Сейчас дольше обычного; обработка продолжается.':' Ориентировочно осталось '+phoneJobDuration(average-elapsed)+'.'):'')+range;
}
function phoneRenderJobProgress(result){
  const p=result.progress||{};phoneJobLatest={progress:p,state:result.state,partial:result.partial===true,received:performance.now()};ph('phoneJob').dataset.jobState=result.state||'running';
  const total=Number(p.total),completed=Number(p.completed),hasCount=Number.isFinite(total)&&total>0&&Number.isFinite(completed);ph('phoneJobCount').classList.toggle('hidden',!hasCount);if(hasCount){ph('phoneJobCount').max=total;ph('phoneJobCount').value=Math.min(total,Math.max(0,completed));}
  ph('phoneJobSteps').replaceChildren();for(const step of p.stages||[]){const row=document.createElement('li');row.dataset.state=step.state;const mark=document.createElement('span');mark.className='job-step-mark';mark.textContent=step.state==='done'?'✓':step.state==='error'?'!':step.state==='waitingManual'?'!':'…';const text=document.createElement('span'),title=document.createElement('strong'),note=document.createElement('small');title.textContent=step.label||step.id;note.textContent=phoneJobDuration(step.elapsedSeconds)+(Number.isFinite(step.completed)&&Number(step.total)>0?' · '+step.completed+' из '+step.total:'');text.append(title,note);row.append(mark,text);ph('phoneJobSteps').append(row);}
  ph('phoneJobDetails').classList.toggle('hidden',!p.stages?.length);phoneJobClock();clearInterval(phoneJobProgressTimer);if(result.state==='running'&&!document.hidden)phoneJobProgressTimer=setInterval(phoneJobClock,500);
}
function phoneResumeJob(info){if(!info?.id||!['running','done','error'].includes(info.state)||phoneState.job)return;if(info.sourceUrl&&!ph('phoneImportUrl').value)ph('phoneImportUrl').value=info.sourceUrl;phoneSetJob(info.id,info.kind||'import',info.cardId);if(info.state==='running')phoneView('import');}
function phoneSetJob(id,kind='import',cardId=null){phoneState.job={id,kind,cardId:cardId||phoneState.detailId};phoneJobLast=phoneState.job;phoneState.jobResult=null;ph('phoneJobRestart').textContent='Повторить обработку';ph('phoneJob').classList.remove('hidden');ph('phoneJobTitle').textContent=kind==='prepare'?'Mac готовит фотографии':'Mac готовит объявление';ph('phoneJobMessage').textContent='Запрос отправлен на компьютер.';for(const id of ['phoneJobOpen','phoneJobRetry','phoneJobRestart'])ph(id).classList.add('hidden');ph('phoneImportSubmit').disabled=true;phoneRenderJobProgress({state:'running',progress:{elapsedSeconds:0,stageElapsedSeconds:0}});phoneCheckJob();}
async function phoneCheckJob(){
  clearTimeout(phoneJobTimer);const job=phoneState.job;if(!job||document.hidden)return;
  try{const result=await phoneApi('jobs/'+encodeURIComponent(job.id));if(phoneState.job!==job)return;ph('phoneJobMessage').textContent=result.message||result.progress?.message||'Компьютер обрабатывает объявление…';ph('phoneJobRetry').classList.add('hidden');phoneRenderJobProgress(result);if(job.kind==='prepare')ph('phoneMediaStatus').textContent=result.message||'Mac готовит фотографии…';
    if(result.state==='done'){phoneState.jobResult=result.result_id;ph('phoneJobTitle').textContent=result.partial?'Карточка готова · фото подготовлены частично':'Готово на Mac';if(result.partial&&result.result_id){phoneJobLast={...job,kind:'prepare',cardId:result.result_id};ph('phoneJobRestart').textContent='Повторить подготовку фото';ph('phoneJobRestart').classList.remove('hidden');}ph('phoneJobOpen').classList.toggle('hidden',!result.result_id);ph('phoneImportSubmit').disabled=false;phoneState.job=null;await phoneRefreshCards({reset:true});if(job.kind==='prepare'&&result.result_id===phoneState.detailId)await phoneOpenCard(result.result_id,{keepScroll:true});}
    else if(result.state==='error'){ph('phoneJobTitle').textContent='Не удалось завершить обработку';ph('phoneImportSubmit').disabled=false;ph('phoneJobRestart').classList.remove('hidden');phoneState.job=null;}
    else{ph('phoneJobTitle').textContent=result.progress?.awaitingUser?'Нужна проверка в Chrome на Mac':result.progress?.label||'Mac готовит объявление';phoneJobTimer=setTimeout(phoneCheckJob,1800);}
  }catch(error){if(phoneState.job!==job)return;ph('phoneJobMessage').textContent=error.message+' Проверка состояния не отменяет работу на Mac.';ph('phoneJobRetry').classList.remove('hidden');clearInterval(phoneJobProgressTimer);}
}
ph('phoneJobOpen').onclick=()=>{if(phoneState.jobResult)phoneOpenCard(phoneState.jobResult);};ph('phoneJobRetry').onclick=()=>{ph('phoneJobRetry').classList.add('hidden');phoneCheckJob();};
ph('phoneJobRestart').onclick=async()=>{if(phoneJobLast?.kind==='prepare'&&phoneJobLast.cardId){ph('phoneJobRestart').disabled=true;try{const result=await phoneApi('cards/'+phoneJobLast.cardId+'/prepare',{});phoneSetJob(result.job_id,'prepare',phoneJobLast.cardId);}catch(error){phoneToast(error.message);}finally{ph('phoneJobRestart').disabled=false;}}else ph('phoneImportForm').requestSubmit();};
ph('phoneImportForm').onsubmit=async event=>{event.preventDefault();let url;try{const parsed=new URL(ph('phoneImportUrl').value);if(parsed.protocol!=='https:'||!/(^|\.)(cian\.ru|avito\.ru)$/i.test(parsed.hostname)||parsed.username||parsed.password||parsed.port)throw Error('Введите HTTPS-ссылку ЦИАН или Авито.');url=parsed.href;}catch(error){phoneToast(error.message==='Invalid URL'?'Проверьте ссылку объявления.':error.message);return;}try{await phoneImportListing(url);}catch(error){phoneToast(error.message);}};
ph('phonePreparePhotos').onclick=async()=>{if(!phoneState.detailId)return;ph('phonePreparePhotos').disabled=true;try{const result=await phoneApi('cards/'+phoneState.detailId+'/prepare',{});phoneSetJob(result.job_id,'prepare');ph('phoneMediaStatus').textContent='Mac загружает фотографии…';phoneToast('Mac загружает фото. Они появятся в карточке после подготовки.');}catch(error){phoneToast(error.message);}finally{ph('phonePreparePhotos').disabled=false;}};
document.addEventListener('visibilitychange',()=>{if(document.hidden){clearTimeout(phonePollTimer);clearTimeout(phoneJobTimer);clearInterval(phoneJobProgressTimer);}else if(phoneState.paired){phoneRefreshCards().catch(()=>{});if(phoneState.job)phoneCheckJob();}});window.addEventListener('online',()=>{if(phoneState.paired)phoneRefreshCards().catch(()=>{});});
initializePwaInstall({container:ph('phoneInstall')});
initializePwaInstall({container:ph('phoneInstallTop'),registerWorker:false});
async function phoneStart(){
  if(phoneStarting)return;phoneStarting=true;const epoch=phoneState.authEpoch,deadline=performance.now()+9000,load=phoneLoadBegin();ph('phoneReconnect').disabled=true;
  try{
    let pairCode='';if(location.hash.length>1){try{pairCode=decodeURIComponent(location.hash.slice(1)).trim();}catch{pairCode=location.hash.slice(1).trim();}history.replaceState({},'',location.pathname+location.search);}
    if(pairCode){ph('phonePairCode').value=pairCode;await phonePair(pairCode,{deadline,loading:load});return;}
    const state=await phoneApi('state',undefined,{deadline});if(epoch!==phoneState.authEpoch)return;
    if(!state.paired){phoneAuth(false);phoneLoadFinish(load,'code');ph('phoneConnection').textContent='Нужно подключение';return;}
    phoneLoadAccepted(load);phoneState.csrf=state.csrfToken||'';phoneAuth(true);phoneView('cards');ph('phoneReconnect').classList.add('hidden');await phoneRefreshCards({reset:true,deadline});phoneLoadFinish(load);phoneResumeJob(state.activeJob);const card=new URLSearchParams(location.search).get('card');if(card)await phoneOpenCard(card,{deadline});
  }catch(error){if(epoch!==phoneState.authEpoch&&error.authEpoch!==phoneState.authEpoch)return;phoneLoadFinish(load,error.status===401?'code':'error',error,phoneState.paired?'Cards':'Connection');if(epoch!==phoneState.authEpoch)return;if(error.status!==401){ph('phonePairStatus').textContent=error.message;ph('phoneReconnect').classList.remove('hidden');}ph('phoneConnection').textContent=error.status===401?'Нужно подключение':'Нет связи с Mac';}
  finally{phoneStarting=false;ph('phoneReconnect').disabled=false;}
}
ph('phoneReconnect').onclick=()=>phoneStart();
ph('phoneLoadRetry').onclick=()=>phoneStart();

async function phoneExportBlob(asset){const epoch=phoneState.authEpoch,url=new URL(asset.url,location.origin);if(url.origin!==location.origin||!/^\/api\/phone\/(exports|card-attachments)\//.test(url.pathname))throw Error('Неверный адрес файла');const response=await fetch(url.pathname,{credentials:'same-origin',cache:'no-store',signal:AbortSignal.timeout(90000)});if(epoch!==phoneState.authEpoch)throw Error('Подключение изменилось');if(response.status===401){phoneAuth(false);throw Error('Введите текущий код с компьютера');}if(!response.ok){const value=await response.json().catch(()=>({}));throw Error(value.error||'Файл недоступен');}const blob=await response.blob();if(epoch!==phoneState.authEpoch)throw Error('Подключение изменилось');return blob;}
async function phoneDownloadAsset(asset){cardDownloadBlob(await phoneExportBlob(asset),asset.name);phoneFilesUI?.refresh();}
async function phonePresentationPhotos(){const id=phoneState.detailId,epoch=phoneState.authEpoch,photos=[];for(const original of (phoneState.detail?.media||[]).slice(0,30)){const selected=original.crop||original,path=phoneAsset(selected.url);if(!path)continue;const response=await fetch(path,{credentials:'same-origin',cache:'no-store'});if(!response.ok)throw Error('Фотография недоступна на Mac');const dataUrl=await cardPhotoDataUrl(await response.blob());if(epoch!==phoneState.authEpoch||id!==phoneState.detailId)throw Error('Выбрана другая карточка');photos.push({dataUrl});}return photos;}
async function phoneEnsurePresentationCard(){if(!phoneState.paired||!phoneState.detail?.id)throw Error('Сначала откройте карточку квартиры');return phoneState.detail.id;}
function phoneInitializeCardTools(){if(phoneArchiveUI)return;
phoneArchiveUI=setupCardArchive({mount:ph('phoneArchiveMount'),getCardId:()=>phoneState.detailId,ensureCard:phoneEnsurePresentationCard,api:phoneApi,downloadAttachment:phoneDownloadAsset,toast:phoneToast});
phoneFilesUI=setupCardFiles({mount:ph('phoneFilesMount'),getCardId:()=>phoneState.detailId,api:phoneApi,download:phoneDownloadAsset,previewImage:async asset=>{const blob=await phoneExportBlob(asset);if(blob.type!=='image/png')throw Error('Нужен PNG');if(phoneExportViewerUrl)URL.revokeObjectURL(phoneExportViewerUrl);phoneExportViewerUrl=URL.createObjectURL(blob);ph('phoneViewerImage').src=phoneExportViewerUrl;ph('phonePhotoViewer').showModal();},toast:phoneToast});
}
function phoneInitializeDesigner(){
  if(phoneDesignInitializing)return phoneDesignInitializing;
  if(phonePresentationUI)return Promise.resolve(phonePresentationUI);
  const epoch=phoneState.authEpoch;
  ph('phoneDesignMount').textContent='Загружаем редактор оформления…';
  const ready=(async()=>{
    const {setupPresentationStudio}=await import('/phone/designer.mjs');
    if(epoch!==phoneState.authEpoch||!phoneState.paired)return null;
    let initialRead=null;
    const api=(path,data)=>{const request=phoneApi(path,data);if(path==='design'&&!data)initialRead=request;return request;};
    const studio=setupPresentationStudio({mount:ph('phoneDesignMount'),getCard:()=>phoneState.detail?{...phoneState.detail,identity:phoneState.detail.id}:{},getPhotos:phonePresentationPhotos,ensureCard:phoneEnsurePresentationCard,api,downloadExport:phoneDownloadAsset,getExportBlob:phoneExportBlob,getMapImage:(card,options={})=>phoneApi('listing-map',{latitude:card.fields?.latitude,longitude:card.fields?.longitude,zoom:options.zoom||15,style:options.style||'navy',...((options.pinColor||options.theme?.social?.accent)?{pinColor:options.pinColor||options.theme.social.accent}:{})}),toast:phoneToast});
    phonePresentationUI=studio;
    await (initialRead||Promise.resolve()).catch(()=>{});
    return epoch===phoneState.authEpoch&&studio===phonePresentationUI?studio:null;
  })();
  phoneDesignInitializing=ready;
  const finish=()=>{if(phoneDesignInitializing===ready)phoneDesignInitializing=null;};ready.then(finish,finish);return ready;
}
ph('phoneDesignCard').onclick=()=>phoneView('design');
ph('phoneNetworkHelp').textContent=location.protocol==='https:'?'Внешняя HTTPS-ссылка работает через мобильный интернет; Mac должен оставаться включённым.':'Локальный адрес работает в одной сети с Mac. Для мобильного интернета используйте внешнюю HTTPS-ссылку из раздела «Телефон» на компьютере.';
ph('phoneNetworkDescription').textContent=location.protocol==='https:'?'Chrome и подготовка объявлений работают на Mac. Телефон получает готовые карточки через интернет. Нужны включённый Mac и работающий внешний доступ.':'Chrome и подготовка объявлений работают на Mac. Телефон получает готовые карточки. Локальная ссылка требует общей сети; внешняя HTTPS-ссылка работает через мобильный интернет.';

async function phoneImportListing(url){
 ph('phoneImportUrl').value=url;ph('phoneImportSubmit').disabled=true;
 try{const result=await phoneApi('import',{url,loader:'chrome',timeout:180});phoneSetJob(result.job_id);return result;}
 catch(error){ph('phoneImportSubmit').disabled=false;throw error;}
}
function phoneInitializeWatch(){
 if(!phoneWatchUI)phoneWatchUI=setupListingWatch({mount:ph('phoneWatchMount'),api:phoneApi,toast:phoneToast,onScan:async url=>{phoneView('import');await phoneImportListing(url);}});
 return phoneWatchUI;
}
function phoneInitializeAudit(){
 if(!phoneImportAuditUI)phoneImportAuditUI=setupImportDiagnostics({mount:ph('phoneImportDiagnostics'),api:phoneApi,toast:phoneToast,getBundle:async()=>{const epoch=phoneState.authEpoch,r=await fetch('/api/phone/import-diagnostics/tg.zip',{credentials:'same-origin',cache:'no-store',signal:AbortSignal.timeout(30000)});if(epoch!==phoneState.authEpoch)throw Error('Подключение изменилось');if(r.status===401){phoneAuth(false);throw Error('Введите текущий код с Mac');}if(!r.ok){const v=await r.json().catch(()=>({}));throw Error(v.error||'Архив ещё не готов');}return r.blob();}});
 return phoneImportAuditUI;
}
function phoneUpdateRouteTools(){
 if(!phoneState.paired||!phoneState.detail?.id)return;
 if(!phoneRoutesUI)phoneRoutesUI=setupPhoneUniversityRoutes({mount:ph('phoneRoutesMount'),getCard:()=>phoneState.detail,api:phoneApi,toast:phoneToast,onSaved:card=>{if(card.id!==phoneState.detailId)return;phoneState.detail=card;ph('phoneDetailStamp').textContent='С Mac · '+phoneTime(card.updated);phoneFilesUI?.refresh();if(phoneState.view==='design')phonePresentationUI?.refresh();}});
 phoneRoutesUI.cardChanged(phoneState.detail);
}
phoneStart();
