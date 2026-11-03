import test from 'node:test';
import assert from 'node:assert/strict';
import {learningAdvertisingState,setupLearningStudio} from '../web/learning-studio.mjs';

const fixture=()=>({status:{counts:{pending:1,approved:30},styleOnlyExamples:25,activeLane:'advertising',lanes:{advertising:{approvedExamples:4,approvedRecords:5,approvedGroups:4,pendingExamples:2,trainExamples:3,validExamples:1,minimumExamples:20,minimumGroups:5,minimumTrain:16,minimumValidation:4,trainingEligible:false,blockingReasons:['reviewed-examples-needed','validation-split-needed'],assistantReviewed:5,humanVerified:0}}},scheduler:{lane:'advertising',phase:'collecting',enabled:true,automaticPromotion:false,workerAlive:true,modelProcessRunning:false,stepsCompleted:0,promotion:{}},text_ai:{model:'Qwen2.5-0.5B-Instruct',stepsCompleted:800,trainedParameters:270336,candidateAdapterSelected:true,advertising:{model:'Qwen3-4B-Instruct-2507',installed:true,supported:true,enabled:true,separateProfile:true,trainedByRedcn:false,candidateAdapterSelected:false,adapterActive:false,adapterCompatible:false,stepsCompleted:0}},events:[]});

test('advertising readiness, provenance and steps do not inherit translation receipts or style counts',()=>{
 const value=fixture(),state=learningAdvertisingState(value);
 assert.match(state.examples,/4 \/ 20/);assert.match(state.splits,/3 \/ 16.*1 \/ 4/);
 assert.match(state.queue,/2.*5/);assert.match(state.provenance,/человеком: 0.*ассистентом: 5/);
 assert.match(state.model,/Qwen3-4B/);assert.match(state.adapter,/базовая модель/);
 assert.match(state.readiness,/Сбор данных/);assert.match(state.style,/25/);
 assert.match(state.translation,/800 шагов/);
 assert.doesNotMatch(Object.entries(state).filter(([key])=>key!=='translation').map(([,value])=>value).join(' '),/800|0\.5B|270.?336/);
});

test('a saved training phase is not a running process and another lane cannot supply progress or acceptance',()=>{
 const value=fixture();value.scheduler={lane:'translation',phase:'training',workerAlive:false,modelProcessRunning:false,stepsCompleted:800,trainingProgress:{stepsCompleted:400,requestedSteps:800},promotion:{state:'accepted',latestEvaluation:{requested:100,candidatePassed:100,stepsCompleted:800}}};
 const state=learningAdvertisingState(value);
 assert.match(state.process,/не подтверждена/);assert.equal(state.progress,'');assert.equal(state.evaluation,'');
 assert.doesNotMatch(state.steps,/800/);
 value.scheduler.lane='advertising';value.scheduler.phase='training';value.scheduler.stepsCompleted=12;value.scheduler.trainingProgress={stepsCompleted:12,requestedSteps:80};value.scheduler.promotion={};
 assert.doesNotMatch(learningAdvertisingState(value).process,/Сейчас выполняется обучение/);
 value.scheduler.modelProcessRunning=true;
 assert.match(learningAdvertisingState(value).process,/Сейчас выполняется обучение/);
 assert.match(learningAdvertisingState(value).progress,/12 \/ 80/);
 value.scheduler.phase='evaluating';assert.match(learningAdvertisingState(value).process,/проверка кандидата/);
});

test('a compatible saved adapter is distinguished from the adapter configured for generation',()=>{
 const value=fixture();Object.assign(value.text_ai.advertising,{candidateAdapterSelected:true,adapterCompatible:true,trainedByRedcn:true,stepsCompleted:40,trainedParameters:1851392,adapterActive:false});
 let state=learningAdvertisingState(value);assert.match(state.adapter,/применение не подтверждено/);assert.match(state.adapter,/40 шагов/);assert.doesNotMatch(state.adapter,/подключён для генерации/);
 value.text_ai.advertising.adapterActive=true;state=learningAdvertisingState(value);assert.match(state.adapter,/совместим и подключён для генерации/);assert.doesNotMatch(state.process,/Сейчас выполняется обучение/);
 value.text_ai.advertising.adapterCompatible=false;value.text_ai.advertising.adapterProblem='Ошибка совместимости';state=learningAdvertisingState(value);assert.doesNotMatch(state.adapter,/подключён для генерации|Квитанция/);assert.match(state.adapter,/Ошибка совместимости/);
});

test('missing lane data stays unknown even when generic memory is ready',()=>{
 const state=learningAdvertisingState({status:{approvedExamples:100,trainingEligible:true},scheduler:{phase:'trained',stepsCompleted:1000},text_ai:{stepsCompleted:1000}});
 assert.match(state.readiness,/не подтверждена/);assert.match(state.examples,/нужны полный текст/);assert.doesNotMatch(state.steps,/1000/);
});

class StudioNode{
 constructor(tag){this.tagName=tag.toLowerCase();this.children=[];this.attributes={};this.style={};this.listeners=new Map();this._text='';this._value=undefined;this.checked=false;this.disabled=false;this.hidden=false;this.readOnly=false;this.className='';}
 get parentElement(){return this.parentNode;}
 get textContent(){return this._text+this.children.map(child=>child.textContent).join('');}
 set textContent(value){this._text=String(value);this.children=[];}
 get value(){return this._value===undefined?(this.tagName==='select'?(this.children[0]?.value||''):''):this._value;}
 set value(value){this._value=String(value);}
 append(...children){for(const child of children){child.parentNode=this;this.children.push(child);}}
 prepend(...children){for(const child of children)child.parentNode=this;this.children.unshift(...children);}
 replaceChildren(...children){this._text='';this.children=[];this.append(...children);}
 setAttribute(name,value){this.attributes[name]=String(value);}
 getAttribute(name){return this.attributes[name]??null;}
 addEventListener(name,callback){if(!this.listeners.has(name))this.listeners.set(name,[]);this.listeners.get(name).push(callback);}
 matches(selector){return selector.startsWith('.')?this.className.split(/\s+/).includes(selector.slice(1)):this.tagName===selector.toLowerCase();}
 querySelectorAll(selector){const selectors=selector.split(',').map(value=>value.trim());return this.children.flatMap(child=>[...(selectors.some(value=>child.matches(value))?[child]:[]),...child.querySelectorAll(selector)]);}
 querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
 remove(){if(this.parentNode)this.parentNode.children=this.parentNode.children.filter(child=>child!==this);}
}
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
const tick=async()=>{await Promise.resolve();await Promise.resolve();};
function harness(t,{response=fixture(),handle}={}){
 const previous=globalThis.document;globalThis.document={createElement:tag=>new StudioNode(tag),hidden:false};
 const mount=new StudioNode('main'),calls=[],applied=[];let fields={city:'Москва',rooms:'1',area:'38',price:'65000',sourceDescription:'Без животных. Фото подтверждены.',photoEvidence:{bedAny:{status:'confirmed'}},source:'PRIVATE_RAW_HTML',ownerToken:'PRIVATE_TOKEN'},group='property-one',output={title:'莫斯科38㎡一居室',body:'莫斯科38㎡一居室，月租65000₽。',tags:'#租房'};
 const control=setupLearningStudio({mount,getFields:()=>fields,getGroup:()=>group,getOutput:()=>output,onApply:async candidate=>{applied.push(candidate);output={...output,...candidate};},api:async(path,data)=>{calls.push({path,data});const result=await handle?.(path,data);if(result!==undefined)return result;if(data?.action==='generate-preview')return {status:'review',title:'新标题',body:'新描述',tags:'#租房'};return response;}});
 t.after(()=>{control.destroy();globalThis.document=previous;});
 const label=(name,root=mount)=>root.querySelectorAll('input,textarea,select').find(node=>node.attributes['aria-label']===name);
 const button=(name,root=mount)=>root.querySelectorAll('button').find(node=>node.textContent===name);
 return {mount,control,calls,applied,label,button,setFields:value=>{fields=value;},setGroup:value=>{group=value;},setResponse:value=>{response=value;},getFields:()=>fields};
}
function fill(node,value){node.value=value;node.oninput?.();}
function check(node){node.checked=true;node.onchange?.();}
async function loadPair(h){await h.button('Загрузить текущую карточку для сверки').onclick();fill(h.label('Что сверено с исходными фактами'),'Сверены цена, запреты и каждый предмет с исходником.');check(h.label('Я сверил весь заголовок и описание с исходными фактами этой квартиры'));}
const groundedEvent=(body='完整正文')=>({id:'grounded-one',kind:'grounded_listing',status:'pending',payload:{fields:{city:'Москва',sourceDescription:'SOURCE_TEXT_END <img src=x onerror=secret()>',price:65000},rules:{},request:{messages:[{role:'user',content:'月租：65000₽/月\n配置：床。'}]},target:{title:'莫斯科38㎡一居室',body},review:{actor:'assistant_editorial_fixture',humanVerified:false,reason:'Editorial fixture only.'}}});

test('pending grounded rows expose the full target and source without a generic approval action',async t=>{
 const response=fixture(),body='正文'.repeat(1800)+'TAIL_OF_FULL_TARGET';response.events=[groundedEvent(body)];const h=harness(t,{response});await h.control.refresh();
 const row=h.mount.querySelector('article');assert.equal(h.label('Полное описание проверяемой записи',row).value,body);assert.match(row.textContent,/SOURCE_TEXT_END/);assert.match(row.textContent,/Проверка ассистента/);assert.equal(row.querySelectorAll('img').length,0);
 assert.equal(h.button('Утвердить',row),undefined);assert.equal(h.button('Сохранить сверенный текст для обучения',row).disabled,true);assert.equal(h.control.activity().dirty,false);
});

test('record-grounded requires explicit review and sends the captured fields without caller-supplied authorship',async t=>{
 const h=harness(t);assert.deepEqual(h.control.activity(),{dirty:false,busy:false});
 await h.button('Загрузить текущую карточку для сверки').onclick();assert.equal(h.control.activity().dirty,true);
 const save=h.button('Сохранить проверенную пару');assert.equal(save.disabled,true);await save.onclick();assert.equal(h.calls.filter(row=>row.data?.action==='record-grounded').length,0);
 fill(h.label('Что сверено с исходными фактами'),'Цена, запреты, мебель и транспорт сверены.');check(h.label('Я сверил весь заголовок и описание с исходными фактами этой квартиры'));assert.equal(save.disabled,false);
 fill(h.label('Полное проверенное описание пары'),'莫斯科38㎡一居室，月租65000₽。不接受宠物。');assert.equal(save.disabled,true);check(h.label('Я сверил весь заголовок и описание с исходными фактами этой квартиры'));
 await save.onclick();const written=h.calls.find(row=>row.data?.action==='record-grounded').data;
 assert.equal(written.groupId,'property-one');assert.equal(written.reviewed,true);assert.equal(written.fields.sourceDescription,h.getFields().sourceDescription);assert.deepEqual(written.fields.photoEvidence,h.getFields().photoEvidence);assert.equal(written.fields.source,undefined);assert.equal(written.fields.ownerToken,undefined);assert.equal(written.actor,undefined);assert.equal(written.humanVerified,undefined);assert.match(written.target.body,/不接受宠物/);assert.equal(h.control.activity().dirty,false);
});

test('a pair checked on another card or on older facts cannot be saved under the current card',async t=>{
 const h=harness(t);await loadPair(h);h.setFields({...h.getFields(),price:70000});await h.button('Сохранить проверенную пару').onclick();
 assert.equal(h.calls.filter(row=>row.data?.action==='record-grounded').length,0);assert.match(h.mount.textContent,/Факты или выбранная карточка изменились/);assert.equal(h.label('Я сверил весь заголовок и описание с исходными фактами этой квартиры').checked,false);
 await loadPair(h);h.setGroup('property-two');await h.button('Сохранить проверенную пару').onclick();assert.equal(h.calls.filter(row=>row.data?.action==='record-grounded').length,0);
});

test('grounded review submits the complete edited target only after new explicit confirmation',async t=>{
 const response=fixture();response.events=[groundedEvent()];const h=harness(t,{response});await h.control.refresh();const row=h.mount.querySelector('article');
 fill(h.label('Основание проверки этой записи',row),'Сверены исходная цена и предметы.');check(h.label('Я сверил весь текст этой записи с её исходными фактами',row));
 fill(h.label('Полное описание проверяемой записи',row),'完整的人工修订。');const save=h.button('Сохранить сверенный текст для обучения',row);assert.equal(save.disabled,true);
 await save.onclick();assert.equal(h.calls.some(row=>row.data?.action==='review-grounded'),false);
 check(h.label('Я сверил весь текст этой записи с её исходными фактами',row));await save.onclick();const written=h.calls.find(row=>row.data?.action==='review-grounded').data;
 assert.deepEqual(written,{action:'review-grounded',id:'grounded-one',target:{title:'莫斯科38㎡一居室',body:'完整的人工修订。'},reviewed:true,reason:'Сверены исходная цена и предметы.'});assert.equal(h.calls.some(row=>row.data?.action==='review'&&row.data?.status==='approved'),false);
});

test('refresh preserves a pending manual review while changed source facts require a new confirmation',async t=>{
 const response=fixture();response.events=[groundedEvent()];const h=harness(t,{response});await h.control.refresh();fill(h.label('Полное описание проверяемой записи'),'手工修改，不可丢失。');fill(h.label('Основание проверки этой записи'),'Проверены предметы.');check(h.label('Я сверил весь текст этой записи с её исходными фактами'));
 await h.control.refresh();assert.equal(h.label('Полное описание проверяемой записи').value,'手工修改，不可丢失。');assert.equal(h.control.activity().dirty,true);
 response.events[0].payload.fields.price=70000;await h.control.refresh();assert.equal(h.label('Полное описание проверяемой записи').value,'手工修改，不可丢失。');assert.equal(h.label('Я сверил весь текст этой записи с её исходными фактами').checked,false);assert.match(h.mount.textContent,/Исходные факты обновились/);assert.equal(h.button('Сохранить сверенный текст для обучения').disabled,true);
});

test('clear removes private review drafts and a late save response cannot restore them',async t=>{
 const pending=deferred(),h=harness(t,{handle:(_path,data)=>data?.action==='record-grounded'?pending.promise:undefined});await loadPair(h);const save=h.button('Сохранить проверенную пару').onclick();await tick();assert.equal(h.control.activity().busy,true);
 h.control.clear();assert.equal(h.label('Полное проверенное описание пары').value,'');assert.doesNotMatch(h.mount.textContent,/Без животных|PRIVATE_TOKEN/);pending.resolve({event:{id:'stored'}});await save;
 assert.equal(h.label('Полное проверенное описание пары').value,'');assert.deepEqual(h.control.activity(),{dirty:false,busy:false});
});

test('activity distinguishes an unapplied model draft from an applied draft and later manual edits',async t=>{
 const h=harness(t);await h.button('Создать ИИ-черновик по карточке').onclick();assert.equal(h.control.activity().dirty,true);await h.button('Применить проверенный черновик').onclick();assert.equal(h.applied.length,1);assert.equal(h.control.activity().dirty,false);
 fill(h.label('Описание · редактируемый ИИ-черновик'),'Новая ручная версия');assert.equal(h.control.activity().dirty,true);h.control.clear();assert.equal(h.control.activity().dirty,false);
});

test('plain descriptions remain style memory and do not create grounded training examples',async t=>{
 const h=harness(t);const kind=h.label('Что запомнить');kind.value='description';kind.onchange();fill(h.label('Текст примера / предметы через запятую'),'Ручной пример стиля без исходных фактов.');check(h.label('Я проверил содержание и разрешаю использовать его'));
 assert.equal(h.control.activity().dirty,true);await h.button('Запомнить').onclick();const written=h.calls.find(row=>row.data?.action==='record').data;assert.equal(written.kind,'description');assert.equal(written.status,'approved');assert.equal(h.calls.some(row=>row.data?.action==='record-grounded'),false);assert.equal(h.control.activity().dirty,false);assert.match(h.mount.querySelector('.learning-advertising-status').textContent,/4 \/ 20/);
});
