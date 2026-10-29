import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {spawnSync} from 'node:child_process';
import {photoSourceUrl,photoSources,cropRectangle,photoZip} from '../web/media-core.mjs';
import {listingSourceUrl,readRelatedHtml,relatedRank} from '../web/related.mjs';
import {analyzeListingSource} from '../web/engine.mjs';

test('photo source normalization never turns a relative image into a loopback link',()=>{
 const url='https://images.cdn-cian.ru/images/123.jpg';assert.equal(photoSourceUrl({sourceUrl:url}),url);assert.equal(photoSourceUrl('//images.cdn-cian.ru/images/123.jpg'),url);
 for(const bad of ['images/1.jpg','http://127.0.0.1:8765/photo.jpg','https://cian.ru.evil.test/x','https://user:pass@cian.ru/a.jpg','data:image/svg+xml,hi','https://cian.ru:444/a'])assert.equal(photoSourceUrl(bad),null);
 assert.deepEqual(photoSources([url,url,{url}]),[url]);
});
test('crop percentages scale to portrait, landscape and square without stretching',()=>{
 for(const [w,h] of [[1280,978],[720,960],[1000,1000],[4032,3024]]){const r=cropRectangle(w,h,{bottom:12});assert.equal(r.width,w);assert.equal(r.height,Math.ceil(h*.88));assert.equal(r.y,0);}
 for(const margins of [{bottom:41},{top:-1},{left:NaN},{left:40,right:40},{top:40,bottom:40}])assert.throws(()=>cropRectangle(1000,1000,margins));
 assert.throws(()=>cropRectangle(10000,10000));
});
test('ZIP can be read by a standard reader, preserves binary and Chinese post',async()=>{
 const zip=await photoZip([{name:'01-original.webp',blob:new Blob([new Uint8Array([0,1,255,4])])},{name:'post.txt',blob:new Blob(['莫斯科租房'])}]);
 const r=spawnSync('python',['-c',"import sys,io,zipfile;z=zipfile.ZipFile(io.BytesIO(sys.stdin.buffer.read()));assert z.read('01-original.webp')==bytes([0,1,255,4]);assert z.read('post.txt').decode()=='莫斯科租房';assert z.testzip() is None"],{input:Buffer.from(await zip.arrayBuffer())});assert.equal(r.status,0,r.stderr.toString());
 await assert.rejects(()=>photoZip([{name:'../a',blob:new Blob(['x'])}]));
});
const recs='<section data-name="SimilarOffers"><article data-name="OfferCard"><a href="/rent/flat/900001/">1-комн. квартира, 39 м²</a><div data-name="PriceInfo">80 000 ₽</div><span>Лианозово 10 мин.</span><div data-name="FeaturesItem">Телевизор</div></article><article data-name="OfferCard"><a href="https://www.cian.ru/rent/flat/900002/">Студия 25 м²</a><span>Прокшино 12 мин.</span></article></section>';
test('recommendation URLs are deduplicated, current listing and non-listing links excluded',()=>{
 const rows=readRelatedHtml(recs+recs+'<a href="javascript:alert(1)">bad</a><a href="/rent/flat/300691182/">current</a>','https://www.cian.ru/rent/flat/300691182/');assert.equal(rows.length,2);assert.equal(rows[0].price,80000);assert.equal(rows[0].area,39);assert.equal(rows[0].origin,'source-recommendation');assert.equal(listingSourceUrl('http://127.0.0.1/x'),null);
});
test('recommendation prices and furniture never leak into the primary listing',async()=>{
 const html=await readFile(new URL('fixtures/cian-54.html',import.meta.url),'utf8'),result=analyzeListingSource(html+recs).facts;
 assert.equal(result.price,'70000');assert.equal(result.area,'54');assert.ok(!result.features.includes('tv'));assert.equal(result.relatedOffers.length,2);
});
test('same station ranking uses whole names and keeps unverified candidates labeled',()=>{
 const current={url:'https://www.cian.ru/rent/flat/300691182/',metro:'Лианозово'},rows=relatedRank(current,readRelatedHtml(recs,'https://www.cian.ru/'),[{id:'a',url:'https://www.cian.ru/rent/flat/12/',metro:'Лианозово'},{id:'b',url:'https://www.cian.ru/rent/flat/13/',metro:'Новое Лианозово'}]);
 assert.equal(rows.length,3);assert.ok(rows[0].sameMetro.includes('лианозово'));assert.equal(rows.find(r=>r.url.endsWith('/900002/')).sameMetro.length,0);assert.ok(!rows.some(r=>r.libraryId==='b'));
});
test('all media and recommendation control ids are present exactly once',async()=>{
 const html=await readFile(new URL('../web/index.html',import.meta.url),'utf8'),ids=[...html.matchAll(/\bid="([^"]+)"/g)].map(m=>m[1]);
 for(const file of ['photo-ui.mjs','related-ui.mjs']){const js=await readFile(new URL('../web/'+file,import.meta.url),'utf8');for(const m of js.matchAll(/\b(?:pe|re)\('([^']+)'\)/g))assert.equal(ids.filter(id=>id===m[1]).length,1,m[1]);}
});
