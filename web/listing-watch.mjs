// The monitor stores links and dates; a full import is always an explicit action.
const lwText=(value,limit=400)=>typeof value==='string'?value.replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/g,'').slice(0,limit):'';
const lwCianHost=host=>host==='cian.ru'||host==='www.cian.ru';

export function safeWatchOfferUrl(value){
  try{
    const url=new URL(String(value??''));
    if(url.protocol!=='https:'||!lwCianHost(url.hostname)||url.username||url.password||url.port&&url.port!=='443')return '';
    const match=url.pathname.match(/^\/(rent|sale)\/flat\/(\d{1,20})\/?$/);
    return match?'https://www.cian.ru/'+match[1]+'/flat/'+match[2]+'/':'';
  }catch{return '';}
}

export function safeWatchSearchUrl(value){
  try{
    const url=new URL(String(value??'').trim());
    if(url.protocol!=='https:'||!lwCianHost(url.hostname)||url.username||url.password||url.port&&url.port!=='443')return '';
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
  return {id,url:safeWatchOfferUrl(row.url),title:lwText(row.title,400)||'Объявление ЦИАН',price,area:typeof row.area==='number'&&Number.isFinite(row.area)&&row.area>0?row.area+' м²':lwText(row.area,80),metro:lwText(row.metro,150),publishedAt:published,publishedLabel:lwText(row.publishedLabel,160),publicationDateSource:lwText(row.publicationDateSource,80),firstSeen:lwDateParts(row.firstSeen)?row.firstSeen:null,lastSeen:lwDateParts(row.lastSeen)?row.lastSeen:null,read:row.read===true,isNew:row.isNew===true};
}

export function setupListingWatch({mount,api,onScan,toast=()=>{}}={}){
  if(!mount)return {refresh:async()=>false,destroy:()=>{}};
  const make=(tag,text='',className='')=>{const element=document.createElement(tag);element.textContent=text;if(className)element.className=className;return element;};
  const button=(text,action,className='btn')=>{const element=make('button',text,className);element.type='button';element.onclick=action;return element;};
  const shell=make('section','','listing-watch'),heading=make('div','','listing-watch-heading'),headingText=make('div'),count=make('span','0','listing-watch-count');
  headingText.append(make('div','ЦИАН · МОНИТОРИНГ','eyebrow'),make('h2','Новые объявления'));
  count.setAttribute('aria-label','Непрочитанных объявлений');heading.append(headingText,count);
  const note=make('p','Раз в час Mac проверяет выбранный поиск. В «Новые» попадают ссылки, впервые найденные после начального снимка. Полный разбор запускается вами отдельно.','muted');
  const details=make('details','','listing-watch-settings'),summary=make('summary','Настроить поиск и обновление');
  const form=make('form'),sourceLabel=make('label','Ссылка на поиск ЦИАН'),source=make('input');source.type='url';source.inputMode='url';source.autocomplete='off';source.placeholder='https://www.cian.ru/cat.php?...';source.setAttribute('aria-label','Ссылка на поиск ЦИАН');source.maxLength=4000;sourceLabel.append(source);
  const enabledLabel=make('label','','listing-watch-toggle'),enabled=make('input');enabled.type='checkbox';enabled.setAttribute('aria-label','Проверять поиск каждый час');enabledLabel.append(enabled,document.createTextNode('Проверять каждый час, пока Mac включён'));
  const adapterLabel=make('label','Способ чтения'),adapter=make('select');adapter.setAttribute('aria-label','Способ чтения ЦИАН');
  for(const [value,title]of [['html','Страница поиска'],['api','API поиска · экспериментально'],['chrome','Обычный Chrome']]){const option=make('option',title);option.value=value;adapter.append(option);}adapterLabel.append(adapter);
  const settingsHelp=make('p','Первый успешный снимок создаёт основу сравнения: старые объявления не будут названы новыми. Проверяется ограниченная часть выдачи, поэтому это не полный каталог ЦИАН.','muted');
  const save=button('Сохранить настройки',()=>saveConfig(),'btn primary'),configNote=make('p','','listing-watch-config-note');configNote.setAttribute('aria-live','polite');
  form.append(sourceLabel,enabledLabel,adapterLabel,settingsHelp,save,configNote);form.onsubmit=event=>{event.preventDefault();saveConfig();};details.append(summary,form);
  const status=make('p','Откройте раздел, чтобы получить состояние мониторинга.','listing-watch-status');status.setAttribute('role','status');status.setAttribute('aria-live','polite');
  const timing=make('p','','muted listing-watch-timing'),coverage=make('p','','muted listing-watch-coverage');
  const toolbar=make('div','','listing-watch-toolbar'),category=make('select');category.setAttribute('aria-label','Категория найденных объявлений');
  for(const [value,title]of [['new','Новые · непрочитанные'],['all','Все найденные']]){const option=make('option',title);option.value=value;category.append(option);}
  const refreshButton=button('Обновить список',()=>refresh()),check=button('Проверить сейчас',()=>checkNow()),readShown=button('Прочитать показанные',()=>readItems(currentItems.filter(row=>!row.read).map(row=>row.id)));
  toolbar.append(category,refreshButton,check,readShown);
  const list=make('div','','listing-watch-list'),empty=make('p','Список ещё не загружен.','muted'),more=button('Показать ещё',()=>loadMore());more.hidden=true;list.append(empty);shell.append(heading,note,details,status,timing,coverage,toolbar,list,more);mount.replaceChildren(shell);
  let destroyed=false,epoch=0,timer=null,dirty=false,mutation=false,loaded=false,nextBefore=null,currentItems=[],lastStatus=null;
  const scanning=new Set();
  const change=()=>{dirty=true;configNote.textContent='Настройки изменены. Сохраните их на Mac.';};source.oninput=change;enabled.onchange=change;adapter.onchange=change;
  function setBusy(value){mutation=value;save.disabled=readShown.disabled=source.disabled=enabled.disabled=adapter.disabled=value||!api;check.disabled=value||!api||!lastStatus?.config?.searchUrl;refreshButton.disabled=!api;}
  function cancelPoll(){clearTimeout(timer);timer=null;}
  function schedule(){
    cancelPoll();if(destroyed||!loaded||document.visibilityState==='hidden'||!mount.getClientRects().length)return;
    timer=setTimeout(()=>refresh({automatic:true}),lastStatus?.state==='checking'?3000:60000);
  }
  function renderStatus(value){
    const data=value?.status||value||{};lastStatus=data;const config=data.config||{};
    count.textContent=String(Math.max(0,Number(data.unreadCount)||0));
    if(!dirty){source.value=lwText(config.searchUrl,4000);enabled.checked=config.enabled===true;adapter.value=['html','api','chrome'].includes(config.adapter)?config.adapter:'html';}
    const states={disabled:'Автоматическая проверка выключена.',idle:'Мониторинг ожидает следующую проверку.',checking:'Mac проверяет выдачу ЦИАН…',error:'Последняя проверка завершилась ошибкой.',needs_attention:'Источник требует внимания. Откройте поиск и проверьте доступ.'};
    status.textContent=states[data.state]||'Состояние мониторинга получено.';
    if(data.error)status.textContent+=' '+lwText(data.error,600);
    if(config.searchUrl&&!data.baselineComplete)status.textContent+=' Первый успешный снимок ещё не создан.';
    timing.textContent=[data.lastRun!=null?'Последняя попытка: '+formatWatchDate(data.lastRun):'Проверок пока не было',data.lastSuccess!=null?'Успешно: '+formatWatchDate(data.lastSuccess):'',data.nextRun!=null&&config.enabled?'Следующая: '+formatWatchDate(data.nextRun):''].filter(Boolean).join(' · ');
    const scope=lwText(data.coverageNote||data.coverageMessage||(data.coverage==='first-2-pages'?'первые две страницы выбранного поиска':data.coverage),500);coverage.textContent=(scope?'Охват: '+scope+'. ':'')+'Дата размещения из источника и дата первого обнаружения показаны отдельно.';
    check.disabled=mutation||!api||!config.searchUrl;details.open=details.open||!config.searchUrl;
  }
  function renderItems(){
    list.replaceChildren();
    if(!currentItems.length){const message=!lastStatus?.baselineComplete?'После первого снимка здесь появятся объявления, найденные следующими проверками.':category.value==='new'?'Новых непрочитанных ссылок пока нет.':'В выбранном поиске пока нет сохранённых ссылок.';list.append(make('p',message,'muted'));}
    for(const row of currentItems){
      const card=make('article','','listing-watch-item');card.dataset.watchId=row.id;
      const title=make('h3',row.title),tags=make('div','','listing-watch-meta');
      if(row.isNew&&!row.read)tags.append(make('span','Новое обнаружение','listing-watch-new'));
      if(row.price)tags.append(make('strong',row.price));if(row.area)tags.append(make('span',row.area));if(row.metro)tags.append(make('span',row.metro));card.append(title,tags);
      const published=row.publishedAt!==null?'Размещено в ЦИАН: '+formatWatchDate(row.publishedAt):row.publishedLabel?'Отметка ЦИАН при последней проверке'+(row.lastSeen!==null?' ('+formatWatchDate(row.lastSeen)+')':'')+': '+row.publishedLabel:'Дата размещения ЦИАН не указана';
      card.append(make('p',published,'listing-watch-date'),make('p','Впервые найдено нами: '+formatWatchDate(row.firstSeen),'muted listing-watch-date'));
      const actions=make('div','','listing-watch-item-actions');
      if(row.url){const link=make('a','Открыть источник','btn');link.href=row.url;link.target='_blank';link.rel='noopener noreferrer';actions.append(link);}
      const scan=button(scanning.has(row.id)?'Передаём в импорт…':'Сканировать объявление',()=>scanItem(row),'btn primary');scan.disabled=!row.url||typeof onScan!=='function'||scanning.has(row.id);actions.append(scan);
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
      loaded=true;renderStatus(state);applyItems(rows);return true;
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
    if(source.value.trim()&&!searchUrl||enabled.checked&&!searchUrl){configNote.textContent='Нужна HTTPS-ссылка на поиск квартир ЦИАН, а не на отдельное объявление.';return false;}
    const value={enabled:enabled.checked,searchUrl,intervalMinutes:60,adapter:adapter.value};
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
  const visibleAgain=()=>{if(loaded&&!destroyed&&document.visibilityState!=='hidden'&&mount.getClientRects().length)refresh({automatic:true});};
  document.addEventListener('visibilitychange',visibleAgain);
  category.onchange=()=>{nextBefore=null;refresh();};setBusy(false);
  if(!api){status.textContent='Мониторинг работает через запущенный сервер Mac.';details.open=false;category.disabled=true;more.disabled=true;}
  return {refresh,destroy(){if(destroyed)return;destroyed=true;++epoch;cancelPoll();document.removeEventListener('visibilitychange',visibleAgain);mount.replaceChildren();currentItems=[];scanning.clear();}};
}
