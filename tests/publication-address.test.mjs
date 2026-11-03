import test from 'node:test';
import assert from 'node:assert/strict';
import {defaultPublicationDesign,resolvePublicationData} from '../web/publication-model.mjs';

const address='Москва, НАО (Новомосковский), Внуковское поселение, ул. Бориса Пастернака, 8';

test('publication address labels translate the source address without changing its facts',()=>{
  const card={fields:{address,metro:'Рассказовка',price:80000}};
  const original=structuredClone(card),data=resolvePublicationData(card);
  assert.equal(data.address,'莫斯科, 新莫斯科行政区, 弗努科沃乡镇（原行政名称）, 鲍里斯·帕斯捷尔纳克街, 8');
  assert.doesNotMatch(data.address,/[А-Яа-яЁё]/u);
  assert.equal(data.mapTitle,'莫斯科 · 拉斯卡佐夫卡');
  assert.equal(data.price,'80,000');
  assert.equal(data.priceUnit,'卢布 / 月');
  assert.deepEqual(card,original);
});

test('publication aliases cover both settlement orders and full street forms',()=>{
  const data=resolvePublicationData({fields:{address:'Новомосковский административный округ, поселение Внуковское, улица Бориса Пастернака, 8'}});
  assert.equal(data.address,'新莫斯科行政区, 弗努科沃乡镇（原行政名称）, 鲍里斯·帕斯捷尔纳克街, 8');
});

test('ambiguous bare NAO remains visible for review instead of choosing a region',()=>{
  const data=resolvePublicationData({fields:{address:'НАО, 8'}});
  assert.match(data.address,/НАО/u);
  assert.ok(data.warnings.some(value=>value.includes('俄文')));
});

test('personal rules override the publication address glossary',()=>{
  const data=resolvePublicationData({fields:{address}}, {}, {rules:{'НАО (Новомосковский)':'自定义行政区','Внуковское поселение':'自定义乡镇','ул. Бориса Пастернака':'自定义街道'}});
  assert.equal(data.address,'莫斯科, 自定义行政区, 自定义乡镇, 自定义街道, 8');
});

test('map callout avoids repeating a city already present in the fallback address',()=>{
  const data=resolvePublicationData({fields:{address}});
  assert.equal(data.mapTitle,data.address);
  assert.equal((data.mapTitle.match(/莫斯科/g)||[]).length,2);
  assert.equal(defaultPublicationDesign().surfaces.pdfOverview.nodes.find(node=>node.id==='title').h,122);
});
