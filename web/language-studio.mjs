import {getTemplate, catalogStats, CATALOG_SIZE, nextTemplateIndex} from './template-catalog.mjs';

// This panel keeps only the current dictionary page and a private, unsaved draft.
export function setupLanguageStudio({local=false,api,getRules=()=>({}),onApplyText,onApplyTemplate,onUseRule,toast=()=>{},translateLocal,capabilities,searchLocal,catalogBaseUrl='../data/template-catalog/'}={}){
  const root=document.querySelector('#rules .heading');
  const toggle=document.getElementById('wordToggle');
  if(!root||!toggle)return {openDictionary:()=>{},clearDraft:()=>{},refreshStats:()=>Promise.resolve(),refreshLocalStats:()=>{}};
  const node=(tag,text='',attrs={})=>{const el=document.createElement(tag);if(text)el.textContent=text;for(const [key,value] of Object.entries(attrs))el.setAttribute(key,String(value));return el;};
  const label=(text,id)=>node('label',text,{for:id});
  const button=(text,id,primary=false)=>node('button',text,{type:'button',id,class:'btn'+(primary?' primary':'')});
  const select=(id,options)=>{const el=node('select','',{id});for(const [value,text] of options)el.append(node('option',text,{value}));return el;};
  const number=value=>Number.isFinite(Number(value))?new Intl.NumberFormat('ru-RU').format(Number(value)):'—';
  const safeError=error=>String(error?.message||error||'Не удалось выполнить запрос').slice(0,500);
  const studio=node('section','',{class:'card language-studio',id:'languageKnowledge','aria-label':'Полная языковая база'});
  studio.append(node('h2','Языковая база и шаблоны'),node('p','Ваши правила ниже — личные точные замены. Полная база ищется здесь; все записи не загружаются в список.',{class:'muted'}));
  const stats=node('div','',{id:'languageStats',class:'language-stats','aria-live':'polite'});
  const refresh=button('Обновить показатели','languageRefresh');
  studio.append(stats,refresh);
  const searchRow=node('div','',{class:'language-grid'}),query=node('input','',{id:'dictionarySearch',type:'search',maxlength:200,placeholder:'Например: духовка, снять квартиру, 地铁'});
  const scope=select('dictionaryScope',[['all','Вся база'],['open','Открытый словарь'],['rental','Аренда и быт'],['custom','Мои правила'],['geo','География Москвы']]);
  const qwrap=node('div'),swrap=node('div');qwrap.append(label('Поиск RU / 中文','dictionarySearch'),query);swrap.append(label('Раздел','dictionaryScope'),scope);searchRow.append(qwrap,swrap);
  const searchStatus=node('p','Введите слово или фразу. На странице не больше 40 результатов.',{id:'dictionaryStatus',class:'muted',role:'status'});
  const results=node('div','',{id:'dictionaryResults',class:'language-results'});
  const pager=node('div','',{class:'language-actions'}),prev=button('← Назад','dictionaryPrev'),next=button('Далее →','dictionaryNext');prev.disabled=true;next.disabled=true;pager.append(prev,next);
  studio.append(searchRow,searchStatus,results,pager);
  const catalog=node('details','',{class:'language-catalog'});catalog.append(node('summary','Каталог готовых схем · заголовки, описание, теги'));
  catalog.append(node('p','По 262 144 индексируемые композиции каждого типа. Они собраны из языковых блоков; это число схем, а не слов, отдельных хештегов или вручную написанных объявлений. Поля заполняются подтверждёнными фактами карточки.',{class:'muted'}));
  const catalogRow=node('div','',{class:'language-grid'}),kind=select('catalogKind',[['title','Заголовок'],['description','Описание'],['tags','Набор тегов']]),index=node('input','',{id:'catalogIndex',type:'number',min:0,max:CATALOG_SIZE-1,step:1,value:0,inputmode:'numeric'});
  const kwrap=node('div'),iwrap=node('div');kwrap.append(label('Тип схемы','catalogKind'),kind);iwrap.append(label('Номер схемы · от 0','catalogIndex'),index);catalogRow.append(kwrap,iwrap);
  const preview=node('pre','',{id:'catalogPreview',class:'language-template-preview','aria-live':'polite'}),catalogId=node('p','',{class:'muted',id:'catalogIdentity'});
  const catalogActions=node('div','',{class:'language-actions'}),catalogNext=button('↻ Следующая схема','catalogNext'),catalogApply=button('Применить к карточке','catalogApply',true),download=node('a','Скачать полный каталог · NDJSON.gz',{id:'catalogDownload',class:'btn',download:''});
  catalogActions.append(catalogNext,catalogApply,download);catalog.append(catalogRow,catalogId,preview,catalogActions);studio.append(catalog);
  root.after(studio);

  const composer=node('details','',{id:'languageComposer',class:'language-studio language-composer'});composer.append(node('summary','RU → 中文 · написать свой текст'));
  composer.append(node('p','Напишите свой вариант по-русски, получите китайский черновик и проверьте его перед применением. Текст не заменяет карточку до нажатия «Применить».',{class:'muted'}));
  const part=select('languagePart',[['body','Описание'],['title','Заголовок'],['tags','Теги']]),russian=node('textarea','',{id:'languageRussianInput',rows:5,maxlength:20000,placeholder:'Ваш текст по-русски · до 20 000 знаков'}),chinese=node('textarea','',{id:'languageChineseOutput',rows:6,maxlength:20000,placeholder:'Китайский черновик можно отредактировать вручную'}),translate=button('Перевести в китайский','languageTranslate',true),apply=button('Применить описание','languageApply',true),review=node('p','Черновик остаётся только в этом редакторе до применения.',{id:'languageReview',class:'language-review',role:'status'});
  apply.disabled=true;
  composer.append(label('Куда применить текст','languagePart'),part,label('Русский оригинал','languageRussianInput'),russian,translate,label('Китайский вариант · редактируемый','languageChineseOutput'),chinese,review,apply);toggle.before(composer);

  let loadedCapabilities=null,statsRequest=null,statsEpoch=0,searchEpoch=0,searchTimer=null,searchOffset=0,searchTotal=0,searchBusy=false;
  let draftEpoch=0,draftBusy=false,draftDiagnostics=null,applyBusy=false,downloadBusy=false;
  const available=()=>typeof capabilities==='function'?capabilities():capabilities;
  function renderStats(){
    const value=loadedCapabilities||available()||{},d=value.dictionary||{},fallback=catalogStats(),t=value.templates||fallback;
    const pairs=d.ruZhPairs??d.entries,forms=d.openWordForms??d.grammaticalForms,curated=d.curatedRules??value.curatedRules??d.curatedEntries;
    const rows=[['Пар RU → ZH',pairs],['Словоформ',forms],['Правил основного словаря',curated],['Моих правил',Object.keys(getRules()||{}).length]];
    stats.replaceChildren();
    for(const [title,value] of rows){const item=node('div','',{class:'language-stat'});item.append(node('strong',number(value)),node('span',title));stats.append(item);}
    const composition=node('p',`Схемы: заголовки ${number(t.titles??t.title?.count??fallback.titles)} · описания ${number(t.descriptions??t.body?.count??t.description?.count??fallback.descriptions)} · наборы тегов ${number(t.tagSets??t.tags?.count??fallback.tagSets)}.`,{class:'language-composition-count'});
    stats.append(composition,node('p',`${number(t.baseLanguageBlocks??128)} языковых блоков и ${number(t.genericTagAtoms??48)} общих тегов составляют каталог. Число разных результатов для конкретной квартиры зависит от заполненных полей.`,{class:'muted'}));
    if(local&&!loadedCapabilities)stats.append(node('p','Полные показатели словаря загружаются при открытии раздела.',{class:'muted'}));
  }
  async function refreshStats(){
    renderStats();if(!local||typeof api!=='function')return;
    if(statsRequest)return statsRequest;
    const epoch=++statsEpoch;refresh.disabled=true;
    statsRequest=(async()=>{try{const data=await api('language');if(epoch===statsEpoch){loadedCapabilities=data;renderStats();}}catch(error){if(epoch===statsEpoch)toast(safeError(error));}finally{if(epoch===statsEpoch){statsRequest=null;refresh.disabled=false;}}})();
    return statsRequest;
  }
  refresh.addEventListener('click',()=>{loadedCapabilities=null;refreshStats();});
  function updatePaging(){prev.disabled=searchBusy||searchOffset===0;next.disabled=searchBusy||searchOffset+40>=searchTotal;}
  function renderSearchRows(rows){
    results.replaceChildren();
    for(const entry of rows.slice(0,40)){
      const row=node('article','',{class:'language-result'}),ru=node('strong',String(entry.ru??entry.russian??'')),zh=node('p',String(entry.zh??entry.chinese??''),{lang:'zh-CN'});
      const source=String(entry.sourceId??entry.source??entry.category??'словарь');
      const meta=node('p',source+' · '+(entry.reviewed===true?'проверенная формулировка':'нужна проверка контекста'),{class:'muted'});
      row.append(ru,zh,meta);
      if(typeof onUseRule==='function'&&ru.textContent&&zh.textContent){const use=button('В мои правила','');use.removeAttribute('id');use.addEventListener('click',async()=>{use.disabled=true;try{await onUseRule(ru.textContent,zh.textContent);renderStats();toast('Точная формулировка добавлена в ваши правила');}catch(error){toast(safeError(error));}finally{use.disabled=false;}});row.append(use);}
      results.append(row);
    }
  }
  async function runSearch(offset=0){
    const term=query.value.trim(),epoch=++searchEpoch;searchOffset=offset;searchBusy=false;
    if(!term){results.replaceChildren();searchTotal=0;searchOffset=0;searchStatus.textContent='Введите слово или фразу. На странице не больше 40 результатов.';updatePaging();return;}
    searchBusy=true;updatePaging();searchStatus.textContent='Ищем в выбранном разделе…';
    try{
      const request={query:term,scope:scope.value,offset,limit:40};
      const data=local&&typeof api==='function'?await api('language/search',request):typeof searchLocal==='function'?await searchLocal(term,{scope:scope.value,offset,limit:40}):[];
      if(epoch!==searchEpoch)return;
      const rows=Array.isArray(data)?data:(data?.entries||data?.rows||data?.items||data?.results||[]);
      // Static callers may return a complete result array; slice it here as well.
      const page=Array.isArray(data)&&rows.length>40?rows.slice(offset,offset+40):rows.slice(0,40);
      searchTotal=Number.isFinite(data?.total)?data.total:Array.isArray(data)?rows.length:offset+page.length+(data?.hasMore?1:0);
      renderSearchRows(page);
      searchStatus.textContent=page.length?`Найдено: ${number(searchTotal)} · ${number(offset+1)}–${number(offset+page.length)}. Открытый словарь и заимствованные названия требуют проверки в контексте.`:'Совпадений нет. Можно добавить точную замену в свои правила ниже.';
    }catch(error){if(epoch===searchEpoch){results.replaceChildren();searchTotal=0;searchStatus.textContent=safeError(error);}}
    finally{if(epoch===searchEpoch){searchBusy=false;updatePaging();}}
  }
  function scheduleSearch(){clearTimeout(searchTimer);++searchEpoch;searchBusy=false;searchOffset=0;searchTotal=0;updatePaging();searchTimer=setTimeout(()=>runSearch(0),400);}
  query.addEventListener('input',scheduleSearch);scope.addEventListener('change',()=>{clearTimeout(searchTimer);runSearch(0);});
  prev.addEventListener('click',()=>runSearch(Math.max(0,searchOffset-40)));next.addEventListener('click',()=>runSearch(searchOffset+40));
  function updateCatalog(){
    const parsed=Number(index.value),ordinal=Number.isFinite(parsed)?Math.min(CATALOG_SIZE-1,Math.max(0,Math.trunc(parsed))):0;
    index.value=String(ordinal);const template=getTemplate(kind.value,ordinal);preview.textContent=template.skeleton;catalogId.textContent=template.id+' · поля в {скобках} заполняются из карточки';
    download.href=local?'/api/local/language/catalog/'+kind.value:new URL(kind.value+'.ndjson.gz',new URL(catalogBaseUrl,location.href)).href;download.download=kind.value+'.ndjson.gz';
  }
  kind.addEventListener('change',updateCatalog);index.addEventListener('change',updateCatalog);
  catalogNext.addEventListener('click',()=>{index.value=String(nextTemplateIndex(Number(index.value)||0));updateCatalog();});
  catalogApply.addEventListener('click',async()=>{updateCatalog();catalogApply.disabled=true;try{if(typeof onApplyTemplate!=='function')throw Error('Применение шаблона недоступно');await onApplyTemplate(kind.value,Number(index.value));toast('Схема применена к подтверждённым полям карточки');}catch(error){toast(safeError(error));}finally{catalogApply.disabled=false;}});
  download.addEventListener('click',async event=>{
    if(!local)return;event.preventDefault();if(downloadBusy)return;downloadBusy=true;download.setAttribute('aria-busy','true');
    const archiveUrl=download.getAttribute('href'),archiveName=kind.value+'.ndjson.gz';
    try{const response=await fetch(archiveUrl,{headers:{'X-Redcn-Token':document.querySelector('meta[name="redcn-token"]')?.content||''},cache:'no-store'});if(!response.ok){const error=await response.json().catch(()=>({}));throw Error(error.error||'Каталог недоступен');}const url=URL.createObjectURL(await response.blob()),anchor=node('a','',{href:url,download:archiveName});document.body.append(anchor);anchor.click();anchor.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(error){toast(safeError(error));}finally{downloadBusy=false;download.removeAttribute('aria-busy');}
  });
  const partNames={title:'заголовок',body:'описание',tags:'теги'};
  function updateDraftButtons(){translate.disabled=draftBusy||applyBusy||!russian.value.trim();apply.disabled=draftBusy||applyBusy||!chinese.value.trim();apply.textContent='Применить '+partNames[part.value];translate.textContent=draftBusy?'Переводим…':'Перевести в китайский';}
  function invalidateDraft(){++draftEpoch;draftBusy=false;draftDiagnostics=null;updateDraftButtons();}
  russian.addEventListener('input',()=>{invalidateDraft();review.textContent='Русский оригинал изменён. Переведите его снова или отредактируйте китайский текст вручную.';});
  part.addEventListener('change',()=>{invalidateDraft();review.textContent='Выбран раздел: '+partNames[part.value]+'. Применение заменит только этот текст.';});
  chinese.addEventListener('input',()=>{invalidateDraft();draftDiagnostics={needsReview:true,manualEdit:true,warnings:['Китайский черновик изменён вручную']};review.textContent='Китайский вариант отредактирован вручную. Проверьте смысл, числа и условия перед применением.';});
  translate.addEventListener('click',async()=>{
    const source=russian.value;if(!source.trim())return;if(source.length>20000){review.textContent='Русский текст: не больше 20 000 знаков.';return;}
    const epoch=++draftEpoch,target=part.value;draftBusy=true;updateDraftButtons();review.textContent='Переводим текст. Китайский результат нужно проверить перед публикацией.';
    try{
      const data=local&&typeof api==='function'?await api('translate',{text:source,part:target}):typeof translateLocal==='function'?await translateLocal(source,{rules:getRules()||{},part:target,mode:'draft'}):null;
      if(epoch!==draftEpoch)return;if(!data||typeof data.text!=='string')throw Error('Переводчик не вернул текст');
      chinese.value=data.text;draftDiagnostics={...data};delete draftDiagnostics.text;
      const unknown=Array.isArray(data.unknown)?data.unknown:[],warnings=Array.isArray(data.warnings)?data.warnings:[],unknownCount=Number.isFinite(data.unknownCount)?data.unknownCount:unknown.length;
      const backend=data.backend==='rental-phrases'?'Словарный черновик':'Машинный черновик';
      review.textContent=`${backend} · непереведённых фрагментов: ${number(unknownCount)}. Проверьте формулировки, числа и условия.`+(unknown.length?' Требуют внимания: '+unknown.slice(0,12).map(value=>typeof value==='string'?value:value.text||value.word||'фрагмент').join(', ')+'.':'')+(warnings.length?' '+warnings.slice(0,4).map(String).join(' · '):'');
    }catch(error){if(epoch===draftEpoch)review.textContent=safeError(error);}
    finally{if(epoch===draftEpoch){draftBusy=false;updateDraftButtons();}}
  });
  apply.addEventListener('click',async()=>{
    const text=chinese.value;if(!text.trim())return;if(text.length>20000){review.textContent='Китайский текст: не больше 20 000 знаков. Сократите черновик перед применением.';return;}const epoch=draftEpoch;applyBusy=true;updateDraftButtons();
    try{if(typeof onApplyText!=='function')throw Error('Применение текста недоступно');await onApplyText({part:part.value,text,sourceRu:russian.value,diagnostics:draftDiagnostics||{needsReview:true,manualEdit:true}});if(epoch===draftEpoch){review.textContent='Применено: '+partNames[part.value]+'. Проверьте результат в карточке.';toast('Китайский текст применён');}}catch(error){if(epoch===draftEpoch)review.textContent=safeError(error);}finally{applyBusy=false;updateDraftButtons();}
  });
  function clearDraft(){++draftEpoch;draftBusy=false;applyBusy=false;draftDiagnostics=null;russian.value='';chinese.value='';review.textContent='Черновик остаётся только в этом редакторе до применения.';updateDraftButtons();}
  function openDictionary(){if(!loadedCapabilities)refreshStats();else renderStats();if(query.value.trim())runSearch(0);}
  renderStats();updateCatalog();updateDraftButtons();
  return {openDictionary,clearDraft,refreshStats,refreshLocalStats:renderStats};
}
