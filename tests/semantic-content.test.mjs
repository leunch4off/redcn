import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {generateListing,extractFacts} from '../web/engine.mjs';
import {roomTerminology,extractRoomCounts,audienceIntroduction,normalizeListingStudioProfile,applyStudioProfile} from '../web/listing-semantics.mjs';

const fields={rooms:'2',layout:'classic',style:'compact',price:'80000',deposit:'40000',area:'52',features:['bedAny','desk','sofa'],sourceDescription:''};

test('audience profiles use different conditional introductions without supplying rental permission or apartment facts',()=>{
  const result=['general','student','family','professional'].map(audience=>generateListing({...fields,audience}));
  assert.equal(new Set(result.map(row=>row.body)).size,4);
  assert(result[1].body.startsWith('在莫斯科读书找住处'));
  assert(result[2].body.startsWith('与家人一起找住处'));
  assert(result[3].body.startsWith('在莫斯科工作找住处'));
  for(const row of result){
    assert.equal(row.problems.length,0);
    for(const fact of ['52㎡','80,000₽','40,000₽','书桌','床','沙发'])assert(row.body.includes(fact),fact);
    assert.doesNotMatch(row.body,/学生可入住|可带孩子|可带宠物|仅限|限女|只租|近大学|通勤\d|免费登记|全新装修/);
  }
  assert.equal(audienceIntroduction('worker',1),audienceIntroduction('professional',1));
  assert.equal(audienceIntroduction('unknown'), '');
  assert.equal(new Set([0,1,2].map(index=>audienceIntroduction('student',index))).size,3);
});

test('Studio and separately supplied bedroom facts produce a visible semantic warning and no inferred independent room',()=>{
  const result=generateListing({...fields,layout:'studio',bedrooms:'1',separate:true});
  assert(result.semanticWarnings.some(message=>/Studio/.test(message)));
  assert.match(result.body,/开放式Studio/);
  assert.doesNotMatch(result.body,/卧室数量|独立卧室|房间彼此独立|房间独立/);
  assert.equal(result.problems.length,0,'warning remains reviewable separately from fatal input errors');
  const custom=generateListing({...fields,layout:'studio',description:'独立卧室，书桌'});
  assert(custom.semanticWarnings.some(message=>/авторском тексте/.test(message)));
  assert.equal(generateListing({...fields,rooms:'1',layout:'studio',features:['bedAny']}).semanticWarnings.length,0,'a bed never means a bedroom');
  assert.equal(roomTerminology({layout:'studio',description:'Нет отдельной спальни. 没有独立卧室。'}).warnings.length,0,'negated bedroom claim is not a contradiction');
});

test('Russian rooms, explicit bedrooms, living rooms and living area keep separate Chinese meanings',()=>{
  const result=generateListing({...fields,bedrooms:'1',livingRooms:'1',living:'29'});
  assert.match(result.body,/户型：两居室/);
  assert.match(result.body,/卧室数量：1间卧室/);
  assert.match(result.body,/客厅数量：1间客厅/);
  assert.match(result.body,/居住面积：29㎡/);
  assert.equal(result.roomTerminology.rooms,2);
  assert.equal(result.roomTerminology.bedrooms,1);
  const unknown=generateListing({...fields,living:'29'});
  assert.doesNotMatch(unknown.body,/卧室数量|客厅数量|两间卧室/);
  assert.equal(roomTerminology({rooms:'2',features:['bed']}).bedrooms,null);
});

test('euro layouts keep explicit source bedroom counts reviewable without deriving them from the room count',()=>{
  assert.match(generateListing({...fields,layout:'euro2'}).body,/独立卧室＋厨房客厅/);
  assert.match(generateListing({...fields,layout:'euro3'}).body,/两间卧室＋厨房客厅/);
  assert(generateListing({...fields,layout:'euro2',bedrooms:'2'}).semanticWarnings.some(message=>/евро-2/.test(message)));
  assert(generateListing({...fields,layout:'euro3',bedrooms:'1'}).semanticWarnings.some(message=>/евро-3/.test(message)));
  assert(roomTerminology({rooms:1,bedrooms:2}).warnings.some(message=>/Спален больше/.test(message)));
});

test('only explicit bedroom and living-room counts are extracted; bedroom area and beds are not room counts',()=>{
  assert.deepEqual(extractRoomCounts('Две спальни, одна гостиная.'),{bedrooms:'2',livingRooms:'1'});
  assert.deepEqual(extractRoomCounts('Спален: 2; гостиных: 1'),{bedrooms:'2',livingRooms:'1'});
  assert.deepEqual(extractRoomCounts('Спальня — 14 м². Есть две кровати. 2-комнатная квартира.'),{});
  assert.deepEqual(extractRoomCounts('Не две спальни, а кухня.'),{});
  const result=extractFacts('2-комнатная квартира, 52 м². Одна спальня, одна гостиная.');
  assert.equal(result.facts.rooms,'2');assert.equal(result.facts.bedrooms,'1');assert.equal(result.facts.livingRooms,'1');
});

test('saved editorial profile changes only presentation and form of address while preserving amounts and active links',()=>{
  const source='🏠 户型: 两居室\n\n✅ 书桌 (照片可见)\n💰 80,000₽ / 月\n12:30 · WeChat: agent_test-1\nhttps://example.org/path?q=a:b;c=1\n📩 欢迎中文咨询';
  const formatted=applyStudioProfile(source,{punctuation:'fullwidth',emoji:'none',addressing:'polite'});
  assert.match(formatted,/户型： 两居室/);assert.match(formatted,/书桌 （照片可见）/);
  assert.match(formatted,/80,000₽ \/ 月/);assert.match(formatted,/12:30/);
  assert(formatted.includes('https://example.org/path?q=a:b;c=1'));
  assert(formatted.includes('agent_test-1'));
  assert(formatted.includes('欢迎您用中文咨询'));
  assert.doesNotMatch(formatted,/🏠|✅|📩/);
  assert(!applyStudioProfile(source,{punctuation:'compact'}).includes('\n\n'));
  assert.equal(normalizeListingStudioProfile({emoji:'invalid'}).emoji,'source');
});

test('engine applies the saved editorial profile consistently to title and description without removing equipment',()=>{
  const profile={id:'team-1',name:'Спокойный',emoji:'none',punctuation:'compact',addressing:'friendly'};
  const result=generateListing({...fields,studioProfile:profile});
  assert.deepEqual(result.studioProfile,profile);
  for(const text of [...result.titles,result.body])assert.doesNotMatch(text,/🏡|🔥|📩|✅|🛋|🛏/);
  for(const fact of ['80,000₽','40,000₽','书桌','床','沙发'])assert(result.body.includes(fact));
  assert(!result.body.includes('\n\n'));
  assert(result.body.includes('欢迎你用中文'));
});

test('the saved title formula uses the same confirmed engine context and retains automatic alternatives',()=>{
  const regular=generateListing(fields),custom=generateListing({...fields,titleFormula:'{city}｜{layout}｜{area}｜{price}'});
  assert.equal(custom.titles[0],'莫斯科｜两居室｜52㎡｜8W/月');
  assert.equal(custom.titles[1],regular.titles[1]);assert.equal(custom.titles[2],regular.titles[2]);
  assert.equal(custom.body,regular.body);
  const invalid=generateListing({...fields,titleFormula:'{sourceDescription}'});
  assert(invalid.problems.some(message=>/Неизвестная переменная/.test(message)));
  const commission=generateListing({...fields,titleFormula:'{price}｜佣金50%'});
  assert(commission.problems.some(message=>/Комиссия/.test(message)));
});

test('non-monthly or foreign-currency source prices never generate a mislabeled monthly ruble publication',()=>{
  for(const basis of [{pricePeriod:'day'},{pricePeriod:'week'},{pricePeriod:'unknown'},{priceCurrency:'unknown'},{priceCurrency:'USD'},{priceCurrency:'EUR'},{depositCurrency:'USD'},{depositCurrency:'EUR'},{depositCurrency:'unknown'}]){
    const result=generateListing({...fields,...basis});
    assert(result.problems.length>0);assert.equal(result.body,'');assert.equal(result.titles.length,0);assert.equal(result.tags,'');
  }
  assert.match(generateListing({...fields,pricePeriod:'month',priceCurrency:'RUB'}).body,/80,000₽/);
  assert.match(generateListing(fields).body,/80,000₽/,'old manual fields without currency/period retain their established monthly-ruble units');
  assert.match(generateListing({...fields,deposit:'0',depositCurrency:'unknown'}).body,/无需押金/,'zero deposit has no conversion ambiguity');
  assert.match(generateListing({...fields,depositCurrency:'RUB',commissionCurrency:'USD'}).body,/80,000₽/,'private commission currency does not alter a public listing');
});

test('the Python CLI Node generator uses the same audiences, room terminology, styles and rate guards',()=>{
  const input={data:{...fields,bedrooms:'1',livingRooms:'1',audience:'family',studioProfile:{emoji:'none',punctuation:'compact',addressing:'polite'}},prefs:{brandTags:''}};
  const result=spawnSync(process.execPath,['cli/generate.mjs'],{input:JSON.stringify(input),encoding:'utf8',cwd:new URL('..',import.meta.url)});
  assert.equal(result.status,0,result.stderr);
  const listing=JSON.parse(result.stdout);
  assert(listing.body.startsWith('与家人一起找住处'));assert(listing.body.includes('1间卧室'));assert(listing.body.includes('1间客厅'));
  assert.equal(listing.audience,'family');assert.doesNotMatch(listing.body,/✅/);
  const rejected=spawnSync(process.execPath,['cli/generate.mjs'],{input:JSON.stringify({data:{...fields,pricePeriod:'day'}}),encoding:'utf8',cwd:new URL('..',import.meta.url)});
  assert.equal(rejected.status,2);assert.match(rejected.stderr,/не за месяц/);assert.equal(rejected.stdout,'');
});
