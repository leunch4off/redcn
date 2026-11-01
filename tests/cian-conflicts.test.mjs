import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {readSourceHtml} from '../web/source-import.mjs';
import {analyzeListingSource,generateListing} from '../web/engine.mjs';
import {cianFieldFacts} from '../web/source-adapters/cian.mjs';
const html=await readFile(new URL('fixtures/cian-conflicts-52.html',import.meta.url),'utf8');
test('unknown numeric source values stay unknown while zero counts and decimal areas survive',()=>{
 const {facts}=cianFieldFacts({offer:{totalArea:'52,5',livingArea:null,kitchenArea:'',roomsCount:undefined,floorNumber:false,balconiesCount:0,loggiasCount:'0',combinedWcsCount:'invalid'}});
 assert.equal(facts.area,'52.5');assert.equal(facts.balconies,'0');assert.equal(facts.loggias,'0');
 for(const key of ['living','kitchen','rooms','floor','combinedWcs'])assert.equal(facts[key],undefined,key);
});
test('null deposit never promises no deposit; explicit zero does',()=>{
 const base={price:'65000',rooms:'2',area:'52',features:[],style:'balanced'};
 assert(!generateListing({...base,deposit:null}).body.includes('无需押金'));
 assert(generateListing({...base,deposit:0}).body.includes('无需押金'));
});
test('source separates ten room photos from plan and retains only observed variant URLs',()=>{
 const source=readSourceHtml(html);assert.equal(source.images.length,10);assert.equal(source.photoItems.length,10);assert.equal(source.layouts.length,1);assert(source.layouts[0].isLayout&&source.layouts[0].isCianLayout);assert.equal(source.photoItems[0].variants.thumbnailUrl,'https://images.cdn-cian.ru/images/fixture-room-1-small.jpg');assert(!source.images.some(url=>/plan|wrong/.test(url)));
});
test('deposit percentage conflict and house material conflict cannot become agreed facts',()=>{
 const {facts}=analyzeListingSource(html);assert.equal(facts.price,'65000');assert.equal(facts.deposit,'');assert.equal(facts.buildingType,'');
 assert(facts.sourceAudit.conflicts.some(c=>c.field==='deposit'&&c.candidates.some(v=>v.value==='32500')));assert(facts.sourceAudit.conflicts.some(c=>c.field==='buildingType'));
});
test('not a bathtub but a shower conflicts with opposite boolean witnesses; neither is advertised as agreed',()=>{
 const {facts}=analyzeListingSource(html);for(const id of ['bath','shower']){assert(!facts.features.includes(id));assert.equal(facts.sourceAudit.furniture.find(r=>r.id===id).status,'conflict');}
});
test('transport identity and source dates keep source meanings and car mode',()=>{
 const {facts}=analyzeListingSource(html);assert.equal(facts.transitDetails.filter(r=>r.name==='Лианозово').length,2);assert.notEqual(facts.transitDetails[1].id,facts.transitDetails[2].id);assert.equal(facts.sourceDates.published,1619455532);assert.match(facts.sourceDates.created,/^2021/);assert.match(facts.sourceDates.updated,/^2026/);
 const car=analyzeListingSource(html.replace('"travelType":"walk"','"travelType":"byCar"')).facts;assert.match(car.transit.split('\n')[0],/car/);assert.notEqual(car.walk,'11');
});
test('CLI discards stale flattened furniture warnings once structured audit resolves that item',async()=>{
 const {spawnSync}=await import('node:child_process');
 const data={price:'65000',rooms:'2',area:'52',features:['fridge'],sourceAudit:{furniture:[{id:'fridge',label:'Холодильник',status:'present'}],warnings:[]}};
 const text='Квартира 52 м², цена 65000 рублей. Нет холодильника. Есть холодильник.';
 const run=spawnSync(process.execPath,['cli/generate.mjs'],{input:JSON.stringify({text,data}),encoding:'utf8'});assert.equal(run.status,0,run.stderr);assert(!JSON.parse(run.stdout).warnings.some(w=>/Холодильник.*conflict/.test(w)));
});
