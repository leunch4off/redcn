/* Indexed compositions, not a claim of 786432 handwritten Chinese texts.
 * Every ordinal resolves locally from six independent eight-choice axes.
 * Facts come only from the caller; missing fact blocks are omitted together
 * with their labels. No renovation, availability or permission is inferred.
 */
export const TEMPLATE_CATALOG_VERSION = 'zh-rental-compositions-v1';
export const TEMPLATE_KIND_COUNT = 262144;
export const CATALOG_SIZE = TEMPLATE_KIND_COUNT;

const TITLE_LEADS = [
  '{{city}}租房','{{city}}房源','{{city}}公寓出租','{{city}}住房出租',
  '{{city}}租赁房源','{{city}}租房信息','{{city}}公寓房源','{{city}}住房信息',
];
const TITLE_LAYOUTS = [
  '户型{{layout}}','房型{{layout}}','公寓户型{{layout}}','住房户型{{layout}}',
  '房源户型{{layout}}','租赁户型{{layout}}','房屋户型{{layout}}','户型信息{{layout}}',
];
const TITLE_AREAS = [
  '{{area}}','面积{{area}}','总面积{{area}}','公寓面积{{area}}',
  '房源面积{{area}}','住房面积{{area}}','面积为{{area}}','面积信息{{area}}',
];
const TITLE_PRICES = [
  '{{price}}','月租{{price}}','租金{{price}}','每月租金{{price}}',
  '房源租金{{price}}','租金为{{price}}','月租金{{price}}','租赁价格{{price}}',
];
const TITLE_DETAILS = [
  '[[{{metro}}]]','[[地铁{{metro}}]]','[[出行{{metro}}]]','[[交通参考{{metro}}]]',
  '[[{{feature}}]]','[[配有{{feature}}]]','[[已配{{feature}}]]','[[配置{{feature}}]]',
];
const TITLE_ORDERS = [
  [0,1,2,3,4],[0,2,1,4,3],[0,3,1,2,4],[1,2,0,3,4],
  [2,1,3,0,4],[3,0,1,4,2],[0,4,1,3,2],[1,3,2,4,0],
];
const BODY_FIELDS = ['location','facts','equipment','transport','conditions'];
const BODY_HEADINGS = [
  ['位置','地址信息','所在位置','房源位置','地段信息','位置资料','地址资料','位置概况'],
  ['房屋信息','公寓资料','住房参数','房源参数','面积与户型','基础信息','房屋资料','户型与面积'],
  ['家具家电','室内配置','家具与设备','配备情况','配置清单','已有配置','室内设备','设施信息'],
  ['交通信息','出行参考','地铁与出行','通勤资料','交通资料','交通概况','出行信息','交通与位置'],
  ['租赁条件','入住条件','租住说明','租赁信息','租房条件','入住说明','租住条件','租赁事项'],
];
const BODY_ORDERS = [
  [0,1,2,3,4],[1,0,3,2,4],[0,3,1,2,4],[1,2,0,3,4],
  [3,0,1,4,2],[0,1,4,2,3],[1,4,0,3,2],[2,1,0,3,4],
];
// These are broad topic labels, not 262144 individual hashtags or popularity
// statistics. No pool asserts students, pets, furniture or a nearby station.
const TAG_FAMILIES = [
  ['莫斯科','莫斯科租房','莫斯科房源','莫斯科住房','莫斯科公寓','莫斯科居住','莫斯科找房','莫斯科看房'],
  ['俄罗斯','俄罗斯租房','俄罗斯生活','俄罗斯住房','俄罗斯公寓','俄罗斯房源','俄罗斯居住','俄罗斯找房'],
  ['租房','租房信息','房屋租赁','公寓租赁','租住信息','住房租赁','房源信息','租赁住房'],
  ['公寓','住房','房屋','出租房源','公寓房源','住房信息','看房','找房'],
  ['莫斯科生活','莫斯科日常','莫斯科居住生活','在莫斯科生活','俄罗斯日常','在俄罗斯生活','俄罗斯居住生活','莫斯科居住日常'],
  ['房源分享','租房分享','房源展示','公寓展示','租房咨询','房源咨询','看房参考','租房参考'],
];
const KIND_ALIASES = {title:'title',titles:'title',description:'description',body:'description',descriptions:'description',tags:'tags',tag:'tags',tagSets:'tags'};
const SALTS = {title:1729,description:8191,tags:131071};
const SEPARATORS = ['｜',' · ',' / ','，'];

function kindOf(value){const kind=KIND_ALIASES[value];if(!kind)throw new TypeError('Неизвестный вид шаблона');return kind;}
function ordinal(value){const number=Number(value);return Number.isFinite(number)?((Math.trunc(number)%TEMPLATE_KIND_COUNT)+TEMPLATE_KIND_COUNT)%TEMPLATE_KIND_COUNT:0;}
function digitsFor(kind,index){
  // Odd multiplication is a permutation modulo 2^18. Consecutive buttons
  // change wording and order rather than moving through one tiny phrase axis.
  let value=(Math.imul(ordinal(index),1597334677)+SALTS[kind])&(TEMPLATE_KIND_COUNT-1);
  const digits=[];for(let i=0;i<6;i++){digits.push(value&7);value>>>=3;}return digits;
}
function textValue(value){return typeof value==='string'||typeof value==='number'?String(value).replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g,'').trim():'';}
function fill(source,context){
  return source.replace(/\[\[([\s\S]*?)\]\]/g,(_match,body)=>{
    const keys=[...body.matchAll(/\{\{([a-zA-Z]+)\}\}/g)].map(match=>match[1]);
    return keys.every(key=>textValue(context[key]))?body:'';
  }).replace(/\{\{([a-zA-Z]+)\}\}/g,(_match,key)=>textValue(context[key]));
}
function cleanTitlePart(value){return value.replace(/^(?:\s*[·｜/,，]\s*)+|(?:\s*[·｜/,，]\s*)+$/g,'').trim();}
function heading(text,style){return style===1?'【'+text+'】':style===2?text+'：':style===3?'• '+text:text;}
function titleParts(d){return [TITLE_LEADS[d[0]],'[['+TITLE_LAYOUTS[d[1]]+']]','[['+TITLE_AREAS[d[2]]+']]','[['+TITLE_PRICES[d[3]]+']]',TITLE_DETAILS[d[4]]];}
function bodyParts(d){const style=(d[0]+d[2]+d[4])%4;return BODY_FIELDS.map((field,i)=>'[['+heading(BODY_HEADINGS[i][d[i]],style)+'\n{{'+field+'}}]]');}

export function getTemplate(kind,index=0){
  kind=kindOf(kind);index=ordinal(index);const axes=digitsFor(kind,index);
  let skeleton;
  if(kind==='title'){
    const parts=titleParts(axes);skeleton=TITLE_ORDERS[axes[5]].map(i=>parts[i]).join(SEPARATORS[(axes[0]+axes[3])%4]);
  }else if(kind==='description'){
    const parts=bodyParts(axes);skeleton=[...BODY_ORDERS[axes[5]].map(i=>parts[i]),'[[{{service}}]]','[[{{contact}}]]'].join('\n\n');
  }else skeleton=TAG_FAMILIES.map((family,i)=>'#'+family[axes[i]]).join(' ')+' [[{{factTags}}]]';
  return {id:'zh-'+kind+'-v1-'+String(index).padStart(6,'0'),kind,index,skeleton,axes};
}

export function renderTemplate(kind,index=0,context={}){
  kind=kindOf(kind);const template=getTemplate(kind,index),d=template.axes;
  if(kind==='title'){
    const values={...context,city:textValue(context.city)||'莫斯科'};
    // Project the original order after omitting missing blocks, never retain
    // an empty label such as "面积" with no area supplied by the engine.
    const raw=titleParts(d);
    if(d[4]<4&&!textValue(values.metro)&&textValue(values.feature))raw[4]=TITLE_DETAILS[d[4]+4];
    else if(d[4]>=4&&!textValue(values.feature)&&textValue(values.metro))raw[4]=TITLE_DETAILS[d[4]-4];
    const all=raw.map(part=>cleanTitlePart(fill(part,values)));
    return TITLE_ORDERS[d[5]].map(i=>all[i]).filter(Boolean).join(SEPARATORS[(d[0]+d[3])%4]);
  }
  if(kind==='description'){
    const parts=bodyParts(d).map(part=>fill(part,context).trim());
    return [...BODY_ORDERS[d[5]].map(i=>parts[i]),textValue(context.service),textValue(context.contact)].filter(Boolean).join('\n\n');
  }
  const supplied=(Array.isArray(context.tags)?context.tags:typeof context.tags==='string'?context.tags.split(/\s+/):[])
    .flatMap(value=>textValue(value).split(/\s+/)).filter(Boolean).map(value=>value.startsWith('#')?value:'#'+value);
  const generic=TAG_FAMILIES.map((family,i)=>'#'+family[d[i]]);
  return [...new Set([...supplied,...generic])].join(' ');
}

export function catalogStats(){
  return {version:TEMPLATE_CATALOG_VERSION,mode:'indexed-compositions',
    titles:TEMPLATE_KIND_COUNT,descriptions:TEMPLATE_KIND_COUNT,tagSets:TEMPLATE_KIND_COUNT,
    total:TEMPLATE_KIND_COUNT*3,baseLanguageBlocks:128,genericTagAtoms:48,
    factPolicy:'caller-confirmed-only',sparseContextMayReduceDistinctOutputs:true,
    countsDescribe:'unique-composed-skeletons-not-handwritten-texts-or-individual-hashtags'};
}
export const getCatalogStats=catalogStats;
export const renderTitleTemplate=(index,context)=>renderTemplate('title',index,context);
export const renderDescriptionTemplate=(index,context)=>renderTemplate('description',index,context);
export const renderTagTemplate=(index,context)=>renderTemplate('tags',index,context);
export function nextTemplateIndex(index){return (ordinal(index)+1)%TEMPLATE_KIND_COUNT;}

export const TEMPLATE_AUDIT_CONTEXT=Object.freeze({
  city:'莫斯科',layout:'两居室',area:'54㎡',price:'8W/月',metro:'西南站，步行7分钟',feature:'书桌、烤箱',
  location:'📍 地址：测试地址',facts:'🏠 两居室\n📐 面积54㎡\n💰 租金8W/月',
  equipment:'书桌、烤箱',transport:'西南站，步行7分钟',conditions:'水电按表另付',
  service:'抵达登记（临时居住登记）：提供办理协助。',contact:'欢迎中文咨询。',tags:[],
});

// Runtime editorial layouts are a separate, additive presentation layer. The
// audited v1 skeleton IDs, rendering and materialized downloads above stay
// unchanged. Twenty layouts are not twenty sets of 262144 audited new texts.
export const LISTING_STYLE_VERSION='zh-rental-layouts-v1';
export const LISTING_STYLE_FAMILIES=Object.freeze([
  {id:'emoji-summary',label:'Короткая подача с эмодзи',modes:['compact','detailed'],format:'icons',order:['location','facts','conditions','equipment','transport','service','contact']},
  {id:'editorial',label:'Редакционная подача',modes:['detailed','story'],format:'editorial',order:['location','facts','equipment','transport','conditions','service','contact']},
  {id:'price-first',label:'Сначала аренда и расходы',modes:['compact','detailed','story'],format:'price',requires:{title:['price'],description:['price']},order:['conditions','facts','location','transport','equipment','service','contact']},
  {id:'room-by-room',label:'По зонам и оснащению',modes:['detailed','story'],format:'rooms',requires:{title:['feature'],description:['equipment']},order:['equipment','facts','location','transport','conditions','service','contact']},
  {id:'commute-first',label:'Сначала транспорт',modes:['compact','detailed','story'],format:'commute',requires:{title:['metro'],description:['transport','metro']},order:['transport','location','facts','equipment','conditions','service','contact']},
  {id:'clean-card',label:'Спокойная карточка',modes:['compact','detailed'],format:'card',order:['facts','conditions','location','equipment','transport','service','contact']},
  {id:'compact',label:'Плотное краткое оформление',modes:['compact','detailed'],format:'compact',order:['facts','location','transport','equipment','conditions','service','contact']},
  {id:'bullet-board',label:'Список для публикации',modes:['compact','detailed'],format:'bullets',order:['location','transport','facts','equipment','conditions','service','contact']},
  {id:'numbered-guide',label:'Нумерованные разделы',modes:['detailed'],format:'numbered',order:['facts','equipment','location','transport','conditions','service','contact']},
  {id:'budget-note',label:'Бюджет и условия',modes:['compact','detailed','story'],format:'budget',requires:{title:['price'],description:['price']},order:['conditions','location','facts','equipment','transport','service','contact']},
  {id:'feature-focus',label:'Акцент на подтверждённом оснащении',modes:['compact','detailed','story'],format:'feature',requires:{title:['feature'],description:['equipment']},order:['equipment','location','facts','conditions','transport','service','contact']},
  {id:'area-layout',label:'Площадь и планировка',modes:['compact','detailed','story'],format:'space',order:['facts','equipment','conditions','location','transport','service','contact']},
  {id:'location-note',label:'Расположение и ориентиры',modes:['compact','detailed','story'],format:'location',order:['location','transport','conditions','facts','equipment','service','contact']},
  {id:'university-reference',label:'Подтверждённые учебные ориентиры',modes:['detailed','story'],format:'university',requires:{title:['university','universityTransport'],description:['university','universityTransport']},order:['transport','location','facts','conditions','equipment','service','contact']},
  {id:'conditions-first',label:'Сначала условия аренды',modes:['compact','detailed','story'],format:'conditions',order:['conditions','facts','equipment','location','transport','service','contact']},
  {id:'calm-paragraph',label:'Нейтральные абзацы',modes:['compact','detailed','story'],format:'paragraph',order:['facts','location','equipment','conditions','transport','service','contact']},
  {id:'viewing-note',label:'Информация перед просмотром',modes:['detailed','story'],format:'viewing',order:['location','facts','conditions','transport','equipment','service','contact']},
  {id:'catalogue',label:'Карточка с настоящим номером объекта',modes:['compact','detailed'],format:'catalogue',requires:{title:['code'],description:['code']},order:['facts','location','conditions','equipment','transport','service','contact']},
  {id:'service-note',label:'Сервис агентства',modes:['compact','detailed','story'],format:'service',requires:{title:['serviceShort'],description:['service']},order:['service','location','facts','equipment','transport','conditions','contact']},
  {id:'split-sections',label:'Две смысловые группы',modes:['detailed','story'],format:'split',order:['location','facts','transport','equipment','conditions','service','contact']},
].map(row=>Object.freeze({...row,modes:Object.freeze(row.modes),order:Object.freeze(row.order),
  ...(row.requires?{requires:Object.freeze(Object.fromEntries(Object.entries(row.requires).map(([kind,keys])=>[kind,Object.freeze(keys)])))}:{})})));

const listingStyleSectionKeys=['location','facts','equipment','transport','conditions','service','contact'];
const listingStyleSectionIcons={location:'📍',facts:'🏠',equipment:'🛋',transport:'🚇',conditions:'💰',service:'✈️',contact:'📩'};
const listingStyleSectionNames={
  location:['位置','房源位置','地址信息','位置资料','地段资料','所在位置','地址参考','位置概况'],
  facts:['房屋信息','户型与面积','房源资料','空间信息','住房参数','基础资料','房屋参数','房源信息'],
  equipment:['家具家电','已有配置','配置清单','家具与设备','室内配置','房源配置','设备清单','配置资料'],
  transport:['交通信息','出行参考','通勤资料','交通资料','地铁与出行','交通参考','出行资料','交通概况'],
  conditions:['租赁条件','费用与条件','入住条件','租住说明','租住事项','租赁事项','费用资料','租住条件'],
  service:['租住协助','相关服务','租住服务','服务说明','办理协助','服务资料','服务信息','相关协助'],
  contact:['中文咨询','咨询方式','联系与咨询','了解房源','咨询信息','联系资料','进一步咨询','中文联系'],
};
const listingStyleGenericHeaders={
  ...Object.fromEntries(Object.entries(listingStyleSectionNames).map(([key,names])=>[key,[...names]])),
  equipment:[...listingStyleSectionNames.equipment,'已配备','家具清单'],
  transport:[...listingStyleSectionNames.transport,'交通','位置与交通'],
};
const listingStyleSubsectionHeaders={
  location:{labels:['周边与房屋亮点'],names:['周边与房屋信息','房源周边资料','位置与周边','位置与房屋资料','周边资料','周边参考','房源环境资料','周边与房源概况']},
  facts:{labels:['公寓介绍','走进这套公寓','居住空间','房源介绍','关于这套住房','莫斯科租房参考','空间与配置','这套公寓的信息'],names:['房源介绍','关于这套住房','公寓资料','住房信息','房源内容','住房资料','公寓介绍','这套房源的信息']},
};
const listingStyleEditorialOpeners=[
  '先看看这套房源已经提供的信息。','把已知的房源资料放在一起看。',
  '这份房源资料按不同部分整理如下。','先了解现有信息，再逐项核对适合自己的条件。',
  '从已提供的资料出发，看看这套住房。','房源资料可以分开慢慢看。',
  '房源信息不必只看一个数字，下面按内容展开。','这套房源的已知信息，整理成下面几部分。',
];
const listingStyleViewingOpeners=[
  '看房前，可以先核对下面这些信息。','先按自己的需求，看看已提供的房源资料。',
  '先读一遍已提供的房源资料，再整理想确认的问题。','准备了解这套房源？可以从以下资料开始。',
];
const listingStyleTitleEmojis=['🏡','🏠','✨','📍','🔎','🗝️','🇷🇺','💛'];
const listingStyleDecorativeIcons=['🛏️','🛋️','🛏','🛋','🏡','🏠','📍','📐','📏','🏢','🏘','🍳','🛁','👗','🚇','🚉','🚌','🚊','💰','💸','🔐','📄','📅','📋','📩','📨','💌','✈️','✈','🌳','🌲','🌿','✨','🔥','💡','🎓','🗺️','🗺'];

function listingStyleValue(value){return Array.isArray(value)?value.map(textValue).filter(Boolean).join('\n'):textValue(value);}
function listingStyleLine(value){return listingStyleValue(value).replace(/\s+/gu,' ').trim();}
function listingStyleMode(context){return ['compact','detailed','story'].includes(context?.style)?context.style:'detailed';}
function listingStyleKind(value){const kind=kindOf(value);if(!['title','description','tags'].includes(kind))throw new TypeError('Неизвестный вид шаблона');return kind;}
function listingStyleInputs(context={}){
  if(!context||typeof context!=='object'||Array.isArray(context))context={};
  const result={...context};
  for(const key of [...listingStyleSectionKeys,'city','layout','area','price','metro','feature','floor','code','university','universityTransport','serviceShort','permissions','countryFlag'])result[key]=listingStyleValue(context[key]);
  if(!result.equipment&&Array.isArray(context.equipmentGroups))result.equipment=renderListingEquipment(context.equipmentGroups,0);
  if(!result.transport&&result.metro)result.transport=result.metro;
  if(result.universityTransport&&!result.transport.includes(result.universityTransport))result.transport=[result.universityTransport,result.transport].filter(Boolean).join('\n');
  // Shorter source blocks are opt-in. A compact visual style never silently
  // removes the caller's inventory, fees, restrictions or agency service.
  if(context.useShortBlocks===true)for(const key of ['location','facts','equipment','transport']){
    const short=listingStyleValue(context[key+'Short']);if(short)result[key]=short;
  }
  return result;
}

function listingStyleEligible(kind,index,context){
  const values=listingStyleInputs(context),mode=listingStyleMode(values);
  const families=LISTING_STYLE_FAMILIES.filter(row=>row.modes.includes(mode)&&
    (!row.requires?.[kind]||row.requires[kind].some(key=>listingStyleValue(values[key]))));
  return {family:families[ordinal(index)%families.length],values,mode};
}

export function getListingStyle(kind,index=0,context={}){
  kind=listingStyleKind(kind);index=ordinal(index);
  if(kind==='tags')return {id:'zh-tags-layouts-v1-'+String(index).padStart(6,'0'),version:LISTING_STYLE_VERSION,
    kind,index,style:listingStyleMode(context),family:'factual-tags',label:'Подтверждённые и общие теги',baseTemplateId:getTemplate(kind,index).id};
  const {family,mode}=listingStyleEligible(kind,index,context);
  return {id:'zh-'+kind+'-layouts-v1-'+mode+'-'+family.id+'-'+String(index).padStart(6,'0'),
    version:LISTING_STYLE_VERSION,kind,index,style:mode,family:family.id,label:family.label,
    baseTemplateId:getTemplate(kind,index).id};
}

function listingStyleDecorated(value){
  const first=listingStyleValue(value).split('\n').find(line=>line.trim())?.trim()||'';
  if(!first)return false;
  const cp=first.codePointAt(0);
  return listingStyleDecorativeIcons.some(icon=>first.startsWith(icon))||cp>=0x1f000&&cp<=0x1faff||/^(?:【|#{1,6}\s|[▪•✅✔☀★☆→❌🚭]|[-*]\s|\d+[.)、]\s)/u.test(first)||
    /^(?:位置|地址|户型|面积|楼层|家具|家电|配置|交通|地铁|租金|押金|中介费|租赁|入住|抵达登记|微信|WeChat)[^\n：:]{0,14}[：:]/iu.test(first);
}
function listingStylePlainLine(value){
  const original=value.trim();let text=original,again=true;
  while(again){again=false;for(const icon of listingStyleDecorativeIcons)if(text.startsWith(icon)){text=text.slice(icon.length).trimStart();again=true;break;}}
  // Only formatting bullets are removed. Check/cross/permission symbols can
  // carry the meaning of a condition and must not be treated as decoration.
  return text.replace(/^(?:[▪•]\s*|[-*]\s+)/u,'').trim()||original;
}
function listingStyleParagraph(value){
  const lines=listingStyleValue(value).split(/\r?\n/u).map(listingStylePlainLine).filter(Boolean);
  let text='';for(const line of lines){if(text)text+=/[。！？!?；;：:]$/u.test(text)?' ':'；';text+=line;}return text;
}
function listingStyleVaryHeading(key,value,d){
  // Replace only a recognised, standalone generic label already provided by
  // the generator. Inline facts, room-specific labels, registration wording
  // and user prose are untouched. This varies wording inside existing blocks
  // instead of surrounding an emoji heading with another heading.
  const section=listingStyleSectionKeys.indexOf(key),subsection=listingStyleSubsectionHeaders[key];
  return listingStyleValue(value).split('\n').map(line=>{
    const label=listingStylePlainLine(line).replace(/[：:]\s*$/u,'').trim();
    const names=listingStyleGenericHeaders[key].includes(label)?listingStyleSectionNames[key]:
      subsection?.labels.includes(label)?subsection.names:null;
    if(!names)return line;
    const start=line.lastIndexOf(label);if(start<0)return line;
    return line.slice(0,start)+names[d[section%6]]+line.slice(start+label.length);
  }).join('\n');
}
function listingStyleHeading(key,d,index){return listingStyleSectionNames[key][d[index%6]];}
function listingStyleBlock(key,value,{format,d,index,forceHeading=false}={}){
  if(!value)return '';
  const decorated=listingStyleDecorated(value),name=listingStyleHeading(key,d,index),body=listingStyleValue(value);
  if(format==='paragraph'||format==='editorial')return (decorated&&!forceHeading?'':name+'：')+listingStyleParagraph(body);
  if(format==='compact')return (decorated&&!forceHeading?'':listingStyleSectionIcons[key]+' ')+listingStyleParagraph(body);
  if(format==='numbered')return String(index+1).padStart(2,'0')+' · '+(decorated&&!forceHeading?body:name+'\n'+body);
  if(decorated&&!forceHeading)return body;
  if(format==='bullets')return '▪️ '+name+'\n'+body;
  if(format==='card'||format==='catalogue')return '【'+name+'】\n'+body;
  return listingStyleSectionIcons[key]+' '+name+'\n'+body;
}

function listingStyleTitle(index,context){
  const {family,values:v}=listingStyleEligible('title',index,context),d=digitsFor('title',index);
  const city=listingStyleLine(v.city)||'莫斯科',parts={city,layout:listingStyleLine(v.layout),area:listingStyleLine(v.area),price:listingStyleLine(v.price)};
  const detail=keys=>keys.map(key=>listingStyleLine(v[key])).find(Boolean)||'';
  const metro=detail(['metro','feature']),feature=detail(['feature','metro']),university=detail(['university','universityTransport']);
  const emoji=city==='莫斯科'?listingStyleTitleEmojis[d[0]]:['🏡','🏠','✨','📍','🔎','🗝️','🏘️','💛'][d[0]];
  const common=(order,extra='',separator='｜')=>[...order.map(key=>parts[key]),extra].filter(Boolean).join(separator);
  switch(family.id){
    case 'emoji-summary':return emoji+' '+common(['city','layout','area','price'],metro,' · ');
    case 'editorial':return '【'+city+'租房】'+common(['layout','area','price'],metro,' · ');
    case 'price-first':return '💰 '+common(['price','layout','area','city'],metro);
    case 'room-by-room':return ['🛋 '+feature,common(['city','layout','area','price'])].filter(Boolean).join('｜');
    case 'commute-first':return ['🚇 '+listingStyleLine(v.metro),common(['city','layout','area','price'])].filter(Boolean).join('｜');
    case 'clean-card':return common(['city','layout','area','price'],detail(['floor','metro']),' / ');
    case 'compact':return common(['city','layout','price','area'],'',' · ');
    case 'bullet-board':return '📌 '+common(['layout','area','price','city'],metro);
    case 'numbered-guide':return city+'房源资料｜'+common(['layout','area','price'],feature);
    case 'budget-note':return '租金参考：'+common(['price','area','layout','city'],metro,' / ');
    case 'feature-focus':return '✨ '+feature+'｜'+common(['layout','area','price','city']);
    case 'area-layout':return '🏠 '+common(['area','layout','city','price'],detail(['floor','feature']));
    case 'location-note':return common(['city'],metro,' · ')+'｜'+common(['layout','area','price']);
    case 'university-reference':return '🎓 院校参考：'+university+'｜'+common(['city','layout','area','price']);
    case 'conditions-first':return [detail(['permissions']),common(['city','price','layout','area'])].filter(Boolean).join('｜');
    case 'calm-paragraph':return city+'租房，'+common(['layout','area','price'],metro,'，');
    case 'viewing-note':return common(['city','layout','area','price'],metro,' · ')+'｜看房资料';
    case 'catalogue':return '['+listingStyleLine(v.code)+'] '+common(['city','layout','area','price'],metro);
    case 'service-note':return listingStyleLine(v.serviceShort)+'｜'+common(['city','layout','area','price']);
    case 'split-sections':return common(['city','layout'],parts.area,' · ')+'｜'+[parts.price,metro].filter(Boolean).join(' / ');
    default:return common(['city','layout','area','price'],metro);
  }
}

function listingStyleDescription(index,context){
  const {family,values:v}=listingStyleEligible('description',index,context),d=digitsFor('description',index);
  const blocks=Object.fromEntries(listingStyleSectionKeys.map(key=>[key,listingStyleVaryHeading(key,v[key],d)]));
  if(!Object.values(blocks).some(Boolean))return '';
  if(['price-first','budget-note'].includes(family.id)&&v.price&&!blocks.conditions.includes(v.price))blocks.conditions=['💰 租金：'+v.price,blocks.conditions].filter(Boolean).join('\n');
  if(family.id==='university-reference'&&v.university&&!blocks.transport.includes(v.university))blocks.transport=['🎓 院校参考：'+v.university,blocks.transport].filter(Boolean).join('\n');
  const ordered=family.order.filter(key=>blocks[key]);
  const render=(keys,format)=>keys.map((key,i)=>listingStyleBlock(key,blocks[key],{format,d,index:i})).filter(Boolean).join(format==='compact'?'\n':'\n\n');
  let body;
  switch(family.format){
    case 'editorial':body=listingStyleEditorialOpeners[d[0]]+'\n\n'+render(ordered,'editorial');break;
    case 'rooms':body=(ordered.length>1?'先看配置资料，再了解房源的其他信息。':'先看已提供的配置资料。')+'\n\n'+render(ordered,'icons');break;
    case 'commute':body=render(ordered,'bullets');break;
    case 'compact':body=render(ordered,'compact');break;
    case 'numbered':body=render(ordered,'numbered');break;
    case 'budget':body='先核对租金与已提供的租赁说明。\n\n'+render(ordered,'card');break;
    case 'feature':body='这次先从已提供的家具家电资料看起。\n\n'+render(ordered,'icons');break;
    case 'paragraph':body=render(ordered,'paragraph');break;
    case 'viewing':body=listingStyleViewingOpeners[d[0]%listingStyleViewingOpeners.length]+'\n\n'+render(ordered,'editorial');break;
    case 'catalogue':body='房源编号：'+v.code+'\n\n'+render(ordered,'catalogue');break;
    case 'split':{
      const first=ordered.filter(key=>['location','facts','transport'].includes(key)),second=ordered.filter(key=>['equipment','conditions'].includes(key)),last=ordered.filter(key=>['service','contact'].includes(key));
      const names={location:'位置',facts:'房屋',transport:'交通',equipment:'配置',conditions:'租赁说明'},groupName=keys=>keys.map(key=>names[key]).join('与');
      body=[first.length?'【'+groupName(first)+'】\n'+render(first,'compact'):'',second.length?'【'+groupName(second)+'】\n'+render(second,'compact'):'',render(last,'icons')].filter(Boolean).join('\n\n');break;
    }
    case 'conditions':body=render(ordered,'card');break;
    case 'service':body=render(ordered,'bullets');break;
    case 'card':body=render(ordered,'card');break;
    case 'price':body=render(ordered,'icons');break;
    case 'location':body=render(ordered,'card');break;
    case 'space':body=render(ordered,'bullets');break;
    default:body=render(ordered,'icons');
  }
  return body.trim();
}

export function renderListingTemplate(kind,index=0,context={}){
  kind=listingStyleKind(kind);index=ordinal(index);
  if(!context||typeof context!=='object'||Array.isArray(context))context={};
  if(kind==='title')return cleanTitlePart(listingStyleTitle(index,context));
  if(kind==='description')return listingStyleDescription(index,context);
  // Tags retain the separately audited vocabulary, all supplied factual/brand
  // tags and complete v1 set variation. Layout styles do not invent audiences.
  return renderTemplate('tags',index,context);
}

export function renderListingEquipment(groups,index=0){
  if(!Array.isArray(groups))return '';
  const defaults={bedroom:['🛏','休息区'],living:['🛋','生活空间'],kitchen:['🍳','厨房'],
    bathroom:['🛁','清洁与卫浴'],storage:['👗','收纳'],study:['🖥','工作与学习']};
  return groups.slice(0,20).map(group=>{
    if(!group||typeof group!=='object'||!Array.isArray(group.items))return '';
    const items=group.items.map(listingStyleValue).filter(Boolean);if(!items.length)return '';
    const [icon,base]=defaults[group.id]||['▪️','已提供的配置'],label=listingStyleValue(group.label)||base;
    switch(ordinal(index)%4){
      case 0:return icon+' '+label+'\n'+items.map(item=>'✅ '+item).join('\n');
      case 1:return '▪️ '+label+'：'+items.join('、');
      case 2:return '【'+label+'】\n'+items.join(' / ');
      default:return icon+' '+label+'\n'+items.join(' · ');
    }
  }).filter(Boolean).join('\n\n');
}

export function listingStyleStats(){return {version:LISTING_STYLE_VERSION,families:LISTING_STYLE_FAMILIES.length,
  modes:['compact','detailed','story'],deterministic:true,baseCatalogVersion:TEMPLATE_CATALOG_VERSION,
  countsDescribe:'twenty-runtime-layout-families-not-an-additional-materialized-catalog',
  missingFacts:'omit-label-and-block',requiredBlocks:'caller-provided-facts-equipment-conditions-service-retained'};}
