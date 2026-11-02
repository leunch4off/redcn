import test from 'node:test';
import assert from 'node:assert/strict';
import {sourceReadinessFeedback,sourceEditorMetadata,importCompletionFeedback} from '../web/source-readiness.mjs';

const source=gallery=>({state:'ready',completed:true,partial:false,sections:[{name:'gallery',reason:'ready',found:true,visited:true,ready:true}],gallery});

test('19 announced photos and one ready visible photo retain an independent missing indicator after save',()=>{
 const value={sourceReadiness:source({observed:1,expected:19,missing:0,state:'complete',limit:30,countEvidence:[{count:19,source:'aria-setsize'}],rawHtml:'PRIVATE'})};
 const saved=sourceEditorMetadata(sourceEditorMetadata(value));
 assert.equal(saved.sourceReadiness.gallery.missing,18);assert.equal(saved.sourceReadiness.gallery.state,'partial');assert.equal(saved.sourceReadiness.partial,true);
 const feedback=sourceReadinessFeedback(saved);assert.match(feedback.message,/найдено 1 из 19 фото; не найдено 18/);assert.equal(feedback.partial,true);
 assert.doesNotMatch(JSON.stringify(saved),/PRIVATE|rawHtml/);
 const completion=importCompletionFeedback({state:'done',photoPartial:false,result_id:'a'.repeat(32)},saved);
 assert.match(completion.message,/Альбом/);assert.equal(completion.retryPhotos,false,'Missing source links require source review, not another cached-photo crop');
});

test('a gallery without announced total or with contradictory counts never claims every photo was collected',()=>{
 for(const gallery of [{observed:3,expected:null},{observed:20,expected:19}]){
  const value={sourceReadiness:source(gallery)},feedback=sourceReadinessFeedback(value);
  assert.match(feedback.message,/полнота не подтверждена/);assert.equal(sourceEditorMetadata(value).sourceReadiness.gallery.completenessVerified,false);
  assert.equal(feedback.state,'unknown');assert.equal(feedback.partial,false);
  const saved=sourceEditorMetadata(sourceEditorMetadata(value));assert.equal(saved.sourceReadiness.state,'unknown');assert.equal(saved.sourceReadiness.completed,false);
  const completion=importCompletionFeedback({state:'done',photoPartial:false,result_id:'a'.repeat(32)},saved);
  assert.equal(completion.title,'Данные прочитаны · полнота фотографий не подтверждена');
  assert.equal(completion.retryPhotos,false);assert.equal(completion.photoPartial,false);
 }
});

test('the30photo retention cap is separate from actual observation and download completion',()=>{
 const value={sourceReadiness:source({observed:40,expected:40,limit:30})},feedback=sourceReadinessFeedback(value);
 assert.equal(feedback.partial,true);assert.match(feedback.message,/сверх лимита 10/);
 assert.equal(sourceEditorMetadata(value).sourceReadiness.gallery.retained,30);
});

test('thirteen identities with only three main links remain partial after save even when every thumbnail downloaded',()=>{
 const value={sourceReadiness:source({version:2,observed:13,expected:13,previewOnly:10,mainLinksObserved:3})};
 const saved=sourceEditorMetadata(sourceEditorMetadata(value)),album=saved.sourceReadiness.gallery;
 assert.equal(album.version,2);assert.equal(album.observed,13);assert.equal(album.missing,0);assert.equal(album.previewOnly,10);assert.equal(album.mainLinksObserved,3);
 assert.equal(album.state,'partial');assert.equal(album.completenessVerified,false);assert.equal(saved.sourceReadiness.completed,false);
 const completion=importCompletionFeedback({state:'done',photoPartial:false,result_id:'a'.repeat(32)},saved);
 assert.match(completion.message,/У 10 фото пока найдены только миниатюры/);assert.equal(completion.sourcePartial,true);
 assert.equal(completion.photoPartial,false);assert.equal(completion.retryPhotos,false);
});

test('unknown or malformed CIAN rendition roles cannot claim full album completeness',()=>{
 for(const roles of [{mainLinksObserved:3,previewOnly:0},{mainLinksObserved:14,previewOnly:0},{mainLinksObserved:13},{mainLinksObserved:13,previewOnly:-1}]){
  const saved=sourceEditorMetadata(sourceEditorMetadata({sourceReadiness:source({version:2,observed:13,expected:13,...roles})}));
  assert.equal(saved.sourceReadiness.gallery.state,'unknown');assert.equal(saved.sourceReadiness.gallery.completenessVerified,false);assert.equal(saved.sourceReadiness.completed,false);
  assert.equal(sourceReadinessFeedback(saved).state,'unknown');
 }
 const complete=sourceEditorMetadata({sourceReadiness:source({version:2,observed:13,expected:13,mainLinksObserved:13,previewOnly:0})});
 assert.equal(complete.sourceReadiness.gallery.state,'complete');assert.equal(complete.sourceReadiness.completed,true);
 assert.equal(sourceReadinessFeedback(complete).message,'');
});
