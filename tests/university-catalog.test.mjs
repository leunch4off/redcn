import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {UNIVERSITY_CATALOG,resolveUniversity,resolveCampus,translateUniversity,searchUniversities,universityRules,universityStats} from '../web/university-catalog.mjs';

test('university runtime exactly matches attributed source dataset',async()=>{
  const source=JSON.parse(await readFile(new URL('../data/moscow-universities.json',import.meta.url),'utf8'));
  assert.deepEqual(UNIVERSITY_CATALOG,source);
  const ids=new Set(),sources=new Map(source.sources.map(row=>[row.id,row]));
  assert.equal(sources.size,source.sources.length);
  for(const row of [...source.universities,...source.campuses]){
    assert(!ids.has(row.id));ids.add(row.id);
    assert(row.ru&&/[\u3400-\u9fff]/u.test(row.zh),'Chinese name: '+row.id);
    assert(Array.isArray(row.aliases));assert(row.region&&row.sourceIds.length);
    for(const id of row.sourceIds)assert(sources.has(id),'Source '+id+' for '+row.id);
  }
  for(const row of source.sources){assert.equal(new URL(row.url).protocol,'https:');assert.equal(row.status,'verified');assert(row.verifiedAt);if(row.sha256)assert.match(row.sha256,/^[0-9a-f]{64}$/);}
});
test('known university aliases normalize case while retaining complete Chinese names',()=>{
  assert.equal(resolveUniversity('мгу')?.id,'msu');assert.equal(resolveUniversity('НИУ ВШЭ')?.id,'hse');
  assert.equal(resolveUniversity('RUDN')?.id,'rudn');assert.equal(resolveUniversity('高等经济大学')?.id,'hse');
  assert.equal(translateUniversity('МГИМО'),'莫斯科国立国际关系学院');
  assert.equal(translateUniversity('Неизвестный университет'),'Неизвестный университет');
  for(const text of ['университет','кампус','вышка','МГУП']){assert.equal(resolveUniversity(text),null);assert.equal(resolveCampus(text),null);}
});
test('specific study destination remains separate from university and apartment proximity',()=>{
  assert.equal(resolveCampus('ВШЭ Таллинская')?.universityId,'hse');
  const odintsovo=resolveCampus('МГИМО Одинцово');assert.equal(odintsovo?.region,'Московская область');assert.equal(odintsovo?.city,'Одинцово');
  assert.equal(resolveUniversity('МГИМО Одинцово'),null);
  for(const row of UNIVERSITY_CATALOG.campuses){
    assert(UNIVERSITY_CATALOG.universities.some(parent=>parent.id===row.universityId));
    assert(row.addressRu);assert(['teaching_campus','teaching_building','main_address','administrative','dormitory','contact_address','student_center'].includes(row.locationType),row.id);
    assert.equal(row.proximityVerified,false);assert(!Object.hasOwn(row,'apartmentWalkingMinutes'));
  }
});
test('search finds Chinese/Russian aliases and address without truncating the full knowledge list',()=>{
  const all=searchUniversities('',{limit:10000});assert.equal(all.length,UNIVERSITY_CATALOG.universities.length+UNIVERSITY_CATALOG.campuses.length);
  const faculties=searchUniversities('Моховая',{limit:100});assert(faculties.some(row=>row.id==='msu-journalism'));assert(faculties.some(row=>row.id==='msu-iaas'));
  assert(searchUniversities('奥金佐沃',{limit:100}).some(row=>row.id==='mgimo-odintsovo'));
  assert(searchUniversities('ВШЭ',{includeCampuses:false,limit:100}).every(row=>row.kind==='university'));
  assert(searchUniversities('ВШЭ',{limit:1}).length<=1);
});
test('rules and counts separate institutions, teaching locations and addresses',()=>{
  const stats=universityStats(),rules=universityRules();
  assert.equal(stats.universities,UNIVERSITY_CATALOG.universities.length);assert.equal(stats.locations,UNIVERSITY_CATALOG.campuses.length);
  assert.equal(rules['МГУ'],resolveUniversity('МГУ').zh);assert.equal(rules['ВШЭ Таллинская'],resolveCampus('ВШЭ Таллинская').zh);
  assert.equal(rules['университет'],undefined);assert.equal(rules['кампус'],undefined);
  assert.equal(stats.mainAddresses,UNIVERSITY_CATALOG.campuses.filter(row=>row.locationType==='main_address').length);
  for(const row of UNIVERSITY_CATALOG.universities){assert(row.chinaEvidence?.type);assert(row.chinaEvidence?.note);assert(row.chinaEvidence?.sourceIds?.length);assert(!Object.hasOwn(row,'popularityRank'));}
});

// A parent abbreviation reused as a building alias must never hijack either resolver.
test('ambiguous aliases are rejected across university and campus kinds',()=>{
  const sentinel='UNI-CAMPUS-COLLISION-TEST';
  // Importing a fresh module creates a fresh lazy index for this fixture.
  return import('../web/university-catalog.mjs?global-collision='+Date.now()).then(fresh=>{
    const u=fresh.UNIVERSITY_CATALOG.universities[0],c=fresh.UNIVERSITY_CATALOG.campuses.find(row=>row.universityId===u.id);
    u.aliases.push(sentinel);c.aliases.push(sentinel);
    assert.equal(fresh.resolveUniversity(sentinel),null);assert.equal(fresh.resolveCampus(sentinel),null);assert.equal(fresh.universityRules()[sentinel],undefined);
  });
});
