import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

const resource=JSON.parse(await readFile(new URL('../data/ru-zh-open-lexicon.json',import.meta.url),'utf8'));

test('open RU-ZH resource keeps real pair counts and source attribution distinct from reviewed rules',()=>{
  assert.equal(resource.kind,'review-only-open-lexicon');assert.equal(resource.reviewed,false);
  assert.equal(resource.license,'CC-BY-SA-4.0');assert.match(resource.attribution,/Wiktionary contributors/);
  assert.deepEqual(resource.languagePair,['ru','zh-Hans']);
  assert.equal(resource.entries.length,resource.stats.entries);assert.equal(resource.stats.reviewedEntries,0);
  const pairs=new Set(),keys=new Set(),sources=new Set(resource.sources.map(row=>row.id));
  for(const [ru,zh,source,page] of resource.entries){
    assert.match(ru,/[а-яё]/i);assert.match(zh,/\p{Script=Han}/u);
    assert(sources.has(source));assert.equal(typeof page,'string');
    const pair=JSON.stringify([ru,zh]);assert(!pairs.has(pair));pairs.add(pair);
    keys.add(ru.toLowerCase().replaceAll('ё','е'));
  }
  assert.equal(keys.size,resource.stats.russianKeys);
  assert(resource.stats.entries>80000);assert(resource.stats.russianKeys<resource.stats.entries);
  for(const source of resource.sources){assert.match(source.sha256,/^[a-f0-9]{64}$/);assert.match(source.url,/^https:\/\/kaikki\.org\//);assert(source.bytes>0);}
});

test('attested inflections point to existing Russian lemmas and are not invented independent words',()=>{
  const normalize=value=>value.toLowerCase().replaceAll('ё','е');
  const lemmas=new Set(resource.entries.map(row=>normalize(row[0]))),links=new Set(),lookup=new Set(lemmas),forms=new Set();
  for(const [form,lemma] of resource.forms){
    assert.match(form,/[а-яё]/i);assert(lemmas.has(normalize(lemma)));
    const key=JSON.stringify([form,lemma]);assert(!links.has(key));links.add(key);
    forms.add(normalize(form));lookup.add(normalize(form));
  }
  assert.equal(links.size,resource.stats.attestedFormLinks);assert.equal(forms.size,resource.stats.attestedFormKeys);
  assert.equal(lookup.size,resource.stats.uniqueLookupKeys);
});
