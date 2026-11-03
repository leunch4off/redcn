import test from 'node:test';
import assert from 'node:assert/strict';
import {readSourceHtml} from '../web/source-import.mjs';
import {cianFieldFacts} from '../web/source-adapters/cian.mjs';
import {avitoFieldFacts,isAvitoNewCandidate} from '../web/source-adapters/avito.mjs';
import {analyzeListingSource} from '../web/engine.mjs';

// Deliberately synthetic literals: no site request, account, seller profile or
// original image is needed to exercise these independent source contracts.
function cian(offer={},dom=''){
 const config=JSON.stringify([{key:'defaultState',value:{offerData:{offer:{cianId:100000001,...offer}}}}]);
 return readSourceHtml(`<html><body>${dom}<script>window._cianConfig['frontend-offer-card']=[].concat(${config});</script></body></html>`);
}
function avito(rows,extra='',price='80 000 ₽ в месяц'){
 return readSourceHtml(`<h1 data-marker="item-view/title-info">Квартира</h1><div data-marker="item-view/item-price">${price}</div><section data-marker="item-view/item-params"><h2>Условия аренды</h2><ul>${rows.map(text=>`<li>${text}</li>`).join('')}</ul></section>${extra}`,{url:'https://www.avito.ru/moskva/kvartiry/test_100000001'});
}

test('CIAN JSON prices preserve observed period/currency rather than monthly or RUB assumptions',()=>{
 for(const [paymentPeriod,currency,pricePeriod,priceCurrency]of [['monthly','rur','month','RUB'],['daily','usd','day','USD'],['weekly','eur','week','EUR'],[null,null,'unknown','unknown']]){
  const {facts,evidence}=cianFieldFacts(cian({bargainTerms:{price:'12 500,50',paymentPeriod,currency}}));
  assert.equal(facts.price,'12500.5');assert.equal(facts.pricePeriod,pricePeriod);assert.equal(facts.priceCurrency,priceCurrency);
  assert.equal(evidence.find(row=>row.field==='price').source,'JSON bargainTerms.price');
  assert.equal(evidence.find(row=>row.field==='price').sourceUrl,'https://www.cian.ru/rent/flat/100000001/');
 }
 for(const invalid of [null,undefined,true,false,'','unknown',-5])assert.equal(cianFieldFacts(cian({bargainTerms:{price:invalid}})).facts.price,undefined);
});

test('CIAN DOM and inert JSON-LD are separate supported price fixtures',()=>{
 const dom=readSourceHtml('<div data-name="PriceInfo">4 500 ₽/сут.</div><div data-name="OfferFactItem">Залог: 10 000 ₽</div>');
 const daily=cianFieldFacts(dom).facts;assert.equal(daily.price,'4500');assert.equal(daily.pricePeriod,'day');assert.equal(daily.deposit,'10000');
 const ld=readSourceHtml('<script type="application/ld+json">{"@type":"Apartment","floorSize":{"value":43.5},"numberOfRooms":2,"numberOfBedrooms":1,"offers":{"price":"72000","priceCurrency":"RUB","priceSpecification":{"billingDuration":"P1M"}}}</script>');
 const monthly=cianFieldFacts(ld).facts;assert.equal(monthly.price,'72000');assert.equal(monthly.pricePeriod,'month');assert.equal(monthly.priceCurrency,'RUB');assert.equal(monthly.rooms,'2');assert.equal(monthly.bedrooms,'1');assert.equal(monthly.area,'43.5');
});

test('CIAN rooms, bedrooms, living rooms and areas remain independent, with explicit euro layout',()=>{
 const fields=cianFieldFacts(cian({totalArea:'52.5',livingArea:30,kitchenArea:9.5,roomsCount:2,bedroomsCount:1,livingRoomsCount:1,layoutType:'Евро-2'})).facts;
 assert.deepEqual(Object.fromEntries(['area','living','kitchen','rooms','bedrooms','livingRooms','layout'].map(key=>[key,fields[key]])),{area:'52.5',living:'30',kitchen:'9.5',rooms:'2',bedrooms:'1',livingRooms:'1',layout:'euro2'});
 const classical=cianFieldFacts(cian({roomsCount:2})).facts;assert.equal(classical.rooms,'2');assert.equal(classical.bedrooms,undefined);assert.equal(classical.livingRooms,undefined);assert.equal(classical.layout,undefined);
 const badCounts=cianFieldFacts(cian({roomsCount:1.5,bedroomsCount:false,livingRoomsCount:null})).facts;assert.equal(badCounts.rooms,undefined);assert.equal(badCounts.bedrooms,undefined);assert.equal(badCounts.livingRooms,undefined);
 const studio=cianFieldFacts(cian({},'<h1>Квартира-студия, 25 м²</h1>')).facts;assert.equal(studio.layout,'studio');assert.equal(studio.bedrooms,undefined);
 assert.equal(cianFieldFacts(cian({description:'Не студия. Две отдельные комнаты.'})).facts.layout,undefined);
});

test('CIAN each station retains its own time, mode and identity even for identical names',()=>{
 const source=cian({geo:{undergrounds:[{id:1,name:'Тестовая',travelTime:8,travelType:'walk',lineType:'mcd1'},{id:2,name:'Тестовая',travelTime:'15–20',travelType:'transport',lineType:'2'},{id:3,name:'Другая',travelTime:4,travelType:'byCar'},{id:4,name:'Неизвестная',travelTime:null,travelType:null}]}});
 const {facts}=cianFieldFacts(source);
 assert.deepEqual(facts.transitDetails.map(row=>[row.id,row.name,row.time,row.mode]),[[1,'Тестовая','8','walk'],[2,'Тестовая','15–20','public'],[3,'Другая','4','car'],[4,'Неизвестная',null,'unknown']]);
 assert.equal(facts.walk,'8');assert.match(facts.transit,/Другая \| 4 \| car/);
 const noWalk=cianFieldFacts(cian({geo:{undergrounds:[{name:'Тестовая',travelTime:5,travelType:'byCar'}]}})).facts;assert.equal(noWalk.walk,'');
});

test('CIAN DOM route text never transfers minutes between stations or invents walking',()=>{
 const source=readSourceHtml('<li data-name="UndergroundItem"><span>Альфа</span><svg aria-label="Пешком"></svg><span>5–8 мин.</span></li><li data-name="UndergroundItem"><span>Бета</span><span>19 мин.</span></li>');
 const {facts}=cianFieldFacts(source);assert.equal(facts.walk,'5–8');assert.deepEqual(facts.transitDetails.map(row=>[row.name,row.time,row.mode]),[['Альфа','5–8','walk'],['Бета','19','unknown']]);
});

test('CIAN commission percent and absence have explicit provenance; agentFee is never clientFee',()=>{
 for(const [clientFee,commission,status]of [[50,'50','present'],[0,'0','none'],[undefined,'','unknown'],[null,'','unknown'],[false,'','unknown']]){
  const {facts,evidence}=cianFieldFacts(cian({bargainTerms:{price:80000,clientFee,agentFee:0}}));
  assert.equal(facts.commission,commission);assert.equal(facts.commissionStatus,status);
  if(status!=='unknown'){assert.equal(facts.commissionSource,'JSON bargainTerms.clientFee');assert(evidence.some(row=>row.field==='commission'));}else assert.equal(facts.commissionSource,'');
 }
 const dom=cianFieldFacts(readSourceHtml('<div data-name="OfferFactItem">Комиссия с арендатора: 35 %</div>')).facts;assert.equal(dom.commission,'35');assert.equal(dom.commissionStatus,'present');assert.equal(dom.commissionSource,'OfferFactItem');
});

test('CIAN seller is derived only from explicit role/boolean, never from zero commission',()=>{
 assert.equal(cianFieldFacts(cian({isByHomeowner:true})).facts.seller,'owner');
 assert.equal(cianFieldFacts(cian({isByAgent:true,isByHomeowner:false})).facts.seller,'agent');
 for(const isByHomeowner of [undefined,false,null,'true'])assert.equal(cianFieldFacts(cian({isByHomeowner,bargainTerms:{clientFee:0}})).facts.seller,'unknown');
 const agent=cianFieldFacts(readSourceHtml('<div data-name="SellerType">Агентство недвижимости</div>')).facts;assert.equal(agent.seller,'agent');assert.equal(agent.sellerSource,'SellerType');
});

test('Avito monthly rent, security deposit, percentage and named one-off charges are separate',()=>{
 const {facts,evidence}=avitoFieldFacts(avito(['Залог: 50 %','Комиссия: 25 %','Уборка: 3 000 ₽','Сервисный сбор: 1 500 ₽']));
 assert.equal(facts.price,'80000');assert.equal(facts.pricePeriod,'month');assert.equal(facts.priceCurrency,'RUB');assert.equal(facts.deposit,'40000');assert.equal(facts.depositPercent,'50');assert.equal(facts.commission,'25');
 assert.deepEqual(facts.oneTimeFees.map(row=>[row.label,row.amount,row.currency]),[['Уборка','3000','RUB'],['Сервисный сбор','1500','RUB']]);
 assert.equal(evidence.find(row=>row.field==='deposit').sourceUrl,'https://www.avito.ru/moskva/kvartiry/test_100000001');
 const missingBase=avitoFieldFacts(avito(['Залог: 50 %'], '', '80000')).facts;assert.equal(missingBase.deposit,undefined);assert.equal(missingBase.depositPercent,'50');
});

test('Avito commission unknown, percent and currency amount cannot substitute for each other or prove owner',()=>{
 for(const [row,percent,status,amount]of [['Комиссия: 0 %','0','none',undefined],['Комиссия: 50 %','50','present',undefined],['Комиссия: 15 000 ₽','','unknown','15000'],['Комиссия: уточняется','','unknown',undefined],['Залог: 50 000 ₽','','unknown',undefined]]){
  const {facts}=avitoFieldFacts(avito([row]));assert.equal(facts.commission,percent);assert.equal(facts.commissionStatus,status);assert.equal(facts.commissionAmount,amount);assert.equal(facts.seller,'unknown');
 }
 const {facts}=avitoFieldFacts(avito(['Тип объявления: От собственника','Комиссия: 40 %']));assert.equal(facts.seller,'owner');assert.match(facts.sellerSource,/Авито/);
 assert.equal(avitoFieldFacts(avito(['Продавец: Агентство'])).facts.seller,'agent');
});

test('source deposit and monetary commission retain foreign or unknown currencies independently of RUB rent',()=>{
 for(const [deposit,fee,currency]of [['$1 000','100 USD','USD'],['1 000 €','100 EUR','EUR'],['1 000 ₽','100 RUB','RUB']]){
  const {facts}=analyzeListingSource(avito(['Залог: '+deposit,'Комиссия: '+fee]));
  assert.equal(facts.priceCurrency,'RUB');assert.equal(facts.deposit,'1000');assert.equal(facts.depositCurrency,currency);assert.equal(facts.commissionAmount,'100');assert.equal(facts.commissionCurrency,currency);assert.equal(facts.commission,'');
 }
 const unknown=avitoFieldFacts(avito(['Залог: 1000'])).facts;assert.equal(unknown.deposit,'1000');assert.equal(unknown.depositCurrency,'unknown');
 const rub=cianFieldFacts(cian({bargainTerms:{price:80000,currency:'rur',deposit:1000}})).facts;assert.equal(rub.depositCurrency,'RUB');
 const foreign=cianFieldFacts(cian({bargainTerms:{price:80000,currency:'rur',deposit:1000,depositCurrency:'usd'}})).facts;assert.equal(foreign.depositCurrency,'USD');
 const unknownCian=cianFieldFacts(cian({bargainTerms:{deposit:1000}})).facts;assert.equal(unknownCian.depositCurrency,'unknown');
 const dom=cianFieldFacts(readSourceHtml('<div data-name="PriceInfo">80000 ₽/мес.</div><div data-name="OfferFactItem">Залог: $1000</div><div data-name="OfferFactItem">Комиссия с арендатора: 100 USD</div>')).facts;
 assert.equal(dom.depositCurrency,'USD');assert.equal(dom.commissionCurrency,'USD');assert.equal(dom.commissionAmount,'100');
});

test('Avito explicit district, address and transit keep their own source evidence',()=>{
 const source=avito([],`<section><h2>Расположение</h2><div><span itemprop="address">Москва, р-н Учебный, Учебная ул., 1</span><div><span>Тестовая</span><svg data-icon-name="walkingroute"></svg><span>7 мин.</span></div><div><span>Другая</span><svg aria-label="Автобус"></svg><span>20 мин.</span></div></div></section>`);
 const {facts,evidence}=avitoFieldFacts(source);assert.equal(facts.district,'р-н Учебный');assert.equal(facts.address,'Москва, р-н Учебный, Учебная ул., 1');assert.equal(facts.metro,'Тестовая');assert.equal(facts.walk,'7');assert.deepEqual(facts.transitDetails.map(row=>[row.name,row.time,row.mode]),[['Тестовая','7','walk'],['Другая','20','public']]);
 for(const field of ['address','district','transit'])assert(evidence.some(row=>row.field===field&&row.sourceUrl===source.url));
});

test('Avito unavailable status beats stale full fields and survives engine projection',()=>{
 for(const [text,status]of [['Объявление в архиве','archived'],['Объявление снято с публикации','removed'],['Объявление недоступно','removed']]){
  const source=avito(['Тип объявления: От собственника'],`<div data-marker="item-view/closed-warning">${text}</div>`);const {facts}=analyzeListingSource(source);assert.equal(facts.publicationStatus,status);assert.equal(facts.eligibleForNew,false);assert.equal(isAvitoNewCandidate(source),false);assert.equal(facts.price,'80000');
 }
 assert.equal(isAvitoNewCandidate(avito(['Залог: 0 ₽'])),true);
});
