import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {CITY_POIS,cityPoiStats,searchCityPois,cityPoiMapLinks} from '../web/city-pois.mjs';

test('five city places exactly match attributed source dataset and retain all categories',async()=>{
 assert.deepEqual(CITY_POIS,JSON.parse(await readFile(new URL('../data/city-pois.json',import.meta.url),'utf8')));
 assert.equal(CITY_POIS.coverage.exhaustive,false);assert.deepEqual(cityPoiStats().categories,{'chinese-grocery':2,'chinese-restaurant':1,park:2});
 const sources=new Map(CITY_POIS.sources.map(row=>[row.id,row]));for(const row of CITY_POIS.places){assert.equal(row.translationStatus,'editorial-category');assert.equal(row.coordinateEvidence.accuracyMeters,null);assert.equal(row.openingStatus,'unknown');assert.equal(row.accessibility,'unknown');assert(row.sourceIds.every(id=>sources.has(id)));assert(row.coordinateEvidence.sourceIds.every(id=>row.sourceIds.includes(id)&&sources.get(id).verifiedAt===row.coordinateEvidence.verifiedAt));}
});
test('published shop markers and restaurant destination never use map viewport centers',()=>{
 const byId=new Map(CITY_POIS.places.map(row=>[row.id,row]));
 for(const [id,lat,lon]of [['korshop-frunzenskaya',55.73100901619647,37.59333107571903],['korshop-belyaevo',55.645903,37.519566],['chinagramota-sretenka',55.766661,37.630577]])assert.deepEqual([byId.get(id).latitude,byId.get(id).longitude],[lat,lon]);
 assert.notEqual(byId.get('korshop-belyaevo').latitude,55.644477080659314);assert.notEqual(byId.get('korshop-frunzenskaya').longitude,37.59484850533909);
 for(const row of CITY_POIS.places.filter(row=>row.category==='park'))assert.match(row.coordinateEvidence.scope,/^historical-park-/);
});
test('Russian Chinese aliases address and category search are detached and bounded',()=>{
 assert.equal(searchCityPois('Миклухо').entries[0].id,'korshop-belyaevo');assert.equal(searchCityPois('粤菜').entries[0].id,'chinagramota-sretenka');assert.equal(searchCityPois('САВЁЛОВСКИЙ').total,1);assert.equal(searchCityPois('',{category:'park'}).total,2);
 assert.equal(searchCityPois('',{limit:1,offset:1}).entries[0].id,'korshop-belyaevo');const rows=searchCityPois('').entries;rows[0].aliases.length=0;assert(CITY_POIS.places[0].aliases.length);assert.throws(()=>searchCityPois('x'.repeat(201)));assert.throws(()=>searchCityPois('',{category:'invented'}));
});
test('map buttons only show public reference points without origin contact or client address',()=>{
 const row={...CITY_POIS.places[0],address:'PRIVATE CLIENT ADDRESS',phone:'+79991234567',origin:{latitude:1,longitude:2}},links=cityPoiMapLinks(row);
 assert.equal(new URL(links.yandex).searchParams.get('pt'),row.longitude+','+row.latitude);assert.equal(new URL(links.google).searchParams.get('query'),row.latitude+','+row.longitude);
 for(const value of Object.values(links)){assert(!value.includes('PRIVATE'));assert(!value.includes('7999'));assert(!new URL(value).searchParams.has('origin'));assert(!new URL(value).searchParams.has('rtext'));}
 assert.throws(()=>cityPoiMapLinks({latitude:true,longitude:37}));
});
