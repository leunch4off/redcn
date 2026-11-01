// Both editors share the authenticated API, never an external map-service key.
export function createUniversityRouteCacheCallbacks(api){
  if(typeof api!=='function')return {};
  const request=(action,route,fields={})=>api('university-route-cache/'+action,{origin:{latitude:fields.latitude??'',longitude:fields.longitude??'',address:fields.address??''},route});
  return {lookupCached:(row,fields)=>request('lookup',row,fields),rememberCached:(row,fields)=>request('remember',row,fields)};
}
