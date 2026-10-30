import {renderPairingQr} from './qr-code.mjs';

export function setupPhoneConnection({local,api,toast,getProfile}){
  const make=(tag,text='',className='')=>{const node=document.createElement(tag);node.textContent=text;if(className)node.className=className;return node;};
  const panel=make('section','','card phone-connection');panel.id='phoneConnectionPanel';
  const heading=make('h2','iPhone · карточки с вашего Mac');
  const help=make('p','Компьютер собирает объявления и фотографии. Телефон показывает готовые карточки, позволяет скопировать текст и скачать фото. Mac должен работать, оба устройства — в одной сети.','muted');
  const status=make('p','','notice');status.id='phoneConnectionStatus';
  const controls=make('div','','phone-connection-actions');
  const connect=make('button','Подключить iPhone','btn primary'),renew=make('button','Новый код и QR','btn'),disable=make('button','Отключить доступ','btn');
  connect.id='phoneConnect';renew.id='phonePairRenew';disable.id='phoneDisable';for(const button of [connect,renew,disable])button.type='button';controls.append(connect,renew,disable);
  const pairBox=make('div','','phone-pair-box hidden');pairBox.id='phonePairBox';
  const qr=make('div','','phone-pair-qr');qr.id='phonePairQr';
  const info=make('div'),title=make('h3','Код для входа с телефона'),code=make('output','','phone-pair-code'),expires=make('p','','muted');code.id='phonePairManualCode';code.style.cssText='display:block;font:700 clamp(24px,4vw,38px)/1.4 ui-monospace,monospace;letter-spacing:.12em;margin:10px 0;user-select:all';code.setAttribute('aria-label','Восемь цифр для подключения телефона');expires.id='phonePairExpires';
  const copyCode=make('button','Скопировать 8 цифр','btn');copyCode.type='button';copyCode.id='phonePairCopyCode';
  const label=make('label','Адрес компьютера'),address=make('select');address.id='phonePairAddress';label.htmlFor=address.id;
  const link=make('a','Открыть подключение');link.id='phonePairLink';link.target='_blank';link.rel='noopener noreferrer';
  const copy=make('button','Скопировать ссылку','btn');copy.type='button';
  info.append(title,code,expires,copyCode,make('p','Введите эти 8 цифр в приложении на экране «Домой» или отсканируйте QR. Код одноразовый и обновляется автоматически.','muted'),label,address,link,copy,make('p','Safari и приложение с экрана «Домой» могут запросить отдельное подключение. Откройте установленное приложение и введите текущий код с этого экрана.','muted'));pairBox.append(qr,info);
  panel.append(heading,help,status,controls,pairBox);
  const settings=document.getElementById('settings');settings.querySelector('.heading')?.after(panel);if(!panel.isConnected)settings.prepend(panel);
  const shortcut=make('button','Телефон','btn');shortcut.id='phoneConnectionShortcut';shortcut.type='button';shortcut.onclick=()=>{document.querySelector('nav [data-tab="settings"]').click();panel.scrollIntoView({block:'start',behavior:'smooth'});refresh().catch(()=>{});};document.querySelector('.header-links').prepend(shortcut);
  let current=null,busy=false,refreshing=false,nextRefresh=0,clockOffset=0,revision=0;
  function countdown(){
    if(!current?.enabled||!current.pairExpires)return;
    const remaining=Math.max(0,Math.ceil(current.pairExpires-(Date.now()/1000+clockOffset)));
    code.textContent=remaining>0?(current.pairCode||'').replace(/(.{4})(.{4})/,'$1 $2'):'—';
    copyCode.disabled=remaining===0||!current.pairCode;
    expires.textContent=remaining>0?'Код действует ещё '+Math.floor(remaining/60)+':'+String(remaining%60).padStart(2,'0')+'. После входа появится новый.':'Обновляем код подключения…';
    if(!remaining){qr.replaceChildren();link.removeAttribute('href');}
  }
  function render(data){
    const selectedHost=address.selectedOptions[0]?.textContent;
    const oldUrl=address.value;current=data;
    if(Number.isFinite(data.serverTime))clockOffset=data.serverTime-Date.now()/1000;
    status.textContent=data.enabled?'Телефонный доступ включён. Новые карточки появятся на подключённом iPhone автоматически.':'Телефонный доступ выключен.';
    connect.disabled=busy;renew.disabled=busy||!data.enabled;disable.disabled=busy||!data.enabled;
    connect.textContent=data.enabled?'Подключить ещё один телефон':'Подключить iPhone';
    pairBox.classList.toggle('hidden',!data.pairUrls?.length);
    address.replaceChildren();
    for(const value of data.pairUrls||[]){const option=make('option',new URL(value).host);option.value=value;if(option.textContent===selectedHost)option.selected=true;address.append(option);}
    if(data.pairUrls?.length){if(oldUrl!==address.value||!qr.firstChild)showQr();countdown();}
    else{qr.replaceChildren();code.textContent='';expires.textContent='';link.removeAttribute('href');}
    nextRefresh=Date.now()+10000;
  }
  function showQr(){if(!address.value)return;link.href=address.value;renderPairingQr(qr,address.value);}
  address.onchange=showQr;
  copy.onclick=async()=>{try{await navigator.clipboard.writeText(address.value);toast('Ссылка подключения скопирована.');}catch{toast('Откройте ссылку подключения и скопируйте адрес из браузера.');}};
  copyCode.onclick=async()=>{try{await navigator.clipboard.writeText(current.pairCode);toast('Код подключения скопирован.');}catch{toast('Введите на телефоне 8 цифр, показанные над кнопкой.');}};
  async function refresh(){if(!local||busy||refreshing)return;refreshing=true;const requestRevision=revision;nextRefresh=Date.now()+10000;try{const data=await api('phone');if(requestRevision===revision)render(data);}catch(error){status.textContent=error.message;throw error;}finally{refreshing=false;}}
  async function action(value){if(busy)return;busy=true;revision++;for(const button of [connect,renew,disable])button.disabled=true;try{if(getProfile&&value!=='disable')await api('phone',{action:'profile',...getProfile()});render(await api('phone',{action:value}));}catch(error){status.textContent=error.message;toast(error.message);}finally{busy=false;if(current){connect.disabled=false;renew.disabled=!current.enabled;disable.disabled=!current.enabled;}}}
  connect.onclick=()=>action(current?.enabled?'pair':'enable');renew.onclick=()=>action('pair');disable.onclick=()=>action('disable');
  if(local){refresh().catch(()=>{renew.disabled=disable.disabled=true;});setInterval(()=>{if(document.hidden)return;countdown();if(current?.enabled&&Date.now()>=nextRefresh)refresh().catch(()=>{});},1000);document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh().catch(()=>{});});}
  else{status.textContent='Для подключения телефона запустите redcn на Mac через start-redcn.command. В локальном редакторе появятся 8 цифр и QR-код.';connect.disabled=renew.disabled=disable.disabled=true;}
  return {enabled:()=>current?.enabled===true,updateProfile:()=>local&&getProfile&&api('phone',{action:'profile',...getProfile()}),refresh};
}
