import test from 'node:test';
import assert from 'node:assert/strict';
import {confirmedPhotoFeatures} from '../web/media-core.mjs';
test('photo candidates require confirmation and overlapping views never add counts',()=>{
 const analysis={originalId:'id',engine:'test',candidates:[{id:'chairs',count:2,confidence:.9,evidence:[{text:'2 chairs'}]},{id:'fridge',confidence:.6}]};
 const items=[{url:'photo1',selected:true,analysis,confirmed:{chairs:{count:2}}},{url:'photo2',selected:true,analysis,confirmed:{chairs:{count:2}}},{url:'excluded',selected:false,analysis,confirmed:{fridge:{count:1}}}];
 const result=confirmedPhotoFeatures(items);
 assert.deepEqual(result.features,['chairs']);assert.deepEqual(result.featureCounts,{chairs:2});assert.equal(result.photoEvidence.length,2);assert.ok(result.photoEvidence.every(row=>row.status==='confirmed'));
});
test('confirmed category without counted objects leaves count unknown',()=>{
 assert.deepEqual(confirmedPhotoFeatures([{selected:true,analysis:{candidates:[{id:'oven',count:null}]},confirmed:{oven:{count:null}}}]).featureCounts,{});
});
