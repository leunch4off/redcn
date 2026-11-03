import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {readFileSync} from 'node:fs';
import {generateListing,DEFAULT_PREFS,extractFacts} from '../web/engine.mjs';
import {serviceClaimChecklist,factLedgerKey} from '../web/fact-ledger.mjs';
import {clientPublicationCard,ClientPublicationReviewRequired,assertClientServiceClaims} from '../web/client-publication.mjs';
const prefs={...DEFAULT_PREFS,benefits:'✅ 免费接送到家\n✅ 中文沟通'};
const sharedCases=JSON.parse(readFileSync(new URL('./fixtures/service-claim-cases.json',import.meta.url),'utf8'));
const fields=(extra={})=>({editorIdentity:'service-a',price:'80000',area:'52',rooms:'2',features:[],registrationMonths:'6',registrationPeople:'3',pickup:true,luggage:true,arrival:true,support:true,serviceBenefits:prefs.benefits,...extra});
function confirmed(value){const at=new Date(Date.now()-1000).toISOString(),cardKey=factLedgerKey(value);return {...value,serviceConfirmations:Object.fromEntries(serviceClaimChecklist(value,{prefs}).claims.map(row=>[row.id,{value:row.value,actor:'PRIVATE_OPERATOR',at,cardKey}]))};}
function rendered(value){return {fields:value,...generateListing(value,undefined,prefs)};}
test('shared multilingual final-boundary corpus agrees with dated-scope policy',()=>{
 for(const row of sharedCases){const card={fields:row.confirmed?confirmed(fields()):fields(),body:row.body,titles:['公寓'],tags:''};if(row.allowed)assert.doesNotThrow(()=>clientPublicationCard(card),row.name);else assert.throws(()=>clientPublicationCard(card),ClientPublicationReviewRequired,row.name);}
});

test('generation and CLI keep neutral assistance but never default free services or unverified registration scope',()=>{
 const value=fields(),result=rendered(value);
 assert.match(result.body,/抵达登记（临时居住登记）：提供办理协助/);
 assert.doesNotMatch(result.body,/免费|6个月|最多3人|我们的优势/);
 const cli=spawnSync(process.execPath,['cli/generate.mjs'],{input:JSON.stringify({data:value,prefs}),encoding:'utf8',cwd:new URL('..',import.meta.url)});
 assert.equal(cli.status,0,cli.stderr);const payload=JSON.parse(cli.stdout);
 assert.doesNotMatch(payload.body,/免费|6个月|最多3人|我们的优势/);assert.equal(payload.fields.serviceBenefits,prefs.benefits);
 const unsafe=generateListing({...value,description:'免费接送到家。'},undefined,prefs);assert(unsafe.problems.length);assert.equal(unsafe.body,'');
});

test('dated current-card receipts enable exact selected service scope for generation and final client projection',()=>{
 const value=confirmed(fields()),card=rendered(value);
 assert.match(card.body,/6个月/);assert.match(card.body,/最多3人/);assert.match(card.body,/免费接送到入住地点/);assert.match(card.body,/免费协助了解抵达/);assert.match(card.body,/我们的优势/);
 const before=JSON.stringify(card),projected=clientPublicationCard(card);
 assert.match(projected.body,/免费接送/);assert(!('serviceBenefits'in projected.fields));assert(!('serviceConfirmations'in projected.fields));assert(!JSON.stringify(projected).includes('PRIVATE_OPERATOR'));assert.equal(JSON.stringify(card),before);
 for(const studioProfile of [{emoji:'none',punctuation:'compact'},{}])assert.doesNotThrow(()=>clientPublicationCard(rendered({...value,studioProfile})));
 assert.doesNotThrow(()=>clientPublicationCard(JSON.parse(JSON.stringify(projected))));
 assert.doesNotThrow(()=>clientPublicationCard({...rendered({...value,resultId:'a'.repeat(32)})}));
 const changed=JSON.parse(JSON.stringify(projected));changed.body+='\n免费住宿登记。';assert.throws(()=>clientPublicationCard(changed),ClientPublicationReviewRequired);
});

test('expired future wrong-card changed-scope and undated receipts cannot authorize service promises',()=>{
 const value=confirmed(fields());
 const mutations=[row=>({...row,at:new Date(Date.now()-31*86400000).toISOString()}),row=>({...row,at:new Date(Date.now()+3600000).toISOString()}),row=>({...row,at:row.at.replace('Z','')}),row=>({...row,actor:''}),row=>({...row,cardKey:'editorIdentity:other'}),row=>({...row,value:{...row.value,extra:true}})];
 for(const mutate of mutations){const altered={...value,serviceConfirmations:Object.fromEntries(Object.entries(value.serviceConfirmations).map(([id,row])=>[id,mutate(row)]))};assert.doesNotMatch(rendered(altered).body,/免费|6个月|最多3人|我们的优势/);assert.throws(()=>clientPublicationCard({...rendered(value),fields:altered}),ClientPublicationReviewRequired);}
 for(const patch of [{registrationPeople:'4'},{registrationMonths:'3'}])assert.doesNotMatch(rendered({...value,...patch}).body,/6个月|最多3人/);
 assert.doesNotMatch(generateListing({...value},undefined,{...prefs,benefits:'服务内容已改变'}).body,/我们的优势/);
});

test('legacy copy/export projections reject unsupported service promises and preserve private text',()=>{
 for(const body of ['免费接送到家','免费协助了解抵达后的相关手续。','免费住宿登记','抵达登记（临时居住登记）：提供办理协助（6个月），最多3人。','🇨🇳 我们的优势：\n'+prefs.benefits]){
  const card={fields:fields(),body,titles:['公寓'],tags:''},before=JSON.stringify(card);
  assert.throws(()=>clientPublicationCard(card),ClientPublicationReviewRequired,body);assert.equal(JSON.stringify(card),before);
 }
 const value=confirmed(fields());assert.throws(()=>clientPublicationCard({...rendered(value),body:'免费住宿登记'}),ClientPublicationReviewRequired);
 assert.throws(()=>clientPublicationCard({...rendered(value),body:'抵达登记（临时居住登记）：提供办理协助（12个月）。'}),ClientPublicationReviewRequired);
 assert.doesNotThrow(()=>assertClientServiceClaims({fields:{},body:'抵达登记（临时居住登记）：提供办理协助。'}));
});

test('unseen coordinated objects remain unknown, with independent visible or available objects preserved',()=>{
 for(const text of ['Холодильник не виден на фото.','Диван и кровать не видны на фото.','На фото не видны холодильник, диван и кровать.','Холодильник, диван и кровать не видны на фото.','В кадре не показаны холодильник, диван, стиральная машина.','На фото нет холодильника.','Холодильник not visible in the photo.','Холодильник is missing from the image.','Холодильник is absent from the photograph.','Фото не показывает холодильник.','Холодильник can’t see in the photo.','看不到 холодильник。','Fridge and sofa are not visible in the photograph.','照片中看不到冰箱和沙发。']){
  const facts=extractFacts(text).facts;for(const id of ['fridge','sofa','bedAny','washer']){assert(!facts.features.includes(id),text);assert.notEqual(facts.sourceAudit.furniture.find(row=>row.id===id).status,'absent',text);}
  const body=generateListing({...fields(),...facts}).body;assert.doesNotMatch(body,/冰箱|沙发|洗衣机|双人床|未配备|没有冰箱|无冰箱/);
 }
 for(const text of ['На фото нет холодильника, зато есть диван.','Холодильник not visible in photo, диван есть.','看不到 холодильник，但 есть диван。']){const facts=extractFacts(text).facts;assert(facts.features.includes('sofa'),text);assert(!facts.features.includes('fridge'),text);}
 assert.deepEqual(extractFacts('Холодильник есть и диван не виден.').facts.features,['fridge']);
 for(const text of ['Кухня оборудована холодильником, диван не виден на фото.','Холодильник установлен на кухне, диван не виден на фото.','Диван не виден на фото, холодильник установлен на кухне.'])assert.deepEqual(extractFacts(text).facts.features,['fridge'],text);
});
