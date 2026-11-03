// Only the public JavaScript module is cached. Private template requests stay
// outside this cache and are repeated for each initialized authenticated view.
export function createPhoneDesignerLoader({importModule,getState}={}){
 if(typeof importModule!=='function'||typeof getState!=='function')throw Error('Нужен загрузчик редактора');
 let cached=null,generation=0;
 const allowed=state=>state?.paired===true&&state?.canEdit===true;
 function load(){
  const state=getState();if(!allowed(state))return Promise.resolve(null);
  const epoch=state.epoch,version=generation;
  if(cached?.epoch===epoch&&cached.generation===version)return cached.promise;
  const record={epoch,generation:version,promise:null};
  record.promise=Promise.resolve().then(importModule).then(module=>{
   const current=getState();return generation===version&&current.epoch===epoch&&allowed(current)?module:null;
  },error=>{if(cached===record)cached=null;throw error;});
  cached=record;return record.promise;
 }
 function prewarm(){const state=getState();return !allowed(state)||state.saveData===true?Promise.resolve(null):load().catch(()=>null);}
 function invalidate(){generation++;cached=null;}
 return {load,prewarm,invalidate};
}
