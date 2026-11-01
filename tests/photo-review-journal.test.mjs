import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import test from 'node:test';
import {mergePhotoReviewJournal} from '../web/photo-ui.mjs';

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
