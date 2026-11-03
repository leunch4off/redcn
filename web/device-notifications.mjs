import {hasOwnKey} from './own-property.mjs';
// Delivery is to this explicitly selected browser. No webhook, address or key.
const ddText=(value,max=200)=>typeof value==='string'?value.replace(/[\u0000-\u001f\u007f]/gu,' ').slice(0,max):'';
const ddScope=value=>typeof value==='string'&&/^[a-f0-9]{64}$/u.test(value);
const ddId=value=>typeof value==='string'&&/^[a-f0-9]{32}$/u.test(value);
export function normalizeDeviceNotice(row){
 if(!row||!ddId(row.notificationId)||row.read===true||typeof row.title!=='string')return null;
 return {id:row.notificationId,title:ddText(row.title,100),body:'Новое подходящее объявление · откройте раздел «Новые»',created:Number.isFinite(row.created)?row.created:null};
}
export function createDeviceDeliveryStore(indexedDB=globalThis.indexedDB){
 let opening;
 function db(){if(!indexedDB)return Promise.reject(Error('Браузер не предоставляет надёжное локальное хранилище уведомлений.'));if(!opening)opening=new Promise((resolve,reject)=>{const request=indexedDB.open('redcn-device-delivery-v1',1);request.onupgradeneeded=()=>{for(const name of['settings','events'])if(!request.result.objectStoreNames.contains(name))request.result.createObjectStore(name);};request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(Error('Не удалось открыть журнал уведомлений'));request.onblocked=()=>reject(Error('Закройте старую вкладку, чтобы обновить журнал уведомлений'));});return opening;}
 async function transaction(name,key,edit){const connection=await db();return new Promise((resolve,reject)=>{const tx=connection.transaction(name,'readwrite'),store=tx.objectStore(name),read=store.get(key);let output;read.onsuccess=()=>{try{const changed=edit(read.result);output=changed?.result;if(changed&&hasOwnKey(changed,'value'))store.put(changed.value,key);}catch(error){tx.abort();reject(error);}};tx.oncomplete=()=>resolve(output);tx.onerror=()=>reject(Error('Не удалось записать журнал уведомлений'));tx.onabort=()=>reject(Error('Запись уведомления отменена'));});}
 return {setting(scope,value){return transaction('settings',scope,old=>value===undefined?{result:old||{enabled:false,label:''}}:{value,result:value});},
  claim(scope,id,now){return transaction('events',scope+':'+id,old=>{const row=old||{attempts:0,state:'pending',nextAt:0,leaseUntil:0};if(row.state==='delivered'||row.state==='failed'||row.nextAt>now||row.leaseUntil>now)return {result:null};const value={...row,state:'delivering',attempts:row.attempts+1,leaseUntil:now+30000};return {value,result:value};});},
  finish(scope,id,result,now){return transaction('events',scope+':'+id,old=>{if(!old)return {result:false};let value;if(result==='delivered')value={...old,state:'delivered',leaseUntil:0,nextAt:0};else if(result==='cancelled')value={...old,state:'pending',attempts:Math.max(0,old.attempts-1),leaseUntil:0,nextAt:now};else value={...old,state:old.attempts>=5?'failed':'pending',leaseUntil:0,nextAt:now+Math.min(300000,5000*2**(old.attempts-1))};return {value,result:value};});},
  retry(scope,id){return transaction('events',scope+':'+id,old=>old?.state==='failed'?{value:{state:'pending',attempts:0,nextAt:0,leaseUntil:0},result:true}:{result:false});},
  close(){opening?.then(connection=>connection.close());opening=null;}};
}
export function createDeviceDelivery({scope,store,deliver,now=()=>Date.now()}={}){
 if(!ddScope(scope)||!store||typeof deliver!=='function')throw Error('Нужен подтверждённый контекст рабочего пространства');
 let enabled=false,generation=0,destroyed=false;
 return {async enable(label){if(destroyed)return false;const epoch=++generation;const value={enabled:true,label:ddText(label,60)||'Этот браузер'};await store.setting(scope,value);if(destroyed||epoch!==generation)return false;enabled=true;return true;},
  async restore(){const epoch=++generation;const value=await store.setting(scope);if(!destroyed&&epoch===generation)enabled=value.enabled===true;return value;},
  async disable(){enabled=false;++generation;await store.setting(scope,{enabled:false,label:''});},
  async ingest(rows){const counts={sent:0,failed:0,cancelled:0};if(!enabled||destroyed)return counts;const epoch=generation;if((await store.setting(scope)).enabled!==true){enabled=false;++generation;return counts;}if(destroyed||epoch!==generation)return counts;for(const raw of(Array.isArray(rows)?rows:[]).slice(0,100)){if(!enabled||destroyed||epoch!==generation)break;if((await store.setting(scope)).enabled!==true){enabled=false;++generation;break;}const notice=normalizeDeviceNotice(raw);if(!notice)continue;const claimed=await store.claim(scope,notice.id,now());if(!claimed)continue;const allowed=()=>enabled&&!destroyed&&epoch===generation;let state='failed';try{if(!allowed()){state='cancelled';counts.cancelled++;continue;}const close=await deliver(notice,{tag:'redcn-notice-'+scope.slice(0,16)+'-'+notice.id,allowed});if(!allowed()){if(typeof close==='function')await close();state='cancelled';counts.cancelled++;}else{state='delivered';counts.sent++;}}catch{if(allowed())counts.failed++;else{state='cancelled';counts.cancelled++;}}finally{await store.finish(scope,notice.id,state,now());}}return counts;},
  async retryFailed(rows){if(!enabled||destroyed)return 0;let count=0;for(const raw of(Array.isArray(rows)?rows:[]).slice(0,100)){const notice=normalizeDeviceNotice(raw);if(notice&&await store.retry(scope,notice.id))count++;}return count;},
  destroy(){destroyed=true;enabled=false;++generation;},get enabled(){return enabled&&!destroyed;}};
}
export function deviceNotificationSupport(runtime=globalThis){
 if(runtime.isSecureContext!==true)return {supported:false,reason:'Уведомления требуют HTTPS или локального адреса Mac.'};
 if(typeof runtime.Notification!=='function'||!runtime.indexedDB)return {supported:false,reason:'Этот браузер не поддерживает системные уведомления с надёжным журналом.'};
 if(/iP(?:hone|ad|od)/u.test(runtime.navigator?.userAgent||'')&&runtime.navigator?.standalone!==true&&!runtime.matchMedia?.('(display-mode: standalone)').matches)return {supported:false,reason:'На iPhone сначала установите приложение из Safari на экран «Домой» и откройте его с иконки.'};
 return {supported:true,permission:runtime.Notification.permission};
}
async function ddDeliver(notice,{tag,allowed}){
 if(Notification.permission!=='granted'||!allowed())throw Error('Нет разрешения на уведомления');
 const registration=await navigator.serviceWorker?.getRegistration?.();
 if(!allowed())throw Error('Доставка выключена');
 if(registration?.active){await registration.showNotification(notice.title,{body:notice.body,tag,data:{redcnDeviceDelivery:1},renotify:false});return async()=>{for(const item of await registration.getNotifications({tag}))item.close();};}
 if(!allowed())throw Error('Доставка выключена');
 const notification=new Notification(notice.title,{body:notice.body,tag});notification.onclick=()=>{notification.close();globalThis.focus?.();};return ()=>notification.close();
}
export function setupDeviceNotifications({mount,api,toast=()=>{},runtime=globalThis}={}){
 if(!mount||typeof api!=='function')return null;
 const make=(tag,text='')=>{const node=document.createElement(tag);node.textContent=text;return node;};
 const panel=make('details'),summary=make('summary','Уведомления на это устройство'),note=make('p','Доставка только в выбранный вами браузер, пока приложение открыто. На iPhone используйте установленное приложение. Telegram, почта и внешние адресаты не подключаются.'),label=make('input'),enable=make('button','Выбрать этот браузер и включить'),disable=make('button','Выключить доставку'),retry=make('button','Повторить недоставленные'),status=make('p','Доставка выключена до вашего выбора.');
 label.placeholder='Название этого устройства';label.maxLength=60;label.setAttribute('aria-label','Название получателя уведомлений');enable.type=disable.type=retry.type='button';status.setAttribute('role','status');panel.append(summary,note,label,enable,disable,retry,status);mount.replaceChildren(panel);
 let controller,contextPromise,store,labelTouched=false,destroyed=false,busy=false,checking=false,timer,scope,actionEpoch=0,permission=runtime.Notification?.permission;
 label.oninput=()=>{labelTouched=true;};
 const support=deviceNotificationSupport(runtime);if(!support.supported){enable.disabled=true;status.textContent=support.reason;}disable.disabled=retry.disabled=true;
 function message(text){if(!destroyed){status.textContent=text;toast(text);}}
 async function context(){if(controller)return controller;if(contextPromise)return contextPromise;contextPromise=(async()=>{const value=await api('device-delivery/context');if(destroyed)return null;if(value?.version!==1||!ddScope(value.workspaceId)||value.channel!=='this-browser')throw Error('Сервер не подтвердил получателя уведомлений');scope=value.workspaceId;store=createDeviceDeliveryStore(runtime.indexedDB);controller=createDeviceDelivery({scope,store,deliver:ddDeliver});return controller;})().finally(()=>{contextPromise=null;});return contextPromise;}
 async function poll(){clearTimeout(timer);if(destroyed||!controller?.enabled||checking)return;checking=true;try{if(runtime.Notification?.permission!=='granted'){await controller.disable();disable.disabled=retry.disabled=true;message('Разрешение браузера отозвано. Доставка остановлена.');return;}const rows=await api('listing-watch/notifications');if(destroyed)return;const result=await controller.ingest(rows?.items);if(result.failed)message('Не удалось показать '+result.failed+' уведомлений. Повторим с задержкой; максимум 5 попыток.');else if(result.sent)message('Доставлено в этот браузер: '+result.sent+'. Журнал защищает от повторов.');}catch(error){if(!destroyed)message(error?.message||'Доставка временно недоступна; повторим после подключения.');}finally{checking=false;if(!destroyed&&controller?.enabled)timer=setTimeout(poll,30000);}}
 enable.onclick=async()=>{if(busy||destroyed||!support.supported)return;busy=true;enable.disabled=true;const epoch=++actionEpoch;try{// Permission starts directly in the operator click, before awaiting an API.
  const permissionPromise=runtime.Notification.requestPermission();permission=await permissionPromise;if(destroyed||epoch!==actionEpoch)return;if(permission!=='granted')throw Error('Браузер не разрешил уведомления. Вы можете изменить разрешение в его настройках.');const current=await context();if(!current||destroyed||epoch!==actionEpoch)return;await current.enable(label.value);if(destroyed||epoch!==actionEpoch)return;disable.disabled=retry.disabled=false;message('Получатель: '+(label.value.trim()||'этот браузер')+'. Включите внутренние уведомления поиска для появления событий.');await poll();}catch(error){message(error.message||'Доставка не включена');}finally{busy=false;if(!destroyed)enable.disabled=false;}};
 disable.onclick=async()=>{++actionEpoch;clearTimeout(timer);await controller?.disable();disable.disabled=retry.disabled=true;message('Доставка на это устройство выключена. Новые уведомления не отправляются.');};
 retry.onclick=async()=>{if(destroyed||busy||!controller?.enabled)return;busy=true;retry.disabled=true;try{const rows=await api('listing-watch/notifications');if(destroyed||!controller.enabled)return;const count=await controller.retryFailed(rows?.items);message('Возвращено в очередь: '+count+'.');await poll();}catch(error){message(error.message||'Не удалось повторить доставку');}finally{busy=false;if(!destroyed)retry.disabled=!controller?.enabled;}};
 async function restoreSaved(quiet=false){const epoch=actionEpoch;try{const current=await context();if(!current||destroyed||epoch!==actionEpoch)return;const saved=await current.restore();if(destroyed||epoch!==actionEpoch)return;if(!labelTouched)label.value=saved.label||'';if(saved.enabled&&runtime.Notification?.permission==='granted'){disable.disabled=retry.disabled=false;if(!quiet)message('Доставка в этот браузер включена.');await poll();}else if(saved.enabled){await current.disable();if(!quiet)message('Нужно заново разрешить уведомления в браузере.');}}catch(error){if(!quiet)message(error.message||'Не удалось получить журнал доставки');}}
 panel.addEventListener('toggle',()=>{if(panel.open&&!destroyed&&!busy&&support.supported)restoreSaved(false);});
 // Restore an earlier explicit choice without another permission prompt.
 // A default-off browser performs no notification/API-notice delivery.
 if(support.supported)Promise.resolve().then(()=>restoreSaved(true));
 return {destroy(){destroyed=true;++actionEpoch;clearTimeout(timer);controller?.destroy();store?.close();mount.replaceChildren();}};
}
