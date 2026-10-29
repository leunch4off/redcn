import {DICTIONARY,FEATURES,DEFAULT_PREFS,generateListing,extractFacts,rubles} from './engine.mjs';
import {TAG_CATALOG,listingEligible} from './content-tools.mjs';
import {setupWordEditor} from './word-editor.mjs';

const $=id=>document.getElementById(id);
const read=(key,fallback)=>{try{return JSON.parse(localStorage.getItem(key))??fallback;}catch{return fallback;}};
let toastTimer;
function toast(message){$('toast').textContent=message;$('toast').classList.remove('hidden');clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').classList.add('hidden'),4500);}
function write(key,value){try{localStorage.setItem(key,JSON.stringify(value));return true;}catch{toast('Не удалось сохранить: хранилище браузера заполнено или недоступно. Экспортируйте копию.');return false;}}
const fields=['url','source','name','district','complex','districtZh','metro','walk','area','rooms','floor','floors','price','deposit','university','uniTime','uniMode','route','details','pickup','arrival','support','luggage','style','layout','kitchen','living','bedroomArea','occupants','transit','universities','highlight','description','utilities','rentFreeMonths','minMonths','separate','children','pets','shortTerm','registration','registrationMonths','registrationPeople','campus','region','audience','ready','newRenovation','seller','commission'];
const defaults={style:'compact',layout:'classic',utilities:'unknown',registration:'assist',support:true,uniMode:'步行',seller:'unknown',audience:'general'};
const storedRules=read('most-rules',null);
let rules=read('most-rules-version',0)===2&&storedRules?storedRules:{...DICTIONARY,...storedRules};
if(read('most-rules-version',0)!==2&&write('most-rules',rules))write('most-rules-version',2);
let objects=read('most-objects',[]);if(!Array.isArray(objects))objects=[];
let prefs={...DEFAULT_PREFS,...read('most-prefs',{})};
let current=null,titleIndex=0,currentTitles=[];
let titleVariant=0,wordEditor=null,photoUrls=[];
let wordCustom=read('most-word-custom',[]);if(!Array.isArray(wordCustom))wordCustom=[];

// Generate checkboxes using safe DOM methods; nothing from a listing becomes HTML.
const groupNames={bedroom:'СПАЛЬНЯ',living:'ГОСТИНАЯ',kitchen:'КУХНЯ',bathroom:'САНУЗЕЛ',storage:'ХРАНЕНИЕ'};
for(const group of Object.keys(groupNames)){
  const heading=document.createElement('div');heading.className='feature-group';heading.textContent=groupNames[group];$('featureGrid').append(heading);
  for(const [id,,label,key] of FEATURES.filter(x=>x[3]===group)){
    const wrapper=document.createElement('label'),input=document.createElement('input');input.type='checkbox';input.id='feature-'+id;
    wrapper.append(input,document.createTextNode(label));$('featureGrid').append(wrapper);
  }
}
function snapshot(){const value={titleVariant,photos:photoUrls};for(const key of fields)value[key]=$(key).type==='checkbox'?$(key).checked:$(key).value;value.features=FEATURES.filter(x=>$('feature-'+x[0]).checked).map(x=>x[0]);return value;}
function populate(value){wordEditor?.reset();const v={...defaults,...value};titleVariant=Number(v.titleVariant)||0;photoUrls=Array.isArray(v.photos)?v.photos:[];renderPhotos();for(const key of fields){if($(key).type==='checkbox')$(key).checked=!!v[key];else $(key).value=v[key]??'';}for(const [id] of FEATURES)$('feature-'+id).checked=Array.isArray(v.features)&&v.features.includes(id);}
function counts(){const title=Array.from($('outputTitle').value).length,body=Array.from($('output').value+'\n'+$('tags').value).length;$('titleCount').textContent='Заголовок: '+title+' знаков';$('bodyCount').textContent='Описание и теги: '+body+' знаков';}
function saveDraft(){write('most-draft',{version:2,id:current,fields:snapshot(),title:$('outputTitle').value,output:$('output').value,tags:$('tags').value,titles:currentTitles});counts();wordEditor?.render();}
function tab(name){document.querySelectorAll('main>section').forEach(e=>e.classList.toggle('hidden',e.id!==name));document.querySelectorAll('nav button').forEach(b=>b.classList.toggle('active',b.dataset.tab===name));$('crumb').textContent=document.querySelector('[data-tab="'+name+'"]').textContent.trim();if(name==='library')renderList();if(name==='rules')renderRules();if(name==='roadmap')loadRoadmap();}
document.querySelectorAll('nav button').forEach(button=>button.onclick=()=>tab(button.dataset.tab));
function renderTitles(){
  $('variants').replaceChildren();
  currentTitles.forEach((title,i)=>{const button=document.createElement('button');button.className='btn variant'+(i===titleIndex?' chosen':'');button.textContent=title;button.setAttribute('aria-pressed',String(i===titleIndex));button.onclick=()=>{titleIndex=i;$('outputTitle').value=title;renderTitles();saveDraft();};$('variants').append(button);});
}
function generate(){
  const result=generateListing(snapshot(),rules,prefs);if(result.problems.length){$('review').classList.add('error');$('review').textContent=result.problems.join(' · ');toast('Проверьте числовые поля');return;}
  currentTitles=result.titles;titleIndex=0;$('outputTitle').value=currentTitles[0];$('output').value=result.body;$('tags').value=result.tags;renderTitles();renderTagReasons(result.tagSuggestions);
  $('review').classList.toggle('error',result.unknown.length>0);$('review').textContent=result.unknown.length?'Нужен перевод в словаре: '+result.unknown.join(' · ')+'. Эти фразы пока остаются в исходном языке.':'Текст готов. Проверьте цену, маршрут, регистрацию и услуги перед публикацией.';saveDraft();
}
$('generate').onclick=generate;
$('rotateTitles').onclick=()=>{titleVariant++;const result=generateListing(snapshot(),rules,prefs);currentTitles=result.titles;titleIndex=0;$('outputTitle').value=currentTitles[0];$('tags').value=result.tags;renderTitles();renderTagReasons(result.tagSuggestions);saveDraft();toast('Новые заголовки и теги. Текст описания сохранён.');};
function renderTagReasons(suggestions=[]){$('tagReasons').replaceChildren();for(const item of suggestions){const div=document.createElement('div');div.className='tag-item';div.textContent='#'+item.tag+' — '+item.ru+' · '+item.reason;$('tagReasons').append(div);}}
function renderTagCatalog(){const q=$('tagSearch').value.trim().toLowerCase();$('tagCatalog').replaceChildren();for(const [tag,ru] of TAG_CATALOG.filter(x=>(x[0]+' '+x[1]).toLowerCase().includes(q))){const button=document.createElement('button');button.className='btn word-result';button.textContent='#'+tag+' — '+ru;button.onclick=()=>{const tags=new Set($('tags').value.split(/\s+/).filter(Boolean));tags.add('#'+tag);$('tags').value=[...tags].join(' ');saveDraft();};$('tagCatalog').append(button);}}
$('tagSearch').oninput=renderTagCatalog;
for(const key of fields)$(key).addEventListener('input',saveDraft);
for(const [id] of FEATURES)$('feature-'+id).addEventListener('change',saveDraft);
for(const key of ['outputTitle','output','tags'])$(key).addEventListener('input',saveDraft);
function extract(){
  if(!$('source').value.trim())return toast('Сначала вставьте текст объявления');
  const {facts,warnings}=extractFacts($('source').value,rules);
  // Preserve service policy, but clear all property facts to avoid borrowing from the last object.
  const old=snapshot(),keep={};for(const key of ['url','source','style','pickup','luggage','support','arrival','registration','registrationMonths','registrationPeople'])keep[key]=old[key];
  populate({...keep,...facts});current=null;$('output').value='';$('outputTitle').value='';$('tags').value='';currentTitles=[];renderTitles();
  $('review').textContent=warnings.join('\n');$('review').classList.remove('error');saveDraft();toast('Факты извлечены. Проверьте поля и отметки мебели.');
}
$('extract').onclick=extract;
let importing=false;
$('import').onclick=async()=>{
  if(importing)return;let raw=$('url').value;
  try{const u=new URL(raw);if(u.protocol!=='https:'||!/(^|\.)(cian\.ru|avito\.ru)$/i.test(u.hostname)||u.username||u.password||u.port)throw Error('Введите HTTPS-ссылку ЦИАН или Авито.');
    let cache=read('most-cache',{}),saved=cache[u.href];
    if(saved&&Date.now()-saved.at<86400000){$('source').value=saved.text;extract();$('importStatus').textContent='Загружено из кеша · '+new Date(saved.at).toLocaleString('ru-RU');return;}
    importing=true;$('import').disabled=true;$('importStatus').textContent='Загружаем страницу…';
    const response=await fetch('/api/import',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url:u.href})});
    const data=await response.json();if(!response.ok)throw Error(data.error||'Источник не разрешил загрузку.');
    $('source').value=data.text;cache[u.href]={at:Date.now(),text:data.text};
    cache=Object.fromEntries(Object.entries(cache).sort((a,b)=>b[1].at-a[1].at).slice(0,20));write('most-cache',cache);extract();$('importStatus').textContent='Страница загружена. Проверьте извлечённые данные.';
  }catch(error){$('importStatus').textContent=error.message+' Вставьте текст вручную.';}
  finally{importing=false;$('import').disabled=false;}
};
async function copy(part){
  if(!$('outputTitle').value&&!$('output').value)return toast('Сначала создайте текст');
  const text=part==='title'?$('outputTitle').value:part==='body'?$('output').value+'\n\n'+$('tags').value:$('outputTitle').value+'\n\n'+$('output').value+'\n\n'+$('tags').value;
  try{await navigator.clipboard.writeText(text);toast('Скопировано');}catch{toast('Браузер не разрешил копирование. Выделите текст и скопируйте вручную.');}
}
$('copy').onclick=()=>copy('all');$('copyTitle').onclick=()=>copy('title');$('copyBody').onclick=()=>copy('body');
$('save').onclick=()=>{
  const value=snapshot();if(!value.name&&!value.metro&&!value.source&&!value.complex)return toast('Добавьте данные объекта');
  const object={...value,id:current||(globalThis.crypto?.randomUUID?.()||'object-'+Date.now().toString(36)+'-'+Math.random().toString(36).slice(2)),title:$('outputTitle').value,output:$('output').value,tags:$('tags').value,titles:currentTitles,updated:new Date().toISOString()};
  const next=[object,...objects.filter(o=>o.id!==object.id)];if(write('most-objects',next)){objects=next;current=object.id;saveDraft();toast('Объект сохранён вместе с заголовком и описанием');}
};
$('newObject').onclick=()=>{saveDraft();populate({});current=null;currentTitles=[];$('output').value='';$('outputTitle').value='';$('tags').value='';renderTitles();saveDraft();toast('Новая карточка. Объекты из библиотеки сохранены.');};
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
$('export').onclick=()=>download('cian-cn-rednote-backup.json',{version:2,objects,rules,prefs,wordCustom,draft:read('most-draft',null)});
$('restore').onclick=()=>$('file').click();
function validRecord(o){return o&&typeof o==='object'&&!Array.isArray(o)&&typeof o.id==='string'&&(!o.features||Array.isArray(o.features)&&o.features.every(x=>typeof x==='string'));}
$('file').onchange=async()=>{
  try{const file=$('file').files[0];if(!file)return;if(file.size>8000000)throw Error('size');const data=JSON.parse(await file.text());
    if(![1,2].includes(data.version)||!Array.isArray(data.objects)||!data.objects.every(validRecord)||!data.rules||Array.isArray(data.rules)||!Object.entries(data.rules).every(([k,v])=>k.trim()&&typeof v==='string')||typeof data.prefs?.benefits!=='string')throw Error('format');
    if(!confirm('Заменить библиотеку, словарь и настройки данными из резервной копии?'))return;
    if(!write('most-objects',data.objects)||!write('most-rules',data.rules)||!write('most-prefs',data.prefs))return;
    write('most-rules-version',2);if(Array.isArray(data.wordCustom))write('most-word-custom',data.wordCustom);if(data.draft)write('most-draft',data.draft);location.reload();
  }catch{toast('Не удалось импортировать файл: проверьте формат резервной копии (до 8 МБ).');}finally{$('file').value='';}
};
$('demo').onclick=()=>{
  populate({name:'Пример · Прокшино, 52 м²',complex:'Прокшино',area:'52',rooms:'2',floor:'11',floors:'17',price:'85000',deposit:'85000',kitchen:'11',occupants:'4',style:'detailed',layout:'classic',separate:true,metro:'Прокшино',walk:'12',
    transit:'Прокшино | 12 | walk | 1\nФилатов Луг | 19 | walk | 1\nСаларьево | 7 | bus | 1',
    universities:'МГУ | 35–50 | public\nРУДН | 30–45 | public',
    highlight:'52㎡宽敞空间＋独立厨房＋双人学习区',description:'两个独立房间，厨房与居住区域分开。窗边设有学习桌，方便在家学习或远程办公。',
    features:['sofa','desk','fridge','oven','stove','bath'],utilities:'meters',registration:'assist',support:true});
  current=null;generate();toast('Это демонстрационные данные, включая маршруты. Не реальное предложение.');
};
const draft=read('most-draft',null);
if(draft?.version===2){populate(draft.fields||{});current=draft.id||null;$('outputTitle').value=draft.title||'';$('output').value=draft.output||'';$('tags').value=draft.tags||'';currentTitles=Array.isArray(draft.titles)?draft.titles:[];renderTitles();}
else populate(draft||{});
wordEditor=setupWordEditor({getRules:()=>rules,getCustom:()=>wordCustom,saveCustom:value=>{wordCustom=value;write('most-word-custom',wordCustom);},onChange:saveDraft,toast});
renderTagCatalog();renderTagReasons(generateListing(snapshot(),rules,prefs).tagSuggestions);counts();

function renderPhotos(){const safe=[];for(const raw of photoUrls.slice(0,30)){try{const u=new URL(typeof raw==='string'?raw:raw.url);if(u.protocol==='https:'&&/(^|\.)(cian\.ru|cdn-cian\.ru|ciancdn\.ru|avito\.ru|avito\.st)$/i.test(u.hostname)&&!u.username&&!u.password)safe.push(u.href);}catch{}}photoUrls=safe;$('photoCard').classList.toggle('hidden',!safe.length);$('photoGallery').replaceChildren();for(const url of safe){const a=document.createElement('a'),img=document.createElement('img');a.href=url;a.target='_blank';a.rel='noopener noreferrer';img.src=url;img.loading='lazy';img.referrerPolicy='no-referrer';img.alt='Фото квартиры из источника';a.append(img);$('photoGallery').append(a);}}
$('importCli').onclick=()=>$('cliFile').click();
$('cliFile').onchange=async()=>{try{const file=$('cliFile').files[0];if(!file)return;if(file.size>8000000)throw Error('Файл больше 8 МБ');const value=JSON.parse(await file.text());if(!value.fields||typeof value.fields!=='object'||!Array.isArray(value.titles)||!value.titles.every(t=>typeof t==='string')||typeof value.body!=='string'||typeof value.tags!=='string')throw Error('Нужен JSON-результат Python CLI с fields/titles/body/tags');populate({...value.fields,photos:value.listing?.images||value.fields.photos||[]});current=null;currentTitles=value.titles;titleIndex=0;$('outputTitle').value=value.titles[0]||'';$('output').value=value.body;$('tags').value=value.tags;renderTitles();renderTagReasons(value.tagSuggestions);saveDraft();toast('Карточка из Python импортирована. Проверьте поля перед публикацией.');}catch(error){toast(error.message);}finally{$('cliFile').value='';}};
let roadmapDataClient=null;
async function loadRoadmap(){try{const response=await fetch('/api/roadmap',{cache:'no-store'});if(!response.ok)throw Error();const data=await response.json();if(!Array.isArray(data.tasks))throw Error();roadmapDataClient=data;renderRoadmap();}catch{$('roadmapSummary').textContent='План не загрузился. Исходник: plan.json и GitHub issue Roadmap.';}}
function renderRoadmap(){if(!roadmapDataClient)return;const tasks=roadmapDataClient.tasks;const done=tasks.filter(t=>t.status==='done').length;$('roadmapSummary').replaceChildren(document.createTextNode(done+' / '+tasks.length+' выполнено · '+(tasks.length-done)+' в планах. '));if(roadmapDataClient.github_issue_url){const a=document.createElement('a');a.textContent='Roadmap на GitHub ↗';a.href=roadmapDataClient.github_issue_url;a.target='_blank';a.rel='noopener noreferrer';$('roadmapSummary').append(a);}const q=$('roadmapSearch').value.toLowerCase(),mode=$('roadmapFilter').value;$('roadmapList').replaceChildren();for(const task of tasks.filter(t=>(mode==='all'||t.status===mode)&&(t.id+' '+t.section+' '+t.title+' '+t.acceptance).toLowerCase().includes(q))){const card=document.createElement('article'),head=document.createElement('header'),p=document.createElement('p'),evidence=document.createElement('div');card.className='task '+task.status;head.textContent=(task.status==='done'?'✓ ':'○ ')+task.id+' · '+task.title;p.textContent=task.acceptance;evidence.className='evidence';evidence.textContent=task.priority+' · '+task.section+(task.evidence.length?' · '+task.evidence.join(' · '):' · Проверка ещё не завершена');card.append(head,p,evidence);$('roadmapList').append(card);}}
$('roadmapSearch').oninput=renderRoadmap;$('roadmapFilter').onchange=renderRoadmap;
