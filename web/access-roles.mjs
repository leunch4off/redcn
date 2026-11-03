export const ACCESS_ROLES=Object.freeze(['owner','editor','viewer']);
export function phoneRoleCapabilities(state={}){
 const supported=ACCESS_ROLES.includes(state.role)&&state.capabilities&&typeof state.capabilities==='object'&&!Array.isArray(state.capabilities);
 const names=['readCards','editCards','importListings','readDossier','manageDossier','readDesign','manageDesign','manageSettings','manageDiagnostics','readDiagnostics','downloadDiagnostics','manageDictionary','proposeDictionary'];
 const result={supported:!!supported,role:supported?state.role:null};
 for(const key of names)result[key]=!!supported&&state.capabilities[key]===true;
 // Device grants are changed only through the authenticated desktop endpoint.
 result.manageAccess=false;return result;
}
export function accessSessionsSnapshot(value){
 if(!value||!Number.isSafeInteger(value.revision)||value.revision<0||!Array.isArray(value.sessions))throw Error('Сервер не поддерживает управление ролями. Обновите сервер Mac.');
 const ids=new Set(),sessions=value.sessions.map(row=>{
  if(!row||typeof row.id!=='string'||! /^[a-f0-9]{64}$/u.test(row.id)||ids.has(row.id)||!['owner','editor','viewer','unknown'].includes(row.role)||!Number.isFinite(row.expires))throw Error('Сервер вернул некорректный список устройств');
  ids.add(row.id);return {id:row.id,role:row.role,label:typeof row.label==='string'?row.label.slice(0,80):'',expires:row.expires,created:Number.isFinite(row.created)?row.created:null};
 });return {revision:value.revision,sessions};
}
export function accessRoleChange(state,row,role,label){
 const snapshot=accessSessionsSnapshot(state);
 if(!snapshot.sessions.some(item=>item.id===row.id)||!ACCESS_ROLES.includes(role))throw Error('Обновите список и выберите известную роль');
 if(typeof label!=='string'||label.length>80||/[\u0000-\u001f\u007f]/u.test(label))throw Error('Название устройства: до 80 символов без управляющих знаков');
 return {action:'set-role',sessionId:row.id,role,label:label.trim(),expectedRevision:snapshot.revision};
}
export function setupAccessRoles({mount,api,toast=()=>{}}={}){
 if(!mount)return null;
 const make=(tag,text='',className='')=>{const node=document.createElement(tag);node.textContent=text;if(className)node.className=className;return node;};
 const shell=make('details','','access-roles'),heading=make('summary','Доступ коллег с телефонов'),body=make('div','','access-roles-body'),status=make('p','Откройте блок, чтобы загрузить устройства с сервера Mac.','muted'),reload=make('button','Обновить список','btn'),list=make('div','','access-roles-list');
 status.setAttribute('role','status');status.setAttribute('aria-live','polite');reload.type='button';
 const note=make('p','Читатель: клиентские карточки и фото. Редактор: объявления, дизайн и общий словарь. Владелец: также приватные договоры, заметки, резервные копии и настройки сервера. Управление доступом выполняется здесь, на Mac. Это роли общей рабочей базы.','muted');
 body.append(note,reload,status,list);shell.append(heading,body);mount.replaceChildren(shell);
 let state=null,epoch=0,busy=false,destroyed=false;
 const message=text=>{status.textContent=text;toast(text);};
 function setBusy(value){busy=value;for(const node of body.querySelectorAll('button,input,select'))node.disabled=value;}
 function draw(){
  list.replaceChildren();status.textContent='Активных устройств: '+state.sessions.length+' · версия '+state.revision+'. Новые и ранее подключённые телефоны получают роль редактора.';
  for(const row of state.sessions){
   const item=make('article','','access-role-device'),name=make('input'),role=make('select'),save=make('button','Сохранить права','btn'),revoke=make('button','Отключить устройство','btn');name.value=row.label;name.maxLength=80;name.placeholder='Например, iPhone коллеги';name.setAttribute('aria-label','Название устройства '+row.id.slice(0,8));role.setAttribute('aria-label','Роль устройства '+row.id.slice(0,8));
   for(const [value,label]of[['viewer','Читатель'],['editor','Редактор'],['owner','Владелец'],...(row.role==='unknown'?[['unknown','Неизвестная · доступ закрыт']]:[])]){const option=make('option',label);option.value=value;role.append(option);}role.value=row.role;save.type=revoke.type='button';
   item.append(make('strong',(row.label||'Телефон')+' · '+row.id.slice(0,8)),make('p','Сессия до '+new Date(row.expires*1000).toLocaleString('ru-RU'),'muted'),name,role,save,revoke);list.append(item);
   save.onclick=()=>{let data;try{data=accessRoleChange(state,row,role.value,name.value);}catch(error){message(error.message);return;}change(data);};
   revoke.onclick=()=>change({action:'revoke',sessionId:row.id,expectedRevision:state.revision});
  }
  if(!state.sessions.length)list.append(make('p','Активных телефонов нет. Подключите телефон кодом через «Телефон».','muted'));
 }
 async function refresh(){if(busy||destroyed)return false;const ticket=++epoch;setBusy(true);try{if(typeof api!=='function')throw Error('Управление доступом работает в редакторе на сервере Mac');const value=accessSessionsSnapshot(await api('phone-sessions'));if(ticket!==epoch||destroyed)return false;state=value;draw();return true;}catch(error){if(ticket===epoch&&!destroyed){state=null;list.replaceChildren();message('Не удалось получить права: '+error.message+' Если сервер ещё не обновлён, роли недоступны; существующие права не менялись.');}return false;}finally{if(ticket===epoch&&!destroyed)setBusy(false);}}
 async function change(data){if(busy||!state||destroyed)return false;const ticket=++epoch;setBusy(true);try{const value=accessSessionsSnapshot(await api('phone-sessions',data));if(ticket!==epoch||destroyed)return false;state=value;draw();message(data.action==='revoke'?'Устройство отключено. Для доступа потребуется новый код с Mac.':'Права сохранены. Они проверяются сервером при следующем запросе телефона.');return true;}catch(error){if(ticket===epoch&&!destroyed){state=null;list.replaceChildren();message(error.message+' Обновите список перед следующим изменением.');}return false;}finally{if(ticket===epoch&&!destroyed)setBusy(false);}}
 reload.onclick=refresh;shell.addEventListener('toggle',()=>{if(shell.open&&!state)refresh();});
 return {refresh,destroy(){destroyed=true;epoch++;mount.replaceChildren();}};
}
