import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {readSourceHtml,sourcePlatform} from '../web/source-import.mjs';
import {analyzeListingSource,generateListing} from '../web/engine.mjs';
const html=await readFile(new URL('fixtures/avito-52.html',import.meta.url),'utf8');
const url='https://www.avito.ru/moskva/kvartiry/2-k._kvartira_52_m_1417_et._7462093377';
const source=readSourceHtml(html,{url:url+'?context=SECRET_CONTEXT'}),result=analyzeListingSource(source),v=result.facts;
test('Avito: four repeated item-params groups retain independent apartment/rent/rules/building facts',()=>{
 for(const [field,value] of Object.entries({price:'75000',area:'52',kitchen:'9.7',living:'30.6',floor:'14',floors:'17',rooms:'2',deposit:'75000',commission:'0',occupants:'2',buildingYear:'2002',buildingType:'панельный',bathroom:'раздельный',passengerLifts:'1',freightLifts:'1',utilities:'meters',latitude:'55.643119',longitude:'37.410196'}))assert.equal(v[field],value,field);
 assert.equal(v.separate,true);assert.equal(v.address,'Москва, Волынская ул., 12к1');assert.equal(v.city,'Москва');
 assert.deepEqual(new Set(source.sections.map(s=>s.section)),new Set(['apartment','rental','rules','building','description','location','highlights']));
 assert(source.records.some(r=>r.name==='Highlights'));assert.equal(source.platform,'avito');
});
test('Avito: explicit no stays no and never becomes children or pets permission',()=>{
 assert.equal(v.petPolicy,'no');assert.equal(v.childrenPolicy,'no');assert.equal(v.smokingPolicy,'no');assert.equal(v.pets,false);assert.equal(v.children,false);
 const generated=generateListing({...v,registration:'assist'});assert.doesNotMatch(generated.body,/可带孩子|宠物入住可|可养宠/);
 const yes=analyzeListingSource(html.replace('Можно с детьми: нет','Можно с детьми: да').replace('Можно с питомцем: нет','Можно с питомцем: да')).facts;
 assert.equal(yes.pets,true);assert.equal(yes.children,true);
 const conflict=analyzeListingSource(html.replace('на длительный срок от собственника.','на длительный срок от собственника. Можно с животными.')).facts;
 assert.equal(conflict.petPolicy,'conflict');assert.equal(conflict.pets,false);
});
test('Avito: only named appliances are present; storage and sleeping places do not invent a wardrobe or a bed',()=>{
 for(const id of ['fridge','stove','microwave','washer','tv','internet','kitchenFurniture'])assert(v.features.includes(id),id);
 for(const id of ['ac','dishwasher','bed','bedAny','sofa','piano','wardrobe','oven'])assert(!v.features.includes(id),id);
 assert(v.sourceAudit.fields.some(e=>e.field==='equipmentText'&&e.source.includes('Авито')));
});
test('Avito: metro ranges and explicit walking icons remain separate routes',()=>{
 assert.equal(v.metro,'Солнцево');assert.equal(v.walk,'16–20');assert.equal(v.transit.split('\n').length,3);assert.match(v.transit,/Говорово \| 21–30 \| walk/);
 const noIcon=analyzeListingSource(html.replace(/<svg[\s\S]*?<\/svg>/g,'')).facts;assert.match(noIcon.transit,/unknown/);assert.equal(noIcon.walk,'');
});
test('Avito: gallery ownership, duplicate markup, generated classes and private source isolation',()=>{
 assert.equal(source.images.length,12);assert(!JSON.stringify(source).includes('SECRET_CONTEXT'));assert(!JSON.stringify(source).includes('PRIVATE_SELLER_PHONE'));assert(!JSON.stringify(source).includes('PAGE_SCRIPT_MUST_NOT_RUN'));assert(!source.images.some(s=>/wrong/.test(s)));
 const duplicate=analyzeListingSource(html+html);assert.equal(duplicate.facts.photos.length,12);assert.equal(duplicate.facts.transit,v.transit);assert.equal(duplicate.facts.price,'75000');
 const changed=analyzeListingSource(html.replaceAll('bx_item-params','changed-generated-id'));assert.equal(changed.facts.living,'30.6');
});
test('Avito: empty primary gallery stays empty and reports incomplete photos',()=>{
 const empty=readSourceHtml(html.replace(/<img\b[^>]*>/g,''));assert.deepEqual(empty.images,[]);assert(analyzeListingSource(empty).warnings.some(w=>/фото основного альбома/.test(w)));
});
test('dispatch uses the supplied host, without mixing CIAN selectors into Avito',async()=>{
 assert.equal(sourcePlatform(html),'avito');assert.equal(sourcePlatform(html,'https://www.cian.ru/rent/flat/1/'),'cian');
 const cian=await readFile(new URL('fixtures/cian-54.html',import.meta.url),'utf8');assert.equal(readSourceHtml(cian).platform,'cian');assert.equal(analyzeListingSource(cian).facts.price,'70000');
});
test('Avito: amounts and excluded utility payments do not become a percentage or included fees',()=>{
 const changed=analyzeListingSource(html.replace('Комиссия: 0 %','Комиссия: 10000 ₽').replace('Другие ЖКУ: включено в платёж','Другие ЖКУ: не включено')).facts;
 assert.equal(changed.commission,'');assert.equal(changed.commissionAmount,'10000');assert.equal(changed.utilities,'extra');
});

test('Avito: unclear or negotiable panel values are not permission',()=>{
 const uncertain=analyzeListingSource(html.replace('Можно с детьми: нет','Можно с детьми: уточните').replace('Можно с питомцем: нет','Можно с животными: по согласованию')).facts;
 assert.equal(uncertain.pets,false);assert.equal(uncertain.children,false);assert.equal(uncertain.petPolicy,'negotiable');
 assert.doesNotMatch(generateListing({...uncertain,registration:'assist'}).body,/可带孩子|可带宠物/);
});
