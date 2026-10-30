import test from 'node:test';
import assert from 'node:assert/strict';
import {translateRussian,setExtendedLexicon,getLexiconStats,searchLexiconPage} from '../web/ru-zh-translator.mjs';

test('translation preserves edge whitespace without WebKit zero-width regexp results',()=>{
 const original=RegExp.prototype[Symbol.match];
 RegExp.prototype[Symbol.match]=function(value){
  if(this.source==='\\s*$'||this.source==='^\\s*')return null;
  return original.call(this,value);
 };
 try{
  const result=translateRussian('\u00a0\tМГУ\u202f ',{rules:{МГУ:'莫斯科国立大学'}});
  assert.equal(result.text,'\u00a0\t莫斯科国立大学\u202f ');
  assert.deepEqual(result.unknown,[]);
  for(const suffix of ['😀','𫓧','\ud83d','\ude00']){
   const source='\t我的房源'+suffix+'\u00a0';
   assert.equal(translateRussian(source).text,source);
  }
  assert.equal(translateRussian('\t\u00a0\u202f ').text,'\t\u00a0\u202f ');
 }finally{RegExp.prototype[Symbol.match]=original;}
});

test('rental translation preserves amounts, units, floors and inventory quantities',()=>{
 const result=translateRussian('Цена 80 тыс. рублей в месяц; Площадь 52,5 м²; Этаж 7 из 16; Есть 2 стула, холодильник и духовка');
 assert.match(result.text,/80,000卢布/);assert.match(result.text,/52\.5平方米/);assert.match(result.text,/第7层，共16层/);assert.match(result.text,/2把椅子/);assert.match(result.text,/冰箱/);assert.match(result.text,/烤箱/);
 assert.deepEqual(result.unknown,[]);
});
test('money grouping preserves ruble amounts and leaves malformed amounts for review',()=>{
 for(const amount of ['80.000','80,000','80 000']){
  const rent=translateRussian('Цена '+amount+' рублей');assert.equal(rent.text,'月租80,000卢布');assert.equal(rent.needsReview,false);
  assert.equal(translateRussian('Залог '+amount+' рублей').text,'押金80,000卢布');
 }
 assert.equal(translateRussian('Цена 80.000,50 рублей').text,'月租80,000.5卢布');
 assert.equal(translateRussian('Цена 80,000.50 рублей').text,'月租80,000.5卢布');
 assert.equal(translateRussian('Цена 80,50 тыс. рублей').text,'月租80,500卢布');
 assert.equal(translateRussian('Цена 1.234.567 рублей').text,'月租1,234,567卢布');
 const malformed=translateRussian('Цена 80.00.00 рублей');assert.equal(malformed.text,'Цена 80.00.00 рублей');assert.equal(malformed.needsReview,true);
});
test('metro travel keeps explicit modes before or after time and never assumes walking',()=>{
 for(const source of ['До метро на автобусе 10 минут','До метро 10 минут на автобусе']){
  const result=translateRussian(source);assert.equal(result.text,'乘公交10分钟到地铁站');assert.equal(result.needsReview,false);
 }
 assert.equal(translateRussian('До метро Саларьево на автобусе 10 минут').text,'乘公交10分钟到萨拉里耶沃地铁站');
 assert.equal(translateRussian('До метро на автобусе Саларьево 10 минут').text,'乘公交10分钟到萨拉里耶沃地铁站');
 assert.equal(translateRussian('До метро на машине 10 минут').text,'驾车10分钟到地铁站');
 assert.equal(translateRussian('До метро 10 минут пешком').text,'步行10分钟到地铁站');
 assert.equal(translateRussian('До метро 10 минут').text,'约10分钟到地铁站');
 const conflict=translateRussian('До метро на автобусе 10 минут пешком');assert.equal(conflict.text,'До метро на автобусе 10 минут пешком');assert.equal(conflict.needsReview,true);
});
test('pet and child restrictions are tenancy conditions rather than missing equipment',()=>{
 const pets=translateRussian('Квартира без животных');assert.match(pets.text,/不接受宠物/);assert.doesNotMatch(pets.text,/配备/);assert.equal(pets.needsReview,false);
 const children=translateRussian('Комната без детей');assert.match(children.text,/不接受/);assert.doesNotMatch(children.text,/配备/);
 const missing=translateRussian('Квартира без холодильника');assert.match(missing.text,/不配备冰箱/);
 assert.equal(translateRussian('Квартира без комиссии').text,'公寓，免中介费');
});
test('negation and unanswered questions never become positive inventory',()=>{
 assert.match(translateRussian('Нет холодильника').text,/不配备冰箱/);
 const unknown=translateRussian('Не установят холодильник до июня');assert.equal(unknown.text,'Не установят холодильник до июня');assert.equal(unknown.needsReview,true);
 const question=translateRussian('Есть ли холодильник завтра');assert.equal(question.text,'Есть ли холодильник завтра');assert.equal(question.needsReview,true);
 const known=translateRussian('можно ли с питомцем');assert.match(known.text,/[？?]|是否|可以|能/);assert.equal(known.unknown.length,0);
});
test('manual rules override community and base values without translating replacement text again',()=>{
 assert.equal(translateRussian('новый ремонт',{rules:{'новый ремонт':'我的装修'}}).text,'我的装修');
 assert.equal(translateRussian('а',{rules:{а:'б',б:'в'}}).text,'б');
 assert.equal(translateRussian('Есть холодильник',{rules:{холодильник:'定制冰箱'}}).text,'配有定制冰箱');
 assert.equal(translateRussian('стол; шкаф',{preservePunctuation:true}).text.includes(';'),true);
});
test('untranslated clauses report unknowns with honest coverage and source positions',()=>{
 const result=translateRussian('неведомый неведомый. холодильник');assert.deepEqual(result.unknown,['неведомый']);assert.equal(result.coverage.translatedWords,1);assert.equal(result.coverage.totalWords,3);
 const match=result.matches.find(m=>m.ru==='холодильник');assert.equal(match.start,'неведомый неведомый. холодильник'.indexOf('холодильник'));assert.equal(result.needsReview,true);
});
test('open dictionary stays review-only and attested forms preserve ambiguity',()=>{
 const stats=setExtendedLexicon({license:'CC-BY-SA-4.0',entries:[['съёмный','可拆卸','fixture','съёмный'],['флеровий','𫓧','fixture','флеровий'],['кастрюля','锅','fixture','кастрюля']],forms:[['кастрюли','кастрюля']],sources:[{id:'fixture',pageUrlPrefix:'https://example.org/wiki/'}]});
 assert.equal(stats.openEntries,3);assert.equal(stats.openWordForms,1);
 assert.equal(searchLexiconPage('кастрюли').entries.some(row=>row.ru==='кастрюля'),true);
 assert.equal(translateRussian('съёмный').unknown.length>0,true);
 const lookup=translateRussian('съёмный',{mode:'lookup'});assert.equal(lookup.text,'可拆卸');assert.equal(lookup.needsReview,true);assert.match(lookup.warnings.join(' '),/непроверенные/);
 assert.equal(getLexiconStats().openLoaded,true);
});
