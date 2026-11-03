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
 return Object.fromEntries(keys.filter(key=>Object.prototype.hasOwnProperty.call(value||{},key)).map(key=>[key,value[key]]));
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
