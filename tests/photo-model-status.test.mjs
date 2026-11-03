import test from 'node:test';
import assert from 'node:assert/strict';
import {learningPhotoModelInfo,LEARNING_PHOTO_MODELS} from '../web/learning-studio.mjs';

test('World v2 is a local proposal model with no automatic facts even when autoApply is requested',()=>{
 const value=learningPhotoModelInfo({model:'redcn-furniture-world-v2',enabled:true,autoApply:true});
 assert.equal(value.provider,'onnx-world');assert.equal(value.reviewOnly,true);
 assert.equal(value.acceptedClasses,0);assert.equal(value.automaticFacts,0);
 assert.equal(value.automaticApplication,false);assert.equal(value.automaticReviewCapture,true);
 assert.match(value.summary,/локальные предложения/);assert.match(value.summary,/требуют подтверждения/);
 assert.match(value.summary,/0 категорий для автоподтверждения/);
});

test('Configuration cannot claim active review capture when disabled or unchecked',()=>{
 for(const model of ['redcn-furniture-v1','redcn-furniture-world-v2'])for(const [enabled,autoApply] of [[false,true],[true,false],[false,false]]){
  const value=learningPhotoModelInfo({model,enabled,autoApply});
  assert.equal(value.automaticReviewCapture,false);assert.equal(value.automaticApplication,false);
  assert.equal(value.acceptedClasses,0);
 }
});

test('Unknown/custom local models do not inherit measured or accepted class counts',()=>{
 const value=learningPhotoModelInfo({model:'fixture-local-vlm:2b',enabled:true,autoApply:true});
 assert.equal(value.label,'fixture-local-vlm:2b');assert.equal(value.acceptedClasses,null);
 assert.equal(value.automaticFacts,null);assert.equal(value.automaticApplication,null);
 assert.match(value.summary,/не подтверждает наличие весов/);
 assert.deepEqual(LEARNING_PHOTO_MODELS.map(row=>row.id),['redcn-furniture-world-v2','redcn-furniture-v1']);
});
