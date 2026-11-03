import {hasOwnKey} from './own-property.mjs';
import {setupTagPostStats} from './tag-post-stats.mjs';
import {setupModelHistory} from './model-history.mjs';
// Private editorial memory. All user values enter the DOM as text.
export function learningTextLanguage(text){return /\p{Script=Han}/u.test(String(text))?'zh':'ru';}
export function learningGroundingFields(fields={}){
 const keys=['city','district','districtZh','complex','metro','metroLine','walk','area','rooms','floor','floors','price','priceCurrency','pricePeriod','deposit','depositCurrency','layout','separate','bedrooms','livingRooms','children','pets','petPolicy','childrenPolicy','registration','serviceConfirmations','features','featureCounts','photoEvidence','photoRejectedFeatures','sourceAudit','utilities','ceiling','balconies','loggias','universityRoutes','kitchen','living','bedroomArea','occupants','registrationMonths','registrationPeople','shortTerm','minMonths','shortTermPolicy','smokingPolicy','guestPolicy','subletPolicy','leaseTerm','contractMonths','prepayMonths'];
 return Object.fromEntries(keys.filter(key=>hasOwnKey(fields,key)).map(key=>[key,fields[key]]));
}
export function individualGroundingFields(fields={}){
 const extra=['description','sourceDescription','renovation','windows','bathroom','buildingYear','details','transit','universities','university','uniTime','uniMode','route','highlight','registrationPolicy','url','editorIdentity','resultId','id','pickup','luggage','arrival','support','serviceBenefits'];
 return {...learningGroundingFields(fields),...Object.fromEntries(extra.filter(key=>hasOwnKey(fields,key)).map(key=>[key,fields[key]]))};
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

// Advertising counters and receipts must never borrow the translation lane's history.
export function learningAdvertisingState(value={}){
 const status=value.status||{},scheduler=value.scheduler||{},text=value.text_ai||{},ad=text.advertising||{};
 const lane=scheduler.lane||status.activeLane||null,isAdvertising=scheduler.lane==='advertising';
 const readiness=status.lanes?.advertising||(isAdvertising?scheduler.readiness:null);
 const count=value=>Number.isInteger(value)&&value>=0?value:null,amount=value=>count(value)===null?'не получено':value.toLocaleString('ru-RU');
 const thresholds=readiness||{},minimum=(key,fallback)=>count(thresholds[key])??fallback;
 const blockers={'reviewed-examples-needed':'нужны проверенные пары «факты + полный текст»','independent-properties-needed':'нужны разные квартиры','training-split-needed':'не хватает примеров для обучения','validation-split-needed':'не хватает отдельных примеров для проверки','duplicate-input-across-splits':'одни исходные данные попали в обе выборки'};
 const queue=readiness?'Ожидают сверки: '+amount(readiness.pendingExamples)+' · допущенных записей: '+amount(readiness.approvedRecords??readiness.approvedExamples):'Сведения о парах объявлений пока не получены.';
 const examples=readiness?'Уникальных проверенных пар: '+amount(readiness.approvedExamples)+' / '+minimum('minimumExamples',20)+' · разных квартир: '+amount(readiness.approvedGroups)+' / '+minimum('minimumGroups',5):'Для обучения нужны полный текст и исходные факты одной квартиры.';
 const splits=readiness?'Для обучения: '+amount(readiness.trainExamples)+' / '+minimum('minimumTrain',16)+' · для отдельной проверки: '+amount(readiness.validExamples)+' / '+minimum('minimumValidation',4):'Разделение обучающих и проверочных квартир пока не получено.';
 const provenance=readiness?'Проверено человеком: '+amount(readiness.humanVerified)+' · проверено ассистентом: '+amount(readiness.assistantReviewed):'Автор проверки пока не указан.';
 const readinessText=!readiness?'Готовность данных не подтверждена.':readiness.trainingEligible===true?'Данные допускаются к обучению. Запуск и приёмка весов показываются отдельно.':'Сбор данных: '+((readiness.blockingReasons||[]).map(reason=>blockers[reason]||'условие допуска требует проверки').join('; ')||'допуск к обучению ещё не подтверждён')+'.';
 const model='Модель объявлений: '+(ad.model||'не определена')+' · '+(ad.installed===true&&ad.supported===true?'установлена':ad.installed===true?'совместимость не подтверждена':'наличие не подтверждено')+' · '+(ad.enabled===true?'генерация включена':'генерация выключена')+(ad.separateProfile===true?' · отдельный профиль объявлений':'');
 let adapter='Активный адаптер объявлений: не подтверждён.';
 if(ad.adapterActive===true&&ad.adapterCompatible===true)adapter='Активный адаптер объявлений: совместим и подключён для генерации.';
 else if(ad.candidateAdapterSelected===true)adapter='Адаптер объявлений выбран, но его применение не подтверждено.';
 else if(hasOwnKey(ad,'candidateAdapterSelected'))adapter='Для объявлений используется базовая модель без выбранного адаптера.';
 if(ad.adapterProblem)adapter+=' '+String(ad.adapterProblem);
 if(ad.trainedByRedcn===true&&ad.adapterCompatible===true)adapter+=' Квитанция выбранных весов: '+amount(ad.stepsCompleted)+' шагов'+(count(ad.trainedParameters)!==null?', обучаемых параметров '+amount(ad.trainedParameters):'')+'.';
 let process='Работа процесса обучения объявлений сейчас не подтверждена.';
 if(isAdvertising&&scheduler.modelProcessRunning===true)process=scheduler.phase==='evaluating'?'Сейчас выполняется проверка кандидата объявлений.':scheduler.phase==='training'?'Сейчас выполняется обучение адаптера объявлений.':'Работает процесс обучения или проверки объявлений.';
 else if(isAdvertising&&scheduler.workerAlive===true)process='Планировщик объявлений работает; процесс обучения сейчас не запущен.';
 else if(lane==='translation'&&scheduler.modelProcessRunning===true)process='Сейчас работает процесс направления перевода. Запуск обучения объявлений не подтверждён.';
 const steps=isAdvertising&&count(scheduler.stepsCompleted)!==null?'Шагов последнего запуска объявлений: '+amount(scheduler.stepsCompleted)+'.':'Шаги последнего запуска объявлений пока не получены.';
 const checkpoint=isAdvertising?scheduler.trainingProgress:null;
 const progress=checkpoint&&count(checkpoint.stepsCompleted)!==null?'Последняя сохранённая контрольная точка: '+amount(checkpoint.stepsCompleted)+' / '+amount(checkpoint.requestedSteps)+' шагов.':'';
 const promotion=isAdvertising?(scheduler.promotion||{}):{},evaluation=promotion.latestEvaluation||{};
 const evaluationText=[count(evaluation.stepsCompleted)!==null?'Кандидат для последней проверки: '+amount(evaluation.stepsCompleted)+' шагов.':'',count(evaluation.requested)!==null?'Последняя проверка ответов: '+amount(evaluation.candidatePassed)+' / '+amount(evaluation.requested)+'.':'',evaluation.message||'',scheduler.adapterReady===true&&isAdvertising&&promotion.state!=='accepted'?'Кандидат сохранён; приёмка новых весов ещё не подтверждена.':'',promotion.state==='accepted'?'Приёмка кандидата объявлений записана. Текущий выбор весов указан выше.':''].filter(Boolean).join(' ');
 const failure=isAdvertising&&scheduler.failureReason?'Причина остановки: '+String(scheduler.failureReason):'';
 const translation=['Перевод: '+(text.model||'модель не определена'),text.candidateAdapterSelected===true?'выбран адаптер перевода':'адаптер перевода не выбран',count(text.stepsCompleted)!==null?'история весов перевода: '+amount(text.stepsCompleted)+' шагов':''].filter(Boolean).join(' · ')+'. Эти показатели относятся к переводу.';
 return {lane,queue,examples,splits,provenance,readiness:readinessText,model,adapter,process,steps,progress,evaluation:evaluationText,failure,translation,style:'Примеры стиля: '+amount(status.styleOnlyExamples??scheduler.styleOnlyExamples)+'. Отдельные заголовки, описания и списки предметов сохраняются в памяти; для обучения объявлений нужна пара с исходными фактами.'};
}

export function setupLearningStudio({mount,api,toast=()=>{},getGroup=()=>'',getFields=()=>({}),getOutput=()=>({}),onApply,canManageSettings=()=>true}={}){
 if(!mount||typeof api!=='function')return null;
 const requestApi=api;let apiPending=0;
 api=async(...args)=>{apiPending++;try{return await requestApi(...args);}finally{apiPending--;}};
 const make=(tag,text='',props={})=>{const value=document.createElement(tag);value.textContent=text;Object.assign(value,props);return value;};
 const details=make('details'),heading=make('summary','Память и обучение локального ИИ');
 details.className='card learning-studio';
 const intro=make('p','Для обучения объявлений сохраняйте полный проверенный текст вместе с фактами той же квартиры. Переводы и примеры стиля учитываются отдельно. Состояние данных, процесса обучения и подключённых весов показано ниже.',{className:'muted'});
 const status=make('p','Откройте блок, чтобы получить состояние памяти.',{role:'status'}),controls=make('div','',{className:'output-options'}),rows=make('div');
 const discoveryStatus=make('p','',{className:'muted',role:'status'}),discoveryRows=make('div');
 const schedulerStatus=make('p','Состояние фонового обучения будет получено с Mac.',{className:'muted',role:'status'}),schedulerForm=make('div'),schedulerEnabled=fieldScheduler(' Обучать адаптер во время простоя Mac'),promotionEnabled=fieldScheduler(' Принимать новые веса после проверки качества');
 const advertisingStatus=make('section','',{className:'learning-advertising-status'}),advertisingLines={};
 advertisingStatus.append(make('h3','Обучение объявлений'));
 for(const key of ['queue','examples','splits','provenance','readiness','model','adapter','process','steps','progress','evaluation','failure']){advertisingLines[key]=make('p','',{className:'muted'});advertisingStatus.append(advertisingLines[key]);}
 const translationTrainingStatus=make('p','Состояние модели перевода пока не получено.',{className:'muted'}),styleStatus=make('p','',{className:'muted'});
 advertisingStatus.append(make('p','Проверочные квартиры выделяются отдельно и не используются для изменения весов.',{className:'muted'}));
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
 function showGroundedSource(parent,fields={},request=null){
  parent.replaceChildren();
  const source=typeof fields.sourceDescription==='string'?fields.sourceDescription:typeof fields.description==='string'?fields.description:'';
  const sourceText=make('p',source||'Исходное описание не заполнено. Сверьте текст с параметрами и подтверждениями ниже.',{className:'muted'});sourceText.style.whiteSpace='pre-wrap';sourceText.style.overflowWrap='anywhere';
  parent.append(make('h4','Исходное описание квартиры'),sourceText);
  const facts=make('details'),parameters=make('pre',JSON.stringify(fields,null,2));parameters.style.whiteSpace='pre-wrap';parameters.style.overflowWrap='anywhere';parameters.style.maxWidth='100%';
  facts.append(make('summary','Все параметры и подтверждения этой квартиры'),parameters);parent.append(facts);
  const messages=Array.isArray(request?.messages)?request.messages.filter(row=>row.role==='user'&&typeof row.content==='string'):[];
  if(messages.length){const canonical=make('details'),text=make('p',messages.map(row=>row.content).join('\n\n'));text.style.whiteSpace='pre-wrap';text.style.overflowWrap='anywhere';canonical.append(make('summary','Подтверждённые условия для этого текста'),text);parent.append(canonical);}
 }
 const groundedForm=make('section','',{className:'learning-grounded-form'}),groundedSource=make('div'),groundedActions=make('div','',{className:'output-options'}),groundedStatus=make('p','Загрузите текущую карточку, затем сверяйте полный текст с её исходными фактами.',{className:'muted',role:'status'});
 groundedForm.append(make('h3','Проверенная пара для обучения объявлений'),make('p','Сверьте цену, ограничения, маршруты и каждый предмет. Укажите, что именно проверили. Сохранение пары добавляет учебные данные; запуск обучения и приёмка модели выполняются отдельно.',{className:'muted'}),groundedSource);
 const groundedTitle=field(groundedForm,'Проверенный заголовок пары',make('textarea','',{rows:2,maxLength:240})),groundedBody=field(groundedForm,'Полное проверенное описание пары',make('textarea','',{rows:7,maxLength:6000}));
 const groundedReason=field(groundedForm,'Что сверено с исходными фактами',make('textarea','',{rows:2,maxLength:2000})),groundedReviewed=field(groundedForm,' Я сверил весь заголовок и описание с исходными фактами этой квартиры',make('input','',{type:'checkbox'}));
 let groundedEpoch=0,groundedCapture=null,groundedCommitted='';const groundedReviewDrafts=new Map();
 const groundedContext=()=>JSON.stringify([getGroup(),individualGroundingFields(getFields())]);
 const groundedFormFingerprint=()=>JSON.stringify([groundedTitle.value,groundedBody.value,groundedReason.value]);
 const groundedReady=()=>!!groundedCapture&&groundedReviewed.checked&&!!groundedReason.value.trim()&&!!groundedTitle.value.trim()&&!!groundedBody.value.trim();
 function updateGroundedSave(){groundedSave.disabled=!groundedReady();}
 button(groundedActions,'Загрузить текущую карточку для сверки',async()=>{
  const groupId=getGroup(),fields=JSON.parse(JSON.stringify(individualGroundingFields(getFields()))),output=getOutput();
  if(!groupId||!Object.keys(fields).length)throw Error('Сначала откройте карточку с исходными фактами');
  groundedEpoch++;groundedCapture={groupId,fields,context:JSON.stringify([groupId,fields])};groundedCommitted='';
  groundedTitle.value=typeof output?.title==='string'?output.title:'';groundedBody.value=typeof output?.body==='string'?output.body:'';groundedReason.value='';groundedReviewed.checked=false;
  showGroundedSource(groundedSource,fields);groundedStatus.textContent='Карточка загружена. Проверьте полный текст, укажите основание и подтвердите сверку.';updateGroundedSave();
 });
 const groundedSave=button(groundedActions,'Сохранить проверенную пару',async()=>{
  if(!groundedReady())throw Error('Нужны заголовок, описание, основание и явное подтверждение сверки');
  if(groundedCapture.context!==groundedContext()){groundedReviewed.checked=false;updateGroundedSave();throw Error('Факты или выбранная карточка изменились. Загрузите её для сверки заново.');}
  const expected=groundedEpoch,submitted=groundedFormFingerprint(),capture=groundedCapture;
  await api('learning',{action:'record-grounded',fields:capture.fields,target:{title:groundedTitle.value.trim(),body:groundedBody.value.trim()},groupId:capture.groupId,reviewed:true,reason:groundedReason.value.trim()});
  if(expected!==groundedEpoch)return;
  if(submitted===groundedFormFingerprint()){groundedCommitted=submitted;groundedReviewed.checked=false;}
  updateGroundedSave();groundedStatus.textContent='Проверенная пара сохранена. Состояние данных и обучения показано выше.';await refresh();
 });groundedSave.disabled=true;
 for(const node of [groundedTitle,groundedBody,groundedReason])node.oninput=()=>{groundedReviewed.checked=false;updateGroundedSave();};
 groundedReviewed.onchange=updateGroundedSave;groundedForm.append(groundedActions,groundedStatus);
 function groundedRowView(row,body,actions){
  const payload=row.payload||{},source=make('div'),editable=['pending','rejected'].includes(row.status),sourceKey=JSON.stringify([payload.fields,payload.rules,payload.request,payload.target]);
  showGroundedSource(source,payload.fields||{},payload.request);body.append(source);
  let draft=groundedReviewDrafts.get(row.id);
  if(!editable){groundedReviewDrafts.delete(row.id);draft=null;}
  else if(draft&&draft.sourceKey!==sourceKey&&draft.dirty){draft={...draft,sourceKey,reason:'',reviewed:false,sourceChanged:true};groundedReviewDrafts.set(row.id,draft);}
  else if(!draft||draft.sourceKey!==sourceKey){draft={sourceKey,title:payload.target?.title||'',body:payload.target?.body||'',reason:'',reviewed:false,dirty:false};groundedReviewDrafts.set(row.id,draft);}
  if(draft?.sourceChanged)body.append(make('p','Исходные факты обновились. Ваш текст сохранён; сверьте его заново перед подтверждением.',{role:'status'}));
  const targetTitle=field(body,'Полный заголовок проверяемой записи',make('textarea','',{rows:2,maxLength:240,readOnly:!editable,value:draft?.title??payload.target?.title??''}));
  const targetBody=field(body,'Полное описание проверяемой записи',make('textarea','',{rows:7,maxLength:6000,readOnly:!editable,value:draft?.body??payload.target?.body??''}));
  const review=payload.review;
  if(review){body.append(make('p',(review.humanVerified===true?'Проверка человека':review.humanVerified===false?'Проверка ассистента':'Вид проверки не указан')+(review.actor?' · '+review.actor:'')+(review.reason?' · '+review.reason:''),{className:'muted'}));}
  if(!editable)return;
  const reason=field(body,'Основание проверки этой записи',make('textarea','',{rows:2,maxLength:2000,value:draft.reason})),verified=field(body,' Я сверил весь текст этой записи с её исходными фактами',make('input','',{type:'checkbox',checked:draft.reviewed}));
  const ready=()=>verified.checked&&!!reason.value.trim()&&!!targetTitle.value.trim()&&!!targetBody.value.trim();
  const save=button(actions,'Сохранить сверенный текст для обучения',async()=>{
   if(!ready())throw Error('Подтвердите сверку полного текста и укажите её основание');
   const submitted=JSON.stringify([draft.title,draft.body,draft.reason]),expected=revision;
   await api('learning',{action:'review-grounded',id:row.id,target:{title:targetTitle.value.trim(),body:targetBody.value.trim()},reviewed:true,reason:reason.value.trim()});
   if(expected!==revision)return;
   if(submitted===JSON.stringify([draft.title,draft.body,draft.reason]))groundedReviewDrafts.delete(row.id);
   await refresh();
  });
  const update=()=>{draft.title=targetTitle.value;draft.body=targetBody.value;draft.reason=reason.value;draft.reviewed=verified.checked;save.disabled=!ready();};
  for(const node of [targetTitle,targetBody,reason])node.oninput=()=>{draft.dirty=true;verified.checked=false;update();};
  verified.onchange=()=>{draft.dirty=true;update();};update();
 }
 const form=make('div','',{className:'learning-form'}),kind=field(form,'Что запомнить',make('select'));
 for(const [value,label]of [['dictionary','Точный перевод / словарь'],['translation','Пример перевода RU ↔ 中文'],['title','Заголовок · память стиля'],['description','Описание · память стиля'],['furniture','Мебель и техника вручную'],['tag_stat','Статистика тега']])kind.append(make('option',label,{value}));
 const ru=field(form,'Русский текст',make('textarea','',{rows:3,maxLength:12000})),zh=field(form,'Китайский текст',make('textarea','',{rows:3,maxLength:12000}));
 const text=field(form,'Текст примера / предметы через запятую',make('textarea','',{rows:4,maxLength:12000}));
 const tag=field(form,'Хештег',make('input','',{type:'text',maxLength:100,placeholder:'#莫斯科租房'})),views=field(form,'Просмотры · ваша приблизительная оценка',make('input','',{type:'number',min:0,max:10000000000,step:1,inputMode:'numeric'}));
 const date=field(form,'Дата наблюдения',make('input','',{type:'date',value:new Date().toISOString().slice(0,10)})),context=field(form,'Контекст статистики · например, теги квартиры у метро',make('input','',{type:'text',maxLength:500}));
 const reviewed=field(form,' Я проверил содержание и разрешаю использовать его',make('input','',{type:'checkbox'}));
 let memoryCommitted='';const memoryFingerprint=()=>JSON.stringify([kind.value,ru.value,zh.value,text.value,tag.value,views.value,date.value,context.value]);
 const formActions=make('div','',{className:'output-options'});
 const filters=make('select');for(const [value,label]of [['pending','Ожидают проверки'],['approved','Утверждены'],['rejected','Отклонены'],['withdrawn','Отозваны из обучения'],['','Все записи']])filters.append(make('option',label,{value}));
 async function run(action){if(busy)return;busy=true;details.setAttribute('aria-busy','true');try{await action();}catch(error){const message=String(error?.message||'Не удалось обновить память');status.textContent=message;draftStatus.textContent=message;translationStatus.textContent=message;toast(message);}finally{busy=false;details.setAttribute('aria-busy','false');}}
 function displayFields(){const pair=['dictionary','translation'].includes(kind.value),stat=kind.value==='tag_stat';ru.parentElement.hidden=!pair;zh.parentElement.hidden=!pair;text.parentElement.hidden=pair||stat;for(const node of [tag,views,date,context])node.parentElement.hidden=!stat;}
 kind.onchange=displayFields;displayFields();
 function rowText(row){const p=row.payload||{};return row.kind==='dictionary'||row.kind==='translation'?[p.ru,p.zh].join(' → '):row.kind==='tag_stat'?[p.tag,p.views===undefined?'Просмотры не заданы':String(p.views)+' просмотров',p.observedAt,p.context].filter(Boolean).join(' · '):row.kind==='furniture'?(p.items||[]).join(', '):p.text||'';}
 function renderActivity(value){
  const s=value.status||{},counts=s.counts||s;
  status.textContent='На проверке: '+Number(counts.pending||0)+' · утверждено: '+Number(counts.approved||0)+' · отклонено: '+Number(counts.rejected||0)+' · отозвано: '+Number(counts.withdrawn||0)+'. Обучение весов и приёмка новой версии выполняются отдельно.';
  const scheduler=value.scheduler||{},config=scheduler.config||scheduler,promotion=scheduler.promotion||{},advertising=learningAdvertisingState(value);
  schedulerEnabled.checked=config.enabled===true;promotionEnabled.checked=config.automaticPromotion===true;schedulerForm.hidden=!canManageSettings();rollback.disabled=promotion.rollbackAvailable!==true;
  for(const [key,node]of Object.entries(advertisingLines)){node.textContent=advertising[key];node.hidden=!advertising[key];}
  translationTrainingStatus.textContent=advertising.translation;styleStatus.textContent=advertising.style;
  schedulerStatus.textContent=[advertising.lane==='advertising'?'Направление планировщика: объявления':advertising.lane==='translation'?'Направление планировщика: перевод':'Направление планировщика не подтверждено',scheduler.message||'Ожидаем состояние планировщика',config.automaticPromotion?'Автоприёмка включена: только после сравнения качества':'Автоприёмка выключена',scheduler.updatedAt?'Обновлено: '+new Date(scheduler.updatedAt*1000).toLocaleString('ru-RU'):''].filter(Boolean).join(' · ');
  const discovery=value.discovery||{},last=discovery.lastCapture;
  discoveryStatus.textContent='Исходные описания: обработано карточек '+Number(discovery.indexedCards||0)+' · уникальных фрагментов '+Number(discovery.uniqueSourcePhrases||0)+' · ожидают перевода и проверки '+Number(discovery.pendingTranslation||0)+(last?' · Последнее пополнение: '+Number(last.newPhrases||0)+' новых, '+Number(last.duplicatePhrases||0)+' повторных, '+new Date(last.capturedAt*1000).toLocaleString('ru-RU'):'')+'. Это очередь новых фраз, а не уже выученные моделью слова.';
 }
 function render(value){
  renderActivity(value);
  const discovery=value.discovery||{};
  discoveryRows.replaceChildren();
  for(const candidate of discovery.candidates||[]){if(candidate.status!=='pending')continue;const block=make('article','',{className:'card-file-row'}),source=make('p',candidate.text),translation=make('textarea','',{rows:2,maxLength:12000,placeholder:'Проверенный китайский перевод'});translation.setAttribute('aria-label','Перевод новой исходной фразы');block.append(source,translation);button(block,'Утвердить перевод для памяти',async()=>{await api('learning',{action:'review-source-phrase',id:candidate.id,zh:translation.value});await refresh();});discoveryRows.append(block);}
  rows.replaceChildren();
  for(const row of value.events||[]){const block=make('article','',{className:'card-file-row'}),body=make('div');body.append(make('strong',({dictionary:'Словарь',translation:'Перевод',title:'Заголовок · память стиля',description:'Описание · память стиля',grounded_listing:'Факты квартиры и полный текст',furniture:'Мебель и техника',tag_stat:'Статистика тега'}[row.kind]||row.kind)+' · '+({pending:'на проверке',approved:'утверждено',rejected:'отклонено',withdrawn:'отозвано из обучения',superseded:'заменено исправлением'}[row.status]||row.status)));block.append(body);const actions=make('div','',{className:'output-options'});
   if(row.kind==='grounded_listing')groundedRowView(row,body,actions);else body.append(make('p',rowText(row).slice(0,2500),{className:'muted'}));
   if(row.kind!=='grounded_listing'&&!['approved','withdrawn','superseded'].includes(row.status))button(actions,'Утвердить',async()=>{await api('learning',{action:'review',id:row.id,status:'approved'});await refresh();});
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
  let payload;const submitted=memoryFingerprint(),selectedKind=kind.value,approved=reviewed.checked;
  if(['dictionary','translation'].includes(kind.value))payload={ru:ru.value,zh:zh.value};
  else if(kind.value==='tag_stat'){if(!date.value)throw Error('Укажите дату наблюдения');if(!views.value.trim())throw Error('Укажите число просмотров');payload={tag:tag.value,views:Number(views.value),observedAt:date.value+'T12:00:00Z',context:context.value,platform:'rednote'};}
  else if(kind.value==='furniture')payload={items:text.value.split(/[,，\n]+/u).map(value=>value.trim()).filter(Boolean)};
  else payload={text:text.value,language:learningTextLanguage(text.value)};
  await api('learning',{action:'record',kind:kind.value,payload,groupId:getGroup(),source:'manual',status:reviewed.checked?'approved':'pending'});
  if(submitted===memoryFingerprint()){memoryCommitted=submitted;reviewed.checked=false;}
  toast(['title','description','furniture'].includes(selectedKind)?'Пример сохранён в память; обучение объявлений требует пары с фактами':approved?'Утверждённый пример сохранён':'Пример сохранён на проверку');await refresh();
 });
 button(controls,'Обновить',refresh);controls.append(filters);filters.onchange=()=>run(refresh);
 schedulerEnabled.onchange=()=>run(async()=>{if(!canManageSettings())throw Error('Настройки обучения доступны владельцу');const enabled=schedulerEnabled.checked;await api('learning',{action:'scheduler-config',enabled});await refresh();});
 promotionEnabled.onchange=()=>run(async()=>{if(!canManageSettings())throw Error('Настройки приёмки доступны владельцу');await api('learning',{action:'scheduler-config',automaticPromotion:promotionEnabled.checked});await refresh();});
 button(controls,'Подготовить утверждённые данные к обучению',async()=>{await api('learning',{action:'snapshot'});await refresh();status.textContent+=' Подготовлена версия данных для обучения; веса модели ещё не изменены.';});
 const compose=make('section'),composeActions=make('div','',{className:'output-options'}),draftStatus=make('p','Модель составляет черновик по цене, расположению и подтверждённым характеристикам. Проверьте смысл перед применением.',{className:'muted',role:'status'});
 const draftMode=field(compose,'Способ подготовки объявления',make('select'));
 for(const [value,label]of [['individual','Полное объявление ИИ · описание и подтверждённые фото-находки'],['advertising-structure','Компоновка проверенных блоков · модель выбирает схему']])draftMode.append(make('option',label,{value}));
 compose.append(make('p','В первом режиме локальная модель пишет заголовок и весь текст по исходному описанию, параметрам и подтверждённому перечню предметов. Непринятый ответ сохраняет текущий текст. Компоновка блоков доступна отдельно; каждый черновик применяется после вашей проверки.',{className:'muted'}));
 const draftTitle=field(compose,'Название · редактируемый ИИ-черновик',make('textarea','',{rows:2,maxLength:240})),draftBody=field(compose,'Описание · редактируемый ИИ-черновик',make('textarea','',{rows:7,maxLength:20000})),draftTags=field(compose,'Теги · редактируемый ИИ-черновик',make('textarea','',{rows:2,maxLength:3000}));
 let draftContext=null,translationContext=null,draftCommitted='',translationCommitted='';
 const fingerprint=()=>JSON.stringify([getGroup(),getFields(),getOutput()]);
 const draftFingerprint=()=>JSON.stringify([draftTitle.value,draftBody.value,draftTags.value]);
 const draftApply=button(composeActions,'Применить проверенный черновик',async()=>{
  if(!draftContext||draftContext!==fingerprint())throw Error('Карточка изменилась. Создайте новый черновик.');
  if(typeof onApply!=='function')throw Error('Откройте карточку для применения');
  const candidate={title:draftTitle.value.trim(),body:draftBody.value.trim(),tags:draftTags.value.trim()};
  if(!candidate.title||!candidate.body)throw Error('Введите название и описание');
  const submitted=draftFingerprint();await onApply(candidate);if(submitted===draftFingerprint())draftCommitted=submitted;draftContext=null;draftApply.disabled=true;draftStatus.textContent='Черновик передан в карточку. Окончательное применение зависит от проверки текущих правок.';
 });draftApply.disabled=true;
 draftMode.onchange=()=>{draftContext=null;draftApply.disabled=true;draftStatus.textContent='Режим изменён. Создайте новый черновик.';};
 const generate=button(composeActions,'Создать ИИ-черновик по карточке',async()=>{
  const expected=fingerprint(),expectedDraft=draftFingerprint(),expectedRevision=revision,selectedMode=draftMode.value;draftApply.disabled=true;draftContext=null;draftStatus.textContent=selectedMode==='advertising-structure'?'Локальная модель выбирает новую подачу подтверждённых фактов…':'Локальная модель составляет текст. Первый запуск включает загрузку весов…';
  const value=await api('learning',{action:'generate-preview',styleMode:selectedMode,fields:selectedMode==='individual'?individualGroundingFields(getFields()):learningGroundingFields(getFields()),output:getOutput(),groupId:getGroup()});
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
 const translationFingerprint=()=>JSON.stringify([translationInput.value,translationOutput.value,direction.value,target.value]);
 const translationActions=make('div','',{className:'output-options'}),translationStatus=make('p','Перевод сохраняется в черновике до вашего применения.',{className:'muted',role:'status'});
 const translationApply=button(translationActions,'Применить проверенный перевод',async()=>{
  if(!translationContext||translationContext!==fingerprint())throw Error('Карточка изменилась. Переведите текст ещё раз.');
  if(typeof onApply!=='function')throw Error('Откройте карточку для применения');
  const part=target.value,text=translationOutput.value.trim();if(!text)throw Error('Введите перевод');
  if(part==='title'&&Array.from(text).length>240||part==='tags'&&text.length>3000)throw Error('Текст слишком длинный для выбранной части карточки');
  const submitted=translationFingerprint();await onApply({[part]:text});if(submitted===translationFingerprint())translationCommitted=submitted;translationContext=null;translationApply.disabled=true;
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
 details.append(heading,intro,advertisingStatus,schedulerForm,schedulerStatus,translationTrainingStatus,styleStatus,compose,translation,groundedForm,status,controls,photoSettings,make('h3','Новые фразы из объявлений'),discoveryStatus,discoveryRows,rows,make('h3','Словарь и память стиля'),form,formActions);mount.append(details);
 const existingInputs=new Set(details.querySelectorAll('input,textarea,select'));let statsInputs=[],statsCommitted='';
 const statsFingerprint=()=>JSON.stringify(statsInputs.map(node=>[node.value,node.checked===true]));
 const postStats=setupTagPostStats({mount:details,api,getTags:()=>getOutput()?.tags||'',onSaved:()=>{statsCommitted=statsFingerprint();return run(refresh);}});
 statsInputs=Array.from(details.querySelectorAll('input,textarea,select')).filter(node=>!existingInputs.has(node));statsCommitted=statsFingerprint();
 const modelHistory=setupModelHistory({mount:details,api});
 details.addEventListener('toggle',()=>{if(details.open)run(refresh);});
 let activityPolling=false;
 const activityTimer=setInterval(async()=>{if(!details.open||document.hidden||busy||activityPolling)return;activityPolling=true;const expected=revision;try{const value=await api('learning',{action:'activity'});if(expected===revision&&details.open&&!busy)renderActivity(value);}catch{}finally{activityPolling=false;}},10000);
 schedulerForm.hidden=!canManageSettings();
 function activity(){
  const contains=nodes=>nodes.some(node=>String(node.value||'').trim());
  const draftDirty=contains([draftTitle,draftBody,draftTags])&&draftFingerprint()!==draftCommitted;
  const translationDirty=contains([translationInput,translationOutput])&&translationFingerprint()!==translationCommitted;
  const memoryDirty=(contains([ru,zh,text,tag,views,context])&&memoryFingerprint()!==memoryCommitted)||reviewed.checked;
  const groundedDirty=((!!groundedCapture||contains([groundedTitle,groundedBody,groundedReason]))&&groundedFormFingerprint()!==groundedCommitted)||groundedReviewed.checked;
  const photoDirty=!!photoSettingsValue&&(photoModel.value!==String(photoSettingsValue.model||'')||photoEnabled.checked!==(photoSettingsValue.enabled===true)||photoAuto.checked!==(photoSettingsValue.autoApply===true));
  return {dirty:!!(draftDirty||translationDirty||memoryDirty||groundedDirty||photoDirty||statsFingerprint()!==statsCommitted||Array.from(groundedReviewDrafts.values()).some(row=>row.dirty)),busy:busy||apiPending>0||photoSettingsLoading||activityPolling};
 }
 return {refresh,capture,activity,setPermissions:()=>{schedulerForm.hidden=!canManageSettings();photoForm.hidden=!canManageSettings();},clear:()=>{revision++;groundedEpoch++;groundedCapture=null;groundedCommitted='';groundedReviewDrafts.clear();groundedSource.replaceChildren();groundedReviewed.checked=false;groundedSave.disabled=true;groundedStatus.textContent='Загрузите текущую карточку, затем сверяйте полный текст с её исходными фактами.';photoSettingsEpoch++;photoSettingsValue=null;photoSettings.open=false;photoSettingsBusy(false);photoModel.replaceChildren();photoStatus.textContent='Откройте настройки, чтобы узнать выбранную модель.';details.open=false;postStats.clear();statsCommitted=statsFingerprint();modelHistory.clear();rows.replaceChildren();discoveryRows.replaceChildren();discoveryStatus.textContent='';draftContext=translationContext=null;draftCommitted=translationCommitted=memoryCommitted='';reviewed.checked=false;draftApply.disabled=translationApply.disabled=true;for(const node of [draftTitle,draftBody,draftTags,translationInput,translationOutput,ru,zh,text,tag,views,context,groundedTitle,groundedBody,groundedReason])node.value='';status.textContent='Откройте блок, чтобы получить состояние памяти.';for(const node of Object.values(advertisingLines))node.textContent='';translationTrainingStatus.textContent='Состояние модели перевода пока не получено.';styleStatus.textContent='';schedulerStatus.textContent='Состояние фонового обучения будет получено с Mac.';},destroy:()=>{revision++;groundedEpoch++;photoSettingsEpoch++;clearInterval(activityTimer);postStats.destroy();modelHistory.destroy();details.remove();}};
}
