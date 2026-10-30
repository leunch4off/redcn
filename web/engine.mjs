import {suggestTags} from './content-tools.mjs';
import {readSourceHtml,sourceFieldFacts} from './source-import.mjs';
import {normalizeRussian,detectFurniture,detectConditions} from './fact-evidence.mjs';
import {translateGeo,GEO_CATALOG} from './geo-catalog.mjs';
// Deterministic copy engine. No inferred furniture, routes, or journey times.
export const DICTIONARY = {
  'Москва':'莫斯科', 'СВАО':'东北行政区', 'район Лианозово':'利阿诺佐沃区', 'р-н Лианозово':'利阿诺佐沃区', 'Лианозово':'利阿诺佐沃', 'Алтуфьево':'阿尔图菲耶沃', 'Угличская ул.':'乌格利奇大街', 'Угличская улица':'乌格利奇大街', 'Марк':'马尔克', 'Бескудниково':'别斯库德尼科沃',
  'Саларьево парк':'萨拉里耶沃公园住宅区', 'Скандинавский':'斯堪的纳维亚住宅区',
  'Москвичка':'莫斯科人住宅区', 'Бунинские луга':'布宁草地住宅区',
  'Испанские кварталы':'西班牙街区', 'Тропарёво Парк':'特罗帕廖沃公园住宅区',
  'Прокшино':'普罗克希诺', 'Саларьево':'萨拉里耶沃', 'Филатов Луг':'菲拉托夫卢格',
  'Ольховая':'奥尔霍瓦亚', 'Корниловская':'科尔尼洛夫斯卡娅', 'Потапово':'波塔波沃',
  'Новомосковская':'新莫斯科', 'Коммунарка':'科穆纳尔卡', 'Румянцево':'鲁缅采沃',
  'Тропарёво':'特罗帕廖沃', 'Рассказовка':'拉斯卡佐夫卡', 'Каховская':'卡霍夫斯卡娅',
  'Севастопольская':'塞瓦斯托波尔', 'Зюзино':'久津诺', 'Бунинская аллея':'布宁林荫道',
  'Улица Горчакова':'戈尔恰科夫街', 'Проспект Вернадского':'维尔纳茨基大街',
  'Университет':'大学站', 'Беляево':'别利亚耶沃', 'Бауманская':'鲍曼', 'Сокол':'索科尔',
  'МГУ':'莫斯科国立大学', 'RUDN':'俄罗斯人民友谊大学', 'РУДН':'俄罗斯人民友谊大学',
  'РАНХиГС':'俄罗斯总统国民经济与公共管理学院', 'МГИМО':'莫斯科国际关系学院',
  'ВШЭ':'高等经济大学', 'РЭУ':'普列汉诺夫俄罗斯经济大学', 'МИСИС':'国立研究型技术大学MISIS',
  'китайские магазины':'附近有中国商店', 'китайский магазин':'附近有中国商店',
  'китайские рестораны':'附近有中餐厅', 'китайский ресторан':'附近有中餐厅',
  'рядом парк':'附近有公园', 'рядом лес':'附近有森林', 'закрытый двор':'封闭式庭院',
  'вид на реку':'窗外可见河景', 'окна во двор':'窗户朝向庭院', 'новый ремонт':'全新装修',
  'дизайнерский ремонт':'设计感装修', 'без мебели':'无家具，方便按自己的喜好布置',
  'два балкона':'两个独立阳台', 'балкон':'带阳台', 'лоджия':'带封闭式阳台',
  'гардеробная':'独立衣帽间', 'можно с детьми':'可带孩子',
  'виза прибытия (временная регистрация)':'抵达登记（临时居住登记）',
  'рабочий стол':'书桌', 'письменный стол':'书桌', 'компьютер':'电脑', 'пианино':'钢琴', 'фортепиано':'钢琴',
  'вытяжка':'抽油烟机', 'духовой шкаф':'烤箱', 'варочная панель':'灶台', 'кладовая':'储藏室', 'стеллаж':'置物架',
  'можно с животными':'宠物入住可与房东协商', 'можно с питомцами':'宠物入住可与房东协商',
  'краткосрочная аренда':'可协商短租', 'wi-fi':'Wi-Fi网络', 'Евроремонт':'欧式装修', 'На улицу и двор':'窗户朝向街道和庭院', 'совмещенный':'合用卫浴', 'Монолитный':'整体浇筑结构', 'Железобетонные':'钢筋混凝土', 'Индивидуальный проект':'独立设计', 'Центральное':'集中供暖', 'в Лианозовском парке':'位于利阿诺佐沃公园内（按房源描述）',
};

export const FEATURES = [
  ['bed','双人床','Двуспальная кровать','bedroom',['двуспальная кровать','двуспальную кровать','двуспальной кроватью']],
  ['bedAny','床（尺寸待确认）','Кровать · размер не указан','bedroom',['кровать']],
  ['mattress','床垫','Матрас','bedroom',['матрас']],
  ['wardrobe','衣柜','Шкаф','bedroom',['шкаф','шкафы','шкафом']],
  ['slidingWardrobe','推拉门衣柜','Шкаф-купе','bedroom',['шкаф-купе']],
  ['nightstands','床头柜','Прикроватные тумбы','bedroom',['прикроватные тумбы']],
  ['roomFurniture','房间家具','Мебель в комнатах','bedroom',['мебель в комнатах']],
  ['desk','学习桌','Рабочий стол','bedroom',['письменный стол','рабочий стол','учебный стол']],
  ['sofa','客厅沙发','Диван','living',['диван','диваном']],
  ['armchair','单人扶手椅','Кресло','living',['кресло']],
  ['tvStand','电视柜','ТВ-тумба · без вывода о телевизоре','living',['тв-тумба']],
  ['tv','电视','Телевизор','living',['телевизор','телевизором','тв']],
  ['computer','电脑','Компьютер','living',['компьютер','моноблок']],
  ['projector','投影仪','Проектор','living',['проектор']],
  ['homeCinema','家庭影院','Домашний кинотеатр','living',['домашний кинотеатр']],
  ['piano','钢琴','Пианино / фортепиано','living',['пианино','фортепиано','рояль']],
  ['ac','空调','Кондиционер','living',['кондиционер','кондиционером']],
  ['internet','网络','Интернет / Wi-Fi','living',['wi-fi','wifi','интернет']],
  ['fridge','冰箱','Холодильник','kitchen',['холодильник','холодильником']],
  ['stove','炉灶','Плита','kitchen',['плита','плитой','варочная панель','электроплита']],
  ['oven','烤箱','Духовка','kitchen',['духовка','духовой шкаф','духовым шкафом']],
  ['hood','抽油烟机','Кухонная вытяжка','kitchen',['вытяжка']],
  ['sink','水槽','Раковина / мойка','kitchen',['раковина','мойка']],
  ['microwave','微波炉','Микроволновка','kitchen',['микроволновка','микроволновая печь','свч']],
  ['dishwasher','洗碗机','Посудомоечная машина','kitchen',['посудомоечная машина','посудомоечная','посудомойка']],
  ['kettle','电热水壶','Чайник','kitchen',['чайник','чайником']],
  ['table','餐桌','Обеденный стол','kitchen',['обеденный стол','стол и стулья','стол со стульями']],
  ['chairs','餐椅','Стулья','kitchen',['стулья']],
  ['kitchenFurniture','厨房橱柜','Кухонная мебель','kitchen',['мебель на кухне']],
  ['washer','洗衣机','Стиральная машина','bathroom',['стиральная машина','стиральная','стиралка']],
  ['dryer','烘干机','Сушильная машина','bathroom',['сушильная машина','сушильная']],
  ['bath','浴缸','Ванна','bathroom',['ванна','ванной']],
  ['shower','淋浴设施','Душ','bathroom',['душ','душевая кабина','душевой кабиной']],
  ['washbasin','洗手盆','Раковина в ванной / умывальник','bathroom',['умывальник','рукомойник']],
  ['closet','独立衣帽间','Гардеробная','storage',['гардеробная','гардеробной']],
  ['pantry','储藏室','Кладовая','storage',['кладовая']],
  ['shelves','置物架','Стеллажи / полки','storage',['стеллаж','полки']],
  ['balcony','阳台','Балкон','storage',['балкон','балконом']],
  ['loggia','封闭式阳台','Лоджия','storage',['лоджия']],
];

export const DEFAULT_PREFS = {
  benefits:'✅ 免费接送到家\n✅ 协助搬运行李\n✅ 中文租赁文件翻译\n✅ 协助核实房东及租赁文件\n✅ 中文沟通，协助看房、签约和入住\n❤️ 入住后也可以随时联系我们。像朋友一样，陪您适应莫斯科生活，遇到问题一起想办法。',
  contact:'', brandTags:'#齐工作室 #齐房产 #莫斯科租房信息 #莫斯科租房中介推荐',
};

export const fold = s => String(s ?? '').toLowerCase().replaceAll('ё','е');
const escape = s => s.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
const catalogMetroNames=[...new Set(GEO_CATALOG.records.filter(row=>row.kind==='metro').map(row=>row.ru))].sort((a,b)=>b.length-a.length);
const catalogMetroByName=new Map(catalogMetroNames.map(name=>[fold(name),name]));
const catalogMetroExpression=new RegExp('(?<![а-яa-z])('+catalogMetroNames.map(name=>escape(name).replaceAll('ё','[ёе]')).join('|')+')(?![а-яa-z])','giu');
export function translate(text, rules) {
  return translateGeo(text,rules);
}

export function rubles(value) { return Number(value).toLocaleString('zh-CN',{maximumFractionDigits:2}); }
export function wan(value, digits=2) { return Number((Number(value)/10000).toFixed(digits))+'W'; }
function kind(v) {
  if (v.layout==='studio') return 'Studio';
  if (v.layout==='euro2') return '一室一厅';
  if (v.layout==='euro3') return '两室一厅';
  return ({1:'独立一居',2:'独立两居',3:'三居室',4:'四居室'})[v.rooms] || (v.rooms?v.rooms+'居室':'整套公寓');
}
const modes={walk:'步行',metro:'地铁',bus:'公交',public:'公共交通',car:'驾车',unknown:'出行方式待确认','步行':'步行','乘坐地铁':'地铁','乘坐公交':'公交'};
export function parseRoutes(raw) {
  return String(raw||'').split('\n').map(s=>s.trim()).filter(Boolean).map(s=>{
    const [name,time='',mode='walk',line='']=s.split('|').map(x=>x.trim());
    return {name,time,mode,line};
  });
}
export function generateListing(v,rules=DICTIONARY,prefs=DEFAULT_PREFS) {
  v={...v};
  const sourceConditions=detectConditions(v.sourceDescription||v.sourceAudit?.description||'').facts;
  const sharingAllowed=Number(v.rooms)===2&&v.separate===true&&!['studio','euro2'].includes(v.layout);
  const unknown=[],problems=[];
  const zh=s=>{const t=translate(s,rules);if(/[а-яё]/i.test(t))unknown.push(String(s));return t;};
  const positive=k=>Number(v[k])>0;
  for(const k of ['price','deposit','area','kitchen','living','bedroomArea','floor','floors','occupants','registrationPeople','registrationMonths']){
    if(v[k]!==undefined && v[k]!=='' && (!Number.isFinite(Number(v[k]))||Number(v[k])<0))problems.push('Проверьте значение: '+k);
  }
  if(positive('floor')&&positive('floors')&&Number(v.floor)>Number(v.floors))problems.push('Этаж выше этажности дома');
  if(positive('kitchen')&&positive('area')&&Number(v.kitchen)>Number(v.area))problems.push('Кухня больше общей площади');
  const routes=parseRoutes(v.transit);
  if(!routes.length&&v.metro)routes.push({name:v.metro,time:v.walk||'',mode:'walk',line:''});
  const universities=parseRoutes(v.universities);
  if(!universities.length&&v.university)universities.push({name:v.university,time:v.uniTime||'',mode:v.uniMode||'public',line:''});
  const layout=kind(v), area=positive('area')?v.area+'㎡':'', price=positive('price')?wan(v.price):'';
  const main=routes[0],metro=main?zh(main.name):'';
  const featureSet=new Set(v.features||[]);if(['bed','bedAny','slidingWardrobe','wardrobe','desk','nightstands'].some(id=>featureSet.has(id)))featureSet.delete('roomFurniture');
  const highlight=zh(v.highlight||'');
  let titles=[
    ['🔥莫斯科'+price+layout,area,main?metro+(main.time?' '+(modes[main.mode]||'')+main.time+'分钟':''):null].filter(Boolean).join('｜'),
    ['🏡莫斯科'+area+layout,featureSet.has('bed')?'独立大床':null,featureSet.has('ac')?'空调':null,price?'月租'+price:null].filter(Boolean).join('｜'),
    sharingAllowed&&positive('price')?['🚨莫斯科'+price+'整套'+layout, '2人分摊参考：人均'+wan(Number(v.price)/2),area].filter(Boolean).join('｜'):
      ['✈️莫斯科'+price+layout,metro?((main?.mode==='walk'&&Number(main.time)<=10)?'近':'')+metro:null,'协助抵达登记'].filter(Boolean).join('｜'),
  ];
  const variation=Math.abs(Number(v.titleVariant)||0);
  const copyVariation=Math.abs(Math.trunc(Number(v.copyVariant)||0));
  const hooks=[];if(featureSet.has('ac'))hooks.push('空调');if(featureSet.has('bed'))hooks.push('双人床');if(featureSet.has('closet'))hooks.push('衣帽间');if(featureSet.has('dishwasher'))hooks.push('洗碗机');
  if(variation%3===1)titles=[['🏡'+area+layout,metro||'莫斯科',hooks.join('＋')||'整套出租',price].filter(Boolean).join('｜'),...titles.slice(0,2)];
  if(variation%3===2)titles=[['✈️莫斯科'+price,area+layout,'协助抵达登记'].filter(Boolean).join('｜'),titles[2],titles[1]];
  if(variation>=3){
    const anchors=[area,metro?metro+(main.time?' '+(modes[main.mode]||'公共交通')+main.time+'分钟':''):null,...hooks].filter(Boolean);
    const start=Math.floor(variation/3)%Math.max(anchors.length,1);
    const ordered=[...anchors.slice(start),...anchors.slice(0,start)];
    const emojis=['🔥','🏡','✨','💗','✈️','🌿'];
    titles=titles.map((_,i)=>[emojis[(variation+i)%emojis.length]+'莫斯科'+price+layout,...ordered.slice(i).concat(ordered.slice(0,i)).slice(0,2)].filter(Boolean).join('｜'));
  }
  const lines=[]; const push=(...s)=>lines.push(...s);
  let phraseSlot=0;
  const wording=(...choices)=>{const salt=++phraseSlot;const mixed=copyVariation===0?0:((Math.imul(copyVariation+salt*73,2654435761)>>>0)^Math.floor(copyVariation/(salt+1)))>>>0;return choices[mixed%choices.length];};
  const equipmentLabel=x=>{
    const count=Number(v.featureCounts?.[x[0]]),minimum=v.sourceAudit?.furniture?.find(row=>row.id===x[0])?.countKind==='minimum';
    return x[1]+(Number.isFinite(count)&&count>1?'（'+(minimum?'至少':'')+count+'件）':'');
  };
  if(v.style==='story')push(wording('🇷🇺 想在莫斯科找一套适合自己生活节奏的住房？这套'+(area?area+'的':'')+layout+'可以了解一下！','🏡 在莫斯科寻找下一处住处？先看看这套'+(area?area+'的':'')+layout+'，再了解位置、配置和租赁条件。','🌿 莫斯科租房参考：这套'+(area?area+'的':'')+layout+'，位置与配置信息整理在下面。'),'');
  if(v.address)push('📍 '+zh(v.address));
  else if(v.complex||v.districtZh)push('📍 莫斯科｜'+zh(v.complex||v.districtZh));
  if(v.campus)push('🎓 校区：'+zh(v.campus));
  if(v.layout==='euro2')push('🏠 户型：一室一厅｜独立卧室＋厨房客厅');
  else if(v.layout==='euro3')push('🏠 户型：两室一厅｜两间卧室＋厨房客厅');
  else if(v.layout==='studio')push('🏠 户型：开放式Studio');
  else push('🏠 户型：'+layout+(v.separate?'｜房间独立':''));
  if(area)push('📐 面积：'+area);
  if(positive('living'))push('🛋 居住面积：'+v.living+'㎡');
  if(positive('kitchen'))push('🍳 厨房：'+v.kitchen+'㎡');
  if(positive('bedroomArea'))push('🛏 卧室：'+v.bedroomArea+'㎡');
  if(v.floor||v.floors)push('🏢 楼层：'+(v.floor||'待确认')+'/'+(v.floors||'待确认')+'层');
  if(v.renovation)push('✨ 装修：'+zh(v.renovation));
  if(v.windows)push('🪟 '+zh(v.windows));
  if(v.bathroom)push('🛁 卫浴：'+zh(v.bathroom).replace(/^(\d+)\s+合用卫浴/,'$1间合用卫浴'));
  if(v.buildingYear)push('🏘 建成年份：'+v.buildingYear);
  if(v.ceiling)push('📏 层高：'+v.ceiling+'米');
  if(positive('price'))push('💰 租金：'+price+'/月（'+rubles(v.price)+'₽）');
  if(v.deposit!==''&&v.deposit!==undefined)push('🔐 押金：'+(Number(v.deposit)===0?'无需押金':wan(v.deposit)+'（'+rubles(v.deposit)+'₽）'));
  if(v.commission!==''&&v.commission!==undefined)push('🤝 中介费：'+(Number(v.commission)===0?'无需支付中介费':v.commission+'%'));
  if(v.prepayMonths)push('📄 预付：'+v.prepayMonths+'个月');
  if(v.contractMonths)push('📅 描述所列合同期限：'+v.contractMonths+'个月');
  if(v.minMonths&&v.leaseTerm==='long')push('📅 卡片所列最低租期：'+v.minMonths+'个月'+(v.contractMonths&&Number(v.contractMonths)!==Number(v.minMonths)?'（与描述不一致，请确认）':''));
  if(highlight)push('',wording('🔥 最大亮点：','✨ 值得关注：','💡 房源亮点：')+highlight);
  if(v.rentFreeMonths&&Number(v.rentFreeMonths)>0)push('🎁 房东提供'+v.rentFreeMonths+'个月装修免租期，具体条件以合同为准。');
  if(v.description)push('',wording('🏡 公寓介绍：','🏡 走进这套公寓：','🏡 居住空间：'),zh(v.description));
  else {
    const lead=wording('🏡 房源介绍：','🏡 关于这套住房：','🌿 莫斯科租房参考：','🏠 空间与配置：','✨ 这套公寓的信息：');
    const apartmentHook=featureSet.has('desk')?'配有书桌，工作学习更方便':featureSet.has('piano')?'室内配有钢琴':featureSet.has('closet')?'设有独立衣帽间':featureSet.has('pantry')?'设有独立储藏室':featureSet.has('computer')?'室内配有电脑':v.separate?'房间彼此独立':'';
    const intro=[area?area+'的'+layout:layout,apartmentHook,v.occupants?'房源描述注明可入住'+v.occupants+'人':null].filter(Boolean).join('，')+'。';
    const descriptions=[intro,'这套'+intro,'先了解面积、租金与入住条件：'+intro,'房源资料整理如下：'+intro];
    push('',lead,wording(...descriptions));
  }
  if(v.style!=='compact'){
    const groups=[['bedroom','🛏️ 独立卧室','休息区域配有'],['living','🛋️ 生活空间','生活区域配有'],['kitchen','🍳 厨房配置','厨房配有'],['bathroom','🛁 卫生间','卫生间配有'],['storage','👗 储物与阳台','收纳与附属设施包括']];
    for(const [key,title,intro] of groups){const f=FEATURES.filter(x=>x[3]===key&&featureSet.has(x[0]));if(!f.length)continue;push('',wording(title,title+'清单',title+'信息'));if(v.style==='story')push(wording(intro,'此区域配备','配置包括')+f.map(equipmentLabel).join('、')+'。');else push(...f.map(x=>wording('✅ ','✔️ ','▪️ ')+equipmentLabel(x)));}
  }else{const f=FEATURES.filter(x=>featureSet.has(x[0]));if(f.length){push('',wording('🛋 家具家电：','🛋 室内配置：','🛋 已配备：','🛋 家具清单：'));if(copyVariation%3===0)push(f.map(equipmentLabel).join(' · '));else if(copyVariation%3===1){for(const key of ['kitchen','bedroom','living','bathroom','storage']){const group=f.filter(x=>x[3]===key);if(group.length)push(group.map(equipmentLabel).join('、'));}}else push(...f.map(x=>wording('✅ ','✔️ ','▪️ ')+equipmentLabel(x)));}}
  const missing=(v.sourceAudit?.furniture||[]).filter(row=>row.status==='absent'&&(row.matches.some(m=>m.source==='Описание'&&m.status==='absent')||['tv','washer'].includes(row.id)));
  if(missing.length)push('','📌 房源注明未配备：'+missing.map(row=>FEATURES.find(f=>f[0]===row.id)?.[1]).filter(Boolean).join('、')+'。');
  if(v.details)push('','🌳 周边与房屋亮点：',zh(v.details));
  const routeLine=r=>zh(r.name)+(r.time?'｜'+(modes[r.mode]||'公共交通')+'约'+r.time+'分钟':'｜时间待确认')+(r.line?'｜'+r.line.replace(/^([0-9]+)$/,'$1号线'):'');
  if(routes.length)push('',wording('🚇 交通：','🚇 地铁与出行：','🚇 位置与交通：'),...routes.map(r=>'▪️ '+routeLine(r)));
  if(v.route)push('🗺 '+zh(v.route));
  if(universities.length)push('','🎓 大学通勤参考：',...universities.map(r=>'▪️ '+routeLine(r)));
  if(sharingAllowed&&positive('price')){
    push('','💸 合租预算参考：');
    for(const n of [2,4])push(n+'人：约'+wan(Number(v.price)/n)+'（'+rubles(Math.round(Number(v.price)/n))+'₽）/人/月');
    push('👥 以上仅为租金分摊示例，不含其他费用；入住人数及合租安排需与房东确认。');
  }
  const utilities={included:'💡 租金包含各项费用。',meters:'💡 物业费已包含，仅水电按表计费。',extra:'💡 物业及水电等费用另付，具体金额请确认。'};
  if(utilities[v.utilities])push('',utilities[v.utilities]);
  if(v.shortTerm&&v.shortTermPolicy!=='no')push('📅 可协商短租'+(v.minMonths?'，'+v.minMonths+'个月起':'')+'，具体条件请咨询。');
  if(v.children&&!['no','conflict'].includes(v.childrenPolicy)&&sourceConditions.childrenPolicy!=='no')push('👶 可带孩子');
  if(v.pets&&!['no','conflict','negotiable'].includes(v.petPolicy)&&sourceConditions.petPolicy!=='no')push('🐾 可带宠物，具体条件请与房东确认');
  if(v.smokingPolicy==='no')push('🚭 仅限不吸烟的租客');
  if(v.guestPolicy==='no')push('👥 房东要求不接待访客或聚会');
  if(v.subletPolicy==='no')push('📝 不得将公寓转租给他人');
  if(v.shortTermPolicy==='no')push('📅 不接受按日短租');
  {
    let registration='抵达登记（临时居住登记）：提供办理协助';
    if(positive('registrationMonths'))registration+='（'+v.registrationMonths+'个月）';
    if(positive('registrationPeople'))registration+='，最多'+v.registrationPeople+'人';
    push('','✈️ '+registration+'。');
  }
  if(v.pickup)push('🚕 免费接送到入住地点'+(v.luggage?'，协助搬运行李':'')+'。');
  if(v.arrival)push('📋 免费协助了解抵达后的相关手续。');
  if(v.support&&prefs.benefits)push('','🇨🇳 我们的优势：',prefs.benefits);
  if(prefs.contact)push('','📩 联系我们：'+prefs.contact);
  else push('',wording('📩 欢迎中文咨询，了解房源详情并预约看房！','💌 想了解租赁条件或安排看房？欢迎用中文私信我们！','📨 欢迎中文留言咨询，我们协助了解房源并预约看房。'));
  const tags=['#莫斯科租房','#莫斯科留学','#莫斯科生活'];
  if(v.rooms)tags.push('#莫斯科'+({1:'一居室',2:'两居室',3:'三居室'}[v.rooms]||'租房'));
  for(const r of routes)tags.push('#'+zh(r.name).replace(/[\s#]/g,''));
  for(const u of universities)tags.push('#'+zh(u.name).replace(/[\s#]/g,''));
  if(prefs.brandTags)tags.push(...prefs.brandTags.split(/\s+/).filter(Boolean).map(t=>t.startsWith('#')?t:'#'+t));
  const tagSuggestions=suggestTags(v,rules,translate,parseRoutes);
  const finalTags=tagSuggestions.map(x=>'#'+x.tag);
  if(prefs.brandTags)finalTags.push(...prefs.brandTags.split(/\s+/).filter(Boolean).map(t=>t.startsWith('#')?t:'#'+t));
  return {titles,body:lines.join('\n'),tags:[...new Set(finalTags)].join(' '),tagSuggestions,unknown:[...new Set(unknown)],problems};
}

export function extractFacts(text,rules=DICTIONARY) {
  const out={features:[]}, warnings=[];const t=normalizeRussian(text).replace(/\u00a0/g,' ');
  const capture=(key,re)=>{const m=t.match(re);if(m)out[key]=m[1].replace(/\s/g,'').replace(',','.');};
  capture('area',/(?:общая\s*площадь\s*[:—-]?\s*)?(\d+(?:[.,]\d+)?)\s*(?:м²|м2|кв\.?\s*м|㎡)/i);
  capture('rooms',/(\d+)\s*[- ]?(?:комн|комнат)/i);
  const f=t.match(/(\d+)\s*\/\s*(\d+)\s*(?:эт|楼|层)/i)||t.match(/этаж\s*[:—-]?\s*(\d+)\s*(?:из|\/)\s*(\d+)/i);if(f){out.floor=f[1];out.floors=f[2];}
  else {capture('floor',/(?:этаж\s*[:—-]?\s*)(\d+)/i);capture('floors',/(\d+)\s*[- ]?этажн/i);}
  const price=t.match(/(?:аренда|цена|стоимость|月租|租金)\s*[:：—-]?\s*(\d[\d\s]*(?:[.,]\d+)?)\s*(тыс\.?|[wW万])?\s*(?:₽|руб|卢布)?/i)
    ||t.match(/(\d[\d\s]*(?:[.,]\d+)?)\s*(тыс\.?|[wW万])?\s*(?:₽|руб)\s*(?:\/\s*(?:мес|月)|в месяц)/i);
  if(price)out.price=String(Number(price[1].replace(/\s/g,'').replace(',','.'))*(price[2]?/[wW万]/.test(price[2])?10000:1000:1));
  const deposit=t.match(/(?:залог|депозит|押金)\s*[:：—-]?\s*(\d[\d\s]*(?:[.,]\d+)?)\s*(тыс\.?|[wW万])?/i);
  if(deposit)out.deposit=String(Number(deposit[1].replace(/\s/g,'').replace(',','.'))*(deposit[2]?/[wW万]/.test(deposit[2])?10000:1000:1));
  if(/без залога/i.test(t))out.deposit='0';
  capture('kitchen',/(?:кухня|кухни|кухня-гостиная)\s*[:—-]?\s*(\d+(?:[.,]\d+)?)\s*(?:м|㎡)/i);
  capture('living',/(?:жилая\s*(?:площадь)?)\s*[:—-]?\s*(\d+(?:[.,]\d+)?)\s*(?:м|㎡)/i);
  if(/студия|studio/i.test(t))out.layout='studio';
  if(/евро[ -]?2|евродвушк/i.test(t))out.layout='euro2';
  if(/евро[ -]?3|евротрешк|евротрёшк/i.test(t))out.layout='euro3';
  const furniture=detectFurniture(text,FEATURES);
  out.features=furniture.filter(r=>r.status==='present').map(r=>r.id);
  out.featureCounts=Object.fromEntries(furniture.filter(r=>r.count).map(r=>[r.id,r.count]));
  for(const row of furniture.filter(r=>['planned','conflict'].includes(r.status)))warnings.push('Проверьте наличие: '+row.label+' ('+row.status+')');
  const routes=[];
  for(const m of t.matchAll(catalogMetroExpression)){
    const name=catalogMetroByName.get(fold(m[0]));if(routes.some(row=>row.name===name))continue;
    const tail=t.slice(m.index+m[0].length,m.index+m[0].length+95).split(/[\n;]/)[0];
    const time=tail.match(/^[^\d]*?(\d+(?:\s*[–—-]\s*\d+)?)\s*(?:минут|мин\.?|分钟)/i);
    routes.push({name,time:time?time[1].replace(/\s/g,''):'',mode:/автобус|транспорт|公交/i.test(tail)?'bus':'walk',at:m.index});
  }
  routes.sort((a,b)=>a.at-b.at);
  if(routes.length){out.metro=routes[0].name;out.walk=routes[0].time;out.transit=routes.map(r=>[r.name,r.time,r.mode].join(' | ')).join('\n');}
  const details=Object.keys(rules).filter(k=>k.length>5&&/магазин|ресторан|вид на|окна во|ремонт|двор|парк|лес|без мебели/.test(k)&&fold(t).includes(fold(k)));
  if(details.length)out.details=details.join(', ');
  if(/(?:можно|разрешено|допускается)\s+с детьми/i.test(t))out.children=true;
  if(/(?:можно|разрешено|обсужда[а-я]+)\s+(?:с\s+)?(?:животными|питомцами)/i.test(t))out.pets=true;
  if(/только\s+(?:вода|свет|воду|свету|счетчик|счётчик)|вода и свет|счетчикам|счётчикам/i.test(t))out.utilities='meters';
  const conditions=detectConditions(text);Object.assign(out,conditions.facts);if(!out.sourceDescription)out.sourceDescription=String(text||'');
  warnings.push('Извлечены только найденные факты. Маршруты до вузов, услуги и доступность объекта проверяются отдельно.');
  out.sourceAudit={version:1,stats:{},fields:conditions.evidence,furniture,warnings,description:String(text||'')};
  return {facts:out,warnings,furniture,evidence:conditions.evidence};
}

export function analyzeListingSource(input,rules=DICTIONARY){
  const source=typeof input==='string'?readSourceHtml(input):input;
  const simple=extractFacts(source.text||source.description||'',rules),structured=sourceFieldFacts(source);
  const conditionText=(source.description||'')+'\n'+(source.records||[]).filter(r=>['OfferFactItem','FeaturesItem'].includes(r.name)).map(r=>r.text).join('\n');
  const conditions=detectConditions(conditionText);
  for(const [key,flag,policy] of [['petsAllowed','pets','petPolicy'],['childrenAllowed','children','childrenPolicy']]){
    const permission=source.offer?.bargainTerms?.[key];
    if(typeof permission!=='boolean')continue;
    const contradictory=conditions.facts[policy]==='no'&&permission===true||conditions.facts[policy]==='yes'&&permission===false;
    conditions.facts[policy]=contradictory?'conflict':permission?'yes':'no';
    conditions.facts[flag]=permission&&!contradictory;
    conditions.evidence.push({field:flag,value:conditions.facts[flag],source:'JSON bargainTerms.'+key,text:key+' = '+permission});
    if(contradictory)conditions.warnings.push('Противоречие разрешения: '+key+'. Положительная формулировка исключена из описания.');
  }
  const furniture=detectFurniture((source.description||source.text||'')+'\n'+(source.records||[]).filter(r=>r.name==='FeaturesItem').map(r=>r.text).join('\n'),FEATURES,source.offer||{});
  const facts={...simple.facts,...structured.facts,...conditions.facts,source:source.text||source.description||'',sourceDescription:source.description||'',features:furniture.filter(r=>r.status==='present').map(r=>r.id),featureCounts:Object.fromEntries(furniture.filter(r=>r.count).map(r=>[r.id,r.count])),photos:source.images||[],relatedOffers:source.related||[]};
  const warnings=[...structured.warnings,...conditions.warnings];
  if(facts.contractMonths&&facts.minMonths&&Number(facts.contractMonths)!==Number(facts.minMonths))warnings.push('Противоречие: в описании договор на '+facts.contractMonths+' месяцев, в условиях карточки — от '+facts.minMonths+' месяцев. Требуется уточнение.');
  if(facts.registrationPolicy==='not_available')warnings.push('Источник: собственник не предоставляет регистрацию. Услуга студии по содействию оформлению указывается отдельно; это не обещание регистрации по адресу объекта.');
  for(const row of furniture.filter(r=>r.status==='conflict'||r.status==='planned'))warnings.push('Мебель: '+row.label+' — '+(row.status==='conflict'?'противоречие источников':'только планируется установка'));
  if(!facts.price)warnings.push('Цена не найдена; заполните вручную.');
  warnings.push('Маршруты до вузов, кампус, услуги и наличие китайских магазинов не определяются без подтверждённых данных.');
  const fields=[...structured.evidence,...conditions.evidence];
  facts.sourceAudit={version:1,stats:source.stats||{},fields,furniture,warnings,description:source.description||'',unmapped:(source.records||[]).filter(r=>!fields.some(e=>e.text===r.text)).slice(0,100)};
  return {facts,warnings,sourceAudit:facts.sourceAudit};
}
