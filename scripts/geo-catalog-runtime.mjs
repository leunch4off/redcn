// Runtime is kept separate so catalog refreshes never change lookup behavior.
const geoFold = value => String(value ?? '').normalize('NFC').toLocaleLowerCase('ru').replaceAll('ё','е').replace(/\s+/g,' ').trim();
const geoKinds = {metro:'地铁站',street:'街道',stop:'公交站'};
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
    updatedAt:GEO_CATALOG.updatedAt,coverage:GEO_CATALOG.coverage,sources:GEO_CATALOG.sources,translationSources:GEO_CATALOG.translationSources||[]};
}
function geoTrie(names){
  const root=new Map();
  for(const name of names){let node=root;for(const char of name){if(!node.has(char))node.set(char,new Map());node=node.get(char);}node.value=name;}
  return root;
}
let geoNameTrie=null;
function geoCatalogMatcher(){
  if(!geoNameTrie)geoNameTrie=geoTrie([...geoNameIndex().keys()].filter(name=>name.length>2));
  return geoNameTrie;
}
const geoRegexCache=new Map();
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
  // Full names only. Never turn Юго-Западная into a made-up mixed name.
  // Existing non-geographic dictionary terms are handled by the caller.
  // A trie avoids a megabyte regular expression and keeps unknown input cheap.
  const folded=original.toLocaleLowerCase('ru').replaceAll('ё','е'),customTrie=geoMatcher(supplied),catalogTrie=geoCatalogMatcher();
  let output='',cursor=0;
  for(let start=0;start<folded.length;start++){
    if(geoWordCharacter(folded[start-1]))continue;
    const geographic=geoMatchAt(catalogTrie,folded,start),custom=geoMatchAt(customTrie,folded,start);
    const match=custom&&(!geographic||custom.end>=geographic.end)?custom:geographic;
    if(!match)continue;
    output+=original.slice(cursor,start)+(supplied.has(match.key)?supplied.get(match.key):geoLabel(resolveGeo(match.key),{includeRussian}));
    cursor=match.end;start=match.end-1;
  }
  return output+original.slice(cursor);
}
