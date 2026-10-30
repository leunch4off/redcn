import {DICTIONARY,FEATURES,DEFAULT_PREFS,generateListing,extractFacts,analyzeListingSource,rubles} from './engine.mjs';
import {TAG_CATALOG,listingEligible} from './content-tools.mjs';
import {setupWordEditor} from './word-editor.mjs';
import {setupInsights} from './insights-ui.mjs';
import {setupHistory} from './history-ui.mjs';
import {photoSources} from './media-core.mjs';
import {setupPhotos} from './photo-ui.mjs';
import {getGeoStats,searchGeo} from './geo-catalog.mjs';
import {setupRelated} from './related-ui.mjs';
import {editorStartup} from './editor-state.mjs';

const $=id=>document.getElementById(id);
const read=(key,fallback)=>{try{return JSON.parse(localStorage.getItem(key))??fallback;}catch{return fallback;}};
let toastTimer;
function toast(message){$('toast').textContent=message;$('toast').classList.remove('hidden');clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').classList.add('hidden'),4500);}
function write(key,value){try{localStorage.setItem(key,JSON.stringify(value));return true;}catch{toast('Не удалось сохранить: хранилище браузера заполнено или недоступно. Экспортируйте копию.');return false;}}
const fields=['url','source','name','district','complex','districtZh','metro','walk','area','rooms','floor','floors','price','deposit','university','uniTime','uniMode','route','details','pickup','arrival','support','luggage','style','layout','kitchen','living','bedroomArea','occupants','transit','universities','highlight','description','utilities','rentFreeMonths','minMonths','separate','children','pets','shortTerm','registration','registrationMonths','registrationPeople','campus','region','audience','ready','newRenovation','seller','commission','phraseNotes','city','address','renovation','windows','bathroom','combinedWcs','separateWcs','buildingYear','buildingType','buildingSeries','floorType','entrances','heating','emergency','ceiling','balconies','loggias','prepayMonths','contractMonths','leaseTerm','registrationPolicy','childrenPolicy','petPolicy','smokingPolicy','guestPolicy','subletPolicy','shortTermPolicy','sourceDescription','railways','lift','latitude','longitude'];
const defaults={style:'compact',layout:'classic',utilities:'unknown',registration:'assist',support:true,uniMode:'步行',seller:'unknown',audience:'general'};
const storedRules=read('most-rules',null);
let rules=read('most-rules-version',0)>=2&&storedRules?storedRules:{...DICTIONARY,...storedRules};
if(read('most-rules-version',0)<2&&write('most-rules',rules))write('most-rules-version',2);
if(read('most-rules-version',0)<3){for(const key of ['Москва','СВАО','район Лианозово','р-н Лианозово','Лианозово','Алтуфьево','Угличская ул.','Угличская улица','Марк','Бескудниково','Евроремонт','На улицу и двор','совмещенный','Монолитный','Железобетонные','Индивидуальный проект','Центральное','в Лианозовском парке'])if(!Object.hasOwn(rules,key))rules[key]=DICTIONARY[key];if(write('most-rules',rules))write('most-rules-version',3);}
if(read('most-rules-version',0)<4){for(const [key,value] of Object.entries(DICTIONARY))if(!Object.hasOwn(rules,key))rules[key]=value;if(write('most-rules',rules))write('most-rules-version',4);}
let objects=read('most-objects',[]);if(!Array.isArray(objects))objects=[];
let prefs={...DEFAULT_PREFS,...read('most-prefs',{})};
let current=null,titleIndex=0,currentTitles=[],sourceAudit=null,featureCounts={},historyUI=null;
let copyVariant=0,localResultId=null,localJob=null,isDemo=false,editorRevision=0;
let titleVariant=0,wordEditor=null,photoUrls=[],insightsUI=null,photoUI=null,relatedUI=null,relatedOffers=[],photoEdits={},photoEvidence=[],photoFeatureBaseline={};
const hosting=document.querySelector('meta[name="redcn-hosting"]')?.content;
const localHosting=hosting==='local',staticHosting=hosting==='github-pages'||localHosting;
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
function snapshot(){const value={isDemo,titleVariant,copyVariant,photos:[...photoUrls],photoEdits:photoUI?.state()||photoEdits,relatedOffers:[...relatedOffers],sourceAudit,photoEvidence:[...photoEvidence],photoFeatureBaseline:{...photoFeatureBaseline},featureCounts:{...featureCounts}};for(const key of fields)value[key]=$(key).type==='checkbox'?$(key).checked:$(key).value;if(value.source?.length>50000)value.source=value.source.slice(0,50000);value.features=FEATURES.filter(x=>$('feature-'+x[0]).checked).map(x=>x[0]);return value;}
function populate(value){editorRevision++;clearTimeout(toastTimer);$('toast').classList.add('hidden');localResultId=null;$('resultLink').removeAttribute('href');$('resultLink').textContent='Сохраните карточку, чтобы получить ссылку';$('sourceLink').classList.add('hidden');$('localResult').classList.toggle('hidden',!localHosting);wordEditor?.reset();const v={...defaults,...value};isDemo=v.isDemo===true;renderDemoNotice();sourceAudit=v.sourceAudit||null;photoEvidence=Array.isArray(v.photoEvidence)?v.photoEvidence:[];photoFeatureBaseline=v.photoFeatureBaseline&&typeof v.photoFeatureBaseline==='object'?v.photoFeatureBaseline:{};featureCounts=v.featureCounts||{};titleVariant=Number(v.titleVariant)||0;copyVariant=Number(v.copyVariant)||0;photoUrls=photoSources(v.photos);photoEdits=v.photoEdits||{};relatedOffers=Array.isArray(v.relatedOffers)?v.relatedOffers.slice(0,40):[];for(const key of fields){if($(key).type==='checkbox')$(key).checked=!!v[key];else $(key).value=v[key]??'';}for(const [id] of FEATURES){$('feature-'+id).checked=Array.isArray(v.features)&&v.features.includes(id);$('feature-qty-'+id).value=featureCounts[id]||'';}renderSourceAudit();renderPhotos();relatedUI?.render();}
function counts(){const title=Array.from($('outputTitle').value).length,body=Array.from($('output').value+'\n'+$('tags').value).length;$('titleCount').textContent='Заголовок: '+title+' знаков';$('bodyCount').textContent='Описание и теги: '+body+' знаков';}
function saveDraft(){write('most-draft',{version:2,id:current,fields:snapshot(),title:$('outputTitle').value,output:$('output').value,tags:$('tags').value,titles:currentTitles});counts();wordEditor?.render();}
function tab(name){document.querySelectorAll('main>section').forEach(e=>e.classList.toggle('hidden',e.id!==name));document.querySelectorAll('nav button').forEach(b=>b.classList.toggle('active',b.dataset.tab===name));$('crumb').textContent=document.querySelector('[data-tab="'+name+'"]').textContent.trim();if(name==='library')renderList();if(name==='rules')renderRules();if(name==='roadmap')loadRoadmap();if(name==='knowledge')insightsUI?.renderKnowledge();if(name==='analytics')insightsUI?.renderAnalytics();if(name==='history')historyUI?.render();}
document.querySelectorAll('nav button').forEach(button=>button.onclick=()=>tab(button.dataset.tab));
function renderTitles(){
  $('variants').replaceChildren();
  currentTitles.forEach((title,i)=>{const button=document.createElement('button');button.className='btn variant'+(i===titleIndex?' chosen':'');button.textContent=title;button.setAttribute('aria-pressed',String(i===titleIndex));button.onclick=()=>{titleIndex=i;$('outputTitle').value=title;renderTitles();saveDraft();};$('variants').append(button);});
}
function generate(){
  let result=generateListing(snapshot(),rules,prefs);if(result.problems.length){$('review').classList.add('error');$('review').textContent=result.problems.join(' · ');toast('Проверьте числовые поля');return;}
  let recent=read('most-copy-fingerprints',{}),key=$('url').value||$('name').value||'draft',known=recent[key]||[];const fingerprint=text=>{let h=2166136261;for(const c of text)h=Math.imul(h^c.codePointAt(0),16777619);return(h>>>0).toString(16);};for(let attempt=0;attempt<128&&known.includes(fingerprint(result.body));attempt++){advanceVariant();result=generateListing(snapshot(),rules,prefs);}recent[key]=[...known,fingerprint(result.body)].slice(-200);write('most-copy-fingerprints',Object.fromEntries(Object.entries(recent).slice(-30)));
  currentTitles=result.titles;titleIndex=0;$('outputTitle').value=currentTitles[0];$('output').value=result.body;$('tags').value=result.tags;renderTitles();renderTagReasons(result.tagSuggestions);
  $('review').classList.toggle('error',result.unknown.length>0);$('review').textContent=result.unknown.length?'Нужен перевод в словаре: '+result.unknown.join(' · ')+'. Эти фразы пока остаются в исходном языке.':(sourceAudit?.warnings?.length?sourceAudit.warnings.join(' · '):'Текст готов. Проверьте цену, маршрут, регистрацию и услуги перед публикацией.');saveDraft();historyUI?.record('generate').catch(error=>toast('История не сохранена: '+error.message));captureKnowledge('example',$('outputTitle').value,$('output').value);
}
function advanceVariant(){let counters=read('most-generation-counters',{});const v=snapshot(),key=v.url||v.name||v.source.slice(0,150)||'draft';const next=Math.max(titleVariant,copyVariant,Number(counters[key])||0)+1;titleVariant=copyVariant=next;counters[key]=next;write('most-generation-counters',Object.fromEntries(Object.entries(counters).slice(-200)));}
$('generate').onclick=()=>{advanceVariant();generate();};
$('rewriteCopy').onclick=()=>{if($('output').value&&!confirm('Создать новую подачу по текущим полям? Ручные правки описания будут заменены.'))return;advanceVariant();generate();};
$('rotateTitles').onclick=()=>{titleVariant++;const result=generateListing(snapshot(),rules,prefs);currentTitles=result.titles;titleIndex=0;$('outputTitle').value=currentTitles[0];$('tags').value=result.tags;renderTitles();renderTagReasons(result.tagSuggestions);saveDraft();historyUI?.record('generate').catch(error=>toast(error.message));toast('Новые заголовки и теги. Текст описания сохранён.');};
function renderTagReasons(suggestions=[]){$('tagReasons').replaceChildren();for(const item of suggestions){const div=document.createElement('div');div.className='tag-item';div.textContent='#'+item.tag+' — '+item.ru+' · '+item.reason;$('tagReasons').append(div);}}
function renderTagCatalog(){const q=$('tagSearch').value.trim().toLowerCase();$('tagCatalog').replaceChildren();for(const [tag,ru] of TAG_CATALOG.filter(x=>(x[0]+' '+x[1]).toLowerCase().includes(q))){const button=document.createElement('button');button.className='btn word-result';button.textContent='#'+tag+' — '+ru;button.onclick=()=>{const tags=new Set($('tags').value.split(/\s+/).filter(Boolean));tags.add('#'+tag);$('tags').value=[...tags].join(' ');saveDraft();};$('tagCatalog').append(button);}}
$('tagSearch').oninput=renderTagCatalog;
for(const key of fields)$(key).addEventListener('input',()=>{editorRevision++;saveDraft();});
for(const [id] of FEATURES)$('feature-'+id).addEventListener('change',()=>{editorRevision++;if(photoFeatureBaseline[id])photoFeatureBaseline[id].checked=$('feature-'+id).checked;saveDraft();});
for(const key of ['outputTitle','output','tags'])$(key).addEventListener('input',()=>{editorRevision++;saveDraft();});
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
function historyCurrent(){return {title:$('outputTitle').value,body:$('output').value,tags:$('tags').value,fields:snapshot(),titles:[...currentTitles],objectId:current,resultId:localResultId};}
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
function renderRules(){
  $('ruleList').replaceChildren();for(const [key,value] of Object.entries(rules)){
    const row=document.createElement('div'),a=document.createElement('input'),b=document.createElement('input'),remove=document.createElement('button');row.className='rule';a.value=key;a.readOnly=true;a.setAttribute('aria-label','Триггер '+key);b.value=value;b.setAttribute('aria-label','Перевод '+key);
    b.onchange=()=>{rules[key]=b.value;write('most-rules',rules);};remove.textContent='×';remove.className='btn';remove.style.padding='5px';remove.setAttribute('aria-label','Удалить правило '+key);remove.onclick=()=>{delete rules[key];write('most-rules',rules);renderRules();};row.append(a,b,remove);$('ruleList').append(row);
  }
}
$('addRule').onclick=()=>{const key=$('trigger').value.trim(),value=$('translation').value.trim();if(!key||!value)return toast('Заполните триггер и перевод');rules[key]=value;if(write('most-rules',rules)){renderRules();$('trigger').value='';$('translation').value='';toast('Правило сохранено');}};
for(const key of ['benefits','contact','brandTags'])$(key).value=prefs[key];
$('saveSettings').onclick=()=>{const next={benefits:$('benefits').value,contact:$('contact').value,brandTags:$('brandTags').value};if(write('most-prefs',next)){prefs=next;toast('Преимущества и теги сохранены. Создайте текст заново, чтобы применить.');}};
function download(name,data){const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
$('export').onclick=async()=>download('cian-cn-rednote-backup.json',{version:2,objects,rules,prefs,wordCustom,history:await historyUI?.exportEntries(),...insightsUI?.exportData(),draft:read('most-draft',null)});
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
renderTagCatalog();renderTagReasons(generateListing(snapshot(),rules,prefs).tagSuggestions);counts();

function renderPhotos(){photoUrls=photoSources(photoUrls);photoUI?.setSources(photoUrls,photoEdits);}
$('importCli').onclick=()=>$('cliFile').click();
$('cliFile').onchange=async()=>{try{const file=$('cliFile').files[0];if(!file)return;if(file.size>8000000)throw Error('Файл больше 8 МБ');const value=JSON.parse(await file.text());if(!value.fields||typeof value.fields!=='object'||!Array.isArray(value.titles)||!value.titles.every(t=>typeof t==='string')||typeof value.body!=='string'||typeof value.tags!=='string')throw Error('Нужен JSON-результат Python CLI с fields/titles/body/tags');populate({...value.fields,photos:value.listing?.images||value.fields.photos||[]});current=null;currentTitles=value.titles;titleIndex=0;$('outputTitle').value=value.titles[0]||'';$('output').value=value.body;$('tags').value=value.tags;renderTitles();renderTagReasons(value.tagSuggestions);saveDraft();toast('Карточка из Python импортирована. Проверьте поля перед публикацией.');}catch(error){toast(error.message);}finally{$('cliFile').value='';}};
let roadmapDataClient=null;
async function loadRoadmap(){try{const response=await fetch(staticHosting?'./plan.json':'/api/roadmap',{cache:'no-store'});if(!response.ok)throw Error();const data=await response.json();if(!Array.isArray(data.tasks))throw Error();roadmapDataClient=data;renderRoadmap();}catch{$('roadmapSummary').textContent='План не загрузился. Исходник: plan.json и GitHub issue Roadmap.';}}
function renderRoadmap(){if(!roadmapDataClient)return;const tasks=roadmapDataClient.tasks;const done=tasks.filter(t=>t.status==='done').length;$('roadmapSummary').replaceChildren(document.createTextNode(done+' / '+tasks.length+' выполнено · '+(tasks.length-done)+' в планах. '));if(roadmapDataClient.github_issue_url){const a=document.createElement('a');a.textContent='Roadmap на GitHub ↗';a.href=roadmapDataClient.github_issue_url;a.target='_blank';a.rel='noopener noreferrer';$('roadmapSummary').append(a);}const q=$('roadmapSearch').value.toLowerCase(),mode=$('roadmapFilter').value;$('roadmapList').replaceChildren();for(const task of tasks.filter(t=>(mode==='all'||t.status===mode)&&(t.id+' '+t.section+' '+t.title+' '+t.acceptance).toLowerCase().includes(q))){const card=document.createElement('article'),head=document.createElement('header'),p=document.createElement('p'),evidence=document.createElement('div');card.className='task '+task.status;head.textContent=(task.status==='done'?'✓ ':'○ ')+task.id+' · '+task.title;p.textContent=task.acceptance;evidence.className='evidence';evidence.textContent=task.priority+' · '+task.section+(task.evidence.length?' · '+task.evidence.join(' · '):' · Проверка ещё не завершена');card.append(head,p,evidence);$('roadmapList').append(card);}}
$('roadmapSearch').oninput=renderRoadmap;$('roadmapFilter').onchange=renderRoadmap;
function captureKnowledge(type,title,text){if(insightsUI&&$('rememberContent').checked&&text.trim())insightsUI.capture({type,title,text,source:$('url').value,status:'unreviewed'});}
insightsUI=setupInsights({read,write,toast,onRule:(ru,cn)=>{rules[ru]=cn;write('most-rules',rules);renderRules();}});
if(staticHosting&&!localHosting){$('importStatus').textContent='GitHub Pages: вставьте текст или импортируйте JSON из Python CLI.';$('import').textContent='Импорт через CLI';}

// Local desktop backend is available only in the loopback-served build.
function noteEntries(){return $('phraseNotes').value.split('\n').map(line=>line.split('|').map(s=>s.trim())).filter(parts=>parts.length>=2&&parts[0]&&parts[1]).map(([zh,ru,...alternatives])=>({zh,ru,alternatives:alternatives.filter(Boolean)}));}
$('phraseNotes').addEventListener('input',()=>wordEditor?.render());
async function localApi(path,data){const response=await fetch('/api/local/'+path,{method:data?'POST':'GET',headers:{'X-Redcn-Token':document.querySelector('meta[name="redcn-token"]')?.content||'',...(data?{'Content-Type':'application/json'}:{})},...(data?{body:JSON.stringify(data)}:{})});const value=await response.json();if(!response.ok)throw Error(value.error||'Ошибка локального сервера');return value;}
function applyGenerated(value){if(!value.fields||!Array.isArray(value.titles)||typeof value.body!=='string'||typeof value.tags!=='string')throw Error('Неверный результат');populate({...value.fields,photos:value.listing?.images||value.fields.photos||[]});current=null;currentTitles=value.titles;titleIndex=0;$('outputTitle').value=value.titles[0]||'';$('output').value=value.body;$('tags').value=value.tags;renderTitles();renderTagReasons(value.tagSuggestions);saveDraft();captureKnowledge('example',$('outputTitle').value,$('output').value);}
function showLocalLink(id){localResultId=id;$('localResult').classList.remove('hidden');$('resultLink').href='/listing/'+id;$('resultLink').textContent=location.origin+'/listing/'+id;try{const u=new URL($('url').value);if(u.protocol==='https:'&&/(^|\.)(cian\.ru|avito\.ru)$/i.test(u.hostname)){$('sourceLink').href=u.href;$('sourceLink').classList.remove('hidden');}else $('sourceLink').classList.add('hidden');}catch{$('sourceLink').classList.add('hidden');}}
async function importLocal(){if(importing)return;importing=true;$('import').disabled=true;$('cancelImport').classList.remove('hidden');try{const old=snapshot(),data={};for(const key of ['style','pickup','luggage','support','arrival','registration','registrationMonths','registrationPeople','phraseNotes'])data[key]=old[key];const start=await localApi('import',{url:$('url').value,loader:$('localLoader').value,browser_channel:$('localBrowser').value,timeout:Number($('localTimeout').value),refresh:$('localRefresh').checked,data,rules,prefs});localJob=start.job_id;while(true){const job=await localApi('jobs/'+localJob);$('importStatus').textContent=job.message;if(job.state==='error')throw Error(job.message);if(job.state==='done'){const result=await localApi('results/'+job.result_id);applyGenerated(result);showLocalLink(job.result_id);history.pushState({},'', '/listing/'+job.result_id);$('review').textContent='Карточка загружена. Проверьте факты, словарь и условия; время до вузов добавляется после проверки.';toast('Заголовки, описание и теги готовы. Ссылка карточки сохранена.');break;}await new Promise(resolve=>setTimeout(resolve,1000));}}catch(error){$('importStatus').textContent=error.message;}finally{importing=false;localJob=null;$('import').disabled=false;$('cancelImport').classList.add('hidden');}}
$('cancelImport').onclick=async()=>{if(localJob)try{await localApi('cancel',{job_id:localJob});$('importStatus').textContent='Отмена импорта…';}catch(error){toast(error.message);}};
async function persistLocal(revision=historyCurrent()){const fields=revision.fields,result={fields,titles:[revision.title,...revision.titles.filter(t=>t!==revision.title)].slice(0,3),body:revision.body,tags:revision.tags,listing:{url:fields.url,text:fields.source,images:fields.photos,related:fields.relatedOffers}};const saved=await localApi('save',{result_id:localResultId,result});showLocalLink(saved.result_id);history.replaceState({},'', '/listing/'+saved.result_id);$('localSaveStatus').textContent='Сохранено '+new Date().toLocaleTimeString('ru-RU');return saved;}
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
$('geoSearch').oninput=renderGeoCatalog;$('geoKind').onchange=renderGeoCatalog;renderGeoCatalog();

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
