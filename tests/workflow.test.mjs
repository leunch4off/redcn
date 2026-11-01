import test from 'node:test';
import assert from 'node:assert/strict';
import {WORKFLOW_STATES,normalizeWorkflow,workflowBoard,workflowMissingFields,updateWorkflowCard,prepareWorkflowBulk,applyWorkflowBulk,workflowDuplicateCandidates,prepareWorkflowLinks,confirmWorkflowLinks,unlinkWorkflowCards,normalizeWorkflowMetric,addWorkflowMetric,workflowTagPerformance} from '../web/workflow.mjs';

const card=(id,platform='cian',extra={})=>({id,title:'房源 '+id,url:platform==='cian'?'https://www.cian.ru/rent/flat/'+id+'/':'https://www.avito.ru/moskva/kvartiry/test_'+id,address:'Москва, Улица Лесная, 12',rooms:'2',area:'52',price:'80000',deposit:'40000',metro:'Сокол',audience:'general',pickup:false,support:true,arrival:false,luggage:false,registration:'assist',output:'Авторский текст KEEP',tags:'#莫斯科租房 #莫斯科生活',source:'PRIVATE_SOURCE_KEEP',photos:['ORIGINAL_KEEP'],...extra});
const metric=(extra={})=>({postId:'post-1',source:'https://www.xiaohongshu.com/explore/post-1',day:'2026-11-01',kind:'daily',tags:['莫斯科租房','莫斯科生活'],views:100,leads:5,...extra});

test('five operator states remain separate from the source publication status',()=>{
 const rows=Object.keys(WORKFLOW_STATES).map((status,index)=>updateWorkflowCard(card(String(index),'avito',{publicationStatus:'archived'}),{status}));
 const board=workflowBoard(rows);for(const key of Object.keys(WORKFLOW_STATES))assert.equal(board[key].length,1);
 assert(rows.every(row=>row.publicationStatus==='archived'));
 assert.throws(()=>normalizeWorkflow({status:'active'}),/статус/);
 assert.equal(normalizeWorkflow({}).status,'new');
});
test('assignee deadlines and missing-data tasks persist without modifying apartment evidence',()=>{
 const original=card('a'),before=structuredClone(original),saved=updateWorkflowCard(original,{assignee:'Анна',dueDate:'2026-11-04',tasks:[{id:'task-a',text:'Уточнить этаж по источнику',done:false,assignee:'Пётр',dueDate:'2026-11-03'}]},'Оператор Анна','2026-11-01T00:00:00Z');
 const restored=normalizeWorkflow(JSON.parse(JSON.stringify(saved.workflow)));
 assert.equal(restored.assignee,'Анна');assert.equal(restored.updatedBy,'Оператор Анна');assert.equal(restored.tasks[0].dueDate,'2026-11-03');
 const done=updateWorkflowCard(saved,{tasks:restored.tasks.map(row=>({...row,done:true}))});assert.equal(done.workflow.tasks[0].done,true);
 assert.deepEqual(original,before);assert.equal(saved.source,before.source);assert.deepEqual(saved.photos,before.photos);
 const missing=workflowMissingFields(card('b','avito',{price:'',area:'',address:'',metro:'',pricePeriod:'unknown',sourceReadiness:{state:'partial'}}));
 for(const clue of ['аренда','площадь','Станция','Адрес','валюта','неполные'])assert(missing.some(row=>row.includes(clue)),clue);
});
test('invalid deadlines duplicate task IDs and capacity overflow cannot silently discard tasks',()=>{
 for(const value of [{dueDate:'2026-02-30'},{tasks:[{id:'a',text:'Проверить',dueDate:'2026-02-30'}]},{tasks:[{id:'a',text:'Один'},{id:'a',text:'Два'}]},{tasks:Array.from({length:101},(_,i)=>({id:String(i),text:'Проверить'}))},{tasks:'wrong'}])assert.throws(()=>normalizeWorkflow(value));
});
test('bulk preview shows service and actual audience changes, leaves unknown data untouched until confirmation',()=>{
 const original=[card('a','cian',{workflow:{status:'published'}}),card('b','avito')],before=structuredClone(original);
 const preview=prepareWorkflowBulk(original,['a','b'],{pickup:true,audience:'student',registration:'included'});
 assert.deepEqual(original,before);assert.equal(preview.rows[0].beforeStatus,'published');assert.equal(preview.rows[0].afterStatus,'verified');
 assert(preview.rows[0].changes.some(row=>row.key==='pickup'&&row.before===false&&row.after===true));
 const applied=applyWorkflowBulk(original,preview,'Проверяющий');
 assert.equal(applied[0].pickup,true);assert.equal(applied[0].audience,'student');assert.equal(applied[0].registration,'included');assert.equal(applied[0].workflow.status,'verified');assert.equal(applied[0].workflow.needsRegeneration,true);
 assert.equal(applied[0].output,before[0].output);assert.equal(applied[0].tags,before[0].tags);assert.equal(applied[0].price,before[0].price);assert.equal(applied[0].source,before[0].source);assert.deepEqual(original,before);
});
test('bulk editing nested server-shaped records preserves the envelope and unselected records',()=>{
 const a={id:'a',fields:card('fields-a'),body:'手工正文',unrelated:{private:'KEEP'}},b={id:'b',fields:card('fields-b')};const cards=[a,b],preview=prepareWorkflowBulk(cards,['a'],{support:false});
 const changed=applyWorkflowBulk(cards,preview);assert.equal(changed[0].fields.support,false);assert.equal(changed[0].body,'手工正文');assert.deepEqual(changed[0].unrelated,a.unrelated);assert.equal(changed[1],b);assert.equal(a.fields.support,true);
});
test('stale or tampered bulk proposals never overwrite a changed card, arbitrary fields are rejected',()=>{
 const cards=[card('a')],preview=prepareWorkflowBulk(cards,['a'],{pickup:true});
 assert.throws(()=>applyWorkflowBulk([card('a','cian',{pickup:true})],preview),/изменились/);
 assert.throws(()=>applyWorkflowBulk([card('a','cian',{price:'90000'})],preview),/изменились/);
 const changed=structuredClone(preview);changed.rows[0].changes[0].after=false;assert.throws(()=>applyWorkflowBulk(cards,changed),/изменились/);
 for(const patch of [{price:'1'},{pickup:'yes'},{registration:'none'},{audience:'unverified'},{}])assert.throws(()=>prepareWorkflowBulk(cards,['a'],patch));
 assert.throws(()=>prepareWorkflowBulk(cards,[],{pickup:true}));assert.throws(()=>prepareWorkflowBulk(cards,['missing'],{pickup:true}));
});
test('cross-source duplicate suggestions require an explicit address and independent matching facts',()=>{
 const a=card('a'),b=card('b','avito',{area:'52.3'}),c=card('c','avito',{address:'Другой дом'}),d=card('d','cian');
 const candidates=workflowDuplicateCandidates([a,b,c,d]);assert(candidates.some(row=>row.ids.join(',')==='a,b'));assert(!candidates.some(row=>row.ids.includes('c')));assert(!candidates.some(row=>row.ids.join(',')==='a,d'));
 assert.equal(workflowDuplicateCandidates([card('e','cian',{address:''}),card('f','avito',{address:''})]).length,0);
 assert.equal(workflowDuplicateCandidates([a,card('g','avito',{rooms:'3'})]).length,0);
 assert.equal(a.workflow,undefined);
});
test('duplicate linking is explicit reciprocal and reversible without merging facts or media',()=>{
 const cards=[card('a'),card('b','avito')],before=structuredClone(cards),preview=prepareWorkflowLinks(cards,['a','b']);
 assert.throws(()=>confirmWorkflowLinks(cards,preview),/Подтвердите/);
 const linked=confirmWorkflowLinks(cards,preview,{confirmed:true,actor:'Анна'});
 assert.equal(linked[0].workflow.links[0].id,'b');assert.equal(linked[1].workflow.links[0].id,'a');assert.equal(linked[0].workflow.links[0].by,'Анна');
 assert.equal(linked[0].source,before[0].source);assert.deepEqual(linked[0].photos,before[0].photos);assert.deepEqual(cards,before);
 const again=confirmWorkflowLinks(linked,prepareWorkflowLinks(linked,['a','b']),{confirmed:true});assert.equal(again[0].workflow.links.length,1);
 const unlinked=unlinkWorkflowCards(linked,'a','b');assert.equal(unlinked[0].workflow.links.length,0);assert.equal(unlinked[1].workflow.links.length,0);assert.equal(linked[0].workflow.links.length,1);
});
test('duplicate comparison rejects same-platform pairs and stale source changes',()=>{
 const cards=[card('a'),card('b','avito')],preview=prepareWorkflowLinks(cards,['a','b']);assert.throws(()=>confirmWorkflowLinks([cards[0],{...cards[1],area:'54'}],preview,{confirmed:true}),/изменились/);
 assert.throws(()=>prepareWorkflowLinks([card('a'),card('b')],['a','b']),/разных источников/);
});
test('real post counts retain source date and immutable tags while blank differs from zero',()=>{
 const raw=card('a'),saved=addWorkflowMetric(raw,metric({views:0,leads:''}));
 assert.equal(saved.workflow.metrics[0].views,0);assert.equal(saved.workflow.metrics[0].leads,null);assert.equal(saved.workflow.metrics[0].day,'2026-11-01');assert.equal(saved.workflow.metrics[0].source,metric().source);
 const modified={...saved,tags:'#ДругиеТеги'},report=workflowTagPerformance([modified]);assert(report.some(row=>row.tag==='莫斯科租房'));assert(!report.some(row=>row.tag==='ДругиеТеги'));assert.equal(report[0].views,0);assert.equal(report[0].leads,null);assert.equal(raw.workflow,undefined);
});
test('same post/day/source observation replaces counters and cross-card copy cannot double them',()=>{
 let a=addWorkflowMetric(card('a'),metric());a=addWorkflowMetric(a,metric({views:120,leads:6}));assert.equal(a.workflow.metrics.length,1);
 const copy={...structuredClone(a),id:'b'},report=workflowTagPerformance([a,copy]);for(const row of report){assert.equal(row.posts,1);assert.equal(row.views,120);assert.equal(row.leads,6);}
});
test('correcting tags for the same measured post does not duplicate its counters',()=>{
 let a=addWorkflowMetric(card('a'),metric());a=addWorkflowMetric(a,metric({tags:['莫斯科租房','ДругиеТеги'],views:120,leads:6}));assert.equal(a.workflow.metrics.length,1);const report=workflowTagPerformance([a]);assert.equal(report.find(row=>row.tag==='莫斯科租房').views,120);assert(!report.some(row=>row.tag==='莫斯科生活'));
});
test('conflicting duplicated observations are visible and never silently summed or selected',()=>{
 const a=addWorkflowMetric(card('a'),metric()),b=addWorkflowMetric(card('b'),metric({views:130})),report=workflowTagPerformance([a,b]);for(const row of report){assert.equal(row.views,null);assert.equal(row.leads,null);assert(row.notes.some(note=>note.includes('противоречивые')));}
});
test('explicit housing classification persists without inferring dormitory from nearby text',()=>{
 const a=card('a','cian',{source:'Рядом общежитие'});assert.equal(normalizeWorkflow({}).housingType,'unknown');const b=updateWorkflowCard(a,{housingType:'apartment'});assert.equal(b.workflow.housingType,'apartment');assert.equal(a.workflow,undefined);assert.throws(()=>normalizeWorkflow({housingType:'near-campus'}));
});
test('daily and cumulative counters remain separate; lone or decreasing cumulative values are unknown',()=>{
 let a=addWorkflowMetric(card('a'),metric({kind:'cumulative',views:1000,leads:30}));
 for(const row of workflowTagPerformance([a])){assert.equal(row.views,null);assert.equal(row.leads,null);}
 a=addWorkflowMetric(a,metric({day:'2026-11-02',kind:'cumulative',views:1200,leads:40}));
 for(const row of workflowTagPerformance([a])){assert.equal(row.views,200);assert.equal(row.leads,10);}
 a=addWorkflowMetric(a,metric({day:'2026-11-03',kind:'cumulative',views:100,leads:2}));for(const row of workflowTagPerformance([a]))assert.equal(row.views,null);
 a=addWorkflowMetric(a,metric({day:'2026-11-03',views:25,leads:1}));const report=workflowTagPerformance([a]);assert(report.some(row=>row.kind==='daily'&&row.views===25));assert(report.some(row=>row.kind==='cumulative'&&row.views===null));
});
test('different post sources stay distinct and never invent causal per-tag attribution',()=>{
 let a=addWorkflowMetric(card('a'),metric());a=addWorkflowMetric(a,metric({postId:'post-2',source:'https://example.org/posts/2',views:40,leads:2,tags:['莫斯科租房']}));const rows=workflowTagPerformance([a]);const common=rows.find(row=>row.tag==='莫斯科租房'),other=rows.find(row=>row.tag==='莫斯科生活');assert.equal(common.posts,2);assert.equal(common.views,140);assert.equal(other.views,100);assert.equal(common.leads,7);
});
test('invalid or unsupported statistics never become measurements',()=>{
 for(const extra of [{views:-1},{views:1.1},{leads:'NaN'},{day:'2026-02-30'},{source:'javascript:alert(1)'},{source:'https://user:secret@example.org/'},{postId:''},{tags:[]},{tags:[{}]},{views:'',leads:''},{kind:'weekly'}])assert.throws(()=>normalizeWorkflowMetric(metric(extra)),JSON.stringify(extra));
});
