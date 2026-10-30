#!/usr/bin/env node
import {readFile,writeFile} from 'node:fs/promises';
const uniBuildRoot=new URL('../',import.meta.url);
const uniBuildData=JSON.parse(await readFile(new URL('data/moscow-universities.json',uniBuildRoot),'utf8'));
const uniBuildIds=new Set(),uniBuildSourceIds=new Set(uniBuildData.sources.map(row=>row.id));
for(const row of [...uniBuildData.universities,...uniBuildData.campuses]){
  if(!row.id||uniBuildIds.has(row.id))throw Error('Duplicate/missing university catalog ID: '+row.id);
  uniBuildIds.add(row.id);
  if(!row.ru||!row.zh||!Array.isArray(row.aliases)||!row.region||!row.sourceIds?.length||row.sourceIds.some(id=>!uniBuildSourceIds.has(id)))throw Error('Invalid university row: '+row.id);
  if(row.kind==='campus'&&!uniBuildData.universities.some(parent=>parent.id===row.universityId))throw Error('Unknown campus parent: '+row.id);
}
const uniBuildRuntime=`// Generated from data/moscow-universities.json. Run scripts/build-university-catalog.mjs.\nexport const UNIVERSITY_CATALOG = ${JSON.stringify(uniBuildData)};\n
const universityFold=value=>String(value??'').normalize('NFC').toLocaleLowerCase('ru').replaceAll('ё','е').replace(/[«»“”„\"']/g,'').replace(/[‐‑–—]/g,'-').replace(/\\s+/g,' ').trim();
let universityIndex=null,campusIndex=null,universityGlobalIndex=null;
function universityMakeIndex(rows){const result=new Map();for(const row of rows)for(const alias of [row.ru,row.zh,...(row.aliases||[])]){const key=universityFold(alias);if(!key)continue;if(!result.has(key))result.set(key,row);else if(result.get(key)?.id!==row.id)result.set(key,null);}return result;}
function universityGlobalLookup(text,kind){universityGlobalIndex??=universityMakeIndex([...UNIVERSITY_CATALOG.universities,...UNIVERSITY_CATALOG.campuses]);const row=universityGlobalIndex.get(universityFold(text));return row?.kind===kind?row:null;}
export function resolveUniversity(text){return universityGlobalLookup(text,'university');}
export function resolveCampus(text){return universityGlobalLookup(text,'campus');}
export function translateUniversity(text){return (resolveCampus(text)||resolveUniversity(text))?.zh??String(text??'');}
function universitySearchRank(row,query){if(!query)return 0;const names=[row.ru,row.zh,...(row.aliases||[])].map(universityFold);return names.includes(query)?3:names.some(name=>name.startsWith(query))?2:names.some(name=>name.includes(query))?1:0;}
export function searchUniversities(query='',{limit=30,includeCampuses=true}={}){const q=universityFold(query);const rows=includeCampuses?[...UNIVERSITY_CATALOG.universities,...UNIVERSITY_CATALOG.campuses]:UNIVERSITY_CATALOG.universities;return rows.filter(row=>!q||[row.ru,row.zh,row.addressRu,row.city,row.region,...(row.aliases||[])].some(value=>universityFold(value).includes(q))).sort((a,b)=>universitySearchRank(b,q)-universitySearchRank(a,q)||Number(a.kind==='campus')-Number(b.kind==='campus')||a.ru.localeCompare(b.ru,'ru')).slice(0,Math.max(0,Math.min(10000,Math.trunc(Number(limit)||30))));}
export function universityRules(){universityIndex??=universityMakeIndex(UNIVERSITY_CATALOG.universities);campusIndex??=universityMakeIndex(UNIVERSITY_CATALOG.campuses);const rows=[...UNIVERSITY_CATALOG.universities,...UNIVERSITY_CATALOG.campuses],result={};for(const row of rows)for(const alias of [row.ru,...row.aliases]){const resolved=universityGlobalLookup(alias,row.kind);if(resolved?.id===row.id)result[alias]=row.zh;}return result;}
export function universityStats(){const values=UNIVERSITY_CATALOG.campuses;return {universities:UNIVERSITY_CATALOG.universities.length,campuses:values.length,locations:values.length,teachingCampuses:values.filter(row=>row.locationType==='teaching_campus').length,teachingBuildings:values.filter(row=>row.locationType==='teaching_building').length,mainAddresses:values.filter(row=>row.locationType==='main_address').length,contactAddresses:values.filter(row=>row.locationType==='contact_address').length,studentCenters:values.filter(row=>row.locationType==='student_center').length,sources:UNIVERSITY_CATALOG.sources.length,aliases:Object.keys(universityRules()).length,asOf:UNIVERSITY_CATALOG.asOf,coverage:UNIVERSITY_CATALOG.coverage};}\n`;
await writeFile(new URL('web/university-catalog.mjs',uniBuildRoot),uniBuildRuntime);
console.log('Built university catalog:',uniBuildData.universities.length,'universities,',uniBuildData.campuses.length,'locations');
