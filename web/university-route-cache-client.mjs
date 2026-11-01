// Both editors share the authenticated API, never an external map-service key.
export function createUniversityRouteCacheCallbacks(api){
  if(typeof api!=='function')return {};
  const request=(action,route,fields={})=>api('university-route-cache/'+action,{origin:{latitude:fields.latitude??'',longitude:fields.longitude??'',address:fields.address??''},route});
  const point=(value,name)=>{const num=v=>typeof v==='number'?v:v!==''&&v!=null?Number(v):null;return {latitude:num(value?.latitude),longitude:num(value?.longitude),name,kind:'point',availability:'unknown',checkedAt:'',sourceUrl:''};};
  const calculateRoute=(route,fields={})=>api('route-provider/calculate',{origin:route.originPoint||point(fields,'Начало'),destination:route.destinationPoint||point(route,'Кампус'),mode:route.mode,useEntrances:route.useEntrances===true,shareCoordinates:true});
  return {calculateRoute,lookupCached:(row,fields)=>request('lookup',row,fields),rememberCached:(row,fields)=>request('remember',row,fields)};
}
