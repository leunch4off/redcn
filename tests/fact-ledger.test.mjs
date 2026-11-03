import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {spawnSync} from 'node:child_process';
import {validateListingFields,assertListingFields,compareFactSnapshots,createFactRevision,sourceFactEvidence,findFactConflicts,factLedgerKey,serviceClaimChecklist} from '../web/fact-ledger.mjs';

const at='2026-11-01T10:00:00.000Z';
const cases=JSON.parse(await readFile(new URL('fixtures/field-schema-cases.json',import.meta.url),'utf8'));
test('shared schema validates known field shapes while retaining legacy and extensible JSON data',()=>{
 for(const row of cases){const before=JSON.stringify(row.fields),result=validateListingFields(row.fields);assert.equal(result.valid,row.valid,row.name+': '+result.problems);assert.equal(JSON.stringify(row.fields),before);}
 assert(validateListingFields({commission:undefined,price:'80000'}).valid);
});
test('schema rejects non-JSON values, cycles, nonfinite amounts and nested code without coercion',()=>{
 const cyclic={price:80000};cyclic.self=cyclic;
 for(const value of [[],null,cyclic,{price:NaN},{price:Infinity},{price:{toString:()=>80000}},{source:()=>'<html>'},{features:[undefined]}])assert.equal(validateListingFields(value).valid,false);
 assert.throws(()=>assertListingFields({price:[80000]}),/price/);
});
test('fact comparison has literal price and condition changes without false number or feature ordering changes',()=>{
 const changes=compareFactSnapshots({price:'80000',deposit:0,petPolicy:'yes',features:['fridge','desk'],featureCounts:{chairs:'2'}},{price:85000,deposit:'0',petPolicy:'no',features:['desk','fridge'],featureCounts:{chairs:2}});
 assert.deepEqual(changes.map(row=>row.field),['price','petPolicy']);assert.equal(changes[0].before,'80000');assert.equal(changes[0].after,85000);
 assert.deepEqual(compareFactSnapshots({price:'80 000',walk:8},{price:80000,walk:'8'}),[]);
 assert.deepEqual(compareFactSnapshots({price:'по запросу'},{price:'Аренда 80000 ₽'}).map(row=>row.field),['price']);
 assert.deepEqual(compareFactSnapshots({price:'1 2'},{price:'12'}).map(row=>row.field),['price']);
});
test('CIAN repeated source snapshot preserves identity and records authors without retaining raw private fields',()=>{
 const first=createFactRevision({url:'https://www.cian.ru/rent/flat/1/?tracking=old',price:'80000',deposit:'40000',petPolicy:'no',source:'PRIVATE_HTML',operatorNotes:'PRIVATE_NOTE',photos:['PRIVATE_PHOTO'],auth:'PRIVATE_AUTH'},{actor:'Евгений',kind:'import',at});
 const second=createFactRevision({url:'https://www.cian.ru/rent/flat/1/?tracking=new',price:'85000',deposit:'30000',petPolicy:'yes'},{actor:'Редактор',kind:'import',previous:first,at:'2026-11-01T11:00:00.000Z'});
 assert.equal(first.key,second.key);assert.equal(second.revision,2);assert.equal(second.actor,'Редактор');assert.deepEqual(second.changes.map(row=>row.field),['price','deposit','petPolicy']);
 assert.doesNotMatch(JSON.stringify(first),/PRIVATE_/);assert.equal(first.fields.price,'80000');
 assert.throws(()=>createFactRevision({editorIdentity:'card-a',price:1},{actor:'',at}),/автора/);
 assert.throws(()=>createFactRevision({editorIdentity:'card-a',price:1},{actor:'Agent',at:'2026-11-01T11:00:00'}),/дат/);
});
test('route snapshots include only bounded factual route fields, never nested source HTML or auth',()=>{
 const value=createFactRevision({editorIdentity:'a',universityRoutes:[{name:'МГУ',time:'30',html:'PRIVATE_HTML',stops:[{name:'Остановка',cookie:'PRIVATE_COOKIE'}],segments:[{mode:'bus',transport:'119',html:'PRIVATE_HTML'}],auth:'PRIVATE_AUTH'}]},{actor:'Agent',at});
 assert.deepEqual(value.fields.universityRoutes,[{name:'МГУ',time:'30',stops:[{name:'Остановка'}],segments:[{mode:'bus',transport:'119'}]}]);
});
test('source facts, manual overrides, observed time and explicit operator verification remain separate',()=>{
 const fields={url:'https://www.cian.ru/rent/flat/1/?token=PRIVATE',price:'80000',transit:'Сокол | 5 | walk',features:['fridge'],sourceAudit:{fields:[{field:'price',value:80000,source:'JSON-LD offers.price',text:'80 000 ₽',sourceUrl:'https://www.cian.ru/rent/flat/1/'},{field:'transit',value:'Сокол | 5 | walk',source:'UndergroundItem',text:'Сокол, 5 минут пешком'}],furniture:[{id:'fridge',label:'Холодильник',status:'present',matches:[{source:'Описание',text:'Есть холодильник'}]}]}};
 const rows=sourceFactEvidence(fields,{observedAt:at,confirmed:{price:{value:'80000',actor:'Евгений',at}}});
 const price=rows.find(row=>row.field==='price');assert.equal(price.origin,'source');assert.equal(price.checkedAt,at);assert.equal(price.checkedBy,'Евгений');assert.equal(price.sourceUrl,'https://www.cian.ru/rent/flat/1/');
 const transit=rows.find(row=>row.field==='transit');assert.equal(transit.checkedAt,null);assert.equal(transit.observedAt,at);
 assert.equal(rows.find(row=>row.field==='features.fridge').source,'Описание');
 const manual=sourceFactEvidence({...fields,price:'90000'},{confirmed:{price:{value:'80000',actor:'Евгений',at}}}).find(row=>row.field==='price');
 assert.equal(manual.origin,'manual');assert.equal(manual.checkedAt,null);assert.equal(manual.sourceUrl,'');
});
test('text/JSON-LD contradictions and current manual differences are displayed as distinct conflict kinds',()=>{
 const fields={price:'90000',buildingType:'Монолитный',features:['fridge'],sourceAudit:{fields:[{field:'price',value:'80000',source:'JSON-LD'},{field:'price',value:'85000',source:'DOM PriceInfo'},{field:'buildingType',value:'Кирпичный',source:'DOM'}],conflicts:[{field:'deposit',message:'Процент и сумма расходятся'}],furniture:[{id:'fridge',label:'Холодильник',status:'absent',matches:[{source:'Описание',text:'Без холодильника'}]}]}};
 const result=findFactConflicts(fields);assert(result.some(row=>row.field==='price'&&row.kind==='sources'));assert(result.some(row=>row.field==='price'&&row.kind==='manual'));assert(result.some(row=>row.field==='buildingType'&&row.kind==='manual'));assert(result.some(row=>row.field==='deposit'&&row.kind==='source-audit'));assert(result.some(row=>row.field==='features.fridge'&&row.kind==='equipment'));
 assert.equal(fields.price,'90000');
});
test('anonymous card identities stay separate and Node CLI rejects malformed input before generation',()=>{
 assert.notEqual(factLedgerKey({editorIdentity:'a'}),factLedgerKey({editorIdentity:'b'}));assert.equal(factLedgerKey({editorIdentity:'a'}),factLedgerKey({editorIdentity:'a',name:'Renamed'}));
 const result=spawnSync(process.execPath,['cli/generate.mjs'],{input:JSON.stringify({data:{price:[80000]}}),encoding:'utf8',cwd:new URL('..',import.meta.url)});
 assert.equal(result.status,2);assert.match(result.stderr,/price/);assert.equal(result.stdout,'');
});
test('agency service checklist records each current claim independently of landlord registration refusal',()=>{
 const fields={editorIdentity:'service-a',registration:'assist',registrationMonths:'3',registrationPeople:'2',registrationPolicy:'not_available',pickup:true,luggage:true,arrival:true,support:true},prefs={benefits:'中文支持'};
 const initial=serviceClaimChecklist(fields,{prefs,now:at});assert.equal(initial.ready,false);assert.deepEqual(initial.claims.map(row=>row.id),['registration','pickup','arrival','support']);
 const receipts=Object.fromEntries(initial.claims.map(row=>[row.id,{value:row.value,actor:'Евгений',at,cardKey:factLedgerKey(fields)}]));
 const checked=serviceClaimChecklist(fields,{prefs,receipts,now:'2026-11-01T11:00:00.000Z'});assert.equal(checked.ready,true);assert(checked.claims.every(row=>row.checkedAt===at&&row.checkedBy==='Евгений'));
 const scope=serviceClaimChecklist({...fields,registrationPeople:'3',luggage:false},{prefs,receipts,now:at});assert.deepEqual(scope.missing.map(row=>row.id),['registration','pickup']);
 assert.equal(serviceClaimChecklist(fields,{prefs:{benefits:'Новая услуга'},receipts,now:at}).missing[0].id,'support');
 assert.equal(serviceClaimChecklist(fields,{prefs,receipts,now:'2026-12-02T11:00:00.000Z'}).ready,false);
 assert.equal(serviceClaimChecklist(fields,{prefs,receipts,now:'2026-10-31T11:00:00.000Z'}).ready,false);
 assert.equal(serviceClaimChecklist({...fields,editorIdentity:'another-card'},{prefs,receipts,now:at}).ready,false);
 assert.equal(serviceClaimChecklist(fields,{prefs,receipts:Object.fromEntries(Object.entries(receipts).map(([key,row])=>[key,{...row,cardKey:undefined}])),now:at}).ready,false);
});
