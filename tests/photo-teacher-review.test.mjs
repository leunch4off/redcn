import test from 'node:test';
import assert from 'node:assert/strict';
import {teacherReviewed,teacherEvidence,isTeacherEvidence} from '../web/photo-teacher-review.mjs';
import {confirmedPhotoFeatures} from '../web/media-core.mjs';
import {resolvePhotoFurniture} from '../web/fact-evidence.mjs';
import {retainReviewedPhotoCandidates} from '../web/photo-ui.mjs';
const sha='a'.repeat(64),other='c'.repeat(64);
const make=()=>({selected:true,url:'https://images.cdn-cian.ru/images/test-1.jpg',analysis:{originalId:sha,candidates:[{id:'washer',label:'Стиральная машина'}]},teacherReview:{version:1,kind:'assistant-visual-review',originalId:sha,reviewId:'b'.repeat(64),datasetId:'d'.repeat(64),humanVerified:false,exhaustive:false,reviewedAt:'2026-11-03T00:00:00Z',features:['washer']}});
test('assistant visual review adds a generic fact with explicit provenance and no invented count',()=>{
 const item=make(),result=confirmedPhotoFeatures([item]);
 assert.deepEqual(result.features,['washer']);assert.deepEqual(result.featureCounts,{});
 assert.equal(result.photoEvidence[0].status,'assistant-reviewed');assert.equal(result.photoEvidence[0].humanVerified,false);
 assert.equal(isTeacherEvidence(result.photoEvidence[0]),true);
 assert.deepEqual(resolvePhotoFurniture({photoEvidence:result.photoEvidence},[['washer']]).features,['washer']);
 assert.equal(item.confirmed,undefined);
});
test('changed bytes, rejection or a claim of human verification invalidate an assistant review',()=>{
 const changed=make();changed.analysis.originalId=other;assert.equal(teacherReviewed(changed,'washer'),false);
 const rejected=make();rejected.rejected={washer:true};assert.deepEqual(confirmedPhotoFeatures([rejected]).features,[]);
 const fake=make();fake.teacherReview.humanVerified=true;assert.equal(teacherEvidence(fake,'washer'),null);
 const forged=teacherEvidence(make(),'washer');forged.evidence[0].photoId=other;assert.equal(isTeacherEvidence(forged),false);
});
test('reanalysis preserves reviewed candidates separately from local model predictions',()=>{
 const item=make(),next=retainReviewedPhotoCandidates(item.analysis,{originalId:sha,candidates:[]},{originalId:sha,teacherReview:item.teacherReview});
 assert.equal(next.candidates[0].reviewOrigin,'assistant-visual-review');assert.equal(next.candidates[0].autoEligible,false);
 assert.deepEqual(confirmedPhotoFeatures([{...item,analysis:next}]).features,['washer']);
});
