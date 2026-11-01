import {setupTagPostStats} from './tag-post-stats.mjs';
// Private editorial memory. All user values enter the DOM as text.
export function learningTextLanguage(text){return /\p{Script=Han}/u.test(String(text))?'zh':'ru';}
export function learningGroundingFields(fields={}){
 const keys=['city','district','districtZh','complex','metro','metroLine','walk','area','rooms','floor','floors','price','priceCurrency','pricePeriod','deposit','depositCurrency','layout','separate','bedrooms','livingRooms','children','pets','petPolicy','childrenPolicy','registration','serviceConfirmations','features','featureCounts','photoRejectedFeatures','utilities','ceiling','balconies','loggias','universityRoutes'];
 return Object.fromEntries(keys.filter(key=>Object.hasOwn(fields,key)).map(key=>[key,fields[key]]));
}

export function setupLearningStudio({mount,api,toast=()=>{},getGroup=()=>'',getFields=()=>({}),getOutput=()=>({}),onApply,canManageSettings=()=>true}={}){
 if(!mount||typeof api!=='function')return null;
 const make=(tag,text='',props={})=>{const value=document.createElement(tag);value.textContent=text;Object.assign(value,props);return value;};
 const details=make('details'),heading=make('summary','Память и обучение локального ИИ');
 details.className='card learning-studio';
 const intro=make('p','Ваши правки накапливаются на Mac. Утверждённые примеры используются для словаря, подсказок и будущего обучения. Число записей памяти не равно числу параметров модели.',{className:'muted'});
 const status=make('p','Откройте блок, чтобы получить состояние памяти.',{role:'status'}),controls=make('div','',{className:'output-options'}),rows=make('div');
 const schedulerStatus=make('p','Состояние фонового обучения будет получено с Mac.',{className:'muted',role:'status'}),schedulerForm=make('div'),schedulerEnabled=fieldScheduler();
 function fieldScheduler(){const wrapper=make('label',' Обучать адаптер во время простоя Mac'),input=make('input','',{type:'checkbox'});wrapper.prepend(input);schedulerForm.append(wrapper);return input;}
 let revision=0,busy=false;
 const button=(parent,text,action)=>{const node=make('button',text,{type:'button',className:'btn'});node.onclick=()=>run(action);parent.append(node);return node;};
 const field=(parent,label,node)=>{const wrapper=make('label',label);node.setAttribute('aria-label',label.trim());wrapper.append(node);parent.append(wrapper);return node;};
 const form=make('div','',{className:'learning-form'}),kind=field(form,'Что запомнить',make('select'));
 for(const [value,label]of [['dictionary','Точный перевод / словарь'],['translation','Пример перевода RU ↔ 中文'],['title','Пример заголовка'],['description','Пример описания'],['furniture','Мебель и техника вручную'],['tag_stat','Статистика тега']])kind.append(make('option',label,{value}));
 const ru=field(form,'Русский текст',make('textarea','',{rows:3,maxLength:12000})),zh=field(form,'Китайский текст',make('textarea','',{rows:3,maxLength:12000}));
 const text=field(form,'Текст примера / предметы через запятую',make('textarea','',{rows:4,maxLength:12000}));
 const tag=field(form,'Хештег',make('input','',{type:'text',maxLength:100,placeholder:'#莫斯科租房'})),views=field(form,'Просмотры · ваша приблизительная оценка',make('input','',{type:'number',min:0,max:10000000000,step:1,inputMode:'numeric'}));
 const date=field(form,'Дата наблюдения',make('input','',{type:'date',value:new Date().toISOString().slice(0,10)})),context=field(form,'Контекст статистики · например, теги квартиры у метро',make('input','',{type:'text',maxLength:500}));
 const reviewed=field(form,' Я проверил содержание и разрешаю использовать его',make('input','',{type:'checkbox'}));
 const formActions=make('div','',{className:'output-options'});
 const filters=make('select');for(const [value,label]of [['pending','Ожидают проверки'],['approved','Утверждены'],['rejected','Отклонены'],['','Все записи']])filters.append(make('option',label,{value}));
 async function run(action){if(busy)return;busy=true;details.setAttribute('aria-busy','true');try{await action();}catch(error){const message=String(error?.message||'Не удалось обновить память');status.textContent=message;draftStatus.textContent=message;translationStatus.textContent=message;toast(message);}finally{busy=false;details.setAttribute('aria-busy','false');}}
 function displayFields(){const pair=['dictionary','translation'].includes(kind.value),stat=kind.value==='tag_stat';ru.parentElement.hidden=!pair;zh.parentElement.hidden=!pair;text.parentElement.hidden=pair||stat;for(const node of [tag,views,date,context])node.parentElement.hidden=!stat;}
 kind.onchange=displayFields;displayFields();
 function rowText(row){const p=row.payload||{};return row.kind==='dictionary'||row.kind==='translation'?[p.ru,p.zh].join(' → '):row.kind==='tag_stat'?[p.tag,p.views===undefined?'Просмотры не заданы':String(p.views)+' просмотров',p.observedAt,p.context].filter(Boolean).join(' · '):row.kind==='furniture'?(p.items||[]).join(', '):p.text||'';}
 function render(value){
  const s=value.status||{},counts=s.counts||s;
  status.textContent='На проверке: '+Number(counts.pending||0)+' · утверждено: '+Number(counts.approved||0)+' · отклонено: '+Number(counts.rejected||0)+'. Обучение весов и приёмка новой версии выполняются отдельно.';
  const scheduler=value.scheduler||{},config=scheduler.config||scheduler,model=value.text_ai||{};schedulerEnabled.checked=config.enabled===true;schedulerForm.hidden=!canManageSettings();
  schedulerStatus.textContent=[model.installed?'Текстовая модель установлена'+(model.enabled?' и включена':''): 'Текстовая модель ещё не установлена',scheduler.message||'Ожидаем состояние планировщика',model.trainedParameters?'Обученных параметров адаптера: '+Number(model.trainedParameters).toLocaleString('ru-RU'):'',scheduler.stepsCompleted?'Выполнено шагов: '+Number(scheduler.stepsCompleted):'',scheduler.adapterReady?'Кандидат адаптера сохранён; требуется приёмка перед подключением':''].filter(Boolean).join(' · ');
  rows.replaceChildren();
  for(const row of value.events||[]){const block=make('article','',{className:'card-file-row'}),body=make('div');body.append(make('strong',({dictionary:'Словарь',translation:'Перевод',title:'Заголовок',description:'Описание',furniture:'Мебель и техника',tag_stat:'Статистика тега'}[row.kind]||row.kind)+' · '+({pending:'на проверке',approved:'утверждено',rejected:'отклонено'}[row.status]||row.status)),make('p',rowText(row).slice(0,2500),{className:'muted'}));block.append(body);const actions=make('div','',{className:'output-options'});
   if(row.status!=='approved')button(actions,'Утвердить',async()=>{await api('learning',{action:'review',id:row.id,status:'approved'});await refresh();});
   if(row.status!=='rejected')button(actions,'Отклонить',async()=>{await api('learning',{action:'review',id:row.id,status:'rejected'});await refresh();});
   block.append(actions);rows.append(block);
  }
  if(!(value.events||[]).length)rows.append(make('p','В этом разделе записей пока нет.',{className:'muted'}));
 }
 async function refresh(){const expected=++revision;const value=await api('learning',{action:'list',status:filters.value||null,limit:30});if(expected===revision)render(value);return value;}
 async function capture(kind,payload,{groupId=getGroup(),source='user-edit'}={}){return api('learning',{action:'record',kind,payload,groupId,source,status:'pending'});}
 button(formActions,'Запомнить',async()=>{
  let payload;
  if(['dictionary','translation'].includes(kind.value))payload={ru:ru.value,zh:zh.value};
  else if(kind.value==='tag_stat'){if(!date.value)throw Error('Укажите дату наблюдения');if(!views.value.trim())throw Error('Укажите число просмотров');payload={tag:tag.value,views:Number(views.value),observedAt:date.value+'T12:00:00Z',context:context.value,platform:'rednote'};}
  else if(kind.value==='furniture')payload={items:text.value.split(/[,，\n]+/u).map(value=>value.trim()).filter(Boolean)};
  else payload={text:text.value,language:learningTextLanguage(text.value)};
  await api('learning',{action:'record',kind:kind.value,payload,groupId:getGroup(),source:'manual',status:reviewed.checked?'approved':'pending'});
  toast(reviewed.checked?'Утверждённый пример сохранён':'Пример сохранён на проверку');reviewed.checked=false;await refresh();
 });
 button(controls,'Обновить',refresh);controls.append(filters);filters.onchange=()=>run(refresh);
 schedulerEnabled.onchange=()=>run(async()=>{if(!canManageSettings())throw Error('Настройки обучения доступны владельцу');const enabled=schedulerEnabled.checked;await api('learning',{action:'scheduler-config',enabled});await refresh();});
 button(controls,'Подготовить утверждённые данные к обучению',async()=>{await api('learning',{action:'snapshot'});await refresh();status.textContent+=' Подготовлена версия данных для обучения; веса модели ещё не изменены.';});
 const compose=make('section'),composeActions=make('div','',{className:'output-options'}),draftStatus=make('p','Модель составляет черновик по цене, расположению и подтверждённым характеристикам. Проверьте смысл перед применением.',{className:'muted',role:'status'});
 const draftTitle=field(compose,'Название · редактируемый ИИ-черновик',make('textarea','',{rows:2,maxLength:240})),draftBody=field(compose,'Описание · редактируемый ИИ-черновик',make('textarea','',{rows:7,maxLength:20000})),draftTags=field(compose,'Теги · редактируемый ИИ-черновик',make('textarea','',{rows:2,maxLength:3000}));
 let draftContext=null,translationContext=null;
 const fingerprint=()=>JSON.stringify([getGroup(),getFields(),getOutput()]);
 const draftApply=button(composeActions,'Применить проверенный черновик',async()=>{
  if(!draftContext||draftContext!==fingerprint())throw Error('Карточка изменилась. Создайте новый черновик.');
  if(typeof onApply!=='function')throw Error('Откройте карточку для применения');
  const candidate={title:draftTitle.value.trim(),body:draftBody.value.trim(),tags:draftTags.value.trim()};
  if(!candidate.title||!candidate.body)throw Error('Введите название и описание');
  await onApply(candidate);draftContext=null;draftApply.disabled=true;draftStatus.textContent='Черновик передан в карточку. Окончательное применение зависит от проверки текущих правок.';
 });draftApply.disabled=true;
 const generate=button(composeActions,'Создать ИИ-черновик по карточке',async()=>{
  const expected=fingerprint();draftApply.disabled=true;draftContext=null;draftStatus.textContent='Локальная модель составляет текст. Первый запуск включает загрузку весов…';
  const value=await api('learning',{action:'generate-preview',fields:learningGroundingFields(getFields()),output:getOutput(),groupId:getGroup()});
  if(expected!==fingerprint())throw Error('Во время генерации открыта или изменена другая карточка. Повторите запрос.');
  draftStatus.textContent=[value.status==='review'?'ИИ-черновик требует проверки':'Локальная модель пока не подготовила пригодный черновик',...(value.warnings||[]),value.elapsedSeconds!==undefined?'Время: '+Number(value.elapsedSeconds).toFixed(1)+' с':''].filter(Boolean).join(' · ');
  if(value.status!=='review')return;
  draftTitle.value=typeof value.title==='string'?value.title:'';draftBody.value=typeof value.body==='string'?value.body:'';draftTags.value=typeof value.tags==='string'?value.tags:'';
  draftContext=expected;draftApply.disabled=!draftTitle.value||!draftBody.value;
 });
 compose.prepend(make('h3','Новая подача объявления'));compose.append(composeActions,draftStatus);
 const translation=make('section'),direction=field(translation,'Направление перевода',make('select'));
 for(const [value,label]of [['ru-zh','Русский → 中文'],['zh-ru','中文 → Русский']])direction.append(make('option',label,{value}));
 const translationInput=field(translation,'Ваш исходный текст',make('textarea','',{rows:4,maxLength:5000})),translationOutput=field(translation,'Перевод · проверьте и при необходимости исправьте',make('textarea','',{rows:5,maxLength:20000})),target=field(translation,'Применить к части карточки',make('select'));
 for(const [value,label]of [['body','Описание'],['title','Название'],['tags','Теги']])target.append(make('option',label,{value}));
 const translationActions=make('div','',{className:'output-options'}),translationStatus=make('p','Перевод сохраняется в черновике до вашего применения.',{className:'muted',role:'status'});
 const translationApply=button(translationActions,'Применить проверенный перевод',async()=>{
  if(!translationContext||translationContext!==fingerprint())throw Error('Карточка изменилась. Переведите текст ещё раз.');
  if(typeof onApply!=='function')throw Error('Откройте карточку для применения');
  const part=target.value,text=translationOutput.value.trim();if(!text)throw Error('Введите перевод');
  if(part==='title'&&Array.from(text).length>240||part==='tags'&&text.length>3000)throw Error('Текст слишком длинный для выбранной части карточки');
  await onApply({[part]:text});translationContext=null;translationApply.disabled=true;
 });translationApply.disabled=true;
 button(translationActions,'Перевести локальной моделью',async()=>{
  const expected=fingerprint(),source=translationInput.value.trim(),selected=direction.value;
  if(!source)throw Error('Введите исходный текст');translationApply.disabled=true;translationContext=null;translationStatus.textContent='Переводим локально…';
  const value=await api('learning',{action:'translate-preview',text:source,direction:selected});
  if(expected!==fingerprint()||source!==translationInput.value.trim()||selected!==direction.value)throw Error('Исходный текст или карточка изменились. Повторите перевод.');
  translationStatus.textContent=[value.status==='review'?'Перевод требует проверки':'Пригодный перевод не получен',...(value.warnings||[])].join(' · ');
  if(value.status!=='review'||typeof value.text!=='string')return;
  translationOutput.value=value.text;translationContext=expected;translationApply.disabled=!value.text.trim();
 });
 for(const node of [translationInput,direction])node.addEventListener(node===direction?'change':'input',()=>{translationContext=null;translationApply.disabled=true;});
 translation.prepend(make('h3','Русский ↔ 中文'));translation.append(translationActions,translationStatus);
 details.append(heading,intro,compose,translation,status,controls,schedulerForm,schedulerStatus,rows,make('h3','Добавить проверенный опыт'),form,formActions);mount.append(details);
 const postStats=setupTagPostStats({mount:details,api,getTags:()=>getOutput()?.tags||'',onSaved:()=>run(refresh)});
 details.addEventListener('toggle',()=>{if(details.open)run(refresh);});
 schedulerForm.hidden=!canManageSettings();
 return {refresh,capture,setPermissions:()=>{schedulerForm.hidden=!canManageSettings();},clear:()=>{revision++;postStats.clear();rows.replaceChildren();draftContext=translationContext=null;draftApply.disabled=translationApply.disabled=true;for(const node of [draftTitle,draftBody,draftTags,translationInput,translationOutput,ru,zh,text,tag,views,context])node.value='';status.textContent='Откройте блок, чтобы получить состояние памяти.';},destroy:()=>{revision++;postStats.destroy();details.remove();}};
}
