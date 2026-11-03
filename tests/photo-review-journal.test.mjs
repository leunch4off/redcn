import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import test from 'node:test';
import {mergePhotoReviewJournal,retainReviewedPhotoCandidates} from '../web/photo-ui.mjs';
import {confirmedPhotoFeatures} from '../web/media-core.mjs';

const sha=value=>createHash('sha256').update(value).digest('hex');
const url='https://images.cdn-cian.ru/images/synthetic-review.jpg';
function item(original=sha('original')){
 const feature='fridge',score=.95;
 return {url,analysis:{status:'review',provider:'yolox-trained',model:'redcn-furniture-v1',
  originalId:original,autoApply:true,unknownIds:[],candidates:[{id:feature,label:'Холодильник',confidence:score,
   autoEligible:false,automaticSuggestion:true,reviewId:sha(original+'|'+feature),requiresConfirmation:true,
   evidence:[{kind:'object-detection',provider:'yolox-trained',model:'redcn-furniture-v1',source:'local-photo',
    originalId:original,photoId:original,confidence:score,region:[.1,.1,.3,.4]}]}]},confirmed:{},rejected:{}};
}

test('direct analysis creates a real pending entry and repeated scans deduplicate',()=>{
 const photo=item(),journal=mergePhotoReviewJournal(null,[photo],{now:'first'});
 assert.equal(journal.entries.length,1);assert.equal(journal.pending,1);assert.equal(journal.confirmed,0);
 assert.equal(journal.entries[0].count,null);assert.equal(journal.entries[0].createdAt,'first');
 assert.deepEqual(photo.confirmed,{});assert.equal(photo.analysis.candidates[0].autoEligible,false);
 const again=mergePhotoReviewJournal(journal,[photo],{now:'second',refresh:true});
 assert.equal(again.entries.length,1);assert.equal(again.entries[0].createdAt,'first');assert.equal(again.entries[0].lastSeenAt,'second');
 assert.equal(again.added,1);
 const snapshot=mergePhotoReviewJournal(again,[photo],{now:'third'});
 assert.deepEqual(snapshot,again);
 snapshot.entries[0].evidence[0].region[0]=.2;
 assert.equal(photo.analysis.candidates[0].evidence[0].region[0],.1);
});

test('World proposals retain their actual provider and new categories only in the review journal',()=>{
 for(const feature of ['fridge','stove','cabinet']){
  const photo=item(),analysis=photo.analysis,candidate=analysis.candidates[0];
  analysis.model='redcn-furniture-world-v2';analysis.provider='onnx-world';
  candidate.id=feature;candidate.label=feature;candidate.reviewId=sha(analysis.originalId+'|'+feature);
  Object.assign(candidate.evidence[0],{model:analysis.model,provider:analysis.provider});
  const before=structuredClone(photo),journal=mergePhotoReviewJournal(null,[photo]);
  assert.equal(journal.pending,1);assert.equal(journal.confirmed,0);
  assert.equal(journal.entries[0].featureId,feature);
  assert.equal(journal.entries[0].provider,'onnx-world');assert.equal(journal.entries[0].model,'redcn-furniture-world-v2');
  assert.equal(journal.entries[0].count,null);assert.equal(journal.entries[0].reason,'model-unaccepted');
  assert.deepEqual(photo,before);assert.deepEqual(confirmedPhotoFeatures([photo]).features,[]);
  const forged=structuredClone(photo);forged.analysis.candidates[0].evidence[0].provider='yolox-trained';
  assert.equal(mergePhotoReviewJournal(null,[forged]),null);
  const crossed=structuredClone(photo);crossed.analysis.provider='yolox-trained';
  crossed.analysis.candidates[0].evidence[0].provider='yolox-trained';
  assert.equal(mergePhotoReviewJournal(null,[crossed]),null);
 }
});

test('manual rejection and confirmation survive scans, URL aliases and global rejection',()=>{
 const photo=item(),journal=mergePhotoReviewJournal(null,[photo],{now:'first'});
 photo.rejected.fridge=true;
 const rejected=mergePhotoReviewJournal(journal,[photo]);
 assert.equal(rejected.pending,0);assert.equal(rejected.rejected,1);
 delete photo.rejected.fridge;photo.confirmed.fridge={count:1};
 const confirmed=mergePhotoReviewJournal(rejected,[photo]);
 assert.equal(confirmed.confirmed,1);assert.equal(confirmed.entries[0].count,null);
 const alias={...item(),url:'https://images.cdn-cian.ru/images/alias.jpg',rejected:{fridge:true}};
 assert.equal(mergePhotoReviewJournal(confirmed,[photo,alias]).rejected,1);
 assert.equal(mergePhotoReviewJournal(confirmed,[photo],{rejectedFeatures:['fridge']}).rejected,1);
 assert.equal(mergePhotoReviewJournal(rejected,[item()]).rejected,1);
});

test('replacement original never inherits prior decisions',()=>{
 const old=item(),journal=mergePhotoReviewJournal(null,[old],{now:'first'}),replacement=item(sha('new original'));
 const updated=mergePhotoReviewJournal(journal,[replacement]);
 assert.equal(updated.entries.length,2);assert.equal(updated.pending,1);
 assert.equal(updated.entries.find(row=>row.originalId===old.analysis.originalId).state,'stale');
 old.confirmed.fridge={count:1};const accepted=mergePhotoReviewJournal(journal,[old]);
 const replaced=mergePhotoReviewJournal(accepted,[replacement]);
 assert.equal(replaced.entries.find(row=>row.originalId===replacement.analysis.originalId).state,'pending');
 assert.deepEqual(replacement.confirmed,{});
});

test('suggestion flags alone cannot create an unbound journal entry',()=>{
 for(const mutate of [photo=>photo.analysis.autoApply=false,photo=>photo.analysis.model='untrusted',
  photo=>photo.analysis.candidates[0].confidence=.3,photo=>photo.analysis.candidates[0].reviewId='invalid',
  photo=>photo.analysis.unknownIds=['fridge'],photo=>photo.analysis.candidates[0].evidence[0].originalId=sha('other'),
  photo=>photo.analysis.candidates[0].evidence[0].region=[0,0,2,2]]){
  const photo=item();mutate(photo);assert.equal(mergePhotoReviewJournal(null,[photo]),null);
 }
 assert.equal(mergePhotoReviewJournal({version:1,entries:[null,{},'bad']},[]).entries.length,0);
});

test('journal is bounded and keeps the currently observed original first',()=>{
 const rows=Array.from({length:600},(_,i)=>{const digest=sha('old'+i);return {id:sha(digest+'|fridge'),originalId:digest,featureId:'fridge',sourceUrl:url+'?i='+i,state:'pending'};});
 const photo=item(),journal=mergePhotoReviewJournal({version:1,entries:rows},[photo]);
 assert.equal(journal.entries.length,600);assert.equal(journal.entries[0].originalId,photo.analysis.originalId);
});

test('same-original reanalysis retains human decisions without retaining guesses or attributing them to the new model',()=>{
 const photo=item(),originalId=photo.analysis.originalId;
 photo.selected=true;photo.confirmed={fridge:{count:1}};photo.rejected={sofa:true};
 photo.analysis.candidates.push({...photo.analysis.candidates[0],id:'sofa',label:'Диван'},
  {...photo.analysis.candidates[0],id:'desk',label:'Письменный стол'});
 const previous=structuredClone(photo.analysis),current={...photo.analysis,engine:'new-model-run',candidates:[]};
 photo.analysis=retainReviewedPhotoCandidates(previous,current,{originalId,confirmed:photo.confirmed,rejected:photo.rejected});
 assert.deepEqual(photo.analysis.candidates.map(row=>row.id),['fridge','sofa']);
 assert.ok(photo.analysis.candidates.every(row=>row.reviewOrigin==='operator-kept'&&row.reviewOriginalId===originalId&&row.autoEligible===false&&row.automaticSuggestion===false));
 const facts=confirmedPhotoFeatures([photo]);
 assert.deepEqual(facts.features,['fridge']);assert.deepEqual(facts.featureCounts,{fridge:1});
 assert.equal(facts.photoEvidence.length,1);assert.equal(facts.photoEvidence[0].engine,'operator-kept');
 assert.equal(facts.photoEvidence[0].status,'confirmed');assert.equal(facts.photoEvidence[0].originalId,originalId);
 assert.deepEqual(current.candidates,[]);assert.equal(previous.candidates[0].reviewOrigin,undefined);
 assert.deepEqual(retainReviewedPhotoCandidates(previous,current,{originalId:sha('other'),confirmed:photo.confirmed,rejected:photo.rejected}),current);
 assert.deepEqual(retainReviewedPhotoCandidates(previous,{...current,originalId:sha('new')},{originalId:sha('new'),confirmed:photo.confirmed,rejected:photo.rejected}).candidates,[]);
 const fresh={...current,candidates:[{id:'fridge',confidence:.2}]};
 const repeated=retainReviewedPhotoCandidates(previous,fresh,{originalId,confirmed:photo.confirmed,rejected:photo.rejected});
 assert.equal(repeated.candidates.filter(row=>row.id==='fridge').length,1);
 assert.equal(repeated.candidates[0].reviewOrigin,undefined);
});
