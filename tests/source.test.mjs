import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {readSourceHtml} from '../web/source-import.mjs';
import {analyzeListingSource,extractFacts,generateListing} from '../web/engine.mjs';
import {historyEntry,historyHtml,historyFingerprint} from '../web/history.mjs';
const html=await readFile(new URL('fixtures/cian-54.html',import.meta.url),'utf8');
const result=analyzeListingSource(html),v=result.facts;
test('complete listing facts use literal data, stable data-name and ids',()=>{
 for(const [key,value] of Object.entries({area:'54',living:'44',kitchen:'7',floor:'3',floors:'3',price:'70000',deposit:'140000',commission:'0',prepayMonths:'1',buildingYear:'2006',entrances:'5',balconies:'0',loggias:'0',occupants:'1',seller:'owner',layout:'euro2',utilities:'meters'}))assert.equal(v[key],value,key);
 assert.equal(v.renovation,'Евроремонт');assert.equal(v.windows,'На улицу и двор');assert.equal(v.buildingType,'Монолитный');
 assert.equal(v.sourceAudit.fields.find(e=>e.field==='price').source,'JSON bargainTerms.price');
});
test('generated CSS classes and repeated documents do not change the property',()=>{
 const other=analyzeListingSource(html.replaceAll('different-generated-css','x42-random-new-class'));
 assert.deepEqual(other.facts.features,v.features);assert.equal(other.facts.price,v.price);
 const twice=analyzeListingSource(html+html);assert.equal(twice.facts.transit,v.transit);assert.equal(twice.facts.featureCounts.slidingWardrobe,'2');assert.equal(twice.facts.photos.length,6);
});
test('no account secrets, phones, tracking or arbitrary code from JSON are returned',()=>{
 const modified=html.replace('"cianId":300691182','"phones":["SECRET_PHONE"],"userId":"SECRET_USER","cianId":300691182');
 const source=readSourceHtml(modified+'<script>throw new Error("should not execute")</script>');
 assert(!JSON.stringify(source).includes('SECRET_PHONE'));assert(!JSON.stringify(source).includes('SECRET_USER'));assert(!JSON.stringify(source).includes('SHOULD_NOT_LEAVE_PARSER'));
 assert.equal(source.offer.cianId,300691182);
});
test('furniture exceptions, TV stand, generic bed and counts stay faithful',()=>{
 for(const id of ['bedAny','slidingWardrobe','nightstands','sofa','armchair','tvStand','dishwasher','internet','bath','kitchenFurniture'])assert(v.features.includes(id),id);
 for(const id of ['bed','tv','fridge','table','chairs','washer','ac'])assert(!v.features.includes(id),id);
 assert.equal(v.featureCounts.slidingWardrobe,'2');
 for(const id of ['fridge','table','chairs','tv','washer'])assert.equal(v.sourceAudit.furniture.find(r=>r.id===id).status,'absent',id);
});
test('mixed Cyrillic/Latin restrictions and conflicting contract lengths are preserved',()=>{
 assert.equal(v.registrationPolicy,'not_available');assert.equal(v.registration,'none');assert.equal(v.petPolicy,'no');assert.equal(v.smokingPolicy,'no');assert.equal(v.guestPolicy,'no');assert.equal(v.subletPolicy,'no');assert.equal(v.shortTermPolicy,'no');
 assert.equal(v.contractMonths,'11');assert.equal(v.minMonths,'12');assert(result.warnings.some(w=>/11 месяцев.*12 месяцев/.test(w)));
});
test('same station on D1 and metro retains distinct times and transport identity',()=>{
 assert.equal(v.transit.split('\n').length,3);assert.match(v.transit,/Лианозово \| 18 \| walk \| D1/);assert.match(v.transit,/Лианозово \| 22 \| walk/);assert.match(v.transit,/Алтуфьево \| 23 \| walk/);
 assert(!v.universities);assert(!v.campus);
});
test('DOM fallback extracts all numeric blocks but does not guess walking from an unlabeled icon',()=>{
 const fallback=analyzeListingSource(html.replace(/<script>[\s\S]*?<\/script>/g,''));
 assert.equal(fallback.facts.area,'54');assert.equal(fallback.facts.floor,'3');assert.equal(fallback.facts.deposit,'140000');assert.equal(fallback.facts.price,'70000');assert.equal(fallback.facts.buildingYear,'2006');
 const route=analyzeListingSource('<li data-name="UndergroundItem"><a>Тестовая</a><span>9 мин.</span></li>');assert.match(route.facts.transit,/unknown/);
});
test('explicit negative source never becomes a free registration or ready furnished promise',()=>{
 const r=generateListing({...v,style:'story',registration:'provided',registrationMonths:'6',support:true});
 assert.doesNotMatch(r.body,/免费提供临时居住登记|免费协助办理临时居住登记|家具家电齐全|合租预算|双人床|✅ 冰箱|✅ 洗衣机/);
 assert.match(r.body,/不提供临时居住登记/);assert.match(r.body,/电视柜/);assert.match(r.body,/140,000₽/);assert.doesNotMatch(r.tags,/莫大|友大|近地铁/);
});
test('future installation and conflicts are shown and excluded from checked furniture',()=>{
 const plain=extractFacts('Хозяин может установить стиральную машину. Нет холодильника, стола и стульев. Есть диван. ТВ-тумба. Кровать.');
 assert(!plain.facts.features.includes('washer'));assert(!plain.facts.features.includes('fridge'));assert(!plain.facts.features.includes('table'));assert(!plain.facts.features.includes('chairs'));assert(!plain.facts.features.includes('tv'));assert(plain.facts.features.includes('sofa'));
 const source=readSourceHtml(html);source.offer.hasFridge=true;
 assert.equal(analyzeListingSource(source).sourceAudit.furniture.find(r=>r.id==='fridge').status,'conflict');
});
test('a hundred reproducible story variants preserve prices, restrictions and missing furniture',()=>{
 const variants=[];for(let n=1;n<=100;n++){const r=generateListing({...v,style:'story',copyVariant:n,titleVariant:n});variants.push(r.body);assert.match(r.body,/70,000₽/);assert.match(r.body,/140,000₽/);assert.match(r.body,/54㎡/);assert.match(r.body,/不接受宠物入住/);assert.doesNotMatch(r.body,/免费提供临时居住登记|✅ 洗衣机|双人床/);}
 assert.equal(new Set(variants).size,100);assert.deepEqual(generateListing({...v,copyVariant:41}),generateListing({...v,copyVariant:41}));
});
test('history freezes exact edited content and HTML escapes source and title',()=>{
 const value={title:'<script>alert(1)</script>',body:'我的正文',tags:'#莫斯科',fields:{price:'70000',source:'<img src=x onerror=alert(1)>'}};
 const entry=historyEntry('copy-all',value,'2026-10-30T00:00:00Z'),archive=historyHtml(entry);
 assert.equal(entry.body,value.body);assert.equal(entry.fingerprint,historyFingerprint(value));assert(!archive.includes('<script>'));assert(!archive.includes('<img src=x'));assert(archive.includes('&lt;script&gt;'));assert(archive.includes('70000'));assert.throws(()=>historyEntry('fake',value));value.fields.price='1';assert.equal(entry.fields.price,'70000');
});
