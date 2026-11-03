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

test('route calculation sends explicit points and mode through authenticated API, without private listing content',async()=>{
 const calls=[],callbacks=createUniversityRouteCacheCallbacks(async(path,payload)=>{calls.push({path,payload});return {ok:true};});
 const row={mode:'walk',originPoint:{latitude:55.7,longitude:37.5,kind:'building-entrance'},destinationPoint:{latitude:55.71,longitude:37.51,kind:'metro-exit'},useEntrances:true};
 await callbacks.calculateRoute(row,{photos:['private'],body:'private',sourceHtml:'private'});
 assert.equal(calls[0].path,'route-provider/calculate');assert.equal(calls[0].payload.shareCoordinates,true);assert.equal(calls[0].payload.useEntrances,true);assert.equal(calls[0].payload.mode,'walk');assert.equal(JSON.stringify(calls).includes('private'),false);
});
