// Only measured backend counters and elapsed wall time are shown here.
// A detector has no tokens; cached inference must not look like a fresh run.
const aiProgressPhases={waiting:'Ожидаем свободную память',loading:'Загружаем модель',preparing:'Готовим запрос',prefill:'Модель читает запрос',generating:'Создаём китайское описание',analyzing:'Ищем видимую мебель и технику',complete:'Обработка завершена',cached:'Результат из кеша',cancelled:'Запрос отменён',timeout:'Время запроса истекло',unavailable:'Модель сейчас недоступна',error:'Не удалось завершить запрос'};
const aiProgressActivePhases=new Set(['waiting','loading','preparing','prefill','generating','analyzing']);
const aiProgressNumber=value=>typeof value==='number'&&Number.isFinite(value)&&value>=0?value:null;
const aiProgressCount=value=>Number.isInteger(value)&&value>=0&&value<=10000000?value:null;
const aiProgressFormat=value=>value.toLocaleString('ru-RU',{maximumFractionDigits:value<1?2:1});
const aiProgressIdentity=value=>typeof value==='string'&&/^[A-Za-z0-9][A-Za-z0-9_.:/+\-]{0,127}$/.test(value)&&!value.includes('://')?value:'';

export function aiTextOutcome(value){
 if(!value||typeof value!=='object')return '';
 if(value.fallback===true||value.backend==='grounded-advertising'||value.textSource==='rules')return 'Ответ локальной модели отклонён. Этот текст составлен правилами по фактам карточки.';
 if(value.modelContribution==='selection-of-layout-only'||value.generationMode==='model-structure-verified-renderer')return 'ИИ выбрал схему подачи; формулировки и факты собраны генератором.';
 if(value.status==='review')return 'Черновик локальной модели: требуется проверка китайского текста и фактов.';
 if(['blocked','unavailable','cancelled'].includes(value.status))return 'Модель не создала принятого варианта; сохранён предыдущий текст.';
 return '';
}

export function aiProgressText(value,{extraElapsed=0}={}){
 if(!value||typeof value!=='object'||Array.isArray(value)||!Object.hasOwn(aiProgressPhases,value.phase))return null;
 const active=value.active!==false&&aiProgressActivePhases.has(value.phase),cached=value.cached===true||value.phase==='cached';
 const rules=value.textSource==='rules',model=aiProgressIdentity(value.model),kind=rules?'Китайский текст по правилам · ответ ИИ отклонён':value.operation==='text'?'Текстовый ИИ':'ИИ · мебель и техника';
 const details=[],metrics=[],elapsed=aiProgressNumber(value.elapsedSeconds),index=aiProgressCount(value.photoIndex),total=aiProgressCount(value.photoTotal);
 if(model)details.push((rules?'Модель черновика: ':'')+model);
 if(total&&index!==null&&index>0&&index<=total)details.push('Фото '+index+' из '+total);
 details.push((value.active===false&&aiProgressActivePhases.has(value.phase)?'Последний этап: ':'')+aiProgressPhases[value.phase]);
 if(elapsed!==null)metrics.push((active?'Прошло ':'Затрачено ')+aiProgressFormat(elapsed+(active?Math.max(0,aiProgressNumber(extraElapsed)||0):0))+' с');
 if(cached)return {title:kind,details:details.join(' · '),metrics:metrics.join(' · '),note:'Сохранённый результат: модель повторно не запускалась; скорость не измеряется.'};
 const tokenBased=value.tokenBased!==false&&['mlx','ollama'].includes(value.backend);
 const detector=value.tokenBased===false||(aiProgressIdentity(value.backend)&&!['mlx','ollama'].includes(value.backend));
 const rate=aiProgressNumber(tokenBased?value.tokensPerSecond:value.imagesPerSecond),tokens=aiProgressCount(value.generatedTokens),seconds=aiProgressNumber(tokenBased?value.generationSeconds:value.inferenceSeconds);
 const stale=active&&(aiProgressNumber(value.sampleAgeSeconds)||0)>5;
 if(tokenBased){
  if(tokens!==null)metrics.push('Создано '+tokens+' токенов');
  if(rate!==null&&rate>0&&tokens>0)metrics.push((stale?'Последнее измерение: ':'')+aiProgressFormat(rate)+' токенов/с');
  if(seconds!==null&&seconds>0)metrics.push('Генерация '+aiProgressFormat(seconds)+' с');
  const processed=aiProgressCount(value.promptTokensProcessed),promptTotal=aiProgressCount(value.promptTokensTotal);
  if(value.phase==='prefill'&&processed!==null&&promptTotal)metrics.push('Запрос: '+processed+' / '+promptTotal+' токенов');
 }else if(detector){
  if(rate!==null&&rate>0)metrics.push((stale?'Последнее измерение: ':'')+aiProgressFormat(rate)+' фото/с');
  if(seconds!==null&&seconds>0)metrics.push('Распознавание '+aiProgressFormat(seconds)+' с');
 }
 const note=rules?(tokenBased&&rate>0&&tokens>0?'Скорость относится к черновику ИИ. Итоговая подача собрана из проверенных фактов.':'Итоговая подача собрана из проверенных фактов. Измеренная скорость модели для этого результата недоступна.'):tokenBased?(rate>0?'Скорость измерена при генерации ответа; загрузка модели учитывается во времени запроса.':active?'Скорость появится после первых сгенерированных токенов.':'Модель не сообщила измеренную скорость генерации.'):detector?'Детектор распознаёт изображения и не генерирует токены. Показываем время и фото/с.':'Показываем время запроса. Модель сообщит измеренную скорость после обработки.';
 return {title:kind,details:details.join(' · '),metrics:metrics.join(' · '),note};
}

export function createAiProgress({after,id}){
 const panel=document.createElement('section'),title=document.createElement('strong'),details=document.createElement('p'),metrics=document.createElement('p'),note=document.createElement('small');
 panel.id=id;panel.className='ai-progress';panel.hidden=true;panel.setAttribute('aria-label','Работа локального ИИ');
 Object.assign(panel.style,{margin:'12px 0',padding:'12px',border:'1px solid var(--line,#d8dee9)',borderRadius:'12px',minWidth:'0',overflowWrap:'anywhere'});
 for(const child of [details,metrics])Object.assign(child.style,{margin:'6px 0',fontVariantNumeric:'tabular-nums'});
 details.setAttribute('role','status');details.setAttribute('aria-live','polite');metrics.setAttribute('aria-live','off');
 panel.append(title,details,metrics,note);after.insertAdjacentElement('afterend',panel);
 let latest=null,received=0,timer=0;
 function render(){
  const text=aiProgressText(latest,{extraElapsed:(performance.now()-received)/1000});panel.hidden=!text;
  if(!text){for(const child of [title,details,metrics,note])child.textContent='';return;}
  title.textContent=text.title;details.textContent=text.details;metrics.textContent=text.metrics;note.textContent=text.note;
 }
 function update(value){
  clearInterval(timer);latest=value&&typeof value==='object'?{...value}:null;received=performance.now();render();
  if(latest?.active!==false&&aiProgressActivePhases.has(latest?.phase))timer=setInterval(()=>{if(!document.hidden)render();},500);
 }
 function stop(phase){
  if(!latest)return;
  const elapsed=aiProgressNumber(latest.elapsedSeconds);
  update({...latest,...(phase&&Object.hasOwn(aiProgressPhases,phase)?{phase}:{}),active:false,
   ...(elapsed!==null?{elapsedSeconds:elapsed+(latest.active!==false&&aiProgressActivePhases.has(latest.phase)?(performance.now()-received)/1000:0)}:{})});
 }
 return {update,stop,clear:()=>update(null),destroy(){clearInterval(timer);panel.remove();}};
}
