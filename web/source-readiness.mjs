// Only status codes cross this UI boundary. Source text, capture details and
// arbitrary backend warning strings must never become readiness feedback.
const sectionNames={gallery:'Фотографии',description:'Описание',characteristics:'Характеристики'};
const sectionReasons={partial:'полнота раздела не подтверждена',not_found:'раздел не найден при проверке',not_visited_budget:'проверка не выполнена: исчерпан лимит времени'};

function readinessOf(value){
  if(!value||typeof value!=='object')return null;
  const candidates=[value.sourceReadiness,value.listing&&value.listing.sourceReadiness,value.fields&&value.fields.sourceReadiness,value];
  return candidates.find(row=>row&&typeof row==='object'&&['ready','partial','unknown'].includes(row.state))||null;
}

export function sourceReadinessFeedback(value,fallback){
  const readiness=readinessOf(value)||readinessOf(fallback),partial=!!(value&&value.sourcePartial===true)||!!(readiness&&(readiness.partial===true||readiness.state==='partial'));
  const state=partial?'partial':readiness?readiness.state:'',details=[];
  if(partial){
    const rows=Array.isArray(readiness&&readiness.sections)?readiness.sections:[];
    for(const name of Object.keys(sectionNames)){
      const row=rows.find(section=>section&&(section.name||section.section)===name);
      if(!row||row.reason==='ready'||row.ready===true)continue;
      details.push(sectionNames[name]+' — '+(sectionReasons[row.reason]||'полнота раздела не подтверждена')+'.');
    }
  }
  const message=partial?['Сбор исходной страницы частичный.',...details].join(' '):state==='unknown'?'Полнота исходной страницы не проверялась.':'';
  return {state,partial,details,message};
}

export function importCompletionFeedback(job,fallback){
  const source=sourceReadinessFeedback(job,fallback),photoPartial=typeof job.photoPartial==='boolean'?job.photoPartial:job.partial===true&&!source.partial;
  const title=source.partial?'Карточка готова · исходная страница собрана частично':photoPartial?'Карточка готова · фото подготовлены частично':'Готово на Mac';
  const message=[source.partial?'Карточка создана.':photoPartial?'Карточка готова.':'',source.message,photoPartial?'Фотографии подготовлены частично.':''].filter(Boolean).join(' ');
  return {title,message,sourceMessage:source.message,sourcePartial:source.partial,photoPartial,retryPhotos:photoPartial&&!!job.result_id};
}

export function importStageMark(step){
  return step.state==='partial'||step.quality==='partial'||step.state==='error'||step.state==='waitingManual'?'!':step.state==='done'?'✓':'…';
}

const metadataKeys=['sourceReadiness','photoItems','layouts','sourceDates','transitDetails','pricePeriod','priceCurrency','depositCurrency','commissionCurrency','sellerSource','commissionSource','commissionStatus','commissionAmount','oneTimeFees','publicationStatus','eligibleForNew','locationSource'];
function metadataUrl(value){try{const url=new URL(value);return ['http:','https:'].includes(url.protocol)&&!url.username&&!url.password?url.href:null;}catch{return null;}}
function metadataString(value,max=500){return typeof value==='string'?value.slice(0,max):null;}
function metadataMedia(value,layout){
  if(!Array.isArray(value))return undefined;
  return value.slice(0,400).filter(row=>row&&typeof row==='object').map(row=>{
    const url=metadataUrl(row.url);if(!url)return null;
    const item={url,isLayout:layout||row.isLayout===true,isCianLayout:layout||row.isCianLayout===true};
    if(typeof row.photoId==='string'||typeof row.photoId==='number')item.photoId=String(row.photoId).slice(0,40);
    if(Number.isInteger(row.order)&&row.order>=0)item.order=row.order;
    if(typeof row.source==='string')item.source=row.source.slice(0,80);
    if(row.variants&&typeof row.variants==='object'){item.variants={};for(const key of ['fullUrl','thumbnailUrl','thumbnail2Url','miniUrl']){const variant=metadataUrl(row.variants[key]);if(variant)item.variants[key]=variant;}}
    return item;
  }).filter(Boolean);
}

// Editor snapshots retain only the source's bounded structured metadata. This
// deliberately excludes offer JSON, capture logs, HTML and browser/session data.
export function sourceEditorMetadata(fields,listing){
  const output={},primary=fields&&typeof fields==='object'?fields:{},fallback=listing&&typeof listing==='object'?listing:{};
  for(const key of metadataKeys){
    const value=primary[key]??fallback[key];
    if(key==='pricePeriod'){
      if(['month','day','week','year','unknown'].includes(value))output[key]=value;
    }else if(['priceCurrency','depositCurrency','commissionCurrency'].includes(key)){
      if(['RUB','USD','EUR','unknown'].includes(value))output[key]=value;
    }else if(key==='publicationStatus'){
      if(['active','archived','removed','unknown'].includes(value))output[key]=value;
    }else if(key==='commissionStatus'){
      if(['present','none','unknown'].includes(value))output[key]=value;
    }else if(key==='eligibleForNew'){
      if(typeof value==='boolean')output[key]=value;
    }else if(['sellerSource','commissionSource','locationSource'].includes(key)){
      const safe=metadataString(value,500);if(safe!==null)output[key]=safe;
    }else if(key==='commissionAmount'){
      if(['string','number'].includes(typeof value)&&/^\d+(?:\.\d{1,2})?$/.test(String(value)))output[key]=String(value).slice(0,20);
    }else if(key==='oneTimeFees'&&Array.isArray(value)){
      output[key]=value.slice(0,30).filter(row=>row&&typeof row==='object').map(row=>({label:metadataString(row.label,150)||'Разовый платёж',amount:metadataString(row.amount,30)??(typeof row.amount==='number'&&Number.isFinite(row.amount)?String(row.amount):''),currency:['RUB','USD','EUR','unknown'].includes(row.currency)?row.currency:'unknown',source:metadataString(row.source,500)||''}));
    }else if(key==='sourceReadiness'){
      if(!value||!['ready','partial','unknown'].includes(value.state))continue;
      const feedback=sourceReadinessFeedback({sourceReadiness:value});
      output[key]={state:feedback.state,partial:feedback.partial,completed:value.completed===true&&!feedback.partial,sections:[]};
      for(const name of Object.keys(sectionNames)){
        const row=Array.isArray(value.sections)&&value.sections.find(item=>item&&(item.name||item.section)===name);if(!row)continue;
        const reason=['ready','partial','not_found','not_visited_budget'].includes(row.reason)?row.reason:row.ready===true?'ready':'partial';
        output[key].sections.push({name,reason,found:typeof row.found==='boolean'?row.found:null,visited:row.visited===true,ready:row.ready===true&&reason==='ready'});
      }
      output[key].warnings=feedback.details;
      for(const measurement of ['seconds','activeSeconds','budgetSeconds','budgetOverrunSeconds'])if(typeof value[measurement]==='number'&&Number.isFinite(value[measurement])&&value[measurement]>=0&&value[measurement]<=7200)output[key][measurement]=value[measurement];
    }else if(key==='photoItems'||key==='layouts'){
      const rows=metadataMedia(value,key==='layouts');if(rows)output[key]=rows;
    }else if(key==='sourceDates'&&value&&typeof value==='object'){
      output[key]={};for(const date of ['created','published','updated','timezone']){const item=value[date];if(typeof item==='string')output[key][date]=item.slice(0,80);else if(typeof item==='number'&&Number.isFinite(item)||item===null)output[key][date]=item;}
    }else if(key==='transitDetails'&&Array.isArray(value)){
      output[key]=value.slice(0,80).filter(row=>row&&typeof row==='object').map(row=>{
        const item={};for(const text of ['name','line','color','source']){const safe=metadataString(row[text],text==='name'?200:80);if(safe!==null)item[text]=safe;}
        if(typeof row.id==='string')item.id=row.id.slice(0,80);else if(typeof row.id==='number'&&Number.isFinite(row.id)||row.id===null)item.id=row.id;
        if(['walk','bus','metro','public','car','unknown'].includes(row.mode))item.mode=row.mode;
        if(typeof row.time==='number'&&Number.isFinite(row.time)&&row.time>=0&&row.time<=1440)item.time=row.time;else if(typeof row.time==='string'&&/^\d{1,4}(?:\.\d{1,2})?(?:[–-]\d{1,4}(?:\.\d{1,2})?)?$/.test(row.time))item.time=row.time;else if(row.time===null)item.time=null;
        const url=metadataUrl(row.url);if(url)item.url=url;
        return item;
      });
    }
  }
  return output;
}
