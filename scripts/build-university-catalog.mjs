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
export function searchUniversities(query='',{limit=30,includeCampuses=true,includeInactive=false}={}){const q=universityFold(query);const rows=includeCampuses?[...UNIVERSITY_CATALOG.universities,...UNIVERSITY_CATALOG.campuses]:UNIVERSITY_CATALOG.universities;return rows.filter(row=>(includeInactive||universityCampusAvailability(row).selectable)&&(!q||[row.ru,row.zh,row.addressRu,row.city,row.region,...(row.aliases||[]),...(row.academicUnitHistory||[]).map(unit=>unit.ru)].some(value=>universityFold(value).includes(q)))).sort((a,b)=>universitySearchRank(b,q)-universitySearchRank(a,q)||Number(a.kind==='campus')-Number(b.kind==='campus')||a.ru.localeCompare(b.ru,'ru')).slice(0,Math.max(0,Math.min(10000,Math.trunc(Number(limit)||30))));}
export function universityRules(){universityIndex??=universityMakeIndex(UNIVERSITY_CATALOG.universities);campusIndex??=universityMakeIndex(UNIVERSITY_CATALOG.campuses);const rows=[...UNIVERSITY_CATALOG.universities,...UNIVERSITY_CATALOG.campuses],result={};for(const row of rows.filter(value=>universityCampusAvailability(value).selectable))for(const alias of [row.ru,...row.aliases]){const resolved=universityGlobalLookup(alias,row.kind);if(resolved?.id===row.id)result[alias]=row.zh;}return result;}
export function universityStats(){const values=UNIVERSITY_CATALOG.campuses;return {universities:UNIVERSITY_CATALOG.universities.length,campuses:values.length,locations:values.length,teachingCampuses:values.filter(row=>row.locationType==='teaching_campus').length,teachingBuildings:values.filter(row=>row.locationType==='teaching_building').length,mainAddresses:values.filter(row=>row.locationType==='main_address').length,contactAddresses:values.filter(row=>row.locationType==='contact_address').length,studentCenters:values.filter(row=>row.locationType==='student_center').length,sources:UNIVERSITY_CATALOG.sources.length,aliases:Object.keys(universityRules()).length,asOf:UNIVERSITY_CATALOG.asOf,coverage:UNIVERSITY_CATALOG.coverage};}\n`;
const uniBuildPointRuntime=String.raw`
const universityCampusById=new Map(UNIVERSITY_CATALOG.campuses.map(row=>[row.id,row]));
const universitySourceById=new Map(UNIVERSITY_CATALOG.sources.map(row=>[row.id,row]));
const universityDateValid=(value,now)=>{if(typeof value!=='string'||!/^\d{4}-\d{2}-\d{2}$/.test(value))return false;const parsed=new Date(value+'T00:00:00Z');return Number.isFinite(parsed.getTime())&&parsed.toISOString().slice(0,10)===value&&value<=now.toISOString().slice(0,10);};
export function universityCampusAvailability(value){
 const row=typeof value==='string'?universityCampusById.get(value):value;
 if(!row)return {selectable:false,status:'unknown',currentCampusId:''};
 const state=row.locationLifecycle;
 return {selectable:!state||state.status==='current-listed',status:state?.status||'catalogued',currentCampusId:state?.currentCampusId||'',checkedAt:state?.checkedAt||'',noteRu:state?.noteRu||''};
}
export function verifiedCampusPoint(value,{now=new Date()}={}){
 const row=typeof value==='string'?universityCampusById.get(value):value,canonical=universityCampusById.get(row?.id),e=canonical?.coordinateEvidence,supplied=row?.coordinateEvidence;
 if(!canonical||!universityCampusAvailability(canonical).selectable||!universityCampusAvailability(row).selectable||!e||!supplied||e.method!=='official-published-point'||!['building','campus','institution'].includes(e.scope))return null;
 if(!['method','scope','checkedAt','accuracyMeters','accuracyStatus','publishedPrecision','entranceVerified','boundCampusId','boundAddressRu','accuracyScope'].every(key=>supplied[key]===e[key])||!Array.isArray(supplied.sourceIds)||JSON.stringify([...supplied.sourceIds].sort())!==JSON.stringify([...e.sourceIds].sort()))return null;
 if(row.latitude!==canonical.latitude||row.longitude!==canonical.longitude||row.addressRu!==canonical.addressRu||e.boundCampusId!==row.id||universityFold(e.boundAddressRu)!==universityFold(row.addressRu)||e.accuracyScope!=='published-'+e.scope+'-point')return null;
 if(!Number.isFinite(row.latitude)||!Number.isFinite(row.longitude)||Math.abs(row.latitude)>90||Math.abs(row.longitude)>180||!universityDateValid(e.checkedAt,now)||e.entranceVerified!==false)return null;
 if(e.accuracyMeters===null?e.accuracyStatus!=='not-published':!Number.isFinite(e.accuracyMeters)||e.accuracyMeters<=0||e.accuracyMeters>10000||e.accuracyStatus!=='published')return null;
 if(!Array.isArray(e.sourceIds)||!e.sourceIds.length||!e.sourceIds.every(id=>{const source=universitySourceById.get(id);if(!source||!row.sourceIds.includes(id)||source.evidenceType!=='official-university'||source.verifiedAt!==e.checkedAt||source.institutionId!==row.universityId||!source.campusIds?.includes(row.id)||!source.supports?.includes('published-coordinate'))return false;try{const url=new URL(source.url);return url.protocol==='https:'&&!url.username&&!url.password;}catch{return false;}}))return null;
 return {latitude:row.latitude,longitude:row.longitude,evidence:JSON.parse(JSON.stringify(e))};
}
`;
await writeFile(new URL('web/university-catalog.mjs',uniBuildRoot),uniBuildRuntime+uniBuildPointRuntime);
console.log('Built university catalog:',uniBuildData.universities.length,'universities,',uniBuildData.campuses.length,'locations');
