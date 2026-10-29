import test from 'node:test';
import assert from 'node:assert/strict';
import {TAG_CATALOG,phraseEntries,tokenize,replaceSpan,listingEligible} from '../web/content-tools.mjs';
import {DICTIONARY,generateListing} from '../web/engine.mjs';
test('user tag catalogue is bilingual and does not duplicate tags',()=>{assert(TAG_CATALOG.length>=54);assert.equal(new Set(TAG_CATALOG.map(x=>x[0])).size,TAG_CATALOG.length);assert(TAG_CATALOG.every(x=>x[1]&&x[2]));});
test('relevant university, campus and layout tags only',()=>{
 const result=generateListing({layout:'euro2',rooms:2,universities:'МГУ | 30 | metro',campus:'主校区',features:[]});
 assert.match(result.tags,/#莫大租房/);assert.match(result.tags,/#主校区/);assert.match(result.tags,/#莫斯科一室一厅/);assert.doesNotMatch(result.tags,/#莫斯科两居室|莫斯科友大租房|落地签|FIT/);
 assert(result.tagSuggestions.every(x=>x.reason&&x.ru));
});
test('tag and title rotation never changes property facts',()=>{
 const v={rooms:1,price:'68000',area:34,transit:'Саларьево | 11 | walk',features:['bed','ac']};
 const a=generateListing(v),b=generateListing({...v,titleVariant:1});assert.notEqual(a.titles[0],b.titles[0]);assert.notEqual(a.tags,b.tags);assert.equal(a.body,b.body);assert.doesNotMatch(a.tags,/#近地铁/);
});
test('ready, renovation and proximity tags require explicit facts',()=>{const a=generateListing({metro:'Саларьево',walk:8,ready:true,newRenovation:true,features:[]});assert.match(a.tags,/#近地铁/);assert.match(a.tags,/#拎包入住/);assert.match(a.tags,/#全新装修/);const b=generateListing({features:[]});assert.doesNotMatch(b.tags,/#近地铁|#全新装修|#拎包入住/);});
test('tokenization preserves all text and longest known phrase',()=>{
 const entries=phraseEntries(DICTIONARY,[]),text='🔥莫斯科国立大学｜月租6.8W\n冰箱＋冰箱';const parts=tokenize(text,entries);assert.equal(parts.map(x=>x.text).join(''),text);assert(parts.some(x=>x.text==='莫斯科国立大学'&&x.entry.ru));for(const part of parts)assert.equal(text.slice(part.start,part.end),part.text);
});
test('selection replacement is exact and stale selection rejected',()=>{assert.equal(replaceSpan('冰箱＋冰箱',3,5,'冰箱','冷藏箱'),'冰箱＋冷藏箱');assert.throws(()=>replaceSpan('洗衣机',0,2,'冰箱','冷藏箱'),/Текст изменился/);});
test('custom CN/RU dictionary overrides built-in meaning',()=>{const entries=phraseEntries({},[{zh:'冰箱',ru:'Моя формулировка',alternatives:['冷藏箱']}]);assert.equal(entries.find(x=>x.zh==='冰箱').ru,'Моя формулировка');});
test('website eligibility matches strict commission boundary',()=>{assert(listingEligible('owner',''));assert(listingEligible('agent','50'));assert(!listingEligible('agent',''));assert(!listingEligible('agent','50.01'));assert(!listingEligible('unknown','0'));});
