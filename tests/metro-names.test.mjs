import test from 'node:test';
import assert from 'node:assert/strict';
import {resolveMetroName,translateMetroName,metroNameCoverage} from '../web/metro-names.mjs';
import {DICTIONARY} from '../web/engine.mjs';

test('available complete Chinese metro names precede component translations and do not add Russian by default',()=>{
  assert.equal(translateMetroName('Юго-Западная',{rules:{западная:'西'}}),'西南');
  assert.equal(translateMetroName('Юго-Западная',{includeRussian:true}),'西南（Юго-Западная）');
  const result=resolveMetroName('Юго-Западная');assert.equal(result.complete,true);assert.equal(result.source,'geo-catalog');assert.match(result.translationSource,/ranepa\.ru/);
});

test('station dictionary names and exact personal replacements retain priority',()=>{
  assert.equal(translateMetroName('Филатов Луг',{rules:DICTIONARY,dictionary:DICTIONARY}),'菲拉托夫卢格');
  assert.equal(translateMetroName('Юго-Западная',{rules:{...DICTIONARY,'юго-западная':'我的车站'},dictionary:DICTIONARY}),'我的车站');
  assert.equal(translateMetroName('Юго-Западная',{rules:{...DICTIONARY,'Юго-Западная':'CUSTOM'},dictionary:DICTIONARY}),'CUSTOM');
});

test('typographic hyphens, spaces, quotes and explicit metro prefixes resolve only whole station identity',()=>{
  for(const name of ['Юго‑Западная','Юго – Западная','м.Юго-Западная','метро «Юго-Западная»','🚇 Юго-Западная','«Юго-Западная»'])assert.equal(translateMetroName(name),'西南',name);
  assert.equal(translateMetroName('Филатов\u202fлуг',{dictionary:DICTIONARY}),'菲拉托夫卢格');
  assert.equal(translateMetroName('м.Филатов Лук'),'м.Филатов Лук');
});

test('ambiguous abbreviations and the Луг/Лук typo are not turned into new station aliases',()=>{
  for(const name of ['Юго','Луг','Лук','Филатов Лук','Филатовый Луг']){const result=resolveMetroName(name,{dictionary:{Луг:'草地',Лук:'洋葱',Юго:'南'}});assert.equal(result.label,name);assert.equal(result.complete,false);assert.equal(result.needsReview,true);assert.equal(result.knownStation,false);}
  const short=resolveMetroName('Юго');assert.equal(short.source,'ambiguous');assert(short.candidates.length>=2);assert(short.candidates.some(row=>row.ru==='Юго-Западная'));
});

test('known untranslated names get conservative whole-word partials with their entire Russian identity retained',()=>{
  const result=resolveMetroName('Ермакова Роща');assert.equal(result.label,'Ермакова 树林（Ермакова Роща）');assert.equal(result.source,'partial');assert.equal(result.complete,false);assert.equal(result.needsReview,true);assert.equal(result.knownStation,true);assert.deepEqual(result.parts.map(part=>part.ru),['Роща']);
  assert.equal(translateMetroName('Ермакова Роща',{partial:false}),'Ермакова Роща');
});

test('a fully literal component combination is marked as literal, not promoted to a canonical Chinese station',()=>{
  const result=resolveMetroName('Лесной Городок');assert.equal(result.label,'Лесной Городок（字面：森林小镇）');assert.equal(result.complete,false);assert(!result.label.includes('森林 小镇'));
  assert.equal(translateMetroName('Железнодорожная'),'Железнодорожная（字面：铁路）');
});

test('unknown proper names, larger words and Roman-number identities are preserved',()=>{
  for(const name of ['СверхНовокосино','Неизвестная Роща','Люберцы II','Новопридуманная','ЛеснойГородок'])assert.equal(translateMetroName(name),name);
  assert.equal(resolveMetroName('Люберцы I').knownStation,true);
});

test('same-object dictionary edits take effect without stale memoized translations',()=>{
  const dictionary={'Филатов Луг':'菲拉托夫卢格'},rules={...dictionary};
  assert.equal(translateMetroName('Филатов Луг',{rules,dictionary}),'菲拉托夫卢格');rules['Филатов Луг']='我的地铁';assert.equal(translateMetroName('Филатов Луг',{rules,dictionary}),'我的地铁');
  assert.equal(dictionary['Филатов Луг'],'菲拉托夫卢格');
});

test('already Chinese names remain Chinese and formatting in explicit replacements is not silently changed',()=>{
  assert.equal(translateMetroName('西南'),'西南');assert.equal(resolveMetroName('西南').canonicalRu,'Юго-Западная');
  const rules={'Юго-Западная':'西 南 / A'};assert.equal(translateMetroName('Юго-Западная',{rules}),'西 南 / A');
  assert.equal(translateMetroName(null),'');assert.equal(translateMetroName(false),'');
});

test('coverage counts names rather than line-interchange rows and does not imply every station is translated',()=>{
  const stats=metroNameCoverage({rules:DICTIONARY,dictionary:DICTIONARY});assert.equal(stats.names,359);assert(stats.full>=273);assert(stats.partial>0);assert(stats.untranslated>0);assert.equal(stats.full+stats.partial+stats.untranslated,stats.names);
});
