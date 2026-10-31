// Installation is detected from the running app, never inferred from a button tap.
export function webAppInstalled({standalone=false,displayMode='browser',appInstalled=false}={}){
  return standalone===true||appInstalled===true||['standalone','fullscreen','minimal-ui'].includes(displayMode);
}

export function pwaInstallPlatform({userAgent='',platform='',maxTouchPoints=0}={}){
  return /iPad|iPhone|iPod/i.test(userAgent)||platform==='MacIntel'&&maxTouchPoints>1?'ios':'other';
}

export function initializePwaInstall({container,phoneUrl='/phone/',registerWorker=true}={}){
  if(typeof document==='undefined')return null;
  const host=typeof container==='string'?document.querySelector(container):container;
  if(!host)return null;
  if(host.pwaInstaller)return host.pwaInstaller;
  const styleId='most-pwa-install-style';
  if(!document.getElementById(styleId)){
    const style=document.createElement('style');style.id=styleId;style.textContent=`
      .pwa-install-button{display:flex;align-items:center;justify-content:center;gap:10px;width:100%;min-height:48px;padding:12px 16px;border:1px solid #cbd7c8;border-radius:14px;background:#f0f6ec;color:#294632;font:600 15px/1.35 system-ui;touch-action:manipulation;cursor:pointer}
      .pwa-install-button[hidden]{display:none!important}.pwa-install-button:focus-visible,.pwa-install-dialog button:focus-visible{outline:3px solid #8eac79;outline-offset:3px}
      .pwa-install-dialog{box-sizing:border-box;max-width:420px;width:calc(100% - 28px);max-height:calc(100dvh - 40px);overflow:auto;border:1px solid #d6dfd0;border-radius:24px;padding:24px;background:#fffef9;color:#25372b;box-shadow:0 24px 80px #15231f40;font:15px/1.6 system-ui}
      .pwa-install-dialog::backdrop{background:#16231cca;backdrop-filter:blur(5px)}.pwa-install-dialog h2{margin:0 0 12px;font:650 22px/1.2 system-ui;letter-spacing:-.5px}.pwa-install-dialog p{margin:12px 0;color:#69766a;font-size:14px;line-height:1.55}.pwa-install-dialog ol{padding-left:24px;margin:18px 0}.pwa-install-dialog li{padding:6px 0}.pwa-install-dialog button{display:block;width:100%;min-height:48px;margin-top:20px;border:0;border-radius:13px;padding:12px;background:#34543a;color:white;font:600 16px/1.4 system-ui;cursor:pointer}.pwa-install-dialog a{color:#315d39}.pwa-install-dialog .pwa-install-note{padding:12px;border-radius:12px;background:#f1f5ec;font-size:12px;color:#586a50}
      @media(max-width:650px){.pwa-install-dialog{margin:auto auto max(14px,env(safe-area-inset-bottom));border-radius:24px;padding:22px;max-height:calc(100dvh - env(safe-area-inset-top) - 30px)}}
    `;document.head.append(style);
  }
  const button=document.createElement('button');button.type='button';button.className='pwa-install-button';button.dataset.pwaInstall='';button.textContent='＋ Установить на экран «Домой»';
  const dialog=document.createElement('dialog');dialog.className='pwa-install-dialog';dialog.setAttribute('aria-label','Установка МОСТ на экран Домой');
  const heading=document.createElement('h2'),intro=document.createElement('p'),steps=document.createElement('ol'),note=document.createElement('p'),close=document.createElement('button');
  heading.textContent='МОСТ на экране «Домой»';close.type='button';close.textContent='Понятно';note.className='pwa-install-note';
  dialog.append(heading,intro,steps,note,close);host.append(button);document.body.append(dialog);
  const modes=['standalone','fullscreen','minimal-ui'].map(mode=>({mode,query:window.matchMedia('(display-mode: '+mode+')')}));
  let deferred=null,appInstalled=false;
  function installed(){return webAppInstalled({standalone:navigator.standalone===true,displayMode:modes.find(row=>row.query.matches)?.mode||'browser',appInstalled});}
  function refresh(){button.hidden=installed();if(button.hidden&&dialog.open)dialog.close();}
  function instructions(){
    steps.replaceChildren();const ios=pwaInstallPlatform(navigator)==='ios';
    const target=new URL(phoneUrl,location.origin);const onPhonePage=location.pathname.startsWith(target.pathname);
    intro.textContent=ios?'Добавьте сайт как приложение — он будет открываться с отдельной иконки.':'Если браузер предлагает установку приложения, выберите её в меню браузера.';
    const values=ios?[
      'Откройте мобильную страницу МОСТ в Safari на iPhone.',
      'Нажмите «Поделиться» — квадрат со стрелкой вверх. В некоторых версиях Safari сначала откройте меню страницы.',
      'Выберите «На экран Домой». Если есть переключатель «Открывать как веб-приложение», оставьте его включённым.',
      'Нажмите «Добавить» и запустите МОСТ с новой иконки.'
    ]:['Откройте мобильную страницу МОСТ.', 'В меню браузера выберите «Установить приложение» или «Добавить на главный экран».', 'После установки откройте МОСТ с новой иконки.'];
    for(const value of values){const item=document.createElement('li');item.textContent=value;steps.append(item);}
    if(!onPhonePage){const link=document.createElement('a');link.href=target.href;link.textContent='Открыть мобильную страницу';const item=document.createElement('li');item.append(link);steps.prepend(item);}
    note.textContent='В приложении, открытом с иконки, кнопка установки исчезнет. Если iPhone запросит подключение к Mac заново, используйте новый код с компьютера.';
    if(typeof dialog.showModal==='function')dialog.showModal();else dialog.setAttribute('open','');
  }
  async function install(){
    if(installed()){refresh();return;}
    if(!deferred){instructions();return;}
    const prompt=deferred;deferred=null;
    try{await prompt.prompt();await prompt.userChoice;}catch{instructions();}
    // appinstalled/display-mode are the evidence of installation. A dismissed
    // prompt, accepted promise or opened guide alone never hides this button.
    refresh();
  }
  function beforeInstall(event){event.preventDefault();deferred=event;refresh();}
  function didInstall(){appInstalled=true;deferred=null;refresh();}
  function closeDialog(){dialog.close();button.focus();}
  button.addEventListener('click',install);close.addEventListener('click',closeDialog);
  dialog.addEventListener('click',event=>{if(event.target===dialog){const bounds=dialog.getBoundingClientRect();if(event.clientX<bounds.left||event.clientX>bounds.right||event.clientY<bounds.top||event.clientY>bounds.bottom)closeDialog();}});
  window.addEventListener('beforeinstallprompt',beforeInstall);window.addEventListener('appinstalled',didInstall);window.addEventListener('pageshow',refresh);document.addEventListener('visibilitychange',refresh);
  for(const row of modes)row.query.addEventListener?.('change',refresh);
  refresh();
  if(registerWorker&&window.isSecureContext&&'serviceWorker' in navigator&&location.pathname.startsWith('/phone/'))navigator.serviceWorker.register('/phone/sw.js',{scope:'/phone/',updateViaCache:'none'}).catch(()=>{});
  const result={button,dialog,refresh,destroy(){window.removeEventListener('beforeinstallprompt',beforeInstall);window.removeEventListener('appinstalled',didInstall);window.removeEventListener('pageshow',refresh);document.removeEventListener('visibilitychange',refresh);for(const row of modes)row.query.removeEventListener?.('change',refresh);button.remove();dialog.remove();delete host.pwaInstaller;}};
  host.pwaInstaller=result;return result;
}
