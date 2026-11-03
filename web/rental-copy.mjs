// Grounded advertising language. These are finite editorial rules, not model
// weights or hundreds of thousands of independently written advertisements.
// All property values are supplied by the fact-checked engine. This module
// never guesses a room, appliance, journey time, permission or availability.
export const RENTAL_COPY_VERSION='grounded-rental-copy-v1';
const text=value=>typeof value==='string'||typeof value==='number'?String(value).trim():'';
const line=value=>text(value).replace(/\s+/gu,' ');
const counter=value=>Number.isFinite(Number(value))?Math.abs(Math.trunc(Number(value))):0;

export function listingCopySeed(fields={}){
  // Counters belong to a card, but fresh imports all used to start at zero.
  // A stable source/card identity spreads their first layouts without changing
  // the persisted counter or making reloads pick a new random rendition.
  let identity=line(fields.url)||line(fields.resultId)||line(fields.editorIdentity)||line(fields.id);
  if(!identity)return 0;
  try{const url=new URL(identity);identity=url.origin+url.pathname.replace(/\/+$/u,'');}catch{}
  let hash=2166136261;
  for(const char of identity)hash=Math.imul(hash^char.codePointAt(0),16777619);
  return (hash>>>0)%4096;
}

export function listingCopyVariation(fields={},part='body'){
  const key={title:'titleVariant',body:'copyVariant',tags:'tagVariant'}[part];
  if(!key)throw new TypeError('Неизвестная часть объявления');
  return listingCopySeed(fields)+counter(fields[key]);
}

export function publicationFingerprint(value,part='body'){
  const source=String(value??'').normalize('NFC');
  // Emoji, spacing and punctuation alone are not a new advertisement. Tag
  // order alone likewise does not make a different recommendation set.
  if(part==='tags')return [...new Set(source.match(/#[^\s#]+/gu)||[])].sort().join(' ');
  return source.replace(/[^\p{L}\p{N}]/gu,'');
}

export function publicationDigest(value,part='body'){
  const normalized=publicationFingerprint(value,part);
  let first=2166136261,second=2246822519;
  for(const char of normalized){const code=char.codePointAt(0);first=Math.imul(first^code,16777619);second=Math.imul(second^code,3266489917);}
  // Bounded duplicate-detection metadata, not a security/content checksum.
  return normalized.length.toString(36)+'-'+(first>>>0).toString(36)+'-'+(second>>>0).toString(36);
}

export function normalizeCopyHistory(value){
  const result={version:1,title:[],body:[],tags:[]};
  for(const part of ['title','body','tags'])if(Array.isArray(value?.[part]))result[part]=[...new Set(value[part].filter(row=>typeof row==='string'&&/^[a-z0-9]+-[a-z0-9]+-[a-z0-9]+$/u.test(row)))].slice(-64);
  return result;
}

export function rememberPublication(history,part,value){
  if(!['title','body','tags'].includes(part))throw new TypeError('Неизвестная часть объявления');
  const result=normalizeCopyHistory(history),digest=publicationDigest(value,part);
  result[part]=[...result[part].filter(row=>row!==digest),digest].slice(-64);
  return result;
}

export const ADVERTISING_LEADS=Object.freeze([
  {id:'overview',requires:['layout'],write:v=>`🏡 莫斯科租房｜${v.area?v.area+'的':''}${v.layout}${v.price?'，月租'+v.price:''}。想了解这套房源，可以从下面的空间、配置和租住条件看起。`},
  {id:'rent-first',requires:['price'],write:v=>`💰 先看预算：月租${v.price}${v.area?'，面积'+v.area:''}${v.layout?'，户型为'+v.layout:''}。租金之外的费用和入住条件也列在下方，方便一起比较。`},
  {id:'space-first',requires:['area'],write:v=>`🏠 ${v.area}${v.layout?' · '+v.layout:''}。选房时，可以把面积、空间安排和现有配置一起看；这套的具体信息都在下面。`},
  {id:'transport-first',requires:['metro'],write:v=>`🚇 出行先看这一条：${v.metro}。${v.area||v.layout?[v.area,v.layout].filter(Boolean).join(' · ')+'，':''}${v.price?'月租'+v.price+'。':''}再看看室内配置和租赁条件是否符合自己的需求。`},
  {id:'equipment-first',requires:['feature'],write:v=>`✨ 这套房源可以先从配置看起：${v.feature}。${[v.area,v.layout].filter(Boolean).join(' · ')}${v.price?'，月租'+v.price:''}。完整配置和其他条件继续往下看。`},
  {id:'layout-question',requires:['layout'],write:v=>`🔎 正在找${v.layout}？这套莫斯科房源${v.area?'面积为'+v.area:''}${v.price?'，月租'+v.price:''}。把位置、配置和费用放在一起，看看是否适合自己的安排。`},
  {id:'quick-summary',requires:['layout','area','price'],write:v=>`📌 先记下这套的几个重点：${[v.layout,v.area,v.price?'月租'+v.price:''].filter(Boolean).join('｜')}。想细看房源，下面把各项信息展开。`},
  {id:'comparison',requires:['layout','area','price'],write:v=>`🗂 看房做比较，可以先从这套开始：${[v.layout,v.area,v.price?'月租'+v.price:''].filter(Boolean).join('，')}。配置、交通和租赁说明分开列出，找起来更直接。`},
  {id:'details-invitation',requires:['layout'],write:v=>`🗝️ 来看看这套${v.area?v.area+'的':''}${v.layout}${v.price?'：月租'+v.price:''}。不只看一个价格，把房源的具体条件也一起看清楚。`},
  {id:'inventory-invitation',requires:['feature'],write:v=>`🛋 ${v.feature}，是这套房源已列明的配置。${v.layout?'户型为'+v.layout+'，':''}${v.area?'面积'+v.area+'，':''}${v.price?'月租'+v.price+'。':''}还有哪些家具家电、费用如何安排，下面逐项说明。`},
  {id:'route-and-home',requires:['metro'],write:v=>`📍 先安排日常出行，再挑住处：${v.metro}。这套${v.area?v.area+'的':''}${v.layout||'房源'}${v.price?'月租'+v.price:''}，室内与租住信息见下方。`},
  {id:'save-to-compare',requires:['layout','area','price'],write:v=>`🏡 ${[v.area,v.layout].filter(Boolean).join(' · ')}${v.price?'｜月租'+v.price:''}。可以先记下来，再按自己的预算、出行和配置需求逐项比较。`},
].map(row=>Object.freeze({...row,requires:Object.freeze(row.requires)})));

export function advertisingLead(context={},index=0){
  const values=Object.fromEntries(['layout','area','price','metro','feature'].map(key=>[key,line(context[key])]));
  const available=ADVERTISING_LEADS.filter(row=>row.requires.some(key=>values[key]));
  if(!available.length)return {text:'',id:null,available:0};
  const selected=available[counter(index)%available.length];
  return {text:selected.write(values),id:selected.id,available:available.length};
}

export function rentalCopyStats(){
  return {version:RENTAL_COPY_VERSION,leadFrames:ADVERTISING_LEADS.length,
    engine:'editorial-rules',modelAuthored:false,unlimited:false,
    counters:'per-card-with-stable-source-seed',
    distinctResults:'measured-per-card-not-promised-by-combination-count'};
}
