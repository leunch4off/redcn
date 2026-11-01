import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {sanitizeClientText,containsClientCommission,clientPublicationCard,ClientPublicationReviewRequired,PUBLICATION_POLICY_VERSION} from '../web/client-publication.mjs';
import {generateListing} from '../web/engine.mjs';
const cases=JSON.parse(await readFile(new URL('fixtures/client-publication-policy.json',import.meta.url),'utf8'));
test('one public policy removes isolated fee clauses while keeping all other financial facts',()=>{
 for(const row of cases){if(row.review){assert.throws(()=>sanitizeClientText(row.input),error=>error instanceof ClientPublicationReviewRequired&&row.input.includes(error.fragment));continue;}assert.equal(sanitizeClientText(row.input),row.output,row.input);assert(!containsClientCommission(row.output));}
});
test('old/raw/manual cards are projected without changing the original and without private fee metadata',()=>{
 const card={id:'a',titles:['好房'],body:'租金65000\n中介费50%\n押金35000',tags:'#莫斯科 #免佣',fields:{price:'65000',deposit:'35000',commission:50,clientCommissionPct:50,clientFee:50,source:'Комиссия50%',sourceAudit:{private:'KEEP'},description:'押金35000\n佣金50%',photos:['https://images.cdn-cian.ru/images/room.jpg']}};
 const before=JSON.stringify(card),out=clientPublicationCard(card);assert.equal(JSON.stringify(card),before);assert.equal(out.body,'租金65000\n押金35000');assert.equal(out.tags,'#莫斯科');assert.equal(out.fields.price,'65000');assert.equal(out.fields.deposit,'35000');for(const key of ['commission','clientCommissionPct','clientFee','source','sourceAudit'])assert(!(key in out.fields),key);assert.equal(out.publicationPolicyVersion,PUBLICATION_POLICY_VERSION);assert.deepEqual(clientPublicationCard(out),out);
});
test('all styles and null/zero/missing/positive commission values never create a commission promise',()=>{
 for(const style of ['compact','detailed','story'])for(const commission of [undefined,null,'',0,'0',50,'50'])for(let variant=0;variant<20;variant++){
  const r=generateListing({rooms:'2',price:'65000',deposit:'35000',commission,area:'52',style,features:['fridge'],copyVariant:variant,titleVariant:variant,tagVariant:variant,templateMode:'catalog',registration:'assist'});
  assert.deepEqual(r.problems,[]);assert(!containsClientCommission([r.titles.join('\n'),r.body,r.tags].join('\n')));assert.match(r.body,/6.5W|65,000/);assert.match(r.body,/3.5W|35,000/);
 }
});

test('legacy null public text normalizes safely without changing private shape',()=>{const raw={body:null,tags:null,titles:null};assert.deepEqual(clientPublicationCard(raw),{body:'',tags:'',titles:[],publicationPolicyVersion:PUBLICATION_POLICY_VERSION});assert.deepEqual(raw,{body:null,tags:null,titles:null});});
test('unresolved source conflicts block stale claims but allow clean publication omitting them',()=>{
 const fields={deposit:'',buildingType:'',sourceAudit:{blockedFields:['deposit','buildingType'],conflicts:[{field:'deposit'}]}};
 for(const body of ['押金：35,000₽','кирпичный дом','板式住宅'])assert.throws(()=>clientPublicationCard({fields,body}),error=>error instanceof ClientPublicationReviewRequired&&error.message.includes('противоречивое'));
 assert.equal(clientPublicationCard({fields,body:'租金65000\n52㎡'}).body,'租金65000\n52㎡');assert.equal(clientPublicationCard({fields,body:'押金待确认'}).body,'押金待确认');
 assert.throws(()=>clientPublicationCard({fields:{...fields,deposit:'35000'},body:'租金65000'}),ClientPublicationReviewRequired);
});

test('internal listing envelope is reduced to property/photo identity rather than raw source text',()=>{const raw={body:'租金65000',listing:{url:'https://www.cian.ru/rent/flat/1/',images:['https://images.cdn-cian.ru/images/one.jpg'],text:'Квартира52м² комиссия50%',fields:{commission:50},metadata:{fee:'SOURCE_PRIVATE'}}};const safe=clientPublicationCard(raw);assert.deepEqual(safe.listing,{url:raw.listing.url,images:raw.listing.images});assert.equal(raw.listing.text,'Квартира52м² комиссия50%');});
