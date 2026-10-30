import {suggestTags} from './content-tools.mjs';
import {readSourceHtml,sourceFieldFacts} from './source-import.mjs';
import {normalizeRussian,detectFurniture,detectConditions} from './fact-evidence.mjs';
import {GEO_CATALOG} from './geo-catalog.mjs';
import {RENTAL_RULES,translateRussian} from './ru-zh-translator.mjs';
import {getTemplate,renderTemplate,catalogStats} from './template-catalog.mjs';
import {COMMUNITY_RENTAL_RULES,COMMUNITY_RENTAL_META} from './community-phrases.mjs';
import {UNIVERSITY_CATALOG,universityRules,resolveUniversity,resolveCampus,universityStats} from './university-catalog.mjs';
// Deterministic copy engine. No inferred furniture, routes, or journey times.
const engineCommunityKey=value=>String(value).toLowerCase().replaceAll('ё','е');
const engineCommunityMeanings=new Map(Object.entries(COMMUNITY_RENTAL_RULES).map(([ru,zh])=>[engineCommunityKey(ru),zh]));
export const DICTIONARY = {
  ...Object.fromEntries(Object.entries(RENTAL_RULES).map(([ru,zh])=>[ru,engineCommunityMeanings.get(engineCommunityKey(ru))||zh])),
  ...COMMUNITY_RENTAL_RULES,
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
  'краткосрочная аренда':'可协商短租', 'wi-fi':'Wi-Fi网络', 'Евроремонт':'现代装修', 'На улицу и двор':'窗户朝向街道和庭院', 'совмещенный':'卫生间与浴室合一', 'совмещённый':'卫生间与浴室合一', 'раздельный':'厕所与浴室分开', 'Монолитный':'整体浇筑结构', 'Железобетонные':'钢筋混凝土', 'Индивидуальный проект':'独立设计', 'Центральное':'集中供暖', 'в Лианозовском парке':'位于利阿诺佐沃公园内（按房源描述）',
  ...universityRules(),
};

export const FEATURES = [
  ['bed','双人床','Двуспальная кровать','bedroom',['двуспальная кровать','двуспальную кровать','двуспальной кроватью']],
  ['bedAny','床（尺寸待确认）','Кровать · размер не указан','bedroom',['кровать']],
  ['mattress','床垫','Матрас','bedroom',['матрас']],
  ['wardrobe','衣柜','Шкаф','bedroom',['шкаф','шкафы','шкафом']],
  ['slidingWardrobe','推拉门衣柜','Шкаф-купе','bedroom',['шкаф-купе']],
  ['nightstands','床头柜','Прикроватные тумбы','bedroom',['прикроватные тумбы']],
  ['roomFurniture','房间家具','Мебель в комнатах','bedroom',['мебель в комнатах']],
  ['desk','书桌','Рабочий стол','bedroom',['письменный стол','рабочий стол','учебный стол']],
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
export function translate(text, rules=DICTIONARY) {
  return translateRussian(text,{rules,mode:'safe',preservePunctuation:true}).text;
}

// Draft translation keeps uncertain terms visible for review; it never claims
// that a dictionary lookup is a verified translation of an entire document.
export function translateDraft(text,{rules=DICTIONARY,entries=[],mode='draft'}={}) {
  return translateRussian(text,{rules,entries,mode});
}
export function generationCapabilities(){
  return {templates:catalogStats(),curatedRules:Object.keys(DICTIONARY).length,
    communityPhrases:COMMUNITY_RENTAL_META.entryCount,
    universities:universityStats(),
    customText:{offline:true,unknown:'review',manualRulesPriority:true},
    variants:['titleVariant','copyVariant','tagVariant']};
}
const safeVariant=value=>{const n=Number(value);return Number.isFinite(n)?Math.abs(Math.trunc(n)):0;};


export function rubles(value) { return Number(value).toLocaleString('zh-CN',{maximumFractionDigits:2}); }
export function wan(value, digits=2) { return Number((Number(value)/10000).toFixed(digits))+'W'; }
function kind(v) {
  if (v.layout==='studio') return 'Studio';
  if (v.layout==='euro2') return '一室一厅';
  if (v.layout==='euro3') return '两室一厅';
  return ({1:'一居室',2:'两居室',3:'三居室',4:'四居室'})[v.rooms] || (v.rooms?v.rooms+'居室':'整套公寓');
}
const modes={walk:'步行',metro:'地铁',bus:'公交',public:'公共交通',car:'驾车',unknown:'出行方式待确认','步行':'步行','乘坐地铁':'地铁','乘坐公交':'公交'};
export function parseRoutes(raw) {
  return String(raw||'').split('\n').map(s=>s.trim()).filter(Boolean).map(s=>{
    const [name,time='',mode='walk',line='']=s.split('|').map(x=>x.trim());
    return {name,time,mode,line};
  });
}
function engineSchoolAliases(){
  const rows=[];
  for(const school of UNIVERSITY_CATALOG.universities)for(const alias of [school.ru,school.zh,...(school.aliases||[])]){
    if(typeof alias==='string'&&alias.length>=3&&resolveUniversity(alias)?.id===school.id)rows.push({alias,record:school,kind:'university'});
  }
  for(const campus of UNIVERSITY_CATALOG.campuses){
    const parent=UNIVERSITY_CATALOG.universities.find(row=>row.id===campus.universityId);if(!parent)continue;
    const parentAliases=[parent.ru,parent.zh,...(parent.aliases||[])].filter(alias=>typeof alias==='string'&&alias.length>=3&&resolveUniversity(alias)?.id===parent.id);
    for(const alias of [campus.ru,campus.zh,...(campus.aliases||[])])if(typeof alias==='string'&&resolveCampus(alias)?.id===campus.id&&parentAliases.some(name=>fold(alias).includes(fold(name))))rows.push({alias,record:campus,kind:'campus'});
  }
  return rows.sort((a,b)=>b.alias.length-a.alias.length);
}
let engineSchoolAliasCache;
export function extractUniversityReferences(text){
  const source=String(text||''),rows=engineSchoolAliasCache||(engineSchoolAliasCache=engineSchoolAliases()),schools=new Map(),campuses=new Map(),evidence=[];
  for(const row of rows){
    const expression=new RegExp('(?<![а-яёa-z0-9])'+escape(row.alias).replaceAll('ё','[ёе]')+'(?![а-яёa-z0-9])','giu');
    const match=expression.exec(source);if(!match||!confirmedDetailTrigger(source,row.alias))continue;
    const clause=source.slice(Math.max(0,match.index-80),match.index+row.alias.length+100).split(/[.!?;\n]/).find(part=>fold(part).includes(fold(row.alias)))||'';
    if(/[?？]|можно ли|есть ли|находится ли/i.test(clause))continue;
    if(row.kind==='campus'){
      if(!campuses.has(row.record.id)){campuses.set(row.record.id,row.record);evidence.push({field:'campus',value:row.record.ru,text:match[0],source:'Описание · точная площадка / адрес вуза'});}
      continue;
    }
    if(schools.has(row.record.id))continue;
    const tail=source.slice(match.index+match[0].length,match.index+match[0].length+90).split(/[.!?;\n,]/)[0];
    const time=tail.match(/^[^\d]{0,25}(\d+(?:\s*[–—-]\s*\d+)?)\s*(?:минут|мин\.?|分钟)/i);
    const routeText=source.slice(Math.max(0,match.index-35),match.index)+tail;
    const mode=/пешком|пешая|步行/i.test(routeText)?'walk':/автобус|公交/i.test(routeText)?'bus':/на метро|метрополитен|乘.*地铁/i.test(routeText)?'metro':'unknown';
    schools.set(row.record.id,{name:row.record.ru,time:time?time[1].replace(/\s/g,''):'',mode});
    evidence.push({field:'university',value:row.record.ru,text:match[0],source:'Описание · точное название вуза'});
  }
  const references=[...schools.values()],foundCampuses=[...campuses.values()],fields={};
  if(references.length){fields.university=references[0].name;fields.universities=references.map(row=>[row.name,row.time,row.mode].join(' | ')).join('\n');fields.uniTime=references[0].time;fields.uniMode=references[0].mode;}
  if(foundCampuses.length===1)fields.campus=foundCampuses[0].ru;
  return {fields,evidence,universities:references,campuses:foundCampuses};
}
export function generateListing(v,rules=DICTIONARY,prefs=DEFAULT_PREFS) {
  v={...v};
  const sourceConditions=detectConditions(v.sourceDescription||v.sourceAudit?.description||'').facts;
  const sharingAllowed=Number(v.rooms)===2&&v.separate===true&&!['studio','euro2'].includes(v.layout);
  const unknown=[],problems=[],translationWarnings=[];
  const zh=s=>{const t=translateRussian(s,{rules,mode:'safe'});unknown.push(...(t.unknown||[]));if(/[а-яё]/i.test(t.text)&&!t.unknown?.length)unknown.push(String(s));translationWarnings.push(...(t.warnings||[]));return t.text;};
  const positive=k=>Number(v[k])>0;
  for(const k of ['price','deposit','area','kitchen','living','bedroomArea','floor','floors','occupants','registrationPeople','registrationMonths']){
    if(v[k]!==undefined && v[k]!=='' && (!Number.isFinite(Number(v[k]))||Number(v[k])<0))problems.push('Проверьте значение: '+k);
  }
  if(positive('floor')&&positive('floors')&&Number(v.floor)>Number(v.floors))problems.push('Этаж выше этажности дома');
  if(positive('kitchen')&&positive('area')&&Number(v.kitchen)>Number(v.area))problems.push('Кухня больше общей площади');
  const routes=parseRoutes(v.transit);
  if(!routes.length&&v.metro)routes.push({name:v.metro,time:v.walk||'',mode:'walk',line:''});
  const universities=parseRoutes(v.universities);
  const universityInputs=String(v.universities||'').split('\n').map(row=>row.trim()).filter(Boolean);
  for(let index=0;index<universities.length;index++)if(!universityInputs[index]?.split('|')[2]?.trim())universities[index].mode='unknown';
  if(!universities.length&&v.university)universities.push({name:v.university,time:v.uniTime||'',mode:v.uniMode||'unknown',line:''});
  // A school is a reference chosen by the operator/source, not evidence that
  // the apartment is nearby. A campus address never supplies a journey time.
  const selectedUniversity=resolveUniversity(universities[0]?.name||'');
  const selectedCampus=resolveCampus(v.campus||'');
  if(selectedCampus&&selectedUniversity&&selectedCampus.universityId!==selectedUniversity.id)problems.push('Выбранный кампус относится к другому вузу. Проверьте вуз и кампус.');
  const universityTarget=universities.length?zh(universities[0].name):selectedCampus?zh(UNIVERSITY_CATALOG.universities.find(row=>row.id===selectedCampus.universityId)?.ru||''):'';
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
  const variation=safeVariant(v.titleVariant);
  const copyVariation=safeVariant(v.copyVariant);
  const tagVariation=safeVariant(v.tagVariant??v.titleVariant);
  const templateMode=v.templateMode==='catalog';
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
  const lines=[],sections={location:[],facts:[],equipment:[],transport:[],conditions:[],service:[],contact:[]};let currentSection='facts';
  const section=name=>{currentSection=name;};
  const push=(...items)=>{lines.push(...items);sections[currentSection].push(...items);};
  let phraseSlot=0;
  const wording=(...choices)=>{const salt=++phraseSlot;const mixed=copyVariation===0?0:((Math.imul(copyVariation+salt*73,2654435761)>>>0)^Math.floor(copyVariation/(salt+1)))>>>0;return choices[mixed%choices.length];};
  const equipmentLabel=x=>{
    const count=Number(v.featureCounts?.[x[0]]),minimum=v.sourceAudit?.furniture?.find(row=>row.id===x[0])?.countKind==='minimum';
    return x[1]+(Number.isFinite(count)&&count>1?'（'+(minimum?'至少':'')+count+'件）':'');
  };
  if(v.style==='story')push(wording('🇷🇺 想在莫斯科找一套适合自己生活节奏的住房？这套'+(area?area+'的':'')+layout+'可以了解一下！','🏡 在莫斯科寻找下一处住处？先看看这套'+(area?area+'的':'')+layout+'，再了解位置、配置和租赁条件。','🌿 莫斯科租房参考：这套'+(area?area+'的':'')+layout+'，位置与配置信息整理在下面。'),'');
  section('location');
  if(v.address)push('📍 '+zh(v.address));
  else if(v.complex||v.districtZh)push('📍 莫斯科｜'+zh(v.complex||v.districtZh));
  if(v.campus){const labels={teaching_campus:'校区',teaching_building:'教学楼',main_address:'院校地址参考',contact_address:'联络地址参考',administrative:'行政地址参考',student_center:'学生中心',dormitory:'宿舍地址参考'};push('🎓 '+(labels[selectedCampus?.locationType]||'校区')+'：'+zh(v.campus));}
  section('facts');
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
  if(v.bathroom)push('🛁 卫浴：'+zh(v.bathroom).replace(/^(\d+)\s*(?:个)?(卫生间与浴室合一|厕所与浴室分开)/,'$1处（$2）'));
  if(v.buildingYear)push('🏘 建成年份：'+v.buildingYear);
  if(v.ceiling)push('📏 层高：'+v.ceiling+'米');
  section('conditions');
  if(positive('price'))push('💰 租金：'+price+'/月（'+rubles(v.price)+'₽）');
  if(v.deposit!==''&&v.deposit!==undefined)push('🔐 押金：'+(Number(v.deposit)===0?'无需押金':wan(v.deposit)+'（'+rubles(v.deposit)+'₽）'));
  if(v.commission!==''&&v.commission!==undefined)push('🤝 中介费：'+(Number(v.commission)===0?'无需支付中介费':v.commission+'%'));
  if(v.prepayMonths)push('📄 预付：'+v.prepayMonths+'个月');
  if(v.contractMonths)push('📅 描述所列合同期限：'+v.contractMonths+'个月');
  if(v.minMonths&&v.leaseTerm==='long')push('📅 卡片所列最低租期：'+v.minMonths+'个月'+(v.contractMonths&&Number(v.contractMonths)!==Number(v.minMonths)?'（与描述不一致，请确认）':''));
  section('facts');
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
  section('equipment');
  if(v.style!=='compact'){
    const groups=[['bedroom','🛏️ 独立卧室','休息区域配有'],['living','🛋️ 生活空间','生活区域配有'],['kitchen','🍳 厨房配置','厨房配有'],['bathroom','🛁 卫生间','卫生间配有'],['storage','👗 储物与阳台','收纳与附属设施包括']];
    for(const [key,title,intro] of groups){const f=FEATURES.filter(x=>x[3]===key&&featureSet.has(x[0]));if(!f.length)continue;push('',wording(title,title+'清单',title+'信息'));if(v.style==='story')push(wording(intro,'此区域配备','配置包括')+f.map(equipmentLabel).join('、')+'。');else push(...f.map(x=>wording('✅ ','✔️ ','▪️ ')+equipmentLabel(x)));}
  }else{const f=FEATURES.filter(x=>featureSet.has(x[0]));if(f.length){push('',wording('🛋 家具家电：','🛋 室内配置：','🛋 已配备：','🛋 家具清单：'));if(copyVariation%3===0)push(f.map(equipmentLabel).join(' · '));else if(copyVariation%3===1){for(const key of ['kitchen','bedroom','living','bathroom','storage']){const group=f.filter(x=>x[3]===key);if(group.length)push(group.map(equipmentLabel).join('、'));}}else push(...f.map(x=>wording('✅ ','✔️ ','▪️ ')+equipmentLabel(x)));}}
  const missing=(v.sourceAudit?.furniture||[]).filter(row=>row.status==='absent'&&(row.matches.some(m=>m.source==='Описание'&&m.status==='absent')||['tv','washer'].includes(row.id)));
  if(missing.length)push('','📌 房源注明未配备：'+missing.map(row=>FEATURES.find(f=>f[0]===row.id)?.[1]).filter(Boolean).join('、')+'。');
  section('location');
  if(v.details)push('','🌳 周边与房屋亮点：',zh(v.details));
  const routeLine=r=>zh(r.name)+(r.time?'｜'+(modes[r.mode]||'公共交通')+'约'+r.time+'分钟':'｜时间待确认')+(r.line?'｜'+r.line.replace(/^([0-9]+)$/,'$1号线'):'');
  section('transport');
  if(routes.length)push('',wording('🚇 交通：','🚇 地铁与出行：','🚇 位置与交通：'),...routes.map(r=>'▪️ '+routeLine(r)));
  if(v.route)push('🗺 '+zh(v.route));
  if(universities.length)push('',universities.some(r=>r.time)?'🎓 大学通勤参考：':'🎓 关注院校（通勤时间待确认）：',...universities.map(r=>'▪️ '+routeLine(r)));
  section('conditions');
  if(sharingAllowed&&positive('price')){
    push('','💸 合租预算参考：');
    for(const n of [2,4])push(n+'人：约'+wan(Number(v.price)/n)+'（'+rubles(Math.round(Number(v.price)/n))+'₽）/人/月');
    push('👥 以上仅为租金分摊示例，不含其他费用；入住人数及合租安排需与房东确认。');
  }
  const utilities={included:'💡 租金包含各项费用。',meters:'💡 物业费已包含，仅水电按表计费。',extra:'💡 物业及水电等费用另付，具体金额请确认。'};
  if(utilities[v.utilities])push('',utilities[v.utilities]);
  if(v.shortTerm&&v.shortTermPolicy!=='no')push('📅 可协商短租'+(v.minMonths?'，'+v.minMonths+'个月起':'')+'，具体条件请咨询。');
  if(v.children&&!['no','conflict'].includes(v.childrenPolicy)&&!['no','conflict'].includes(sourceConditions.childrenPolicy))push('👶 可带孩子');
  if(v.pets&&!['no','conflict','negotiable'].includes(v.petPolicy)&&!['no','conflict','negotiable'].includes(sourceConditions.petPolicy))push('🐾 可带宠物，具体条件请与房东确认');
  if(v.smokingPolicy==='no')push('🚭 仅限不吸烟的租客');
  if(v.guestPolicy==='no')push('👥 房东要求不接待访客或聚会');
  if(v.subletPolicy==='no')push('📝 不得将公寓转租给他人');
  if(v.shortTermPolicy==='no')push('📅 不接受按日短租');
  section('service');
  {
    let registration='抵达登记（临时居住登记）：提供办理协助';
    if(positive('registrationMonths'))registration+='（'+v.registrationMonths+'个月）';
    if(positive('registrationPeople'))registration+='，最多'+v.registrationPeople+'人';
    push('','✈️ '+registration+'。');
  }
  if(v.pickup)push('🚕 免费接送到入住地点'+(v.luggage?'，协助搬运行李':'')+'。');
  if(v.arrival)push('📋 免费协助了解抵达后的相关手续。');
  if(v.support&&prefs.benefits)push('','🇨🇳 我们的优势：',prefs.benefits);
  section('contact');
  if(prefs.contact)push('','📩 联系我们：'+prefs.contact);
  else push('',wording('📩 欢迎中文咨询，了解房源详情并预约看房！','💌 想了解租赁条件或安排看房？欢迎用中文私信我们！','📨 欢迎中文留言咨询，我们协助了解房源并预约看房。'));
  const tags=['#莫斯科租房','#莫斯科留学','#莫斯科生活'];
  if(v.rooms)tags.push('#莫斯科'+({1:'一居室',2:'两居室',3:'三居室'}[v.rooms]||'租房'));
  for(const r of routes)tags.push('#'+zh(r.name).replace(/[\s#]/g,''));
  for(const u of universities)tags.push('#'+zh(u.name).replace(/[\s#]/g,''));
  if(prefs.brandTags)tags.push(...prefs.brandTags.split(/\s+/).filter(Boolean).map(t=>t.startsWith('#')?t:'#'+t));
  let tagSuggestions=suggestTags({...v,tagVariant:tagVariation,
    pets:!!v.pets&&!['no','conflict','negotiable'].includes(v.petPolicy)&&!['no','conflict','negotiable'].includes(sourceConditions.petPolicy),
    children:!!v.children&&!['no','conflict'].includes(v.childrenPolicy)&&!['no','conflict'].includes(sourceConditions.childrenPolicy)},rules,translate,parseRoutes);
  const finalTags=tagSuggestions.map(x=>'#'+x.tag);
  if(prefs.brandTags)finalTags.push(...prefs.brandTags.split(/\s+/).filter(Boolean).map(t=>t.startsWith('#')?t:'#'+t));
  let body=lines.join('\n'),renderedTags=[...new Set(finalTags)].join(' '),templateInfo=null;
  if(templateMode){
    const titleContext={city:'莫斯科',layout,area,price:price?price+'/月':'',
      metro:main?[metro,main.time?(modes[main.mode]||'公共交通')+'约'+main.time+'分钟':''].filter(Boolean).join(' '):'',
      feature:FEATURES.filter(x=>featureSet.has(x[0])).slice(0,2).map(equipmentLabel).join('、')};
    const titleIndexes=[variation,variation+1,variation+2];
    titles=titleIndexes.map(index=>renderTemplate('title',index,titleContext));
    const blockContext=Object.fromEntries(Object.entries(sections).map(([key,value])=>[key,value.join('\n').replace(/^\n+|\n+$/g,'')]));
    body=renderTemplate('description',copyVariation,blockContext);
    renderedTags=renderTemplate('tags',tagVariation,{tags:[...new Set(finalTags)]});
    const byTag=new Map(tagSuggestions.map(row=>[row.tag,row]));
    tagSuggestions=[...new Set((renderedTags.match(/#[^\s#]+/g)||[]).map(tag=>tag.slice(1)))].map(tag=>byTag.get(tag)||{tag,reason:finalTags.includes('#'+tag)?'Тег студии из настроек':'Общая тема аренды; редакционная формулировка',ru:'Редакционный тег аренды'});
    templateInfo={version:1,mode:'catalog',titleId:getTemplate('title',variation).id,
      titleIds:titleIndexes.map(index=>getTemplate('title',index).id),
      descriptionId:getTemplate('description',copyVariation).id,tagId:getTemplate('tags',tagVariation).id,
      indices:{title:variation,description:copyVariation,tags:tagVariation},counts:catalogStats()};
  }
  if(universityTarget)titles[0]+='｜🎓通勤目标：'+universityTarget;
  return {titles,body,tags:renderedTags,tagSuggestions,unknown:[...new Set(unknown)],translationWarnings:[...new Set(translationWarnings)],problems,templateInfo};
}

// Trigger dictionaries describe vocabulary, not evidence. A larger vocabulary
// must not turn a negated or merely planned amenity into a listing highlight.
function confirmedDetailTrigger(text,phrase){
  const source=fold(text),needle=fold(phrase);let from=0,found=false,blocked=false,index;
  while((index=source.indexOf(needle,from))!==-1){
    const before=source.slice(Math.max(0,index-75),index).split(/[.!?;\n,]/).at(-1);
    const after=source.slice(index+needle.length,index+needle.length+75).split(/[.!?;\n,]/)[0];
    const beforeBlock=/(?:^|\s)(?:не|нет|без|отсутствует|не будет|планируется)\s*(?:[^\s]+\s+){0,2}$/.test(before);
    const afterBlock=/^\s*(?:(?:только|пока|еще|скоро)\s+)?(?:нет|отсутств|не\s+(?:будет|предусмотр)|будет|будут|планиру|ожидается|запланирован)/.test(after);
    if(beforeBlock||afterBlock)blocked=true;else found=true;
    from=index+Math.max(needle.length,1);
  }
  return found&&!blocked;
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
  const details=Object.keys(rules).filter(k=>k.length>5&&/магазин|ресторан|вид на|окна во|ремонт|двор|парк|лес|без мебели/.test(k)&&confirmedDetailTrigger(t,k));
  if(details.length)out.details=details.join(', ');
  if(/(?:можно|разрешено|допускается)\s+с детьми/i.test(t))out.children=true;
  if(/(?:можно|разрешено|обсужда[а-я]+)\s+(?:с\s+)?(?:животными|питомцами)/i.test(t))out.pets=true;
  if(/только\s+(?:вода|свет|воду|свету|счетчик|счётчик)|вода и свет|счетчикам|счётчикам/i.test(t))out.utilities='meters';
  const conditions=detectConditions(text),schoolReferences=extractUniversityReferences(text);Object.assign(out,conditions.facts,schoolReferences.fields);if(!out.sourceDescription)out.sourceDescription=String(text||'');
  warnings.push('Извлечены только найденные факты. Маршруты до вузов, услуги и доступность объекта проверяются отдельно.');
  out.sourceAudit={version:1,stats:{},fields:[...conditions.evidence,...schoolReferences.evidence],furniture,warnings,description:String(text||'')};
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
  // Recommendations and page navigation may mention another university.
  // Prefer the apartment's own description when that section was identified.
  const schoolReferences=extractUniversityReferences(source.description||source.text||'');
  for(const key of ['university','universities','uniTime','uniMode','campus'])delete facts[key];
  Object.assign(facts,schoolReferences.fields);
  const warnings=[...structured.warnings,...conditions.warnings];
  if(facts.contractMonths&&facts.minMonths&&Number(facts.contractMonths)!==Number(facts.minMonths))warnings.push('Противоречие: в описании договор на '+facts.contractMonths+' месяцев, в условиях карточки — от '+facts.minMonths+' месяцев. Требуется уточнение.');
  if(facts.registrationPolicy==='not_available')warnings.push('Источник: собственник не предоставляет регистрацию. Услуга студии по содействию оформлению указывается отдельно; это не обещание регистрации по адресу объекта.');
  for(const row of furniture.filter(r=>r.status==='conflict'||r.status==='planned'))warnings.push('Мебель: '+row.label+' — '+(row.status==='conflict'?'противоречие источников':'только планируется установка'));
  if(!facts.price)warnings.push('Цена не найдена; заполните вручную.');
  warnings.push('Маршруты до вузов, кампус, услуги и наличие китайских магазинов не определяются без подтверждённых данных.');
  const fields=[...structured.evidence,...conditions.evidence,...schoolReferences.evidence];
  facts.sourceAudit={version:1,stats:source.stats||{},fields,furniture,warnings,description:source.description||'',unmapped:(source.records||[]).filter(r=>!fields.some(e=>e.text===r.text)).slice(0,100)};
  return {facts,warnings,sourceAudit:facts.sourceAudit};
}
