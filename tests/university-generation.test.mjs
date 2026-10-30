import test from 'node:test';
import assert from 'node:assert/strict';
import {UNIVERSITY_CATALOG,resolveUniversity} from '../web/university-catalog.mjs';
import {DICTIONARY,generateListing,translate,extractFacts,extractUniversityReferences,analyzeListingSource} from '../web/engine.mjs';
import {translateRussian} from '../web/ru-zh-translator.mjs';

const uniFixture={templateMode:'catalog',rooms:'2',price:'80000',area:'54',style:'compact',features:[],support:false};
const msu=()=>resolveUniversity('МГУ');
test('each catalog university participates in titles and descriptions only after explicit selection',()=>{
  for(const row of UNIVERSITY_CATALOG.universities){
    const result=generateListing({...uniFixture,university:row.ru});
    assert(result.titles[0].includes(row.zh),row.ru+' title');
    assert(result.body.includes(row.zh),row.ru+' body');
    assert(result.titles[0].includes('通勤目标'));assert.match(result.body,/时间待确认/);
    assert.doesNotMatch(result.body,/步行约\d|公交约\d|地铁约\d|近地铁|距离大学/);
  }
});
test('campus references use complete Chinese names without borrowing their address or metro',()=>{
  for(const row of UNIVERSITY_CATALOG.campuses){
    const result=generateListing({...uniFixture,campus:row.ru});
    assert(result.body.includes(row.zh),row.ru);
    if(row.addressRu)assert(!result.body.includes(row.addressRu));
    assert.doesNotMatch(result.body,/步行约\d|公交约\d|地铁约\d/);
  }
});
test('a known university and campus mismatch blocks silent association',()=>{
  const campus=UNIVERSITY_CATALOG.campuses.find(row=>row.universityId!==msu().id);
  assert(campus);
  const result=generateListing({...uniFixture,university:msu().ru,campus:campus.ru});
  assert(result.problems.some(value=>value.includes('другому вузу')));
});
test('school vocabulary never injects unrelated universities into an empty card',()=>{
  const result=generateListing(uniFixture);
  assert.doesNotMatch(result.titles.join('\n')+result.body+result.tags,/通勤目标|关注院校|莫斯科国立大学|莫大租房|大学通勤/);
});
test('a university reference does not turn general housing into student-only marketing',()=>{
  const general=generateListing({...uniFixture,university:'МГУ',audience:'general'});
  assert.doesNotMatch(general.tags,/留学生租房|莫斯科留学/);
  assert.doesNotMatch(general.body,/仅限学生|只接受学生/);
  const student=generateListing({...uniFixture,university:'МГУ',audience:'student'});
  assert.match(student.tags,/留学生租房|莫斯科留学/);
});
test('custom school translations have priority over canonical defaults',()=>{
  const rules={...DICTIONARY,'МГУ':'我的大学称呼'};
  assert.equal(translate('МГУ',rules),'我的大学称呼');
  const result=generateListing({...uniFixture,university:'МГУ'},rules);
  assert(result.body.includes('我的大学称呼'));assert(result.titles[0].includes('我的大学称呼'));
});
test('full university names with initials remain intact inside prose and custom rules win',()=>{
  for(const row of UNIVERSITY_CATALOG.universities){
    const source='Вуз: '+row.ru+'.';
    const result=translateRussian(source);
    assert(result.text.includes(row.zh),row.ru+' inside prose');
    const known=result.matches.find(match=>match.ru===row.ru);
    assert(known,row.ru+' source span');assert.equal(source.slice(known.start,known.end),row.ru);
    const custom=translateRussian(source,{rules:{[row.ru]:'我的院校名称'}});
    assert(custom.text.includes('我的院校名称'));assert(!custom.text.includes(row.zh));
  }
});
test('university times require supplied route data and an unknown mode stays unknown',()=>{
  const unknown=generateListing({...uniFixture,universities:'МГУ | 25'});
  assert.match(unknown.body,/出行方式待确认约25分钟/);assert.doesNotMatch(unknown.body,/步行约25|公共交通约25/);
  const known=generateListing({...uniFixture,universities:'МГУ | 25 | bus'});
  assert.match(known.body,/公交约25分钟/);
});
test('source school mentions preserve only observed times and exact alias boundaries',()=>{
  const result=extractFacts('Квартира 54 м². До МГУ — 25 минут на автобусе. Цена 80000 рублей.');
  assert.equal(result.facts.university,msu().ru);assert.equal(result.facts.uniTime,'25');assert.equal(result.facts.uniMode,'bus');
  const nameOnly=extractFacts('Квартира 54 м². В описании указан МГУ.');
  assert.equal(nameOnly.facts.uniTime,'');assert.equal(nameOnly.facts.uniMode,'unknown');
  assert(!extractUniversityReferences('Это ПсевдоМГУтест, название вымышлено.').universities.some(row=>row.name===msu().ru));
  assert(!extractUniversityReferences('Нет рядом МГУ.').universities.some(row=>row.name===msu().ru));
  assert(!extractUniversityReferences('Есть ли МГУ поблизости?').universities.some(row=>row.name===msu().ru));
});
test('the apartment description wins over unrelated university mentions elsewhere on the page',()=>{
  const result=analyzeListingSource({text:'Рекомендации рядом с МГУ. Цена 80000 рублей. Квартира 54 м².',description:'Квартира со шкафом и столом. О вузе ничего не сказано.',records:[],images:[]});
  assert.equal(result.facts.university,undefined);assert.equal(result.facts.campus,undefined);
  assert(!result.sourceAudit.fields.some(row=>row.field==='university'));
});
