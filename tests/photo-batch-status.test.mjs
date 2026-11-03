import test from 'node:test';
import assert from 'node:assert/strict';
import {photoBatchSummary} from '../web/photo-batch-status.mjs';
import {readCianHtml} from '../web/source-adapters/cian.mjs';
import {photoObservationSummary} from '../web/photo-ui.mjs';

test('a failed download and failed crop report separate reasons and keep18 originals of19',()=>{
 const summary=photoBatchSummary({photoCount:19,mediaStatus:'partial',photoProcessing:{downloadFailed:1,failed:1,skipped:0},mediaFailures:[{index:3,stage:'crop',originalSaved:true,message:'OCR unavailable'},{index:7,stage:'download',originalSaved:false,message:'HTTP failed'}]},18,2);
 assert.equal(summary.expected,19);assert.equal(summary.count,18);assert.equal(summary.retry,true);
 assert.match(summary.text,/18 из 19/);assert.match(summary.text,/Не удалось скачать 1/);assert.match(summary.text,/Автообрезка недоступна для 1/);
 assert.equal(summary.failures[0].originalSaved,true);assert.equal(summary.failures[1].originalSaved,false);
});

test('zero downloaded photos explain failed status instead of endlessly saying Mac is preparing',()=>{
 const summary=photoBatchSummary({photoCount:19,mediaStatus:'partial',photoProcessing:{downloadFailed:19}},0,0);
 assert.match(summary.text,/0 из 19/);assert.match(summary.text,/Не удалось скачать 19/);assert.equal(summary.retry,true);
});
test('failure after the30thphoto and incomplete inventory remain visible',()=>{
 const summary=photoBatchSummary({photoCount:36,mediaStatus:'partial',photoProcessing:{downloadFailed:1},
  mediaFailures:[{index:36,stage:'download',originalSaved:false,message:'last photo failed'}],
  photoInventory:{state:'partial',total:36,totalAvailable:36,checked:30,failed:1,skipped:5,autoApplySummary:{needsReview:4}}},35,0);
 assert.equal(summary.failures[0].index,36);assert.match(summary.text,/Мебель и техника: проверено 30 из 36/);
 assert.match(summary.text,/Не удалось проанализировать 1/);assert.match(summary.text,/Ещё не проверено 5/);assert.match(summary.text,/Неподтверждённых находок: 4/);
});
test('over200photos is reported and never claimed to be a complete inventory',()=>{
 const summary=photoBatchSummary({photoCount:200,photoInventory:{state:'partial',total:200,totalAvailable:205,checked:200,failed:0,truncated:true,coverageLimit:200}},200);
 assert.match(summary.text,/200 из 205/);assert.match(summary.text,/Ещё не проверено 5/);assert.match(summary.text,/Лимит одного альбома — 200/);
});
test('photo UI separates unchecked, failed and no confident findings',()=>{
 const summary=photoObservationSummary([{selected:true},{selected:true,analysis:{status:'timeout',candidates:[]}},
  {selected:true,analysis:{status:'review',candidates:[]}},
  {selected:true,analysis:{status:'review',candidates:[{id:'sofa'}]}},
  {selected:false,analysis:{status:'review',candidates:[]}}]);
 assert.deepEqual([summary.total,summary.checked,summary.failed,summary.unprocessed,summary.unrecognized,summary.pending],[4,2,1,1,1,1]);
 assert.match(summary.text,/не означает отсутствие мебели/);
});

test('intentional original choices and absent watermarks do not require endless retries',()=>{
 const summary=photoBatchSummary({photoCount:19,photoProcessing:{skipped:19,deferred:0,downloadFailed:0}},19,0,false);
 assert.equal(summary.retry,false);assert.match(summary.text,/исходные фотографии без изменений/);
});

test('CIAN scoped gallery retains19 photos when offer JSON is absent and OG has one',()=>{
 const urls=Array.from({length:19},(_,index)=>'https://images.cdn-cian.ru/images/room-'+index+'.jpg');
 const html='<meta property="og:image" content="https://images.cdn-cian.ru/share-composite.jpg"><h1 data-name="OfferTitle">Квартира 54 м²</h1><div data-name="OfferGallery">'+urls.map((url,index)=>'<img '+(index===0?'src="'+url+'"':'data-src="'+url+'" src="data:image/gif;base64,AA"')+'>').join('')+'<div data-name="SimilarOffers"><img src="https://images.cdn-cian.ru/unrelated.jpg"></div></div>';
 assert.deepEqual(readCianHtml(html).images,urls);
});

test('CIAN gallery chooses largest srcset image once and excludes unrelated images',()=>{
 const source=readCianHtml('<div data-name="Gallery"><img src="https://images.cdn-cian.ru/small.jpg" srcset="https://images.cdn-cian.ru/medium.jpg 400w, https://images.cdn-cian.ru/full.jpg 1600w"></div><img src="https://images.cdn-cian.ru/avatar.jpg">');
 assert.deepEqual(source.images,['https://images.cdn-cian.ru/full.jpg']);
});
