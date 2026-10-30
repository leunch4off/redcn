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
