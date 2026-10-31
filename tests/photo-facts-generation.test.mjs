import test from 'node:test';
import assert from 'node:assert/strict';
import {FEATURES,extractFacts,analyzeListingSource,generateListing} from '../web/engine.mjs';
import {detectFurniture,resolvePhotoFurniture} from '../web/fact-evidence.mjs';
import {PHOTO_FEATURE_IDS,PHOTO_OBJECT_TERMS,PHOTO_NON_DIRECT_PATTERN,PHOTO_REVIEW_ONLY,isAutomaticPhotoObservation} from '../web/photo-observation-policy.mjs';
import {spawnSync} from 'node:child_process';

const SHA='a'.repeat(64),OTHER_SHA='b'.repeat(64),MODEL='qwen3-vl:2b-instruct-q4_K_M';
const featureLabel=id=>FEATURES.find(row=>row[0]===id)?.[1];
const descriptions={oven:'Духовой шкаф со стеклянной дверцей и ручкой',desk:'Рабочий письменный стол с отдельной столешницей и ящиками',
  chairs:'Кухонные стулья с отдельными спинками возле стола',bedAny:'Кровать с мягким изголовьем у стены',
  tvStand:'ТВ-тумба с двумя ящиками под настенным экраном',monitor:'Монитор на подставке с прямоугольным экраном',
  router:'Роутер с двумя антеннами и сетевыми разъёмами',dryingRack:'Складная сушилка для белья с металлическими прутьями'};
const positive=(id,changes={})=>({id,status:'auto-visible',autoEligible:true,
  originalId:SHA,photoId:SHA,confidence:.96,count:null,
  evidence:[{kind:'model-visual-observation',provider:'ollama',model:MODEL,
    source:'local-photo',originalId:SHA,photoId:SHA,confidence:.96,
    directlyVisible:true,text:descriptions[id]||FEATURES.find(row=>row[0]===id)?.[2]+' с различимыми деталями на фотографии'}],...changes});
const confirmed=(id,changes={})=>positive(id,{status:'confirmed',...changes});
const resolve=fields=>resolvePhotoFurniture(fields,FEATURES);
const output=(fields,style='compact',templateMode='catalog')=>generateListing({style,templateMode,...fields});
const audit=(id,status)=>({id,label:FEATURES.find(row=>row[0]===id)?.[2]||id,status,matches:[]});

test('unobserved and explicitly absent equipment remain private audit facts, not negative public claims',()=>{
  const source='Нет холодильника. Без телевизора. Посудомоечная машина отсутствует.';
  const extracted=extractFacts(source).facts;
  assert.equal(extracted.sourceAudit.furniture.find(row=>row.id==='fridge').status,'absent');
  assert.equal(extracted.sourceAudit.furniture.find(row=>row.id==='tv').status,'absent');
  for(const fields of [{features:[]},extracted,{features:[],sourceAudit:{furniture:[audit('washer','absent'),audit('ac','unknown')]}}]){
    for(const style of ['compact','detailed','story']){
      const result=output(fields,style);
      assert.doesNotMatch(result.body,/未配备|没有冰箱|无电视|无洗衣机|нет холодильника|без телевизора/iu);
      for(const id of ['fridge','tv','dishwasher','washer','ac'])assert(!result.body.includes(featureLabel(id)),id);
      assert.deepEqual(result.problems,[]);
    }
  }
});

test('human photo confirmation survives source absent/planned/conflict and produces a review warning',()=>{
  for(const status of ['absent','planned','conflict']){
    const fields={features:[],featureCounts:{},sourceAudit:{furniture:[audit('oven',status)]},photoEvidence:[confirmed('oven')]};
    const before=structuredClone(fields),facts=resolve(fields),result=output(fields);
    assert.deepEqual(facts.features,['oven']);
    assert.deepEqual(facts.conflicts,[{id:'oven',label:'Духовка',status,source:'photo/source'}]);
    assert.match(result.body,/烤箱/);
    assert.doesNotMatch(result.body,/未配备|没有烤箱|без духовки|照片可见/iu);
    assert(result.translationWarnings.some(warning=>warning.includes('Фото и источник расходятся: Духовка')));
    assert.deepEqual(result.problems,[]);
    assert.deepEqual(fields,before,'generation must preserve the original audit and inputs');
  }
});

test('missing Cian checkbox flags are unknown while false flags cannot suppress a human-confirmed visible object',()=>{
  const missing=analyzeListingSource({description:'Квартира.',offer:{},records:[]}).facts;
  assert.equal(missing.sourceAudit.furniture.find(row=>row.id==='fridge').status,'unknown');
  assert.doesNotMatch(output(missing).body,/冰箱|未配备|没有/);
  const unchecked=analyzeListingSource({description:'Квартира.',offer:{hasFridge:false},records:[]}).facts;
  assert.equal(unchecked.sourceAudit.furniture.find(row=>row.id==='fridge').status,'absent');
  assert.doesNotMatch(output(unchecked).body,/冰箱|未配备|没有/);
  const photographed=output({...unchecked,photoEvidence:[confirmed('fridge')]});
  assert.match(photographed.body,/冰箱/);
  assert(photographed.translationWarnings.some(warning=>warning.includes('Фото и источник расходятся')));
  assert.deepEqual(photographed.problems,[]);
});

test('auto-visible direct high-confidence observations add only their object and mark photographic provenance',()=>{
  for(const score of [.9,.96,1]){
    const row=positive('oven',{confidence:score});row.evidence[0].confidence=score;
    const facts=resolve({features:[],photoEvidence:[row]}),result=output({features:[],photoEvidence:[row]});
    assert.deepEqual(facts.features,['oven']);
    assert.deepEqual(facts.visualFeatures,['oven']);
    assert.match(result.body,/烤箱（照片可见）/);
    assert.doesNotMatch(result.body,/衣柜|冰箱|微波炉|水槽|炉灶/);
  }
});

test('automatic acceptance requires matching photo SHA and direct model evidence, not merely a cached flag',async t=>{
  const rejected={
    'different top-level photoId':()=>positive('oven',{photoId:OTHER_SHA}),
    'different observation photoId':()=>{const row=positive('oven');row.evidence[0].photoId=OTHER_SHA;return row;},
    'different observation originalId':()=>{const row=positive('oven');row.evidence[0].originalId=OTHER_SHA;return row;},
    'missing observation originalId':()=>{const row=positive('oven');delete row.evidence[0].originalId;return row;},
    'missing top-level originalId':()=>{const row=positive('oven');delete row.originalId;return row;},
    'indirect observation':()=>{const row=positive('oven');row.evidence[0].directlyVisible=false;return row;},
    'missing directness flag':()=>{const row=positive('oven');delete row.evidence[0].directlyVisible;return row;},
    'OCR text is not a visual inventory':()=>{const row=positive('oven');row.evidence[0].kind='ocr-text';return row;},
    'classifier crop is not an automatic model observation':()=>{const row=positive('oven');row.evidence[0].kind='classification-region';return row;},
    'different confidence provenance':()=>{const row=positive('oven');row.evidence[0].confidence=.6;return row;},
    'missing model provenance':()=>{const row=positive('oven');delete row.evidence[0].model;return row;},
    'missing provider provenance':()=>{const row=positive('oven');delete row.evidence[0].provider;return row;},
    'different provider provenance':()=>{const row=positive('oven');row.evidence[0].provider='unverified';return row;},
    'missing source provenance':()=>{const row=positive('oven');delete row.evidence[0].source;return row;},
    'cloud model cannot satisfy local evidence':()=>{const row=positive('oven');row.evidence[0].model='unverified-cloud-model';return row;},
    'blank evidence text':()=>positive('oven',{evidence:[{text:'  '}]}),
    'absent evidence':()=>positive('oven',{evidence:[]}),
    'invalid photo SHA':()=>positive('oven',{originalId:'old-photo-id'}),
  };
  for(const [name,create]of Object.entries(rejected))await t.test(name,()=>{
    const row=create(),facts=resolve({features:[],photoEvidence:[row]});
    assert.deepEqual(facts.features,[]);
    assert.deepEqual(facts.photoEvidence,[]);
    assert.doesNotMatch(output({features:[],photoEvidence:[row]}).body,/烤箱/);
  });
});

test('negated or planned observation text cannot contradict the automatic directly-visible flag',()=>{
  for(const text of ['Духовка отсутствует на фотографии','No visible oven in this photograph',
    '烤箱不可见，因为橱柜关闭','Планируется установить духовку']){
    const row=positive('oven');row.evidence[0].text=text;
    assert.deepEqual(resolve({features:[],photoEvidence:[row]}).features,[],text);
    assert.doesNotMatch(output({features:[],photoEvidence:[row]}).body,/烤箱/);
  }
});

test('candidates, low confidence, stale eligibility and malformed confidence never become public equipment',()=>{
  const rows=[positive('oven',{status:'candidate'}),positive('oven',{status:'rejected'}),
    positive('oven',{autoEligible:false}),positive('oven',{autoEligible:1}),
    ...[.89,0,1.01,-1,NaN,Infinity,'0.99',null].map(confidence=>positive('oven',{confidence})),
    positive('invented-feature')];
  for(const row of rows){
    assert.deepEqual(resolve({features:[],photoEvidence:[row]}).features,[],JSON.stringify(row));
    assert.doesNotMatch(output({features:[],photoEvidence:[row]}).body,/烤箱/);
  }
});

test('source-confirmed equipment and later human confirmation remove only automatic provenance markers',()=>{
  const automatic=positive('desk'),human=confirmed('desk');
  for(const fields of [
    {features:[],photoEvidence:[automatic,human]},
    {features:['desk'],sourceAudit:{furniture:[audit('desk','present')]},photoEvidence:[automatic]},
  ]){
    assert.deepEqual(resolve(fields).visualFeatures,[]);
    assert.match(output(fields).body,/书桌/);
    assert.doesNotMatch(output(fields).body,/书桌（照片可见）/);
  }
});

test('repeated photographed chairs use the maximum confirmed count rather than summing overlapping views',()=>{
  const fields={features:[],featureCounts:{chairs:'2'},photoEvidence:[
    confirmed('chairs',{count:2}),confirmed('chairs',{originalId:OTHER_SHA,photoId:OTHER_SHA,count:3}),
    confirmed('chairs',{count:2}),positive('chairs',{count:12}),
  ]};
  assert.equal(resolve(fields).featureCounts.chairs,3);
  assert.match(output(fields).body,/餐椅（3件）/);
  assert.doesNotMatch(output(fields).body,/餐椅（(?:7|12|19)件）/);
  assert.deepEqual(resolve({features:[],photoEvidence:[positive('chairs',{count:2})]}).featureCounts,{},'automatic counts remain unconfirmed');
  for(const count of [0,13,-2,1.2,'2',NaN])assert.deepEqual(resolve({features:[],photoEvidence:[confirmed('chairs',{count})]}).featureCounts,{});
});

test('generic beds never become double beds and review-only structural categories require human confirmation',()=>{
  const generic=extractFacts('В комнате есть кровать.').facts;
  assert(generic.features.includes('bedAny'));
  assert(!generic.features.includes('bed'));
  for(const fields of [generic,{features:[],photoEvidence:[positive('bedAny')]}]){
    assert.match(output(fields).body,/床/);
    assert.doesNotMatch(output(fields).body,/双人床/);
  }
  for(const id of ['bed','internet','roomFurniture','closet','pantry','balcony','loggia']){
    assert(!resolve({features:[],photoEvidence:[positive(id)]}).features.includes(id),id);
    assert(resolve({features:[],photoEvidence:[confirmed(id)]}).features.includes(id),id);
  }
  assert.deepEqual(resolve({features:['bedAny','wardrobe'],photoEvidence:[confirmed('bed'),confirmed('slidingWardrobe')]}).features,['bed','slidingWardrobe']);
});

test('desk and dining table, oven and wardrobe, TV stand and television remain distinct object classes',()=>{
  for(const [text,present,absent]of [
    ['В спальне рабочий стол.',['desk'],['table']],
    ['В комнате письменный стол.',['desk'],['table']],
    ['На кухне обеденный стол и стулья.',['table','chairs'],['desk']],
    ['На кухне встроенный духовой шкаф и кухонные шкафы.',['oven','kitchenFurniture'],['wardrobe','stove','fridge','microwave']],
    ['В комнате ТВ-тумба.',['tvStand'],['tv']],
  ]){
    const facts=extractFacts(text).facts;
    for(const id of present)assert(facts.features.includes(id),text+' → '+id);
    for(const id of absent)assert(!facts.features.includes(id),text+' ≠ '+id);
  }
  for(const [id,unrelated]of [['desk','table'],['oven','wardrobe'],['tvStand','tv'],['monitor','computer'],['router','internet'],['dryingRack','dryer']]){
    const fields={features:[],photoEvidence:[positive(id)]};
    assert.deepEqual(resolve(fields).features,[id]);
    assert(output(fields).body.includes(featureLabel(id)+'（照片可见）'));
    assert(!output(fields).body.includes(featureLabel(unrelated)+'（照片可见）'),id+' must not imply '+unrelated);
  }
});

const addedFurniture=[
  ['coffeeTable','журнальный столик'],['dresser','комод'],['cabinet','буфет'],
  ['shoeCabinet','обувница'],['ottoman','пуф'],['bench','скамья'],['mirror','зеркало'],
  ['curtains','шторы'],['dryingRack','сушилка для белья'],['iron','утюг'],['vacuum','пылесос'],
  ['coffeeMachine','кофемашина'],['toaster','тостер'],['router','роутер'],
  ['monitor','монитор'],['speakers','акустическая система'],['waterHeater','водонагреватель'],['waterFilter','фильтр для воды'],
];

test('all eighteen added object categories have positive text triggers and Chinese generation labels',()=>{
  assert.equal(addedFurniture.length,18);
  for(const [id,word]of addedFurniture){
    const facts=extractFacts('Есть '+word+'.').facts;
    assert(facts.features.includes(id),word+' → '+id);
    assert(output(facts).body.includes(featureLabel(id)),word+' → '+featureLabel(id));
  }
});

test('the eighteen new vocabulary entries do not turn negative or planned mentions into available objects',()=>{
  for(const [id,word]of addedFurniture){
    for(const [text,status]of [['Без '+word+'.','absent'],['Планируется установить '+word+'.','planned']]){
      const rows=detectFurniture(text,FEATURES),row=rows.find(row=>row.id===id),facts=extractFacts(text).facts;
      assert.equal(row.status,status,text+' → '+id);
      assert(!facts.features.includes(id),text+' must remain private');
      assert(!output(facts).body.includes(featureLabel(id)),text+' must not be advertised');
      assert.doesNotMatch(output(facts).body,/未配备|没有|没有配备/);
    }
  }
});

test('automatic policy covers the exact generator feature set and uses the same object terms as Python',()=>{
  assert.deepEqual([...PHOTO_FEATURE_IDS].sort(),FEATURES.map(row=>row[0]).sort());
  assert.equal(PHOTO_FEATURE_IDS.length,57);
  const script="import ast,json,pathlib; tree=ast.parse(pathlib.Path('cli/local_photo_ai.py').read_text()); values={target.id:node.value for node in tree.body if isinstance(node,ast.Assign) for target in node.targets if isinstance(target,ast.Name)}; print(json.dumps({'terms':ast.literal_eval(values['_OBJECT_WORDS']),'nonDirect':ast.literal_eval(values['_NON_DIRECT'].args[0])}))";
  const python=spawnSync('python3',['-c',script],{cwd:new URL('../',import.meta.url),encoding:'utf8'});
  assert.equal(python.status,0,python.stderr);
  assert.deepEqual(PHOTO_OBJECT_TERMS,JSON.parse(python.stdout).terms);
  assert.equal(PHOTO_NON_DIRECT_PATTERN,JSON.parse(python.stdout).nonDirect);
  for(const id of PHOTO_REVIEW_ONLY)assert.equal(isAutomaticPhotoObservation(positive(id)),false,id);
});

test('object-specific terms prevent unrelated generic observations from becoming automatic equipment',()=>{
  for(const id of ['oven','fridge','desk','monitor','router','waterFilter']){
    const row=positive(id);row.evidence[0].text='Хорошо различимый предмет у дальней стены';
    assert.equal(isAutomaticPhotoObservation(row),false,id);
  }
  for(const [id,text]of [['oven','Духовой шкаф со стеклянной дверцей и ручкой'],['desk','Writing desk with drawers beside the window'],
    ['fridge','冰箱有两个独立的门，靠厨房墙壁放置'],['router','Роутер с двумя антеннами и сетевыми разъёмами']]){
    const row=positive(id);row.evidence[0].text=text;
    assert.equal(isAutomaticPhotoObservation(row),true,id);
  }
});

test('automatic policy rejects malformed rows, model identifiers, text controls and strict Boolean substitutions',()=>{
  for(const row of [null,[],true,'oven',{id:'__proto__'},{id:'constructor'},positive('oven',{autoEligible:'true'}),
    positive('oven',{originalId:SHA+'\n'}),positive('oven',{status:true})])assert.equal(isAutomaticPhotoObservation(row),false);
  for(const model of ['',MODEL+'\n',' '+MODEL,'https://host/model','qwen3-cloud','x'.repeat(129),{},null]){
    const row=positive('oven');row.evidence[0].model=model;assert.equal(isAutomaticPhotoObservation(row),false,String(model));
  }
  for(const change of [{directlyVisible:1},{confidence:'0.96'},{text:'oven\u0000here'},{text:'oven'},{text:'烤箱😀😀😀'},{text:'oven '+ 'x'.repeat(300)}]){
    const row=positive('oven');Object.assign(row.evidence[0],change);assert.equal(isAutomaticPhotoObservation(row),false,JSON.stringify(change));
  }
});

test('automatic policy rejects speculative and future observations in Russian, English and Chinese',()=>{
  for(const text of ['Духовка отсутствует на фотографии','Духовой шкаф предположительно скрыт за дверцей',
    'Планируется установить духовку','Духовку купят в следующем месяце','Установят духовой шкаф',
    'No visible oven in this photograph','The oven will be installed','Planning to buy an oven',
    'An oven is going to be installed','烤箱不可见，因为橱柜关闭','计划购买一个烤箱','烤箱將在下週安裝']){
    const row=positive('oven');row.evidence[0].text=text;
    assert.equal(isAutomaticPhotoObservation(row),false,text);
  }
  const visible=positive('desk');visible.evidence[0].text='Writing desk with two drawers beside the window';
  assert.equal(isAutomaticPhotoObservation(visible),true,'window must not match the English future word will');
});

test('explicit source negatives and planned purchases never masquerade as available furniture',()=>{
  for(const [text,id,status]of [
    ['Не будет кофемашины.','coffeeMachine','absent'],
    ['Кофемашина не предусмотрена.','coffeeMachine','absent'],
    ['Кофемашину планируют установить.','coffeeMachine','planned'],
    ['Кофемашину можно купить.','coffeeMachine','planned'],
    ['Планируем купить пылесос.','vacuum','planned'],
    ['Пылесос будет куплен.','vacuum','planned'],
  ]){
    const facts=extractFacts(text).facts;
    assert.equal(facts.sourceAudit.furniture.find(row=>row.id===id).status,status,text);
    assert(!facts.features.includes(id),text);
    assert(!output(facts).body.includes(featureLabel(id)),text);
  }
});
