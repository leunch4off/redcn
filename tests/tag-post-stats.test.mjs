import test from 'node:test';
import assert from 'node:assert/strict';
import {postStatsDraft} from '../web/tag-post-stats.mjs';

test('one publication carries its whole tag set and explicit approximate flag',()=>{
 const row=postStatsDraft({postId:'post-a',observedAt:'2026-10-30',views:'100',tags:'#租房 #莫斯科',metro:'示例站',rooms:'2',area:'52.4',rent:'85000',approximate:true});
 assert.deepEqual(row.tags,['#租房','#莫斯科']);assert.equal(row.views,100);assert.equal(row.facts.area,52.4);assert.equal(row.approximate,true);
});
test('blank room count is not silently converted to a studio',()=>{
 assert.throws(()=>postStatsDraft({views:'100',rooms:'',area:'50',rent:'80000'}));
 const row=postStatsDraft({views:'0',rooms:'0',area:'20',rent:'40000'});assert.equal(row.views,0);assert.equal(row.facts.rooms,0);
});
