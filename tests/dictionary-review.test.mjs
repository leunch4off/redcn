import test from 'node:test';
import assert from 'node:assert/strict';
import {PRONUNCIATION_TERMS,dictionaryPairConflict,searchPronunciation,pronounceKnownTerm} from '../web/dictionary-review.mjs';

test('approved dictionary adoption detects case and whitespace conflicts without overwriting personal rules',()=>{
 const original={'  рабочий   стол ':'旧书桌','МГУ':'莫斯科国立大学'};
 assert.deepEqual(dictionaryPairConflict(original,'Рабочий стол','书桌'),{before:'旧书桌',after:'书桌',conflict:true,existingKey:'  рабочий   стол '});
 assert.equal(dictionaryPairConflict(original,'МГУ','莫斯科国立大学').conflict,false);
 assert.deepEqual(dictionaryPairConflict(original,'шкаф','衣柜'),{before:null,after:'衣柜',conflict:false,existingKey:null});
 assert.equal(original['  рабочий   стол '],'旧书桌');
});
test('pronunciation catalogue contains only ten individually sourced Mandarin terms, with explicit Traditional originals',()=>{
 assert.equal(PRONUNCIATION_TERMS.length,10);assert.equal(new Set(PRONUNCIATION_TERMS.map(row=>row.zh)).size,10);
 for(const row of PRONUNCIATION_TERMS){assert.match(row.sourceUrl,/^https:\/\/dict\.concised\.moe\.edu\.tw\/dictView\.jsp\?ID=\d+&la=0&powerMode=0$/);assert.equal(row.checkedAt,'2026-11-01');assert(row.sourceWord&&row.ru&&row.pinyin);assert.match(row.variety,/MOE Taiwan/);}
 assert.equal(searchPronunciation('кухня')[0].pinyin,'chú fáng');assert.equal(searchPronunciation('臥室')[0].zh,'卧室');
 assert.equal(searchPronunciation('bīng xiāng')[0].zh,'冰箱');assert.deepEqual(searchPronunciation('неизвестное слово'),[]);
});
test('pronunciation does not silently use an English voice or invent a reading for an unknown phrase',()=>{
 assert.equal(pronounceKnownTerm('未知词').started,false);assert.equal(pronounceKnownTerm('地铁',{}).started,false);
 let calls=0;class Utterance{constructor(text){this.text=text;}}
 const voice={lang:'zh-CN',name:'Chinese voice'},environment={SpeechSynthesisUtterance:Utterance,speechSynthesis:{getVoices:()=>[{lang:'en-US'},voice],cancel:()=>{},speak:item=>{calls++;assert.equal(item.text,'地铁');assert.equal(item.voice,voice);assert.equal(item.rate,.85);}}};
 assert.equal(calls,0);assert.equal(pronounceKnownTerm('地铁',environment).started,true);assert.equal(calls,1);
 environment.speechSynthesis.getVoices=()=>[{lang:'en-US'}];assert.equal(pronounceKnownTerm('地铁',environment).started,false);assert.equal(calls,1);
});
