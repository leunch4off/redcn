// A saved-card URL is an explicit choice; it must never open an unrelated draft.
// Samples are marked explicitly rather than guessed from an apartment's content.
export function editorStartup({local=false,pathname='/',draft=null}={}){
  const match=local&&String(pathname).match(/^\/listing\/([a-f0-9]{32})$/);
  if(match)return {kind:'remote',resultId:match[1],fields:{},draft:null};
  if(!draft||typeof draft!=='object'||Array.isArray(draft))return {kind:'empty',resultId:null,fields:{},draft:null};
  if(draft.version===2)return {kind:'draft',resultId:null,fields:draft.fields&&typeof draft.fields==='object'&&!Array.isArray(draft.fields)?draft.fields:{},draft};
  return {kind:'draft',resultId:null,fields:draft,draft:null};
}
