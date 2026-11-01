import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

const data=JSON.parse(await readFile(new URL('../data/metro-official-evidence.json',import.meta.url),'utf8'));
const sources=new Map(data.sources.map(row=>[row.id,row])),lines=new Map(data.lines.map(row=>[row.key,row]));
test('official metro supplement is bounded to reviewed pairs and attributes every claim',()=>{
  assert.equal(data.coverage.complete,false);assert.equal(data.coverage.stationLineAssertions,data.stationLineAssertions.length);
  assert.equal(data.coverage.currentHexColorsVerified,0);assert.equal(sources.size,data.sources.length);
  const checked=new Set();
  for(const row of data.stationLineAssertions){
    const key=row.ru+'|'+row.lineKey;assert(!checked.has(key));checked.add(key);assert(lines.has(row.lineKey));
    assert.equal(row.scope,'station-line-membership');assert.equal(row.translationStatus,'unchanged');
    assert.match(row.checkedAt,/^\d{4}-\d{2}-\d{2}$/);assert(row.sourceIds.length);
    for(const id of row.sourceIds){const source=sources.get(id);assert(source);assert.equal(source.evidenceType,'official-transport');assert.equal(source.verifiedAt,row.checkedAt);}
  }
  for(const source of sources.values()){
    const url=new URL(source.url);assert.equal(url.protocol,'https:');assert(['mosmetro.ru','www.mosmetro.ru','transport.mos.ru'].includes(url.hostname));
    assert.equal(source.status,'verified');assert(source.retrievalMethod);assert.equal(source.sha256,undefined);
  }
});
test('interchange names retain distinct verified line identities and retired service is explicit',()=>{
  assert.deepEqual(new Set(data.stationLineAssertions.filter(row=>row.ru==='Деловой центр').map(row=>row.lineKey)),new Set(['filyovskaya-branch','solntsevskaya','rublevo-arkhangelskaya']));
  assert.deepEqual(new Set(data.stationLineAssertions.filter(row=>row.ru==='Новомосковская').map(row=>row.lineKey)),new Set(['sokolnicheskaya','troitskaya']));
  assert.equal(data.stationLineAssertions.find(row=>row.ru==='Каховская').lineKey,'bolshaya-koltsevaya');
  assert.equal(data.retiredServices.find(row=>row.ru==='Каховская').status,'retired-merged-into-bkl');
  assert.equal(lines.get('rublevo-arkhangelskaya').number,'17');assert.equal(lines.get('troitskaya').number,'16');
  for(const line of lines.values()){assert.equal(line.colorHex,undefined);if(line.number)assert(line.numberSourceIds.every(id=>sources.has(id)));}
});
test('official Chinese legend evidence cannot silently certify station translations or service state',()=>{
  assert(data.officialChineseLineNames.length>=16);
  for(const row of data.officialChineseLineNames){assert.equal(row.scope,'printed-line-name-only');assert.equal(row.mapVersionDate,null);assert.equal(row.translationStatus,'official-legend');assert(row.sourceIds.every(id=>sources.has(id)));}
  assert.match(sources.get('mosmetro-chinese-map-pdf').limitations,/outlined station labels/);
  assert.match(data.integrationPolicy.stationTranslations,/Do not upgrade community translations/);
  assert.match(data.integrationPolicy.serviceStatus,/No open-now/);
});
