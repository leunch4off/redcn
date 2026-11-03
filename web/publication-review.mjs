import {hasOwnKey} from './own-property.mjs';
export function publicationReviewActions(snapshot={}){
 const state=snapshot.state,valid=Number.isSafeInteger(snapshot.revision)&&snapshot.revision>=0&&Number.isFinite(snapshot.savedUpdated);
 return {submit:valid,approve:valid&&state==='pending'&&snapshot.canReview===true,reject:valid&&state==='pending'&&snapshot.canReview===true};
}
export function publicationReviewMutation(snapshot,action,comment=''){
 const allowed=publicationReviewActions(snapshot);
 if(!hasOwnKey(allowed,action)||!allowed[action])throw Error('Обновите состояние проверки; согласование доступно другому участнику для актуального поста');
 if(typeof comment!=='string'||comment.length>1000)throw Error('Комментарий: до 1000 символов');
 if(action==='reject'&&!comment.trim())throw Error('Укажите причину возврата на доработку');
 return {action,expectedRevision:snapshot.revision,expectedUpdated:snapshot.savedUpdated,comment:comment.trim()};
}
export function setupPublicationReview({mount,api,getCardId=()=>null,toast=()=>{}}={}){
 if(!mount)return null;
 const el=(tag,text='',className='')=>{const node=document.createElement(tag);node.textContent=text;if(className)node.className=className;return node;};
 const panel=el('details','','publication-review'),heading=el('summary','Проверка сохранённого поста коллегой'),body=el('div','','publication-review-body'),status=el('p','Сохраните изменения карточки перед отправкой на проверку.','muted'),note=el('p','Это отдельная проверка сохранённого текста и полей. Изменение клиентской версии делает согласование неактуальным. Другой участник подтверждается сессией сервера; размещение автоматически не запускается.','muted'),comment=el('textarea'),actions=el('div','','publication-review-actions'),refreshButton=el('button','Обновить проверку','btn'),submit=el('button','Отправить сохранённый пост','btn'),approve=el('button','Одобрить пост','btn'),reject=el('button','Вернуть на доработку','btn'),history=el('div','','publication-review-history');
 status.setAttribute('role','status');status.setAttribute('aria-live','polite');comment.rows=3;comment.maxLength=1000;comment.placeholder='Комментарий или причина возврата';comment.setAttribute('aria-label','Комментарий к проверке поста');for(const button of [refreshButton,submit,approve,reject])button.type='button';actions.append(refreshButton,submit,approve,reject);body.append(note,status,comment,actions,history);panel.append(heading,body);mount.replaceChildren(panel);
 let snapshot=null,loadedId=null,epoch=0,busy=false,destroyed=false;
 const message=text=>{status.textContent=text;toast(text);},current=(id,ticket)=>!destroyed&&ticket===epoch&&getCardId()===id;
 function permissions(){const flags=publicationReviewActions(snapshot||{});submit.disabled=busy||!flags.submit;approve.disabled=busy||!flags.approve;reject.disabled=busy||!flags.reject;refreshButton.disabled=busy;comment.disabled=busy;}
 function draw(){
  const names={'not-submitted':'Ещё не отправлен',pending:'Ожидает другого участника',approved:'Одобрен коллегой',rejected:'Возвращён на доработку',stale:'Клиентская версия изменилась: отправьте текущую сохранённую версию повторно'};
  status.textContent=(names[snapshot.state]||'Состояние неизвестно')+' · версия проверки '+snapshot.revision+'.'+(snapshot.state==='pending'&&!snapshot.canReview?' Собственную отправку одобряет другая сессия коллеги.':'');history.replaceChildren();
  if(snapshot.review?.comment)history.append(el('p','Комментарий: '+snapshot.review.comment));
  for(const row of (snapshot.history||[]).slice(0,12))history.append(el('p','v'+row.revision+' · '+row.at+' · '+({submit:'Отправлено',approve:'Одобрено',reject:'На доработку'}[row.action]||row.action)+' · '+(row.actor?.startsWith('phone:')?'Телефон '+row.actor.slice(6,14):row.actor||'Участник')+(row.comment?' · '+row.comment:''),'muted'));
  permissions();
 }
 async function refresh(){if(busy||destroyed)return false;const id=getCardId(),ticket=++epoch;snapshot=null;loadedId=null;history.replaceChildren();permissions();if(!/^[a-f0-9]{32}$/u.test(id||'')){status.textContent='Сначала сохраните карточку на сервере Mac.';return false;}busy=true;permissions();try{if(typeof api!=='function')throw Error('Подключитесь к серверу Mac');const value=await api('publication-review/'+id);if(!current(id,ticket))return false;if(value.cardId!==id||!publicationReviewActions(value).submit)throw Error('Сервер ещё не поддерживает проверку постов');snapshot=value;loadedId=id;draw();return true;}catch(error){if(current(id,ticket))message('Проверка недоступна: '+error.message);return false;}finally{if(ticket===epoch&&!destroyed){busy=false;permissions();}}}
 async function change(action){if(busy||!snapshot||destroyed)return false;const id=getCardId();if(id!==loadedId){clear();return message('Открыта другая карточка. Обновите проверку.');}let data;try{data=publicationReviewMutation(snapshot,action,comment.value);}catch(error){message(error.message);return false;}const ticket=++epoch;busy=true;permissions();try{const value=await api('publication-review/'+id,data);if(!current(id,ticket))return false;snapshot=value;comment.value='';draw();return true;}catch(error){if(current(id,ticket)){snapshot=null;loadedId=null;history.replaceChildren();message(error.message+' Обновите проверку перед повторным действием.');}return false;}finally{if(ticket===epoch&&!destroyed){busy=false;permissions();}}}
 function clear(){epoch++;snapshot=null;loadedId=null;busy=false;comment.value='';history.replaceChildren();status.textContent='Сохраните изменения карточки и обновите проверку.';permissions();}
 refreshButton.onclick=refresh;submit.onclick=()=>change('submit');approve.onclick=()=>change('approve');reject.onclick=()=>change('reject');panel.addEventListener('toggle',()=>{if(panel.open&&!snapshot)refresh();});permissions();
 return {refresh,clear,destroy(){destroyed=true;epoch++;mount.replaceChildren();}};
}
