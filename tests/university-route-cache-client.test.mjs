import test from 'node:test';
import assert from 'node:assert/strict';
import {createUniversityRouteCacheCallbacks} from '../web/university-route-cache-client.mjs';

test('both editors reuse existing authenticated api and send only origin and route fields',async()=>{
 const calls=[],api=async(path,payload)=>{calls.push({path,payload});return {hit:true,route:payload.route};},callbacks=createUniversityRouteCacheCallbacks(api),row={campusId:'msu-physics',mode:'public'},fields={latitude:55.7,longitude:37.5,address:'Москва, адрес',photos:['private-photo-reference'],source:'unneeded source text'};
 const response=await callbacks.lookupCached(row,fields);await callbacks.rememberCached(row,fields);
 assert.equal(response.hit,true);assert.deepEqual(calls.map(value=>value.path),['university-route-cache/lookup','university-route-cache/remember']);
 for(const call of calls)assert.deepEqual(call.payload,{origin:{latitude:55.7,longitude:37.5,address:'Москва, адрес'},route:row});
 assert.deepEqual(createUniversityRouteCacheCallbacks(null),{});
});
