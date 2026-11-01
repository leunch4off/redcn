import {containsClientCommission,sanitizeClientText} from './client-publication.mjs';

export const TITLE_FORMULA_VARIABLES=Object.freeze(['city','layout','area','price','metro','floor','feature','university','code']);
const variableSet=new Set(TITLE_FORMULA_VARIABLES);
const keys={history:'most-title-history-v1',formulas:'most-title-formulas-v1',profiles:'most-studio-profiles-v1',selection:'most-content-selection-v1'};
const enums={punctuation:['standard','compact','fullwidth'],emoji:['source','none'],addressing:['neutral','polite','friendly']};
export const DEFAULT_STUDIO_PROFILE=Object.freeze({id:'default',name:'Обычный',punctuation:'standard',emoji:'source',addressing:'neutral'});

export function validateTitleFormula(formula){
 const problems=[],variables=[];
 if(typeof formula!=='string')return {valid:false,problems:['Формула должна быть текстом'],variables};
 if(!formula.trim()||formula.length>500)problems.push('Формула должна содержать от 1 до 500 знаков');
 if(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/u.test(formula))problems.push('В формуле есть управляющие символы');
 if(containsClientCommission(formula))problems.push('Комиссия не включается в заголовок для клиента');
 const matches=[...formula.matchAll(/\{([a-zA-Z][a-zA-Z0-9]*)\}/gu)];
 for(const match of matches){if(!variableSet.has(match[1]))problems.push('Неизвестная переменная: '+match[1]);else if(!variables.includes(match[1]))variables.push(match[1]);}
 if(/[{}]/u.test(formula.replace(/\{([a-zA-Z][a-zA-Z0-9]*)\}/gu,'')))problems.push('Используйте подстановку в виде {price} без вложенных скобок');
 if(!matches.length)problems.push('Добавьте хотя бы одну переменную текущего объявления');
 if(matches.length>20)problems.push('Допускается до 20 подстановок');
 return {valid:!problems.length,problems:[...new Set(problems)],variables};
}

export function renderTitleFormula(formula,context={}){
 const validation=validateTitleFormula(formula);if(!validation.valid)throw Error(validation.problems.join(' · '));
 const values=Object.create(null);
 for(const key of TITLE_FORMULA_VARIABLES){
  const value=context&&Object.hasOwn(context,key)?context[key]:'';
  if(value!=null&&typeof value!=='string'&&typeof value!=='number'||typeof value==='number'&&!Number.isFinite(value))throw TypeError('Неверное значение подстановки '+key);
  const text=value==null?'':String(value).trim();
  if(containsClientCommission(text))throw Error('Комиссия не включается в заголовок для клиента');
  values[key]=text;
 }
 // Empty facts disappear; separators cannot turn an unknown value into a claim.
 // No evaluation, HTML parsing, property traversal or recursive interpolation.
 const rendered=formula.replace(/\{([a-zA-Z][a-zA-Z0-9]*)\}/gu,(_,key)=>values[key]);
 const cleaned=rendered.split(/\s*[|｜·•]\s*/u).map(part=>part.trim()).filter(Boolean).join(formula.includes('｜')?'｜':formula.includes('·')?' · ':' | ')
  .replace(/\s+/gu,' ').trim();
 if(!validation.variables.some(key=>values[key]))return '';
 return sanitizeClientText(cleaned);
}

export function normalizeStudioProfile(value={}){
 if(!value||typeof value!=='object'||Array.isArray(value))throw TypeError('Нужен профиль стиля');
 const profile={id:String(value.id||'default'),name:String(value.name||'Обычный').trim(),punctuation:value.punctuation||'standard',emoji:value.emoji||'source',addressing:value.addressing||'neutral'};
 if(!/^[a-zA-Z0-9_-]{1,80}$/u.test(profile.id))throw Error('Неверный ID профиля');
 if(!profile.name||profile.name.length>80||/[\u0000-\u001f\u007f]/u.test(profile.name))throw Error('Название профиля должно содержать от 1 до 80 знаков');
 for(const [key,options] of Object.entries(enums))if(!options.includes(profile[key]))throw Error('Неизвестная настройка стиля: '+key);
 return profile;
}

export function titleHistoryKey(fields={}){
 const source=typeof fields.url==='string'?fields.url.trim():'';
 if(source)try{const url=new URL(source);if(['https:','http:'].includes(url.protocol))return 'url:'+url.origin+url.pathname.replace(/\/+$/u,'');}catch{}
 if(typeof fields.resultId==='string'&&fields.resultId)return 'card:'+fields.resultId.slice(0,100);
 if(typeof fields.editorIdentity==='string'&&/^[a-zA-Z0-9_-]{1,100}$/u.test(fields.editorIdentity))return 'editor:'+fields.editorIdentity;
 if(typeof fields.name==='string'&&fields.name.trim())return 'name:'+fields.name.trim().slice(0,200);
 return 'draft';
}

export function recordTitleHistory(history,key,title,kind='selected',at=new Date().toISOString()){
 if(typeof title!=='string'||!title.trim())return history&&typeof history==='object'&&!Array.isArray(history)?history:{};
 title=sanitizeClientText(title).trim();if(!title)return history||{};
 if(title.length>2000)throw Error('Заголовок слишком длинный для истории');
 const valid=history&&typeof history==='object'&&!Array.isArray(history)?history:{};
 const previous=Array.isArray(valid[key])?valid[key].filter(row=>row&&typeof row.title==='string'&&typeof row.at==='string'):[];
 const row={title,kind:['selected','manual','generated','restored','formula'].includes(kind)?kind:'selected',at};
 return Object.fromEntries([...Object.entries(valid).filter(([name])=>name!==key).slice(-39),[key,[row,...previous.filter(item=>item.title!==title)].slice(0,50)]]);
}

function diffTokens(value){
 const text=String(value??'');
 // Clauses preserve Chinese words, numbers, spaces and punctuation exactly.
 return text.match(/[^\n。！？!?；;]+[。！？!?；;]?|\n/gu)||[];
}
export function textDiff(before,after){
 before=String(before??'');after=String(after??'');
 if(before===after)return before?[{kind:'same',text:before}]:[];
 const a=diffTokens(before),b=diffTokens(after);
 if(a.length>300||b.length>300)return [{kind:'removed',text:before},{kind:'added',text:after}].filter(row=>row.text);
 const table=Array.from({length:a.length+1},()=>new Uint16Array(b.length+1));
 for(let i=a.length-1;i>=0;i--)for(let j=b.length-1;j>=0;j--)table[i][j]=a[i]===b[j]?table[i+1][j+1]+1:Math.max(table[i+1][j],table[i][j+1]);
 const result=[];let i=0,j=0;
 const push=(kind,text)=>{const last=result.at(-1);if(last?.kind===kind)last.text+=text;else result.push({kind,text});};
 while(i<a.length||j<b.length){if(i<a.length&&j<b.length&&a[i]===b[j]){push('same',a[i++]);j++;}else if(j<b.length&&(i===a.length||table[i][j+1]>=table[i+1][j]))push('added',b[j++]);else push('removed',a[i++]);}
 return result;
}
export function publicationTextDiff(before={},after={}){
 return ['title','body','tags'].map(key=>({key,before:String(before[key]??''),after:String(after[key]??''),changes:textDiff(before[key],after[key])})).filter(row=>row.before!==row.after);
}

const clone=value=>JSON.parse(JSON.stringify(value));
function safeSelection(value){return value&&typeof value==='object'&&!Array.isArray(value)?{profile:String(value.profile||'default'),formula:String(value.formula||'')}: {profile:'default',formula:''};}
function safeProfiles(rows){const result=[{...DEFAULT_STUDIO_PROFILE}];for(const row of Array.isArray(rows)?rows:[])try{const normalized=normalizeStudioProfile(row);if(normalized.id!=='default'&&!result.some(item=>item.id===normalized.id))result.push(normalized);}catch{}return result.slice(0,31);}
function safeFormulas(rows){return (Array.isArray(rows)?rows:[]).filter(row=>row&&typeof row.id==='string'&&/^[a-zA-Z0-9_-]{1,80}$/u.test(row.id)&&typeof row.name==='string'&&row.name.length<=80&&validateTitleFormula(row.formula).valid).map(row=>({id:row.id,name:row.name,formula:row.formula})).slice(0,30);}
function safeOutput(value={}){return {title:sanitizeClientText(String(value.title??'')),body:sanitizeClientText(String(value.body??'')),tags:sanitizeClientText(String(value.tags??''))};}

export function setupContentStudio({mount,read,write,getFields,getOutput,setOutput,toast=()=>{},onProfileChange=()=>{},previewTitle}={}){
 if(!mount)return null;
 const make=(tag,text,options={})=>{const node=document.createElement(tag);if(text!==undefined)node.textContent=text;Object.assign(node,options);return node;};
 const notify=message=>{status.textContent=message;toast(message);};
 let history=read(keys.history,{}),profiles=safeProfiles(read(keys.profiles,[])),formulas=safeFormulas(read(keys.formulas,[])),selection=safeSelection(read(keys.selection,{})),pending=null;
 let baseline={...getOutput()},manual=new Set();
 const details=make('details'),summary=make('summary','Заголовки, сохранённые стили и проверка правок'),body=make('div',undefined,{className:'content-studio-body'});
 mount.classList.add('content-studio');mount.replaceChildren(details);details.append(summary,body);
 const status=make('p','Настройки применяются при следующей генерации.',{className:'muted'});status.setAttribute('role','status');
 const section=(title)=>{const block=make('section'),heading=make('h3',title);block.append(heading);body.append(block);return block;};
 const label=(block,text,node)=>{const wrapper=make('label',text);node.setAttribute('aria-label',text);wrapper.append(node);block.append(wrapper);return node;};
 const button=(block,text,action)=>{const node=make('button',text,{type:'button',className:'btn'});node.onclick=action;block.append(node);return node;};
 const select=(block,title,rows)=>{const node=label(block,title,make('select'));for(const [value,text] of rows)node.append(make('option',text,{value}));return node;};
 const profileSection=section('Профили оформления текста');
 const profileSelect=select(profileSection,'Сохранённый стиль',[]),profileName=label(profileSection,'Название своего стиля',make('input',undefined,{type:'text',maxLength:80,placeholder:'Например, лаконично и без эмодзи'}));
 const punctuation=select(profileSection,'Пунктуация',[['standard','Обычная'],['compact','Меньше пустых строк'],['fullwidth','Китайская пунктуация']]);
 const emoji=select(profileSection,'Эмодзи',[['source','Сохранить'],['none','Без декоративных эмодзи']]);
 const addressing=select(profileSection,'Обращение',[['neutral','Нейтральное'],['polite','Вежливое 您'],['friendly','Дружелюбное 你']]);
 const uid=prefix=>prefix+'-'+(globalThis.crypto?.randomUUID?.()||Date.now().toString(36)+'-'+Math.random().toString(36).slice(2));
 const commitSelection=()=>{if(write(keys.selection,selection)!==false){onProfileChange(settings());return true;}return false;};
 function renderProfiles(){profileSelect.replaceChildren();for(const row of profiles)profileSelect.append(make('option',row.name,{value:row.id}));if(!profiles.some(row=>row.id===selection.profile))selection.profile='default';profileSelect.value=selection.profile;const row=profiles.find(row=>row.id===selection.profile);profileName.value=row.id==='default'?'':row.name;punctuation.value=row.punctuation;emoji.value=row.emoji;addressing.value=row.addressing;}
 profileSelect.onchange=()=>{selection.profile=profileSelect.value;renderProfiles();commitSelection();};
 button(profileSection,'Сохранить стиль',()=>{try{const old=profiles.find(row=>row.id===selection.profile),profile=normalizeStudioProfile({id:old?.id==='default'?uid('style'):old.id,name:profileName.value,punctuation:punctuation.value,emoji:emoji.value,addressing:addressing.value});const next=[...profiles.filter(row=>row.id!==profile.id),profile];if(next.length>31)throw Error('Допускается до 30 своих стилей');if(write(keys.profiles,next.filter(row=>row.id!=='default'))===false)return;profiles=next;selection.profile=profile.id;renderProfiles();commitSelection();notify('Стиль сохранён. Факты объявления сохраняются.');}catch(error){notify(error.message);}});
 button(profileSection,'Удалить свой стиль',()=>{if(selection.profile==='default')return notify('Обычный стиль остаётся доступным');const next=profiles.filter(row=>row.id!==selection.profile);if(write(keys.profiles,next.filter(row=>row.id!=='default'))===false)return;profiles=next;selection.profile='default';renderProfiles();commitSelection();});
 const formulaSection=section('Своя формула заголовка');
 const formulaSelect=select(formulaSection,'Формула',[['','Автоматические заголовки']]),formulaName=label(formulaSection,'Название формулы',make('input',undefined,{type:'text',maxLength:80})),formulaText=label(formulaSection,'Шаблон с проверяемыми полями',make('textarea',undefined,{rows:3,maxLength:500,placeholder:'{city}｜{layout}｜{area}｜{price}'}));
 formulaSection.append(make('p','Поля: '+TITLE_FORMULA_VARIABLES.map(key=>'{'+key+'}').join(' ')+' . Неизвестные факты пропускаются; новые характеристики формула не подтверждает.',{className:'muted'}));
 const formulaPreview=make('output',undefined,{className:'content-studio-preview'});formulaSection.append(formulaPreview);
 function renderFormulas(){formulaSelect.replaceChildren(make('option','Автоматические заголовки',{value:''}));for(const row of formulas)formulaSelect.append(make('option',row.name,{value:row.id}));const row=formulas.find(row=>row.id===selection.formula);if(!row)selection.formula='';formulaSelect.value=selection.formula;formulaName.value=row?.name||'';formulaText.value=row?.formula||'';formulaPreview.textContent='';}
 formulaSelect.onchange=()=>{selection.formula=formulaSelect.value;renderFormulas();commitSelection();};
 function checkFormula(){const validation=validateTitleFormula(formulaText.value);if(!validation.valid)throw Error(validation.problems.join(' · '));const value=previewTitle?previewTitle(formulaText.value):renderTitleFormula(formulaText.value,{});if(typeof value!=='string')throw Error('Не удалось проверить заголовок');formulaPreview.textContent=value||'Нет известных полей для этой формулы';return value;}
 button(formulaSection,'Предпросмотр',()=>{try{checkFormula();}catch(error){notify(error.message);}});
 button(formulaSection,'Сохранить формулу',()=>{try{checkFormula();const name=formulaName.value.trim();if(!name||name.length>80)throw Error('Укажите название формулы до 80 знаков');const row={id:selection.formula||uid('formula'),name,formula:formulaText.value};const next=[...formulas.filter(item=>item.id!==row.id),row];if(next.length>30)throw Error('Допускается до 30 формул');if(write(keys.formulas,next)===false)return;formulas=next;selection.formula=row.id;renderFormulas();commitSelection();notify('Формула сохранена для будущих заголовков');}catch(error){notify(error.message);}});
 button(formulaSection,'Удалить формулу',()=>{const next=formulas.filter(row=>row.id!==selection.formula);if(write(keys.formulas,next)===false)return;formulas=next;selection.formula='';renderFormulas();commitSelection();});
 const historySection=section('История выбранных заголовков');
 historySection.append(make('p','История привязана к текущему объявлению. Восстановление меняет только заголовок.',{className:'muted'}));
 const historySelect=select(historySection,'Предыдущая версия',[]),historyPreview=make('div',undefined,{className:'content-studio-diff'});historySection.append(historyPreview);
 function appendDiff(parent,changes){for(const row of changes){const node=make(row.kind==='added'?'ins':row.kind==='removed'?'del':'span',row.text);parent.append(node);}}
 function showHistory(){historyPreview.replaceChildren();const row=historyRows().find(item=>item.at+'\n'+item.title===historySelect.value);if(!row)return;appendDiff(historyPreview,textDiff(getOutput()?.title||'',row.title));}
 historySelect.onchange=showHistory;
 function historyRows(){const rows=history?.[titleHistoryKey(getFields())];return Array.isArray(rows)?rows.filter(row=>row&&typeof row.title==='string'&&typeof row.at==='string').slice(0,50):[];}
 function renderHistory(){historySelect.replaceChildren();for(const row of historyRows())historySelect.append(make('option',new Date(row.at).toLocaleString('ru-RU')+' · '+row.title,{value:row.at+'\n'+row.title}));showHistory();}
 button(historySection,'Запомнить текущий заголовок',()=>{recordTitle(getOutput()?.title,'manual');notify('Текущий заголовок сохранён в истории');});
 button(historySection,'Восстановить выбранный заголовок',()=>{const row=historyRows().find(item=>item.at+'\n'+item.title===historySelect.value);if(!row)return notify('Выберите сохранённый заголовок');try{const title=sanitizeClientText(row.title);if(!title)throw Error('В версии нет текста для публикации');recordTitle(getOutput()?.title,'manual');setOutput({...getOutput(),title});recordTitle(title,'restored');notify('Заголовок восстановлен; описание и теги сохранены');}catch(error){notify(error.message);}});
 const review=section('Изменения перед перегенерацией');review.hidden=true;review.setAttribute('aria-live','polite');
 const diff=make('div',undefined,{className:'content-studio-diff'});review.append(make('p','Красным показан удаляемый текст, зелёным — новый. Примените результат или сохраните текущую редакцию.'),diff);
 button(review,'Применить новую версию',async()=>{if(!pending)return;const task=pending;if(JSON.stringify(getOutput())!==task.outputFingerprint||JSON.stringify(getFields())!==task.fieldsFingerprint){pending=null;review.hidden=true;return notify('Поля или текст изменились после предпросмотра. Запустите генерацию ещё раз.');}pending=null;review.hidden=true;try{recordTitle(getOutput()?.title,'manual');await task.commit(task.candidate);markCommitted(getOutput(),task.keys);recordTitle(task.candidate.title,'generated');}catch(error){notify(error.message);}});
 button(review,'Оставить текущий текст',()=>{pending=null;review.hidden=true;notify('Текущая редакция сохранена');});
 body.append(status);
 function settings(){return {studioProfile:clone(profiles.find(row=>row.id===selection.profile)||DEFAULT_STUDIO_PROFILE),titleFormula:formulas.find(row=>row.id===selection.formula)?.formula||''};}
 function recordTitle(title,kind='selected'){try{const next=recordTitleHistory(history,titleHistoryKey(getFields()),title,kind);if(write(keys.history,next)===false)return false;history=next;renderHistory();return true;}catch(error){notify(error.message);return false;}}
 function requestRegeneration(candidate,commit){
  try{const before=safeOutput(getOutput()),next=safeOutput({...before,...candidate}),changes=publicationTextDiff(before,next);
   const affected=changes.some(row=>manual.has(row.key)||row.before!==String(baseline[row.key]??''));
   const committedKeys=['title','body','tags'].filter(key=>Object.hasOwn(candidate,key));
   if(!changes.length||!affected||!Object.values(before).some(value=>value.trim())){commit(candidate);markCommitted(getOutput(),committedKeys);recordTitle(next.title,'generated');return true;}
   pending={candidate:{...candidate,...next},keys:committedKeys,commit,outputFingerprint:JSON.stringify(getOutput()),fieldsFingerprint:JSON.stringify(getFields())};diff.replaceChildren();
   const labels={title:'Заголовок',body:'Описание',tags:'Теги'};
   for(const row of changes){const block=make('section'),heading=make('h4',labels[row.key]),text=make('div');appendDiff(text,row.changes);block.append(heading,text);diff.append(block);}
   review.hidden=false;details.open=true;review.scrollIntoView?.({block:'nearest',behavior:'smooth'});return false;
  }catch(error){notify(error.message);return false;}
 }
 function refresh(){pending=null;review.hidden=true;renderHistory();}
 function markCommitted(output=getOutput(),committedKeys=['title','body','tags']){for(const key of committedKeys)if(['title','body','tags'].includes(key)){baseline[key]=String(output[key]??'');manual.delete(key);}}
 function markManual(key){if(['title','body','tags'].includes(key))manual.add(key);}
 function applySettings(value={}){
  try{
   if(Object.hasOwn(value,'studioProfile')){
    const profile=normalizeStudioProfile(value.studioProfile||{});
    // Profiles embedded in a saved card remain selectable even if the local
    // catalogue was cleared; loading a card never changes its saved layout.
    profiles=[...profiles.filter(row=>row.id!==profile.id),profile];selection.profile=profile.id;
   }
   if(Object.hasOwn(value,'titleFormula')){
    if(value.titleFormula){const validation=validateTitleFormula(value.titleFormula);if(!validation.valid)throw Error(validation.problems.join(' · '));let row=formulas.find(item=>item.formula===value.titleFormula);if(!row){row={id:uid('card-formula'),name:'Формула сохранённой карточки',formula:value.titleFormula};formulas=[...formulas.slice(-29),row];}selection.formula=row.id;}
    else selection.formula='';
   }
   renderProfiles();renderFormulas();refresh();
  }catch(error){notify(error.message);}
 }
 renderProfiles();renderFormulas();renderHistory();
 return {recordTitle,refresh,settings,applySettings,markManual,markCommitted,selectedProfile:()=>settings().studioProfile,profiles:()=>clone(profiles),requestRegeneration};
}
