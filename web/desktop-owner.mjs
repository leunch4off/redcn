/** An owner opens the existing server through its local CLI, never a public token endpoint. */
export async function bootstrapDesktopOwner({document:doc=globalThis.document,location:loc=globalThis.location,history:historyApi=globalThis.history,fetch:fetcher=globalThis.fetch,storage}={}){
 const match=/^#redcn-owner=([A-Za-z0-9_-]{43})$/.exec(loc.hash||'');
 if((loc.hash||'').startsWith('#redcn-owner='))historyApi.replaceState({},'',loc.pathname+loc.search);
 if(doc.querySelector('meta[name="redcn-hosting"]')?.content!=='local')return true;
 const meta=doc.querySelector('meta[name="redcn-token"]');
 if(meta?.content)return true;
 const scope=doc.querySelector('meta[name="redcn-workspace"]')?.content||'local';
 const key='redcn.owner-session.'+scope;
 try{storage=storage||globalThis.localStorage;}catch{storage=null;}
 let sessionPath='';try{sessionPath=storage?.getItem(key)||'';}catch{}
 if(match||/^\/owner\/[a-f0-9]{32}\/access$/.test(sessionPath)){
  // Replace the current history entry before asynchronous work. Fragments do
  // not enter HTTP request URLs/referrers or application diagnostic messages.
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),8000);
  try{
   if(match){
    const response=await fetcher('/api/local/desktop-access',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:match[1]}),credentials:'same-origin',cache:'no-store',signal:controller.signal});
    if(!response.ok)throw Error('Не удалось подтвердить владельца рабочего пространства');
    const result=await response.json();if(result?.ok!==true)throw Error('Неверный ответ локального сервера');
    sessionPath=result.sessionPath;
   }
   if(!/^\/owner\/[a-f0-9]{32}\/access$/.test(sessionPath))throw Error('Неверный путь локальной сессии');
   const response=await fetcher(sessionPath,{credentials:'same-origin',cache:'no-store',signal:controller.signal});
   if(!response.ok)throw Error('Владелец не подтверждён');
   const result=await response.json();
   if(!meta||!result||typeof result.token!=='string'||!/^[A-Za-z0-9_-]{43}$/.test(result.token)||(match&&result.token!==match[1]))throw Error('Неверный ответ локального сервера');
   meta.content=result.token;
   // Persist only the opaque cookie path. Cookies are not port-scoped, so the
   // owner credential itself must not be stored in a broad localhost cookie.
   try{storage?.setItem(key,sessionPath);}catch{}
   return true;
  }catch{
   try{storage?.removeItem(key);}catch{}
   return showDesktopOwnerGate(doc,'Локальный доступ не подтверждён. Откройте редактор с Mac ещё раз.');
  }finally{clearTimeout(timer);}
 }
 return showDesktopOwnerGate(doc,'Откройте редактор от владельца рабочего пространства.');
}

function showDesktopOwnerGate(doc,message){
 const existing=doc.getElementById('desktopOwnerGate');if(existing)return new Promise(()=>{});
 const gate=doc.createElement('section');gate.id='desktopOwnerGate';gate.setAttribute('role','status');
 Object.assign(gate.style,{position:'fixed',inset:'0',zIndex:'10000',display:'grid',placeContent:'center',gap:'18px',padding:'28px',background:'#07172c',color:'#f5f2e9',fontFamily:'system-ui,sans-serif'});
 const title=doc.createElement('h1');title.textContent='Локальный доступ';
 const text=doc.createElement('p');text.textContent=message;
 const help=doc.createElement('p');help.textContent='В Terminal на Mac выполните команду ниже. Она откроет ваш браузер и сохранит вход; секреты вводить или копировать не нужно.';
 const code=doc.createElement('code');code.textContent='cd ~/Downloads/redcn-v5 && .venv/bin/python scripts/open-local-workspace.py';code.style.overflowWrap='anywhere';
 const note=doc.createElement('p');note.textContent='Сессии телефонов и ваши карточки сохраняются.';
 gate.append(title,text,help,code,note);doc.body.append(gate);
 // Stop private application initialization. The visible gate is a completed
 // local access screen, not a server connection spinner or a retry loop.
 return new Promise(()=>{});
}
