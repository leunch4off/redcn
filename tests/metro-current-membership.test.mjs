import test from 'node:test';
import assert from 'node:assert/strict';
import {GEO_CATALOG,resolveVerifiedMetroLines} from '../web/geo-catalog.mjs';
test('current station-number membership does not inherit a retired line with the same ID',()=>{
 const rows=resolveVerifiedMetroLines('Каховская');assert.equal(rows.length,1);assert.equal(rows[0].number,'11');assert.equal(rows[0].lineRu,'Большая кольцевая');assert.equal(rows[0].officialColorName,'бирюзовый');assert.equal(rows[0].colorRGBVerified,false);
 const old=GEO_CATALOG.records.find(row=>row.id==='hh:11.44');assert.equal(old.membershipStatus,'historical-line-membership');assert.notEqual(old.serviceStatus,'closed','station is not closed because its former independent line ended');
 assert.equal(resolveVerifiedMetroLines('Севастопольская')[0].number,'9');
});
test('known interchange keeps explicit line branches and does not restore BKL from an old map',()=>{
 const rows=resolveVerifiedMetroLines('Деловой центр');assert.deepEqual(rows.map(row=>row.number).sort(),['17','4А','8А'].sort());assert(!rows.some(row=>row.number==='11'));
 const ra=resolveVerifiedMetroLines('Шелепиха');assert.equal(ra.length,1);assert.equal(ra[0].number,'17');assert.equal(ra[0].officialColorName,'графитовый');
});
test('a neighboring future station and the rest of the HH snapshot are not promoted',()=>{
 assert.deepEqual(resolveVerifiedMetroLines('Ильинская'),[]);assert.deepEqual(resolveVerifiedMetroLines('Неизвестная'),[]);
 assert.equal(GEO_CATALOG.records.length,20470,'the geographical snapshot is preserved');
 for(const row of resolveVerifiedMetroLines('Новомосковская')){assert.match(row.checkedAt,/^\d{4}-\d{2}-\d{2}$/);assert(row.sourceUrls.every(url=>url.startsWith('https://')));assert.equal(row.colorRGBVerified,false);}
 assert.deepEqual(resolveVerifiedMetroLines('Новомосковская').map(row=>row.number).sort(),['1','16']);
});
