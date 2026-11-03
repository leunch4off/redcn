import test from 'node:test';
import assert from 'node:assert/strict';
import {GEO_CATALOG,resolveGeo,resolveGeoLine,officialGeoNames,translateGeo} from '../web/geo-catalog.mjs';
test('contractor-published station names carry scoped provenance without operator certification',()=>{
 const row=resolveGeo('Железнодорожная','metro');assert.equal(row.zh,'热列兹诺多罗日纳亚');assert.equal(row.translationStatus,'published-primary-name');assert.equal(row.translationAuthority,'contractor-published-name-not-operator');assert.equal(row.translationVerifiedAt,'2026-11-01');assert(row.translationSource.startsWith('https://npsgk.ru/ch/news/'));assert.equal(row.alternativeChineseNames[0].operatorCertified,false);
 assert(!officialGeoNames().some(value=>value.ru==='Железнодорожная'));assert.match(translateGeo('Железнодорожная'),/热列兹诺多罗日纳亚/);
});
test('new community line spelling never imports community color or present-service claims',()=>{
 const row=resolveGeoLine('Рублёво-Архангельская');assert.equal(row.zh,'鲁布廖夫-阿尔汉格尔斯克线');assert.equal(row.translationStatus,'community');assert.equal(row.communityNameEvidence.operatorCertified,false);assert.equal(row.officialColorName,'графитовый');assert.equal(row.colorStatus,'snapshot-rgb-not-officially-verified');assert(!officialGeoNames().some(value=>value.ru==='Рублёво-Архангельская'&&value.scope==='printed-line-name-only'));
});
test('line-specific published alternatives preserve existing Chinese choice on other lines',()=>{
 const rows=GEO_CATALOG.records.filter(value=>value.kind==='metro'&&value.ru==='Петровско-Разумовская');const d1=rows.find(value=>value.lines.includes('131'));assert.equal(d1,undefined,'the HH snapshot lacks the new D1 record; never attach its evidence to the D3 record');assert(GEO_CATALOG.publishedStationNameFacts.some(value=>value.ru==='Петровско-Разумовская'&&value.lineIds.includes('131')));
 for(const row of rows.filter(value=>!value.lines.includes('131')))assert(!row.alternativeChineseNames?.some(value=>value.sourceId==='nps-zh-petrovsko'));
 assert.equal(GEO_CATALOG.publishedNameAliasReview.length,3);assert(GEO_CATALOG.publishedNameAliasReview.every(value=>value.decision.length>40));
});
