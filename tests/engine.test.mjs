import test from 'node:test';
import assert from 'node:assert/strict';
import {DICTIONARY,DEFAULT_PREFS,generateListing,extractFacts,translate,wan} from '../web/engine.mjs';
const property={rooms:'2',layout:'euro2',style:'detailed',price:'70000',deposit:'35000',area:'39.3',floor:'3',floors:'17',occupants:'4',registration:'assist',features:['bed','ac','dishwasher']};
test('separate titles and body, exact money and sharing budget',()=>{
  const r=generateListing(property);assert.equal(r.titles.length,3);assert.match(r.titles[0],/7W/);assert.match(r.body,/35,000₽/);assert.match(r.body,/4人：约1.75W/);assert.match(r.body,/一室一厅/);assert.match(r.body,/洗碗机/);assert.doesNotMatch(r.body,/冰箱|大学通勤|35–50/);
});
test('route ranges and modes stay with their own station',()=>{
  const r=generateListing({...property,transit:'Прокшино | 6–10 | walk | 1\nФилатов Луг | 8 | bus',universities:'МГУ | 35–50 | public'});
  assert.match(r.body,/普罗克希诺｜步行约6–10分钟｜1号线/);assert.match(r.body,/菲拉托夫卢格｜公交约8分钟/);assert.match(r.body,/莫斯科国立大学｜公共交通约35–50分钟/);assert.doesNotMatch(r.body,/[а-яё]/i);assert.match(r.tags,/#莫斯科国立大学/);
});
test('no times invented for university or metro',()=>{
  const r=generateListing({...property,transit:'Румянцево',universities:'РУДН'});assert.match(r.body,/时间待确认/);assert.doesNotMatch(r.body,/15–20|25–30|红线/);
});
test('registration can be removed or scoped; never promise visa',()=>{
  const r=generateListing({...property,registration:'provided',registrationMonths:'6',registrationPeople:'3'});
  assert.match(r.body,/免费提供临时居住登记（6个月），最多3人/);assert.doesNotMatch(r.body,/落地签/);
  assert.doesNotMatch(generateListing({...property,registration:'none'}).body,/临时居住登记/);
});
test('no irrelevant university tags',()=>{const r=generateListing(property);assert.doesNotMatch(r.tags,/莫大|国立大学/);assert.match(r.tags,/#齐工作室/);});
test('custom dictionary special chars, overlapping terms and no recursive replacement',()=>{
  assert.equal(translate('Саларьево парк; Саларьево',DICTIONARY),'萨拉里耶沃公园住宅区; 萨拉里耶沃');
  assert.equal(translate('евро+2',{'евро+2':'一室一厅'}),'一室一厅');
  assert.equal(translate('а',{'а':'б','б':'в'}),'б');
});
test('negation and future installation do not become existing appliances',()=>{
  let r=extractFacts('Есть холодильник, кондиционер и диван. Нет стиральной машины. Посудомоечная машина отсутствует.');
  assert(r.facts.features.includes('fridge'));assert(r.facts.features.includes('ac'));assert(!r.facts.features.includes('washer'));assert(!r.facts.features.includes('dishwasher'));
  r=extractFacts('Хозяин может установить стиральную машину. Телевизор будет установлен позже.');
  assert(!r.facts.features.includes('washer'));assert(!r.facts.features.includes('tv'));
});
test('facts extract area decimals, price, deposit, floors and multiple station ranges',()=>{
  const r=extractFacts('2-комнатная квартира, 39,3 м². 3/17 этаж. Аренда 70 000 ₽, залог 35 000 ₽. Кухня 8 м². Метро Потапово — 4 минуты пешком; Новомосковская — 21–30 минут пешком.');
  assert.equal(r.facts.area,'39.3');assert.equal(r.facts.price,'70000');assert.equal(r.facts.deposit,'35000');assert.equal(r.facts.kitchen,'8');assert.equal(r.facts.floor,'3');assert.match(r.facts.transit,/Потапово \| 4 \| walk/);assert.match(r.facts.transit,/Новомосковская \| 21–30 \| walk/);
});
test('thousands, W, no deposit',()=>{assert.equal(extractFacts('Цена: 6,4 тыс. руб. Без залога').facts.price,'6400');assert.equal(extractFacts('租金：6.4W/月').facts.price,'64000');assert.equal(extractFacts('Без залога').facts.deposit,'0');assert.equal(wan(95000),'9.5W');});
test('invalid facts flagged; zero deposit preserved',()=>{assert(generateListing({...property,floor:'18',floors:'17'}).problems.length);assert.match(generateListing({...property,deposit:'0'}).body,/无需押金/);});
test('styles change presentation without introducing features',()=>{for(const style of ['compact','detailed','story']){const r=generateListing({...property,style});assert.match(r.body,/空调/);assert.doesNotMatch(r.body,/河景|衣帽间|采光明亮/);}});
