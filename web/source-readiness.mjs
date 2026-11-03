import {hasOwnKey} from './own-property.mjs';
// Only status codes cross this UI boundary. Source text, capture details and
// arbitrary backend warning strings must never become readiness feedback.
const sectionNames={gallery:'Фотографии',description:'Описание',characteristics:'Характеристики'};
const sectionReasons={partial:'полнота раздела не подтверждена',not_found:'раздел не найден при проверке',not_visited_budget:'проверка не выполнена: исчерпан лимит времени'};
const schemaReasons={'unknown-schema':'Форма страницы источника изменилась: проверьте извлечённые сведения.','critical-fields-missing':'Не все обязательные сведения извлечены из источника.','invalid-critical-value':'В источнике есть неоднозначное или недопустимое значение.','field-conflict':'В источнике есть противоречивые сведения.','no-photo-evidence':'Фотографии источника не подтверждены.'};
function schemaSummary(value){
 if(!value||value.version!==1||!['recognized','partial','unsupported'].includes(value.state))return null;
 const names=['price','area','layout','description'],reasons=Array.isArray(value.reasons)?value.reasons.filter(key=>hasOwnKey(schemaReasons,key)):[];
 return {version:1,state:value.state,platform:['cian','avito'].includes(value.platform)?value.platform:'unknown',reasons,missingCritical:Array.isArray(value.missingCritical)?value.missingCritical.filter(key=>names.includes(key)):[],criticalCoverage:Object.fromEntries(names.map(key=>[key,value.criticalCoverage?.[key]===true])),unknownDoesNotMeanAbsent:true};
}

function readinessOf(value){
  if(!value||typeof value!=='object')return null;
  const candidates=[value.sourceReadiness,value.listing&&value.listing.sourceReadiness,value.fields&&value.fields.sourceReadiness,value];
  const ready=candidates.find(row=>row&&typeof row==='object'&&['ready','partial','unknown'].includes(row.state));
  if(ready)return ready;
  const schema=schemaSummary(value.sourceSchemaHealth||value.listing?.sourceSchemaHealth||value.fields?.sourceSchemaHealth);
  return schema?{state:schema.state==='recognized'?'unknown':'partial',partial:schema.state!=='recognized',completed:false,sections:[],schema}:null;
}

function albumStatus(value){
 if(!value||typeof value!=='object'||!Number.isInteger(value.observed)||value.observed<0||value.observed>1800)return null;
 const expected=value.expected===null?null:Number.isInteger(value.expected)&&value.expected>0&&value.expected<=400?value.expected:null,observed=value.observed,limit=Number.isInteger(value.limit)&&value.limit>0&&value.limit<=200?value.limit:30;
 const hasRoles=value.version===2||hasOwnKey(value,'previewOnly')||hasOwnKey(value,'mainLinksObserved');
 const rolesValid=hasRoles&&Number.isInteger(value.previewOnly)&&value.previewOnly>=0&&Number.isInteger(value.mainLinksObserved)&&value.mainLinksObserved>=0&&value.previewOnly+value.mainLinksObserved<=observed;
 const missing=expected===null?null:Math.max(0,expected-observed),countConflict=value.countConflict===true||expected!==null&&observed>expected;
 const state=missing||rolesValid&&value.previewOnly?'partial':expected===null||countConflict||hasRoles&&(!rolesValid||value.mainLinksObserved+value.previewOnly<observed)?'unknown':'complete';
 const countEvidence=(Array.isArray(value.countEvidence)?value.countEvidence:[]).filter(row=>row&&Number.isInteger(row.count)&&row.count>0&&row.count<=400&&['aria-setsize','aria-label','gallery-text','data-photo-count','data-image-count','data-total','structured-photo-list'].includes(row.source)).slice(0,30).map(row=>({count:row.count,source:row.source}));
 return {version:hasRoles?2:1,state,observed,expected,missing,countConflict,countEvidence,limit,retained:Math.min(observed,limit),overLimit:Math.max(0,observed-limit),completenessVerified:state==='complete',...(rolesValid?{previewOnly:value.previewOnly,mainLinksObserved:value.mainLinksObserved}:{})};
}

export function sourceReadinessFeedback(value,fallback){
  const readiness=readinessOf(value)||readinessOf(fallback),album=albumStatus(readiness?.gallery),partial=!!(value&&value.sourcePartial===true)||!!(readiness&&(readiness.partial===true||readiness.state==='partial'))||!!(album&&(album.missing||album.overLimit||album.previewOnly));
  const state=partial?'partial':album?.state==='unknown'?'unknown':readiness?readiness.state:'',details=[];
  if(partial){
    const rows=Array.isArray(readiness&&readiness.sections)?readiness.sections:[];
    for(const name of Object.keys(sectionNames)){
      const row=rows.find(section=>section&&(section.name||section.section)===name);
      if(!row||row.reason==='ready'||row.ready===true)continue;
      details.push(sectionNames[name]+' — '+(sectionReasons[row.reason]||'полнота раздела не подтверждена')+'.');
    }
    const schema=schemaSummary(readiness?.schema);
    if(schema)for(const reason of schema.reasons)if(reason!=='no-photo-evidence')details.push(schemaReasons[reason]);
  }
  if(album){
    if(album.missing||album.overLimit)details.push('Альбом — найдено '+album.observed+' из '+(album.expected??'?')+' фото; не найдено '+(album.missing||0)+', сверх лимита '+album.overLimit+'.');
    if(album.previewOnly)details.push('У '+album.previewOnly+' фото пока найдены только миниатюры; ссылки на основные версии ещё не получены.');
    else if(album.state==='unknown')details.push('Альбом — найдено '+album.observed+' фото; полнота не подтверждена'+(album.countConflict?': счётчики противоречивы.':album.expected===null?': общий счётчик отсутствует.':': основные версии некоторых фото ещё не подтверждены.'));
  }
  const message=partial?['Сбор исходной страницы частичный.',...details].join(' '):state==='unknown'?['Полнота исходной страницы не проверялась.',...details].join(' '):details.join(' ');
  return {state,partial,galleryState:album?.state||'',details,message};
}

export function importCompletionFeedback(job,fallback){
  const source=sourceReadinessFeedback(job,fallback),photoPartial=typeof job.photoPartial==='boolean'?job.photoPartial:job.partial===true&&!source.partial;
  const title=source.partial?'Карточка готова · исходная страница собрана частично':source.state==='unknown'?'Данные прочитаны · полнота '+(source.galleryState==='unknown'?'фотографий':'источника')+' не подтверждена':photoPartial?'Карточка готова · фото подготовлены частично':'Готово на Mac';
  const message=[source.partial?'Карточка создана.':photoPartial?'Карточка готова.':'',source.message,photoPartial?'Фотографии подготовлены частично.':''].filter(Boolean).join(' ');
  return {title,message,sourceMessage:source.message,sourcePartial:source.partial,photoPartial,retryPhotos:photoPartial&&!!job.result_id};
}

export function importStageMark(step){
  return step.state==='partial'||step.quality==='partial'||step.state==='error'||step.state==='waitingManual'?'!':step.state==='done'?'✓':'…';
}

const metadataKeys=['sourceReadiness','sourceSchemaHealth','photoItems','layouts','sourceDates','transitDetails','pricePeriod','priceCurrency','depositCurrency','commissionCurrency','sellerSource','commissionSource','commissionStatus','commissionAmount','oneTimeFees','publicationStatus','eligibleForNew','locationSource'];
function metadataUrl(value){try{const url=new URL(value);return ['http:','https:'].includes(url.protocol)&&!url.username&&!url.password?url.href:null;}catch{return null;}}
function metadataString(value,max=500){return typeof value==='string'?value.slice(0,max):null;}
function metadataMedia(value,layout){
  if(!Array.isArray(value))return undefined;
  return value.slice(0,400).filter(row=>row&&typeof row==='object').map(row=>{
    const url=metadataUrl(row.url);if(!url)return null;
    const item={url,isLayout:layout||row.isLayout===true,isCianLayout:layout||row.isCianLayout===true};
    if(typeof row.photoId==='string'||typeof row.photoId==='number')item.photoId=String(row.photoId).slice(0,100);
    if(Number.isInteger(row.order)&&row.order>=0)item.order=row.order;
    if(typeof row.source==='string')item.source=row.source.slice(0,80);
    if(['platform-photo-id','observed-dom-node','observed-picture','observed-cian-photo-url'].includes(row.identitySource))item.identitySource=row.identitySource;
    if(row.variants&&typeof row.variants==='object'){item.variants={};for(const key of ['fullUrl','thumbnailUrl','thumbnail2Url','miniUrl']){const variant=metadataUrl(row.variants[key]);if(variant)item.variants[key]=variant;}}
    if(Array.isArray(row.observedVariants))item.observedVariants=row.observedVariants.slice(0,60).filter(value=>value&&['data-original','data-src','data-srcset','srcset','currentSrc','src','href'].includes(value.source)&&metadataUrl(value.url)).map(value=>({url:metadataUrl(value.url),source:value.source}));
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
    }else if(key==='sourceSchemaHealth'){
      const safe=schemaSummary(value);if(safe)output[key]=safe;
    }else if(key==='sourceReadiness'){
      if(!value||!['ready','partial','unknown'].includes(value.state))continue;
      const feedback=sourceReadinessFeedback({sourceReadiness:value});
      output[key]={state:feedback.state,partial:feedback.partial,completed:value.completed===true&&feedback.state==='ready',sections:[]};
      for(const name of Object.keys(sectionNames)){
        const row=Array.isArray(value.sections)&&value.sections.find(item=>item&&(item.name||item.section)===name);if(!row)continue;
        const reason=['ready','partial','not_found','not_visited_budget'].includes(row.reason)?row.reason:row.ready===true?'ready':'partial';
        output[key].sections.push({name,reason,found:typeof row.found==='boolean'?row.found:null,visited:row.visited===true,ready:row.ready===true&&reason==='ready'});
      }
      output[key].warnings=feedback.details;
      const schema=schemaSummary(value.schema);if(schema)output[key].schema=schema;
      const album=albumStatus(value.gallery);if(album)output[key].gallery=album;
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
