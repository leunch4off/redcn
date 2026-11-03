import test from 'node:test';
import assert from 'node:assert/strict';
import {parseTrainingBoxes} from '../web/photo-training.mjs';

test('photo labels preserve explicit boxes and manually linked instance IDs',()=>{
 assert.deepEqual(parseTrainingBoxes('0.1 0.2 0.3 0.4 chair-one\n0.6,0.1,0.2,0.2'),[
  {region:[.1,.2,.3,.4],instanceId:'chair-one',linkConfirmed:true},{region:[.6,.1,.2,.2]}]);
 assert.deepEqual(parseTrainingBoxes(''),[]);
});
test('photo labels reject negative, empty, nonfinite and out-of-frame regions',()=>{
 for(const text of ['0 0 0 .5','-.1 0 .5 .5','.9 0 .2 .2','NaN 0 .5 .5','0 0 .5','.1 .1 .1 .1 bad/id'])assert.throws(()=>parseTrainingBoxes(text));
});
