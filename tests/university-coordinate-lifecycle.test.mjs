import test from 'node:test';
import assert from 'node:assert/strict';
import {UNIVERSITY_CATALOG,resolveCampus,verifiedCampusPoint,universityCampusAvailability,searchUniversities,universityRules} from '../web/university-catalog.mjs';

const clone=value=>JSON.parse(JSON.stringify(value));
test('only explicit source-bound address points supply catalogue coordinates',()=>{
  assert.equal(verifiedCampusPoint('msu'),null,'university never chooses its main building');
  assert.equal(verifiedCampusPoint('msu-physics-optics'),null,'neighbouring building never borrows physics coordinates');
  const points=UNIVERSITY_CATALOG.campuses.filter(row=>row.coordinateEvidence);
  assert.equal(points.length,4);
  for(const row of points){const point=verifiedCampusPoint(row);assert(point,row.id);assert.equal(point.evidence.boundCampusId,row.id);assert.equal(point.evidence.boundAddressRu,row.addressRu);assert.equal(point.evidence.accuracyScope,'published-building-point');assert.equal(point.evidence.accuracyMeters,null);assert.equal(point.evidence.entranceVerified,false);}
});
test('tampered address, coordinates, source, date and precision do not gain verification',()=>{
  const canonical=resolveCampus('МГУ физфак');
  for(const change of [row=>row.addressRu='Москва, Ленинские горы, д. 1, стр. 62',row=>row.latitude+=.01,row=>row.coordinateEvidence.boundCampusId='other',row=>row.coordinateEvidence.boundAddressRu='another address',row=>row.coordinateEvidence.sourceIds=['location-msu-sternberg'],row=>row.coordinateEvidence.checkedAt='2099-01-01',row=>row.coordinateEvidence.accuracyStatus='exact',row=>{row.coordinateEvidence.accuracyStatus='published';row.coordinateEvidence.accuracyMeters=1;},row=>row.coordinateEvidence.entranceVerified=true,row=>row.locationLifecycle={status:'closed'}]){
    const row=clone(canonical);change(row);assert.equal(verifiedCampusPoint(row),null);
  }
});
test('former faculty address stays resolvable for old cards and is excluded from current suggestions',()=>{
  const old=resolveCampus('ВШЭ экономфак Шаболовка');assert.equal(old.id,'hse-economics-former-shabolovka');
  const state=universityCampusAvailability(old);assert.equal(state.selectable,false);assert.equal(state.currentCampusId,'hse-economics-pokrovsky');assert.equal(old.locationLifecycle.buildingClosed,false);
  assert.equal(searchUniversities('ВШЭ экономфак Шаболовка',{limit:100}).some(row=>row.id===old.id),false);
  assert.equal(searchUniversities('ВШЭ экономфак Шаболовка',{limit:100,includeInactive:true}).some(row=>row.id===old.id),true);
  assert.equal(universityRules()['ВШЭ экономфак Шаболовка'],undefined);
  const current=resolveCampus(state.currentCampusId)||UNIVERSITY_CATALOG.campuses.find(row=>row.id===state.currentCampusId);assert(universityCampusAvailability(current).selectable);assert.match(current.addressRu,/Покровский/);
});
test('new faculty contact points remain separate from teaching buildings, closures and geocoding',()=>{
  const ids=['hse-chemistry-vavilova','hse-biology-profsoyuz','hse-biology-miklukho','hse-urban-myasnitskaya','hse-urban-basmannaya','hse-physics-basmannaya','hse-economics-pokrovsky'];
  for(const id of ids){const row=UNIVERSITY_CATALOG.campuses.find(value=>value.id===id);assert.equal(row.locationType,'faculty_contact');assert.equal(row.locationLifecycle.status,'current-listed');assert.equal(row.locationLifecycle.physicalAccessVerified,false);assert.equal(verifiedCampusPoint(row),null);}
  assert.notEqual(resolveCampus('МГУ юрфак').id,resolveCampus('МГУ ФМП').id);
});
test('a closed unit remains historical evidence and never becomes a current university alias',()=>{
 const hse=UNIVERSITY_CATALOG.universities.find(row=>row.id==='hse'),unit=hse.academicUnitHistory[0];assert.equal(unit.status,'closed');assert.equal(unit.closureScope,'academic-unit-only');assert.equal(unit.effectiveClosureDate,null);assert.equal(resolveCampus(unit.ru),null);assert.equal(universityRules()[unit.ru],undefined);assert(searchUniversities(unit.ru,{limit:100}).some(row=>row.id==='hse'));
 const source=UNIVERSITY_CATALOG.sources.find(row=>row.id===unit.sourceIds[0]);assert(source.supports.includes('academic-unit-closure'));assert.equal(source.publishedAt,unit.documentDate);
});
