import {setupPresentationStudio} from './presentation-studio.mjs';
import {setupCardArchive} from './card-archive.mjs';
import {setupCardFiles,cardDownloadBlob} from './card-files.mjs';
import {setupPhoneConnection} from './phone-connection.mjs';
import {DICTIONARY,FEATURES,DEFAULT_PREFS,generateListing,extractFacts,analyzeListingSource,rubles,translateDraft,generationCapabilities} from './engine.mjs';
import {TAG_CATALOG,listingEligible} from './content-tools.mjs';
import {setupWordEditor} from './word-editor.mjs';
import {setupInsights} from './insights-ui.mjs';
import {setupHistory} from './history-ui.mjs';
import {photoSources} from './media-core.mjs';
import {setupPhotos} from './photo-ui.mjs';
import {getGeoStats,searchGeo,GEO_CATALOG} from './geo-catalog.mjs';
import {setupRelated} from './related-ui.mjs';
import {editorStartup} from './editor-state.mjs';
import {setupLanguageStudio} from './language-studio.mjs';
import {getLexiconStats,setExtendedLexicon} from './ru-zh-translator.mjs';
import {setupUniversityUI} from './university-ui.mjs';
import {setupUniversityRoutes} from './university-routes.mjs';
import {setupListingWatch} from './listing-watch.mjs';
import {setupImportDiagnostics} from './import-diagnostics.mjs';

const $=id=>document.getElementById(id);
const read=(key,fallback)=>{try{return JSON.parse(localStorage.getItem(key))??fallback;}catch{return fallback;}};
let toastTimer,relaySyncTimer,profileSyncTimer,phoneConnection=null,editorCardEpoch=0,archiveUI=null,presentationUI=null,cardFilesUI=null,languageUI=null,universityUI=null;
function toast(message){$('toast').textContent=message;$('toast').classList.remove('hidden');clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').classList.add('hidden'),4500);}
function write(key,value){try{localStorage.setItem(key,JSON.stringify(value));return true;}catch{toast('Не удалось сохранить: хранилище браузера заполнено или недоступно. Экспортируйте копию.');return false;}}
const fields=['url','source','name','district','complex','districtZh','metro','walk','area','rooms','floor','floors','price','deposit','university','uniTime','uniMode','route','details','pickup','arrival','support','luggage','style','layout','kitchen','living','bedroomArea','occupants','transit','universities','highlight','description','utilities','rentFreeMonths','minMonths','separate','children','pets','shortTerm','registration','registrationMonths','registrationPeople','campus','region','audience','ready','newRenovation','seller','commission','phraseNotes','city','address','renovation','windows','bathroom','combinedWcs','separateWcs','buildingYear','buildingType','buildingSeries','floorType','entrances','heating','emergency','ceiling','balconies','loggias','prepayMonths','contractMonths','leaseTerm','registrationPolicy','childrenPolicy','petPolicy','smokingPolicy','guestPolicy','subletPolicy','shortTermPolicy','sourceDescription','railways','lift','latitude','longitude'];
const defaults={style:'compact',layout:'classic',utilities:'unknown',registration:'assist',support:true,uniMode:'unknown',seller:'unknown',audience:'general'};
const storedRules=read('most-rules',null);
let rules={...DICTIONARY,...(storedRules&&typeof storedRules==='object'&&!Array.isArray(storedRules)?storedRules:{})};
if(read('most-rules-version',0)<5){
  const staleDefaults={'Евроремонт':['欧式装修'],'евроремонт':['欧式装修'],
    'совмещенный':['合用卫浴'],'совмещённый':['合用卫浴'],
    'совмещенный санузел':['合用卫浴'],'совмещённый санузел':['合用卫浴'],
    'раздельный':['干湿分离'],'раздельный санузел':['干湿分离']};
  for(const [key,oldValues] of Object.entries(staleDefaults))if(oldValues.includes(rules[key])&&DICTIONARY[key])rules[key]=DICTIONARY[key];
  if(write('most-rules',personalRules()))write('most-rules-version',5);
}
function personalRules(){return Object.fromEntries(Object.entries(rules).filter(([key,value])=>!Object.hasOwn(DICTIONARY,key)||value!==DICTIONARY[key]));}
function saveRules(){if(!write('most-rules',personalRules()))return false;write('most-rules-version',5);queueProfileSync();wordEditor?.render();languageUI?.refreshLocalStats?.();return true;}
let objects=read('most-objects',[]);if(!Array.isArray(objects))objects=[];
let prefs={...DEFAULT_PREFS,...read('most-prefs',{})};
let current=null,titleIndex=0,currentTitles=[],sourceAudit=null,featureCounts={},historyUI=null;
let copyVariant=0,tagVariant=0,localResultId=null,localJob=null,isDemo=false,editorRevision=0;
let titleVariant=0,wordEditor=null,photoUrls=[],insightsUI=null,photoUI=null,relatedUI=null,relatedOffers=[],photoEdits={},photoEvidence=[],photoFeatureBaseline={};
const hosting=document.querySelector('meta[name="redcn-hosting"]')?.content;
const localHosting=hosting==='local',staticHosting=hosting==='github-pages'||localHosting;
let universityRoutesUI=null,universityRouteDraft=null,listingWatchUI=null,importAuditUI=null;
let wordCustom=read('most-word-custom',[]);if(!Array.isArray(wordCustom))wordCustom=[];

// Generate checkboxes using safe DOM methods; nothing from a listing becomes HTML.
const groupNames={bedroom:'СПАЛЬНЯ',living:'ГОСТИНАЯ',kitchen:'КУХНЯ',bathroom:'САНУЗЕЛ',storage:'ХРАНЕНИЕ'};
for(const group of Object.keys(groupNames)){
  const heading=document.createElement('div');heading.className='feature-group';heading.textContent=groupNames[group];$('featureGrid').append(heading);
  for(const [id,,label,key] of FEATURES.filter(x=>x[3]===group)){
    const wrapper=document.createElement('label'),input=document.createElement('input');input.type='checkbox';input.id='feature-'+id;
    const quantity=document.createElement('input');quantity.type='number';quantity.min='1';quantity.max='50';quantity.className='feature-qty';quantity.id='feature-qty-'+id;quantity.placeholder='шт.';quantity.setAttribute('aria-label','Количество: '+label);quantity.oninput=()=>{editorRevision++;if(photoFeatureBaseline[id])photoFeatureBaseline[id].count=quantity.value;if(quantity.value)featureCounts[id]=quantity.value;else delete featureCounts[id];saveDraft();};wrapper.append(input,document.createTextNode(label),quantity);$('featureGrid').append(wrapper);
  }
}
function snapshot(){const routeRows=universityRoutesUI?.value()??universityRouteDraft;const value={...(Array.isArray(routeRows)?{universityRoutes:routeRows}:{}),resultId:localResultId,isDemo,templateMode:'catalog',titleVariant,copyVariant,tagVariant,photos:[...photoUrls],photoEdits:photoUI?.state()||photoEdits,relatedOffers:[...relatedOffers],sourceAudit,photoEvidence:[...photoEvidence],photoFeatureBaseline:{...photoFeatureBaseline},featureCounts:{...featureCounts}};for(const key of fields)value[key]=$(key).type==='checkbox'?$(key).checked:$(key).value;if(value.source?.length>50000)value.source=value.source.slice(0,50000);value.features=FEATURES.filter(x=>$('feature-'+x[0]).checked).map(x=>x[0]);return value;}
function populate(value){languageUI?.clearDraft();universityUI?.clearSuggestions();clearTimeout(relaySyncTimer);editorCardEpoch++;editorRevision++;clearTimeout(toastTimer);$('toast').classList.add('hidden');localResultId=localHosting&&/^[a-f0-9]{32}$/.test(value?.resultId||'')?value.resultId:null;archiveUI?.clear();cardFilesUI?.clear();$('resultLink').removeAttribute('href');$('resultLink').textContent='Сохраните карточку, чтобы получить ссылку';$('sourceLink').classList.add('hidden');$('localResult').classList.toggle('hidden',!localHosting);wordEditor?.reset();const v={...defaults,...value};universityRouteDraft=Array.isArray(v.universityRoutes)?v.universityRoutes:null;isDemo=v.isDemo===true;renderDemoNotice();sourceAudit=v.sourceAudit||null;photoEvidence=Array.isArray(v.photoEvidence)?v.photoEvidence:[];photoFeatureBaseline=v.photoFeatureBaseline&&typeof v.photoFeatureBaseline==='object'?v.photoFeatureBaseline:{};featureCounts=v.featureCounts||{};titleVariant=Number(v.titleVariant)||0;copyVariant=Number(v.copyVariant)||0;tagVariant=Number(v.tagVariant??v.titleVariant)||0;photoUrls=photoSources(v.photos);photoEdits=v.photoEdits||{};relatedOffers=Array.isArray(v.relatedOffers)?v.relatedOffers.slice(0,40):[];for(const key of fields){if($(key).type==='checkbox')$(key).checked=!!v[key];else $(key).value=v[key]??'';}for(const [id] of FEATURES){$('feature-'+id).checked=Array.isArray(v.features)&&v.features.includes(id);$('feature-qty-'+id).value=featureCounts[id]||'';}renderSourceAudit();renderPhotos();universityRoutesUI?.refresh(v);relatedUI?.render();if(localHosting&&localResultId){showLocalLink(localResultId);archiveUI?.refresh();cardFilesUI?.refresh();}}
function counts(){const title=Array.from($('outputTitle').value).length,body=Array.from($('output').value+'\n'+$('tags').value).length;$('titleCount').textContent='Заголовок: '+title+' знаков';$('bodyCount').textContent='Описание и теги: '+body+' знаков';}
function saveDraft(){write('most-draft',{version:2,id:current,fields:snapshot(),title:$('outputTitle').value,output:$('output').value,tags:$('tags').value,titles:currentTitles});counts();wordEditor?.render();}
function tab(name){document.querySelectorAll('main>section').forEach(e=>e.classList.toggle('hidden',e.id!==name));document.querySelectorAll('nav button').forEach(b=>b.classList.toggle('active',b.dataset.tab===name));$('crumb').textContent=document.querySelector('[data-tab="'+name+'"]').textContent.trim();if(name==='watch')listingWatchUI?.refresh();if(name==='editor')importAuditUI?.refresh();if(name==='library'){renderList();renderMacCards();}if(name==='presentation')refreshPresentation().catch(error=>toast(error.message));if(name==='rules'){renderRules();languageUI?.openDictionary();}if(name==='roadmap')loadRoadmap();if(name==='knowledge'){ensureInsights().renderKnowledge();renderGeoCatalog();universityUI?.openKnowledge();}if(name==='analytics')ensureInsights().renderAnalytics();if(name==='history')historyUI?.render();}
document.querySelectorAll('nav button').forEach(button=>button.onclick=()=>tab(button.dataset.tab));
function renderTitles(){
  $('variants').replaceChildren();
  currentTitles.forEach((title,i)=>{const button=document.createElement('button');button.className='btn variant'+(i===titleIndex?' chosen':'');button.textContent=title;button.setAttribute('aria-pressed',String(i===titleIndex));button.onclick=()=>{titleIndex=i;$('outputTitle').value=title;renderTitles();saveDraft();};$('variants').append(button);});
}
function generate(){
  let result=generateListing(snapshot(),rules,prefs);if(result.problems.length){$('review').classList.add('error');$('review').textContent=result.problems.join(' · ');toast('Проверьте числовые поля');return;}
  let recent=read('most-copy-fingerprints',{}),key=$('url').value||$('name').value||'draft',known=recent[key]||[];const fingerprint=text=>{let h=2166136261;for(const c of text)h=Math.imul(h^c.codePointAt(0),16777619);return(h>>>0).toString(16);};for(let attempt=0;attempt<128&&known.includes(fingerprint(result.body));attempt++){advanceVariant();result=generateListing(snapshot(),rules,prefs);}recent[key]=[...known,fingerprint(result.body)].slice(-200);write('most-copy-fingerprints',Object.fromEntries(Object.entries(recent).slice(-30)));
  currentTitles=result.titles;titleIndex=0;$('outputTitle').value=currentTitles[0];$('output').value=result.body;$('tags').value=result.tags;renderTitles();renderTagReasons(result.tagSuggestions);
  $('review').classList.toggle('error',result.unknown.length>0);$('review').textContent=result.unknown.length?'Нужен перевод в словаре: '+result.unknown.join(' · ')+'. Эти фразы пока остаются в исходном языке.':(sourceAudit?.warnings?.length?sourceAudit.warnings.join(' · '):'Текст готов. Проверьте цену, маршрут, регистрацию и услуги перед публикацией.');saveDraft();historyUI?.record('generate').catch(error=>toast('История не сохранена: '+error.message));captureKnowledge('example',$('outputTitle').value,$('output').value);syncPhoneCard();
}
function advanceVariant(part='all'){
  let counters=read('most-generation-counters',{});const v=snapshot(),key=v.url||v.name||v.source.slice(0,150)||'draft';
  const saved=counters[key],prior=typeof saved==='number'?{title:saved,body:saved,tags:saved}:saved||{};
  const next={title:titleVariant,body:copyVariant,tags:tagVariant};
  for(const kind of part==='all'?['title','body','tags']:[part])next[kind]=Math.max(next[kind],Number(prior[kind])||0)+1;
  titleVariant=next.title;copyVariant=next.body;tagVariant=next.tags;counters[key]=next;
  write('most-generation-counters',Object.fromEntries(Object.entries(counters).slice(-200)));
}
$('generate').onclick=()=>{advanceVariant();generate();};
function rewritePart(part,index){
  if(!['title','body','tags'].includes(part))throw Error('Неизвестный раздел текста');
  if(index===undefined)advanceVariant(part);
  else if(part==='title')titleVariant=index;else if(part==='body')copyVariant=index;else tagVariant=index;
  const result=generateListing(snapshot(),rules,prefs);
  if(result.problems.length){$('review').classList.add('error');$('review').textContent=result.problems.join(' · ');return;}
  if(part==='title'){currentTitles=result.titles;titleIndex=0;$('outputTitle').value=currentTitles[0];renderTitles();}
  if(part==='body')$('output').value=result.body;
  if(part==='tags'){$('tags').value=result.tags;renderTagReasons(result.tagSuggestions);}
  editorRevision++;saveDraft();syncPhoneCard();historyUI?.record('generate').catch(error=>toast(error.message));
  toast(({title:'Новые заголовки',body:'Новое описание',tags:'Новые теги'})[part]+'. Остальные тексты сохранены.');
}
$('rewriteCopy').onclick=()=>rewritePart('body');
$('rotateTitles').onclick=()=>rewritePart('title');
$('rotateTags').onclick=()=>rewritePart('tags');
function renderTagReasons(suggestions=[]){$('tagReasons').replaceChildren();for(const item of suggestions){const div=document.createElement('div');div.className='tag-item';div.textContent='#'+item.tag+' — '+item.ru+' · '+item.reason;$('tagReasons').append(div);}}
function renderTagCatalog(){const q=$('tagSearch').value.trim().toLowerCase();$('tagCatalog').replaceChildren();for(const [tag,ru] of TAG_CATALOG.filter(x=>(x[0]+' '+x[1]).toLowerCase().includes(q))){const button=document.createElement('button');button.className='btn word-result';button.textContent='#'+tag+' — '+ru;button.onclick=()=>{const tags=new Set($('tags').value.split(/\s+/).filter(Boolean));tags.add('#'+tag);$('tags').value=[...tags].join(' ');saveDraft();};$('tagCatalog').append(button);}}
$('tagSearch').oninput=renderTagCatalog;
for(const key of fields)$(key).addEventListener('input',()=>{editorRevision++;saveDraft();});
for(const [id] of FEATURES)$('feature-'+id).addEventListener('change',()=>{editorRevision++;if(photoFeatureBaseline[id])photoFeatureBaseline[id].checked=$('feature-'+id).checked;saveDraft();});
for(const key of ['outputTitle','output','tags'])$(key).addEventListener('input',()=>{editorRevision++;saveDraft();syncPhoneCard();});
function extract(){
  if(!$('source').value.trim())return toast('Сначала вставьте текст объявления');
  captureKnowledge('example','Входящий текст объявления',$('source').value);
  const raw=$('source').value;const {facts,warnings}=/<(?:!doctype|html|div|script)\b/i.test(raw)?analyzeListingSource(raw,rules):extractFacts(raw,rules);
  // Preserve service policy, but clear all property facts to avoid borrowing from the last object.
  const old=snapshot(),keep={};for(const key of ['url','source','style','pickup','luggage','support','arrival','registration','registrationMonths','registrationPeople'])keep[key]=old[key];
  populate({...keep,...facts});current=null;$('output').value='';$('outputTitle').value='';$('tags').value='';currentTitles=[];renderTitles();
  $('review').textContent=warnings.join('\n');$('review').classList.remove('error');saveDraft();toast('Факты извлечены. Проверьте поля и отметки мебели.');
}
$('extract').onclick=extract;
let importing=false;
$('import').onclick=async()=>{
  if(localHosting)return importLocal();
  if(staticHosting)return toast('GitHub Pages не выполняет серверные запросы. Импортируйте ссылку через Python CLI или вставьте текст.');
  if(importing)return;let raw=$('url').value;
  try{const u=new URL(raw);if(u.protocol!=='https:'||!/(^|\.)(cian\.ru|avito\.ru)$/i.test(u.hostname)||u.username||u.password||u.port)throw Error('Введите HTTPS-ссылку ЦИАН или Авито.');
    let cache=read('most-cache',{}),saved=cache[u.href];
    if(saved&&Date.now()-saved.at<86400000){if(saved.source)applySourceEnvelope(saved.source);else{$('source').value=saved.text;extract();}$('importStatus').textContent='Загружено из кеша · '+new Date(saved.at).toLocaleString('ru-RU');return;}
    importing=true;$('import').disabled=true;$('importStatus').textContent='Загружаем страницу…';
    const response=await fetch('/api/import',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url:u.href})});
    const data=await response.json();if(!response.ok)throw Error(data.error||'Источник не разрешил загрузку.');
    $('source').value=data.text;cache[u.href]={at:Date.now(),text:data.text,...(data.source?{source:data.source}:{})};
    cache=Object.fromEntries(Object.entries(cache).sort((a,b)=>b[1].at-a[1].at).slice(0,20));write('most-cache',cache);if(data.source)applySourceEnvelope(data.source);else extract();$('importStatus').textContent='Страница загружена. Проверьте извлечённые данные.';
  }catch(error){$('importStatus').textContent=error.message+' Вставьте текст вручную.';}
  finally{importing=false;$('import').disabled=false;}
};
function historyCurrent(){return {title:$('outputTitle').value,body:$('output').value,tags:$('tags').value,fields:snapshot(),titles:[...currentTitles],objectId:current,resultId:localResultId,cardEpoch:editorCardEpoch};}
async function copy(part){
  if(!$('outputTitle').value&&!$('output').value)return toast('Сначала создайте текст');
  const revision=historyCurrent();
  const text=part==='title'?$('outputTitle').value:part==='body'?$('output').value+'\n\n'+$('tags').value:$('outputTitle').value+'\n\n'+$('output').value+'\n\n'+$('tags').value;
  try{await navigator.clipboard.writeText(text);}catch{return toast('Браузер не разрешил копирование. Выделите текст и скопируйте вручную.');}
  try{const saved=await historyUI?.record('copy-'+(part==='all'?'all':part),revision);toast('Сохранено в буфер обмена'+(saved?.saved?' · история сохранена':'')+(saved?.snapshot?' · PNG готов':'')+(saved?.warning?' · '+saved.warning:''));}catch(error){toast('Сохранено в буфер обмена. История не записана: '+error.message);}
}
$('copy').onclick=()=>copy('all');$('copyTitle').onclick=()=>copy('title');$('copyBody').onclick=()=>copy('body');
$('save').onclick=async()=>{
  const value=snapshot();if(!value.name&&!value.metro&&!value.source&&!value.complex)return toast('Добавьте данные объекта');
  const object={...value,id:current||(globalThis.crypto?.randomUUID?.()||'object-'+Date.now().toString(36)+'-'+Math.random().toString(36).slice(2)),title:$('outputTitle').value,output:$('output').value,tags:$('tags').value,titles:currentTitles,updated:new Date().toISOString()};
  const next=[object,...objects.filter(o=>o.id!==object.id)];if(write('most-objects',next)){objects=next;current=object.id;saveDraft();const revision=historyCurrent();try{const event=await historyUI?.record('save',revision);if(localHosting)await persistLocal(revision);toast('Объект сохранён'+(event?.snapshot?' · PNG готов':'')+(event?.warning?' · '+event.warning:''));}catch(error){toast('Объект сохранён в браузере. '+error.message);}}
};
$('newObject').onclick=()=>{if(localHosting)history.pushState({},'', '/');saveDraft();populate({});current=null;currentTitles=[];$('output').value='';$('outputTitle').value='';$('tags').value='';renderTitles();saveDraft();toast('Новая карточка. Объекты из библиотеки сохранены.');};
function renderList(){
  const q=$('search').value.toLowerCase();$('list').replaceChildren();
  const mode=$('libraryFilter').value;const found=objects.filter(o=>[o.name,o.metro,o.complex,o.title].join(' ').toLowerCase().includes(q)&&(mode==='all'||mode==='owner'&&o.seller==='owner'||mode==='eligible'&&listingEligible(o.seller,o.commission)));
  if(!found.length)$('list').textContent='Здесь появятся сохранённые объекты.';
  found.forEach(object=>{const row=document.createElement('div'),title=document.createElement('span'),open=document.createElement('button'),remove=document.createElement('button');row.className='saved';
    title.textContent=(object.name||object.complex||object.metro||'Без названия')+' · '+(object.price?rubles(object.price)+' ₽':'Цена не указана');
    open.className=remove.className='btn';open.textContent='Открыть';open.onclick=()=>{populate(object);current=object.id;currentTitles=Array.isArray(object.titles)?object.titles:[];$('outputTitle').value=object.title||'';$('output').value=object.output||'';$('tags').value=object.tags||'';renderTitles();saveDraft();tab('editor');};
    remove.textContent='Удалить';remove.onclick=()=>{if(confirm('Удалить объект «'+(object.name||'Без названия')+'»?')){const next=objects.filter(o=>o.id!==object.id);if(write('most-objects',next)){objects=next;if(current===object.id)current=null;renderList();saveDraft();}}};row.append(title,open,remove);$('list').append(row);
  });
}
$('search').oninput=renderList;$('libraryFilter').onchange=renderList;
let ruleOffset=0;
function renderRules(){
  const q=$('ruleSearch').value.trim().toLowerCase(),all=$('ruleScope').value==='all';
  const found=Object.entries(all?rules:personalRules()).filter(([key,value])=>(key+' '+value).toLowerCase().includes(q));
  ruleOffset=Math.min(ruleOffset,Math.max(0,Math.ceil(found.length/40)-1)*40);
  $('ruleList').replaceChildren();for(const [key,value] of found.slice(ruleOffset,ruleOffset+40)){
    const row=document.createElement('div'),a=document.createElement('input'),b=document.createElement('input'),remove=document.createElement('button');row.className='rule';a.value=key;a.readOnly=true;a.setAttribute('aria-label','Триггер '+key);b.value=value;b.setAttribute('aria-label','Перевод '+key);
    b.onchange=()=>{if(!b.value.trim()){b.value=rules[key];return toast('Перевод не может быть пустым');}rules[key]=b.value;if(saveRules())renderRules();};
    const custom=!Object.hasOwn(DICTIONARY,key)||value!==DICTIONARY[key];
    remove.textContent=custom?'×':'•';remove.disabled=!custom;remove.className='btn';remove.style.padding='5px';remove.setAttribute('aria-label','Убрать личное правило '+key);remove.onclick=()=>{if(Object.hasOwn(DICTIONARY,key))rules[key]=DICTIONARY[key];else delete rules[key];if(saveRules())renderRules();};row.append(a,b,remove);$('ruleList').append(row);
  }
  $('ruleCount').textContent=found.length?(all?'Активных правил: ':'Личных правил: ')+found.length+' · '+(ruleOffset+1)+'–'+Math.min(ruleOffset+40,found.length):'Личных замен пока нет. Встроенные переводы доступны в поиске полной базы выше.';
  $('rulePrev').disabled=ruleOffset===0;$('ruleNext').disabled=ruleOffset+40>=found.length;
}
$('ruleSearch').oninput=()=>{ruleOffset=0;renderRules();};$('ruleScope').onchange=()=>{ruleOffset=0;renderRules();};
$('rulePrev').onclick=()=>{ruleOffset=Math.max(0,ruleOffset-40);renderRules();};$('ruleNext').onclick=()=>{ruleOffset+=40;renderRules();};
function usePersonalRule(key,value){rules[key]=value;if(!saveRules())throw Error('Правило не сохранено');ruleOffset=0;$('ruleSearch').value=key;$('ruleScope').value=Object.hasOwn(personalRules(),key)?'custom':'all';renderRules();}
$('addRule').onclick=()=>{const key=$('trigger').value.trim(),value=$('translation').value.trim();if(!key||!value)return toast('Заполните триггер и перевод');try{usePersonalRule(key,value);$('trigger').value='';$('translation').value='';toast('Правило сохранено');}catch(error){toast(error.message);}};
for(const key of ['benefits','contact','brandTags'])$(key).value=prefs[key];
$('saveSettings').onclick=()=>{const next={benefits:$('benefits').value,contact:$('contact').value,brandTags:$('brandTags').value};if(write('most-prefs',next)){prefs=next;queueProfileSync();toast('Преимущества и теги сохранены. Создайте текст заново, чтобы применить.');}};
function download(name,data){const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
$('export').onclick=async()=>download('cian-cn-rednote-backup.json',{version:2,objects,rules,prefs,wordCustom,history:await historyUI?.exportEntries(),...ensureInsights().exportData(),draft:read('most-draft',null)});
$('restore').onclick=()=>$('file').click();
function validRecord(o){return o&&typeof o==='object'&&!Array.isArray(o)&&typeof o.id==='string'&&(!o.features||Array.isArray(o.features)&&o.features.every(x=>typeof x==='string'));}
$('file').onchange=async()=>{
  try{const file=$('file').files[0];if(!file)return;if(file.size>32000000)throw Error('size');const data=JSON.parse(await file.text());
    if(![1,2].includes(data.version)||!Array.isArray(data.objects)||!data.objects.every(validRecord)||!data.rules||Array.isArray(data.rules)||!Object.entries(data.rules).every(([k,v])=>k.trim()&&typeof v==='string')||typeof data.prefs?.benefits!=='string')throw Error('format');
    if(!confirm('Заменить библиотеку, словарь и настройки данными из резервной копии?'))return;
    if(!write('most-objects',data.objects)||!write('most-rules',data.rules)||!write('most-prefs',data.prefs))return;
    write('most-rules-version',2);if(Array.isArray(data.wordCustom))write('most-word-custom',data.wordCustom);if(Array.isArray(data.knowledge))write('most-knowledge',data.knowledge);if(Array.isArray(data.metrics))write('most-metrics',data.metrics);if(data.draft)write('most-draft',data.draft);if(Array.isArray(data.history))await historyUI?.importEntries(data.history);location.reload();
  }catch{toast('Не удалось импортировать файл: проверьте формат резервной копии (до 32 МБ).');}finally{$('file').value='';}
};
$('demo').textContent='Загрузить учебный пример';
$('demo').onclick=()=>{
  if(localHosting)history.replaceState({},'', '/');
  populate({isDemo:true,name:'Пример · Прокшино, 52 м²',complex:'Прокшино',area:'52',rooms:'2',floor:'11',floors:'17',price:'85000',deposit:'85000',kitchen:'11',occupants:'4',style:'detailed',layout:'classic',separate:true,metro:'Прокшино',walk:'12',
    transit:'Прокшино | 12 | walk | 1\nФилатов Луг | 19 | walk | 1\nСаларьево | 7 | bus | 1',
    universities:'МГУ | 35–50 | public\nРУДН | 30–45 | public',
    highlight:'52㎡宽敞空间＋独立厨房＋双人学习区',description:'两个独立房间，厨房与居住区域分开。窗边设有学习桌，方便在家学习或远程办公。',
    features:['sofa','desk','fridge','oven','stove','bath'],utilities:'meters',registration:'assist',support:true});
  current=null;generate();toast('Учебный пример загружен.');
};
function renderDemoNotice(){const notice=$('demoNotice');notice.classList.toggle('hidden',!isDemo);notice.textContent=isDemo?'Учебный пример: цена, мебель и маршруты заданы для знакомства с редактором. Загрузите ссылку или вставьте своё объявление, чтобы работать с квартирой.':'';}
const startup=editorStartup({local:localHosting,pathname:location.pathname,draft:read('most-draft',null)});
populate(startup.fields);
if(startup.draft){const draft=startup.draft;current=draft.id||null;$('outputTitle').value=draft.title||'';$('output').value=draft.output||'';$('tags').value=draft.tags||'';currentTitles=Array.isArray(draft.titles)?draft.titles:[];renderTitles();}
wordEditor=setupWordEditor({getRules:()=>rules,getCustom:()=>[...wordCustom,...noteEntries()],saveCustom:value=>{wordCustom=value;write('most-word-custom',wordCustom);},onChange:saveDraft,toast});
renderTagCatalog();renderTagReasons();counts();

function renderPhotos(){photoUrls=photoSources(photoUrls);photoUI?.setSources(photoUrls,photoEdits);}
$('importCli').onclick=()=>$('cliFile').click();
$('cliFile').onchange=async()=>{try{const file=$('cliFile').files[0];if(!file)return;if(file.size>8000000)throw Error('Файл больше 8 МБ');const value=JSON.parse(await file.text());if(!value.fields||typeof value.fields!=='object'||!Array.isArray(value.titles)||!value.titles.every(t=>typeof t==='string')||typeof value.body!=='string'||typeof value.tags!=='string')throw Error('Нужен JSON-результат Python CLI с fields/titles/body/tags');populate({...value.fields,photos:value.fields.photos||value.listing?.images||[]});current=null;currentTitles=value.titles;titleIndex=0;$('outputTitle').value=value.titles[0]||'';$('output').value=value.body;$('tags').value=value.tags;renderTitles();renderTagReasons(value.tagSuggestions);saveDraft();toast('Карточка из Python импортирована. Проверьте поля перед публикацией.');}catch(error){toast(error.message);}finally{$('cliFile').value='';}};
let roadmapDataClient=null;
async function loadRoadmap(){try{const response=await fetch(staticHosting?'./plan.json':'/api/roadmap',{cache:'no-store'});if(!response.ok)throw Error();const data=await response.json();if(!Array.isArray(data.tasks))throw Error();roadmapDataClient=data;renderRoadmap();}catch{$('roadmapSummary').textContent='План не загрузился. Исходник: plan.json и GitHub issue Roadmap.';}}
function renderRoadmap(){if(!roadmapDataClient)return;const tasks=roadmapDataClient.tasks;const done=tasks.filter(t=>t.status==='done').length;$('roadmapSummary').replaceChildren(document.createTextNode(done+' / '+tasks.length+' выполнено · '+(tasks.length-done)+' в планах. '));if(roadmapDataClient.github_issue_url){const a=document.createElement('a');a.textContent='Roadmap на GitHub ↗';a.href=roadmapDataClient.github_issue_url;a.target='_blank';a.rel='noopener noreferrer';$('roadmapSummary').append(a);}const q=$('roadmapSearch').value.toLowerCase(),mode=$('roadmapFilter').value;$('roadmapList').replaceChildren();for(const task of tasks.filter(t=>(mode==='all'||t.status===mode)&&(t.id+' '+t.section+' '+t.title+' '+t.acceptance).toLowerCase().includes(q))){const card=document.createElement('article'),head=document.createElement('header'),p=document.createElement('p'),evidence=document.createElement('div');card.className='task '+task.status;head.textContent=(task.status==='done'?'✓ ':'○ ')+task.id+' · '+task.title;p.textContent=task.acceptance;evidence.className='evidence';evidence.textContent=task.priority+' · '+task.section+(task.evidence.length?' · '+task.evidence.join(' · '):' · Проверка ещё не завершена');card.append(head,p,evidence);$('roadmapList').append(card);}}
$('roadmapSearch').oninput=renderRoadmap;$('roadmapFilter').onchange=renderRoadmap;
function ensureInsights(){if(!insightsUI)insightsUI=setupInsights({read,write,toast,onRule:(ru,cn)=>{usePersonalRule(ru,cn);}});return insightsUI;}
function captureKnowledge(type,title,text){const enabled=insightsUI?$('rememberContent').checked:read('most-remember',true);if(enabled&&text.trim())ensureInsights().capture({type,title,text,source:$('url').value,status:'unreviewed'});}
if(staticHosting&&!localHosting){$('importStatus').textContent='GitHub Pages: вставьте текст или импортируйте JSON из Python CLI.';$('import').textContent='Импорт через CLI';}

// Local desktop backend is available only in the loopback-served build.
function noteEntries(){return $('phraseNotes').value.split('\n').map(line=>line.split('|').map(s=>s.trim())).filter(parts=>parts.length>=2&&parts[0]&&parts[1]).map(([zh,ru,...alternatives])=>({zh,ru,alternatives:alternatives.filter(Boolean)}));}
$('phraseNotes').addEventListener('input',()=>wordEditor?.render());
async function localApi(path,data){const response=await fetch('/api/local/'+path,{method:data?'POST':'GET',headers:{'X-Redcn-Token':document.querySelector('meta[name="redcn-token"]')?.content||'',...(data?{'Content-Type':'application/json'}:{})},...(data?{body:JSON.stringify(data)}:{})});const value=await response.json();if(!response.ok)throw Error(value.error||'Ошибка локального сервера');return value;}
function applyGenerated(value){if(!value.fields||!Array.isArray(value.titles)||typeof value.body!=='string'||typeof value.tags!=='string')throw Error('Неверный результат');populate({...value.fields,photos:value.fields.photos||value.listing?.images||[]});current=null;currentTitles=value.titles;titleIndex=0;$('outputTitle').value=value.titles[0]||'';$('output').value=value.body;$('tags').value=value.tags;renderTitles();renderTagReasons(value.tagSuggestions);saveDraft();captureKnowledge('example',$('outputTitle').value,$('output').value);}
function showLocalLink(id){const changed=localResultId!==id;localResultId=id;if(changed){archiveUI?.refresh();cardFilesUI?.refresh();}if(current){const item=objects.find(o=>o.id===current);if(item){item.resultId=id;write('most-objects',objects);}}$('localResult').classList.remove('hidden');$('resultLink').href='/listing/'+id;$('resultLink').textContent=location.origin+'/listing/'+id;try{const u=new URL($('url').value);if(u.protocol==='https:'&&/(^|\.)(cian\.ru|avito\.ru)$/i.test(u.hostname)){$('sourceLink').href=u.href;$('sourceLink').classList.remove('hidden');}else $('sourceLink').classList.add('hidden');}catch{$('sourceLink').classList.add('hidden');}}
async function importLocal(){if(importing)return;importing=true;$('import').disabled=true;$('cancelImport').classList.remove('hidden');try{const old=snapshot(),data={};for(const key of ['style','pickup','luggage','support','arrival','registration','registrationMonths','registrationPeople','phraseNotes'])data[key]=old[key];const start=await localApi('import',{url:$('url').value,loader:$('localLoader').value,browser_channel:$('localBrowser').value,timeout:Number($('localTimeout').value),refresh:$('localRefresh').checked,data,rules,prefs});localJob=start.job_id;while(true){const job=await localApi('jobs/'+localJob);$('importStatus').textContent=job.message;if(job.state==='error')throw Error(job.message);if(job.state==='done'){const result=await localApi('results/'+job.result_id);applyGenerated(result);showLocalLink(job.result_id);history.pushState({},'', '/listing/'+job.result_id);$('review').textContent='Карточка загружена. Проверьте факты, словарь и условия; время до вузов добавляется после проверки.';toast('Заголовки, описание и теги готовы. Ссылка карточки сохранена.');break;}await new Promise(resolve=>setTimeout(resolve,1000));}}catch(error){$('importStatus').textContent=error.message;}finally{importing=false;localJob=null;$('import').disabled=false;$('cancelImport').classList.add('hidden');}}
$('cancelImport').onclick=async()=>{if(localJob)try{await localApi('cancel',{job_id:localJob});$('importStatus').textContent='Отмена импорта…';}catch(error){toast(error.message);}};
async function persistLocal(revision=historyCurrent()){const cardEpoch=revision.cardEpoch??editorCardEpoch;const fields=revision.fields,result={fields,titles:[revision.title,...revision.titles.filter(t=>t!==revision.title)].slice(0,3),body:revision.body,tags:revision.tags,listing:{url:fields.url,text:fields.source,images:fields.photos,related:fields.relatedOffers}};const saved=await localApi('save',{result_id:cardEpoch===editorCardEpoch?(localResultId||revision.resultId):revision.resultId,result});if(cardEpoch===editorCardEpoch){showLocalLink(saved.result_id);history.replaceState({},'', '/listing/'+saved.result_id);$('localSaveStatus').textContent='Сохранено '+new Date().toLocaleTimeString('ru-RU');saveDraft();}return saved;}
$('saveLocal').onclick=async()=>{try{const revision=historyCurrent();await persistLocal(revision);const event=await historyUI?.record('save',revision);toast('Сохранено по ссылке'+(event?.snapshot?' · PNG готов':'')+(event?.warning?' · '+event.warning:''));}catch(error){toast(error.message);}};
if(localHosting){$('localBrowser').value=read('most-local-browser',document.querySelector('meta[name="redcn-browser-channel"]')?.content||'chrome');$('localBrowser').onchange=()=>write('most-local-browser',$('localBrowser').value);$('localControls').classList.remove('hidden');$('localHelp').classList.add('hidden');$('importStatus').textContent='Локальный импорт: Chrome и кеш на 24 часа';$('import').textContent='Загрузить и создать';if(startup.resultId){const loadRevision=editorRevision;$('importStatus').textContent='Загружаем сохранённую карточку…';localApi('results/'+startup.resultId).then(result=>{if(editorRevision!==loadRevision)return;applyGenerated(result);showLocalLink(startup.resultId);$('importStatus').textContent='Сохранённая карточка загружена.';}).catch(error=>{if(editorRevision===loadRevision){$('importStatus').textContent=error.message;toast(error.message);}});}}

var fieldLabels=Object.fromEntries(fields.map(id=>[id,document.querySelector('label[for="'+id+'"]')?.textContent||id]));
function renderSourceAudit(){
  $('registration').disabled=false;
  $('sourceFields').replaceChildren();$('furnitureEvidence').replaceChildren();$('sourceWarnings').replaceChildren();
  if(!sourceAudit){$('sourceStats').textContent='После разбора HTML появятся значения и фрагменты источника.';return;}
  if(sourceAudit.warnings?.length)$('sourceAnalysis').open=true;
  $('sourceStats').textContent='Стабильные атрибуты data-name / id + JSON данных · '+(sourceAudit.stats?.records||0)+' уникальных блоков · '+(sourceAudit.stats?.jsonStates||0)+' копий JSON · повторы не суммируются.';
  for(const warning of sourceAudit.warnings||[]){const div=document.createElement('div');div.className='evidence-warning';div.textContent=warning;$('sourceWarnings').append(div);}
  for(const row of sourceAudit.fields||[]){const tr=document.createElement('tr');for(const text of [fieldLabels?.[row.field]||row.field,String(row.value),row.text,row.source]){const td=document.createElement('td');td.textContent=text;tr.append(td);}$('sourceFields').append(tr);}
  const states={present:'Есть',absent:'Нет по источнику',planned:'Планируется',conflict:'Противоречие',unknown:'Не указано',covered:'Уточнено отдельной отметкой'};
  for(const row of sourceAudit.furniture||[]){const tr=document.createElement('tr');for(const text of [row.label,states[row.status]||row.status,(row.matches||[]).map(m=>m.source+': '+m.text).join(' · ')||'Нет подтверждения']){const td=document.createElement('td');td.textContent=text;tr.append(td);}$('furnitureEvidence').append(tr);const box=$('feature-'+row.id);if(box)box.title=(states[row.status]||'')+' · '+(row.matches||[]).map(m=>m.text).join(' · ');}
}
$('registrationPolicy').addEventListener('change',()=>{renderSourceAudit();saveDraft();});
$('importHtml').onclick=()=>$('htmlFile').click();
$('htmlFile').onchange=async()=>{try{const file=$('htmlFile').files[0];if(!file)return;if(file.size>8000000)throw Error('Файл больше 8 МБ');const raw=await file.text();const result=/<(?:!doctype|html|div|script)\b/i.test(raw)?analyzeListingSource(raw,rules):extractFacts(raw,rules),old=snapshot(),keep={};for(const key of ['style','pickup','luggage','support','arrival','registration','phraseNotes'])keep[key]=old[key];populate({...keep,...result.facts,source:result.facts.source||raw});current=null;advanceVariant();generate();$('sourceAnalysis').open=true;$('importStatus').textContent='HTML разобран локально: '+file.name;await historyUI?.record('import');toast('Поля и мебель заполнены. Проверьте предупреждения источника.');}catch(error){toast(error.message);}finally{$('htmlFile').value='';}};
historyUI=setupHistory({read,write,getCurrent:historyCurrent,restore:entry=>{populate(entry.fields);current=objects.some(o=>o.id===entry.objectId)?entry.objectId:null;currentTitles=entry.titles||[entry.title];$('outputTitle').value=entry.title;$('output').value=entry.body;$('tags').value=entry.tags;renderTitles();saveDraft();tab('editor');},local:localHosting,api:localApi,toast});
renderSourceAudit();

function applySourceEnvelope(source){const old=snapshot(),keep={};for(const key of ['style','pickup','luggage','support','arrival','registration','phraseNotes'])keep[key]=old[key];const result=analyzeListingSource(source,rules);populate({...keep,...result.facts});current=null;advanceVariant();generate();$('sourceAnalysis').open=true;}

photoUI=setupPhotos({local:localHosting,hosted:!staticHosting,api:localApi,toast,read,write,onChange:saveDraft,getPost:()=>({url:$('url').value,title:$('outputTitle').value,body:$('output').value,tags:$('tags').value}),onFeatures:applyPhotoFeatures});
relatedUI=setupRelated({getCurrent:snapshot,getObjects:()=>objects,getSource:()=>relatedOffers,onAdd:rows=>{relatedOffers=[...relatedOffers,...rows].filter((r,i,all)=>all.findIndex(x=>x.url===r.url)===i).slice(-40);saveDraft();},toast,onOpen:async row=>{try{await historyUI?.record('save');}catch(error){toast('Не удалось записать текущую версию: '+error.message);return;}if(row.libraryId){const object=objects.find(o=>o.id===row.libraryId);if(object){populate(object.fields||object);current=object.id;currentTitles=object.titles||[object.title];$('outputTitle').value=object.title||'';$('output').value=object.output||object.body||'';$('tags').value=object.tags||'';renderTitles();saveDraft();tab('editor');return;}}const old=snapshot(),keep={};for(const k of ['style','pickup','luggage','support','arrival','registration','phraseNotes'])keep[k]=old[k];populate({...keep,url:row.url});current=null;currentTitles=[];$('outputTitle').value='';$('output').value='';$('tags').value='';renderTitles();saveDraft();tab('editor');if(localHosting)await importLocal();else if(!staticHosting)$('import').click();else toast('Ссылка выбрана. Импортируйте HTML / JSON этой карточки или загрузите её в локальном redcn.');}});
renderPhotos();relatedUI.render();

function renderGeoCatalog(){const stats=getGeoStats();$('geoSummary').textContent=`Записей станций по линиям: ${stats.metro} (${stats.metroNames} названий, ${stats.translatedMetroNames} с китайским вариантом) · улиц и дорог: ${stats.street} · остановочных объектов: ${stats.stop}. Срез: ${stats.updatedAt}. Полнота: ${typeof stats.coverage==='string'?stats.coverage:'см. источники'}`;const rows=searchGeo($('geoSearch').value,{kind:$('geoKind').value,limit:30});$('geoResults').replaceChildren();for(const row of rows){const el=document.createElement('div');el.className='tag-item';el.textContent=row.ru+' → '+(row.zh||'Русское название сохранено целиком')+' · '+row.translationStatus;$('geoResults').append(el);}}
$('geoSearch').oninput=renderGeoCatalog;$('geoKind').onchange=renderGeoCatalog;

function applyPhotoFeatures(observation){
 const blocked=new Set((sourceAudit?.furniture||[]).filter(row=>['absent','planned','conflict'].includes(row.status)).map(row=>row.id));
 const next=new Set((observation.features||[]).filter(id=>!blocked.has(id))),previous=new Set(photoEvidence.map(row=>row.id));
 if(!(observation.features||[]).length&&!previous.size)return;
 for(const id of new Set([...previous,...next])){
  const box=$('feature-'+id),input=$('feature-qty-'+id);if(!box||!input)continue;
  if(!Object.hasOwn(photoFeatureBaseline,id))photoFeatureBaseline[id]={checked:box.checked,count:featureCounts[id]??''};
  const baseline=photoFeatureBaseline[id];
  box.checked=next.has(id)||baseline.checked;
  const count=next.has(id)?Math.max(Number(baseline.count)||0,Number(observation.featureCounts?.[id])||0):Number(baseline.count)||0;
  if(count)featureCounts[id]=count;else delete featureCounts[id];input.value=count||'';
  if(!next.has(id))delete photoFeatureBaseline[id];
 }
 photoEvidence=(observation.photoEvidence||[]).filter(row=>next.has(row.id));
 if((observation.features||[]).some(id=>blocked.has(id)))toast('Фото расходится с данными объявления. Проверьте противоречие в разборе источника.');
 generate();saveDraft();
}

phoneConnection=setupPhoneConnection({local:localHosting,api:localApi,toast,getProfile:()=>({rules:personalRules(),prefs,data:Object.fromEntries(['style','pickup','luggage','support','arrival','registration','registrationMonths','registrationPeople','audience'].map(key=>[key,snapshot()[key]]))})});

function queueProfileSync(){
  clearTimeout(profileSyncTimer);if(!localHosting)return;
  profileSyncTimer=setTimeout(()=>phoneConnection?.updateProfile()?.catch(error=>toast('Настройки сохранены в браузере. Не удалось передать их телефону: '+error.message)),350);
}

const languageRemoteData='https://raw.githubusercontent.com/leunch4off/redcn/feature/rcn-v8-knowledge-photos/data/';
const languagePreviewOrigin=['localhost','127.0.0.1','[::1]','::1'].includes(location.hostname);
let staticLanguageResource=null,staticLanguageRequest=null,staticLanguageForms=null,staticLanguagePairCount=null;
const languageSearchFold=value=>String(value??'').normalize('NFC').toLowerCase().replaceAll('ё','е').trim();
async function ensureStaticLanguageResource(){
  if(staticLanguageResource)return staticLanguageResource;
  if(!staticLanguageRequest)staticLanguageRequest=(async()=>{
    const response=await fetch(languagePreviewOrigin?'/ru-zh-open-lexicon.json':languageRemoteData+'ru-zh-open-lexicon.json');
    if(!response.ok)throw Error('Открытый словарь не загрузился: HTTP '+response.status);
    const resource=await response.json();if(!Array.isArray(resource.entries))throw Error('Неверный формат открытого словаря');
    resource.entries=resource.entries.filter(row=>Array.isArray(row)&&typeof row[0]==='string'&&typeof row[1]==='string');
    staticLanguageResource=resource;staticLanguageForms=new Map((resource.forms||[]).map(([form,lemma])=>[languageSearchFold(form),languageSearchFold(lemma)]));
    setExtendedLexicon(resource);
    staticLanguagePairCount=new Set([...Object.entries(DICTIONARY),...resource.entries].map(([ru,zh])=>languageSearchFold(ru)+'\0'+zh)).size;
    languageUI?.refreshStats();return resource;
  })().catch(error=>{staticLanguageRequest=null;throw error;});
  return staticLanguageRequest;
}
async function searchDesktopLanguage(query,{scope='all',offset=0,limit=40}={}){
  const q=languageSearchFold(query),rows=[];let resource=null;
  if(['all','open'].includes(scope))resource=await ensureStaticLanguageResource();
  if(['all','custom'].includes(scope))for(const [ru,zh] of Object.entries(personalRules()))rows.push({ru,zh,sourceId:'custom',reviewed:true});
  if(['all','rental'].includes(scope))for(const [ru,zh] of Object.entries(DICTIONARY))rows.push({ru,zh,sourceId:'rental-core',reviewed:true});
  if(['all','geo'].includes(scope))for(const row of GEO_CATALOG.records)rows.push({...row,sourceId:row.source||'geo-catalog',reviewed:row.translationStatus==='reviewed'});
  if(resource)for(const [ru,zh,sourceId,sourcePage] of resource.entries)rows.push({ru,zh,sourceId:sourceId||'wiktionary',sourcePage,reviewed:false});
  const lemma=staticLanguageForms?.get(q),seen=new Set();
  const found=rows.filter(row=>{const ru=languageSearchFold(row.ru),zh=languageSearchFold(row.zh);if(!ru.includes(q)&&!zh.includes(q)&&(!lemma||ru!==lemma))return false;const key=ru+'\0'+zh+'\0'+row.sourceId;if(seen.has(key))return false;seen.add(key);return true;});
  found.sort((a,b)=>Number(languageSearchFold(b.ru)===q||languageSearchFold(b.zh)===q)-Number(languageSearchFold(a.ru)===q||languageSearchFold(a.zh)===q)||Number(b.sourceId==='custom')-Number(a.sourceId==='custom')||a.ru.localeCompare(b.ru,'ru'));
  return {entries:found.slice(offset,offset+Math.min(40,limit)),total:found.length,offset};
}
function applyComposedText({part,text,sourceRu}){
  if(!['title','body','tags'].includes(part)||typeof text!=='string'||!text.trim())throw Error('Выберите текст для применения');
  if(part==='title'){currentTitles=[text,...currentTitles.filter(title=>title!==text)].slice(0,3);titleIndex=0;$('outputTitle').value=text;renderTitles();}
  if(part==='body'){$('output').value=text;if(typeof sourceRu==='string'&&sourceRu.trim())$('description').value=sourceRu;}
  if(part==='tags'){$('tags').value=text;renderTagReasons();}
  editorRevision++;saveDraft();syncPhoneCard();historyUI?.record('generate').catch(error=>toast(error.message));
}
languageUI=setupLanguageStudio({
  local:localHosting,api:async(path,data)=>{clearTimeout(profileSyncTimer);await phoneConnection?.updateProfile();return localApi(path,data);},
  getRules:personalRules,onUseRule:usePersonalRule,toast,
  onApplyText:applyComposedText,onApplyTemplate:(kind,index)=>rewritePart(kind==='description'?'body':kind,index),
  translateLocal:text=>translateDraft(text,{rules,mode:'draft'}),searchLocal:searchDesktopLanguage,
  capabilities:()=>({ ...generationCapabilities(),dictionary:{...getLexiconStats(),ruZhPairs:staticLanguagePairCount??undefined,curatedRules:Object.keys(DICTIONARY).length}}),
  catalogBaseUrl:languagePreviewOrigin?'/data/template-catalog/':languageRemoteData+'template-catalog/',
});

function syncPhoneCard(){
 clearTimeout(relaySyncTimer);if(!localHosting||!phoneConnection?.enabled()||isDemo||!$('output').value.trim()||!(Number($('price').value)>0))return;
 const revision=historyCurrent(),expected=editorRevision,cardEpoch=editorCardEpoch,resultId=localResultId;
 relaySyncTimer=setTimeout(async()=>{try{
  await phoneConnection.updateProfile();
  const fields=revision.fields,result={fields,titles:[revision.title,...revision.titles.filter(t=>t!==revision.title)].slice(0,3),body:revision.body,tags:revision.tags,listing:{url:fields.url,text:fields.source,images:fields.photos,related:fields.relatedOffers}};
  const saved=await localApi('save',{result_id:cardEpoch===editorCardEpoch?(localResultId||resultId):resultId,result});if(cardEpoch===editorCardEpoch){showLocalLink(saved.result_id);$('localSaveStatus').textContent=expected===editorRevision?'Синхронизировано с телефоном '+new Date().toLocaleTimeString('ru-RU'):'Готовим обновление для телефона…';}
 }catch(error){toast('Карточка сохранена в браузере. Телефон не получил обновление: '+error.message);}},900);
}

// Presentation design and the private dossier share the same stable Mac card ID.
function presentationCurrentCard(){const revision=historyCurrent(),{resultId,...presentationFields}=revision.fields;return {id:localResultId,identity:editorCardEpoch,fields:presentationFields,titles:[revision.title,...revision.titles.filter(t=>t!==revision.title)],body:revision.body,tags:revision.tags};}
async function ensurePresentationCard(){if(!localHosting)throw Error('Сохранение на Mac доступно в локальном редакторе.');const epoch=editorCardEpoch;if(!$('outputTitle').value.trim()&&!$('output').value.trim())throw Error('Сначала заполните карточку и создайте описание.');clearTimeout(relaySyncTimer);const saved=await persistLocal();if(epoch!==editorCardEpoch)throw Error('Карточка изменилась. Повторите действие.');return saved.result_id;}
async function localExportBlob(asset){const url=new URL(asset.url,location.origin);if(url.origin!==location.origin||!/^\/api\/local\/(exports|card-attachments)\//.test(url.pathname))throw Error('Неверный адрес файла');const response=await fetch(url.pathname,{headers:{'X-Redcn-Token':document.querySelector('meta[name="redcn-token"]')?.content||''},cache:'no-store'});if(!response.ok){const error=await response.json().catch(()=>({}));throw Error(error.error||'Файл недоступен');}return response.blob();}
async function localDownloadAsset(asset){cardDownloadBlob(await localExportBlob(asset),asset.name);cardFilesUI?.refresh();}
let presentationReady=Promise.resolve();
function ensurePresentation(){
 if(presentationUI)return presentationUI;
 let initialDesignRequest=null;
 const designApi=localHosting?(path,data)=>{const request=localApi(path,data);if(path==='design'&&!data)initialDesignRequest=request;return request;}:null;
 presentationUI=setupPresentationStudio({mount:$('presentationMount'),getRules:()=>rules,getCard:presentationCurrentCard,getPhotos:()=>photoUI?.presentationPhotos()||[],ensureCard:ensurePresentationCard,api:designApi,downloadExport:localDownloadAsset,getExportBlob:localExportBlob,getMapImage:localHosting?(card,options={})=>localApi('listing-map',{latitude:card.fields?.latitude,longitude:card.fields?.longitude,zoom:options.zoom||15,style:options.style||'navy',...((options.pinColor||options.theme?.social?.accent)?{pinColor:options.pinColor||options.theme.social.accent}:{})}):null,toast});
 presentationReady=initialDesignRequest?initialDesignRequest.catch(()=>{}):Promise.resolve();
 return presentationUI;
}
async function refreshPresentation(){const ui=ensurePresentation();await presentationReady;return ui.refresh();}
archiveUI=localHosting?setupCardArchive({mount:$('cardArchiveMount'),getCardId:()=>localResultId,ensureCard:ensurePresentationCard,api:localApi,downloadAttachment:localDownloadAsset,toast}):null;
cardFilesUI=setupCardFiles({mount:$('cardFilesMount'),getCardId:()=>localResultId,api:localApi,download:localDownloadAsset,toast});
$('openPresentation').onclick=()=>tab('presentation');
if(localHosting){archiveUI.refresh();cardFilesUI.refresh();}else{$('cardFilesMount').classList.add('hidden');$('macCardCatalog').classList.add('hidden');}
let macCatalogRevision=0;
async function renderMacCards(){if(!localHosting)return;const revision=++macCatalogRevision,list=$('macCardList');list.textContent='Загружаем карточки с компьютера…';try{let cursor='0',more=true,rows=[];while(more&&rows.length<1000){const page=await localApi('cards?after='+encodeURIComponent(cursor));rows.push(...page.cards);more=page.hasMore;cursor=page.cursor;}if(revision!==macCatalogRevision)return;list.replaceChildren();for(const card of rows){const row=document.createElement('div'),link=document.createElement('a'),text=document.createElement('span');row.className='card-file-row';link.className='btn';link.href='/listing/'+card.id;link.textContent='Открыть';text.textContent=[card.title,card.metro,card.price&&rubles(card.price)+' ₽'].filter(Boolean).join(' · ');row.append(link,text);list.append(row);}if(!rows.length)list.textContent='Здесь появятся все карточки, подготовленные на Mac, включая отправленные с телефона.';}catch(error){if(revision===macCatalogRevision)list.textContent=error.message;}}
$('macCardRefresh').onclick=renderMacCards;

universityUI=setupUniversityUI({local:localHosting,api:localApi,toast,onSelect:()=>{universityRoutesUI?.setPrimary({university:$('university').value,campus:$('campus').value});editorRevision++;saveDraft();}});



// Routes are an editable draft; existing source text is never replaced here.
function universityRouteFields(){return Object.fromEntries(['latitude','longitude','address','city','metro','university','campus','universities','uniTime','uniMode','route'].map(key=>[key,$(key).value]));}
universityRoutesUI=setupUniversityRoutes({mount:$('universityRoutesMount'),getFields:universityRouteFields,translate:value=>translateDraft(value,{rules}).text,onChange:rows=>{universityRouteDraft=rows;editorRevision++;saveDraft();}});
universityRoutesUI.refresh({...universityRouteFields(),...(Array.isArray(universityRouteDraft)?{universityRoutes:universityRouteDraft}:{})});
for(const id of ['university','campus'])$(id).addEventListener('change',()=>universityRoutesUI?.setPrimary({university:$('university').value,campus:$('campus').value}));
$('universities').addEventListener('change',()=>{universityRoutesUI.refresh(universityRouteFields());universityRouteDraft=universityRoutesUI.value();editorRevision++;saveDraft();});
for(const id of ['uniTime','uniMode','route'])$(id).addEventListener('change',()=>{const rows=universityRoutesUI.value();if(!rows.length)return;rows[0]={...rows[0],time:$('uniTime').value,mode:$('uniMode').value,transport:$('route').value};universityRoutesUI.refresh({...universityRouteFields(),universityRoutes:rows});universityRouteDraft=universityRoutesUI.value();editorRevision++;saveDraft();});
if(localHosting){
 listingWatchUI=setupListingWatch({mount:$('listingWatchMount'),api:localApi,toast,onScan:async url=>{tab('editor');$('url').value=url;await importLocal();}});
 importAuditUI=setupImportDiagnostics({mount:$('importDiagnosticsMount'),api:localApi,toast,getBundle:async()=>{const r=await fetch('/api/local/import-diagnostics/tg.zip',{headers:{'X-Redcn-Token':document.querySelector('meta[name="redcn-token"]')?.content||''},cache:'no-store'});if(!r.ok){const v=await r.json().catch(()=>({}));throw Error(v.error||'Архив ещё не готов');}return r.blob();}});
 importAuditUI.refresh();
}else $('listingWatchMount').textContent='Автоматический мониторинг работает на сервере Mac.';

// Ready means the editor handlers are available; optional panels initialize on demand.
document.documentElement.dataset.editorReady='true';
performance.mark?.('most-editor-ready');
