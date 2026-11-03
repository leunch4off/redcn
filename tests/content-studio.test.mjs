import test from 'node:test';
import assert from 'node:assert/strict';
import {TITLE_FORMULA_VARIABLES,validateTitleFormula,renderTitleFormula,normalizeStudioProfile,
 titleHistoryKey,recordTitleHistory,textDiff,publicationTextDiff} from '../web/content-studio.mjs';

test('title formula permits only documented current-card substitutions, without evaluation',()=>{
 assert.deepEqual(TITLE_FORMULA_VARIABLES,['city','layout','area','price','metro','floor','feature','university','code']);
 const formula='{city}｜{layout}｜{area}｜{price}';
 assert.equal(validateTitleFormula(formula).valid,true);
 assert.equal(renderTitleFormula(formula,{city:'莫斯科',layout:'两居室',area:'52㎡',price:'8W/月'}),'莫斯科｜两居室｜52㎡｜8W/月');
 for(const formula of ['{commission}','{constructor}','{__proto__}','{fields.price}','{{price}}','{price','price}','literal only','',null,'{price}\u0000'])assert.equal(validateTitleFormula(formula).valid,false,String(formula));
 assert.equal(renderTitleFormula('{city}｜{code}',{city:'莫斯科',code:'${globalThis.injected=true}'}),'莫斯科｜${globalThis.injected=true}');
 assert.equal(globalThis.injected,undefined);
});

test('title formula keeps values nonrecursive and suppresses absent facts instead of guessing',()=>{
 assert.equal(renderTitleFormula('{city}｜{metro}｜{area}｜{price}',{city:'莫斯科',metro:'',area:'52㎡',price:null}),'莫斯科｜52㎡');
 assert.equal(renderTitleFormula('{price}',{}),'');
 assert.equal(renderTitleFormula('价格 {price}',{}),'');
 assert.equal(renderTitleFormula('{code}',{code:'{price}'}),'{price}');
 assert.equal(renderTitleFormula('{price}',{price:0}),'0');
 assert.throws(()=>renderTitleFormula('{city}',{city:[]}),TypeError);
 assert.throws(()=>renderTitleFormula('{price}',{price:NaN}),TypeError);
 assert.throws(()=>renderTitleFormula('{price}',{price:Infinity}),TypeError);
 assert.throws(()=>renderTitleFormula('{price}',{price:{toString(){return '8W';}}}),TypeError);
 assert.equal(renderTitleFormula('{city}',Object.create({city:'inherited'})),'');
});

test('custom title formula cannot reintroduce public commission statements',()=>{
 for(const formula of ['{price} 中介费','{city} Без комиссии','{layout} agency fee'])assert.equal(validateTitleFormula(formula).valid,false);
 for(const value of ['无中介费','комиссия 50%','brokerage fee'])assert.throws(()=>renderTitleFormula('{city}',{city:value}),/Комиссия/);
});

test('studio profile validation persists the full punctuation emoji and addressing contract',()=>{
 const value={id:'plain',name:'Лаконичный',punctuation:'compact',emoji:'none',addressing:'polite'};
 assert.deepEqual(normalizeStudioProfile(value),value);
 assert.deepEqual(normalizeStudioProfile({}),{id:'default',name:'Обычный',punctuation:'standard',emoji:'source',addressing:'neutral'});
 for(const value of [{id:'../style'},{id:'test',name:' '},{punctuation:'script'},{emoji:true},{addressing:'students-only'},null,[]])assert.throws(()=>normalizeStudioProfile(value));
});

test('title history belongs to the source listing and ignores URL tracking changes',()=>{
 const first=titleHistoryKey({url:'https://www.avito.ru/moskva/kvartiry/7462093377?context=one',resultId:'a'.repeat(32)});
 assert.equal(first,titleHistoryKey({url:'https://www.avito.ru/moskva/kvartiry/7462093377/?context=two'}));
 assert.notEqual(first,titleHistoryKey({url:'https://www.avito.ru/moskva/kvartiry/7462093378'}));
 assert.equal(titleHistoryKey({resultId:'b'.repeat(32)}),'card:'+'b'.repeat(32));
 assert.equal(titleHistoryKey({editorIdentity:'23378c18-dc20-4d4a-b506-145a50465611',name:'Квартира'}),'editor:23378c18-dc20-4d4a-b506-145a50465611');
 assert.equal(titleHistoryKey({name:' Квартира 1 '}),'name:Квартира 1');
 assert.equal(titleHistoryKey({}),'draft');
 let history=recordTitleHistory({},first,'Первый','selected','2026-11-01T00:00:00Z');
 const frozen=JSON.stringify(history);
 history=recordTitleHistory(history,first,'Второй','selected','2026-11-01T00:01:00Z');
 assert.equal(history[first][0].title,'Второй');assert.equal(history[first][1].title,'Первый');
 assert.equal(JSON.parse(frozen)[first].length,1);
 history=recordTitleHistory(history,'other','Другой','selected','2026-11-01T00:02:00Z');
 assert.equal(history.other.length,1);assert.equal(history[first].length,2);
 history=recordTitleHistory(history,first,'Первый','restored','2026-11-01T00:03:00Z');
 assert.equal(history[first].length,2);assert.equal(history[first][0].kind,'restored');
});

test('anonymous card title histories remain separate and the stable editor identity survives reload',()=>{
 const a={editorIdentity:'23378c18-dc20-4d4a-b506-145a50465611'},b={editorIdentity:'653d85a8-70de-4876-bc73-d93089a8804c'};
 const keyA=titleHistoryKey(a),keyB=titleHistoryKey(b);
 assert.notEqual(keyA,keyB);
 assert.equal(titleHistoryKey(JSON.parse(JSON.stringify(a))),keyA);
 assert.equal(titleHistoryKey({...a,name:'Переименованная карточка'}),keyA);
 const first=recordTitleHistory({},keyA,'Первый анонимный объект');
 const second=recordTitleHistory(first,keyB,'Второй анонимный объект');
 assert.equal(second[keyA][0].title,'Первый анонимный объект');
 assert.equal(second[keyB][0].title,'Второй анонимный объект');
});

test('title history is bounded, source dictionaries are immutable, and client text rules still apply',()=>{
 let history={};for(let source=0;source<45;source++)for(let title=0;title<55;title++)history=recordTitleHistory(history,'source-'+source,'标题 '+title,'manual',String(title));
 assert.equal(Object.keys(history).length,40);assert.equal(history['source-44'].length,50);
 assert.equal(history['source-44'][0].title,'标题 54');
 assert.equal(recordTitleHistory(history,'source-44','',undefined),history);
 assert.throws(()=>recordTitleHistory(history,'source-44','租金8W含中介费'),/публикац/);
 assert.equal(recordTitleHistory({},'source','中介费 50%').source,undefined);
});

test('regeneration diff preserves old/new phrase positions, Chinese punctuation, and unknowns exactly',()=>{
 const before='📍 莫斯科。\n月租8W。\n书桌、冰箱。';
 const after='📍 莫斯科。\n月租8.5W。\n书桌、冰箱。\n押金待确认。';
 const rows=textDiff(before,after);
 assert.equal(rows.filter(row=>row.kind!=='added').map(row=>row.text).join(''),before);
 assert.equal(rows.filter(row=>row.kind!=='removed').map(row=>row.text).join(''),after);
 assert(rows.some(row=>row.kind==='removed'&&row.text.includes('8W')));
 assert(rows.some(row=>row.kind==='added'&&row.text.includes('8.5W')));
 const diff=publicationTextDiff({title:'标题',body:before,tags:'#莫斯科'},{title:'新标题',body:after,tags:'#莫斯科'});
 assert.deepEqual(diff.map(row=>row.key),['title','body']);
 assert.deepEqual(publicationTextDiff({title:'same'},{title:'same'}),[]);
});

test('bounded diff does not silently truncate long posts and handles empty fields',()=>{
 const before='原文。'.repeat(500),after='新版。'.repeat(501);
 assert.deepEqual(textDiff(before,after),[{kind:'removed',text:before},{kind:'added',text:after}]);
 assert.deepEqual(textDiff('', '新增'),[{kind:'added',text:'新增'}]);
 assert.deepEqual(textDiff('删除',''),[{kind:'removed',text:'删除'}]);
 assert.deepEqual(textDiff('',''),[]);
});
