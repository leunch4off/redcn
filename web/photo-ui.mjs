import {hasOwnKey} from './own-property.mjs';
import {createAiProgress} from './ai-progress.mjs';
import {clientPublicationCard} from './client-publication.mjs';
import {isAutomaticPhotoObservation,PHOTO_FEATURE_IDS} from './photo-observation-policy.mjs';
import {teacherReviewed,validTeacherReview} from './photo-teacher-review.mjs';
import {cardPhotoDataUrl} from './card-files.mjs';
import {withRequestDeadline} from './request-deadline.mjs';
import {setupFurniturePhotos} from './furniture-photos.mjs';
import {setupPhotoInstanceEditor} from './photo-instance-editor.mjs';
import {photoSources,cropRectangle,photoFileName,photoExportAsset,photoZip,confirmedPhotoFeatures} from './media-core.mjs';

// A prepared backend derivative is usable only with its own original and a
// verified selection reason. Missing OCR is never a clean-image certificate.
export function presentationAcceptedCrop(value,originalId){
 const original=value?.original,selected=value?.selected;
 if(!original||original.id!==originalId||!selected||!/^([a-f0-9]{64})$/.test(selected.id||''))return null;
 const proof=value.variantSelection,variant=!!(proof&&proof.policy==='observed-variants-v1-resolution-480-400000'&&proof.canonicalOriginalId===originalId&&proof.sourceOriginalId===value.selectedOriginal?.id&&value.selectedOriginal?.kind==='original'&&['image/jpeg','image/png','image/webp'].includes(value.selectedOriginal?.mime)&&photoSources([proof.selectedUrl]).length===1&&/^[a-f0-9]{64}$/.test(proof.groupSha||'')&&/^[a-f0-9]{64}$/.test(proof.decisionSha||'')&&proof.quality?.resolutionEligible===true&&proof.quality?.status==='resolution-checked'&&Math.min(value.selectedOriginal?.width||0,value.selectedOriginal?.height||0)>=480&&(value.selectedOriginal?.width||0)*(value.selectedOriginal?.height||0)>=400000);
 if(selected.kind==='original')return variant&&value.reason==='observed-variant'&&selected.id===proof.sourceOriginalId?{...selected,variantSelection:proof}:null;
 if(selected.kind!=='crop'||selected.originalId!==(variant?proof.sourceOriginalId:originalId))return null;
 if(variant){if(value.reason==='manual'&&selected.verification==='manual'||value.reason==='ocr-checked'&&selected.verification==='ocr-checked'&&selected.verificationEvidence?.knownRegionsExcluded===true&&selected.verificationEvidence?.postOcr==='not-detected')return {...selected,variantSelection:proof};return null;}
 if(value.reason==='manual'&&selected.verification==='manual')return selected;
 if(value.reason==='ocr-checked'&&selected.verification==='ocr-checked'&&selected.verificationEvidence?.knownRegionsExcluded===true&&selected.verificationEvidence?.postOcr==='not-detected')return selected;
 return null;
}

const photoJournalFeatures=new Set(['sofa','bedAny','chairs','tv','fridge','oven','microwave','sink','table','desk','wardrobe','washer','dishwasher','piano','coffeeMachine','mirror','curtains','coffeeTable','stove','cabinet']);
const photoJournalProviders=new Map([['redcn-furniture-v1','yolox-trained'],['redcn-furniture-world-v2','onnx-world']]);
const photoJournalDigest=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);

// Completeness of a photo pass and acceptance of its objects are independent.
// No detections never proves an empty room; a stored failure is not a scan.
export function photoObservationSummary(items=[]){
 const selected=items.filter(item=>item?.selected),reviewed=selected.filter(item=>['ok','review'].includes(item.analysis?.status)),failed=selected.filter(item=>item.analysis&&!['ok','review'].includes(item.analysis.status));
 const unknown=selected.length-reviewed.length-failed.length;
 const empty=reviewed.filter(item=>!(item.analysis.candidates||[]).length&&!validTeacherReview(item.teacherReview,item.analysis.originalId)).length;
 const pending=reviewed.reduce((total,item)=>total+(item.analysis.candidates||[]).filter(row=>!item.rejected?.[row.id]&&!item.confirmed?.[row.id]&&!teacherReviewed(item,row.id)&&!(item.analysis.autoApply===true&&isAutomaticPhotoObservation({...row,status:'auto-visible',originalId:item.analysis.originalId,photoId:item.analysis.originalId}))).length,0);
 const assistantReviewed=selected.filter(item=>validTeacherReview(item.teacherReview,item.analysis?.originalId)).length;
 const parts=['Локальный фотоанализ: '+reviewed.length+' из '+selected.length+' фото.'];
 if(failed.length)parts.push('Ошибки или незавершённый анализ: '+failed.length+'.');
 if(unknown)parts.push('Ещё не проверено: '+unknown+'.');
 if(empty)parts.push('На '+empty+' фото нет уверенных находок; это не означает отсутствие мебели.');
 if(pending)parts.push('Находки для подтверждения: '+pending+'.');
 if(assistantReviewed)parts.push('Отдельно просмотрено помощником: '+assistantReviewed+' фото.');
 return {total:selected.length,checked:reviewed.length,failed:failed.length,unprocessed:unknown,unrecognized:empty,pending,assistantReviewed,text:parts.join(' ')};
}
const photoJournalScore=value=>typeof value==='number'&&Number.isFinite(value)&&value>=0&&value<=1;
const reviewedPhotoFeatures=new Set(PHOTO_FEATURE_IDS);

/** A new inference cannot erase an operator's review of these same bytes.
 * Missing proposals retain only reviewed rows, clearly outside the new model
 * output. The caller supplies the canonical identity verified by the server. */
export function retainReviewedPhotoCandidates(previous,current,{originalId,confirmed={},rejected={},teacherReview=null}={}){
 if(!photoJournalDigest(originalId)||previous?.originalId!==originalId||current?.originalId!==originalId)return current;
 const candidates=Array.isArray(current.candidates)?[...current.candidates]:[],present=new Set(candidates.map(row=>row?.id));
 for(const row of Array.isArray(previous.candidates)?previous.candidates.slice(0,64):[]){
  if(!row||!reviewedPhotoFeatures.has(row.id)||present.has(row.id))continue;
  const confirmation=hasOwnKey(confirmed,row.id)&&confirmed[row.id],teacher=validTeacherReview(teacherReview,originalId)&&teacherReview.features.includes(row.id),reviewed=teacher||confirmation===true||confirmation&&typeof confirmation==='object'&&!Array.isArray(confirmation)||hasOwnKey(rejected,row.id)&&rejected[row.id]===true;
  if(!reviewed)continue;
  candidates.push({...JSON.parse(JSON.stringify(row)),reviewOrigin:teacher?'assistant-visual-review':'operator-kept',reviewOriginalId:originalId,autoEligible:false,automaticSuggestion:false});present.add(row.id);
 }
 return {...current,candidates};
}

/** The direct-photo action uses the same bounded review queue as batch scans.
 * It never confirms a finding or assigns an item count by itself. */
export function mergePhotoReviewJournal(previous,items,{now=new Date().toISOString(),refresh=false,rejectedFeatures=[]}={}){
 const sources=Array.isArray(items)?items.slice(0,600):[],entries=new Map(),current=new Map(),rejected=new Set(Array.isArray(rejectedFeatures)?rejectedFeatures:[]);
 const knownPrevious=previous?.version===1&&Array.isArray(previous.entries);
 for(const item of sources){const digest=item?.analysis?.originalId||item?.serverId;if(typeof item?.url==='string'&&photoJournalDigest(digest))current.set(item.url,digest);}
 function decision(digest,feature,old){
  if(rejected.has(feature))return 'rejected';
  const same=sources.filter(item=>item?.analysis?.originalId===digest);
  if(same.some(item=>item.rejected?.[feature]===true))return 'rejected';
  if(same.some(item=>item.confirmed?.[feature]&&typeof item.confirmed[feature]==='object'))return 'confirmed';
  return old;
 }
 for(const row of knownPrevious?previous.entries.slice(0,600):[]){
  if(!row||!photoJournalDigest(row.id)||!photoJournalDigest(row.originalId)||!photoJournalFeatures.has(row.featureId)||typeof row.sourceUrl!=='string'||!['pending','confirmed','rejected','stale'].includes(row.state))continue;
  const state=row.state==='pending'&&current.has(row.sourceUrl)&&current.get(row.sourceUrl)!==row.originalId?'stale':row.state;
  entries.set(row.id,{...row,state:decision(row.originalId,row.featureId,state),count:null});
 }
 let added=0;
 for(const item of sources){
  const analysis=item?.analysis,digest=analysis?.originalId;
  if(analysis?.autoApply!==true||!photoJournalProviders.has(analysis.model)||analysis.provider!==photoJournalProviders.get(analysis.model)||!['review','ok'].includes(analysis.status)||!photoJournalDigest(digest)||!photoSources([item.url]).length)continue;
  for(const candidate of Array.isArray(analysis.candidates)?analysis.candidates.slice(0,64):[]){
   if(!candidate||candidate.automaticSuggestion!==true||!photoJournalDigest(candidate.reviewId)||!photoJournalFeatures.has(candidate.id)||!photoJournalScore(candidate.confidence)||candidate.confidence<.85||(Array.isArray(analysis.unknownIds)&&analysis.unknownIds.includes(candidate.id)))continue;
   const evidence=Array.isArray(candidate.evidence)?candidate.evidence.slice(0,8):[];
   const bound=evidence.some(row=>{const region=row?.region;return row?.kind==='object-detection'&&row.provider===analysis.provider&&row.model===analysis.model&&row.source==='local-photo'&&row.originalId===digest&&row.photoId===digest&&row.confidence===candidate.confidence&&Array.isArray(region)&&region.length===4&&region.every(photoJournalScore)&&region[2]>0&&region[3]>0&&region[0]+region[2]<=1+1e-9&&region[1]+region[3]<=1+1e-9;});
   if(!bound)continue;
   const old=entries.get(candidate.reviewId);
   if(old&&(old.originalId!==digest||old.featureId!==candidate.id))continue;
   const state=decision(digest,candidate.id,['confirmed','rejected'].includes(old?.state)?old.state:'pending');
   entries.set(candidate.reviewId,{id:candidate.reviewId,originalId:digest,sourceUrl:item.url,
    featureId:candidate.id,label:typeof candidate.label==='string'?candidate.label.slice(0,150):candidate.id,
    confidence:candidate.confidence,evidence:JSON.parse(JSON.stringify(evidence)),provider:analysis.provider,model:analysis.model,
    automatic:true,state,reason:'model-unaccepted',count:null,createdAt:old?.createdAt||now,lastSeenAt:refresh?now:old?.lastSeenAt||now});
   if(!old)added++;
  }
 }
 if(!knownPrevious&&!entries.size)return null;
 const rows=[...entries.values()].sort((a,b)=>Number(current.get(a.sourceUrl)!==a.originalId)-Number(current.get(b.sourceUrl)!==b.originalId)||Number(a.state!=='pending')-Number(b.state!=='pending')||a.id.localeCompare(b.id)).slice(0,600);
 return {version:1,requested:sources.some(item=>item?.analysis?.autoApply===true),entries:rows,
  added:Math.min(600,(Number.isInteger(previous?.added)?Math.max(0,previous.added):0)+added),
  pending:rows.filter(row=>row.state==='pending').length,confirmed:rows.filter(row=>row.state==='confirmed').length,rejected:rows.filter(row=>row.state==='rejected').length};
}

export function setupPhotos({local,hosted,api,toast,read,write,onChange,getPost,onFeatures,getCardId=()=>null,getRejectedFeatures=()=>[]}){
 const pe=id=>document.getElementById(id);let items=[],active=null,epoch=0,objectUrls=[],viewerUrls=[],edits={},busy=false,zipUrl=null,sourceReadyEpoch=0,autoLoadPendingEpoch=null;
 const aiProgress=createAiProgress({after:pe('photoStatus'),id:'photoAiProgress'});
 const itemSource=url=>{const rows=typeof getPost==='function'?getPost()?.fields?.photoItems:null;return Array.isArray(rows)?rows.find(row=>row?.url===url):null;};
 const sourceCacheKey=url=>{const source=itemSource(url);return source?url+'\n'+JSON.stringify([source.photoId,source.variants||{},source.observedVariants||[]]):url;};
 const furniturePhotos=setupFurniturePhotos({getItems:()=>items,onOpen:item=>show(item),read,write});
 const instanceMount=document.createElement('div');let instanceEditor=null,instanceCard=null;
 if(local)pe('photoGallery').insertAdjacentElement('afterend',instanceMount);
 function refreshInstances(){
  if(!local)return;const id=getCardId();if(id===instanceCard)return;instanceCard=id;instanceEditor?.dispose();instanceEditor=null;
  if(/^[a-f0-9]{32}$/.test(id||''))instanceEditor=setupPhotoInstanceEditor({mount:instanceMount,api,cardId:id,loadPhoto:binary});
  else instanceMount.textContent='Сохраните карточку на Mac, чтобы сопоставить предметы на разных ракурсах.';
 }

 const photoDb=new Promise((resolve,reject)=>{const r=indexedDB.open('redcn-photos-v1',1);r.onupgradeneeded=()=>r.result.createObjectStore('photos',{keyPath:'url'});r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(r.error);});photoDb.catch(()=>{});
 async function cacheGet(url,key=url){try{const db=await photoDb;return await new Promise((resolve,reject)=>{const r=db.transaction('photos').objectStore('photos').get(key);r.onsuccess=()=>resolve(r.result?.cacheVersion===2?{...r.result,url}:null);r.onerror=()=>reject(r.error);});}catch{return null;}}
 async function cachePut(item){try{const db=await photoDb;await new Promise((resolve,reject)=>{const tx=db.transaction('photos','readwrite');tx.objectStore('photos').put({url:item.cacheKey||item.url,originalUrl:item.url,cacheVersion:2,blob:item.blob,width:item.width,height:item.height,mime:item.mime,cropBlob:item.cropBlob,cropMeta:item.cropMeta,variantBlob:item.variantBlob,variantMeta:item.variantMeta,variantReview:item.variantReview,serverId:item.serverId,analysis:item.analysis,at:Date.now()});tx.oncomplete=resolve;tx.onerror=()=>reject(tx.error);});}catch{toast('Фото готово, но не поместилось в кеш браузера. Оригинал можно скачать.');}}
 function state(){
  const journal=mergePhotoReviewJournal(edits.reviewJournal,items,{rejectedFeatures:getRejectedFeatures()});
  if(journal)edits.reviewJournal=journal;
  return {...(journal?{reviewJournal:journal}:{}),order:items.map(i=>i.url),excluded:items.filter(i=>!i.selected).map(i=>i.url),originals:items.filter(i=>i.preferOriginal).map(i=>i.url),crops:Object.fromEntries(items.filter(i=>!i.preferOriginal&&i.manualCropOverride&&i.cropMeta?.verification==='manual'&&i.cropMeta?.margins).map(i=>[i.url,i.cropMeta.margins])),observations:Object.fromEntries(items.filter(i=>i.analysis).map(i=>[i.url,{analysis:i.analysis,confirmed:i.confirmed||{},rejected:i.rejected||{},...(i.teacherReview?{teacherReview:i.teacherReview}:{})}]))};}
 function changed(){edits=state();onChange?.();}
 async function preparePresentation(){
  const token=epoch;let cancelled=false,timer;
  const valid=()=>{if(cancelled)throw Error('Дождитесь завершения обработки фото и повторите экспорт.');if(token!==epoch)throw Error('Карточка изменилась. Обновите макет.');};
  const wait=async()=>{valid();while(sourceReadyEpoch!==token||autoLoadPendingEpoch===token||busy||items.some(item=>item.loadingPending||item.acceptedPending)){await new Promise(resolve=>setTimeout(resolve,40));valid();}};
  const prepare=async()=>{await wait();const selected=items.filter(item=>item.selected).slice(0,30);let next=0;await Promise.all(Array.from({length:Math.min(3,selected.length)},async()=>{while(next<selected.length){valid();const item=selected[next++];await ensure(item,false,{verifyOriginal:true});valid();}}));await wait();};
  // An export does not cancel a user's photo job when its waiting budget ends.
  // It simply refuses to save a half-settled source for the client material.
  try{await Promise.race([prepare(),new Promise((_,reject)=>{timer=setTimeout(()=>{cancelled=true;reject(Error('Обработка фото ещё идёт. Дождитесь её завершения и повторите экспорт.'));},30000);})]);}
  finally{cancelled=true;clearTimeout(timer);}
 }
 function status(){for(const id of ['photoAnalyze','photoLoad','photoRefresh','photoDownload','photoDownloadCropped','photoCropAuto','photoAttach','photoCropApply','photoAttachOne'])pe(id).disabled=busy||(id==='photoAnalyze'&&!local);const ready=items.filter(i=>i.blob).length,crops=items.filter(i=>i.cropBlob).length,errors=items.filter(i=>i.error).length;pe('photoStatus').textContent=ready+' / '+items.length+' оригиналов загружено · '+crops+' копий кадрировано'+(busy?' · обработка…':'')+(errors?' · '+errors+' требуют внимания':'');}
 function assetUrl(blob){const url=URL.createObjectURL(blob);objectUrls.push(url);return url;}
 function imageNode(blob,url,alt){const image=document.createElement('img');image.src=blob?assetUrl(blob):url;image.alt=alt;image.loading='lazy';image.referrerPolicy='no-referrer';return image;}
 function render(){furniturePhotos.render();refreshInstances();for(const url of objectUrls)URL.revokeObjectURL(url);objectUrls=[];pe('photoGallery').replaceChildren();pe('photoCard').classList.toggle('hidden',!items.length);status();
  items.forEach((item,index)=>{const card=document.createElement('article'),open=document.createElement('button'),meta=document.createElement('p'),tools=document.createElement('div'),label=document.createElement('label'),selected=document.createElement('input');card.className='photo-tile';open.className='photo-open';open.type='button';open.setAttribute('aria-label','Открыть фото '+(index+1));open.append(imageNode(item.preferOriginal?item.blob:item.cropBlob||item.variantBlob||item.blob,item.url,'Фото '+(index+1)));open.onclick=()=>show(item);selected.type='checkbox';selected.checked=item.selected;selected.onchange=()=>{item.selected=selected.checked;changed();emitFeatures();renderObservations();furniturePhotos.render();};label.append(selected,document.createTextNode((index===items.findIndex(i=>i.selected)?'Обложка · ':'')+'Фото '+(index+1)));meta.className='small';meta.textContent=item.error||item.message||(item.blob?item.width+' × '+item.height+' · '+(item.cropBlob?'копия кадрирована':'оригинал сохранён'):'Ожидает загрузки');
   const move=(text,delta)=>{const b=document.createElement('button');b.className='btn';b.textContent=text;b.setAttribute('aria-label',(delta<0?'Поднять':'Опустить')+' фото '+(index+1));b.disabled=index+delta<0||index+delta>=items.length;b.onclick=()=>{[items[index],items[index+delta]]=[items[index+delta],items[index]];changed();render();};return b;};tools.className='photo-tile-tools';const cover=document.createElement('button');cover.className='btn';cover.textContent='На обложку';cover.disabled=index===0&&item.selected;cover.onclick=()=>{item.selected=true;items.splice(index,1);items.unshift(item);changed();render();};tools.append(label,move('↑',-1),move('↓',1),cover);card.append(open,meta,tools);pe('photoGallery').append(card);});renderObservations();
 }
 async function binary(id,{signal}={}){return withRequestDeadline(25000,async requestSignal=>{const r=await fetch('/api/local/media/'+id,{headers:{'X-Redcn-Token':document.querySelector('meta[name="redcn-token"]')?.content||''},cache:'no-store',signal:requestSignal});if(!r.ok)throw Error('Локальная копия фото недоступна');return r.blob();},{signal});}
 async function dimensions(blob){if(blob.size>12_000_000||!['image/jpeg','image/png','image/webp'].includes(blob.type))throw Error('Фото: JPEG/PNG/WebP до 12 МБ');const bitmap=await createImageBitmap(blob);try{cropRectangle(bitmap.width,bitmap.height);return {width:bitmap.width,height:bitmap.height};}finally{bitmap.close();}}
 function reconcileObservationOriginal(item,originalId){
  if(!item.analysis||item.analysis.originalId===originalId)return false;
  item.analysis=null;item.confirmed={};item.rejected={};item.teacherReview=null;return true;
 }
 async function acceptedCrop(item,knownChoice=null){
  if(item.acceptedPending)return item.acceptedPending;
  if(!local||!knownChoice&&(item.preferOriginal||item.expectedMargins||item.manualCropOverride&&item.cropMeta?.verification==='manual'||item.acceptedCheckedAt&&Date.now()-item.acceptedCheckedAt<5000))return;
  const pending=(async()=>{try{
   const source=itemSource(item.url),choice=knownChoice||await api('photo',{action:'accepted',url:item.url,...(source?{source}:{})});
   if(/^[a-f0-9]{64}$/.test(choice.original?.id||'')){
    const changedOriginal=choice.original.id!==item.serverId;
    if(changedOriginal){const blob=await binary(choice.original.id),size=await dimensions(blob);Object.assign(item,size,{blob,mime:blob.type,serverId:choice.original.id,cropBlob:null,cropMeta:null,variantBlob:null,variantMeta:null});}
    const changedObservation=reconcileObservationOriginal(item,choice.original.id);
    if(changedOriginal||changedObservation){await cachePut(item);changed();emitFeatures();}
   }
   if(item.preferOriginal||item.expectedMargins||item.manualCropOverride&&item.cropMeta?.verification==='manual')return;
   const selected=presentationAcceptedCrop(choice,item.serverId);item.variantReview=choice.variantReview||null;
   if(selected){let preparedBlob=selected.kind==='crop'&&item.cropMeta?.id===selected.id?item.cropBlob:selected.kind==='original'&&item.variantMeta?.id===selected.id?item.variantBlob:null;if(!preparedBlob){preparedBlob=await binary(selected.id);await dimensions(preparedBlob);}if(item.preferOriginal||item.expectedMargins||item.manualCropOverride&&item.cropMeta?.verification==='manual')return;if(selected.kind==='crop'){item.cropBlob=preparedBlob;item.cropMeta=selected;item.variantBlob=null;item.variantMeta=null;}else{item.variantBlob=preparedBlob;item.variantMeta=selected;item.cropBlob=null;item.cropMeta=null;}item.message=selected.kind==='original'?'Принят наблюдаемый вариант подходящего размера; исходники сохранены':selected.verification==='manual'?'Ручная производная с Mac':'Проверенная OCR-копия с Mac';await cachePut(item);}
   else if((item.cropMeta||item.variantMeta)&&!item.manualCropOverride){item.cropBlob=null;item.cropMeta=null;item.variantBlob=null;item.variantMeta=null;await cachePut(item);}
  }catch{if(item.cropMeta?.verification==='ocr-checked'||item.variantMeta){item.cropBlob=null;item.cropMeta=null;item.variantBlob=null;item.variantMeta=null;}}})();
  item.acceptedPending=pending;
  try{return await pending;}finally{if(item.acceptedPending===pending){delete item.acceptedPending;item.acceptedCheckedAt=Date.now();}}
 }
 // Cache hydration and the designer can request the same item concurrently.
 // A ready original is not a ready selected image until its crop is settled.
 async function ensure(item,refresh=false,{verifyOriginal=false}={}){if(item.loadingPending)return item.loadingPending;if(item.blob&&!refresh&&!verifyOriginal)return item;const pending=loadOriginal(item,refresh);item.loadingPending=pending;try{return await pending;}finally{if(item.loadingPending===pending)delete item.loadingPending;}}
 async function loadOriginal(item,refresh=false){
  try{item.error='';item.message='Загружается…';status();let blob,metadata,choice;
   // The server's canonical identity is authoritative. Browser bitmap caches
   // never replace the newer observation/decisions loaded with the card.
   if(local){const source=itemSource(item.url);if(!refresh)try{choice=await api('photo',{action:'accepted',url:item.url,...(source?{source}:{})});metadata=choice.original;}catch{}if(!metadata?.id)metadata=await api('photo',{url:item.url,refresh,action:'load',...(source?{source}:{})});}
   const saved=refresh?null:item.blob?Object.fromEntries(['blob','width','height','mime','cropBlob','cropMeta','variantBlob','variantMeta','variantReview','serverId','analysis'].map(key=>[key,item[key]])):await cacheGet(item.url,item.cacheKey);
   if(saved?.blob&&(!local||saved.serverId===metadata.id)){
    const {analysis:cachedAnalysis,confirmed:cachedConfirmation,rejected:cachedRejection,...cached}=saved;
    const changedObservation=local?reconcileObservationOriginal(item,metadata.id):false;
    Object.assign(item,cached,{selected:item.selected});item.serverId=local?metadata.id:null;
    if(!item.analysis&&local&&cachedAnalysis?.originalId===metadata.id)item.analysis=cachedAnalysis;
    item.message='Оригинал из кеша';if(item.preferOriginal||item.expectedMargins&&JSON.stringify(item.expectedMargins)!==JSON.stringify(item.cropMeta?.margins)){delete item.cropBlob;delete item.cropMeta;}
    if(item.expectedMargins&&!item.cropBlob)await applyClientCrop(item,item.expectedMargins);
    await acceptedCrop(item,choice);if(changedObservation){await cachePut(item);changed();emitFeatures();}return item;
   }
   if(local){blob=await binary(metadata.id);item.serverId=metadata.id;}
   else{blob=await withRequestDeadline(25000,async signal=>{const r=await fetch(hosted?'/api/photo?url='+encodeURIComponent(item.url):item.url,{referrerPolicy:'no-referrer',signal});if(!r.ok)throw Error('Источник не отдал фото ('+r.status+')');return r.blob();});}
   const size=await dimensions(blob),changedObservation=reconcileObservationOriginal(item,local?metadata.id:null);Object.assign(item,size,{blob,mime:blob.type,message:'Оригинал сохранён',cropBlob:null,cropMeta:null,variantBlob:null,variantMeta:null});await cachePut(item);if(item.expectedMargins)await applyClientCrop(item,item.expectedMargins);await acceptedCrop(item,choice);if(changedObservation){changed();emitFeatures();}return item;
  }catch(error){item.error=(error.message||'Ошибка фото')+(!local?' · Если источник запрещает CORS, откройте локальный redcn или прикрепите оригиналы.':'');item.message='';throw error;}
 }
 async function loadAll(refresh=false){if(busy){toast('Дождитесь обработки фотографий.');return;}busy=true;status();const currentEpoch=epoch,queue=items.filter(i=>i.selected);let next=0;try{await Promise.all(Array.from({length:Math.min(3,queue.length)},async()=>{while(next<queue.length){if(epoch!==currentEpoch)return;const item=queue[next++];try{await ensure(item,refresh);}catch{}if(epoch===currentEpoch)render();}}));}finally{busy=false;if(epoch===currentEpoch){render();if(local&&pe('photoAnalyzeAuto').checked)await analyzeAll();}}}
 async function clientCrop(item,margins){await ensure(item);return applyClientCrop(item,margins);}
 async function applyClientCrop(item,margins){const bitmap=await createImageBitmap(item.blob);try{const rect=cropRectangle(bitmap.width,bitmap.height,margins),canvas=document.createElement('canvas');canvas.width=rect.width;canvas.height=rect.height;canvas.getContext('2d').drawImage(bitmap,rect.x,rect.y,rect.width,rect.height,0,0,rect.width,rect.height);const blob=await new Promise((resolve,reject)=>canvas.toBlob(b=>b?resolve(b):reject(Error('Не удалось создать кадр')),'image/jpeg',.95));item.cropBlob=blob;item.cropMeta={margins,rect,width:rect.width,height:rect.height,mime:'image/jpeg',verification:'manual'};item.expectedMargins=margins;item.preferOriginal=false;item.manualCropOverride=true;item.error='';item.message='Ручная граница применена к копии';await cachePut(item);}finally{bitmap.close();}}
 async function cropItem(item,margins){await ensure(item);if(local){const source=itemSource(item.url),original=await api('photo',{action:'load',url:item.url,...(source?{source}:{})});item.serverId=original.id;let target=original.id,choice=null;if(!margins){choice=await api('photo',{action:'accepted',url:item.url,...(source?{source}:{})});const accepted=presentationAcceptedCrop(choice,original.id);if(accepted&&choice.reason==='manual'){item.message='Сохранённая ручная граница имеет приоритет.';return;}if(accepted&&choice.variantSelection)target=choice.variantSelection.sourceOriginalId;}const result=await api('photo',{action:'crop',id:target,...(margins?{margins}:{})});if(choice?.variantSelection&&target!==original.id)result.variantSelection=choice.variantSelection;if(result.kind==='crop'){item.cropBlob=await binary(result.id);item.cropMeta=result;item.preferOriginal=false;item.manualCropOverride=result.verification==='manual'&&!!margins;item.expectedMargins=item.manualCropOverride?result.margins:undefined;await cachePut(item);}item.message=result.message;item.error=result.status==='review'?result.message:'';delete item.acceptedCheckedAt;if(!margins)await acceptedCrop(item);}else if(margins)await clientCrop(item,margins);else throw Error('Автоматический поиск метки доступен в локальном redcn. Здесь можно задать границу кадра вручную.');}
 function downloadPhoto(name,blob){const a=document.createElement('a'),url=URL.createObjectURL(blob);a.href=url;a.download=name;if(name.endsWith('.zip')){if(zipUrl)URL.revokeObjectURL(zipUrl);zipUrl=url;pe('photoZipLink').href=url;pe('photoZipLink').download=name;pe('photoZipLink').classList.remove('hidden');}document.body.append(a);a.click();a.remove();if(!name.endsWith('.zip'))setTimeout(()=>URL.revokeObjectURL(url),5000);}
 function renderVariants(item){
  let panel=pe('photoVariantReview');if(!panel){panel=document.createElement('details');panel.id='photoVariantReview';pe('photoViewStatus').insertAdjacentElement('afterend',panel);}panel.replaceChildren();const rows=item.variantReview?.candidates||[];panel.classList.toggle('hidden',!rows.length);if(!rows.length)return;
  const summary=document.createElement('summary');summary.textContent='Варианты источника · '+rows.length;panel.append(summary);
  for(const [index,row] of rows.entries()){const text=document.createElement('p');text.className='small';text.textContent=(index+1)+'. '+(row.status==='available'?row.width+' × '+row.height+' · '+(row.quality?.resolutionEligible?'размер подходит для публикации':'маленькое превью, автоматическая замена отключена'):'Не загружен · '+(row.message||row.errorClass||'источник недоступен'))+'. '+(row.markState==='not-detected'?'OCR не обнаружил метку; это не гарантирует её отсутствие.':'Метка требует отдельной проверки.');
   if(photoSources([row.url]).length){const link=document.createElement('a');link.href=row.url;link.target='_blank';link.rel='noopener noreferrer';link.textContent=' Источник ↗';text.append(link);}
   if(row.status==='available'&&/^[a-f0-9]{64}$/.test(row.id||'')){const button=document.createElement('button');button.type='button';button.className='btn';button.textContent='Скачать вариант';button.onclick=async()=>{try{downloadPhoto(String(items.indexOf(item)+1).padStart(2,'0')+'-source-variant-'+(index+1)+'.'+({'image/png':'png','image/webp':'webp'}[row.mime]||'jpg'),await binary(row.id));}catch(error){toast(error.message);}};text.append(' ',button);}panel.append(text);
  }
 }
 async function exportPhotos(cropped=false){if(busy){toast('Дождитесь обработки фотографий.');return;}busy=true;status();try{const files=[],manifest=[],post=clientPublicationCard(getPost?.()||{});for(let index=0;index<items.length;index++){const i=items[index];if(!i.selected)continue;try{await ensure(i);if(cropped)await acceptedCrop(i);const asset=photoExportAsset(i,cropped),name=photoFileName(index,asset.blob.type,asset.kind==='crop');files.push({name,blob:asset.blob});manifest.push({name,sourceUrl:i.url,originalWidth:i.width,originalHeight:i.height,kind:asset.kind,reason:asset.reason,crop:asset.crop,variant:asset.variant,selectedId:asset.kind==='crop'?i.cropMeta?.id:asset.kind==='variant'?i.variantMeta?.id:i.serverId,...(asset.reason==='original-fallback'?{note:i.error||i.message||'Оригинал сохранён: проверенной обрезки нет'}:{})});}catch(error){manifest.push({sourceUrl:i.url,error:error.message});}}
   if(!files.length)throw Error('Нет готовых фотографий для скачивания. Проверьте сообщения у фото.');const count=files.length,fallbacks=manifest.filter(row=>row.reason==='original-fallback').length;files.push({name:'manifest.json',blob:new Blob([JSON.stringify(clientPublicationCard({version:1,listingUrl:post.url,photos:manifest}),null,2)],{type:'application/json'})},{name:'post.txt',blob:new Blob([[post.title,post.body,post.tags,post.url].filter(Boolean).join('\n\n')],{type:'text/plain;charset=utf-8'})});downloadPhoto('redcn-'+(cropped?'prepared':'originals')+'.zip',await photoZip(files));toast('ZIP готов: '+count+' фото'+(fallbacks?' · '+fallbacks+' сохранены оригиналами':'')+(manifest.some(r=>r.error)?' · ошибки перечислены в manifest.json':''));
  }catch(error){toast(error.message);}finally{busy=false;render();}}
 function show(item){active=item;renderVariants(item);for(const u of viewerUrls)URL.revokeObjectURL(u);viewerUrls=[];const viewUrl=blob=>{const url=URL.createObjectURL(blob);viewerUrls.push(url);return url;};pe('photoDialog').showModal();pe('photoDialogTitle').textContent='Фото '+(items.indexOf(item)+1);pe('photoOriginalLink').href=item.url;pe('photoViewStatus').textContent=item.error||item.message||'Оригинал из источника';pe('photoOriginalView').src=item.blob?viewUrl(item.blob):item.url;const prepared=!item.preferOriginal&&(item.cropBlob||item.variantBlob);pe('photoCropView').classList.toggle('hidden',!prepared);if(prepared)pe('photoCropView').src=viewUrl(prepared);pe('photoCropView').alt=item.cropBlob?'Кадрированная копия фото':'Принятый наблюдаемый вариант фото';const caption=pe('photoCropView').parentElement.querySelector('figcaption');if(caption)caption.textContent=item.cropBlob?'Кадрированная копия':'Принятый вариант';for(const side of ['top','bottom','left','right'])pe('crop-'+side).value=item.cropMeta?.margins?.[side]||0;}

 // Suggestions stay separate from listing facts until explicitly confirmed.
 const analyzeButton=pe('photoAnalyze'),analyzeAuto=pe('photoAnalyzeAuto'),observations=pe('photoObservations');analyzeButton.disabled=!local;analyzeAuto.checked=local&&read('most-photo-analyze',true);analyzeAuto.disabled=!local;analyzeAuto.onchange=()=>write('most-photo-analyze',analyzeAuto.checked);
 function emitFeatures(){onFeatures?.(confirmedPhotoFeatures(items));}
 function observationDetail(item,row){
  const journal=edits.reviewJournal?.entries,pending=Array.isArray(journal)&&journal.some(entry=>entry?.originalId===item.analysis.originalId&&entry.featureId===row.id&&entry.state==='pending');
  const decision=item.rejected?.[row.id]?' · отклонено':item.confirmed?.[row.id]?' · подтверждено':row.automaticSuggestion&&pending?' · автодобавлено в журнал · требует подтверждения':'';
  if(teacherReviewed(item,row.id)&&!item.confirmed?.[row.id])return row.label+decision+' · проверено помощником по этому фото · не оценка локальной модели';
  if(row.reviewOrigin==='assistant-visual-review')return row.label+decision+' · разбор помощником';
  if(row.reviewOrigin==='operator-kept'&&row.reviewOriginalId===item.analysis.originalId)return row.label+decision+' · сохранено решение оператора для этого оригинала';
  return row.label+decision+' · '+(item.analysis.provider==='yolox-trained'?'оценка '+Number(row.confidence).toFixed(2):Math.round(row.confidence*100)+'%')+' · '+(row.evidence||[]).map(e=>e.text||e.label).join('; ');
 }
 function renderObservations(){observations.replaceChildren();const summary=photoObservationSummary(items),found=items.filter(i=>i.selected&&i.analysis);observations.classList.toggle('hidden',!summary.total);if(!summary.total)return;const title=document.createElement('h3'),help=document.createElement('p');title.textContent='Предметы на фотографиях';help.className='small';help.textContent=summary.text+' Автодобавление сохраняет находки ИИ в журнал проверки. Предположения обученной модели требуют подтверждения перед включением в описание. Отметьте видимые предметы; ошибочные снимите. Оценка модели не гарантирует точность, количество между ракурсами не суммируется.';observations.append(title,help);
  for(const item of found){const section=document.createElement('div'),heading=document.createElement('p');heading.textContent='Фото '+(items.indexOf(item)+1)+' · '+item.analysis.engine;section.append(heading);if(!item.analysis.candidates?.length){const empty=document.createElement('p');empty.className='small';empty.textContent=!['ok','review'].includes(item.analysis.status)?'Анализ этого фото не завершён. Повторите анализ; сохранённый оригинал доступен.':validTeacherReview(item.teacherReview,item.analysis.originalId)?'Фото просмотрено помощником. Отдельные предметы не отмечены; это не означает их отсутствие.':'Предметы не распознаны уверенно. Можно отметить мебель в карточке вручную.';section.append(empty);}
   for(const row of item.analysis.candidates||[]){const line=document.createElement('label'),check=document.createElement('input'),count=document.createElement('input'),detail=document.createElement('span');check.type='checkbox';check.checked=!item.rejected?.[row.id]&&(!!item.confirmed?.[row.id]||teacherReviewed(item,row.id)||(item.analysis.autoApply===true&&isAutomaticPhotoObservation({...row,status:'auto-visible',originalId:item.analysis.originalId,photoId:item.analysis.originalId})));count.type='number';count.min='1';count.max='12';count.step='1';count.placeholder='Кол-во';count.style.width='90px';count.setAttribute('aria-label',row.label+': количество на фото '+(items.indexOf(item)+1));count.value=item.confirmed?.[row.id]?.count??row.count??'';detail.textContent=observationDetail(item,row);line.title='Проверьте видимый предмет. Неизвестное не означает отсутствие.';line.append(check,detail,count);line.style.display='flex';line.style.gap='8px';line.style.alignItems='center';
    const update=async()=>{item.confirmed??={};item.rejected??={};const n=Number(count.value);if(check.checked){delete item.rejected[row.id];item.confirmed[row.id]={count:count.value&&Number.isInteger(n)&&n>=1&&n<=12?n:null};}else{delete item.confirmed[row.id];item.rejected[row.id]=true;}await cachePut(item);changed();emitFeatures();detail.textContent=observationDetail(item,row);};check.onchange=update;count.onchange=update;section.append(line);}
   observations.append(section);}
 }
 async function analyzeAll(){
  if(busy)return;if(!local){toast('Анализ фото доступен в локальном redcn на Mac.');return;}
  busy=true;status();const token=epoch,queue=items.filter(i=>i.selected);aiProgress.clear();
  if(edits.reviewJournal?.version===1)edits.reviewJournal={...edits.reviewJournal,added:0};
  try{
   for(const [index,item] of queue.entries()){
    if(epoch!==token)break;
    const started=performance.now();
    aiProgress.update({operation:'furniture',phase:'preparing',active:true,photoIndex:index+1,photoTotal:queue.length,elapsedSeconds:0});
    try{
     await ensure(item,false,{verifyOriginal:true});const originalId=item.serverId;
     if(!photoJournalDigest(originalId))throw Error('Не удалось подтвердить оригинал фото. Повторите загрузку.');
     aiProgress.update({operation:'furniture',phase:'analyzing',active:true,photoIndex:index+1,photoTotal:queue.length,elapsedSeconds:(performance.now()-started)/1000});
     const analysis=await api('photo',{action:'analyze',id:originalId});
     if(epoch!==token)break;
     if(analysis.originalId!==originalId)throw Error('Ответ анализа относится к другому оригиналу. Повторите анализ фото.');
     aiProgress.update({operation:'furniture',phase:['review','ok'].includes(analysis.status)?'complete':'unavailable',elapsedSeconds:(performance.now()-started)/1000,...analysis.ai,active:false,photoIndex:index+1,photoTotal:queue.length});
     if(item.analysis?.originalId!==analysis.originalId){item.confirmed={};item.rejected={};item.teacherReview=null;}
     item.analysis=retainReviewedPhotoCandidates(item.analysis,analysis,{originalId,confirmed:item.confirmed,rejected:item.rejected,teacherReview:item.teacherReview});item.confirmed??={};item.message=item.analysis.message;
     const journal=mergePhotoReviewJournal(edits.reviewJournal,items,{refresh:true,rejectedFeatures:getRejectedFeatures()});
     if(journal)edits.reviewJournal=journal;
     await cachePut(item);
    }catch(error){if(epoch!==token)break;aiProgress.stop('error');item.error=error.message;}
    if(epoch===token)render();
   }
   if(epoch===token){changed();emitFeatures();}
  }finally{busy=false;aiProgress.stop();if(epoch===token)render();}
 }
 analyzeButton.onclick=()=>analyzeAll();
 pe('photoAutoLoad').checked=read('most-photo-autoload',true);pe('photoAutoLoad').onchange=()=>write('most-photo-autoload',pe('photoAutoLoad').checked);
 pe('photoLoad').onclick=()=>loadAll();pe('photoRefresh').onclick=()=>loadAll(true);pe('photoDownload').onclick=()=>exportPhotos();pe('photoDownloadCropped').onclick=()=>exportPhotos(true);
 pe('photoCropAuto').onclick=async()=>{if(busy)return;if(!local){toast('Для OCR запустите локальный redcn. Ручное кадрирование доступно в просмотре фото.');return;}busy=true;status();const token=epoch;try{for(const i of items.filter(i=>i.selected)){if(epoch!==token)break;try{await cropItem(i);}catch(error){i.error=error.message;}if(epoch===token)render();}changed();toast('Проверка кадрирования завершена. Фото с сомнительным результатом отмечены.');}finally{busy=false;render();}};
 pe('photoClose').onclick=()=>pe('photoDialog').close();pe('photoDialog').addEventListener('click',event=>{if(event.target===pe('photoDialog'))pe('photoDialog').close();});
 pe('photoDownloadOne').onclick=async()=>{if(!active)return;try{await ensure(active);downloadPhoto(photoFileName(items.indexOf(active),active.blob.type),active.blob);show(active);}catch(error){toast(error.message);}};
 pe('photoCropApply').onclick=async()=>{if(!active||busy)return;const margins=Object.fromEntries(['top','bottom','left','right'].map(k=>[k,Number(pe('crop-'+k).value)]));busy=true;try{const selected=pe('cropAll').checked?items.filter(i=>i.selected):[active];let done=0;for(const i of selected){try{await cropItem(i,margins);if(!i.error)done++;}catch(error){i.error=error.message;}}changed();render();show(active);toast('Кадрировано: '+done+' из '+selected.length+'. Оригиналы сохранены.');}finally{busy=false;}};
 pe('photoReset').onclick=async()=>{if(!active)return;active.cropBlob=null;active.cropMeta=null;active.variantBlob=null;active.variantMeta=null;active.expectedMargins=null;active.preferOriginal=true;active.manualCropOverride=false;active.error='';active.message='Показан оригинал';await cachePut(active);changed();render();show(active);};
 pe('photoDownloadCropOne').onclick=()=>{if(active?.cropBlob)downloadPhoto(photoFileName(items.indexOf(active),'image/jpeg',true),active.cropBlob);else toast('Сначала создайте кадрированную копию.');};
 pe('photoFiles').onchange=async()=>{try{const files=[...pe('photoFiles').files];const queue=active?[active]:items.filter(i=>!i.blob);if(files.length>queue.length)throw Error('Прикрепите не больше файлов, чем незагруженных фото; либо откройте нужное фото.');for(let n=0;n<files.length;n++){const file=files[n],item=queue[n],size=await dimensions(file);let serverId=null;if(local){const encoded=await new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(r.result);r.onerror=()=>reject(r.error);r.readAsDataURL(file);});serverId=(await api('photo',{action:'attach',url:item.url,data:encoded})).id;}Object.assign(item,size,{blob:file,mime:file.type,error:'',message:'Оригинал прикреплён вручную',serverId,cropBlob:null,cropMeta:null,variantBlob:null,variantMeta:null,variantReview:null,analysis:null,confirmed:{},teacherReview:null});await cachePut(item);changed();emitFeatures();}render();if(active)show(active);toast('Оригиналы прикреплены в выбранном порядке.');}catch(error){toast(error.message);}finally{pe('photoFiles').value='';}};
 pe('photoAttach').onclick=()=>{active=null;pe('photoFiles').click();};pe('photoAttachOne').onclick=()=>pe('photoFiles').click();
 return {state,preparePresentation,async presentationPhotos(){const token=epoch,selected=items.filter(item=>item.selected).slice(0,30),photos=new Array(selected.length);let next=0;await Promise.all(Array.from({length:Math.min(3,selected.length)},async()=>{while(next<selected.length){const index=next++,item=selected[index];await ensure(item);await acceptedCrop(item);if(token!==epoch)throw Error('Карточка изменилась. Обновите макет.');const asset=photoExportAsset(item,true),blob=asset.blob;if(blob)photos[index]={dataUrl:await cardPhotoDataUrl(blob),originalId:item.serverId||'',selectedId:asset.kind==='crop'?item.cropMeta?.id||'':asset.kind==='variant'?item.variantMeta?.id||'':item.serverId||'',selection:asset.kind==='crop'?item.cropMeta?.verification||'manual':asset.kind==='variant'?'observed-variant':'original'};}}));return photos.filter(Boolean);},setSources(urls,saved={}){epoch++;sourceReadyEpoch=0;autoLoadPendingEpoch=epoch;aiProgress.clear();if(zipUrl){URL.revokeObjectURL(zipUrl);zipUrl=null;}pe('photoZipLink').classList.add('hidden');edits=saved||{};const safe=photoSources(urls),order=[...new Set([...(Array.isArray(edits.order)?edits.order:[]).filter(u=>safe.includes(u)),...safe])];items=order.map(url=>({url,cacheKey:sourceCacheKey(url),selected:!edits.excluded?.includes(url),preferOriginal:edits.originals?.includes(url)===true,manualCropOverride:!edits.originals?.includes(url)&&!!edits.crops?.[url],expectedMargins:edits.originals?.includes(url)?undefined:edits.crops?.[url],analysis:edits.observations?.[url]?.analysis,confirmed:edits.observations?.[url]?.confirmed||{},rejected:edits.observations?.[url]?.rejected||{},teacherReview:edits.observations?.[url]?.teacherReview||null}));active=null;render();const token=epoch;const start=()=>{if(token!==epoch)return;if(!pe('photoAutoLoad').checked){autoLoadPendingEpoch=null;return;}if(busy){setTimeout(start,250);return;}autoLoadPendingEpoch=null;loadAll();};Promise.all(items.map(async item=>{const cached=await cacheGet(item.url,item.cacheKey);if(epoch===token&&cached?.blob)try{await ensure(item);}catch{}})).then(()=>{if(epoch===token){sourceReadyEpoch=token;render();start();}});},refresh:render};
}
