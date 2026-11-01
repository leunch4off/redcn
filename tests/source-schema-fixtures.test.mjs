import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {readSourceHtml} from '../web/source-import.mjs';
import {analyzeListingSource} from '../web/engine.mjs';

for(const platform of ['cian','avito'])test('user-authorized '+platform+' observed DOM derivative keeps extraction contracts with wholly fictional fields',()=>{
 const name='fixtures/'+platform+'-user-observed-v26.html',html=readFileSync(new URL(name,import.meta.url),'utf8'),manifest=JSON.parse(readFileSync(new URL(name.replace('.html','.manifest.json'),import.meta.url),'utf8'));
 const url=platform==='cian'?'https://www.cian.ru/rent/flat/999000001/':'https://www.avito.ru/moskva/kvartiry/fictional_999000001',source=readSourceHtml(html,{url}),result=analyzeListingSource(source);
 assert.equal(source.platform,platform);assert.equal(manifest.valuesAreFictional,true);assert.equal(manifest.permission.startsWith('User supplied'),true);
 for(const [field,value]of Object.entries({price:'80000',area:'54',rooms:'2',floor:'5',floors:'17'}))assert.equal(result.facts[field],value,field);
 assert.ok(result.facts.features.includes('wardrobe'));assert.ok(result.facts.features.includes('fridge'));assert.ok(manifest.observedSections.length>=5);
 assert.doesNotMatch(html,/<script|onclick=|src="https:\/\/(?!fixture\.invalid)/i);
});
