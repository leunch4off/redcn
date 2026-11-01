import test from 'node:test';
import assert from 'node:assert/strict';
import {sourceReadinessFeedback,importCompletionFeedback,importStageMark,sourceEditorMetadata} from '../web/source-readiness.mjs';

const partialSource=()=>({state:'partial',partial:true,completed:false,sections:[
  {name:'gallery',ready:true,reason:'ready',visited:true,found:true},
  {name:'description',ready:false,reason:'not_visited_budget',visited:false,found:null},
  {name:'characteristics',ready:false,reason:'not_found',visited:true,found:false}
]});

test('completed operation keeps source gaps visible and never offers a photo retry for them',()=>{
  const job={state:'done',partial:true,photoPartial:false,sourcePartial:true,result_id:'a'.repeat(32),sourceReadiness:partialSource()},before=structuredClone(job);
  const result=importCompletionFeedback(job);
  assert.match(result.title,/исходная страница собрана частично/);
  assert.match(result.message,/Сбор исходной страницы частичный/);
  assert.match(result.message,/Описание — проверка не выполнена: исчерпан лимит времени/);
  assert.match(result.message,/Характеристики — раздел не найден при проверке/);
  assert.doesNotMatch(result.message,/Фотографии|нет холодильника|нет мебели/);
  assert.equal(result.retryPhotos,false);assert.equal(result.photoPartial,false);assert.deepEqual(job,before);
});

test('unknown completeness is unverified, while a fully checked source adds no warning',()=>{
  assert.equal(sourceReadinessFeedback({sourceReadiness:{state:'unknown',partial:false,completed:false,sections:[]}}).message,'Полнота исходной страницы не проверялась.');
  assert.equal(sourceReadinessFeedback({sourceReadiness:{state:'ready',partial:false,completed:true,sections:[]}}).message,'');
  assert.equal(sourceReadinessFeedback({}).message,'');
});

test('saved listing/card readiness supplies the persistent source warning without exposing capture',()=>{
  const saved={listing:{sourceReadiness:partialSource(),capture:{rawHtml:'PRIVATE_CAPTURE'}}};
  assert.match(importCompletionFeedback({state:'done',partial:true,photoPartial:false,result_id:'x'},saved).message,/Сбор исходной страницы частичный/);
  const card={fields:{sourceReadiness:partialSource()}};
  assert.match(sourceReadinessFeedback(card).message,/Описание/);
  assert.doesNotMatch(JSON.stringify(sourceReadinessFeedback(saved)),/PRIVATE_CAPTURE|rawHtml/);
});

test('only known section names and reason codes become user feedback',()=>{
  const readiness=partialSource();readiness.warnings=['PRIVATE_COOKIE'];
  readiness.sections.push({name:'PRIVATE_CONTRACT',reason:'PRIVATE_AUTH'});
  readiness.sections[1].reason='PRIVATE_DESCRIPTION';
  const feedback=sourceReadinessFeedback({sourceReadiness:readiness});
  assert.match(feedback.message,/Описание — полнота раздела не подтверждена/);
  assert.doesNotMatch(JSON.stringify(feedback),/PRIVATE|COOKIE|CONTRACT|AUTH/);
});

test('source and photo completeness remain independent with explicit and legacy contracts',()=>{
  const photo=importCompletionFeedback({state:'done',partial:true,photoPartial:true,sourcePartial:false,result_id:'x'});
  assert.equal(photo.retryPhotos,true);assert.equal(photo.sourcePartial,false);assert.match(photo.message,/Фотографии подготовлены частично/);
  const both=importCompletionFeedback({state:'done',partial:true,photoPartial:true,sourcePartial:true,result_id:'x',sourceReadiness:partialSource()});
  assert.equal(both.retryPhotos,true);assert.match(both.message,/Сбор исходной страницы частичный/);assert.match(both.message,/Фотографии подготовлены частично/);
  assert.equal(importCompletionFeedback({partial:true,result_id:'x'}).retryPhotos,true);
  assert.equal(importCompletionFeedback({partial:true,sourcePartial:true,result_id:'x'}).retryPhotos,false);
  assert.equal(importCompletionFeedback({partial:true,photoPartial:false,result_id:'x'}).retryPhotos,false);
});

test('a partially checked scroll stage never receives a completion check mark',()=>{
  assert.equal(importStageMark({state:'partial'}),'!');
  assert.equal(importStageMark({state:'done',quality:'partial'}),'!');
  assert.equal(importStageMark({state:'done',quality:'complete'}),'✓');
  assert.equal(importStageMark({state:'running'}),'…');
});

test('editor snapshots retain structured source metadata through listing fallback, clones and save projection',()=>{
  const listing={sourceReadiness:partialSource(),photoItems:[{url:'https://images.cdn-cian.ru/images/room.jpg',photoId:'123',order:0,isLayout:false,isCianLayout:false,variants:{fullUrl:'https://images.cdn-cian.ru/images/room.jpg',thumbnailUrl:'https://images.cdn-cian.ru/images/room-small.jpg'},source:'CIAN offer'}],
    layouts:[{url:'https://images.cdn-cian.ru/images/plan.jpg',photoId:'456',order:1,isLayout:true,isCianLayout:true,variants:{fullUrl:'https://images.cdn-cian.ru/images/plan.jpg'}}],
    sourceDates:{created:'2021-04-26T00:00:00Z',published:1619455532,updated:null,timezone:'Europe/Moscow'},transitDetails:[{id:9,name:'Лианозово',line:'МЦД',color:'#000',mode:'walk',time:7,source:'CIAN geo.undergrounds'}]};
  const before=structuredClone(listing),fields=sourceEditorMetadata({},listing),snapshot=sourceEditorMetadata(fields),saved=sourceEditorMetadata(snapshot);
  for(const key of ['photoItems','layouts','sourceDates','transitDetails'])assert.deepEqual(saved[key],listing[key]);
  assert.equal(saved.sourceReadiness.state,'partial');assert.equal(saved.sourceReadiness.sections[1].reason,'not_visited_budget');
  saved.photoItems[0].variants.thumbnailUrl='https://example.test/changed.jpg';assert.deepEqual(listing,before);assert.deepEqual(fields.photoItems,listing.photoItems);
  assert.deepEqual(sourceEditorMetadata({photoItems:[]},listing).photoItems,[]);
  assert.deepEqual(sourceEditorMetadata({}),{});
});

test('source snapshot metadata cannot retain raw DOM, credentials, unknown nested fields or malformed URLs',()=>{
  const metadata=sourceEditorMetadata({sourceReadiness:{...partialSource(),capture:'PRIVATE_CAPTURE',warnings:['PRIVATE_WARNING']},
    photoItems:[{url:'https://images.cdn-cian.ru/images/room.jpg',html:'PRIVATE_HTML',variants:{fullUrl:'https://user:PRIVATE_AUTH@example.test/photo.jpg',rawHtml:'PRIVATE_HTML'}},{url:'javascript:PRIVATE_SCRIPT'}],
    layouts:[{url:'https://images.cdn-cian.ru/images/plan.jpg',cookies:'PRIVATE_COOKIE'}],
    sourceDates:{published:1619455532,rawHtml:'PRIVATE_HTML'},transitDetails:[{id:5,name:'Лианозово',rawHtml:'PRIVATE_HTML',offer:{token:'PRIVATE_TOKEN'}}],capture:{html:'PRIVATE_HTML'},offer:{token:'PRIVATE_TOKEN'}});
  assert.doesNotMatch(JSON.stringify(metadata),/PRIVATE|rawHtml|cookies|offer|capture/);
  assert.equal(metadata.photoItems.length,1);assert.equal(metadata.photoItems[0].variants.fullUrl,undefined);assert.equal(metadata.layouts[0].isLayout,true);assert.equal(metadata.transitDetails[0].id,5);
});
