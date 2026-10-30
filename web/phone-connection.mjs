import {renderPairingQr} from './qr-code.mjs';

export function setupPhoneConnection({local,api,toast,getProfile}){
  const make=(tag,text='',className='')=>{const node=document.createElement(tag);node.textContent=text;if(className)node.className=className;return node;};
  const panel=make('section','','card phone-connection');panel.id='phoneConnectionPanel';
  const heading=make('h2','iPhone · карточки с вашего Mac');
  const help=make('p','Компьютер собирает объявления и фотографии. Телефон показывает готовые карточки, позволяет скопировать текст и скачать фото. Mac должен работать, оба устройства — в одной сети.','muted');
  const status=make('p','','notice');status.id='phoneConnectionStatus';
  const controls=make('div','','phone-connection-actions');
  const connect=make('button','Подключить iPhone','btn primary'),renew=make('button','Новый QR-код','btn'),disable=make('button','Отключить доступ','btn');
  connect.id='phoneConnect';renew.id='phonePairRenew';disable.id='phoneDisable';for(const button of [connect,renew,disable])button.type='button';controls.append(connect,renew,disable);
  const pairBox=make('div','','phone-pair-box hidden');pairBox.id='phonePairBox';
  const qr=make('div','','phone-pair-qr');qr.id='phonePairQr';
  const info=make('div'),title=make('h3','Наведите камеру iPhone на QR-код'),expires=make('p','','muted');
  const label=make('label','Адрес компьютера'),address=make('select');address.id='phonePairAddress';label.htmlFor=address.id;
  const link=make('a','Открыть подключение');link.id='phonePairLink';link.target='_blank';link.rel='noopener noreferrer';
  const copy=make('button','Скопировать ссылку','btn');copy.type='button';
  info.append(title,expires,label,address,link,copy,make('p','После подключения нажмите «Установить» на телефоне. В iPhone выберите «Поделиться → На экран “Домой”».','muted'));pairBox.append(qr,info);
  panel.append(heading,help,status,controls,pairBox);
  const settings=document.getElementById('settings');settings.querySelector('.heading')?.after(panel);if(!panel.isConnected)settings.prepend(panel);
  const shortcut=make('button','Телефон','btn');shortcut.id='phoneConnectionShortcut';shortcut.type='button';shortcut.onclick=()=>{document.querySelector('nav [data-tab="settings"]').click();panel.scrollIntoView({block:'start',behavior:'smooth'});};document.querySelector('.header-links').prepend(shortcut);
  let current=null,busy=false;
  function render(data){
    current=data;status.textContent=data.enabled?'Телефонный доступ включён. Новые карточки появятся на подключённом iPhone автоматически.':'Телефонный доступ выключен.';
    connect.disabled=busy;renew.disabled=busy||!data.enabled;disable.disabled=busy||!data.enabled;
    connect.textContent=data.enabled?'Подключить ещё один телефон':'Подключить iPhone';
    pairBox.classList.toggle('hidden',!data.pairUrls?.length);
    address.replaceChildren();
    for(const value of data.pairUrls||[]){const option=make('option',new URL(value).host);option.value=value;address.append(option);}
    if(data.pairUrls?.length){expires.textContent='Одноразовое подключение, действует до '+new Date(data.pairExpires*1000).toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit'})+'.';showQr();}
    else qr.replaceChildren();
  }
  function showQr(){link.href=address.value;renderPairingQr(qr,address.value);}
  address.onchange=showQr;
  copy.onclick=async()=>{try{await navigator.clipboard.writeText(address.value);toast('Ссылка подключения скопирована.');}catch{toast('Откройте ссылку подключения и скопируйте адрес из браузера.');}};
  async function action(value){if(busy)return;busy=true;for(const button of [connect,renew,disable])button.disabled=true;try{if(getProfile&&value!=='disable')await api('phone',{action:'profile',...getProfile()});render(await api('phone',{action:value}));}catch(error){status.textContent=error.message;toast(error.message);}finally{busy=false;if(current){connect.disabled=false;renew.disabled=!current.enabled;disable.disabled=!current.enabled;}}}
  connect.onclick=()=>action(current?.enabled?'pair':'enable');renew.onclick=()=>action('pair');disable.onclick=()=>action('disable');
  if(local)api('phone').then(render).catch(error=>{status.textContent=error.message;renew.disabled=disable.disabled=true;});
  else{status.textContent='Для подключения телефона запустите redcn на Mac через start-redcn.command. В локальном редакторе появится QR-код.';connect.disabled=renew.disabled=disable.disabled=true;}
  return {enabled:()=>current?.enabled===true,updateProfile:()=>local&&getProfile&&api('phone',{action:'profile',...getProfile()}),refresh:()=>local&&api('phone').then(render)};
}
