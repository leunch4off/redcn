// Operator workflow is separate from source publication status and client copy.
export const WORKFLOW_STATES=Object.freeze({new:'Новый',verified:'Проверен',ready:'Готов',published:'Опубликован',archive:'Архив'});
const wfServices=Object.freeze({pickup:'Встреча / трансфер',arrival:'Помощь по прибытии',support:'Поддержка',luggage:'Помощь с багажом'});
const wfAudience=Object.freeze({general:'Общий профиль',student:'Студенты',family:'Семьи',professional:'Работающие'});
const wfObject=value=>value&&typeof value==='object'&&!Array.isArray(value);
const wfText=(value,max,label)=>{if(typeof value!=='string'||value.length>max||/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/u.test(value))throw Error('Неверное поле: '+label);return value.trim();};
const wfClone=value=>JSON.parse(JSON.stringify(value));
const wfId=()=>globalThis.crypto?.randomUUID?.()||'wf-'+Date.now().toString(36)+'-'+Math.random().toString(36).slice(2);
const wfDate=value=>typeof value==='string'&&/^\d{4}-\d{2}-\d{2}$/u.test(value)&&Number.isFinite(Date.parse(value+'T00:00:00Z'))&&new Date(value+'T00:00:00Z').toISOString().slice(0,10)===value;
const wfToday=()=>new Intl.DateTimeFormat('en-CA',{timeZone:'Europe/Moscow',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
const wfSource=value=>{try{const url=new URL(value);if(['https:','http:'].includes(url.protocol)&&!url.username&&!url.password&&String(value).length<=2048)return url.href;}catch{}return '';};
export function workflowCardId(card){const id=card?.id??card?.resultId??card?.fields?.resultId;if(typeof id!=='string'||!id||id.length>120)throw Error('У карточки нет устойчивого ID');return id;}
function wfFacts(card){return wfObject(card?.fields)?card.fields:card;}
function wfData(card){return normalizeWorkflow(card?.workflow??card?.fields?.workflow??{});}
function wfLabel(card){const facts=wfFacts(card);return String(card.title||card.titles?.[0]||facts.name||facts.address||facts.metro||workflowCardId(card)).slice(0,300);}
function wfTagList(value){const rows=Array.isArray(value)?value:String(value||'').split(/\s+/u);if(rows.some(tag=>typeof tag!=='string'))throw Error('Теги должны быть строками');const tags=[...new Set(rows.map(tag=>tag.replace(/^#+/u,'').trim()).filter(Boolean))];if(tags.length>80)throw Error('Допускается до 80 тегов');return tags.map(tag=>wfText(tag,100,'тег'));}
function wfPlatform(card){try{const host=new URL(wfFacts(card).url||card.url).hostname;if(/(^|\.)cian\.ru$/i.test(host))return 'cian';if(/(^|\.)avito\.ru$/i.test(host))return 'avito';return host;}catch{return '';}}
function wfCount(value,label){if(value===''||value===null||value===undefined)return null;if(!['string','number'].includes(typeof value)||!/^\d+$/u.test(String(value)))throw Error(label+' должен быть целым неотрицательным числом');const number=Number(value);if(!Number.isSafeInteger(number)||number>1_000_000_000_000)throw Error(label+' выходит за допустимый диапазон');return number;}
export function normalizeWorkflowMetric(value){
 if(!wfObject(value))throw Error('Неверная строка статистики');
 const source=wfSource(value.source);if(!source)throw Error('Нужна HTTP/HTTPS-ссылка на источник статистики');
 const day=String(value.day||'');if(!wfDate(day))throw Error('Нужна существующая дата статистики');
 const kind=value.kind||'daily';if(!['daily','cumulative'].includes(kind))throw Error('Выберите дневные данные или накопительный снимок');
 const postId=wfText(value.postId,200,'ID поста');if(!postId)throw Error('Нужен ID или ссылка конкретного поста');
 const tags=wfTagList(value.tags);if(!tags.length)throw Error('Укажите фактические теги опубликованного поста');
 const views=wfCount(value.views,'Просмотры'),leads=wfCount(value.leads,'Обращения');if(views===null&&leads===null)throw Error('Введите хотя бы один реальный счётчик');
 return {id:typeof value.id==='string'?value.id.slice(0,120):wfId(),postId,source,day,kind,tags,views,leads,recordedAt:typeof value.recordedAt==='string'?value.recordedAt:new Date().toISOString()};
}
export function normalizeWorkflow(value={}){
 if(!wfObject(value))throw Error('Неверные данные рабочего процесса');
 const status=value.status||'new';if(!Object.hasOwn(WORKFLOW_STATES,status))throw Error('Неизвестный рабочий статус карточки');
 const dueDate=value.dueDate||'';if(dueDate&&!wfDate(dueDate))throw Error('Неверный срок карточки');
 const housingType=value.housingType||'unknown';if(!['unknown','apartment','room','dormitory'].includes(housingType))throw Error('Неизвестный тип жилья');
 if(value.tasks!==undefined&&(!Array.isArray(value.tasks)||value.tasks.length>100))throw Error('Допускается до 100 задач карточки');
 if(value.links!==undefined&&(!Array.isArray(value.links)||value.links.length>100))throw Error('Допускается до 100 связей карточки');
 if(value.metrics!==undefined&&(!Array.isArray(value.metrics)||value.metrics.length>2000))throw Error('Допускается до 2000 наблюдений; сохраните экспорт перед продолжением');
 const tasks=(Array.isArray(value.tasks)?value.tasks:[]).map(task=>{
  if(!wfObject(task))throw Error('Неверная задача карточки');const text=wfText(task.text,500,'задача');if(!text)throw Error('Пустая задача карточки');
  const due=task.dueDate||'';if(due&&!wfDate(due))throw Error('Неверный срок задачи');
  return {id:typeof task.id==='string'&&task.id?task.id.slice(0,120):wfId(),text,done:task.done===true,assignee:wfText(task.assignee||'',100,'ответственный задачи'),dueDate:due};
 });
 if(new Set(tasks.map(task=>task.id)).size!==tasks.length)throw Error('Задачи должны иметь разные ID');
 const links=(Array.isArray(value.links)?value.links:[]).filter(link=>wfObject(link)&&typeof link.id==='string').slice(0,100).map(link=>({id:link.id.slice(0,120),confirmedAt:String(link.confirmedAt||'').slice(0,80),by:String(link.by||'Оператор').slice(0,100)}));
 const metrics=(Array.isArray(value.metrics)?value.metrics:[]).map(normalizeWorkflowMetric);
 return {status,housingType,assignee:wfText(value.assignee||'',100,'ответственный'),dueDate,tasks,links,metrics,needsRegeneration:value.needsRegeneration===true,updatedAt:typeof value.updatedAt==='string'?value.updatedAt:'',updatedBy:typeof value.updatedBy==='string'?value.updatedBy.slice(0,100):''};
}
export function updateWorkflowCard(card,patch,actor='Оператор этого браузера',at=new Date().toISOString()){
 const next=wfClone(card),before=wfData(card),workflow=normalizeWorkflow({...before,...patch,updatedAt:at,updatedBy:actor});next.workflow=workflow;return next;
}
export function workflowMissingFields(card){
 const facts=wfFacts(card),missing=[];
 if(!(Number(facts.price)>0))missing.push('Подтверждённая аренда');
 if(!(Number(facts.area)>0))missing.push('Общая площадь');
 if(!String(facts.metro||facts.transit||'').trim())missing.push('Станция / маршрут');
 if(!String(facts.address||facts.district||'').trim())missing.push('Адрес / район');
 if(facts.pricePeriod==='unknown'||facts.priceCurrency==='unknown')missing.push('Период / валюта ставки');
 if(facts.sourceReadiness?.state==='partial')missing.push('Проверить неполные разделы источника');
 return missing;
}
export function workflowBoard(cards){
 const board=Object.fromEntries(Object.keys(WORKFLOW_STATES).map(key=>[key,[]]));
 for(const card of cards||[])board[wfData(card).status].push(card);return board;
}
function wfBulkPatch(patch){
 if(!wfObject(patch))throw Error('Нужны поля массового изменения');const result={};
 for(const [key,value]of Object.entries(patch)){
  if(Object.hasOwn(wfServices,key)){if(typeof value!=='boolean')throw Error('Услуга должна быть отметкой: '+key);result[key]=value;}
  else if(key==='registration'){if(!['assist','included'].includes(value))throw Error('Неизвестный вариант регистрации');result[key]=value;}
  else if(key==='audience'){if(!Object.hasOwn(wfAudience,value))throw Error('Неизвестный профиль тегов');result[key]=value;}
  else throw Error('Поле не разрешено для массового изменения: '+key);
 }
 if(!Object.keys(result).length)throw Error('Выберите хотя бы одно изменение');return result;
}
function wfRevision(card,fields=[]){const facts=wfFacts(card);return JSON.stringify({id:workflowCardId(card),updated:card.updated??card.updatedAt??'',url:facts.url||'',title:wfLabel(card),price:facts.price,area:facts.area,address:facts.address,rooms:facts.rooms,workflow:wfData(card),values:Object.fromEntries(fields.map(key=>[key,facts[key]??null]))});}
export function prepareWorkflowBulk(cards,ids,patch){
 const values=wfBulkPatch(patch),selected=[...new Set(ids||[])];if(!selected.length||selected.length>100)throw Error('Выберите от 1 до 100 карточек');
 const rows=selected.map(id=>{const matches=(cards||[]).filter(row=>workflowCardId(row)===id);if(matches.length!==1)throw Error('Карточка недоступна или ID неоднозначен: '+id);const card=matches[0],facts=wfFacts(card),before=wfData(card),changes=Object.entries(values).filter(([key,value])=>facts[key]!==value).map(([key,value])=>({key,before:facts[key]??null,after:value}));const nextStatus=changes.length&&['ready','published'].includes(before.status)?'verified':before.status;return {id,label:wfLabel(card),changes,beforeStatus:before.status,afterStatus:nextStatus,revision:wfRevision(card,Object.keys(values))};});
 return {version:1,patch:values,rows};
}
export function applyWorkflowBulk(cards,preview,actor='Оператор этого браузера'){
 if(!wfObject(preview)||preview.version!==1||!Array.isArray(preview.rows))throw Error('Нужен предпросмотр изменений');
 const validated=prepareWorkflowBulk(cards,preview.rows.map(row=>row.id),preview.patch);
 if(JSON.stringify(validated)!==JSON.stringify(preview))throw Error('Карточки изменились после предпросмотра. Подготовьте его заново.');
 const byId=new Map(preview.rows.map(row=>[row.id,row])),at=new Date().toISOString();
 return (cards||[]).map(card=>{const row=byId.get(workflowCardId(card));if(!row||!row.changes.length)return card;let next=wfClone(card);if(wfObject(next.fields))next.fields={...next.fields,...preview.patch};else Object.assign(next,preview.patch);next=updateWorkflowCard(next,{status:row.afterStatus,needsRegeneration:true},actor,at);return next;});
}
export function workflowDuplicateCandidates(cards){
 const rows=cards||[],result=[],groups=new Map();
 const address=card=>String(wfFacts(card).address||'').toLocaleLowerCase('ru').replace(/[^\p{L}\p{N}]/gu,'');
 for(const card of rows){const facts=wfFacts(card),key=address(card)+'|'+String(facts.rooms||'');if(!address(card)||!facts.rooms||!(Number(facts.area)>0)||!wfPlatform(card))continue;if(!groups.has(key))groups.set(key,[]);groups.get(key).push(card);}
 outer:for(const group of groups.values())for(let i=0;i<group.length;i++)for(let j=i+1;j<group.length;j++){
  const a=group[i],b=group[j],af=wfFacts(a),bf=wfFacts(b);
  if(wfPlatform(a)===wfPlatform(b))continue;
  if(!(Number(af.area)>0&&Number(bf.area)>0)||Math.abs(Number(af.area)-Number(bf.area))>0.5||!String(af.rooms||'')||String(af.rooms)!==String(bf.rooms))continue;
  result.push({ids:[workflowCardId(a),workflowCardId(b)],reason:'Совпадают введённый адрес, число комнат и близкая площадь. Требуется ручная сверка.'});
  if(result.length>=200)break outer;
 }return result;
}
export function prepareWorkflowLinks(cards,ids){
 const selected=[...new Set(ids||[])];if(selected.length<2||selected.length>20)throw Error('Выберите от 2 до 20 карточек для сравнения');
 const rows=selected.map(id=>{const matches=(cards||[]).filter(item=>workflowCardId(item)===id);if(matches.length!==1)throw Error('Карточка недоступна или ID неоднозначен: '+id);const card=matches[0],facts=wfFacts(card);return {id,label:wfLabel(card),platform:wfPlatform(card),url:wfSource(facts.url||''),address:String(facts.address||''),area:String(facts.area||''),rooms:String(facts.rooms||''),price:String(facts.price||''),revision:wfRevision(card)};});
 if(new Set(rows.map(row=>row.platform).filter(Boolean)).size<2)throw Error('Нужны объявления как минимум двух разных источников');
 return {version:1,rows};
}
export function confirmWorkflowLinks(cards,preview,{confirmed=false,actor='Оператор этого браузера'}={}){
 if(confirmed!==true)throw Error('Подтвердите, что сравнили карточки и это одна квартира');
 const verified=prepareWorkflowLinks(cards,preview?.rows?.map(row=>row.id));if(JSON.stringify(verified)!==JSON.stringify(preview))throw Error('Сравниваемые карточки изменились. Повторите проверку.');
 const ids=preview.rows.map(row=>row.id),at=new Date().toISOString();
 return cards.map(card=>{const id=workflowCardId(card);if(!ids.includes(id))return card;const old=wfData(card),links=[...old.links];for(const other of ids.filter(value=>value!==id)){if(!links.some(link=>link.id===other))links.push({id:other,confirmedAt:at,by:actor});}return updateWorkflowCard(card,{links},actor,at);});
}
export function unlinkWorkflowCards(cards,id,otherId,actor='Оператор этого браузера'){
 if(id===otherId)throw Error('Нельзя связывать карточку с собой');
 return (cards||[]).map(card=>{const current=workflowCardId(card);return current===id||current===otherId?updateWorkflowCard(card,{links:wfData(card).links.filter(link=>link.id!==(current===id?otherId:id))},actor):card;});
}
export function addWorkflowMetric(card,value){
 const metric=normalizeWorkflowMetric(value),workflow=wfData(card),key=row=>JSON.stringify([row.postId,row.source,row.day,row.kind]);
 const metrics=[...workflow.metrics.filter(row=>key(row)!==key(metric)),metric];
 if(metrics.length>2000)throw Error('Достигнут предел 2000 наблюдений; сохраните экспорт перед продолжением');
 return updateWorkflowCard(card,{metrics});
}
export function workflowTagPerformance(cards){
 const series=new Map(),groups=new Map(),observations=new Map();
 for(const card of cards||[])for(const metric of wfData(card).metrics){const key=JSON.stringify([metric.source,metric.postId,metric.kind,metric.day]),previous=observations.get(key);if(!previous)observations.set(key,metric);else if(JSON.stringify([previous.views,previous.leads,[...previous.tags].sort()])!==JSON.stringify([metric.views,metric.leads,[...metric.tags].sort()]))observations.set(key,{...previous,views:null,leads:null,tags:[...new Set([...previous.tags,...metric.tags])],metricConflict:true});}
 for(const metric of observations.values()){const key=JSON.stringify([metric.source,metric.postId,metric.kind,[...metric.tags].sort()]);if(!series.has(key))series.set(key,new Map());series.get(key).set(metric.day,metric);}
 for(const [key,byDay]of series){const rows=[...byDay.values()].sort((a,b)=>a.day.localeCompare(b.day)),latest=rows.at(-1),values={},notes=[];
  if(rows.some(row=>row.metricConflict))notes.push('Разные карточки содержат противоречивые показатели или теги одного поста за одну дату; спорное наблюдение исключено');
  for(const name of ['views','leads']){
   if(latest.kind==='daily'){const supplied=rows.map(row=>row[name]).filter(value=>value!==null);values[name]=supplied.length?supplied.reduce((a,b)=>a+b,0):null;if(supplied.length<rows.length)notes.push('Часть дневных '+(name==='views'?'просмотров':'обращений')+' неизвестна');}
   else{const previous=rows.at(-2);values[name]=previous&&latest[name]!==null&&previous[name]!==null&&latest[name]>=previous[name]?latest[name]-previous[name]:null;if(values[name]===null)notes.push('Нужны два непротиворечивых накопительных снимка '+name);}
  }
  for(const tag of latest.tags){const groupKey=tag+'|'+latest.kind;if(!groups.has(groupKey))groups.set(groupKey,{tag,kind:latest.kind,posts:new Set(),views:null,leads:null,notes:new Set(),periods:[]});const group=groups.get(groupKey);group.posts.add(latest.source+'|'+latest.postId);for(const name of ['views','leads'])if(values[name]!==null)group[name]=(group[name]??0)+values[name];for(const note of notes)group.notes.add(note);group.periods.push({from:latest.kind==='cumulative'?rows.at(-2)?.day||null:rows[0].day,to:latest.day});}
 }
 return [...groups.values()].map(row=>({...row,posts:row.posts.size,notes:[...row.notes]})).sort((a,b)=>a.tag.localeCompare(b.tag)||a.kind.localeCompare(b.kind));
}

export function setupWorkflow({mount,getObjects,saveObjects,onOpen=()=>{},toast=()=>{}}={}){
 if(!mount)return null;
 let selected=new Set(),activeId=null,pendingBulk=null,pendingLinks=null;
 const make=(tag,text,props={})=>{const node=document.createElement(tag);if(text!==undefined)node.textContent=text;Object.assign(node,props);return node;};
 const element=(tag,text,props)=>make(tag,text,props),button=(text,handler)=>{const node=make('button',text,{type:'button',className:'btn'});node.onclick=handler;return node;};
 const shell=make('section',undefined,{className:'workflow'}),heading=make('h2','Рабочая доска карточек'),note=make('p','Рабочий статус отличается от статуса объявления на площадке. Данные этой доски сохраняются с библиотекой в данном браузере.',{className:'muted'}),status=make('p','Выберите карточку или несколько карточек для пакетных действий.',{className:'workflow-status'});
 status.setAttribute('role','status');status.setAttribute('aria-live','polite');
 const board=make('div',undefined,{className:'workflow-board'}),toolbar=make('div',undefined,{className:'workflow-toolbar'}),editor=make('div',undefined,{className:'workflow-editor'}),actions=make('details'),actionBody=make('div',undefined,{className:'workflow-actions'}),analytics=make('details'),metricsBody=make('div');
 actions.append(make('summary','Массовые изменения и сверка дублей'),actionBody);analytics.append(make('summary','Теги, просмотры и обращения'),metricsBody);
 shell.append(heading,note,status,toolbar,board,editor,actions,analytics);mount.replaceChildren(shell);
 const message=text=>{status.textContent=text;toast(text);};
 const cards=()=>{const value=getObjects();return Array.isArray(value)?value.filter(row=>wfObject(row)&&typeof (row.id??row.resultId??row.fields?.resultId)==='string'):[];};
 function persist(next){const changed=new Map(next.map(card=>[workflowCardId(card),card])),raw=getObjects(),merged=raw.map(row=>{try{return changed.get(workflowCardId(row))||row;}catch{return row;}});if(saveObjects(merged)===false)throw Error('Изменения не сохранены');refresh();}
 function run(handler){try{handler();}catch(error){message(error.message);}}
 function control(parent,text,type='text',value=''){const label=make('label',text),input=make(type==='select'?'select':type==='textarea'?'textarea':'input');if(!['select','textarea'].includes(type))input.type=type;input.setAttribute('aria-label',text);if(type!=='select')input.value=value;label.append(input);parent.append(label);return input;}
 function select(parent,text,options,value=''){const node=control(parent,text,'select');for(const [key,label]of options)node.append(make('option',label,{value:key}));node.value=value;return node;}
 const fieldNames={...wfServices,registration:'Регистрация',audience:'Профиль тегов'};
 function valueLabel(key,value){if(typeof value==='boolean')return value?'Да':'Нет';if(value===null||value===undefined||value==='')return 'Не указано';return key==='audience'?(wfAudience[value]||value):key==='registration'?({assist:'Помощь',included:'Включена'}[value]||value):String(value);}
 function table(parent,headers,rows){const wrap=make('div',undefined,{className:'workflow-table-wrap'}),node=make('table'),head=make('tr'),thead=make('thead'),body=make('tbody');for(const label of headers)head.append(make('th',label));thead.append(head);for(const row of rows){const tr=make('tr');for(const value of row)tr.append(make('td',String(value??'—')));body.append(tr);}node.append(thead,body);wrap.append(node);parent.append(wrap);}
 function renderBoard(){board.replaceChildren();const all=cards(),groups=workflowBoard(all);for(const [state,label]of Object.entries(WORKFLOW_STATES)){const column=make('section',undefined,{className:'workflow-column'});column.dataset.state=state;column.append(make('h3',label+' · '+groups[state].length));for(const card of groups[state]){const id=workflowCardId(card),data=wfData(card),item=make('article',undefined,{className:'workflow-card'}),checkLabel=make('label'),check=make('input',undefined,{type:'checkbox',checked:selected.has(id)});check.setAttribute('aria-label','Выбрать '+wfLabel(card));check.onchange=()=>{check.checked?selected.add(id):selected.delete(id);status.textContent='Выбрано: '+selected.size;};checkLabel.append(check,make('strong',wfLabel(card)));item.append(checkLabel);if(data.assignee||data.dueDate)item.append(make('p',[data.assignee,data.dueDate&&'до '+data.dueDate].filter(Boolean).join(' · '),{className:data.dueDate&&data.dueDate<wfToday()&&state!=='archive'?'workflow-overdue':'muted'}));if(data.needsRegeneration)item.append(make('p','Поля менялись: обновите описание перед публикацией.',{className:'workflow-warning'}));const missing=workflowMissingFields(card);if(missing.length)item.append(make('p','Уточнить: '+missing.join(' · '),{className:'muted'}));item.append(button('Рабочие данные',()=>{activeId=id;renderEditor();editor.scrollIntoView?.({block:'nearest'});}),button('Открыть карточку',()=>onOpen(id)));column.append(item);}board.append(column);}}
 function renderEditor(){editor.replaceChildren();const card=cards().find(row=>workflowCardId(row)===activeId);if(!card)return;const workflow=wfData(card),fingerprint=wfRevision(card),fields=make('div',undefined,{className:'workflow-form'});editor.append(make('h3','Рабочие данные · '+wfLabel(card)),fields);
  const state=select(fields,'Рабочий статус',Object.entries(WORKFLOW_STATES),workflow.status),housing=select(fields,'Подтверждённый тип жилья',[['unknown','Не подтверждён'],['apartment','Квартира'],['room','Комната'],['dormitory','Общежитие']],workflow.housingType),assignee=control(fields,'Ответственный','text',workflow.assignee),due=control(fields,'Срок карточки · Москва','date',workflow.dueDate);assignee.maxLength=100;
  fields.append(button('Сохранить рабочие данные',()=>run(()=>{const current=cards().find(row=>workflowCardId(row)===activeId);if(!current||wfRevision(current)!==fingerprint)throw Error('Карточка изменилась. Откройте рабочие данные заново.');persist(cards().map(row=>workflowCardId(row)===activeId?updateWorkflowCard(row,{status:state.value,housingType:housing.value,assignee:assignee.value,dueDate:due.value}):row));message('Рабочие данные сохранены');})));
  const taskList=make('div',undefined,{className:'workflow-task-list'});editor.append(make('h4','Задачи и недостающие данные'),taskList);
  for(const task of workflow.tasks){const row=make('div',undefined,{className:'workflow-task'}),label=make('label'),check=make('input',undefined,{type:'checkbox',checked:task.done});label.append(check,make('span',task.text));check.onchange=()=>run(()=>{persist(cards().map(item=>workflowCardId(item)===activeId?updateWorkflowCard(item,{tasks:wfData(item).tasks.map(value=>value.id===task.id?{...value,done:check.checked}:value)}):item));});row.append(label,make('small',[task.assignee,task.dueDate].filter(Boolean).join(' · ')));taskList.append(row);}
  const taskForm=make('div',undefined,{className:'workflow-form'}),text=control(taskForm,'Новая задача','text'),taskAssignee=control(taskForm,'Ответственный задачи','text'),taskDue=control(taskForm,'Срок задачи · Москва','date');text.maxLength=500;taskAssignee.maxLength=100;taskForm.append(button('Добавить задачу',()=>run(()=>{const id=activeId;persist(cards().map(row=>workflowCardId(row)===id?updateWorkflowCard(row,{tasks:[...wfData(row).tasks,{id:wfId(),text:text.value,done:false,assignee:taskAssignee.value,dueDate:taskDue.value}]}):row));message('Задача добавлена');})));editor.append(taskForm);
  if(workflow.links.length){editor.append(make('h4','Подтверждённые связи'));for(const link of workflow.links){const row=make('p',link.id+' · '+link.by+' · '+link.confirmedAt,{className:'muted'});row.append(button('Убрать связь с '+link.id,()=>run(()=>{persist(unlinkWorkflowCards(cards(),activeId,link.id));message('Двусторонняя связь удалена; сами карточки сохранены');})));editor.append(row);}}
  const metricForm=make('details'),metricBody=make('div',undefined,{className:'workflow-form'});metricForm.append(make('summary','Добавить реальные показатели опубликованного поста'),metricBody);editor.append(metricForm);
  const post=control(metricBody,'ID поста','text'),source=control(metricBody,'Источник статистики · ссылка','url'),day=control(metricBody,'Дата статистики','date',wfToday()),kind=select(metricBody,'Вид статистики',[['daily','За конкретный день'],['cumulative','Накопительный снимок']],'daily'),tags=control(metricBody,'Фактические теги поста','textarea',card.tags||wfFacts(card).tags||''),views=control(metricBody,'Просмотры · пусто если неизвестно','number'),leads=control(metricBody,'Обращения · пусто если неизвестно','number');for(const node of [views,leads]){node.min='0';node.step='1';}
  metricBody.append(make('p','Введите данные кабинета или собственной записи. Состав тегов фиксируется в наблюдении; счётчики одинакового поста/источника/даты заменяются, не удваиваются.',{className:'muted'}),button('Сохранить показатели',()=>run(()=>{const metric={postId:post.value,source:source.value,day:day.value,kind:kind.value,tags:tags.value,views:views.value,leads:leads.value};persist(cards().map(row=>workflowCardId(row)===activeId?addWorkflowMetric(row,metric):row));message('Реальные показатели сохранены с источником и составом тегов');})));
  if(workflow.metrics.length){editor.append(make('h4','Сохранённые наблюдения поста'));table(editor,['Пост','Дата','Вид','Просмотры','Обращения','Теги','Источник'],workflow.metrics.map(row=>[row.postId,row.day,row.kind,row.views??'Неизвестно',row.leads??'Неизвестно',row.tags.map(tag=>'#'+tag).join(' '),row.source]));}
 }
 function renderActions(){actionBody.replaceChildren();const form=make('div',undefined,{className:'workflow-form'}),serviceControls={};actionBody.append(form);
  for(const [key,label]of Object.entries(wfServices))serviceControls[key]=select(form,label,[['keep','Не менять'],['yes','Включить'],['no','Убрать']],'keep');
  const registration=select(form,'Регистрация',[['keep','Не менять'],['assist','Помощь'],['included','Включена']],'keep'),audience=select(form,'Профиль тегов',[['keep','Не менять'],...Object.entries(wfAudience)],'keep'),preview=make('div',undefined,{className:'workflow-preview'});actionBody.append(preview);
  form.append(button('Предпросмотр массового изменения',()=>run(()=>{const patch={};for(const [key,node]of Object.entries(serviceControls))if(node.value!=='keep')patch[key]=node.value==='yes';if(registration.value!=='keep')patch.registration=registration.value;if(audience.value!=='keep')patch.audience=audience.value;pendingBulk=prepareWorkflowBulk(cards(),[...selected],patch);preview.replaceChildren(make('h4','Изменения перед применением'));table(preview,['Карточка','Поле','Было','Станет'],pendingBulk.rows.flatMap(row=>[...row.changes.map(change=>[row.label,fieldNames[change.key]||change.key,valueLabel(change.key,change.before),valueLabel(change.key,change.after)]),...(row.beforeStatus!==row.afterStatus?[[row.label,'Рабочий статус',WORKFLOW_STATES[row.beforeStatus],WORKFLOW_STATES[row.afterStatus]]]:[])]));preview.append(make('p','Описание и теги не перезаписываются. После изменения полей обновите их в редакторе; готовые/опубликованные карточки возвращаются на проверку.',{className:'muted'}),button('Применить показанные изменения',()=>run(()=>{if(!pendingBulk)throw Error('Подготовьте предпросмотр');persist(applyWorkflowBulk(cards(),pendingBulk));pendingBulk=null;message('Выбранные поля обновлены, остальные данные сохранены');})),button('Отменить массовое изменение',()=>{pendingBulk=null;preview.replaceChildren();}));})));
  const candidateNote=make('p',undefined,{className:'muted'}),candidates=workflowDuplicateCandidates(cards());candidateNote.textContent=candidates.length?'Возможных пар по адресу/площади/комнатам: '+candidates.length+'. Это подсказки для ручной сверки.':'Автоматически подтверждённых дублей нет. Для сверки выберите карточки разных источников.';actionBody.append(candidateNote);for(const candidate of candidates.slice(0,10))actionBody.append(button('Выбрать пару: '+candidate.ids.join(' / '),()=>{selected=new Set(candidate.ids);renderBoard();status.textContent=candidate.reason;}));
  const linkPreview=make('div',undefined,{className:'workflow-preview'});actionBody.append(button('Сравнить выбранные источники',()=>run(()=>{pendingLinks=prepareWorkflowLinks(cards(),[...selected]);linkPreview.replaceChildren(make('h4','Сверка перед связыванием'));table(linkPreview,['Источник','Карточка','Адрес','Площадь','Комнаты','Цена'],pendingLinks.rows.map(row=>[row.platform,row.label,row.address,row.area,row.rooms,row.price]));for(const row of pendingLinks.rows)if(row.url){const link=make('a','Открыть источник '+row.platform,{href:row.url,target:'_blank',rel:'noopener noreferrer'});linkPreview.append(link);}const label=make('label'),confirmed=make('input',undefined,{type:'checkbox'});label.append(confirmed,document.createTextNode(' Я сравнил объявления и подтверждаю, что это одна квартира'));linkPreview.append(label,button('Подтвердить связь без объединения данных',()=>run(()=>{persist(confirmWorkflowLinks(cards(),pendingLinks,{confirmed:confirmed.checked}));pendingLinks=null;message('Двусторонняя связь сохранена; исходники, фото и карточки не объединялись');})));})),linkPreview);
 }
 function renderMetrics(){metricsBody.replaceChildren(make('p','Здесь показаны введённые показатели постов, в которых использован тег. Вклад отдельного тега не измерен. Дневные и накопительные серии не смешиваются; пустое значение не означает ноль.',{className:'muted'}));const rows=workflowTagPerformance(cards());if(!rows.length)return metricsBody.append(make('p','Реальных наблюдений пока нет. Добавьте их в рабочих данных карточки.'));table(metricsBody,['Тег','Данные','Даты наблюдений','Постов','Просмотры постов','Обращения','Проверка полноты'],rows.map(row=>['#'+row.tag,row.kind==='daily'?'Дневные суммы':'Разности снимков',row.periods.map(period=>(period.from||'?')+' — '+period.to).join('; '),row.posts,row.views??'Неизвестно',row.leads??'Неизвестно',row.notes.join(' · ')||'Все введённые значения известны']));}
 toolbar.append(button('Обновить доску',()=>refresh()),button('Снять выбор',()=>{selected.clear();refresh();}));
 function refresh(){try{const ids=new Set(cards().map(workflowCardId));selected=new Set([...selected].filter(id=>ids.has(id)));pendingBulk=null;pendingLinks=null;renderBoard();renderEditor();renderActions();renderMetrics();status.textContent='Карточек: '+ids.size+' · выбрано: '+selected.size;}catch(error){message('Рабочая доска: '+error.message);}}
 refresh();return {refresh,destroy:()=>mount.replaceChildren()};
}
