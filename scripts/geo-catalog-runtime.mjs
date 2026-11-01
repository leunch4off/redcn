// Runtime is kept separate so catalog refreshes never change lookup behavior.
const geoFold = value => String(value ?? '').normalize('NFC').toLocaleLowerCase('ru').replaceAll('ё','е').replace(/\s+/g,' ').trim();
const geoKinds = {metro:'地铁站',street:'街道',stop:'公交站',line:'地铁线路'};
let geoByName=null;
function geoNameIndex(){
  if(!geoByName){
    const index=new Map();
    for(const row of GEO_CATALOG.records){const key=geoFold(row.ru);if(!index.has(key))index.set(key,[]);index.get(key).push(row);}
    geoByName=index;
  }
  return geoByName;
}
export function resolveGeo(name,kind=''){
  const key=geoFold(name);if(!key)return undefined;
  const rows=geoNameIndex().get(key)||[];
  return rows.find(row=>(!kind||row.kind===kind)&&row.translationStatus==='reviewed')||rows.find(row=>!kind||row.kind===kind);
}
export function resolveGeoLine(name){
  const key=geoFold(name).replace(/\s+линия$/u,'');
  const row=GEO_CATALOG.lines.find(line=>(line.officialNameEvidence||line.communityNameEvidence)&&geoFold(line.ru).replace(/\s+линия$/u,'')===key);
  return row?{...row,kind:'line'}:undefined;
}
export function officialGeoNames(){
  return (GEO_CATALOG.officialNameCatalog||[]).map(row=>({...row,sourceIds:[...(row.sourceIds||[])]}));
}
// Verified membership is an independently sourced fact. A retired HH line row,
// a shared station name or an RGB value never establishes current membership.
export function resolveVerifiedMetroLines(name){
  const station=geoFold(name),facts=new Map((GEO_CATALOG.officialLineCatalog||[]).map(row=>[row.key,row])),sources=new Map(GEO_CATALOG.sources.map(row=>[row.id,row]));
  const result=[];
  for(const claim of GEO_CATALOG.officialStationLineAssertions||[]){
    if(geoFold(claim.ru)!==station||claim.scope!=='station-line-membership')continue;
    const line=facts.get(claim.lineKey);if(!line?.number||!claim.checkedAt)continue;
    const sourceIds=[...new Set([...(claim.sourceIds||[]),...(line.numberSourceIds||[]),...(line.colorSourceIds||[])])];
    if(!sourceIds.length||sourceIds.some(id=>!sources.has(id)))continue;
    if(result.some(row=>row.key===line.key))continue;
    result.push({stationRu:claim.ru,key:line.key,number:line.number,lineRu:line.ru,officialColorName:line.officialColorName||'',checkedAt:claim.checkedAt,sourceIds,sourceUrls:sourceIds.map(id=>sources.get(id).url),colorRGBVerified:false,scope:'explicit-station-line-membership'});
  }
  return result;
}
export function geoLabel(row,{includeRussian=true}={}){
  if(!row)return '';
  const chinese=row.zh||geoKinds[row.kind]||'地名';
  return includeRussian?chinese+'（'+row.ru+'）':row.zh||row.ru;
}
export function searchGeo(query,{kind='',limit=30}={}){
  const q=geoFold(query),safeLimit=Math.max(1,Math.min(200,Number(limit)||30));
  if(!q)return [];
  const matches=[];
  for(const row of GEO_CATALOG.records){if(kind&&row.kind!==kind)continue;
    const ru=geoFold(row.ru),zh=geoFold(row.zh);
    if(ru.includes(q)||zh.includes(q))matches.push({...row,label:geoLabel(row),exact:ru===q||zh===q});
  }
  return matches.sort((a,b)=>Number(b.exact)-Number(a.exact)||a.ru.localeCompare(b.ru,'ru')).slice(0,safeLimit);
}
export function getGeoStats(){
  const records=GEO_CATALOG.records;
  return {metro:records.filter(r=>r.kind==='metro').length,street:records.filter(r=>r.kind==='street').length,
    stop:records.filter(r=>r.kind==='stop').length,metroNames:new Set(records.filter(r=>r.kind==='metro').map(r=>geoFold(r.ru))).size,
    translated:records.filter(r=>r.zh).length,translatedMetroNames:new Set(records.filter(r=>r.kind==='metro'&&r.zh).map(r=>geoFold(r.ru))).size,
    reviewed:records.filter(r=>r.translationStatus==='reviewed').length,reviewedNames:new Set(records.filter(r=>r.translationStatus==='reviewed').map(r=>geoFold(r.ru))).size,
    updatedAt:GEO_CATALOG.updatedAt,coverage:GEO_CATALOG.coverage,sources:GEO_CATALOG.sources,translationSources:GEO_CATALOG.translationSources||[],officialNames:officialGeoNames(),officialNamesCoverage:GEO_CATALOG.officialNamesCoverage||{complete:false},verifiedMetroMemberships:(GEO_CATALOG.officialStationLineAssertions||[]).length};
}
function geoTrie(names){
  const root=new Map();
  for(const name of names){let node=root;for(const char of name){if(!node.has(char))node.set(char,new Map());node=node.get(char);}node.value=name;}
  return root;
}
let geoNameTrie=null;
let geoNameStarts=null;
function geoCatalogCanStart(text){
  if(!geoNameStarts)geoNameStarts=new Set([...geoNameIndex().keys()].filter(name=>name.length>2).map(name=>name[0]));
  for(let index=0;index<text.length;index++)if(geoNameStarts.has(text[index])&&!geoWordCharacter(text[index-1]))return true;
  return false;
}
function geoCatalogMatcher(){
  if(!geoNameTrie)geoNameTrie=geoTrie([...geoNameIndex().keys()].filter(name=>name.length>2));
  return geoNameTrie;
}
const geoRegexCache=new Map();
export function geoTranslationCacheStats(){return {nameIndexReady:geoByName!==null,catalogTrieReady:geoNameTrie!==null,customMatchers:geoRegexCache.size};}
const geoEmptyOverrides=Object.freeze({});
function geoMatcher(supplied){
  const key=JSON.stringify([...supplied]);
  if(geoRegexCache.has(key))return geoRegexCache.get(key);
  const trie=geoTrie(supplied.keys());
  if(geoRegexCache.size>=8)geoRegexCache.delete(geoRegexCache.keys().next().value);
  geoRegexCache.set(key,trie);return trie;
}
const geoWordCharacter=char=>!!char&&/[\p{L}\p{N}_]/u.test(char);
function geoMatchAt(root,text,start){
  let node=root,best=null;
  for(let i=start;i<text.length;i++){
    const char=/\s/u.test(text[i])?' ':text[i];
    if(!node.has(char))break;
    node=node.get(char);
    if(node.value&&!geoWordCharacter(text[i+1]))best={key:node.value,end:i+1};
  }
  return best;
}
export function translateGeo(text,overrides=geoEmptyOverrides,{includeRussian=true}={}){
  const original=String(text??'');if(!original)return original;
  const supplied=new Map(Object.entries(overrides||{}).filter(([key,value])=>key.trim()&&typeof value==='string').map(([key,value])=>[geoFold(key),value]));
  if(supplied.has(geoFold(original)))return supplied.get(geoFold(original));
  const exact=resolveGeo(original);
  if(exact)return geoLabel(exact,{includeRussian});
  const line=resolveGeoLine(original);
  if(line)return geoLabel(line,{includeRussian});
  // Full names only. Never turn Юго-Западная into a made-up mixed name.
  // Existing non-geographic dictionary terms are handled by the caller.
  // A trie avoids a megabyte regular expression and keeps unknown input cheap.
  const folded=original.toLocaleLowerCase('ru').replaceAll('ё','е'),customTrie=geoMatcher(supplied),catalogTrie=geoCatalogCanStart(folded)?geoCatalogMatcher():null;
  let output='',cursor=0;
  for(let start=0;start<folded.length;start++){
    if(geoWordCharacter(folded[start-1]))continue;
    const geographic=catalogTrie?geoMatchAt(catalogTrie,folded,start):null,custom=geoMatchAt(customTrie,folded,start);
    const match=custom&&(!geographic||custom.end>=geographic.end)?custom:geographic;
    if(!match)continue;
    output+=original.slice(cursor,start)+(supplied.has(match.key)?supplied.get(match.key):geoLabel(resolveGeo(match.key),{includeRussian}));
    cursor=match.end;start=match.end-1;
  }
  return output+original.slice(cursor);
}
