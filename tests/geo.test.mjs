import test from 'node:test';
import assert from 'node:assert/strict';
import {GEO_CATALOG,getGeoStats,searchGeo,resolveGeo,resolveGeoLine,officialGeoNames,translateGeo,geoLabel} from '../web/geo-catalog.mjs';

test('already Chinese text does not allocate a full Moscow matcher; exact and custom names still work',async()=>{
 const fresh=await import('../web/geo-catalog.mjs?negative-prefix-test');
 assert.equal(fresh.geoTranslationCacheStats().catalogTrieReady,false);
 for(const value of ['莫斯科温馨两居室','家具与家电按照片确认。','采光明亮，独立卧室'])assert.equal(fresh.translateGeo(value),value);
 assert.equal(fresh.geoTranslationCacheStats().catalogTrieReady,false);
 assert.equal(fresh.translateGeo('你好 世界',{你好:'您好'}),'您好 世界');
 assert.equal(fresh.translateGeo('Юго-Западная'),'西南（Юго-Западная）');
});

test('the negative prefix filter preserves Cyrillic Latin and digit names and word boundaries',()=>{
 const names=[resolveGeo('Юго-Западная'),GEO_CATALOG.records.find(row=>row.ru.startsWith('AlfaBitza')),
  GEO_CATALOG.records.find(row=>/^\d/u.test(row.ru)&&row.ru.length>2)];
 for(const row of names){assert.ok(row);const value='Рядом: '+row.ru+';';assert.equal(translateGeo(value),'Рядом: '+geoLabel(row)+';');assert.equal(translateGeo('X'+row.ru+'X'),'X'+row.ru+'X');}
 const short=GEO_CATALOG.records.find(row=>row.ru==='9');assert.ok(short);assert.equal(translateGeo('9'),geoLabel(short));assert.equal(translateGeo('этаж 9 / корпус 9'),'этаж 9 / корпус 9');
});

test('catalog contains substantial real station, street and stop snapshots with provenance',()=>{
 const s=getGeoStats();
 assert.ok(s.metro>=400);assert.ok(s.street>=5000);assert.ok(s.stop>=14000);
 assert.ok(s.metroNames<s.metro,'line interchanges remain separate');
 assert.ok(s.sources.every(source=>source.url&&(/^[a-f0-9]{64}$/.test(source.sha256)&&source.fetchedAt||['primary-search-excerpt','web-primary-page','web-primary-pdf-text'].includes(source.retrievalMethod)&&/^\d{4}-\d{2}-\d{2}$/.test(source.verifiedAt)&&!source.sha256)));
 const osm=s.sources.find(source=>source.id==='osm-moscow');
 assert.equal(osm.license,'ODbL 1.0');assert.match(osm.query,/3600102269/);assert.ok(osm.asOf);
 assert.match(s.coverage,/not asserted/);
 assert.ok(GEO_CATALOG.records.filter(row=>row.kind==='metro').every(row=>row.serviceStatus==='not_verified'||row.serviceStatus==='passenger-service-confirmed'&&row.serviceEvidence?.scope==='named-station-service-only'));
 assert.ok(s.translatedMetroNames>=273);
 assert.ok(s.translationSources.length>=9);
 assert.ok(GEO_CATALOG.records.filter(row=>row.translationStatus==='community').every(row=>row.translationSource&&row.translationLicense==='CC-BY-SA-4.0'));
});
test('each published official-name entry and reviewed runtime label has a dated scoped source',()=>{
 const sources=new Map(GEO_CATALOG.sources.map(row=>[row.id,row])),names=officialGeoNames();assert.equal(names.length,21);assert.equal(GEO_CATALOG.officialNamesCoverage.complete,false);
 for(const name of names){assert(name.ru&&name.zh&&name.scope);assert.match(name.checkedAt,/^\d{4}-\d{2}-\d{2}$/);for(const id of name.sourceIds){assert(sources.has(id));assert.equal(sources.get(id).verifiedAt,name.checkedAt);}}
 for(const row of GEO_CATALOG.records.filter(row=>row.translationStatus==='reviewed')){assert.match(row.translationVerifiedAt,/^\d{4}-\d{2}-\d{2}$/);assert(row.translationSource);assert(row.translationEvidence.scope);assert(row.translationEvidence.sourceIds.every(id=>sources.has(id)));}
 const avenue=resolveGeo('Проспект Вернадского','metro');assert.equal(avenue.translationEvidence.scope,'published-avenue-name');assert.match(avenue.translationEvidence.applicationNote,/not an operator-certified/);
 const troparyovo=resolveGeo('Тропарёво','metro');assert.match(troparyovo.translationEvidence.sourceLocator,/page 2/);assert.equal(troparyovo.translationVerifiedAt,'2026-11-01');
 names[0].sourceIds.length=0;assert(officialGeoNames()[0].sourceIds.length,'public getter detaches source references');
});
test('official printed line names are usable while membership matches exact line identity only',()=>{
 assert.equal(resolveGeoLine('Сокольническая линия').zh,'驯隼人村庄线');assert.equal(translateGeo('Большая кольцевая'),'大环线（Большая кольцевая линия）');assert.equal(resolveGeoLine('Рублёво-Архангельская').translationStatus,'community','the new source is community nomenclature, not the old official legend');assert.equal(resolveGeoLine('Рублёво-Архангельская').officialNameEvidence,undefined);
 const pairs=GEO_CATALOG.records.flatMap(row=>(row.officialMembershipEvidence||[]).map(evidence=>({row,evidence})));assert.equal(pairs.length,14);assert.equal(GEO_CATALOG.officialStationLineAssertions.length,16);
 for(const {row,evidence}of pairs){assert.equal(row.ru.toLowerCase(),evidence.ru.toLowerCase());assert.equal(evidence.scope,'station-line-membership');assert.equal(evidence.translationStatus,'unchanged');}
 const legacy=GEO_CATALOG.records.find(row=>row.ru==='Каховская'&&row.lines.includes('11'));assert.equal(legacy.officialMembershipEvidence,undefined);assert.equal(legacy.serviceStatus,'not_verified');
 const named=GEO_CATALOG.records.find(row=>row.ru==='Саларьево'&&row.lines.includes('1'));assert.equal(named.serviceStatus,'not_verified');assert.equal(named.translationStatus,'studio_dictionary');assert.equal(named.officialMembershipEvidence[0].lineNumber,'1');
});
test('current primary facts remain scoped to named line 17 stations and explicit Chinese examples',()=>{
 const line=GEO_CATALOG.lines.find(row=>row.id==='171');assert.equal(line.officialNumber,'17');assert.equal(line.officialColorName,'графитовый');assert.equal(line.colorStatus,'snapshot-rgb-not-officially-verified');
 const stations=GEO_CATALOG.records.filter(row=>row.serviceStatus==='passenger-service-confirmed');assert.equal(stations.length,5);assert(stations.every(row=>row.lines.includes('171')&&row.officialLineNumbers.includes('17')&&row.serviceEvidence.verifiedAt==='2026-11-01'));
 for(const [ru,zh]of [['Митино','米金诺'],['Полянка','珀梁卡']]){const row=resolveGeo(ru,'metro');assert.equal(row.zh,zh);assert.equal(row.translationStatus,'reviewed');assert.equal(row.translationVerifiedAt,'2026-11-01');assert.match(row.translationSource,/transport.mos.ru/);}
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
