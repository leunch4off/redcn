import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {readSourceHtml,sourceFieldFacts} from '../web/source-import.mjs';
import {analyzeListingSource,generateListing} from '../web/engine.mjs';
import {findFactConflicts,sourceFactEvidence} from '../web/fact-ledger.mjs';

const cianUrl='https://www.cian.ru/rent/flat/10000001/';
const avitoUrl='https://www.avito.ru/moskva/kvartiry/test_10000001';
function cian({domPrice='75 000 ₽ в месяц',ldPrice='72000',ldCurrency='RUB',ldPeriod='P1M',ldArea=51,extra=''}={}){
 const offer={cianId:10000001,roomsCount:2,totalArea:52,bargainTerms:{price:80000,paymentPeriod:'monthly',currency:'rur',deposit:40000,clientFee:50}};
 return '<h1 data-name="OfferTitle">2-комнатная квартира 52 м²</h1><div data-name="PriceInfo">'+domPrice+'</div>'+extra+'<script type="application/ld+json">'+JSON.stringify({'@type':'Apartment',floorSize:{value:ldArea},offers:{price:ldPrice,priceCurrency:ldCurrency,priceSpecification:{billingDuration:ldPeriod}}})+'</script><script>window._cianConfig["frontend-offer-card"]=[].concat('+JSON.stringify([{key:'defaultState',value:{offerData:{offer}}}])+');</script>';
}
function avito({prices=['80 000 ₽ в месяц','75 000 ₽ в месяц'],area='54'}={}){
 return '<link rel="canonical" href="'+avitoUrl+'"><h1 data-marker="item-view/title-info">2-к. квартира, 52 м²</h1>'+prices.map(price=>'<div data-marker="item-view/item-price">'+price+'</div>').join('')+'<section data-marker="item-view/item-params"><h2>О квартире</h2><ul><li>Общая площадь: '+area+' м²</li><li>Количество комнат: 2</li></ul></section>';
}
const rows=(facts,field)=>facts.sourceAudit.fields.filter(row=>row.field===field);
const values=(facts,field)=>rows(facts,field).map(row=>row.value);

test('actual CIAN adapter→engine→ledger retains JSON, JSON-LD and DOM values without changing winners',()=>{
 const markup=cian(),source=readSourceHtml(markup),adapter=sourceFieldFacts(source),analyzed=analyzeListingSource(source),facts=analyzed.facts;
 assert.equal(adapter.facts.price,'80000');assert.equal(facts.price,'80000');assert.equal(facts.area,'52');
 assert.deepEqual(values(facts,'price'),['80000','72000','75000']);assert.deepEqual(values(facts,'area'),['52','51']);
 assert(rows(facts,'price').every(row=>row.sourceUrl===cianUrl&&row.currency==='RUB'&&row.period==='month'));
 assert.deepEqual(new Set(rows(facts,'price').map(row=>row.source)),new Set(['JSON bargainTerms.price','JSON-LD price','PriceInfo']));
 assert(adapter.conflicts.some(row=>row.field==='price'&&row.candidates.length===3));
 assert(analyzed.warnings.some(text=>/price.*проверка/u.test(text)));assert(analyzed.warnings.some(text=>/area.*проверка/u.test(text)));
 const conflicts=findFactConflicts(facts);assert(conflicts.some(row=>row.field==='price'&&row.kind==='sources'&&row.values.length===3));assert(conflicts.some(row=>row.field==='area'&&row.kind==='sources'));
 assert(findFactConflicts({...facts,price:'81000'}).some(row=>row.field==='price'&&row.kind==='manual'));
 const generated=generateListing(facts);assert(generated.body.includes('80,000₽'));assert(!generated.body.includes('75,000₽'));assert.equal(facts.price,'80000');
});

test('actual Avito adapter→engine→ledger retains conflicting price and area rows instead of dropping them',()=>{
 const source=readSourceHtml(avito(),{url:avitoUrl}),adapter=sourceFieldFacts(source),{facts,warnings}=analyzeListingSource(source);
 assert.equal(adapter.facts.price,'80000');assert.equal(facts.price,'80000');assert.equal(facts.area,'52');
 assert.deepEqual(values(facts,'price'),['80000','75000']);assert.deepEqual(values(facts,'area'),['52','54']);
 assert(rows(facts,'price').every(row=>row.sourceUrl===avitoUrl&&row.currency==='RUB'&&row.period==='month'));
 for(const field of ['price','area']){assert(adapter.conflicts.some(row=>row.field===field));assert(warnings.some(text=>text.includes(field)&&text.includes('проверка')));assert(findFactConflicts(facts).some(row=>row.field===field&&row.kind==='sources'));}
 assert(findFactConflicts({...facts,price:'81000'}).some(row=>row.field==='price'&&row.kind==='manual'));
 assert(generateListing(facts).body.includes('80,000₽'));
});

test('rate witnesses retain own units and unrelated periods or currencies are never one price conflict',()=>{
 for(const markup of [cian({domPrice:'80 000 ₽ в месяц',ldPrice:'1500',ldCurrency:'USD',ldPeriod:'P1D',ldArea:52}),avito({prices:['80 000 ₽ в месяц','1 500 USD в сутки'],area:'52'})]){
  const source=readSourceHtml(markup,{url:markup.includes('data-marker')?avitoUrl:cianUrl}),{facts}=analyzeListingSource(source),conflicts=findFactConflicts(facts);
  assert.equal(facts.price,'80000');assert.equal(facts.priceCurrency,'RUB');assert.equal(facts.pricePeriod,'month');
  assert(rows(facts,'price').some(row=>row.value==='1500'&&row.currency==='USD'&&row.period==='day'));
  assert(!facts.sourceAudit.conflicts.some(row=>row.field==='price'));assert(!conflicts.some(row=>row.field==='price'&&row.kind==='sources'));
  assert(conflicts.some(row=>row.field==='priceCurrency'));assert(conflicts.some(row=>row.field==='pricePeriod'));
  const provenance=sourceFactEvidence({...facts,price:'1500'}).find(row=>row.field==='price');assert.equal(provenance.origin,'manual');
 }
});

test('numeric spelling equality and existing CIAN blocked-field policy survive witness retention',()=>{
 const equal=analyzeListingSource(cian({domPrice:'80 000 ₽ в месяц',ldPrice:'80000.0',ldArea:52})).facts;
 assert(!equal.sourceAudit.conflicts.some(row=>['price','area'].includes(row.field)));
 assert(!findFactConflicts(equal).some(row=>['price','area'].includes(row.field)&&row.kind==='sources'));
 const facts=analyzeListingSource(cian({extra:'<div data-name="OfferSummaryInfoItem">Общая площадь: 54 м²</div><div data-name="OfferFactItem">Залог: 30 000 ₽</div><div data-name="OfferFactItem">Комиссия с арендатора: 35 %</div>'})).facts;
 assert.equal(facts.area,'');assert(facts.sourceAudit.blockedFields.includes('area'));assert(values(facts,'area').includes('54'));
 assert.deepEqual(values(facts,'deposit'),['40000','30000']);assert.deepEqual(values(facts,'commission'),['50','35']);
});

test('CLI source analyzer receives the same actual witness conflicts for both adapters',()=>{
 for(const [markup,url,expected]of[[cian(),cianUrl,['80000','72000','75000']],[avito(),avitoUrl,['80000','75000']]]){
  const result=spawnSync(process.execPath,['cli/analyze-source.mjs',url],{input:markup,encoding:'utf8',cwd:new URL('..',import.meta.url)});
  assert.equal(result.status,0,result.stderr);const payload=JSON.parse(result.stdout);
  assert.deepEqual(values(payload.fields,'price'),expected);assert(payload.fields.sourceAudit.conflicts.some(row=>row.field==='price'));
 }
});
