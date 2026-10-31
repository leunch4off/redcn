import test from 'node:test';
import assert from 'node:assert/strict';
import {GEO_CATALOG,getGeoStats,searchGeo,resolveGeo,translateGeo,geoLabel} from '../web/geo-catalog.mjs';

test('catalog contains substantial real station, street and stop snapshots with provenance',()=>{
 const s=getGeoStats();
 assert.ok(s.metro>=400);assert.ok(s.street>=5000);assert.ok(s.stop>=14000);
 assert.ok(s.metroNames<s.metro,'line interchanges remain separate');
 assert.ok(s.sources.every(source=>/^[a-f0-9]{64}$/.test(source.sha256)&&source.url&&source.fetchedAt));
 const osm=s.sources.find(source=>source.id==='osm-moscow');
 assert.equal(osm.license,'ODbL 1.0');assert.match(osm.query,/3600102269/);assert.ok(osm.asOf);
 assert.match(s.coverage,/not asserted/);
 assert.ok(GEO_CATALOG.records.filter(row=>row.kind==='metro').every(row=>row.serviceStatus==='not_verified'));
 assert.ok(s.translatedMetroNames>=273);
 assert.ok(s.translationSources.length>=9);
 assert.ok(GEO_CATALOG.records.filter(row=>row.translationStatus==='community').every(row=>row.translationSource&&row.translationLicense==='CC-BY-SA-4.0'));
});
test('reviewed complete Chinese station names preserve original spelling',()=>{
 const southwest=resolveGeo('ЮГО-ЗАПАДНАЯ','metro');
 assert.equal(southwest.zh,'西南');assert.equal(southwest.translationStatus,'reviewed');
 assert.match(southwest.translationSource,/ranepa.ru/);
 assert.equal(translateGeo('Юго-Западная'),'西南（Юго-Западная）');
 assert.equal(resolveGeo('Тропарево','metro').zh,'特罗帕廖沃');
});
test('geo translation is one pass with whole-name matches and caller override precedence',()=>{
 assert.equal(translateGeo('метро Юго-Западная',{метро:'地铁',западная:'西'}),'地铁 西南（Юго-Западная）');
 assert.equal(translateGeo('Юго-Западная',{'юго-западная':'CUSTOM'}),'CUSTOM');
 assert.equal(translateGeo('Юго-Западная и Москва',{Москва:'莫斯科'}),'西南（Юго-Западная） и 莫斯科');
 assert.equal(translateGeo('СверхНовокосино'),'СверхНовокосино');
 assert.equal(translateGeo('Тропарёво'),geoLabel(resolveGeo('Тропарево')));
});
test('unreviewed names retain entire Russian name rather than invented syllables',()=>{
 const row=GEO_CATALOG.records.find(row=>row.kind==='metro'&&!row.zh);
 assert.ok(row);assert.equal(translateGeo(row.ru),'地铁站（'+row.ru+'）');
 assert.ok(searchGeo(row.ru,{kind:'metro',limit:10}).some(result=>result.id===row.id));
 assert.ok(searchGeo('西南',{kind:'metro'}).some(result=>result.ru==='Юго-Западная'));
 assert.deepEqual(searchGeo(''),[]);assert.ok(searchGeo('улица',{limit:2000}).length<=200);
});
test('every catalog record has a stable id, kind and original name',()=>{
 assert.equal(new Set(GEO_CATALOG.records.map(row=>row.id)).size,GEO_CATALOG.records.length);
 assert.ok(GEO_CATALOG.records.every(row=>row.id&&row.ru&&['metro','street','stop'].includes(row.kind)&&row.source));
 const row=GEO_CATALOG.records.find(row=>row.kind==='street');assert.ok(row.segments>=1);
});
test('repeated unknown text and edited dictionary use cached matching without stale values',()=>{
 const rules={Москва:'莫斯科'};
 translateGeo('Москва неизвестный объект рядом с метро',rules); // compile once
 const started=performance.now();
 for(let i=0;i<128;i++)assert.equal(translateGeo('Неизвестный объект в Москве',rules),'Неизвестный объект в Москве');
 const elapsed=performance.now()-started;
 assert.ok(elapsed<2000,`128 repeated translations took ${elapsed.toFixed(0)} ms`);
 rules.Москва='UPDATED';assert.equal(translateGeo('Москва рядом',rules),'UPDATED рядом');
});
