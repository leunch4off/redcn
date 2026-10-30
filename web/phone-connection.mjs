import {renderPairingQr} from './qr-code.mjs';

export function setupPhoneConnection({local,api,toast,getProfile}){
  const make=(tag,text='',className='')=>{const node=document.createElement(tag);node.textContent=text;if(className)node.className=className;return node;};
  const panel=make('section','','card phone-connection');panel.id='phoneConnectionPanel';
  const heading=make('h2','iPhone · карточки с вашего Mac');
  const help=make('p','Компьютер собирает объявления и фотографии. Телефон показывает готовые карточки, позволяет скопировать текст и скачать фото. Для домашнего адреса нужна одна сеть. Внешняя HTTPS-ссылка позволяет работать с телефона через мобильный интернет.','muted');
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
  const remoteBox=make('div','','phone-remote-box'),remoteStatus=make('p','','notice'),remoteControls=make('div','','phone-connection-actions');remoteStatus.id='phoneRemoteStatus';
  const remoteStart=make('button','Включить доступ из интернета','btn primary'),remoteStop=make('button','Отключить внешний адрес','btn');remoteStart.id='phoneRemoteStart';remoteStop.id='phoneRemoteStop';remoteStart.type=remoteStop.type='button';remoteControls.append(remoteStart,remoteStop);
  const remoteLink=make('a','Открыть на телефоне через интернет');remoteLink.id='phoneRemoteLink';remoteLink.target='_blank';remoteLink.rel='noopener noreferrer';remoteLink.className='hidden';
  const remoteCopy=make('button','Скопировать внешнюю ссылку','btn');remoteCopy.id='phoneRemoteCopy';remoteCopy.type='button';remoteCopy.className='btn hidden';
  remoteBox.append(make('h3','Работа с улицы'),make('p','Mac остаётся сервером. HTTPS-туннель передаёт данные телефону; вход по 8 цифрам сохраняется. Адрес временный и меняется после перезапуска туннеля.','muted'),remoteStatus,remoteControls,remoteLink,remoteCopy);
  panel.append(heading,help,status,controls,remoteBox,pairBox);
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
    const oldUrl=address.value,wasRemoteReady=current?.remote?.state==='ready';current=data;
    if(Number.isFinite(data.serverTime))clockOffset=data.serverTime-Date.now()/1000;
    status.textContent=data.enabled?'Телефонный доступ включён. Новые карточки появятся на подключённом iPhone автоматически.':'Телефонный доступ выключен.';
    connect.disabled=busy;renew.disabled=busy||!data.enabled;disable.disabled=busy||!data.enabled;
    connect.textContent=data.enabled?'Подключить ещё один телефон':'Подключить iPhone';
    pairBox.classList.toggle('hidden',!data.pairUrls?.length);
    address.replaceChildren();
    for(const value of data.pairUrls||[]){const option=make('option',new URL(value).host);option.value=value;if(option.textContent===selectedHost)option.selected=true;address.append(option);}
    if(data.pairUrls?.length){if(!wasRemoteReady&&data.remote?.state==='ready'){const option=[...address.options].find(option=>option.value.startsWith(data.remote.url+'/'));if(option)address.value=option.value;}if(oldUrl!==address.value||!qr.firstChild)showQr();countdown();}
    else{qr.replaceChildren();code.textContent='';expires.textContent='';link.removeAttribute('href');}
    const remote=data.remote||{},ready=remote.state==='ready';remoteStart.disabled=busy||remote.available===false||remote.state==='starting'||ready;remoteStop.disabled=busy||!['starting','ready'].includes(remote.state);
    remoteStatus.textContent=ready?'Внешний HTTPS-адрес работает. Откройте его на iPhone и введите код с Mac.':remote.state==='starting'?'Подключаем внешний адрес… Обычно это занимает несколько секунд.':remote.state==='error'?remote.error:remote.available===false?'Для внешней ссылки нужен cloudflared на Mac.':'Внешний доступ выключен. Домашний адрес продолжает работать.';
    remoteLink.classList.toggle('hidden',!ready);remoteCopy.classList.toggle('hidden',!ready);if(ready){remoteLink.href=remote.url+'/phone/';remoteLink.textContent=remote.url+'/phone/';}else remoteLink.removeAttribute('href');
    nextRefresh=Date.now()+(remote.state==='starting'?1000:10000);
  }
  function showQr(){if(!address.value)return;link.href=address.value;renderPairingQr(qr,address.value);}
  address.onchange=showQr;
  copy.onclick=async()=>{try{await navigator.clipboard.writeText(address.value);toast('Ссылка подключения скопирована.');}catch{toast('Откройте ссылку подключения и скопируйте адрес из браузера.');}};
  copyCode.onclick=async()=>{try{await navigator.clipboard.writeText(current.pairCode);toast('Код подключения скопирован.');}catch{toast('Введите на телефоне 8 цифр, показанные над кнопкой.');}};
  async function refresh(){if(!local||busy||refreshing)return;refreshing=true;const requestRevision=revision;nextRefresh=Date.now()+10000;try{const data=await api('phone');if(requestRevision===revision)render(data);}catch(error){status.textContent=error.message;throw error;}finally{refreshing=false;}}
  async function action(value){if(busy)return;busy=true;revision++;for(const button of [connect,renew,disable,remoteStart,remoteStop])button.disabled=true;try{if(getProfile&&value!=='disable')await api('phone',{action:'profile',...getProfile()});render(await api('phone',{action:value}));}catch(error){status.textContent=error.message;toast(error.message);}finally{busy=false;if(current)render(current);}}
  connect.onclick=()=>action(current?.enabled?'pair':'enable');renew.onclick=()=>action('pair');disable.onclick=()=>action('disable');
  remoteStart.onclick=()=>action('remote-start');remoteStop.onclick=()=>action('remote-stop');remoteCopy.onclick=async()=>{try{await navigator.clipboard.writeText(current.remote.url+'/phone/');toast('Внешняя ссылка скопирована. Откройте её в Safari, затем установите на экран «Домой».');}catch{toast('Скопируйте HTTPS-адрес из ссылки над кнопкой.');}};
  if(local){refresh().catch(()=>{renew.disabled=disable.disabled=true;});setInterval(()=>{if(document.hidden)return;countdown();if(current?.enabled&&Date.now()>=nextRefresh)refresh().catch(()=>{});},1000);document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh().catch(()=>{});});}
  else{status.textContent='Для подключения телефона запустите redcn на Mac через start-redcn.command. В локальном редакторе появятся 8 цифр и QR-код.';connect.disabled=renew.disabled=disable.disabled=remoteStart.disabled=remoteStop.disabled=true;}
  return {enabled:()=>current?.enabled===true,updateProfile:()=>local&&getProfile&&api('phone',{action:'profile',...getProfile()}),refresh};
}
