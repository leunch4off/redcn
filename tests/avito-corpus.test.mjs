import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {readSourceHtml} from '../web/source-import.mjs';
import {avitoFieldFacts,isAvitoNewCandidate} from '../web/source-adapters/avito.mjs';
import {analyzeListingSource} from '../web/engine.mjs';

// This corpus verifies our offline contract. Its manifest explicitly records
// synthetic provenance; it is not evidence that a live Avito page is reachable.
const manifest=JSON.parse(await readFile(new URL('fixtures/avito-corpus-manifest.json',import.meta.url),'utf8'));
const url='https://www.avito.ru/moskva/kvartiry/synthetic_fixture_1000000000';
const cases=new Map(await Promise.all(manifest.cases.map(async spec=>{
  const html=await readFile(new URL('fixtures/'+spec.file,import.meta.url),'utf8');
  const source=readSourceHtml(html,{url});
  return [spec.file,{...spec,html,source,...avitoFieldFacts(source)}];
})));
const fixture=name=>cases.get('avito-corpus-'+name+'.html');

test('Avito corpus declares derived provenance and has eight distinct ordinary, missing and closed cases',()=>{
  assert.equal(manifest.version,1);
  assert.match(manifest.provenance,/Synthetic/);
  assert.match(manifest.provenance,/not downloaded HTML snapshots/);
  assert.equal(cases.size,8);
  for(const item of cases.values()){
    assert.equal(item.source.platform,'avito',item.file);
    assert.doesNotMatch(item.html,/data:image|tel:|mailto:|<script|<img|PRIVATE_|SECRET_/i,item.file);
  }
});

test('Avito modern monthly contract keeps decimal areas, deposit and explicit percentage independent',()=>{
  const {facts:v,evidence}=fixture('monthly-modern');
  for(const [field,value] of Object.entries({price:'82500',pricePeriod:'month',priceCurrency:'RUB',deposit:'41250',commission:'25',seller:'owner',area:'58.5',living:'31.2',kitchen:'11.4',floor:'7',floors:'16',utilities:'meters'}))assert.equal(v[field],value,field);
  assert.equal(v.address,'Москва, Учебная улица, 1');
  assert(evidence.some(item=>item.field==='deposit'&&/41 250/.test(item.text)));
  assert(evidence.some(item=>item.field==='commission'&&/25/.test(item.text)));
});

test('Avito daily studio does not silently become monthly rent or a separate bedroom',()=>{
  const item=fixture('daily-studio'),v=item.facts;
  assert.equal(v.price,'3500');
  assert.equal(v.pricePeriod,'day');
  assert.equal(v.deposit,'5000');
  assert.equal(v.layout,'studio');
  assert.equal(v.commission,'0');
  assert.equal(v.seller,'agent');
  assert.notEqual(v.layout,'euro2');
  const analyzed=analyzeListingSource(item.source).facts;
  assert.equal(analyzed.pricePeriod,'day');
  assert.equal(analyzed.price,'3500');
});

test('Avito previous markup with id=item-price and semantic headings retains fields and amount fee',()=>{
  const {facts:v,evidence}=fixture('legacy-monthly');
  for(const [field,value] of Object.entries({price:'64000',pricePeriod:'month',deposit:'64000',commission:'',commissionAmount:'10000',seller:'agent',area:'36',living:'19',kitchen:'8'}))assert.equal(v[field],value,field);
  assert.match(v.sourceDescription,/Кухня отдельная/);
  assert(evidence.some(item=>item.field==='commissionAmount'&&/10 000/.test(item.text)));
});

test('Avito weekly room preserves its observed rate and never infers a fee from the deposit',()=>{
  const {facts:v}=fixture('room-weekly');
  assert.equal(v.price,'12000');
  assert.equal(v.pricePeriod,'week');
  assert.equal(v.deposit,'6000');
  assert.equal(v.commission,'');
  assert.equal(v.commissionStatus,'unknown');
  assert.equal(v.seller,'owner');
  assert.equal(v.area,'15');
});

test('Avito partial load keeps unavailable facts and rental period unknown',()=>{
  const item=fixture('partial'),v=item.facts;
  assert.equal(v.price,'71000');
  assert.equal(v.pricePeriod,'unknown');
  assert.equal(v.seller,'unknown');
  assert.equal(v.commission,'');
  for(const key of ['deposit','area','rooms','living','kitchen','floor','floors','sourceDescription','address'])assert.equal(v[key],undefined,key);
  assert.deepEqual(item.source.images,[]);
  assert.equal(item.source.publicationStatus,'unknown');
  assert.equal(isAvitoNewCandidate(item.source),false);
  assert(item.warnings.some(message=>/фото основного альбома/.test(message)));
});

test('Avito archived, removed and unavailable objects are excluded despite stale prices or recommendations',()=>{
  for(const name of ['archived','removed','unavailable']){
    const item=fixture(name);
    assert.equal(item.source.publicationStatus,item.status,name);
    assert.equal(item.facts.publicationStatus,item.status,name);
    assert.equal(isAvitoNewCandidate(item.source),false,name);
    assert.doesNotMatch(item.source.text,/999 999|Рекомендуемая квартира/);
  }
  assert.equal(fixture('unavailable').facts.price,undefined);
});

test('Avito complete ordinary cases remain eligible while explicit inactive states take precedence',()=>{
  for(const name of ['monthly-modern','daily-studio','legacy-monthly','room-weekly']){
    const item=fixture(name);
    assert.equal(item.source.publicationStatus,'active',name);
    assert.equal(isAvitoNewCandidate(item.source),true,name);
  }
  const item=fixture('monthly-modern');
  const closed=readSourceHtml(item.html.replace('</main>','<div data-marker="item-view/item-status">Объявление в архиве</div></main>'),{url});
  assert.equal(isAvitoNewCandidate(closed),false);
});

test('Avito corpus price facts survive repeated documents and randomized class names',()=>{
  for(const name of ['monthly-modern','daily-studio','legacy-monthly','room-weekly']){
    const item=fixture(name);
    const source=readSourceHtml((item.html+item.html).replaceAll('<section','<section class="unrelated-generated-css"'),{url});
    const result=avitoFieldFacts(source);
    for(const field of ['price','pricePeriod','deposit','commission','seller','area'])assert.equal(result.facts[field],item.facts[field],name+': '+field);
  }
});
