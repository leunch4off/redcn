const prClone=value=>JSON.parse(JSON.stringify(value));
const prId=card=>typeof card?.id==='string'&&/^[a-f0-9]{32}$/.test(card.id)?card.id:'';
const prStamp=card=>typeof card?.updated==='number'&&Number.isFinite(card.updated)&&card.updated>=0?card.updated:null;
const prFieldsKey=entry=>JSON.stringify(['universityRoutes','universities','university','campus','uniTime','uniMode','route','latitude','longitude','address','city','metro'].map(key=>entry?.card?.fields?.[key]));

export function createPhoneRouteDraftStore(){
  const entries=new Map();
  const fields=entry=>({...entry.card.fields,...(entry.draft!==null?{universityRoutes:prClone(entry.draft)}:{})});
  function receive(card){
    const id=prId(card);if(!id)return null;let entry=entries.get(id);const stamp=prStamp(card);
    if(!entry){entry={id,card:prClone(card),baseUpdated:stamp,draft:null,dirty:false,revision:0,saving:false,rebasing:false,conflict:false,error:'',remote:null};entries.set(id,entry);return entry;}
    if(stamp===null||entry.baseUpdated!==null&&stamp<entry.baseUpdated)return entry;
    if(entry.dirty||entry.saving){if(stamp!==entry.baseUpdated&&(!entry.remote||stamp>=prStamp(entry.remote))){entry.remote=prClone(card);entry.conflict=true;}}
    else{entry.card=prClone(card);entry.baseUpdated=stamp;entry.remote=null;entry.conflict=false;}
    return entry;
  }
  function edit(id,routes){const entry=entries.get(id);if(!entry||!Array.isArray(routes))return null;if(entry.dirty&&JSON.stringify(entry.draft)===JSON.stringify(routes))return entry;entry.draft=prClone(routes);entry.dirty=true;entry.revision++;entry.error='';return entry;}
  function begin(id,routes){const entry=entries.get(id);if(!entry||!entry.dirty||entry.saving||entry.rebasing||entry.baseUpdated===null)return null;edit(id,routes);entry.saving=true;entry.error='';return {id,entry,revision:entry.revision,routes:prClone(entry.draft),expectedUpdated:entry.baseUpdated};}
  function accept(snapshot,card){
    const entry=entries.get(snapshot.id);if(entry!==snapshot.entry)return null;
    if(prId(card)!==entry.id||prStamp(card)===null||prStamp(card)<snapshot.expectedUpdated)throw Error('Неверная версия ответа Mac.');
    const latest=entry.remote&&prStamp(entry.remote)>prStamp(card)?entry.remote:card;
    entry.card=prClone(latest);entry.baseUpdated=prStamp(card);entry.saving=false;entry.error='';
    if(entry.revision===snapshot.revision){entry.dirty=false;entry.draft=null;entry.baseUpdated=prStamp(latest);}
    entry.remote=prStamp(latest)>entry.baseUpdated?prClone(latest):null;entry.conflict=!!entry.remote;return entry;
  }
  function fail(snapshot,error){const entry=entries.get(snapshot.id);if(entry!==snapshot.entry)return null;entry.saving=false;entry.error=String(error?.message||'Не удалось сохранить. Черновик оставлен.').slice(0,500);entry.conflict=entry.conflict||error?.status===409;return entry;}
  function rebase(id,card){const entry=entries.get(id);if(!entry||prId(card)!==id||prStamp(card)===null)throw Error('Не получена версия карточки.');const latest=entry.remote&&prStamp(entry.remote)>prStamp(card)?entry.remote:card;if(entry.baseUpdated!==null&&prStamp(latest)<entry.baseUpdated)throw Error('Mac вернул старую версию.');entry.card=prClone(latest);entry.baseUpdated=prStamp(latest);entry.remote=null;entry.conflict=false;entry.error='';return entry;}
  return {receive,edit,begin,accept,fail,rebase,fields,get:id=>entries.get(id),clear:()=>entries.clear()};
}

export function setupPhoneUniversityRoutes({mount,getCard=()=>null,api,onSaved=()=>{},toast=()=>{},loadModule=()=>import('/phone/routes.mjs')}={}){
  if(!mount)return {cardChanged:()=>{},clear:()=>{},destroy:()=>{}};
  const make=(tag,text='',className='')=>{const node=document.createElement(tag);node.textContent=text;if(className)node.className=className;return node;};
  const store=createPhoneRouteDraftStore(),details=make('details','','phone-route-controller'),summary=make('summary','Вузы, маршруты и карты'),status=make('p','','muted'),body=make('div'),actions=make('div','','phone-route-controller-actions'),save=make('button','Сохранить маршруты','btn primary'),rebase=make('button','Обновить версию, сохранив мой черновик','btn');
  save.type=rebase.type='button';status.setAttribute('role','status');status.setAttribute('aria-live','polite');actions.append(save,rebase);details.append(summary,status,body,actions);mount.replaceChildren(details);
  let active='',authEpoch=0,viewEpoch=0,destroyed=false,widget=null,widgetId='',moduleReady=null,loading=false,renderedKey='',loadError='';
  function update(message=''){
    if(destroyed)return;const entry=store.get(active);save.disabled=!api||!entry?.dirty||entry.saving||entry.rebasing||entry.baseUpdated===null;rebase.hidden=!entry?.conflict;rebase.disabled=!api||entry?.saving||entry?.rebasing;
    status.textContent=message||(!entry?'Откройте карточку квартиры.':loading?'Загружаем форму маршрутов…':loadError?loadError:entry.saving?'Сохраняем на Mac…':entry.rebasing?'Получаем версию Mac…':entry.error?entry.error:entry.conflict?'На Mac новая версия. Ваш черновик сохранён.':entry.dirty?'Маршруты изменены. Сохраните их отдельно.':widget?'Маршруты берутся из этой карточки.':'Откройте блок для настройки маршрутов.');
  }
  function removeWidget(){widget?.destroy();widget=null;widgetId='';renderedKey='';body.replaceChildren();}
  async function open(){
    if(destroyed||!details.open)return;const entry=store.get(active);if(!entry)return;
    if(widget&&widgetId===active||loading)return;const id=active,auth=authEpoch,view=++viewEpoch;loading=true;loadError='';update();
    try{
      if(!moduleReady)moduleReady=Promise.resolve().then(loadModule).catch(error=>{moduleReady=null;throw error;});
      const module=await moduleReady;if(destroyed||auth!==authEpoch||view!==viewEpoch||active!==id||!details.open)return;
      if(typeof module.setupUniversityRoutes!=='function')throw Error('Форма маршрутов недоступна.');removeWidget();
      widget=module.setupUniversityRoutes({mount:body,getFields:()=>store.fields(entry),onChange:rows=>{if(!destroyed&&auth===authEpoch&&view===viewEpoch&&active===id){store.edit(id,rows);update();}}});widgetId=id;renderedKey=prFieldsKey(entry);
    }catch(error){if(!destroyed&&auth===authEpoch&&view===viewEpoch&&active===id)loadError=String(error?.message||'Не удалось открыть маршруты.');}
    finally{if(!destroyed&&auth===authEpoch&&view===viewEpoch){loading=false;update();}}
  }
  function cardChanged(card){
    if(destroyed)return;const id=prId(card),changed=id!==active;const entry=id?store.receive(card):null;
    if(changed){active=id;++viewEpoch;loading=false;loadError='';removeWidget();}
    if(entry&&widgetId===id&&!entry.dirty&&!entry.saving&&renderedKey!==prFieldsKey(entry)){widget.refresh(store.fields(entry));renderedKey=prFieldsKey(entry);}
    update();if(details.open&&entry&&!widget&&!loading)open();
  }
  save.onclick=async()=>{
    if(destroyed||!api||widgetId!==active)return;const snapshot=store.begin(active,widget.value());if(!snapshot)return;const auth=authEpoch;update();
    try{
      const value=await api('cards/'+snapshot.id+'/routes',{routes:snapshot.routes,expectedUpdated:snapshot.expectedUpdated});if(destroyed||auth!==authEpoch)return;
      const entry=store.accept(snapshot,value?.card||value);if(!entry||active!==snapshot.id)return;
      if(!entry.dirty&&widgetId===entry.id){widget.refresh(store.fields(entry));renderedKey=prFieldsKey(entry);}onSaved(prClone(entry.card));update(entry.dirty?'Сохранено. Более новые правки остаются в черновике.':'Маршруты сохранены на Mac.');
    }catch(error){if(!destroyed&&auth===authEpoch){store.fail(snapshot,error);if(active===snapshot.id){update();toast(String(error?.message||'Черновик сохранён в этом окне.'));}}}
  };
  rebase.onclick=async()=>{
    const id=active,entry=store.get(id),auth=authEpoch,view=viewEpoch;if(destroyed||!api||!entry?.conflict||entry.saving||entry.rebasing)return;entry.rebasing=true;let message='';update();
    try{const value=await api('cards/'+id);if(destroyed||auth!==authEpoch||view!==viewEpoch||active!==id||store.get(id)!==entry)return;store.rebase(id,value?.card||value);onSaved(prClone(entry.card));message='Новая версия получена. Проверьте маршруты и нажмите «Сохранить».';}
    catch(error){if(!destroyed&&auth===authEpoch&&active===id){entry.error=String(error?.message||'Не удалось получить версию.');update();}}
    finally{if(store.get(id)===entry)entry.rebasing=false;if(!destroyed&&auth===authEpoch&&active===id)update(message);}
  };
  details.ontoggle=()=>{if(details.open)open();else{++viewEpoch;loading=false;removeWidget();update();}};
  function clear(){++authEpoch;++viewEpoch;active='';loading=false;loadError='';removeWidget();store.clear();details.open=false;update();}
  cardChanged(getCard());
  return {cardChanged,clear,destroy(){if(destroyed)return;clear();destroyed=true;details.ontoggle=null;mount.replaceChildren();}};
}
