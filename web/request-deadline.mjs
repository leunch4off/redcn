// A request owns its timer and abort listener, including response-body reads.
// Older Safari has AbortController but no static AbortSignal timeout/any APIs.
function rdAbortReason(){const error=Error('The request was cancelled.');error.name='AbortError';return error;}
function rdTimeoutReason(){const error=Error('The request timed out.');error.name='TimeoutError';return error;}

export async function withRequestDeadline(milliseconds,operation,{signal:externalSignal}={}){
  if(typeof milliseconds!=='number'||!Number.isFinite(milliseconds)||milliseconds<0||milliseconds>2147483647)throw RangeError('Invalid request deadline.');
  if(typeof operation!=='function')throw TypeError('A request operation is required.');
  if(externalSignal!=null&&(typeof externalSignal.addEventListener!=='function'||typeof externalSignal.removeEventListener!=='function'))throw TypeError('Invalid cancellation signal.');
  let controller=null;try{if(typeof AbortController==='function')controller=new AbortController();}catch{}
  let timer=null,disposed=false,aborted=false,timedOut=false,reason,listening=false,rejectAbort;
  const cancellation=new Promise((_,reject)=>{rejectAbort=reject;});
  const detach=()=>{if(listening){externalSignal.removeEventListener('abort',onExternalAbort);listening=false;}};
  const clearTimer=()=>{if(timer!==null){clearTimeout(timer);timer=null;}};
  function abort(value,timeout=false){
    if(disposed||aborted)return;aborted=true;timedOut=timeout;reason=value;clearTimer();detach();
    // Reject our promise first: old WebKit may replace the controller's reason
    // with a generic AbortError while it aborts a fetch or response body.
    rejectAbort(reason);
    if(controller)try{controller.abort(reason);}catch{try{controller.abort();}catch{}}
  }
  function onExternalAbort(){abort(externalSignal.reason===undefined?rdAbortReason():externalSignal.reason);}
  const scope={signal:controller?.signal||externalSignal||undefined,get aborted(){return aborted;},get timedOut(){return timedOut;},get reason(){return reason;},throwIfAborted(){if(aborted)throw reason;}};
  try{
    if(externalSignal?.aborted)onExternalAbort();
    else{
      if(externalSignal){listening=true;externalSignal.addEventListener('abort',onExternalAbort,{once:true});if(externalSignal.aborted)onExternalAbort();}
      if(!aborted)timer=setTimeout(()=>abort(rdTimeoutReason(),true),Math.ceil(milliseconds));
    }
    const result=Promise.resolve().then(()=>{scope.throwIfAborted();return operation(scope.signal,scope);});
    return await Promise.race([cancellation,result]);
  }finally{disposed=true;clearTimer();detach();}
}
