import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {readFileSync} from 'node:fs';
import {DICTIONARY,DEFAULT_PREFS,generateListing,extractFacts,translate,translateDraft,generationCapabilities} from '../web/engine.mjs';
import {COMMUNITY_RENTAL_ENTRIES,COMMUNITY_RENTAL_RULES,COMMUNITY_RENTAL_META} from '../web/community-phrases.mjs';

const sample={templateMode:'catalog',style:'compact',rooms:2,layout:'classic',separate:true,
  price:80000,deposit:40000,area:54,floor:5,floors:16,address:'Москва',
  transit:'Юго-Западная | 7 | walk',description:'两间独立房间。',details:'рядом парк',
  features:['desk','oven','hood','washer','chairs'],featureCounts:{chairs:2},
  utilities:'meters',registration:'assist',titleVariant:0,copyVariant:0,tagVariant:0};
const relevant=[/80,000₽/,/40,000₽/,/54㎡/,/书桌/,/烤箱/,/抽油烟机/,/洗衣机/,/餐椅（2件）/,/抵达登记（临时居住登记）/];

test('catalog descriptions and titles only rearrange confirmed apartment information',()=>{
  for(const n of [0,1,7,63,511,4095,200000,262143]){
    const value=generateListing({...sample,titleVariant:n,copyVariant:n,tagVariant:n});
    assert.equal(value.titles.length,3);
    assert(value.templateInfo.titleId&&value.templateInfo.descriptionId&&value.templateInfo.tagId);
    for(const expected of relevant)assert.match(value.body,expected);
    assert.doesNotMatch(value.body,/冰箱|微波炉|投影仪|可带宠物|可带孩子|落地签|保证签证/);
    assert.doesNotMatch(value.titles.join(' '),/冰箱|微波炉|精装修|拎包入住/);
    assert.doesNotMatch(value.tags,/莫大|莫斯科友大|留学生首选|落地签/);
    assert.deepEqual(value,generateListing({...sample,titleVariant:n,copyVariant:n,tagVariant:n}));
  }
});

test('title, body and tag catalogue selectors are independent when their indices are explicit',()=>{
  const base=generateListing(sample);
  const title=generateListing({...sample,titleVariant:19});
  assert.notDeepEqual(base.titles,title.titles);assert.equal(base.body,title.body);assert.equal(base.tags,title.tags);
  const body=generateListing({...sample,copyVariant:27});
  assert.notEqual(base.body,body.body);assert.deepEqual(base.titles,body.titles);assert.equal(base.tags,body.tags);
  const tags=generateListing({...sample,tagVariant:41});
  assert.notEqual(base.tags,tags.tags);assert.deepEqual(base.titles,tags.titles);assert.equal(base.body,tags.body);
});

test('hundreds of selected catalogue bodies are distinct and keep mandatory facts',()=>{
  const bodies=new Set();
  for(let i=0;i<350;i++){
    const result=generateListing({...sample,copyVariant:i,titleVariant:0,tagVariant:0});
    bodies.add(result.body);
    assert.match(result.body,/80,000₽/);assert.match(result.body,/54㎡/);
    assert.match(result.body,/抵达登记（临时居住登记）/);
  }
  assert.equal(bodies.size,350);
});

test('empty catalogue contexts and contradictory permissions do not grow unsupported claims',()=>{
  const empty=generateListing({templateMode:'catalog',titleVariant:127,copyVariant:1024,tagVariant:999});
  assert.doesNotMatch(empty.titles.join(' '),/近地铁|精装|明亮|拎包|豪华|宠物|免费|㎡|W\/月/);
  assert.doesNotMatch(empty.tags,/近地铁|全新装修|拎包入住|莫大|友大|留学/);
  const facts=extractFacts('Можно с животными. Без животных. Нельзя с детьми. Регистрацию не делаем.').facts;
  const result=generateListing({...facts,templateMode:'catalog',copyVariant:12345,children:true,pets:true});
  assert.doesNotMatch(result.body,/可带宠物|可带孩子|不提供临时居住登记|落地签/);
  assert.match(result.body,/抵达登记（临时居住登记）：提供办理协助/);
});

test('draft translation exposes unknown fragments and exact user overrides remain non-recursive',()=>{
  assert.equal(translate('а',{'а':'б','б':'в'}),'б');
  const unknown=translateDraft('абракадабрафраза',{mode:'safe'});
  assert(unknown.unknown.some(value=>value.includes('абракадабрафраза')));
  const custom=translateDraft('абракадабрафраза',{rules:{'абракадабрафраза':'自定义表达'},mode:'safe'});
  assert.equal(custom.text,'自定义表达');assert.equal(custom.unknown.length,0);
  assert(Object.keys(DICTIONARY).length>80);
  assert(generationCapabilities().customText.manualRulesPriority);
});

test('CLI and browser function render the same catalogue fields with one generator',()=>{
  const input={text:'Квартира 54 м². Цена 80000 руб. Есть рабочий стол и духовой шкаф.',data:sample};
  const run=spawnSync(process.execPath,['cli/generate.mjs'],{cwd:new URL('../',import.meta.url),input:JSON.stringify(input),encoding:'utf8'});
  assert.equal(run.status,0,run.stderr);
  const cli=JSON.parse(run.stdout),browser=generateListing(cli.fields,DICTIONARY,DEFAULT_PREFS);
  for(const field of ['titles','body','tags','templateInfo'])assert.deepEqual(cli[field],browser[field]);
});

test('expanded amenity triggers remain vocabulary rather than proof of planned or absent amenities',()=>{
  const denied=extractFacts('Новый ремонт только планируется. Китайский магазин отсутствует. Рядом парк отсутствует.').facts;
  const result=generateListing({...denied,templateMode:'catalog'});
  assert.doesNotMatch(result.body,/全新装修|附近有中国商店|附近有公园/);
  const present=extractFacts('Новый ремонт. Китайский магазин. Рядом парк.').facts;
  assert.match(present.details,/новый ремонт/);assert.match(present.details,/китайский магазин/);assert.match(present.details,/рядом парк/);
});

test('all attributed community phrases reach the real draft translator with manual precedence',()=>{
  const research=JSON.parse(readFileSync(new URL('../data/chinese-rental-research.json',import.meta.url),'utf8'));
  assert.equal(COMMUNITY_RENTAL_META.entryCount,405);
  assert.deepEqual(COMMUNITY_RENTAL_ENTRIES,research.entries.map(({ru,zh,category})=>[ru,zh,category]));
  assert.deepEqual(COMMUNITY_RENTAL_RULES,Object.fromEntries(research.entries.map(({ru,zh})=>[ru,zh])));
  const sourceIds=new Set(research.sources.map(source=>source.id));
  assert(COMMUNITY_RENTAL_META.entries.every(entry=>entry.sourceIds.every(id=>sourceIds.has(id))));
  for(const [ru,zh] of COMMUNITY_RENTAL_ENTRIES){
    const result=translateDraft(ru,{mode:'safe'});
    assert.equal(result.text,DICTIONARY[ru]||zh,ru);
    assert.deepEqual(result.unknown,[],ru);
  }
  assert.equal(generationCapabilities().communityPhrases,405);
  assert.equal(translateDraft('совмещенный санузел',{rules:{...DICTIONARY,'совмещенный санузел':'自定义卫浴'}}).text,'自定义卫浴');
});

test('private bathroom layouts and renovation labels do not acquire shared or dry-wet meanings',()=>{
  const combined=generateListing({templateMode:'catalog',bathroom:'1 совмещенный',renovation:'Евроремонт'});
  assert.match(combined.body,/卫生间与浴室合一/);
  assert.match(combined.body,/现代装修/);
  assert.doesNotMatch(combined.body,/合用卫浴|共享卫浴|干湿分离|欧式装修/);
  const separate=generateListing({templateMode:'catalog',bathroom:'1 раздельный'});
  assert.match(separate.body,/厕所与浴室分开/);
  assert.doesNotMatch(separate.body,/干湿分离|共享卫浴/);
});

test('known community questions translate as questions without granting pet or child permission',()=>{
  for(const [ru,zh] of [['можно ли с питомцем','可以带宠物入住吗'],['можно ли с ребёнком','可以带孩子入住吗']]){
    assert.equal(translateDraft(ru).text,zh);
    const facts=extractFacts(ru+'?').facts;
    assert(!facts.pets);assert(!facts.children);
    const generated=generateListing({...facts,templateMode:'catalog',description:ru,details:'абракадабрафраза'});
    assert.doesNotMatch(generated.tags,/#可带宠物|#可带孩子/);
    assert(generated.unknown.includes('абракадабрафраза'));
  }
});
