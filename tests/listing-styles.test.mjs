import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {getTemplate,renderTemplate,TEMPLATE_AUDIT_CONTEXT,CATALOG_SIZE,
  LISTING_STYLE_VERSION,LISTING_STYLE_FAMILIES,getListingStyle,
  renderListingTemplate,renderListingEquipment,listingStyleStats} from '../web/template-catalog.mjs';

const context={
  city:'莫斯科',layout:'两居室',area:'54㎡',price:'8W/月',metro:'西南站 · 步行7分钟',
  feature:'书桌、烤箱',floor:'3/17层',code:'FIXTURE-042',university:'已选教学楼',
  serviceShort:'协助抵达登记',permissions:'可带孩子',
  location:'📍 地址：已确认的测试地址',
  facts:'🏠 户型：两居室\n📐 面积：54㎡\n🏢 楼层：3/17层',
  equipment:'🛋 已确认家具\n✅ 书桌\n✅ 衣柜（至少2件）\n🍳 厨房设备\n✅ 烤箱\n🛁 清洁与卫浴\n✅ 洗衣机',
  transport:'🚇 西南站 · 步行7分钟\n🎓 已选教学楼 · 已提供的公共交通32分钟',
  conditions:'💰 月租：8W/月\n🔐 押金：8W\n💡 水电按表另付\n🚭 禁止吸烟\n👶 可带孩子',
  service:'✈️ 抵达登记（临时居住登记）：提供办理协助。',
  contact:'📩 WeChat：fixture_agent',
  tags:['#莫斯科租房','#西南站','#两居室','#书桌','#fixture_agent'],
};
const words=value=>value.replace(/[^\p{L}\p{N}]/gu,'');
const basicKeys=['location','facts','equipment','transport','conditions','service','contact'];

test('the audited v1 catalog remains byte-for-byte reproducible for its established contract',()=>{
  const rows=['title','description','tags'].flatMap(kind=>[0,1,7,64,452,1000,CATALOG_SIZE-1]
    .map(index=>({template:getTemplate(kind,index),text:renderTemplate(kind,index,TEMPLATE_AUDIT_CONTEXT)})));
  assert.equal(createHash('sha256').update(JSON.stringify(rows)).digest('hex'),
    'a408942a2e7fdaef55b31ef914c0d0e994379129634050e5c449aed4fba9b928');
});

test('twenty runtime families have separate metadata and do not inflate the old catalog inventory',()=>{
  assert.equal(LISTING_STYLE_FAMILIES.length,20);
  assert.equal(new Set(LISTING_STYLE_FAMILIES.map(row=>row.id)).size,20);
  const ids=new Set();
  for(let index=0;index<20;index++){
    const info=getListingStyle('description',index,context);ids.add(info.family);
    assert.equal(info.version,LISTING_STYLE_VERSION);
    assert.equal(info.baseTemplateId,getTemplate('description',index).id);
    assert.notEqual(info.id,info.baseTemplateId);
    assert(info.id.includes(info.family));
  }
  assert.equal(ids.size,20);
  assert.equal(listingStyleStats().families,20);
  assert.match(listingStyleStats().countsDescribe,/not-an-additional-materialized/);
  assert.equal(CATALOG_SIZE,262144);
});

test('selection and rendering are seeded by the existing counter and have a bounded index space',()=>{
  for(const kind of ['title','description','tags'])for(const index of [0,1,19,20,452,262143]){
    assert.deepEqual(getListingStyle(kind,index,context),getListingStyle(kind,index,context));
    assert.equal(renderListingTemplate(kind,index,context),renderListingTemplate(kind,index,context));
    assert.equal(getListingStyle(kind,index+CATALOG_SIZE,context).id,getListingStyle(kind,index,context).id);
  }
  assert.equal(getListingStyle('title',-1,context).index,CATALOG_SIZE-1);
  assert.equal(getListingStyle('title',Infinity,context).index,0);
  for(const kind of ['unknown','__proto__','constructor']){
    assert.throws(()=>getListingStyle(kind,0,context),TypeError);
    assert.throws(()=>renderListingTemplate(kind,0,context),TypeError);
  }
});

test('titles visibly vary focus and structure while retaining the four supplied core facts',()=>{
  const values=[];
  for(let index=0;index<20;index++){
    const title=renderListingTemplate('title',index,context);values.push(title);
    for(const key of ['city','layout','area','price'])assert(title.includes(context[key]),key+':'+index);
    assert.doesNotMatch(title,/undefined|\{\{|\[\[/);
    assert(!/^[｜·/,，]|[｜·/,，]$/.test(title));
  }
  assert.equal(new Set(values).size,20);
  assert.match(values[2],/^💰 8W\/月/);
  assert.match(values[4],/^🚇 西南站/);
  assert.match(values[10],/^✨ 书桌、烤箱/);
  assert.match(values[17],/^\[FIXTURE-042\]/);
});

test('all style and density modes retain supplied facts inventory restrictions and agency assistance',()=>{
  for(const style of ['compact','detailed','story'])for(let index=0;index<80;index++){
    const body=renderListingTemplate('description',index,{...context,style}),normalized=words(body);
    for(const key of basicKeys)assert(normalized.includes(words(context[key])),key+':'+style+':'+index);
    assert.equal(body.split('抵达登记（临时居住登记）').length,2,'one registration statement');
    assert(body.includes('至少2件'));
    assert(body.includes('禁止吸烟'));
  }
});

test('body styles change section order paragraph density and substantive editorial framing',()=>{
  const bodies=Array.from({length:20},(_,index)=>renderListingTemplate('description',index,context));
  assert(new Set(bodies).size>=18);
  assert(new Set(bodies.map(value=>basicKeys.map(key=>({key,at:words(value).indexOf(words(context[key]))}))
    .sort((a,b)=>a.at-b.at).map(row=>row.key).join('|'))).size>=12);
  assert(bodies[1].startsWith('先')||bodies[1].startsWith('这')||bodies[1].startsWith('房')||bodies[1].startsWith('从')||bodies[1].startsWith('把'));
  assert(bodies[2].indexOf('月租')<bodies[2].indexOf('地址'));
  assert(bodies[3].indexOf('已确认家具')<bodies[3].indexOf('地址'));
  assert(bodies[4].indexOf('公共交通32分钟')<bodies[4].indexOf('面积'));
  assert.match(bodies[8],/^01 · /);
  assert(bodies[6].split('\n').length<bodies[8].split('\n').length);
  assert(bodies[15].split('\n').length<bodies[0].split('\n').length);
  assert.match(bodies[19],/【位置与房屋与交通】/);
});

test('special layouts are available only with their required supplied fields',()=>{
  for(let index=0;index<120;index++){
    const sparse={city:'莫斯科',facts:'面积：54㎡'};
    const title=getListingStyle('title',index,sparse),body=getListingStyle('description',index,sparse);
    for(const info of [title,body])assert(!['price-first','budget-note','room-by-room','feature-focus','commute-first','university-reference','catalogue','service-note'].includes(info.family),info.family);
    const value=renderListingTemplate('description',index,sparse);
    assert.doesNotMatch(value,/烤箱|洗碗机|宠物|孩子|学生|大学|院校|FIXTURE|押金|免费|落地签/);
    assert(value.includes('54㎡'));
  }
});

test('format inspiration never copies Sydney student female roommate or free-fee claims into an unrelated card',()=>{
  const input={city:'莫斯科',layout:'一居室',area:'41㎡',price:'68000卢布/月',
    facts:'面积：41㎡',equipment:'冰箱、沙发、床',conditions:'水电另付',
    service:'抵达登记（临时居住登记）：提供办理协助。'};
  for(let index=0;index<100;index++)for(const kind of ['title','description']){
    const value=renderListingTemplate(kind,index,input);
    assert.doesNotMatch(value,/悉尼|Sydney|女生|限女|室友|留学|学生|可带宠物|无物业费|免押|免中介费|免费登记|免费接送|包落地|豪华|采光|大窗|随时入住|莫斯科城|YL26269/);
    assert.doesNotMatch(value,/(?:没有|未配备|不配备)(?:冰箱|沙发|床)/);
  }
});

test('an unknown permission has no positive or negative rental claim',()=>{
  for(let index=0;index<80;index++){
    const text=renderListingTemplate('title',index,{city:'莫斯科',layout:'公寓',pets:true,students:true});
    assert.doesNotMatch(text,/宠物|学生|可入住|不接受/);
  }
  const allowed=renderListingTemplate('title',14,{...context,permissions:'可带孩子'});
  assert(allowed.startsWith('可带孩子'));
});

test('existing emoji headings are not duplicated and semantic permission symbols are retained',()=>{
  const blocks={facts:'🏠 户型：一居室',equipment:'🍳 厨房设备\n✅ 冰箱',conditions:'❌ 不接受宠物\n🚭 禁止吸烟',service:'✈️ 抵达登记（临时居住登记）：提供办理协助。'};
  for(let index=0;index<40;index++){
    const body=renderListingTemplate('description',index,blocks);
    assert.equal((body.match(/厨房设备/g)||[]).length,1);
    assert.equal((body.match(/抵达登记（临时居住登记）/g)||[]).length,1);
    assert(body.includes('❌ 不接受宠物'));
    assert(body.includes('🚭 禁止吸烟'));
    assert.doesNotMatch(body,/家具家电\n🍳 厨房设备|租住协助\n✈️/);
  }
});

test('existing generic headings vary in place without replacing inline facts or specific room labels',()=>{
  const source={style:'compact',facts:'🏠 户型：两居室\n📐 面积：54㎡\n🏡 公寓介绍：\n已有房源描述。',
    location:'📍 已确认的测试地址\n🌳 周边与房屋亮点：\n附近有公园',
    equipment:'🛋 家具家电：\n书桌、烤箱\n🍳 厨房设备\n✅ 洗衣机',
    transport:'🚇 交通：\n西南站，步行7分钟',conditions:'🚭 禁止吸烟',
    service:'✈️ 抵达登记（临时居住登记）：提供办理协助。'};
  const equipmentHeadings=new Set(),transportHeadings=new Set();
  for(let index=0;index<80;index++){
    const body=renderListingTemplate('description',index,source),plain=words(body);
    for(const value of ['户型：两居室','面积：54㎡','已有房源描述','已确认的测试地址','附近有公园',
      '书桌、烤箱','厨房设备','洗衣机','西南站，步行7分钟','禁止吸烟',source.service])assert(plain.includes(words(value)),value);
    assert.equal((body.match(/抵达登记（临时居住登记）/g)||[]).length,1);
    assert.doesNotMatch(body,/家具家电\n🛋|交通信息\n🚇|租住协助\n✈️/);
    for(const line of body.split('\n')){
      const heading=line.replace(/^[^\p{L}\p{N}]*/u,'').replace(/[：:]$/u,'');
      if(['家具家电','已有配置','配置清单','家具与设备','室内配置','房源配置','设备清单','配置资料'].includes(heading))equipmentHeadings.add(heading);
      if(['交通信息','出行参考','通勤资料','交通资料','地铁与出行','交通参考','出行资料','交通概况'].includes(heading))transportHeadings.add(heading);
    }
  }
  assert.equal(equipmentHeadings.size,8);assert.equal(transportHeadings.size,8);
});

test('compact mode does not automatically use shorter blocks or drop any rental condition',()=>{
  const source={...context,style:'compact',factsShort:'短版面积54㎡',equipmentShort:'短版书桌与烤箱',conditionsShort:'不应使用的短条件'};
  const body=renderListingTemplate('description',6,source);
  assert(words(body).includes(words(context.facts)));
  assert(words(body).includes(words(context.equipment)));
  assert(!body.includes('短版'));
  const opted=renderListingTemplate('description',6,{...source,useShortBlocks:true});
  assert(opted.includes('短版面积54㎡'));assert(opted.includes('短版书桌与烤箱'));
  assert(words(opted).includes(words(context.conditions)));
  assert(!opted.includes('不应使用的短条件'));
  assert(opted.includes('抵达登记（临时居住登记）'));
});

test('room grouping uses only supplied items and never invents a separate bedroom or hidden kitchen equipment',()=>{
  const groups=[{id:'bedroom',items:['床（尺寸待确认）','衣柜（至少2件）']},{id:'kitchen',items:['烤箱']},{id:'bathroom',items:['洗衣机']},{id:'living',items:[]}];
  for(let index=0;index<4;index++){
    const text=renderListingEquipment(groups,index);
    for(const item of ['床（尺寸待确认）','衣柜（至少2件）','烤箱','洗衣机'])assert(text.includes(item));
    assert.doesNotMatch(text,/独立卧室|双人床|冰箱|微波炉|灶台|全齐|齐全/);
    assert(text.includes('清洁与卫浴'));
  }
  assert.equal(renderListingEquipment([{id:'kitchen',items:[]}]),'');
});

test('tag rendering preserves the old audited inventory and every supplied factual or brand tag',()=>{
  for(let index=0;index<120;index++){
    const text=renderListingTemplate('tags',index,context);
    assert.equal(text,renderTemplate('tags',index,context));
    for(const tag of context.tags)assert(text.split(/\s+/u).includes(tag));
    assert.equal(getListingStyle('tags',index,context).family,'factual-tags');
    assert.doesNotMatch(text,/学生|女生|宠物|免费|悉尼/);
  }
});

test('empty blocks and Unicode data are handled without mutation or invented placeholders',()=>{
  assert.equal(renderListingTemplate('description',0,{}),'');
  assert.equal(renderListingTemplate('description',1,null),'');
  assert.equal(renderListingTemplate('description',1,[]),'');
  const input={...context,equipmentGroups:[{id:'study',items:['书桌','电脑']}],facts:'𫓧开头的自定义文字\n\ud800保留的输入',conditions:'🉑 可带孩子\n❌ 不接受宠物'};
  const before=JSON.stringify(input);
  for(let index=0;index<20;index++){
    const value=renderListingTemplate('description',index,input);
    assert(value.includes('𫓧开头的自定义文字'));
    assert(value.includes('\ud800保留的输入'));
    assert(value.includes('🉑 可带孩子'));
    assert(!value.includes('undefined'));
  }
  assert.equal(JSON.stringify(input),before);
  for(let index=0;index<80;index++)assert(!/[｜·/,，]$/.test(renderListingTemplate('title',index,{city:'莫斯科'})));
});
