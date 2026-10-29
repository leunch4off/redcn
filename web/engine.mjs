import {suggestTags} from './content-tools.mjs';
// Deterministic copy engine. No inferred furniture, routes, or journey times.
export const DICTIONARY = {
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
  'можно с животными':'宠物入住可与房东协商', 'можно с питомцами':'宠物入住可与房东协商',
  'краткосрочная аренда':'可协商短租', 'wi-fi':'Wi-Fi网络',
};

export const FEATURES = [
  ['bed','双人床＋床垫','Двуспальная кровать','bedroom',['двуспальная кровать','двуспальную кровать','двуспальной кроватью']],
  ['wardrobe','大型衣柜','Шкаф','bedroom',['шкаф','шкафы','шкафом']],
  ['desk','学习桌＋椅子','Рабочий стол','bedroom',['письменный стол','рабочий стол','учебный стол']],
  ['sofa','客厅沙发','Диван','living',['диван','диваном']],
  ['tv','电视','Телевизор','living',['телевизор','телевизором','тв']],
  ['ac','空调','Кондиционер','living',['кондиционер','кондиционером']],
  ['internet','Wi-Fi网络','Интернет / Wi-Fi','living',['wi-fi','wifi','интернет']],
  ['fridge','冰箱','Холодильник','kitchen',['холодильник','холодильником']],
  ['stove','炉灶','Плита','kitchen',['плита','плитой','варочная панель','электроплита']],
  ['oven','烤箱','Духовка','kitchen',['духовка','духовой шкаф','духовым шкафом']],
  ['microwave','微波炉','Микроволновка','kitchen',['микроволновка','микроволновая печь','свч']],
  ['dishwasher','洗碗机','Посудомоечная машина','kitchen',['посудомоечная машина','посудомоечная','посудомойка']],
  ['kettle','电热水壶','Чайник','kitchen',['чайник','чайником']],
  ['table','餐桌＋椅子','Обеденный стол','kitchen',['обеденный стол','стол и стулья','стол со стульями']],
  ['washer','洗衣机','Стиральная машина','bathroom',['стиральная машина','стиральная','стиралка']],
  ['dryer','烘干机','Сушильная машина','bathroom',['сушильная машина','сушильная']],
  ['bath','浴缸','Ванна','bathroom',['ванна','ванной']],
  ['shower','淋浴设施','Душ','bathroom',['душ','душевая кабина','душевой кабиной']],
  ['closet','独立衣帽间','Гардеробная','storage',['гардеробная','гардеробной']],
  ['balcony','阳台','Балкон','storage',['балкон','балконом']],
];

export const DEFAULT_PREFS = {
  benefits:'✅ 免费接送到家\n✅ 协助搬运行李\n✅ 中文租赁文件翻译\n✅ 协助核实房东及租赁文件\n✅ 中文沟通，协助看房、签约和入住\n❤️ 入住后也可以随时联系我们。像朋友一样，陪您适应莫斯科生活，遇到问题一起想办法。',
  contact:'', brandTags:'#齐工作室 #齐房产 #莫斯科租房信息 #莫斯科租房中介推荐',
};

export const fold = s => String(s ?? '').toLowerCase().replaceAll('ё','е');
const escape = s => s.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
export function translate(text, rules) {
  const entries = Object.entries(rules).filter(([k,v])=>k.trim() && typeof v==='string').sort((a,b)=>b[0].length-a[0].length);
  if (!entries.length) return String(text ?? '');
  const lookup = new Map(entries.map(([k,v])=>[fold(k),v]));
  const expression = entries.map(([k])=>escape(k).replaceAll('ё','[ёе]').replaceAll('Ё','[ёе]')).join('|');
  return String(text ?? '').replace(new RegExp(expression,'giu'),match=>lookup.get(fold(match)) ?? match);
}

export function rubles(value) { return Number(value).toLocaleString('zh-CN',{maximumFractionDigits:2}); }
export function wan(value, digits=2) { return Number((Number(value)/10000).toFixed(digits))+'W'; }
function kind(v) {
  if (v.layout==='studio') return 'Studio';
  if (v.layout==='euro2') return '一室一厅';
  if (v.layout==='euro3') return '两室一厅';
  return ({1:'独立一居',2:'独立两居',3:'三居室',4:'四居室'})[v.rooms] || (v.rooms?v.rooms+'居室':'整套公寓');
}
const modes={walk:'步行',metro:'地铁',bus:'公交',public:'公共交通','步行':'步行','乘坐地铁':'地铁','乘坐公交':'公交'};
export function parseRoutes(raw) {
  return String(raw||'').split('\n').map(s=>s.trim()).filter(Boolean).map(s=>{
    const [name,time='',mode='walk',line='']=s.split('|').map(x=>x.trim());
    return {name,time,mode,line};
  });
}
export function generateListing(v,rules=DICTIONARY,prefs=DEFAULT_PREFS) {
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
  const featureSet=new Set(v.features||[]);
  const highlight=zh(v.highlight||'');
  let titles=[
    ['🔥莫斯科'+price+layout,area,main?metro+(main.time?' '+(modes[main.mode]||'')+main.time+'分钟':''):null].filter(Boolean).join('｜'),
    ['🏡莫斯科'+area+layout,featureSet.has('bed')?'独立大床':null,featureSet.has('ac')?'空调':null,price?'月租'+price:null].filter(Boolean).join('｜'),
    positive('occupants')&&positive('price')?['🚨莫斯科'+price+'整套'+layout, v.occupants+'人人均'+wan(Number(v.price)/Number(v.occupants)),area].filter(Boolean).join('｜'):
      ['✈️莫斯科'+price+layout,metro?'近'+metro:null,v.registration&&v.registration!=='none'?'免费协助登记':null].filter(Boolean).join('｜'),
  ];
  const variation=Math.abs(Number(v.titleVariant)||0);
  const hooks=[];if(featureSet.has('ac'))hooks.push('空调');if(featureSet.has('bed'))hooks.push('双人床');if(featureSet.has('closet'))hooks.push('衣帽间');if(featureSet.has('dishwasher'))hooks.push('洗碗机');
  if(variation%3===1)titles=[['🏡'+area+layout,metro||'莫斯科',hooks.join('＋')||'整套出租',price].filter(Boolean).join('｜'),...titles.slice(0,2)];
  if(variation%3===2)titles=[['✈️莫斯科'+price,area+layout,v.registration&&v.registration!=='none'?'免费协助登记':hooks.join('＋')||metro].filter(Boolean).join('｜'),titles[2],titles[1]];
  const lines=[]; const push=(...s)=>lines.push(...s);
  if(v.style==='story')push('🇷🇺 想在莫斯科找一套适合自己生活节奏的住房？这套'+(area?area+'的':'')+layout+'可以了解一下！','');
  if(v.complex||v.districtZh)push('📍 莫斯科｜'+zh(v.complex||v.districtZh));
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
  if(positive('price'))push('💰 租金：'+price+'/月（'+rubles(v.price)+'₽）');
  if(v.deposit!==''&&v.deposit!==undefined)push('🔐 押金：'+(Number(v.deposit)===0?'无需押金':wan(v.deposit)+'（'+rubles(v.deposit)+'₽）'));
  if(highlight)push('','🔥 最大亮点：'+highlight);
  if(v.rentFreeMonths&&Number(v.rentFreeMonths)>0)push('🎁 房东提供'+v.rentFreeMonths+'个月装修免租期，具体条件以合同为准。');
  if(v.description)push('','🏡 公寓介绍：',zh(v.description));
  if(v.style!=='compact'){
    const groups=[['bedroom','🛏️ 独立卧室','休息区域配有'],['living','🛋️ 生活空间','生活区域配有'],['kitchen','🍳 厨房配置','厨房配有'],['bathroom','🛁 卫生间','卫生间配有'],['storage','👗 储物与阳台','收纳与附属设施包括']];
    for(const [key,title,intro] of groups){const f=FEATURES.filter(x=>x[3]===key&&featureSet.has(x[0]));if(!f.length)continue;push('',title);if(v.style==='story')push(intro+f.map(x=>x[1]).join('、')+'。');else push(...f.map(x=>'✅ '+x[1]));}
  }else{const f=FEATURES.filter(x=>featureSet.has(x[0]));if(f.length)push('','🛋 家具家电：',...f.map(x=>'✅ '+x[1]));}
  if(v.details)push('','🌳 周边与房屋亮点：',zh(v.details));
  const routeLine=r=>zh(r.name)+(r.time?'｜'+(modes[r.mode]||'公共交通')+'约'+r.time+'分钟':'｜时间待确认')+(r.line?'｜'+r.line.replace(/^([0-9]+)$/,'$1号线'):'');
  if(routes.length)push('','🚇 交通：',...routes.map(r=>'▪️ '+routeLine(r)));
  if(v.route)push('🗺 '+zh(v.route));
  if(universities.length)push('','🎓 大学通勤参考：',...universities.map(r=>'▪️ '+routeLine(r)));
  if(positive('occupants')&&positive('price')){
    push('','💸 合租预算参考：');
    const max=Math.min(Math.floor(Number(v.occupants)),12);
    for(let n=2;n<=max;n++)push(n+'人：约'+wan(Number(v.price)/n)+'（'+rubles(Math.round(Number(v.price)/n))+'₽）/人/月');
    push('👥 入住人数及合租安排需与房东确认。');
  }
  const utilities={included:'💡 租金包含各项费用。',meters:'💡 物业费已包含，仅水电按表计费。',extra:'💡 物业及水电等费用另付，具体金额请确认。'};
  if(utilities[v.utilities])push('',utilities[v.utilities]);
  if(v.shortTerm)push('📅 可协商短租'+(v.minMonths?'，'+v.minMonths+'个月起':'')+'，具体条件请咨询。');
  if(v.children)push('👶 可带孩子');
  if(v.pets)push('🐾 宠物入住可与房东协商');
  if(v.registration&&v.registration!=='none'){
    let registration=v.registration==='provided'?'免费提供临时居住登记':'免费协助办理临时居住登记';
    if(positive('registrationMonths'))registration+='（'+v.registrationMonths+'个月）';
    if(positive('registrationPeople'))registration+='，最多'+v.registrationPeople+'人';
    push('','✈️ '+registration+'。');
  }
  if(v.pickup)push('🚕 免费接送到入住地点'+(v.luggage?'，协助搬运行李':'')+'。');
  if(v.arrival)push('📋 免费协助了解抵达后的相关手续。');
  if(v.support&&prefs.benefits)push('','🇨🇳 我们的优势：',prefs.benefits);
  if(prefs.contact)push('','📩 联系我们：'+prefs.contact);
  else push('','📩 欢迎中文咨询，了解房源详情并预约看房！');
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
  const out={features:[]}, warnings=[];const t=String(text||'').replace(/\u00a0/g,' ');
  const capture=(key,re)=>{const m=t.match(re);if(m)out[key]=m[1].replace(/\s/g,'').replace(',','.');};
  capture('area',/(?:общая\s*площадь\s*[:—-]?\s*)?(\d+(?:[.,]\d+)?)\s*(?:м²|м2|кв\.?\s*м|㎡)/i);
  capture('rooms',/(\d+)\s*[- ]?(?:комн|комнат)/i);
  const f=t.match(/(\d+)\s*\/\s*(\d+)\s*(?:эт|楼|层)/i);if(f){out.floor=f[1];out.floors=f[2];}
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
  for(const [id,,, ,triggers] of FEATURES){
    let positive=false,negative=false;
    for(const trigger of triggers){
      const re=new RegExp('(?<![а-яёa-z])'+escape(trigger)+'(?![а-яёa-z])','giu');
      for(const match of t.matchAll(re)){
        const before=fold(t.slice(Math.max(0,match.index-65),match.index));
        const after=fold(t.slice(match.index+match[0].length,match.index+match[0].length+30));
        if(/(?:нет|без|не будет|не установлен[ао]?|отсутствует|может установить|можно установить|установит|установят|будет установлен[ао]?)\s*(?:[а-я-]+\s*){0,2}$/.test(before)||/^\s*(?:отсутствует|не установлен|будет установлен|установят)/.test(after))negative=true;
        else positive=true;
      }
    }
    if(positive&&!negative)out.features.push(id);
    if(negative)warnings.push('Проверьте наличие: '+FEATURES.find(x=>x[0]===id)[2]);
  }
  const metroNames=['Саларьево','Филатов Луг','Прокшино','Коммунарка','Новомосковская','Потапово','Ольховая','Румянцево','Тропарёво','Каховская','Севастопольская','Зюзино','Рассказовка','Проспект Вернадского','Университет','Беляево','Бунинская аллея','Корниловская'];
  const routes=[];
  for(const name of metroNames){const re=new RegExp(escape(name).replaceAll('ё','[ёе]'),'giu');let m=re.exec(t);if(!m)continue;const tail=t.slice(m.index+name.length,m.index+name.length+95).split(/[\n;]/)[0];const time=tail.match(/^[^\d]*?(\d+(?:\s*[–—-]\s*\d+)?)\s*(?:минут|мин\.?|分钟)/i);routes.push({name,time:time?time[1].replace(/\s/g,''):'',mode:/автобус|транспорт|公交/i.test(tail)?'bus':'walk',at:m.index});}
  routes.sort((a,b)=>a.at-b.at);
  if(routes.length){out.metro=routes[0].name;out.walk=routes[0].time;out.transit=routes.map(r=>[r.name,r.time,r.mode].join(' | ')).join('\n');}
  const details=Object.keys(rules).filter(k=>k.length>5&&/магазин|ресторан|вид на|окна во|ремонт|двор|парк|лес|без мебели/.test(k)&&fold(t).includes(fold(k)));
  if(details.length)out.details=details.join(', ');
  if(/(?:можно|разрешено|допускается)\s+с детьми/i.test(t))out.children=true;
  if(/(?:можно|разрешено|обсужда[а-я]+)\s+(?:с\s+)?(?:животными|питомцами)/i.test(t))out.pets=true;
  if(/только\s+(?:вода|свет|воду|свету|счетчик|счётчик)|вода и свет|счетчикам|счётчикам/i.test(t))out.utilities='meters';
  warnings.push('Извлечены только найденные факты. Маршруты до вузов, услуги и доступность объекта проверяются отдельно.');
  return {facts:out,warnings};
}
