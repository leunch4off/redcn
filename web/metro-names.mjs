import {GEO_CATALOG} from './geo-catalog.mjs';

// Full attested/curated names always precede editorial word explanations.
// These component meanings are never advertised as an official station name.
export const METRO_COMPONENT_RULES=Object.freeze({
  'луг':'草地','роща':'树林','лесной':'森林','городок':'小镇','город':'城市',
  'парк':'公园','бульвар':'林荫道','площадь':'广场','улица':'街',
  'шоссе':'公路','проспект':'大街','вокзал':'火车站','аэропорт':'机场',
  'железнодорожная':'铁路','железнодорожный':'铁路','товарная':'货运',
  'строитель':'建设者','строителей':'建设者','красный':'红','красная':'红',
  'красное':'红','северная':'北','южная':'南','западная':'西',
  'восточная':'东','левобережная':'左岸','правобережная':'右岸'
});
const metroNameHan=/\p{Script=Han}/u,metroNameRussian=/[А-Яа-яЁё]/u;
const metroNameAmbiguous=new Set(['юго','луг','лук']);
const metroNameText=value=>typeof value==='string'?value.trim().slice(0,500):typeof value==='number'&&Number.isFinite(value)?String(value):'';
const metroNameFold=value=>String(value??'').normalize('NFC').toLocaleLowerCase('ru').replaceAll('ё','е').replace(/[‐‑‒–—]/g,'-').replace(/\s+/gu,' ').replace(/([\p{L}])\s*-\s*(?=[\p{L}])/gu,'$1-').trim();
const metroNameIsChinese=value=>typeof value==='string'&&metroNameHan.test(value)&&!metroNameRussian.test(value);
let metroNameCatalogIndex=null;
function metroNameIndex(){
  if(!metroNameCatalogIndex){const index=new Map();for(const row of GEO_CATALOG.records){if(row.kind!=='metro')continue;const key=metroNameFold(row.ru);if(!index.has(key))index.set(key,[]);index.get(key).push(row);}metroNameCatalogIndex=index;}
  return metroNameCatalogIndex;
}
function metroNamePreferred(rows=[]){return rows.find(row=>row.translationStatus==='reviewed'&&metroNameIsChinese(row.zh))||rows.find(row=>row.translationStatus==='studio_dictionary'&&metroNameIsChinese(row.zh))||rows.find(row=>metroNameIsChinese(row.zh))||rows[0];}
const metroNameRuleIndexes=new WeakMap(),metroNameNoRules=new Map();
function metroNameRuleIndex(rules){
  if(!rules||typeof rules!=='object'||Array.isArray(rules))return metroNameNoRules;
  const entries=Object.entries(rules).filter(([,value])=>typeof value==='string'&&value.trim()),fingerprint=JSON.stringify(entries),cached=metroNameRuleIndexes.get(rules);
  if(cached?.fingerprint===fingerprint)return cached.index;
  const index=new Map();for(const [ru,zh]of entries){const key=metroNameFold(ru);if(!index.has(key))index.set(key,[]);index.get(key).push({ru,zh:zh.trim()});}
  metroNameRuleIndexes.set(rules,{fingerprint,index});return index;
}
function metroNameRuleMatches(rules,key){return metroNameRuleIndex(rules).get(key)||[];}
function metroNameWithoutDecoration(value){
  let result=value;const quotes=[['«','»'],['“','”'],['"','"'],["'","'"],['「','」']];
  for(const [left,right]of quotes)if(result.startsWith(left)&&result.endsWith(right)&&result.length>1){result=result.slice(left.length,-right.length).trim();break;}
  result=result.replace(/^(?:🚇\s*|станция\s+метро\s+|метро\s+|ст\.\s*м\.\s*|м\.\s*)/iu,'').trim();
  for(const [left,right]of quotes)if(result.startsWith(left)&&result.endsWith(right)&&result.length>1){result=result.slice(left.length,-right.length).trim();break;}
  return result;
}
function metroNameCandidates(key){
  if(key.length<3)return [];
  return [...metroNameIndex().entries()].filter(([name])=>name.startsWith(key)||name.split(/[\s-]+/u).includes(key)).slice(0,6).map(([,rows])=>{const row=metroNamePreferred(rows);return {ru:row.ru,zh:row.zh||'',id:row.id};});
}
function metroNameResult(original,patch={}){return {label:original,original,canonicalRu:'',knownStation:false,complete:false,needsReview:!!original,source:original?'unresolved':'empty',translationStatus:original?'untranslated':'',translationSource:'',parts:[],candidates:[],...patch};}
function metroNamePartial(row){
  const original=row.ru,parts=[];let mixed='',cursor=0;
  for(const match of original.matchAll(/[\p{L}\p{M}]+/gu)){
    const word=match[0],zh=METRO_COMPONENT_RULES[metroNameFold(word)];if(!zh)continue;
    mixed+=original.slice(cursor,match.index)+zh;parts.push({ru:word,zh,start:match.index,end:match.index+word.length,source:'editorial-component'});cursor=match.index+word.length;
  }
  if(!parts.length)return null;
  mixed+=original.slice(cursor);
  // Only whitespace introduced between our own Chinese components is joined.
  // A user's exact replacement and catalog spelling are never reformatted.
  mixed=mixed.replace(/([\p{Script=Han}])\s+(?=[\p{Script=Han}])/gu,'$1');
  return {parts,mixed,label:metroNameRussian.test(mixed)?mixed+'（'+original+'）':original+'（字面：'+mixed+'）'};
}

/**
 * A metro-field formatter, not a general Russian translator or fuzzy geocoder.
 * Pass rules plus dictionary:DICTIONARY when rules contains the merged defaults;
 * changed/new full-key user entries then win without promoting generic words
 * such as Луг into an unknown station. No service/line/location is inferred.
 */
export function resolveMetroName(value,{rules={},dictionary={},includeRussian=false,partial=true}={}){
  const original=metroNameText(value);if(!original)return metroNameResult('');
  const undecorated=metroNameWithoutDecoration(original),key=metroNameFold(undecorated),originalKey=metroNameFold(original),index=metroNameIndex(),rows=index.get(key)||[],row=metroNamePreferred(rows);
  const ruleMatches=[...metroNameRuleMatches(rules,key),...(originalKey!==key?metroNameRuleMatches(rules,originalKey):[])],baseMatches=[...metroNameRuleMatches(dictionary,key),...(originalKey!==key?metroNameRuleMatches(dictionary,originalKey):[])];
  const baseValues=new Set(baseMatches.map(match=>match.zh)),explicit=ruleMatches.filter(match=>!baseValues.has(match.zh));
  const exact=explicit.at(-1);
  if(exact){const complete=metroNameIsChinese(exact.zh),suffix=includeRussian&&row&&complete?'（'+row.ru+'）':'';return metroNameResult(original,{label:exact.zh+suffix,canonicalRu:row?.ru||'',knownStation:!!row,complete,needsReview:!row||!complete||new Set(explicit.map(match=>match.zh)).size>1,source:'exact-rule',translationStatus:'explicit-rule'});}
  if(row){
    const dictionaryMatch=[...baseMatches,...ruleMatches].filter(match=>metroNameIsChinese(match.zh)).at(-1);
    if(dictionaryMatch)return metroNameResult(original,{label:dictionaryMatch.zh+(includeRussian?'（'+row.ru+'）':''),canonicalRu:row.ru,knownStation:true,complete:true,needsReview:false,source:'dictionary',translationStatus:'dictionary'});
    if(metroNameIsChinese(row.zh))return metroNameResult(original,{label:row.zh+(includeRussian?'（'+row.ru+'）':''),canonicalRu:row.ru,knownStation:true,complete:true,needsReview:false,source:'geo-catalog',translationStatus:row.translationStatus||'',translationSource:row.translationSource||''});
    const fallback=partial?metroNamePartial(row):null;
    if(fallback)return metroNameResult(original,{label:fallback.label,canonicalRu:row.ru,knownStation:true,source:'partial',translationStatus:'editorial-component',parts:fallback.parts});
    return metroNameResult(original,{canonicalRu:row.ru,knownStation:true});
  }
  // A Chinese name is not converted back to Russian. Matching a known Chinese
  // label establishes spelling only, never whether the station currently runs.
  if(metroNameIsChinese(undecorated)){
    const match=[...index.values()].map(metroNamePreferred).find(candidate=>metroNameFold(candidate.zh)===key);
    return metroNameResult(original,{label:undecorated,canonicalRu:match?.ru||'',knownStation:!!match,complete:true,needsReview:!match,source:match?'geo-catalog':'chinese-input',translationStatus:match?.translationStatus||''});
  }
  return metroNameResult(original,{source:metroNameAmbiguous.has(key)?'ambiguous':'unresolved',candidates:metroNameCandidates(key)});
}

export function translateMetroName(value,options={}){return resolveMetroName(value,options).label;}

export function metroNameCoverage(options={}){
  const rows=[...metroNameIndex().values()].map(metroNamePreferred),counts={names:rows.length,full:0,partial:0,untranslated:0};
  for(const row of rows){const result=resolveMetroName(row.ru,options);if(result.complete)counts.full++;else if(result.source==='partial')counts.partial++;else counts.untranslated++;}
  return counts;
}
