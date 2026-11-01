import test from 'node:test';
import assert from 'node:assert/strict';
import {publicationReviewActions,publicationReviewMutation} from '../web/publication-review.mjs';
const state={revision:4,savedUpdated:1234,state:'pending',canReview:true};
test('review buttons follow server receipt and never allow self or stale approval',()=>{
 assert.deepEqual(publicationReviewActions(state),{submit:true,approve:true,reject:true});
 for(const input of [{...state,canReview:false},{...state,state:'stale'},{...state,state:'approved'},{...state,state:'rejected'},{}])assert.equal(publicationReviewActions(input).approve,false);
 assert.deepEqual(publicationReviewMutation(state,'approve',' Проверено '),{action:'approve',expectedRevision:4,expectedUpdated:1234,comment:'Проверено'});
 assert.throws(()=>publicationReviewMutation({...state,canReview:false},'approve'));assert.throws(()=>publicationReviewMutation({...state,state:'stale'},'approve'));assert.throws(()=>publicationReviewMutation(state,'reject',''));
 assert.equal(publicationReviewMutation(state,'reject','Проверьте адрес').comment,'Проверьте адрес');assert.throws(()=>publicationReviewMutation(state,'publish'));
});
