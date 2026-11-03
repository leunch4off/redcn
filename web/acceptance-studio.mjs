// Authenticated operator observations. Never turn a report into a roadmap
// checkbox, infer an iPhone model, or send an invitation/message.
export function acceptanceClientEnvironment({window:win=globalThis.window,navigator:nav=globalThis.navigator,keyboard={}}={}){
 const visual=win.visualViewport;
 return {origin:win.location.origin,userAgent:String(nav.userAgent||'').slice(0,400),
  standalone:nav.standalone===true||win.matchMedia?.('(display-mode: standalone)').matches===true,
  secureContext:win.isSecureContext===true,viewportWidth:Math.round(win.innerWidth),viewportHeight:Math.round(win.innerHeight),
  visualViewportHeight:Math.round(visual?.height||win.innerHeight),reducedViewportObserved:keyboard.reducedViewportObserved===true,
  shareAvailable:typeof nav.share==='function',fileShareAvailable:typeof nav.canShare==='function'};
}

export function setupAcceptanceStudio({mount,api,toast=()=>{},getClient=()=>acceptanceClientEnvironment()}={}){
 if(!mount||typeof api!=='function')return null;
 const make=(tag,text='',props={})=>{const n=document.createElement(tag);n.textContent=text;Object.assign(n,props);return n;};
 const details=make('details','',{className:'card learning-studio acceptance-studio'});
 details.append(make('summary','Проверки телефона и редактура'));
 const status=make('p','Записывайте только то, что действительно проверили. Этот журнал сам не меняет roadmap.',{role:'status'}),form=make('div'),rows=make('div');
 const field=(label,node,parent=form)=>{const wrap=make('label',label);node.setAttribute('aria-label',label);wrap.append(node);parent.append(wrap);return node;};
 const task=field('Какая проверка выполнена',make('select'));
 const model=field('Устройство · укажите модель вручную',make('input','',{type:'text',maxLength:160,placeholder:'Например, iPhone 14 Pro'}));
 const browser=field('Браузер и версия',make('input','',{type:'text',maxLength:160,placeholder:'Например, Safari 18'}));
 const os=field('Операционная система',make('input','',{type:'text',maxLength:160,placeholder:'Например, iOS 18'}));
 const network=field('Сеть во время проверки',make('select'));
 for(const [value,label]of [['unknown','Не определена'],['wifi','Wi-Fi'],['cellular','Мобильная сеть · Wi-Fi выключен'],['other','Другая сеть']])network.append(make('option',label,{value}));
 const checks=make('fieldset'),legend=make('legend','Фактические результаты');checks.append(legend);form.append(checks);
 const reviewerFields=make('div'),reviewer=field('Рецензент · имя или псевдоним',make('input','',{maxLength:160}),reviewerFields),relationship=field('Кто выполнил редактуру',make('select'),reviewerFields);
 for(const [value,label]of [['unknown','Не подтверждено'],['independent-person','Отдельный человек'],['same-operator','Я сам']])relationship.append(make('option',label,{value}));
 const native=field('Рецензент подтвердил, что китайский — родной язык',make('input','',{type:'checkbox'}),reviewerFields);form.append(reviewerFields);
 const notes=field('Наблюдения, ошибки и правки · ссылки на проверенные примеры',make('textarea','',{rows:5,maxLength:4000,placeholder:'Что получилось, что не получилось, что исправлено. Не указывайте пароли или код подключения.'}));
 const environment=make('p','',{className:'quiet'}),actions=make('div','',{className:'output-options'}),save=make('button','Сохранить фактическую проверку',{type:'button',className:'btn'}),refreshButton=make('button','Обновить журнал',{type:'button',className:'btn'}),exportButton=make('button','Скачать весь журнал JSON',{type:'button',className:'btn'});
 actions.append(save,refreshButton,exportButton);details.append(status,form,environment,actions,rows);mount.append(details);
 let current=null,revision=0,formVersion=0,destroyed=false,busy=false,eventId=null,pendingReport=null;
 const resultControls=new Map();
 function switchTask(){checks.replaceChildren(legend);resultControls.clear();for(const [key,label]of Object.entries(current?.tasks?.[task.value]?.checks||{})){
  const select=field(label,make('select'),checks);for(const[value,text]of [['not-run','Не проверялось'],['pass','Проверено · получилось'],['fail','Проверено · обнаружена проблема']])select.append(make('option',text,{value}));resultControls.set(key,select);
 }reviewerFields.hidden=task.value!=='RCN-262';eventId=null;pendingReport=null;}
 function environmentText(){const c=getClient();environment.textContent='Наблюдения браузера: '+c.viewportWidth+'×'+c.viewportHeight+'; видимая высота '+c.visualViewportHeight+'; '+(c.standalone?'запуск как приложение':'запуск в браузере')+'. Модель телефона и мобильную сеть браузер надёжно не подтверждает.';return c;}
 function render(value){current=value;const selected=task.value;task.replaceChildren();for(const[id,definition]of Object.entries(value.tasks||{}))task.append(make('option',id+' · '+definition.title,{value:id}));if(value.tasks?.[selected])task.value=selected;switchTask();rows.replaceChildren();
  status.textContent='Свидетельств на Mac: '+Number(value.total||0)+'. Записи оператора не являются автоматическим заключением о приёмке.';
  for(const row of value.records||[]){const section=make('section','',{className:'card-file-row'});const text=make('div');text.append(make('strong',row.taskId+' · '+(value.tasks?.[row.taskId]?.title||'')),make('p',new Date(row.recordedAt).toLocaleString()+' · '+row.device.model+' · '+row.device.network),make('p',row.notes),make('p',Object.entries(row.checks).map(([key,result])=>(value.tasks?.[row.taskId]?.checks?.[key]||key)+': '+({'pass':'проверено','fail':'проблема','not-run':'не проверялось'}[result]||result)).join('\n')),make('p','Источник: сообщение авторизованного оператора. Roadmap автоматически не менялся.',{className:'quiet'}));section.append(text);rows.append(section);}environmentText();
 }
 async function run(action){if(busy||destroyed)return;busy=true;details.setAttribute('aria-busy','true');save.disabled=refreshButton.disabled=exportButton.disabled=true;try{await action();}catch(error){if(!destroyed){status.textContent=String(error?.message||'Не удалось записать проверку');toast(status.textContent);}}finally{busy=false;if(!destroyed){details.setAttribute('aria-busy','false');save.disabled=refreshButton.disabled=exportButton.disabled=false;}}}
 async function refresh(){const expected=++revision,value=await api('acceptance-evidence');if(!destroyed&&expected===revision)render(value);return value;}
 function invalidate(){formVersion++;eventId=null;pendingReport=null;}
 form.addEventListener('input',invalidate);form.addEventListener('change',invalidate);task.onchange=switchTask;
 save.onclick=()=>run(async()=>{if(!eventId)eventId='acceptance-'+Date.now()+'-'+Math.random().toString(36).slice(2);if(!pendingReport){pendingReport={action:'record',eventId,taskId:task.value,device:{model:model.value,browser:browser.value,os:os.value,network:network.value},client:environmentText(),checks:Object.fromEntries([...resultControls].map(([key,node])=>[key,node.value])),notes:notes.value};if(task.value==='RCN-262')pendingReport.reviewer={name:reviewer.value,relationship:relationship.value,nativeSpeakerReported:native.checked};}const expected=++revision,expectedForm=formVersion,value=await api('acceptance-evidence',pendingReport);if(!destroyed&&expected===revision){if(expectedForm===formVersion){render(value);notes.value='';eventId=null;toast('Фактические наблюдения сохранены на Mac');}else status.textContent='Предыдущая проверка сохранена. Новые правки формы сохранены; отправьте их отдельно.';}});
 refreshButton.onclick=()=>run(refresh);exportButton.onclick=()=>run(async()=>{const value=await api('acceptance-evidence',{action:'export'});if(destroyed)return;const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'})),a=make('a','',{href:url,download:'redcn-acceptance-evidence.json'});a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
 details.ontoggle=()=>{if(details.open&&!current)run(refresh);};
 return {refresh:()=>run(refresh),clear(){revision++;current=null;eventId=null;pendingReport=null;rows.replaceChildren();status.textContent='Войдите заново, чтобы открыть журнал проверок.';for(const node of [model,browser,os,notes,reviewer])node.value='';native.checked=false;network.value='unknown';task.replaceChildren();checks.replaceChildren(legend);resultControls.clear();},destroy(){destroyed=true;revision++;details.remove();}};
}
