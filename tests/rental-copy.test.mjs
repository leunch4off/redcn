import test from 'node:test';
import assert from 'node:assert/strict';
import {generateListing,DICTIONARY,DEFAULT_PREFS} from '../web/engine.mjs';
import {listingCopySeed,listingCopyVariation,publicationFingerprint,publicationDigest,
  normalizeCopyHistory,rememberPublication,advertisingLead,rentalCopyStats} from '../web/rental-copy.mjs';

const fixture={url:'https://www.cian.ru/rent/flat/100001/',templateMode:'catalog',rooms:'1',layout:'euro2',
  area:'40',price:'70000',deposit:'35000',floor:'10',floors:'23',style:'detailed',
  transit:'Прокшино | 5 | walk',features:['wardrobe','desk','oven','dishwasher','bath'],
  utilities:'meters',smokingPolicy:'no',guestPolicy:'no',subletPolicy:'no',shortTermPolicy:'no',
  description:'Сохранённый оригинал описания',copyVariant:0,titleVariant:0,tagVariant:0};

test('new card identities spread first layouts while counters and reloads remain stable',()=>{
  const families=new Set(),leads=new Set();
  for(let index=0;index<65;index++){
    const fields={...fixture,url:'https://www.cian.ru/rent/flat/'+(100000+index)+'/'};
    const result=generateListing(fields),reloaded=generateListing(JSON.parse(JSON.stringify(fields)));
    assert.equal(result.body,reloaded.body);assert.deepEqual(result.titles,reloaded.titles);
    assert.deepEqual(result.templateInfo.indices,{title:0,description:0,tags:0});
    families.add(result.templateInfo.editorial.descriptionStyle.family);
    leads.add(result.templateInfo.copyEngine.leadFrame);
  }
  assert(families.size>=14,'fresh listings must not all select the same layout');
  assert(leads.size>=10,'fresh listings must not all select the same opening');
  assert.equal(listingCopySeed({...fixture,url:fixture.url+'?tracking=1#photo'}),listingCopySeed(fixture));
  assert.equal(listingCopySeed({}),0);
  assert.equal(listingCopyVariation({...fixture,copyVariant:9})-listingCopyVariation(fixture),9);
});

test('fifty same-card rewrites vary prose and preserve every supplied constraint and appliance',()=>{
  const texts=new Set(),openings=new Set(),rentSentences=new Set();
  const rules={...DICTIONARY,'Сохранённый оригинал описания':'这段原有描述完整保留。'};
  const original=JSON.stringify(fixture);
  for(let counter=0;counter<50;counter++){
    const result=generateListing({...fixture,copyVariant:counter},rules,DEFAULT_PREFS);
    assert.deepEqual(result.problems,[]);
    assert(!texts.has(publicationFingerprint(result.body)),'wording/punctuation-normalized duplicate at '+counter);
    texts.add(publicationFingerprint(result.body));openings.add(result.body.split('\n\n')[0]);
    for(const fact of ['40㎡','一室一厅','独立卧室＋厨房客厅','70,000₽','35,000₽','10/23层',
      '普罗克希诺｜步行约5分钟','衣柜','书桌','烤箱','洗碗机','浴缸','物业费已包含，仅水电按表计费',
      '仅限不吸烟','不接待访客或聚会','不得将公寓转租','不接受按日短租','这段原有描述完整保留',
      '抵达登记（临时居住登记）'])assert(result.body.includes(fact),fact+':'+counter);
    assert.doesNotMatch(result.body,/冰箱|微波炉|空调|采光|豪华|随时入住|免费登记|学生首选|宠物|中介费|佣金/);
    rentSentences.add(result.body.match(/(?:租金：|每月租金为|月租是|租房预算先记这一项：)[^\n]+/)?.[0]);
  }
  assert.equal(texts.size,50);assert(openings.size>=12);assert(rentSentences.size>=4);
  assert.equal(JSON.stringify(fixture),original,'the generator must not alter source fields');
});

test('one part changes independently and the persisted raw index is never seeded a second time',()=>{
  const initial=generateListing(fixture);
  const next=generateListing({...fixture,copyVariant:1});
  assert.deepEqual(next.titles,initial.titles);assert.equal(next.tags,initial.tags);assert.notEqual(next.body,initial.body);
  const restored={...fixture,copyVariant:next.templateInfo.indices.description};
  assert.equal(restored.copyVariant,1);assert.equal(generateListing(restored).body,next.body);
});

test('duplicate tracking ignores punctuation emoji and tag order, persists, and stays bounded',()=>{
  assert.equal(publicationFingerprint('🏡 面积：40㎡。'),publicationFingerprint('面积 40㎡!'));
  assert.equal(publicationDigest('#莫斯科 #租房','tags'),publicationDigest('#租房 #莫斯科','tags'));
  let history=rememberPublication({},'body','原文40㎡');
  assert(normalizeCopyHistory(JSON.parse(JSON.stringify(history))).body.includes(publicationDigest('原文40㎡')));
  for(let index=0;index<100;index++)history=rememberPublication(history,'body','版本'+index);
  assert.equal(history.body.length,64);assert.deepEqual(history.title,[]);assert.deepEqual(history.tags,[]);
  const clean=normalizeCopyHistory({body:['<script>',null,{},'a-b-c']});assert.deepEqual(clean.body,['a-b-c']);
});

test('sparse lead frames do not invent missing property facts or advertise infinite AI output',()=>{
  const stats=rentalCopyStats();assert.equal(stats.leadFrames,12);assert.equal(stats.modelAuthored,false);assert.equal(stats.unlimited,false);
  assert.equal(advertisingLead({}).text,'');
  for(let index=0;index<50;index++){
    const lead=advertisingLead({area:'40㎡'},index).text;
    assert(lead.includes('40㎡'));
    assert.doesNotMatch(lead,/一室|卧室|租金\d|地铁|厨房|洗碗机|免费|宠物|学生|豪华/);
  }
});
