import test from 'node:test';
import assert from 'node:assert/strict';
import {normalizeUniversityRoutes,resolveUniversityRoutes,universityStraightLineKm,universityRouteCoordinates,universityMapLinks,universityStopLinks} from '../web/university-routes.mjs';
import {UNIVERSITY_CATALOG} from '../web/university-catalog.mjs';

test('only the explicitly selected verified building supplies a published point with provenance',()=>{
  const fields={latitude:55.7,longitude:37.5,universityRoutes:[{name:'МГУ',campusId:'msu-physics',mode:'public'}]};
  const row=resolveUniversityRoutes(fields)[0];assert.equal(row.destination.latitude,55.699876);assert.equal(row.destinationPointSource,'catalogue');assert.equal(row.destinationPointEvidence.accuracyMeters,null);assert.equal(row.destinationPointEvidence.entranceVerified,false);assert.equal(row.links.yandexKind,'route');assert.equal(row.confirmedTime,false);assert.equal(row.transport,'');assert.equal(row.distanceKind,'straight-line');
  const partial=resolveUniversityRoutes({...fields,universityRoutes:[{...fields.universityRoutes[0],latitude:55.8}]})[0];assert.equal(partial.destinationPointSource,'');assert.equal(partial.straightLineKm,null);
  const manual=resolveUniversityRoutes({...fields,universityRoutes:[{...fields.universityRoutes[0],latitude:55.8,longitude:37.8}]})[0];assert.equal(manual.destinationPointSource,'manual');assert.equal(manual.destination.latitude,55.8);assert.equal(manual.destinationPointEvidence,null);
  const mismatch=resolveUniversityRoutes({...fields,universityRoutes:[{name:'МГУ',campusId:'msu-physics',address:'Другой корпус'}]})[0];assert.equal(mismatch.destinationPointSource,'');assert.equal(mismatch.links.yandex,'');
  const universityOnly=resolveUniversityRoutes({latitude:55.7,longitude:37.5,university:'МГУ'})[0];assert.equal(universityOnly.destinationPointSource,'');assert.equal(universityOnly.straightLineKm,null);
});

test('legacy university rows retain explicit duration, transport and selected campus without inventing a route',()=>{
  const fields={university:'МГУ',campus:'МГУ — Главное здание',universities:'МГУ | 40-55 | public | Автобус 119\nРУДН | | | '};
  const normalized=normalizeUniversityRoutes(fields);assert.equal(normalized.length,2);assert.equal(normalized[0].campus,fields.campus);assert.equal(normalized[0].transport,'Автобус 119');
  const rows=resolveUniversityRoutes(fields);assert.equal(rows[0].timeLabel,'约40–55分钟');assert.equal(rows[0].transportLabel,'公共交通 · Автобус 119');assert.equal(rows[1].timeLabel,'通勤时间待确认');assert.equal(rows[1].transportLabel,'');assert.equal(rows[1].distanceKm,null);
});

test('an explicitly emptied structured list does not revive old legacy university fields',()=>{
  assert.deepEqual(normalizeUniversityRoutes({university:'МГУ',universities:'МГУ | 40 | metro',universityRoutes:[]}),[]);
  assert.deepEqual(resolveUniversityRoutes({university:'МГУ',universityRoutes:[]}),[]);
});

test('specific catalog campus address opens Google transit directions while Yandex remains an honest address search',()=>{
  const row=resolveUniversityRoutes({latitude:55.7,longitude:37.5,universityRoutes:[{name:'МГУ',campusId:'msu-main'}]})[0];
  assert.equal(row.title,'莫斯科国立大学');assert.equal(row.campusTitle,'莫斯科国立大学主楼');assert.equal(row.confirmedDestination,true);
  assert.equal(row.links.googleKind,'route');const google=new URL(row.links.google);assert.equal(google.pathname,'/maps/dir/');assert.equal(google.searchParams.get('travelmode'),'transit');assert.match(google.searchParams.get('destination'),/Ленинские горы/);
  assert.equal(row.links.yandexKind,'search');assert.equal(new URL(row.links.yandex).searchParams.get('rtext'),null);assert.equal(row.distanceKm,null);assert.equal(row.distanceLabel,'');assert.equal(row.confirmedTime,false);assert.equal(row.transport,'');
});

test('university name alone does not select a main campus or manufacture distance or transport',()=>{
  const row=resolveUniversityRoutes({latitude:55.7,longitude:37.5,university:'МГУ'})[0];
  assert.equal(row.confirmedDestination,false);assert.equal(row.campusTitle,'');assert.equal(row.links.googleKind,'search');assert.equal(new URL(row.links.google).pathname,'/maps/search/');assert.equal(row.links.yandexKind,'search');assert.equal(row.distanceKind,'unknown');assert.equal(row.distanceKm,null);assert.equal(row.transportLabel,'');
});

test('coordinate zero is valid and Haversine distance is labelled straight line, never travelling distance',()=>{
  assert.deepEqual(universityRouteCoordinates(0,0),{latitude:0,longitude:0});assert.equal(universityRouteCoordinates(false,0),null);assert.equal(universityRouteCoordinates('',0),null);assert.equal(universityRouteCoordinates('  ',0),null);assert.equal(universityRouteCoordinates('0x0',0),null);assert.equal(universityRouteCoordinates([],0),null);assert.equal(universityRouteCoordinates(91,0),null);
  const distance=universityStraightLineKm({latitude:0,longitude:0},{latitude:0,longitude:1});assert(Math.abs(distance-111.195)<.01);
  const row=resolveUniversityRoutes({latitude:0,longitude:0,universityRoutes:[{name:'Test campus',latitude:0,longitude:1}]})[0];
  assert.equal(row.distanceKind,'straight-line');assert.match(row.distanceLabel,/^直线距离约111\.2公里$/);assert.equal(row.timeLabel,'通勤时间待确认');
  const yandex=new URL(row.links.yandex);assert.equal(yandex.searchParams.get('rtext'),'0,0~0,1');assert.equal(yandex.searchParams.get('rtt'),'mt');
  const point=new URL(universityStopLinks({name:'Stop',latitude:55.7,longitude:37.5}).yandex);assert.equal(point.searchParams.get('pt'),'37.5,55.7');
});

test('provided route distance remains distinct from computed straight-line distance and no time is inferred',()=>{
  const row=resolveUniversityRoutes({latitude:55.7,longitude:37.5,universityRoutes:[{name:'МГУ',latitude:55.702,longitude:37.530,routeDistanceKm:6.2,transport:'Автобус 119 → метро',mode:'bus'}]})[0];
  assert.equal(row.distanceKind,'provided-route');assert.equal(row.distanceKm,6.2);assert.match(row.distanceLabel,/^路线距离约6\.2公里（按已填资料）$/);assert(row.straightLineKm>0);assert.equal(row.timeLabel,'通勤时间待确认');assert.equal(row.transport,'Автобус 119 → метро');
});

test('campus conflicts suppress destination links and derived distances instead of selecting the wrong institution',()=>{
  const row=resolveUniversityRoutes({latitude:55.7,longitude:37.5,universityRoutes:[{name:'РУДН',campusId:'msu-main',latitude:55.7,longitude:37.6,routeDistanceKm:5}]})[0];
  assert(row.warnings.length);assert.equal(row.confirmedDestination,false);assert.equal(row.links.google,'');assert.equal(row.links.yandex,'');assert.equal(row.distanceKm,null);assert.equal(row.distanceLabel,'');
  const alternative=UNIVERSITY_CATALOG.campuses.find(value=>value.universityId==='msu'&&value.id!=='msu-main');assert(alternative);
  const mismatched=resolveUniversityRoutes({universityRoutes:[{name:'МГУ',campusId:'msu-main',campus:alternative.ru}]})[0];assert(mismatched.warnings.length);assert.equal(mismatched.links.google,'');
});

test('named stops open fixed map providers with safely encoded queries and no guessed coordinates',()=>{
  const name='<script> & redirect=https://example.invalid/';const links=universityStopLinks({name});
  for(const [provider,hostname]of [['yandex','yandex.ru'],['google','www.google.com'],['osm','www.openstreetmap.org']]){const link=new URL(links[provider]);assert.equal(link.hostname,hostname);assert.equal(link.protocol,'https:');assert(!links[provider].includes('<script>'));}
  assert.equal(new URL(links.yandex).searchParams.get('text'),'Москва, '+name);assert.equal(new URL(links.yandex).searchParams.get('pt'),null);
  const row=resolveUniversityRoutes({universityRoutes:[{name:'МГУ',stops:[{name:'Остановка у дома'},{name:'Университет',latitude:55.7,longitude:37.5}]}]})[0];assert.deepEqual(row.stops,['Остановка у дома','Университет']);assert.equal(row.stopDetails[0].latitude,'');assert(row.stopDetails[1].links.yandex.includes('pt='));
});

test('normalization is detached, bounded, idempotent and preserves incomplete coordinate input without calculating a point',()=>{
  const fields={universityRoutes:[{name:' МГУ ',latitude:'55.7',longitude:'',stops:[{name:' A ',latitude:0,longitude:''}],routeDistanceKm:0,unexpectedUrl:'javascript:alert(1)'}]};const saved=JSON.stringify(fields),rows=normalizeUniversityRoutes(fields);
  assert.deepEqual(normalizeUniversityRoutes({universityRoutes:rows}),rows);assert.equal(rows[0].latitude,55.7);assert.equal(rows[0].longitude,'');assert.equal(rows[0].routeDistanceKm,0);assert(!Object.hasOwn(rows[0],'unexpectedUrl'));assert.equal(JSON.stringify(fields),saved);
  const output=resolveUniversityRoutes({latitude:55,longitude:37,universityRoutes:rows})[0];assert.equal(output.straightLineKm,null);
  assert.equal(normalizeUniversityRoutes({universityRoutes:Array.from({length:12},()=>({name:'School'}))}).length,10);
});

test('manual translation overrides canonical title and campus while invalid time ranges stay unconfirmed',()=>{
  const row=resolveUniversityRoutes({universityRoutes:[{name:'МГУ',campus:'МГУ — Главное здание',time:'55–40'}]},{translate:value=>value==='МГУ'?'我的大学':value==='МГУ — Главное здание'?'我的校区':value})[0];
  assert.equal(row.title,'我的大学');assert.equal(row.campusTitle,'我的校区');assert.equal(row.confirmedTime,false);assert.equal(row.timeLabel,'通勤时间待确认');assert(row.warnings.length);
});

test('each provider gets its documented transport mode and query addresses are not repeated',()=>{
  for(const [mode,googleMode,yandexMode]of [['walk','walking','pd'],['car','driving','auto'],['bike','bicycling','bc'],['public','transit','mt']]){const links=universityMapLinks({latitude:55,longitude:37},{latitude:56,longitude:38},mode);assert.equal(new URL(links.google).searchParams.get('travelmode'),googleMode);assert.equal(new URL(links.yandex).searchParams.get('rtt'),yandexMode);}
  const query=new URL(universityStopLinks({address:'Москва, Ленинские горы'}).yandex).searchParams.get('text');assert.equal(query,'Москва, Ленинские горы');
});

test('map hyperlink queries omit pasted telephone numbers without changing source or bus identifiers',()=>{
  const source={address:'Москва, улица 8 Марта, 12. +7 (999) 123-45-67, автобус 119'};const old=JSON.stringify(source),query=new URL(universityStopLinks(source).google).searchParams.get('query');
  assert(!query.includes('999'));assert(query.includes('автобус 119'));assert(query.includes('8 Марта'));assert.equal(JSON.stringify(source),old);
  const local=new URL(universityStopLinks({name:'Остановка 8 (999) 123-45-67'}).yandex).searchParams.get('text');assert(!local.includes('999'));
  assert.equal(new URL(universityStopLinks({latitude:55.712345,longitude:37.523456}).google).searchParams.get('query'),'55.712345,37.523456');
});

test('selected door points survive normalization and drive exported map links without replacing saved building coordinates',()=>{
 const a={latitude:55.7,longitude:37.5,name:'Вход дома',kind:'building-entrance',availability:'available',checkedAt:'2026-11-01',sourceUrl:'https://example.org/door'},b={...a,latitude:55.71,longitude:37.51,name:'Выход метро',kind:'metro-exit'};
 const fields={latitude:55.6,longitude:37.4,universityRoutes:[{name:'Тестовый корпус',address:'Тестовый адрес',latitude:55.8,longitude:37.6,originPoint:a,destinationPoint:b,useEntrances:true,mode:'walk'}]},route=resolveUniversityRoutes(fields)[0];
 assert.equal(route.origin.latitude,55.7);assert.equal(route.destination.latitude,55.71);assert.equal(route.destinationPointSource,'selected-point');assert.match(route.links.yandex,/55\.7%2C37\.5/);assert.equal(fields.latitude,55.6);assert.equal(normalizeUniversityRoutes(fields)[0].originPoint.name,'Вход дома');
});

test('expired provider receipt becomes stale and remains explicitly a network estimate',()=>{
 const calculation={provider:'valhalla-fossgis-demo',calculatedAt:'2026-11-01T10:00:00Z',expiresAt:'2026-11-01T10:10:00Z',durationSeconds:700,distanceKm:1.1,travelTimeKind:'network-estimate',responseSha256:'a'.repeat(64)},row={name:'Корпус',method:'provider',sourceUrl:'https://valhalla1.openstreetmap.de/route',checkedAt:'2026-11-01',time:'12',calculation};
 const result=resolveUniversityRoutes({universityRoutes:[row]},{now:'2026-11-01T10:11:00Z'})[0];assert.equal(result.verification.stale,true);assert.equal(result.verification.verified,false);assert.equal(result.verification.estimated,true);assert.ok(result.warnings.some(s=>s.includes('Срок маршрутного расчёта истёк')));
});

test('former campus cannot re-enter routing through manually supplied coordinates',()=>{
 const result=resolveUniversityRoutes({latitude:55.7,longitude:37.5,universityRoutes:[{name:'ВШЭ',campusId:'hse-economics-former-shabolovka',latitude:55.72,longitude:37.61,originPoint:{latitude:55.7,longitude:37.5},destinationPoint:{latitude:55.72,longitude:37.61},mode:'walk',routeDistanceKm:1,time:'12'}]})[0];
 assert.equal(result.confirmedDestination,false);assert.equal(result.distanceKm,null);assert.equal(result.links.google,'');assert.equal(result.destination.address,'');assert.ok(result.warnings.some(s=>s.includes('прежний')));
});

test('a partial selected point cannot silently fall back to an address or catalogue centre',()=>{
 const row={name:'МГУ',campusId:'msu-physics',mode:'walk',time:'12',routeDistanceKm:1,destinationPoint:{latitude:55.7,longitude:''}},route=resolveUniversityRoutes({latitude:55.6,longitude:37.5,universityRoutes:[row]})[0];
 assert.equal(route.confirmedDestination,false);assert.equal(route.distanceKm,null);assert.equal(route.links.google,'');assert.equal(route.destination.address,'');assert.ok(route.warnings.some(s=>s.includes('не полностью')));
});
