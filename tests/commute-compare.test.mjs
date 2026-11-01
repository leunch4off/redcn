import test from 'node:test';
import assert from 'node:assert/strict';
import {universityCampusRegistry,commuteTimeRange,compareCommutes,rentalHousingContext,districtHousingSummary} from '../web/commute-compare.mjs';
import {normalizeUniversityRoutes,resolveUniversityRoutes,universityRouteVerification} from '../web/university-routes.mjs';

const now='2026-11-01T09:00:00Z';
const route=(patch={})=>({name:'МГУ',campusId:'msu-main',time:'40–55',mode:'public',transport:'Автобус 119 → метро',method:'manual-confirmed',checkedAt:'2026-10-31',accessMinutes:5,egressMinutes:7,transfers:1,...patch});
const card=(id,patch={})=>({id,title:'Квартира '+id,price:'80000',deposit:'40000',commission:'50',district:'Гагаринский',housingType:'apartment',universityRoutes:[route()],...patch});

test('campus registry exposes multiple concrete buildings, exact source receipt dates and address purpose',()=>{
 const rows=universityCampusRegistry({universityId:'msu'});assert(rows.length>2);assert(rows.some(row=>row.id==='msu-main'));assert(rows.some(row=>row.id==='msu-first'));
 assert(rows.every(row=>row.sources.length&&row.sources.every(source=>/^https:/.test(source.url)&&/^\d{4}-\d{2}-\d{2}$/.test(source.checkedAt))));
 assert(rows.every(row=>row.institutionUrlVerified));assert(rows.some(row=>row.sources.some(source=>source.publisher==='cpk.msu.ru')));
 const portal=universityCampusRegistry().find(row=>row.id==='markhi-main');assert(portal);assert.equal(portal.institutionUrlVerified,portal.sources.some(source=>source.evidenceType==='official-university'&&source.status==='verified'),'an educational portal alone is not relabelled as institution website');
 const contacts=universityCampusRegistry().filter(row=>row.locationType==='contact_address');assert(contacts.length>0);assert(contacts.every(row=>/не подтверждён/.test(row.locationLabel)));
 assert(universityCampusRegistry({query:'Моховая'}).some(row=>row.id==='msu-journalism'));
 assert(universityCampusRegistry({query:'第一人文'}).some(row=>row.id==='msu-first'));
});

test('comparison requires a concrete selected campus and never defaults the institution to its main building',()=>{
 const objects=[card('a')];assert.equal(compareCommutes(objects,{now}).included.length,0);
 assert.match(compareCommutes(objects,{now}).review[0].reasons[0],/конкретный/);
 const noCampus=card('b',{universityRoutes:[route({campusId:''})]});
 assert.equal(compareCommutes([noCampus],{campusId:'msu-main',now}).included.length,0);
 const different=card('c',{universityRoutes:[route({campusId:'msu-first'})]});
 assert.equal(compareCommutes([different],{campusId:'msu-main',now}).included.length,0);
 assert.equal(compareCommutes([different],{campusId:'msu-first',now}).included.length,1);
});

test('commute limits use the upper range bound and retain explicit unknown, stale and wrong-mode decisions',()=>{
 const objects=[card('a'),card('b',{universityRoutes:[route({time:'35'})]}),card('c',{universityRoutes:[route({time:'55–40'})]}),card('d',{universityRoutes:[route({checkedAt:'2025-01-01'})]}),card('e',{universityRoutes:[route({mode:'car',time:'10'})]})];
 const result=compareCommutes(objects,{campusId:'msu-main',maxMinutes:50,now});
 assert.deepEqual(result.included.map(row=>row.id),['b']);assert.equal(result.review.length,4);
 assert(result.review.find(row=>row.id==='a').reasons.some(reason=>/Верхняя граница/.test(reason)));
 assert(result.review.find(row=>row.id==='d').reasons.some(reason=>/актуальной/.test(reason)));
 assert(result.review.find(row=>row.id==='e').reasons.some(reason=>/транспорта/.test(reason)));
 assert.equal(compareCommutes(objects,{campusId:'msu-main',maxMinutes:10,mode:'all',now}).included[0].id,'e');
 for(const maxMinutes of [false,{},'40min',-1,1.5,1441])assert.throws(()=>compareCommutes(objects,{campusId:'msu-main',maxMinutes,now}),/Лимит/);
 assert.deepEqual(commuteTimeRange('0–7'),{min:0,max:7});assert.equal(commuteTimeRange('半小时'),null);assert.equal(commuteTimeRange('40–30'),null);
});

test('dated estimates appear only by explicit opt-in and never become verified provider results',()=>{
 const objects=[card('a',{universityRoutes:[route({method:'estimate'})]}),card('b',{universityRoutes:[route({method:'estimate',checkedAt:''})]})];
 assert.equal(compareCommutes(objects,{campusId:'msu-main',now}).included.length,0);
 const selected=compareCommutes(objects,{campusId:'msu-main',includeEstimates:true,now});assert.deepEqual(selected.included.map(row=>row.id),['a']);
 assert.equal(selected.included[0].route.verification.verified,false);assert.equal(selected.included[0].route.confirmedTime,false);assert.match(selected.included[0].route.timeLabel,/预估/);
 const provider=universityRouteVerification(route({method:'provider',sourceUrl:''}),{now});assert.equal(provider.verified,false);assert(provider.issues.some(text=>/URL/.test(text)));
 assert.equal(universityRouteVerification(route({method:'provider',sourceUrl:'https://yandex.ru/maps/?rtext=55,37~56,38'}),{now}).verified,true);
 assert.equal(universityRouteVerification(route({checkedAt:'2026-11-02'}),{now}).verified,false);
 assert.equal(universityRouteVerification(route({checkedAt:'2026-11-02'}),{now:'2026-11-01T22:00:00Z'}).verified,true,'calendar-day receipts follow Moscow, not the execution host timezone');
 assert.equal(universityRouteVerification(route(),{now:'invalid'}).verified,false);
});

test('computed estimates disclose Haversine method and calculation date without claiming a route',()=>{
 const output=resolveUniversityRoutes({latitude:55.7,longitude:37.5,universityRoutes:[{name:'Учебный корпус',latitude:55.72,longitude:37.52}]},{now})[0];
 assert.equal(output.distanceMethod,'haversine');assert.equal(output.distanceCheckedAt,'2026-11-01');assert.match(output.distanceEvidenceLabel,/Haversine/);assert.match(output.distanceEvidenceLabel,/非实际路线/);assert.equal(output.confirmedTime,false);
 const provided=resolveUniversityRoutes({universityRoutes:[route({routeDistanceKm:10})]},{now})[0];assert.equal(provided.distanceMethod,'provided');assert.equal(provided.distanceCheckedAt,'2026-10-31');
});

test('transport segments walking and transfer counts are explicit and preserved without computing a total',()=>{
 const fields={universityRoutes:[route({time:'',segments:[{mode:'walk',from:'Дом',to:'Остановка',minutes:5},{mode:'bus',transport:'119',from:'Остановка',to:'Университет',minutes:25}]})]};
 const normalized=normalizeUniversityRoutes(fields);assert.equal(normalized[0].accessMinutes,5);assert.equal(normalized[0].egressMinutes,7);assert.equal(normalized[0].transfers,1);assert.equal(normalized[0].segments[1].transport,'119');
 const resolved=resolveUniversityRoutes(fields,{now})[0];assert.match(resolved.partsLabel,/到站步行5分钟/);assert.match(resolved.partsLabel,/下车后步行7分钟/);assert.match(resolved.partsLabel,/换乘1次/);assert(resolved.segmentLabels[1].includes('119'));assert.equal(resolved.confirmedTime,false);assert.match(resolved.timeLabel,/待确认/);
 const compared=compareCommutes([card('a',fields)],{campusId:'msu-main',now});assert.equal(compared.included.length,0,'sum of riding segments is not an arrival-time measurement');
 assert.deepEqual(normalizeUniversityRoutes({universityRoutes:normalized}),normalized);
 const zero=resolveUniversityRoutes({universityRoutes:[route({accessMinutes:0,egressMinutes:0,transfers:0})]},{now})[0];assert.match(zero.partsLabel,/换乘0次/);
});

test('selected-building mismatches suppress route links and comparison rather than substitute a main campus',()=>{
 for(const patch of [{name:'РУДН'},{address:'Москва, улица другого корпуса, 99'}]){
  const fields={latitude:55.7,longitude:37.5,universityRoutes:[route(patch)]};const resolved=resolveUniversityRoutes(fields,{now})[0];
  assert.equal(resolved.confirmedDestination,false);assert.equal(resolved.links.google,'');assert.equal(resolved.distanceKm,null);assert(resolved.warnings.length);
  assert.equal(compareCommutes([card('a',fields)],{campusId:'msu-main',now}).included.length,0);
 }
});

test('table keeps monthly rent and private initial payment separate without inventing unknown costs or currency conversion',()=>{
 const result=compareCommutes([card('a'),card('b',{deposit:'',commission:''}),card('c',{price:'3500',pricePeriod:'day'}),card('d',{deposit:'1000',depositCurrency:'USD'})],{campusId:'msu-main',now});
 assert.equal(result.rows[0].monthlyRent,80000);assert.equal(result.rows[0].payment.total,160000);
 assert.equal(result.rows[1].payment.total,null);assert.equal(result.rows[1].payment.subtotal,80000);
 assert.equal(result.rows[2].monthlyRent,null);assert.equal(result.rows[2].payment.total,null);
 assert.equal(result.rows[3].payment.total,null);assert.equal(result.rows[3].payment.subtotal,120000,'only ruble rent and percent fee enter the subtotal');
});

test('district summaries distinguish explicitly classified dormitories from ordinary apartment descriptions',()=>{
 const nearby=card('a',{sourceDescription:'Квартира рядом с общежитием МГУ.'}),dorm=card('b',{housingType:'dormitory'}),unknown=card('c',{housingType:'',description:'Похоже на студенческий кампус.'}),workflow=card('d',{housingType:'apartment',workflow:{housingType:'room'}});
 assert.equal(rentalHousingContext(nearby).kind,'apartment');assert.match(rentalHousingContext(nearby).note,/не меняет тип/);
 assert.equal(rentalHousingContext(unknown).kind,'unknown');assert.equal(rentalHousingContext(workflow).kind,'room');
 assert.deepEqual(districtHousingSummary([nearby,dorm,unknown,workflow]),[{district:'Гагаринский',total:4,apartment:1,room:1,dormitory:1,unknown:1}]);
 const filtered=compareCommutes([nearby,dorm,unknown,workflow],{campusId:'msu-main',housingKind:'dormitory',now});assert.deepEqual(filtered.included.map(row=>row.id),['b']);
});

test('comparison and canonical evidence detach source records and strip unsafe route-source schemes',()=>{
 const objects=[card('a',{universityRoutes:[route({sourceUrl:'javascript:alert(1)'})]})],before=structuredClone(objects);const result=compareCommutes(objects,{campusId:'msu-main',now});
 assert.deepEqual(objects,before);assert.equal(result.rows[0].route.sourceUrl,'');
 result.rows[0].route.segments.push({mode:'walk'});assert.deepEqual(objects,before);
 assert.equal(universityRouteVerification(route({checkedAt:'2026-02-30'}),{now}).checkedAt,'');
});
