import test from 'node:test';
import assert from 'node:assert/strict';
import {detectConditions} from '../web/fact-evidence.mjs';

test('explicit utility bills become an extra-charge witness without guessing the amount',()=>{
 const result=detectConditions('Дополнительно коммунальные платежи по квитанции (7-9т.р в месяц) + интернет.');
 assert.equal(result.facts.utilities,'extra');
 assert.deepEqual(result.evidence,[{field:'utilities',value:'extra',source:'Описание / условия',text:'коммунальные платежи по квитанции'}]);
 assert.deepEqual(Object.keys(result.facts),['utilities']);
 for(const source of ['Коммунальные платежи оплачиваются по квитанции.',
  'В стоимость не включены коммунальные платежи по квитанции.',
  'Коммунальные платежи по квитанции не включены в стоимость.'])assert.equal(detectConditions(source).facts.utilities,'extra',source);
});

test('included or negated utility bills are not reclassified as extra charges',()=>{
 for(const source of ['Коммунальные платежи по квитанции включены в стоимость.',
  'В аренду включены коммунальные платежи по квитанции.',
  'Коммунальные платежи по квитанции входят в стоимость аренды.',
  'Не нужно оплачивать коммунальные платежи по квитанции.',
  'Коммунальные платежи по квитанции не оплачиваются отдельно.',
  'Коммунальные платежи не по квитанции.',
  'Коммунальные платежи включены. Интернет оплачивается по квитанции.'])assert.equal(detectConditions(source).facts.utilities,undefined,source);
 assert.equal(detectConditions('Оплата ЖКХ включена (без счетчиков)').facts.utilities,'meters');
});

test('explicit apartment smoking prohibition survives intervening qualifiers',()=>{
 for(const source of ['Курение в квартире категорически запрещено.',
  'Курение в помещении строго запрещено.',
  'Курение запрещено.'])assert.equal(detectConditions(source).facts.smokingPolicy,'no',source);
 for(const source of ['Курение в квартире не запрещено.',
  'Курение на балконе запрещено.',
  'Курение в квартире обсуждается.'])assert.equal(detectConditions(source).facts.smokingPolicy,undefined,source);
});

test('admission range has its own contract and never changes the budget divisor',()=>{
 for(const source of ['Рассмотрим к проживанию 1- 2х человек.',
  'Рассматриваем для проживания 1–2 человека.',
  'Рассмотрим к проживанию1—2хчеловек.']){
  const result=detectConditions(source);
  assert.deepEqual(result.restrictions,[{kind:'occupancy-range',minimum:1,maximum:2,text:'入住人数：1–2人。'}]);
  assert.equal(result.facts.occupants,undefined);
  assert.equal(result.evidence.some(row=>row.field==='occupants'),false);
 }
 for(const source of ['Бюджет на 1–2 человек — 90 000 рублей.',
  'Не рассматриваем для проживания 1–2 человек.',
  'Рассматриваем 13 человек.',
  'Рассмотрим к проживанию 2–1 человек.',
  'Рассмотрим к проживанию 0–2 человек.'])assert.deepEqual(detectConditions(source).restrictions,[],source);
});

test('only an explicit additive meters-and-internet payment clause creates a network fee restriction',()=>{
 for(const source of ['Оплата за месяц + счетчики и интернет.',
  'Оплата за месяц плюс счётчики и интернет.']){
  const result=detectConditions(source);
  assert.deepEqual(result.restrictions,[{kind:'internet-extra',text:'网络费用另付。'}]);
  assert.deepEqual(result.facts,{});
  assert.deepEqual(result.evidence,[]);
 }
 for(const source of ['Оплата за месяц + счетчики и интернет включены в аренду.',
  'Оплата за месяц + счетчики, без интернета.'])assert.deepEqual(detectConditions(source).restrictions,[],source);
});
