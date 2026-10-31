// Temporary, authenticated diagnostics. This widget never starts an import.
export function setupImportDiagnostics({mount,api,getBundle,toast=()=>{}}={}){
 if(!mount)return null;
 let destroyed=false,revision=0,timer=0,busy=false,state=null;
 const make=(tag,text='',className='')=>{const element=document.createElement(tag);element.textContent=text;if(className)element.className=className;return element;};
 const panel=make('details','','import-audit'),summary=make('summary','Диагностика · 10 ручных проверок'),note=make('p','Записываются этапы загрузки, прокрутки и чтения. После десяти запусков сбор выключается и Mac создаёт tg.zip.','import-audit-note'),status=make('p','','import-audit-status'),actions=make('div','','import-audit-actions'),arm=make('button','Начать 10 проверок'),refreshButton=make('button','Обновить'),download=make('button','Скачать tg.zip'),checks=make('ol','','import-audit-checks');
 for(const button of [arm,refreshButton,download])button.type='button';
 actions.append(arm,refreshButton,download);panel.append(summary,note,status,actions,checks);mount.replaceChildren(panel);download.hidden=true;
 const visible=()=>!document.hidden&&mount.getClientRects().length>0;
 function schedule(){clearTimeout(timer);if(!destroyed&&visible()&&state&&['armed','finishing'].includes(state.state))timer=setTimeout(()=>refresh(),5000);}
 function draw(value){
  state=value;const completed=Number(value.completed)||0,target=Number(value.target)||10;
  summary.textContent='Диагностика · '+completed+' / '+target+' проверок';
  const labels={idle:'Сбор ещё не запущен.',armed:'Следующие ручные запуски ЦИАН записываются.',finishing:'Все проверки приняты. Ожидаем окончания активных запусков.',complete:'Сбор завершён. Обычный импорт продолжает работать.',paused:'Сбор приостановлен.'};
  status.textContent=(labels[value.state]||'Проверяем состояние.')+(value.inFlight?' Сейчас выполняется: '+value.inFlight+'.':'')+(value.bundleError?' Архив не создан: '+value.bundleError:'');
  arm.disabled=busy||['armed','finishing'].includes(value.state);arm.textContent=value.state==='paused'?'Продолжить сбор':value.state==='complete'?'Новая серия из 10 проверок':'Начать 10 проверок';
  download.hidden=!value.bundleReady;download.disabled=busy;checks.replaceChildren();
  for(const row of value.checks||[]){const item=make('li'),labels={running:'выполняется',success:'готово',error:'ошибка',cancelled:'отменено',interrupted:'прервано'};item.textContent='№'+row.number+' · '+(labels[row.outcome]||row.outcome)+' · '+Math.round(Number(row.elapsedSeconds)||0)+' с';checks.append(item);}
 }
 async function refresh(){
  const current=++revision;clearTimeout(timer);
  try{const value=await api('import-diagnostics');if(destroyed||current!==revision)return;draw(value);}
  catch(error){if(!destroyed&&current===revision)status.textContent=error.message;}
  finally{if(!destroyed&&current===revision)schedule();}
 }
 arm.onclick=async()=>{if(busy)return;busy=true;arm.disabled=true;const current=++revision;try{const value=await api('import-diagnostics',{action:'arm',target:10});if(!destroyed&&current===revision)draw(value);}catch(error){if(!destroyed)status.textContent=error.message;}finally{busy=false;if(!destroyed){if(state)draw(state);schedule();}}};
 refreshButton.onclick=()=>refresh();
 download.onclick=async()=>{if(busy||!getBundle)return;busy=true;download.disabled=true;try{const blob=await getBundle();if(destroyed)return;const url=URL.createObjectURL(blob),link=make('a');link.href=url;link.download='tg.zip';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);toast('tg.zip подготовлен к скачиванию');}catch(error){if(!destroyed)status.textContent=error.message;}finally{busy=false;if(!destroyed)download.disabled=false;}};
 panel.ontoggle=()=>{if(panel.open)refresh();};
 const visibility=()=>{if(document.hidden)clearTimeout(timer);else if(visible())refresh();};document.addEventListener('visibilitychange',visibility);
 return{refresh,destroy(){destroyed=true;revision++;clearTimeout(timer);document.removeEventListener('visibilitychange',visibility);mount.replaceChildren();}};
}
