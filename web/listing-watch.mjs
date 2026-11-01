// The monitor stores links and dates; a full import is always an explicit action.
const lwText=(value,limit=400)=>typeof value==='string'?value.replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/g,'').slice(0,limit):'';
const lwCianHost=host=>host==='cian.ru'||host==='www.cian.ru';
const lwAvitoHost=host=>host==='avito.ru'||host==='www.avito.ru';

export function safeWatchOfferUrl(value){
  try{
    const url=new URL(String(value??''));
    if(url.protocol!=='https:'||!(lwCianHost(url.hostname)||lwAvitoHost(url.hostname))||url.username||url.password||url.port&&url.port!=='443')return '';
    if(lwAvitoHost(url.hostname))return /^\/[^/?#]+\/(?:kvartiry|komnaty)\/[^/?#]*_\d{1,20}\/?$/u.test(url.pathname)?'https://www.avito.ru'+url.pathname.replace(/\/$/u,''):'';
    const match=url.pathname.match(/^\/(rent|sale)\/flat\/(\d{1,20})\/?$/);
    return match?'https://www.cian.ru/'+match[1]+'/flat/'+match[2]+'/':'';
  }catch{return '';}
}

export function safeWatchSearchUrl(value){
  try{
    const url=new URL(String(value??'').trim());
    if(url.protocol!=='https:'||!(lwCianHost(url.hostname)||lwAvitoHost(url.hostname))||url.username||url.password||url.port&&url.port!=='443')return '';
    if(lwAvitoHost(url.hostname)){if(!/^\/[^/?#]+\/(?:kvartiry|komnaty)\/?$/u.test(url.pathname))return '';url.hash='';return url.href;}
    if(url.pathname!=='/cat.php'&&!/^\/snyat(?:[-/][^?#]*)?\/?$/u.test(url.pathname))return '';
    url.hash='';return url.href;
  }catch{return '';}
}

function lwDateParts(value){
  if(typeof value==='number'){
    if(!Number.isFinite(value)||value<0||value>8_640_000_000_000)return null;
    const date=new Date(value<100_000_000_000?value*1000:value);
    return Number.isFinite(date.getTime())?{date,hasTime:true}:null;
  }
  if(typeof value!=='string')return null;
  const match=value.match(/^(\d{4})-(\d{2})-(\d{2})(?:T.*)?$/u);if(!match)return null;
  const year=Number(match[1]),month=Number(match[2]),day=Number(match[3]);
  const calendar=new Date(Date.UTC(year,month-1,day,12));
  if(calendar.getUTCFullYear()!==year||calendar.getUTCMonth()!==month-1||calendar.getUTCDate()!==day)return null;
  if(value.length===10)return {date:calendar,hasTime:false};
  // A missing timezone must not silently become the viewer's local timezone.
  if(!/(?:Z|[+-]\d{2}:\d{2})$/iu.test(value))return {date:calendar,hasTime:false};
  const date=new Date(value);return Number.isFinite(date.getTime())?{date,hasTime:true}:null;
}

export function formatWatchDate(value){
  const parsed=lwDateParts(value);if(!parsed)return 'Дата не указана';
  return new Intl.DateTimeFormat('ru-RU',{timeZone:'Europe/Moscow',day:'2-digit',month:'2-digit',year:'numeric',...(parsed.hasTime?{hour:'2-digit',minute:'2-digit'}:{})}).format(parsed.date)+(parsed.hasTime?' МСК':'');
}

export function normalizeWatchItem(input={}){
  const row=input&&typeof input==='object'?input:{};
  const id=typeof row.id==='string'&&/^[a-zA-Z0-9:_-]{1,100}$/.test(row.id)?row.id:'';
  const published=lwDateParts(row.publishedAt)?row.publishedAt:null;
  const currency=lwText(row.currency||row.priceCurrency,8).toUpperCase(),unit=({RUB:'₽',RUR:'₽',USD:'$',EUR:'€'})[currency]||(/^[A-Z]{3}$/.test(currency)?currency:'');
  const price=typeof row.price==='number'&&Number.isFinite(row.price)&&row.price>=0?new Intl.NumberFormat('ru-RU',{maximumFractionDigits:2}).format(row.price)+(unit?' '+unit:''):lwText(row.price,100);
  const platform=row.platform==='avito'||id.startsWith('avito:')?'avito':'cian',decision=['accepted','rejected','pending'].includes(row.decision)?row.decision:'accepted';
  return {id,url:safeWatchOfferUrl(row.url),platform,decision,decisionReasons:(Array.isArray(row.decisionReasons)?row.decisionReasons:[]).filter(value=>typeof value==='string').slice(0,12).map(value=>lwText(value,400)),publicationStatus:lwText(row.publicationStatus,30),district:lwText(row.district,150),rooms:typeof row.rooms==='number'&&Number.isFinite(row.rooms)?String(row.rooms):'',title:lwText(row.title,400)||'Объявление '+(platform==='avito'?'Авито':'ЦИАН'),price:price+({month:' / мес.',day:' / сутки',week:' / неделю'}[row.pricePeriod]||''),area:typeof row.area==='number'&&Number.isFinite(row.area)&&row.area>0?row.area+' м²':lwText(row.area,80),metro:lwText(row.metro,150),publishedAt:published,publishedLabel:lwText(row.publishedLabel,160),publicationDateSource:lwText(row.publicationDateSource,80),firstSeen:lwDateParts(row.firstSeen)?row.firstSeen:null,lastSeen:lwDateParts(row.lastSeen)?row.lastSeen:null,read:row.read===true,isNew:row.isNew===true};
}

export function setupListingWatch({mount,api,onScan,toast=()=>{}}={}){
  if(!mount)return {refresh:async()=>false,destroy:()=>{}};
  const make=(tag,text='',className='')=>{const element=document.createElement(tag);element.textContent=text;if(className)element.className=className;return element;};
  const button=(text,action,className='btn')=>{const element=make('button',text,className);element.type='button';element.onclick=action;return element;};
  const shell=make('section','','listing-watch'),heading=make('div','','listing-watch-heading'),headingText=make('div'),count=make('span','0','listing-watch-count');
  headingText.append(make('div','ЦИАН / АВИТО · МОНИТОРИНГ','eyebrow'),make('h2','Новые объявления'));
  count.setAttribute('aria-label','Непрочитанных объявлений');heading.append(headingText,count);
  const note=make('p','Раз в час Mac проверяет выбранный поиск. В «Новые» попадают ссылки, впервые найденные после начального снимка. Полный разбор запускается вами отдельно.','muted');
  const details=make('details','','listing-watch-settings'),summary=make('summary','Настроить поиск и обновление');
  const form=make('form'),sourceLabel=make('label','Ссылка на поиск ЦИАН или Авито'),source=make('input');source.type='url';source.inputMode='url';source.autocomplete='off';source.placeholder='https://www.cian.ru/cat.php?... или https://www.avito.ru/moskva/kvartiry?...';source.setAttribute('aria-label','Ссылка на поиск ЦИАН или Авито');source.maxLength=4000;sourceLabel.append(source);
  const enabledLabel=make('label','','listing-watch-toggle'),enabled=make('input');enabled.type='checkbox';enabled.setAttribute('aria-label','Проверять поиск каждый час');enabledLabel.append(enabled,document.createTextNode('Проверять каждый час, пока Mac включён'));
  const adapterLabel=make('label','Способ чтения'),adapter=make('select');adapter.setAttribute('aria-label','Способ чтения поиска');
  for(const [value,title]of [['html','Страница поиска'],['api','API поиска · экспериментально'],['chrome','Обычный Chrome']]){const option=make('option',title);option.value=value;adapter.append(option);}adapterLabel.append(adapter);
  const settingsHelp=make('p','Первый успешный снимок создаёт основу сравнения: старые объявления не будут названы новыми. Сортировка поиска — по дате. Для Авито поддержан публичный HTML или обычный Chrome, без частного API. Охват ограничен выбранными страницами и общим сроком 35 секунд; это не полный каталог.','muted');
  const filters=make('div','','listing-watch-filter-grid'),filterInputs={};
  for(const [key,labelText]of[['minRent','Аренда от, ₽ / месяц'],['maxRent','Аренда до, ₽ / месяц'],['minArea','Площадь от, м²'],['maxArea','Площадь до, м²'],['rooms','Комнаты через запятую · 0 = Studio'],['districts','Районы через запятую']]){const label=make('label',labelText),input=make('input');input.setAttribute('aria-label',labelText);input.type=['rooms','districts'].includes(key)?'text':'number';if(input.type==='number'){input.min='0';input.step='any';}label.append(input);filters.append(label);filterInputs[key]=input;}
  const maxPagesLabel=make('label','Предельное число страниц · 1–8'),maxPages=make('input');maxPages.type='number';maxPages.min='1';maxPages.max='8';maxPages.step='1';maxPages.value='2';maxPages.setAttribute('aria-label','Предельное число страниц');maxPagesLabel.append(maxPages);
  const noticesLabel=make('label','','listing-watch-toggle'),notices=make('input');notices.type='checkbox';notices.setAttribute('aria-label','Получать внутренние уведомления о новых подходящих ссылках');noticesLabel.append(notices,document.createTextNode('Получать уведомления в приложении · без Telegram, почты и внешней отправки'));
  const save=button('Сохранить настройки',()=>saveConfig(),'btn primary'),configNote=make('p','','listing-watch-config-note');configNote.setAttribute('aria-live','polite');
  form.append(sourceLabel,enabledLabel,adapterLabel,maxPagesLabel,filters,noticesLabel,settingsHelp,save,configNote);form.onsubmit=event=>{event.preventDefault();saveConfig();};details.append(summary,form);
  const status=make('p','Откройте раздел, чтобы получить состояние мониторинга.','listing-watch-status');status.setAttribute('role','status');status.setAttribute('aria-live','polite');
  const timing=make('p','','muted listing-watch-timing'),coverage=make('p','','muted listing-watch-coverage');
  const toolbar=make('div','','listing-watch-toolbar'),category=make('select');category.setAttribute('aria-label','Категория найденных объявлений');
  for(const [value,title]of [['new','Новые · подходящие'],['pending','Нужно проверить данные'],['rejected','Не прошли фильтры'],['accepted','Все подходящие'],['all','Все найденные']]){const option=make('option',title);option.value=value;category.append(option);}
  const refreshButton=button('Обновить список',()=>refresh()),check=button('Проверить сейчас',()=>checkNow()),readShown=button('Прочитать показанные',()=>readItems(currentItems.filter(row=>!row.read).map(row=>row.id)));
  toolbar.append(category,refreshButton,check,readShown);
  const list=make('div','','listing-watch-list'),empty=make('p','Список ещё не загружен.','muted'),more=button('Показать ещё',()=>loadMore());more.hidden=true;list.append(empty);
  const noticesPanel=make('details','','listing-watch-settings'),noticesSummary=make('summary','Внутренние уведомления'),noticesList=make('div');noticesPanel.append(noticesSummary,noticesList);
  const logPanel=make('details','','listing-watch-settings'),logSummary=make('summary','Технический журнал импортов и проверок'),logKind=make('select'),logList=make('div');logKind.setAttribute('aria-label','Тип операций в журнале');for(const [value,label]of[['all','Все операции'],['import','Ручные импорты'],['watch','Проверки выдачи']]){const option=make('option',label);option.value=value;logKind.append(option);}logPanel.append(logSummary,logKind,logList);
  shell.append(heading,note,details,status,timing,coverage,toolbar,list,more,noticesPanel,logPanel);mount.replaceChildren(shell);
  let destroyed=false,epoch=0,timer=null,dirty=false,mutation=false,loaded=false,nextBefore=null,currentItems=[],lastStatus=null;
  const scanning=new Set();
  const change=()=>{dirty=true;configNote.textContent='Настройки изменены. Сохраните их на Mac.';};source.oninput=change;enabled.onchange=change;adapter.onchange=change;maxPages.oninput=change;notices.onchange=change;for(const input of Object.values(filterInputs))input.oninput=change;
  function setBusy(value){mutation=value;for(const node of[save,readShown,source,enabled,adapter,maxPages,notices,...Object.values(filterInputs)])node.disabled=value||!api;check.disabled=value||!api||!lastStatus?.config?.searchUrl;refreshButton.disabled=!api;}
  function cancelPoll(){clearTimeout(timer);timer=null;}
  function schedule(){
    cancelPoll();if(destroyed||!loaded||document.visibilityState==='hidden'||!mount.getClientRects().length)return;
    timer=setTimeout(()=>refresh({automatic:true}),lastStatus?.state==='checking'?3000:60000);
  }
  function renderStatus(value){
    const data=value?.status||value||{};lastStatus=data;const config=data.config||{};
    count.textContent=String(Math.max(0,Number(data.unreadCount)||0));
    if(!dirty){source.value=lwText(config.searchUrl,4000);enabled.checked=config.enabled===true;adapter.value=['html','api','chrome'].includes(config.adapter)?config.adapter:'html';maxPages.value=String(config.maxPages??2);notices.checked=config.notifications?.enabled===true;for(const [key,input]of Object.entries(filterInputs))input.value=Array.isArray(config.filters?.[key])?config.filters[key].join(', '):String(config.filters?.[key]??'');}
    noticesSummary.textContent='Внутренние уведомления · '+Math.max(0,Number(data.notificationsUnreadCount)||0)+' непрочитано';
    const states={disabled:'Автоматическая проверка выключена.',idle:'Мониторинг ожидает следующую проверку.',checking:'Mac проверяет сохранённую выдачу…',error:'Последняя проверка завершилась ошибкой.',needs_attention:'Источник требует внимания. Откройте поиск и проверьте доступ.'};
    status.textContent=states[data.state]||'Состояние мониторинга получено.';
    if(data.error)status.textContent+=' '+lwText(data.error,600);
    if(config.searchUrl&&!data.baselineComplete)status.textContent+=' Первый успешный снимок ещё не создан.';
    timing.textContent=[data.lastRun!=null?'Последняя попытка: '+formatWatchDate(data.lastRun):'Проверок пока не было',data.lastSuccess!=null?'Успешно: '+formatWatchDate(data.lastSuccess):'',data.nextRun!=null&&config.enabled?'Следующая: '+formatWatchDate(data.nextRun):''].filter(Boolean).join(' · ');
    const scope=lwText(data.coverageNote||data.coverageMessage||(data.coverage==='first-2-pages'?'первые две страницы выбранного поиска':data.coverage),500);coverage.textContent=(scope?'Охват: '+scope+'. ':'')+(data.coverageInfo?.recognizedItems!=null?'Распознано: '+data.coverageInfo.recognizedItems+'; дубли: '+(data.coverageInfo.duplicateCount??'не измерены')+'; без даты публикации: '+(data.coverageInfo.unknownPublicationDates??'не измерено')+'. ':'')+'Число пропущенных записей неизвестно: порядок выдачи может меняться. Дата размещения и первого обнаружения показаны отдельно.';
    check.disabled=mutation||!api||!config.searchUrl;details.open=details.open||!config.searchUrl;
  }
  function renderItems(){
    list.replaceChildren();
    if(!currentItems.length){const message=!lastStatus?.baselineComplete?'После первого снимка здесь появятся объявления, найденные следующими проверками.':category.value==='new'?'Новых непрочитанных ссылок пока нет.':'В выбранном поиске пока нет сохранённых ссылок.';list.append(make('p',message,'muted'));}
    for(const row of currentItems){
      const card=make('article','','listing-watch-item');card.dataset.watchId=row.id;
      const title=make('h3',row.title),tags=make('div','','listing-watch-meta');
      if(row.isNew&&!row.read)tags.append(make('span','Новое обнаружение','listing-watch-new'));
      if(row.price)tags.append(make('strong',row.price));if(row.area)tags.append(make('span',row.area));if(row.metro)tags.append(make('span',row.metro));if(row.district)tags.append(make('span',row.district));tags.append(make('span',({accepted:'Подходит фильтрам',rejected:'Отклонено фильтрами',pending:'Нужны данные'})[row.decision]));card.append(title,tags);
      const platform=row.platform==='avito'?'Авито':'ЦИАН';
      const published=row.publishedAt!==null?'Размещено в '+platform+': '+formatWatchDate(row.publishedAt):row.publishedLabel?'Отметка '+platform+' при последней проверке'+(row.lastSeen!==null?' ('+formatWatchDate(row.lastSeen)+')':'')+': '+row.publishedLabel:'Дата размещения '+platform+' не указана';
      card.append(make('p',published,'listing-watch-date'),make('p','Впервые найдено нами: '+formatWatchDate(row.firstSeen),'muted listing-watch-date'));
      for(const reason of row.decisionReasons)card.append(make('p',reason,'muted'));
      const actions=make('div','','listing-watch-item-actions');
      if(row.url){const link=make('a','Открыть источник','btn');link.href=row.url;link.target='_blank';link.rel='noopener noreferrer';actions.append(link);}
      const scan=button(scanning.has(row.id)?'Передаём в импорт…':'Сканировать объявление',()=>scanItem(row),'btn primary');scan.disabled=!row.url||typeof onScan!=='function'||scanning.has(row.id)||['removed','archived','inactive'].includes(row.publicationStatus);actions.append(scan);
      if(!row.read){const read=button('Прочитано',()=>readItems([row.id]));read.disabled=mutation||!row.id;actions.append(read);}card.append(actions);list.append(card);
    }
    more.hidden=nextBefore==null;more.disabled=mutation;readShown.disabled=mutation||!currentItems.some(row=>row.id&&!row.read)||!api;
  }
  const itemPath=before=>'listing-watch/items?category='+encodeURIComponent(category.value)+'&limit=50'+(before!=null?'&before='+encodeURIComponent(String(before)):'');
  function applyItems(value,append=false){
    const rows=Array.isArray(value?.items)?value.items:[];const normalized=rows.map(normalizeWatchItem).filter(row=>row.id);
    currentItems=[...new Map([...(append?currentItems:[]),...normalized].map(row=>[row.id,row])).values()];
    nextBefore=value?.hasMore&&value?.nextBefore!=null?value.nextBefore:null;renderItems();
  }
  async function refresh({automatic=false}={}){
    if(destroyed||!api)return false;cancelPoll();
    if(automatic&&(document.visibilityState==='hidden'||!mount.getClientRects().length))return false;
    const request=++epoch;refreshButton.disabled=true;if(!loaded)status.textContent='Получаем состояние и новые ссылки…';
    try{
      const [state,rows]=await Promise.all([api('listing-watch'),api(itemPath())]);if(destroyed||request!==epoch)return false;
      loaded=true;renderStatus(state);applyItems(rows);if(noticesPanel.open)await loadNotices();if(logPanel.open)await loadLog();return true;
    }catch(error){if(!destroyed&&request===epoch){status.textContent=lwText(error?.message,600)||'Не удалось получить мониторинг.';if(!automatic)toast(status.textContent);}return false;}
    finally{if(!destroyed&&request===epoch){refreshButton.disabled=false;schedule();}}
  }
  async function mutate(run){
    if(destroyed||mutation||!api)return false;cancelPoll();setBusy(true);
    try{await run();return true;}catch(error){if(!destroyed){status.textContent=lwText(error?.message,600)||'Операция не завершена.';toast(status.textContent);}return false;}
    finally{if(!destroyed){setBusy(false);renderItems();schedule();}}
  }
  async function saveConfig(){
    const searchUrl=source.value.trim()?safeWatchSearchUrl(source.value):'';
    if(source.value.trim()&&!searchUrl||enabled.checked&&!searchUrl){configNote.textContent='Нужна HTTPS-ссылка на поиск квартир ЦИАН/Авито, а не на отдельное объявление.';return false;}
    const count=Number(maxPages.value);if(!Number.isInteger(count)||count<1||count>8){configNote.textContent='Охват: от 1 до 8 страниц.';return false;}
    if(searchUrl.includes('avito.ru')&&adapter.value==='api'){configNote.textContent='API выдачи Авито не подключён. Выберите публичный HTML или обычный Chrome.';return false;}
    const selectedFilters={};for(const [key,input]of Object.entries(filterInputs)){if(!input.value.trim())continue;if(key==='districts')selectedFilters[key]=input.value.split(',').map(value=>value.trim()).filter(Boolean);else if(key==='rooms'){selectedFilters[key]=input.value.split(/[,\s]+/u).map(Number);if(selectedFilters[key].some(value=>!Number.isInteger(value)||value<0||value>20)){configNote.textContent='Комнаты: целые числа от 0 до 20 через запятую.';return false;}}else{const number=Number(input.value);if(!Number.isFinite(number)||number<0){configNote.textContent='Числовые фильтры должны быть неотрицательными.';return false;}selectedFilters[key]=number;}}
    const value={enabled:enabled.checked,searchUrl,intervalMinutes:60,adapter:adapter.value,maxPages:count,filters:selectedFilters,notifications:{enabled:notices.checked,channel:'in-app'}};
    return mutate(async()=>{const result=await api('listing-watch',value);if(destroyed)return;dirty=false;configNote.textContent='Настройки сохранены на Mac.';renderStatus(result);await refresh();});
  }
  async function checkNow(){return mutate(async()=>{const result=await api('listing-watch/check',{});if(destroyed)return;renderStatus(result);await refresh();});}
  async function readItems(ids){const selected=[...new Set(ids.filter(id=>typeof id==='string'&&id))];if(!selected.length)return false;return mutate(async()=>{await api('listing-watch/read',{ids:selected});await refresh();});}
  async function loadMore(){
    if(nextBefore==null||mutation||!api||destroyed)return false;const request=++epoch,before=nextBefore;more.disabled=true;
    try{const value=await api(itemPath(before));if(destroyed||request!==epoch)return false;applyItems(value,true);return true;}
    catch(error){if(!destroyed&&request===epoch)toast(lwText(error?.message,500)||'Не удалось загрузить следующую страницу.');return false;}
    finally{if(!destroyed&&request===epoch){more.disabled=false;schedule();}}
  }
  async function scanItem(row){
    if(destroyed||!row.url||typeof onScan!=='function'||scanning.has(row.id))return;scanning.add(row.id);renderItems();
    try{await onScan(row.url);}catch(error){if(!destroyed)toast(lwText(error?.message,500)||'Не удалось открыть импорт.');}
    finally{scanning.delete(row.id);if(!destroyed)renderItems();}
  }
  let noticesEpoch=0,logEpoch=0;
  async function loadNotices(){if(!api||destroyed)return;const request=++noticesEpoch,sourceUrl=lastStatus?.config?.searchUrl;noticesList.textContent='Загружаем внутренний канал…';try{const response=await api('listing-watch/notifications');if(destroyed||request!==noticesEpoch||sourceUrl!==lastStatus?.config?.searchUrl)return;noticesList.replaceChildren();for(const row of response.items||[]){const item=normalizeWatchItem(row),article=make('article','','listing-watch-item');article.append(make('strong',item.title),make('p','Найдено: '+formatWatchDate(row.created),'muted'));if(item.url){const link=make('a','Открыть источник','btn');link.href=item.url;link.target='_blank';link.rel='noopener noreferrer';article.append(link);}if(row.read!==true)article.append(button('Прочитано',()=>readItems([item.id])));noticesList.append(article);}if(!noticesList.childElementCount)noticesList.textContent='Сообщений нет. Внутренние уведомления создаются только после явного включения и нового подходящего обнаружения.';}catch(error){if(!destroyed&&request===noticesEpoch)noticesList.textContent=lwText(error.message,500);}}
  async function loadLog(){if(!api||destroyed)return;const request=++logEpoch;logList.textContent='Загружаем технические измерения…';try{const response=await api('activity-log?kind='+encodeURIComponent(logKind.value)+'&limit=50');if(destroyed||request!==logEpoch)return;logList.replaceChildren();for(const row of response.items||[]){const article=make('article','','listing-watch-item');article.append(make('strong',(row.kind==='import'?'Ручной импорт':'Проверка выдачи')+' · '+lwText(row.outcome,40)),make('p',formatWatchDate(row.at)+' · '+lwText(row.source,40)+' · '+(Number.isFinite(row.elapsed)?row.elapsed.toFixed(3)+' с':'длительность неизвестна'),'muted'));if(row.reason)article.append(make('p',lwText(row.reason,500)));const stages=Object.entries(row.stages||{}).filter(([,value])=>typeof value==='number'&&Number.isFinite(value)).map(([name,value])=>lwText(name,40)+': '+value.toFixed(3)+' с');if(stages.length)article.append(make('p',stages.join(' · '),'muted'));logList.append(article);}if(!logList.childElementCount)logList.textContent='Завершённых операций пока нет. Журнал содержит только безопасные технические поля, без HTML, cookies и заголовков запросов.';}catch(error){if(!destroyed&&request===logEpoch)logList.textContent=lwText(error.message,500);}}
  noticesPanel.addEventListener('toggle',()=>{if(noticesPanel.open)loadNotices();});logPanel.addEventListener('toggle',()=>{if(logPanel.open)loadLog();});logKind.onchange=loadLog;
  const visibleAgain=()=>{if(loaded&&!destroyed&&document.visibilityState!=='hidden'&&mount.getClientRects().length)refresh({automatic:true});};
  document.addEventListener('visibilitychange',visibleAgain);
  category.onchange=()=>{nextBefore=null;refresh();};setBusy(false);
  if(!api){status.textContent='Мониторинг работает через запущенный сервер Mac.';details.open=false;category.disabled=true;more.disabled=true;}
  return {refresh,destroy(){if(destroyed)return;destroyed=true;++epoch;cancelPoll();document.removeEventListener('visibilitychange',visibleAgain);mount.replaceChildren();currentItems=[];scanning.clear();}};
}
