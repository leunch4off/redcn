import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile,readdir} from 'node:fs/promises';
import {spawnSync} from 'node:child_process';
import vm from 'node:vm';
import {hasOwnKey} from '../web/own-property.mjs';

const strip=source=>source.replace(/^import .*?;\n/gm,'').replace(/^export /gm,'');

test('own-property checks keep inherited, shadowed and null-prototype records distinct',()=>{
 const record=Object.assign(Object.create({inherited:true}),{present:undefined,hasOwnProperty:false});
 assert.equal(hasOwnKey(record,'present'),true);
 assert.equal(hasOwnKey(record,'missing'),false);
 assert.equal(hasOwnKey(record,'inherited'),false);
 assert.equal(hasOwnKey(record,'hasOwnProperty'),true);
 const empty=Object.create(null);empty.__proto__='stored value';
 assert.equal(hasOwnKey(empty,'__proto__'),true);
 assert.equal(hasOwnKey(empty,'constructor'),false);
 const symbol=Symbol('own');empty[symbol]=0;assert.equal(hasOwnKey(empty,symbol),true);
 assert.equal(hasOwnKey('photo',0),true);
 assert.equal(hasOwnKey(7,'toString'),false);
 assert.throws(()=>hasOwnKey(null,'x'),TypeError);
 assert.throws(()=>hasOwnKey(undefined,'x'),TypeError);
});

test('source-schema warnings and album roles work while Object.hasOwn stays unavailable',async()=>{
 const [helper,readiness]=await Promise.all(['web/own-property.mjs','web/source-readiness.mjs'].map(path=>readFile(new URL('../'+path,import.meta.url),'utf8')));
 const sandbox={URL};
 vm.runInNewContext('Object.hasOwn=undefined;\n'+strip(helper)+'\n'+strip(readiness)+'\n'+
   'globalThis.feedback=sourceReadinessFeedback;globalThis.metadata=sourceEditorMetadata;globalThis.nativeMissing=()=>typeof Object.hasOwn===\'undefined\';',sandbox);
 const value={sourceSchemaHealth:{version:1,state:'partial',platform:'cian',
   reasons:['unknown-schema','field-conflict','toString','constructor','__proto__','PRIVATE_REASON'],
   missingCritical:['price','PRIVATE_FIELD'],criticalCoverage:{price:false,area:true,layout:true,description:true},
   privateReviewFragment:'PRIVATE_CAPTURE'}};
 const feedback=sandbox.feedback(value);
 assert.equal(feedback.partial,true);assert.match(feedback.message,/Форма страницы источника изменилась/);
 assert.match(feedback.message,/противоречивые сведения/);
 const saved=sandbox.metadata(value);
 assert.deepEqual(JSON.parse(JSON.stringify(saved.sourceSchemaHealth.reasons)),['unknown-schema','field-conflict']);
 assert.doesNotMatch(JSON.stringify({feedback,saved}),/PRIVATE|toString|constructor|__proto__|function/);
 const album=sandbox.metadata({sourceReadiness:{state:'ready',partial:false,completed:true,sections:[],
   gallery:{version:1,observed:2,expected:2,previewOnly:1,mainLinksObserved:1}}});
 assert.equal(album.sourceReadiness.gallery.state,'partial');
 assert.equal(album.sourceReadiness.gallery.previewOnly,1);
 assert.equal(album.sourceReadiness.completed,false);
 assert.equal(sandbox.nativeMissing(),true,'Compatibility must not modify the global Object');
});

test('listing generation, import, translation and history work without ES2022 own-key or at methods',()=>{
 // A fresh process imports the actual module graph with the methods missing.
 // Do not remove built-ins from the concurrent Node test runner itself.
 const result=spawnSync(process.execPath,['--input-type=module','-e',String.raw`
  import assert from 'node:assert/strict';
  Object.defineProperty(Object,'hasOwn',{configurable:true,writable:true,value:undefined});
  Object.defineProperty(Array.prototype,'at',{configurable:true,writable:true,value:undefined});
  Object.defineProperty(String.prototype,'at',{configurable:true,writable:true,value:undefined});
  const {generateListing,extractFacts,analyzeListingSource,FEATURES}=await import('./web/engine.mjs');
  const {detectFurniture}=await import('./web/fact-evidence.mjs');
  const {resolveMetroName}=await import('./web/metro-names.mjs');
  const {translateRussian}=await import('./web/ru-zh-translator.mjs');
  const {archiveValidateAttachmentFile}=await import('./web/card-archive.mjs');
  const {textDiff}=await import('./web/content-studio.mjs');
  const {workflowTagPerformance}=await import('./web/workflow.mjs');
  const {sourceEditorMetadata}=await import('./web/source-readiness.mjs');
  const fields={area:'42',price:'75000',layout:'studio',metro:'Саларьево',features:['fridge']};
  const listing=generateListing(fields);
  assert.deepEqual(listing.problems,[]);assert.match(listing.body,/42㎡/);assert.match(listing.body,/冰箱/);
  assert.equal(resolveMetroName('Саларьево').knownStation,true);
  assert.equal(resolveMetroName('Саларьево',{rules:{Саларьево:'测试站'}}).label,'测试站');
  assert.equal(resolveMetroName('').label,'');
  const furniture=detectFurniture('На кухне есть холодильник. Нет духовки.',FEATURES);
  assert.equal(furniture.find(row=>row.id==='fridge').status,'present');
  assert.equal(furniture.find(row=>row.id==='oven').status,'absent');
  const imported=extractFacts('Студия 42 м². Аренда 75000 руб. На кухне есть холодильник. Дизайнерский ремонт.',{'дизайнерский ремонт':'设计装修'}).facts;
  assert.equal(imported.area,'42');assert.equal(imported.price,'75000');
  assert(imported.features.includes('fridge'));assert.equal(imported.details,'дизайнерский ремонт');
  const source=analyzeListingSource({description:'Есть холодильник. Духовки нет.',text:'Студия 42 м²',offer:{}});
  assert(source.facts.features.includes('fridge'));assert(!source.facts.features.includes('oven'));
  const translated=translateRussian('красный диванчик');
  assert(translated.unknown.includes('диванчик'),'A partial word must not become a confirmed sofa');
  assert.match(translateRussian('Есть холодильник.').text,/冰箱/);
  assert.equal(archiveValidateAttachmentFile({name:'план.PDF',size:200}).name,'план.PDF');
  assert.throws(()=>archiveValidateAttachmentFile({name:'no-extension',size:200}),/Поддерживаются/);
  assert.deepEqual(textDiff('',''),[]);
  const before='旧文字。共同😀段。',after='新文字。共同😀段。',diff=textDiff(before,after);
  assert.equal(diff.filter(row=>row.kind!=='added').map(row=>row.text).join(''),before);
  assert.equal(diff.filter(row=>row.kind!=='removed').map(row=>row.text).join(''),after);
  assert(diff.some(row=>row.kind==='same'&&row.text.includes('😀')));
  assert.deepEqual(workflowTagPerformance([]),[]);
  const first={id:'m1',postId:'post',source:'https://example.com/post',day:'2026-11-01',kind:'cumulative',tags:['莫斯科租房'],views:100,leads:2};
  const report=metrics=>workflowTagPerformance([{id:'card',workflow:{metrics}}])[0];
  const single=report([first]);assert.equal(single.views,null);assert.equal(single.periods[0].from,null);
  const two=report([first,{...first,id:'m2',day:'2026-11-02',views:140,leads:4}]);
  assert.equal(two.views,40);assert.equal(two.leads,2);assert.equal(two.periods[0].from,'2026-11-01');
  const schema=sourceEditorMetadata({sourceSchemaHealth:{version:1,state:'partial',reasons:['unknown-schema','constructor']}});
  assert.deepEqual(schema.sourceSchemaHealth.reasons,['unknown-schema']);
  assert.equal(typeof Object.hasOwn,'undefined');
  assert.equal(typeof Array.prototype.at,'undefined');
  assert.equal(typeof String.prototype.at,'undefined');
 `],{cwd:new URL('../',import.meta.url),encoding:'utf8',timeout:30000});
 assert.equal(result.status,0,result.stderr||result.error?.message||result.stdout);
});

test('browser modules and the generated city runtime do not require ES2022 Object.hasOwn or at',async()=>{
 const web=new URL('../web/',import.meta.url),files=(await readdir(web)).filter(name=>/\.(?:mjs|js)$/.test(name));
 for(const name of files){const source=await readFile(new URL(name,web),'utf8');assert.doesNotMatch(source,/\bObject\s*\.\s*hasOwn\s*\(/,name);assert.doesNotMatch(source,/\.\s*at\s*\(/,name);}
 const city=await readFile(new URL('../scripts/city-pois-runtime.mjs',import.meta.url),'utf8');
 assert.doesNotMatch(city,/\bObject\s*\.\s*hasOwn\s*\(/,'Rebuilding city references must not reintroduce the unsupported API');
});
