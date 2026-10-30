import test from 'node:test';
import assert from 'node:assert/strict';
import {extractFacts,generateListing,FEATURES,analyzeListingSource} from '../web/engine.mjs';
import {detectConditions} from '../web/fact-evidence.mjs';

test('arrival registration service survives source refusal without becoming a visa promise',()=>{
  for(const text of ['Регистрацию не делаем.','Без регистрации.','Регистрация невозможна.']){
    const v=extractFacts(text).facts;
    assert.equal(v.registrationPolicy,'not_available');
    const r=generateListing({...v,registration:'none'});
    assert.match(r.body,/抵达登记（临时居住登记）：提供办理协助/);
    assert.doesNotMatch(r.body,/不提供临时居住登记|落地签|保证签证|免费提供/);
  }
});

test('pets appear only with explicit positive permission; silence, negotiation, negatives and conflicts are omitted',()=>{
  for(const text of ['Есть диван.','Без животных.','Нельзя с животными.','С питомцами не разрешено.','Обсуждается с животными.','Можно с животными. Без животных.']){
    const v=extractFacts(text).facts;
    assert.doesNotMatch(generateListing({...v,sourceDescription:text}).body,/宠物/,text);
  }
  for(const text of ['Можно с животными.','Разрешено с питомцами.','Можно с детьми и животными.']){
    assert.match(generateListing(extractFacts(text).facts).body,/可带宠物/,text);
  }
  assert.doesNotMatch(generateListing({pets:true,petPolicy:'conflict'}).body,/宠物/);
  assert.doesNotMatch(generateListing({children:true,sourceDescription:'Нельзя с детьми.'}).body,/可带孩子/);
  assert.match(generateListing(extractFacts('Можно с детьми.').facts).body,/可带孩子/);
});

test('kitchen appliances and unusual equipment require their own evidence',()=>{
  const v=extractFacts('Есть встроенный духовой шкаф, вытяжка, рабочий стол, компьютер, пианино, кладовая и стеллаж. На кухне минимум 2 кухонных стула.').facts;
  for(const id of ['oven','hood','desk','computer','piano','pantry','shelves','chairs'])assert(v.features.includes(id),id);
  for(const id of ['wardrobe','fridge','microwave','sink','stove'])assert(!v.features.includes(id),id);
  assert.equal(v.featureCounts.chairs,'2');
  const r=generateListing({...v,style:'compact'});
  assert.match(r.body,/餐椅（至少2件）/);
  for(const label of ['烤箱','抽油烟机','书桌','电脑','钢琴','储藏室','置物架'])assert(r.body.includes(label),label);
  assert.doesNotMatch(r.body,/冰箱|微波炉|水槽|炉灶/);
  assert.equal(new Set(FEATURES.map(x=>x[0])).size,FEATURES.length);
});

test('explicit permission panels and strict JSON booleans fill children/pets without overriding contradictions',()=>{
  const source={description:'Есть диван.',records:[{name:'OfferFactItem',text:'Можно с детьми и животными'}],offer:{bargainTerms:{petsAllowed:true,childrenAllowed:true}}};
  const result=analyzeListingSource(source);
  assert.equal(result.facts.pets,true);assert.equal(result.facts.children,true);
  assert.match(generateListing(result.facts).body,/可带宠物/);
  const conflict=analyzeListingSource({...source,description:'Без животных. Нельзя с детьми.'});
  assert.equal(conflict.facts.petPolicy,'conflict');assert.equal(conflict.facts.childrenPolicy,'conflict');
  assert.doesNotMatch(generateListing(conflict.facts).body,/可带宠物|可带孩子/);
  assert(conflict.warnings.some(x=>x.includes('petsAllowed')));
});

test('explicitly separate two-room apartment offers only illustrative two/four-person rent split',()=>{
  const v={...extractFacts('2-комнатная квартира. Две изолированные комнаты. Цена 80 000 руб.').facts,price:'80000'};
  assert.equal(v.separate,true);
  const r=generateListing(v);
  assert.match(r.body,/2人：约4W（40,000₽）/);assert.match(r.body,/4人：约2W（20,000₽）/);
  assert.match(r.body,/仅为租金分摊示例/);assert.match(r.body,/入住人数及合租安排需与房东确认/);
  for(const other of [{...v,separate:false},{...v,layout:'euro2'},{...v,rooms:1,occupants:4}])assert.doesNotMatch(generateListing(other).body,/合租预算/);
});

test('compact variants alter line structure, stay reproducible and retain all confirmed equipment',()=>{
  const v={style:'compact',price:'80000',area:'54',features:['desk','chairs','oven','hood','computer','piano'],featureCounts:{chairs:'2'}};
  const variants=[0,1,2].map(copyVariant=>generateListing({...v,copyVariant}).body);
  assert.equal(new Set(variants).size,3);
  assert.notEqual(variants[0].split('\n').length,variants[2].split('\n').length);
  for(const body of variants){for(const label of ['80,000₽','54㎡','书桌','餐椅（2件）','烤箱','抽油烟机','电脑','钢琴'])assert(body.includes(label),label);assert(body.length<550);}
  assert.equal(generateListing({...v,copyVariant:2}).body,variants[2]);
  assert.equal(detectConditions('2 отдельные комнаты').facts.separate,true);
});

test('metro extraction uses the catalog and keeps complete compound station names',()=>{
  const v=extractFacts('Метро Юго-Западная — 7 минут пешком; Аэропорт Внуково — 12 минут автобусом.').facts;
  assert.match(v.transit,/Юго-Западная \| 7 \| walk/);
  assert.match(v.transit,/Аэропорт Внуково \| 12 \| bus/);
  assert.equal(v.transit.split('\n').length,2);
});

test('sink and cupboards retain their room context rather than becoming kitchen or bedroom equipment',()=>{
  const bathroom=extractFacts('В ванной комнате новая раковина.').facts;
  assert(bathroom.features.includes('washbasin'));assert(!bathroom.features.includes('sink'));
  const body=generateListing({...bathroom,style:'detailed'}).body;
  assert.match(body,/洗手盆/);assert.doesNotMatch(body,/厨房配置|水槽/);
  const kitchen=extractFacts('Встроенные шкафы на кухне. На кухне раковина и вытяжка.').facts;
  assert(kitchen.features.includes('kitchenFurniture'));assert(kitchen.features.includes('sink'));
  assert(!kitchen.features.includes('wardrobe'));assert(!kitchen.features.includes('washbasin'));
  const generic=extractFacts('Есть раковина.').facts;
  assert(!generic.features.includes('sink'));assert(!generic.features.includes('washbasin'));
  assert(extractFacts('В спальне шкаф.').facts.features.includes('wardrobe'));
});
