// The primary editor requests free model prose; explicit catalogue choices keep
// their separate deterministic path. Neither a fallback nor a failed response
// may replace the user's current publication here.
export function individualCopyRequest(fields,output,groupId,part='all'){
 if(!['title','body','all'].includes(part))throw Error('Выберите заголовок, описание или всё объявление');
 if(!fields||typeof fields!=='object'||Array.isArray(fields))throw Error('Откройте карточку объявления');
 return {action:'generate-preview',styleMode:'individual',part,fields,output:{title:String(output?.title||''),body:String(output?.body||'')},groupId:String(groupId||'')||undefined};
}
export function individualCopyCandidate(value,part='all'){
 if(value?.status!=='review'||value.modelAccepted!==true||value.fallback===true||value.generationMode!=='individual-model-prose'){
  throw Error((Array.isArray(value?.warnings)?value.warnings.filter(text=>typeof text==='string').join(' · '):'')||'Локальная модель не создала пригодный текст. Текущий текст сохранён.');
 }
 if(!['title','body','all'].includes(part)||typeof value.title!=='string'||!value.title.trim()||typeof value.body!=='string'||!value.body.trim())throw Error('ИИ вернул неполный текст. Текущий текст сохранён.');
 if(value.title.length>240||value.body.length>6000)throw Error('Ответ ИИ превышает допустимый размер');
 if(part==='title')return {title:value.title};
 if(part==='body')return {body:value.body};
 return {title:value.title,body:value.body,tags:typeof value.tags==='string'?value.tags:''};
}
export function individualCopyMetadata(value){
 const keys=['status','model','backend','styleId','variation','factsHash','elapsedSeconds','fallback','warnings','ai','modelAccepted','textSource','generationMode','modelContribution','factualTextSource','contract','inventoryCount','photoInventoryCount','sourceContextCharacters','sourceContextTruncated','part'];
 const result=Object.fromEntries(keys.filter(key=>Object.prototype.hasOwnProperty.call(value||{},key)).map(key=>[key,value[key]]));
 if(value?.parts&&typeof value.parts==='object'&&!Array.isArray(value.parts)){
  const parts={};for(const part of ['title','body','tags']){const row=value.parts[part];if(!row||typeof row!=='object'||Array.isArray(row))continue;parts[part]=Object.fromEntries([...keys,'textDigest'].filter(key=>Object.prototype.hasOwnProperty.call(row,key)).map(key=>[key,row[key]]));}
  if(Object.keys(parts).length){result.parts=parts;result.provenanceVersion=1;}
 }
 if(Array.isArray(value?.appliedParts))result.appliedParts=['title','body','tags'].filter(part=>value.appliedParts.includes(part));
 return result;
}

// Shared with publication_provenance.py. This bounded fingerprint detects
// edits for attribution; the server uses full-payload CAS for safe writes.
export function publicationTextDigest(value){
 const chars=Array.from(typeof value==='string'?value:'');let first=2166136261,second=2246822519;
 for(const char of chars){const code=char.codePointAt(0);first=Math.imul(first^code,16777619);second=Math.imul(second^code,3266489917);}
 return 'p1:'+chars.length+':'+(first>>>0).toString(16).padStart(8,'0')+':'+(second>>>0).toString(16).padStart(8,'0');
}

export function mergeIndividualCopyMetadata(previous,value,candidate){
 const metadata=individualCopyMetadata(value),parts={...(individualCopyMetadata(previous).parts||{})};
 const appliedParts=['title','body','tags'].filter(part=>typeof candidate?.[part]==='string');
 for(const part of appliedParts){
  const row={...individualCopyMetadata(value),part,textDigest:publicationTextDigest(candidate[part])};delete row.parts;delete row.appliedParts;delete row.provenanceVersion;
  if(part==='tags')Object.assign(row,{generationMode:'factual-tags',textSource:'rules',modelAccepted:false,modelContribution:'none'});
  else if(row.generationMode==='individual-model-prose')row.modelContribution=part+'-prose';
  parts[part]=row;
 }
 Object.assign(metadata,{parts,appliedParts,provenanceVersion:1});
 if(metadata.generationMode==='individual-model-prose')metadata.modelContribution=appliedParts.includes('title')&&appliedParts.includes('body')?'title-and-body-prose':appliedParts.some(part=>part!=='tags')?appliedParts.find(part=>part!=='tags')+'-prose':'none';
 return metadata;
}

export function individualPartOrigin(value,part,text){
 if(!['title','body','tags'].includes(part))throw Error('Неизвестная часть объявления');
 const metadata=individualCopyMetadata(value),row=metadata.parts?.[part];
 if(row){
  if(row.textDigest!==publicationTextDigest(text)||row.generationMode==='manual')return 'manual';
  if(row.generationMode==='assistant-editorial'&&row.textSource==='assistant-reviewed')return 'assistant-reviewed';
  if(row.generationMode==='individual-model-prose'&&row.modelAccepted===true&&row.fallback!==true)return 'individual';
  if(row.textSource==='rules'||row.generationMode==='explicit-template')return 'rules';
 }
 const scope=metadata.part||'all';
 if([part,'all'].includes(scope)&&metadata.generationMode==='individual-model-prose'&&metadata.modelAccepted===true)return part==='tags'?'rules':'legacy-model';
 if([part,'all'].includes(scope)&&(metadata.textSource==='rules'||metadata.generationMode==='explicit-template'))return 'rules';
 return 'unknown';
}

export function individualCopySummary(value,output){
 const labels={individual:'ИИ',manual:'ручная правка',rules:'по правилам','assistant-reviewed':'проверено помощником','legacy-model':'ИИ, прежняя запись',unknown:'прежний текст'};
 return [['title','Заголовок'],['body','Описание'],['tags','Теги']].filter(([part])=>String(output?.[part]||'').trim()).map(([part,label])=>label+': '+labels[individualPartOrigin(value,part,output[part])]).join(' · ');
}

export function manualIndividualCopyMetadata(previous,part,text){
 if(!['title','body','tags'].includes(part))throw Error('Неизвестная часть объявления');
 const value={status:'saved',part,generationMode:'manual',textSource:'operator',modelAccepted:false,modelContribution:'none'};
 const result=mergeIndividualCopyMetadata(previous,value,{[part]:text});Object.assign(result.parts[part],value);return result;
}

export function startIndividualTextJob(api,payload,{onProgress=()=>{},pollInterval=400,maximumWait=90000}={}){
 let id=null,cancelRequested=false,ended=false,cancelPromise=null;
 const requestId=globalThis.crypto?.randomUUID?.()||'text-'+Date.now().toString(36)+'-'+Math.random().toString(36).slice(2);
 const cancel=()=>{
  cancelRequested=true;
  if(id&&!ended&&!cancelPromise)cancelPromise=api('text-jobs/'+id+'/cancel',{});
  return cancelPromise||Promise.resolve(null);
 };
 const completion=(async()=>{
  const started=Date.now();
  try{
   const value=await api('text-jobs',{...payload,requestId});
   if(typeof value?.job_id!=='string'||!/^[a-f0-9]{32}$/.test(value.job_id))throw Error('Сервер не вернул ID текстового задания');
   id=value.job_id;if(cancelRequested)await cancel();
   while(true){
    const job=await api('text-jobs/'+id);onProgress(job);
    if(job.state==='done'){
     if(!job.result||!['draft','card'].includes(job.resultType))throw Error('Текстовое задание завершилось без результата');
     return {result:job.result,resultType:job.resultType,cancellationRequested:cancelRequested};
    }
    if(job.state==='cancelled')throw Error(job.message||'Генерация отменена.');
    if(job.state==='error')throw Error(job.message||'Не удалось подготовить текст.');
    if(Date.now()-started>maximumWait){await cancel();throw Error('Не удалось дождаться результата. Запрошена отмена; проверьте состояние карточки.');}
    await new Promise(resolve=>setTimeout(resolve,pollInterval));
   }
  }finally{ended=true;}
 })();
 return {completion,cancel,get id(){return id;},get cancellationRequested(){return cancelRequested;}};
}
