import {setupTagPostStats} from './tag-post-stats.mjs';
import {setupModelHistory} from './model-history.mjs';
// Private editorial memory. All user values enter the DOM as text.
export function learningTextLanguage(text){return /\p{Script=Han}/u.test(String(text))?'zh':'ru';}
export function learningGroundingFields(fields={}){
 const keys=['city','district','districtZh','complex','metro','metroLine','walk','area','rooms','floor','floors','price','priceCurrency','pricePeriod','deposit','depositCurrency','layout','separate','bedrooms','livingRooms','children','pets','petPolicy','childrenPolicy','registration','serviceConfirmations','features','featureCounts','photoEvidence','photoRejectedFeatures','sourceAudit','utilities','ceiling','balconies','loggias','universityRoutes','kitchen','living','bedroomArea','occupants','registrationMonths','registrationPeople','shortTerm','minMonths','shortTermPolicy','smokingPolicy','guestPolicy','subletPolicy','leaseTerm','contractMonths','prepayMonths'];
 return Object.fromEntries(keys.filter(key=>Object.hasOwn(fields,key)).map(key=>[key,fields[key]]));
}

export const LEARNING_PHOTO_MODELS=Object.freeze([
 Object.freeze({id:'redcn-furniture-world-v2',label:'YOLO-World v2 · локальные предложения',provider:'onnx-world'}),
 Object.freeze({id:'redcn-furniture-v1',label:'Мебель v1 · локальные предложения',provider:'yolox-trained'}),
]);
export function learningPhotoModelInfo(config={}){
 const model=typeof config?.model==='string'?config.model:'',known=LEARNING_PHOTO_MODELS.find(row=>row.id===model),enabled=config?.enabled===true,capture=enabled&&config?.autoApply===true&&!!known;
 const label=known?.label||model||'Модель не выбрана';
 const summary=[label,enabled?'анализ включён':'анализ выключен',known?'0 категорий для автоподтверждения · 0 автоматических фактов':'Автоматические наблюдения допускаются только по правилам выбранной модели',known?(capture?'Предложения сохраняются в журнал проверки.':'Автосохранение предложений в журнал выключено.'):'',known?'Найденные предметы требуют подтверждения перед включением в описание.':'','Настройка не подтверждает наличие весов или работающий процесс.'].filter(Boolean).join(' · ');
 return {model,label,provider:known?.provider||null,reviewOnly:!!known,acceptedClasses:known?0:null,automaticFacts:known?0:null,automaticApplication:known?false:null,automaticReviewCapture:capture,summary};
}

export function setupLearningStudio({mount,api,toast=()=>{},getGroup=()=>'',getFields=()=>({}),getOutput=()=>({}),onApply,canManageSettings=()=>true}={}){
 if(!mount||typeof api!=='function')return null;
 const make=(tag,text='',props={})=>{const value=document.createElement(tag);value.textContent=text;Object.assign(value,props);return value;};
 const details=make('details'),heading=make('summary','Память и обучение локального ИИ');
 details.className='card learning-studio';
 const intro=make('p','Ваши правки накапливаются на Mac. Утверждённые примеры используются для словаря, подсказок и будущего обучения. Число записей памяти не равно числу параметров модели.',{className:'muted'});
 const status=make('p','Откройте блок, чтобы получить состояние памяти.',{role:'status'}),controls=make('div','',{className:'output-options'}),rows=make('div');
 const discoveryStatus=make('p','',{className:'muted',role:'status'}),discoveryRows=make('div');
 const schedulerStatus=make('p','Состояние фонового обучения будет получено с Mac.',{className:'muted',role:'status'}),schedulerForm=make('div'),schedulerEnabled=fieldScheduler(' Обучать адаптер во время простоя Mac'),promotionEnabled=fieldScheduler(' Принимать новые веса после проверки качества');
 function fieldScheduler(label){const wrapper=make('label',label),input=make('input','',{type:'checkbox'});input.setAttribute('aria-label',label.trim());wrapper.prepend(input);schedulerForm.append(wrapper);return input;}
 schedulerForm.append(make('p','Веса — обученные настройки модели. Новая версия включается только после полного сравнения с текущей: проверочные факты без ошибок, отсутствие ухудшений и разные варианты текста. Обучение и проверка уступают место работе с объявлениями. Предыдущая версия сохраняется для отката.',{className:'muted'}));
 let revision=0,busy=false;
 const button=(parent,text,action)=>{const node=make('button',text,{type:'button',className:'btn'});node.onclick=()=>run(action);parent.append(node);return node;};
 const rollback=button(schedulerForm,'Вернуть предыдущие веса',async()=>{if(!canManageSettings())throw Error('Выбор весов доступен владельцу');await api('learning',{action:'rollback-model'});await refresh();toast('Предыдущие веса восстановлены');});rollback.disabled=true;
 const field=(parent,label,node)=>{const wrapper=make('label',label);node.setAttribute('aria-label',label.trim());wrapper.append(node);parent.append(wrapper);return node;};
 const photoSettings=make('details'),photoForm=make('div'),photoStatus=make('p','Откройте настройки, чтобы узнать выбранную модель.',{className:'muted',role:'status'});
 photoSettings.className='learning-photo-settings';photoSettings.append(make('summary','Модель для анализа фотографий'),photoForm,photoStatus);
 const photoModel=field(photoForm,'Локальная модель фото',make('select'));
 const photoEnabled=field(photoForm,' Включить локальный анализ фотографий',make('input','',{type:'checkbox'}));
 const photoAutoLabel=make('label'),photoAuto=make('input','',{type:'checkbox'}),photoAutoText=make('span',' Сохранять предложения в журнал проверки');photoAuto.setAttribute('aria-label','Автодобавление фото-находок');photoAutoLabel.append(photoAuto,photoAutoText);photoForm.append(photoAutoLabel);
 const photoHelp=make('p','Выбор модели не скачивает её веса. Изменение применяется к следующим запросам анализа.',{className:'muted'});photoForm.append(photoHelp);
 let photoSettingsEpoch=0,photoSettingsValue=null,photoSettingsLoading=false;
 function photoSettingsBusy(value){photoSettingsLoading=value;for(const node of [photoModel,photoEnabled,photoAuto,photoSave,photoReload])node.disabled=value||node===photoSave&&!photoSettingsValue;}
 function photoSettingsPreview(){const info=learningPhotoModelInfo({model:photoModel.value,enabled:photoEnabled.checked,autoApply:photoAuto.checked});photoAutoText.textContent=info.reviewOnly?' Сохранять предложения в журнал проверки':' Автодобавление наблюдений по допуску модели';photoStatus.textContent=(photoSettingsValue?'Настройки выбранной модели: ':'')+info.summary;}
 function renderPhotoSettings(value){
  photoSettingsValue={...value};photoModel.replaceChildren();
  for(const row of LEARNING_PHOTO_MODELS)photoModel.append(make('option',row.label,{value:row.id}));
  if(typeof value.model==='string'&&!LEARNING_PHOTO_MODELS.some(row=>row.id===value.model))photoModel.append(make('option','Текущая локальная модель · '+value.model,{value:value.model}));
  photoModel.value=typeof value.model==='string'?value.model:'';photoEnabled.checked=value.enabled===true;photoAuto.checked=value.autoApply===true;photoForm.hidden=!canManageSettings();photoSettingsPreview();
 }
 async function refreshPhotoSettings(){const expected=++photoSettingsEpoch;photoSettingsBusy(true);photoStatus.textContent='Читаем настройки анализа фото…';try{const value=await api('photo-ai');if(expected===photoSettingsEpoch)renderPhotoSettings(value);}catch(error){if(expected===photoSettingsEpoch)photoStatus.textContent=error.message;throw error;}finally{if(expected===photoSettingsEpoch)photoSettingsBusy(false);}}
 const photoSave=button(photoForm,'Сохранить модель фото',async()=>{
  if(!canManageSettings())throw Error('Настройки анализа фото доступны владельцу');
  if(!photoSettingsValue||photoSettingsLoading)throw Error('Сначала загрузите настройки анализа фото');
  const expected=++photoSettingsEpoch;photoSettingsBusy(true);
  try{const value=await api('photo-ai',{model:photoModel.value,enabled:photoEnabled.checked,autoApply:photoAuto.checked});if(expected===photoSettingsEpoch){renderPhotoSettings(value);toast('Настройки фото сохранены. Допуск к автоматическому описанию не изменён.');}}
  catch(error){if(expected===photoSettingsEpoch)photoStatus.textContent=error.message;throw error;}
  finally{if(expected===photoSettingsEpoch)photoSettingsBusy(false);}
 });
 const photoReload=button(photoSettings,'Обновить настройки фото',refreshPhotoSettings);photoSave.disabled=true;
 for(const node of [photoModel,photoEnabled,photoAuto])node.onchange=photoSettingsPreview;
 photoSettings.addEventListener('toggle',()=>{if(photoSettings.open&&!photoSettingsValue&&!photoSettingsLoading)run(refreshPhotoSettings);});
 photoForm.hidden=!canManageSettings();
 const form=make('div','',{className:'learning-form'}),kind=field(form,'Что запомнить',make('select'));
 for(const [value,label]of [['dictionary','Точный перевод / словарь'],['translation','Пример перевода RU ↔ 中文'],['title','Пример заголовка'],['description','Пример описания'],['furniture','Мебель и техника вручную'],['tag_stat','Статистика тега']])kind.append(make('option',label,{value}));
 const ru=field(form,'Русский текст',make('textarea','',{rows:3,maxLength:12000})),zh=field(form,'Китайский текст',make('textarea','',{rows:3,maxLength:12000}));
 const text=field(form,'Текст примера / предметы через запятую',make('textarea','',{rows:4,maxLength:12000}));
 const tag=field(form,'Хештег',make('input','',{type:'text',maxLength:100,placeholder:'#莫斯科租房'})),views=field(form,'Просмотры · ваша приблизительная оценка',make('input','',{type:'number',min:0,max:10000000000,step:1,inputMode:'numeric'}));
 const date=field(form,'Дата наблюдения',make('input','',{type:'date',value:new Date().toISOString().slice(0,10)})),context=field(form,'Контекст статистики · например, теги квартиры у метро',make('input','',{type:'text',maxLength:500}));
 const reviewed=field(form,' Я проверил содержание и разрешаю использовать его',make('input','',{type:'checkbox'}));
 const formActions=make('div','',{className:'output-options'});
 const filters=make('select');for(const [value,label]of [['pending','Ожидают проверки'],['approved','Утверждены'],['rejected','Отклонены'],['withdrawn','Отозваны из обучения'],['','Все записи']])filters.append(make('option',label,{value}));
 async function run(action){if(busy)return;busy=true;details.setAttribute('aria-busy','true');try{await action();}catch(error){const message=String(error?.message||'Не удалось обновить память');status.textContent=message;draftStatus.textContent=message;translationStatus.textContent=message;toast(message);}finally{busy=false;details.setAttribute('aria-busy','false');}}
 function displayFields(){const pair=['dictionary','translation'].includes(kind.value),stat=kind.value==='tag_stat';ru.parentElement.hidden=!pair;zh.parentElement.hidden=!pair;text.parentElement.hidden=pair||stat;for(const node of [tag,views,date,context])node.parentElement.hidden=!stat;}
 kind.onchange=displayFields;displayFields();
 function rowText(row){const p=row.payload||{};return row.kind==='dictionary'||row.kind==='translation'?[p.ru,p.zh].join(' → '):row.kind==='tag_stat'?[p.tag,p.views===undefined?'Просмотры не заданы':String(p.views)+' просмотров',p.observedAt,p.context].filter(Boolean).join(' · '):row.kind==='furniture'?(p.items||[]).join(', '):p.text||'';}
 function renderActivity(value){
  const s=value.status||{},counts=s.counts||s;
  status.textContent='На проверке: '+Number(counts.pending||0)+' · утверждено: '+Number(counts.approved||0)+' · отклонено: '+Number(counts.rejected||0)+' · отозвано: '+Number(counts.withdrawn||0)+'. Обучение весов и приёмка новой версии выполняются отдельно.';
  const scheduler=value.scheduler||{},config=scheduler.config||scheduler,model=value.text_ai||{},promotion=scheduler.promotion||{},evaluation=promotion.latestEvaluation||{};schedulerEnabled.checked=config.enabled===true;promotionEnabled.checked=config.automaticPromotion===true;schedulerForm.hidden=!canManageSettings();rollback.disabled=promotion.rollbackAvailable!==true;
  schedulerStatus.textContent=[model.installed?'Текстовая модель установлена'+(model.enabled?' и включена':''): 'Текстовая модель ещё не установлена',scheduler.message||'Ожидаем состояние планировщика',model.trainedParameters?'Обученных параметров адаптера: '+Number(model.trainedParameters).toLocaleString('ru-RU'):'',scheduler.stepsCompleted?'Выполнено шагов: '+Number(scheduler.stepsCompleted):'',config.automaticPromotion?'Автоприёмка включена: только после полного сравнения качества':'Автоприёмка выключена',evaluation.stepsCompleted?'Последний кандидат обучен: '+Number(evaluation.stepsCompleted)+' шагов':'',evaluation.requested?'Последняя проверка: '+Number(evaluation.candidatePassed||0)+'/'+Number(evaluation.requested):'',evaluation.message||'',scheduler.adapterReady&&scheduler.qualityOutcome!=='accepted'?'Кандидат сохранён; текущие веса работают до успешной приёмки':'',promotion.state==='accepted'?'Проверенные новые веса подключены; предыдущую версию можно вернуть':''].filter(Boolean).join(' · ');
  if(scheduler.trainingProgress)schedulerStatus.textContent+=' · Сохранённая контрольная точка: '+Number(scheduler.trainingProgress.stepsCompleted)+'/'+Number(scheduler.trainingProgress.requestedSteps)+' шагов';
  schedulerStatus.textContent+=' · '+(scheduler.modelProcessRunning?'Сейчас выполняется фоновый процесс обучения или проверки':scheduler.workerAlive?'Планировщик работает; его процесс обучения сейчас не запущен':'Живая работа планировщика этим ответом не подтверждена')+(scheduler.updatedAt?' · Проверка: '+new Date(scheduler.updatedAt*1000).toLocaleString('ru-RU'):'');
  const discovery=value.discovery||{},last=discovery.lastCapture;
  discoveryStatus.textContent='Исходные описания: обработано карточек '+Number(discovery.indexedCards||0)+' · уникальных фрагментов '+Number(discovery.uniqueSourcePhrases||0)+' · ожидают перевода и проверки '+Number(discovery.pendingTranslation||0)+(last?' · Последнее пополнение: '+Number(last.newPhrases||0)+' новых, '+Number(last.duplicatePhrases||0)+' повторных, '+new Date(last.capturedAt*1000).toLocaleString('ru-RU'):'')+'. Это очередь новых фраз, а не уже выученные моделью слова.';
 }
 function render(value){
  renderActivity(value);
  const discovery=value.discovery||{};
  discoveryRows.replaceChildren();
  for(const candidate of discovery.candidates||[]){if(candidate.status!=='pending')continue;const block=make('article','',{className:'card-file-row'}),source=make('p',candidate.text),translation=make('textarea','',{rows:2,maxLength:12000,placeholder:'Проверенный китайский перевод'});translation.setAttribute('aria-label','Перевод новой исходной фразы');block.append(source,translation);button(block,'Утвердить перевод для памяти',async()=>{await api('learning',{action:'review-source-phrase',id:candidate.id,zh:translation.value});await refresh();});discoveryRows.append(block);}
  rows.replaceChildren();
  for(const row of value.events||[]){const block=make('article','',{className:'card-file-row'}),body=make('div');body.append(make('strong',({dictionary:'Словарь',translation:'Перевод',title:'Заголовок',description:'Описание',furniture:'Мебель и техника',tag_stat:'Статистика тега'}[row.kind]||row.kind)+' · '+({pending:'на проверке',approved:'утверждено',rejected:'отклонено',withdrawn:'отозвано из обучения',superseded:'заменено исправлением'}[row.status]||row.status)),make('p',rowText(row).slice(0,2500),{className:'muted'}));block.append(body);const actions=make('div','',{className:'output-options'});
   if(!['approved','withdrawn','superseded'].includes(row.status))button(actions,'Утвердить',async()=>{await api('learning',{action:'review',id:row.id,status:'approved'});await refresh();});
   if(!['rejected','withdrawn','superseded'].includes(row.status))button(actions,'Отклонить',async()=>{await api('learning',{action:'review',id:row.id,status:'rejected'});await refresh();});
   if(row.status!=='withdrawn')button(actions,'Отозвать из обучения',async()=>{const result=await api('learning',{action:'withdraw',id:row.id});await refresh();status.textContent=result.message;});
   button(actions,'Происхождение и версии корпуса',async()=>{const value=await api('learning',{action:'provenance',id:row.id});let view=body.querySelector('.learning-provenance');if(!view){view=make('div','',{className:'learning-provenance'});body.append(view);}view.replaceChildren();view.append(make('p','Источник: '+value.source+' · создано: '+value.createdAt,{className:'muted'}));for(const event of value.reviewHistory||[])view.append(make('p',event.action+' · '+event.actor+' · '+event.created,{className:'muted'}));if(value.historicalActorUnknown)view.append(make('p','Для старой записи участник решения не был записан.',{className:'muted'}));view.append(make('p','Версии корпуса: '+((value.snapshots||[]).map(item=>item.snapshotId+' ('+item.partition+')').join(', ')||'ещё не включён'),{className:'muted'}));if(value.withdrawal)view.append(make('p','Разрешение отозвано. Влияние на уже обученные веса не удалено; затронутым моделям нужно переобучение.',{className:'muted'}));});
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
 promotionEnabled.onchange=()=>run(async()=>{if(!canManageSettings())throw Error('Настройки приёмки доступны владельцу');await api('learning',{action:'scheduler-config',automaticPromotion:promotionEnabled.checked});await refresh();});
 button(controls,'Подготовить утверждённые данные к обучению',async()=>{await api('learning',{action:'snapshot'});await refresh();status.textContent+=' Подготовлена версия данных для обучения; веса модели ещё не изменены.';});
 const compose=make('section'),composeActions=make('div','',{className:'output-options'}),draftStatus=make('p','Модель составляет черновик по цене, расположению и подтверждённым характеристикам. Проверьте смысл перед применением.',{className:'muted',role:'status'});
 const draftMode=field(compose,'Способ подготовки объявления',make('select'));
 for(const [value,label]of [['advertising','Новый рекламный текст · проверка фактов'],['advertising-structure','Новая подача · фактические блоки сохраняются']])draftMode.append(make('option',label,{value}));
 compose.append(make('p','В режиме новой подачи модель выбирает расположение блоков, а подтверждённые факты сохраняются целиком. Уже использованные варианты не повторяются; когда схемы закончатся, появится сообщение. Каждый черновик применяется после вашей проверки.',{className:'muted'}));
 const draftTitle=field(compose,'Название · редактируемый ИИ-черновик',make('textarea','',{rows:2,maxLength:240})),draftBody=field(compose,'Описание · редактируемый ИИ-черновик',make('textarea','',{rows:7,maxLength:20000})),draftTags=field(compose,'Теги · редактируемый ИИ-черновик',make('textarea','',{rows:2,maxLength:3000}));
 let draftContext=null,translationContext=null;
 const fingerprint=()=>JSON.stringify([getGroup(),getFields(),getOutput()]);
 const draftFingerprint=()=>JSON.stringify([draftTitle.value,draftBody.value,draftTags.value]);
 const draftApply=button(composeActions,'Применить проверенный черновик',async()=>{
  if(!draftContext||draftContext!==fingerprint())throw Error('Карточка изменилась. Создайте новый черновик.');
  if(typeof onApply!=='function')throw Error('Откройте карточку для применения');
  const candidate={title:draftTitle.value.trim(),body:draftBody.value.trim(),tags:draftTags.value.trim()};
  if(!candidate.title||!candidate.body)throw Error('Введите название и описание');
  await onApply(candidate);draftContext=null;draftApply.disabled=true;draftStatus.textContent='Черновик передан в карточку. Окончательное применение зависит от проверки текущих правок.';
 });draftApply.disabled=true;
 draftMode.onchange=()=>{draftContext=null;draftApply.disabled=true;draftStatus.textContent='Режим изменён. Создайте новый черновик.';};
 const generate=button(composeActions,'Создать ИИ-черновик по карточке',async()=>{
  const expected=fingerprint(),expectedDraft=draftFingerprint(),expectedRevision=revision,selectedMode=draftMode.value;draftApply.disabled=true;draftContext=null;draftStatus.textContent=selectedMode==='advertising-structure'?'Локальная модель выбирает новую подачу подтверждённых фактов…':'Локальная модель составляет текст. Первый запуск включает загрузку весов…';
  const value=await api('learning',{action:'generate-preview',styleMode:selectedMode,fields:learningGroundingFields(getFields()),output:getOutput(),groupId:getGroup()});
  if(expectedRevision!==revision)return;
  if(expected!==fingerprint()||selectedMode!==draftMode.value)throw Error('Во время генерации изменилась карточка или выбранный режим. Повторите запрос.');
  if(expectedDraft!==draftFingerprint()){draftStatus.textContent='Вы изменили черновик во время генерации. Ручной текст сохранён; ответ модели не применён.';return;}
  const grounded=value.fallback===true||value.backend==='grounded-advertising',structured=value.modelContribution==='selection-of-layout-only'&&value.factualTextSource==='verified-renderer',ai=value.ai||{},elapsed=value.elapsedSeconds??ai.elapsedSeconds;
  draftStatus.textContent=[value.status==='review'?(structured?'Модель выбрала подачу; фактические блоки сохранены. Проверьте черновик':grounded?'Рекламный черновик из проверенных фактов; ответ модели не прошёл проверку':'ИИ-черновик требует проверки'):'Локальная модель пока не подготовила пригодный черновик',...(value.warnings||[]),Number.isInteger(ai.generatedTokens)&&ai.generatedTokens>=0?'Модель: '+ai.generatedTokens+' токенов':'',Number.isFinite(ai.tokensPerSecond)&&ai.tokensPerSecond>=0?ai.tokensPerSecond.toFixed(1)+' ток/с':'',Number.isFinite(elapsed)&&elapsed>=0?'Время: '+elapsed.toFixed(1)+' с':''].filter(Boolean).join(' · ');
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
  const expected=fingerprint(),expectedDraft=translationOutput.value,expectedRevision=revision,source=translationInput.value.trim(),selected=direction.value;
  if(!source)throw Error('Введите исходный текст');translationApply.disabled=true;translationContext=null;translationStatus.textContent='Переводим локально…';
  const value=await api('learning',{action:'translate-preview',text:source,direction:selected});
  if(expectedRevision!==revision)return;
  if(expected!==fingerprint()||source!==translationInput.value.trim()||selected!==direction.value)throw Error('Исходный текст или карточка изменились. Повторите перевод.');
  if(expectedDraft!==translationOutput.value){translationStatus.textContent='Вы изменили перевод во время генерации. Ручной текст сохранён; ответ модели не применён.';return;}
  translationStatus.textContent=[value.status==='review'?'Перевод требует проверки':'Пригодный перевод не получен',...(value.warnings||[])].join(' · ');
  if(value.status!=='review'||typeof value.text!=='string')return;
  translationOutput.value=value.text;translationContext=expected;translationApply.disabled=!value.text.trim();
 });
 for(const node of [translationInput,direction])node.addEventListener(node===direction?'change':'input',()=>{translationContext=null;translationApply.disabled=true;});
 translation.prepend(make('h3','Русский ↔ 中文'));translation.append(translationActions,translationStatus);
 details.append(heading,intro,compose,translation,status,controls,schedulerForm,schedulerStatus,photoSettings,make('h3','Новые фразы из объявлений'),discoveryStatus,discoveryRows,rows,make('h3','Добавить проверенный опыт'),form,formActions);mount.append(details);
 const postStats=setupTagPostStats({mount:details,api,getTags:()=>getOutput()?.tags||'',onSaved:()=>run(refresh)});
 const modelHistory=setupModelHistory({mount:details,api});
 details.addEventListener('toggle',()=>{if(details.open)run(refresh);});
 let activityPolling=false;
 const activityTimer=setInterval(async()=>{if(!details.open||document.hidden||busy||activityPolling)return;activityPolling=true;const expected=revision;try{const value=await api('learning',{action:'activity'});if(expected===revision&&details.open&&!busy)renderActivity(value);}catch{}finally{activityPolling=false;}},10000);
 schedulerForm.hidden=!canManageSettings();
 return {refresh,capture,setPermissions:()=>{schedulerForm.hidden=!canManageSettings();photoForm.hidden=!canManageSettings();},clear:()=>{revision++;photoSettingsEpoch++;photoSettingsValue=null;photoSettings.open=false;photoSettingsBusy(false);photoModel.replaceChildren();photoStatus.textContent='Откройте настройки, чтобы узнать выбранную модель.';details.open=false;postStats.clear();modelHistory.clear();rows.replaceChildren();discoveryRows.replaceChildren();discoveryStatus.textContent='';draftContext=translationContext=null;draftApply.disabled=translationApply.disabled=true;for(const node of [draftTitle,draftBody,draftTags,translationInput,translationOutput,ru,zh,text,tag,views,context])node.value='';status.textContent='Откройте блок, чтобы получить состояние памяти.';},destroy:()=>{revision++;photoSettingsEpoch++;clearInterval(activityTimer);postStats.destroy();modelHistory.destroy();details.remove();}};
}
