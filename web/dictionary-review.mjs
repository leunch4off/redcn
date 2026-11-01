// Reviewed pronunciation facts only; no arbitrary transliteration is guessed.
// MOE's Mandarin entries use Traditional spelling; Simplified display is explicit.
export const PRONUNCIATION_TERMS=Object.freeze([
 ['метро','地铁','地鐵','dì tiě',8437],['арендная плата','租金','租金','zū jīn',37356],
 ['залог','押金','押金','yā jīn',40773],['мебель','家具','家具','jiā jù',21829],
 ['холодильник','冰箱','冰箱','bīng xiāng',1760],['спальня','卧室','臥室','wò shì',43162],
 ['кухня','厨房','廚房','chú fáng',32621],['квартира','公寓','公寓','gōng yù',17819],
 ['письменный стол','书桌','書桌','shū zhuō',35343],['стиральная машина','洗衣机','洗衣機','xǐ yī jī',26416],
].map(([ru,zh,sourceWord,pinyin,id])=>Object.freeze({ru,zh,sourceWord,pinyin,
 source:'Ministry of Education, Concised Mandarin Chinese Dictionary (2021)',
 sourceUrl:'https://dict.concised.moe.edu.tw/dictView.jsp?ID='+id+'&la=0&powerMode=0',checkedAt:'2026-11-01',variety:'Mandarin (MOE Taiwan)'})));

const dictionaryReviewKey=value=>String(value??'').trim().replace(/\s+/gu,' ').toLocaleLowerCase('ru');
export function dictionaryPairConflict(rules,ru,zh){
 const key=dictionaryReviewKey(ru),entry=Object.entries(rules&&typeof rules==='object'&&!Array.isArray(rules)?rules:{}).find(([text])=>dictionaryReviewKey(text)===key);
 return {before:entry?.[1]??null,after:zh,conflict:!!entry&&entry[1]!==zh,existingKey:entry?.[0]??null};
}
export function searchPronunciation(query=''){
 const needle=dictionaryReviewKey(query);return PRONUNCIATION_TERMS.filter(row=>dictionaryReviewKey([row.ru,row.zh,row.sourceWord,row.pinyin].join(' ')).includes(needle));
}
export function pronounceKnownTerm(zh,environment=globalThis){
 const term=PRONUNCIATION_TERMS.find(row=>row.zh===zh||row.sourceWord===zh);
 if(!term)return {started:false,reason:'Для этой фразы проверенного произношения в учебном наборе нет.'};
 const speech=environment.speechSynthesis,Utterance=environment.SpeechSynthesisUtterance;
 if(!speech||typeof Utterance!=='function')return {started:false,reason:'Озвучивание браузером недоступно. Откройте запись словаря по ссылке.'};
 const voices=typeof speech.getVoices==='function'?speech.getVoices():[];
 const voice=voices.find(row=>/^zh[-_]CN$/i.test(row.lang))||voices.find(row=>/^zh(?:[-_]|$)/i.test(row.lang));
 if(!voice)return {started:false,reason:'Китайский голос ещё не загружен или отсутствует. Повторите позже либо откройте запись словаря.'};
 const utterance=new Utterance(term.zh);utterance.lang=voice.lang;utterance.voice=voice;utterance.rate=.85;
 speech.cancel();speech.speak(utterance);return {started:true,voice:voice.name||voice.lang,source:'browser-speech'};
}

export function setupDictionaryReview({mount,api,getRules=()=>({}),onUseRule,translatePhrase,toast=()=>{}}={}){
 if(!mount)return null;
 const el=(tag,text,attrs={})=>{const node=document.createElement(tag);if(text!==undefined)node.textContent=text;for(const [key,value]of Object.entries(attrs))node.setAttribute(key,String(value));return node;};
 const buttons=[];let state={revision:0,entries:[],rules:{},events:[]},epoch=0,busy=false,loaded=false,proposalOrigin='operator';
 const shell=el('details',undefined,{class:'dictionary-review'}),heading=el('summary','Утверждение исправлений · общий словарь с версиями');shell.append(heading);
 const body=el('div',undefined,{class:'dictionary-review-body'}),status=el('p','Откройте блок, чтобы загрузить версию общего словаря.',{role:'status','aria-live':'polite'});shell.append(body);body.append(status);mount.replaceChildren(shell);
 const label=(text,node)=>{node.setAttribute('aria-label',text);const wrapper=el('label',text);wrapper.append(node);return wrapper;};
 const button=(text,action,parent=body)=>{const node=el('button',text,{type:'button',class:'btn'});node.onclick=action;buttons.push(node);parent.append(node);return node;};
 const setBusy=value=>{busy=value;for(const node of buttons)if(node.isConnected)node.disabled=value||node.dataset.reviewDisabled==='true';};
 const notify=text=>{status.textContent=text;toast(text);};
 const form=el('div',undefined,{class:'dictionary-review-grid'}),ru=el('input',undefined,{maxlength:200,placeholder:'Русская фраза'}),zh=el('textarea',undefined,{rows:2,maxlength:2000,placeholder:'Проверяемая китайская формулировка'}),source=el('textarea',undefined,{rows:2,maxlength:2000,placeholder:'Источник, пример до/после или замечание оператора'});
 form.append(label('Русская фраза исправления',ru),label('Китайская пара для проверки',zh),label('Источник и пример исправления',source));body.append(form);
 const draftNote=el('p','Переводчик создаёт только черновик. Кнопка предложения не меняет действующий словарь.',{class:'muted'});body.append(draftNote);
 button('Перевести выбранную фразу',async()=>{
  if(busy)return;if(!ru.value.trim())return notify('Введите русскую фразу');
  if(typeof translatePhrase!=='function')return notify('Подключаемый переводчик сейчас недоступен. Введите китайский вариант вручную.');
  const request=++epoch,original=ru.value;setBusy(true);draftNote.textContent='Запрашиваем перевод фразы. Результат требует отдельного утверждения.';
  try{const result=await translatePhrase(original);if(request!==epoch||ru.value!==original)return;if(!result||typeof result.text!=='string')throw Error('Переводчик не вернул черновик');zh.value=result.text;proposalOrigin='machine';draftNote.textContent='Машинный/словарный черновик · '+(result.backend||'подключённый провайдер')+'. '+(result.warnings||[]).map(String).slice(0,3).join(' ')+' Проверьте смысл перед предложением.';}
  catch(error){if(request===epoch)notify(error.message);}finally{if(request===epoch)setBusy(false);}
 });
 ru.addEventListener('input',()=>{epoch++;proposalOrigin='operator';setBusy(false);});
 zh.addEventListener('input',()=>{epoch++;proposalOrigin='operator';setBusy(false);});
 source.addEventListener('input',()=>{epoch++;setBusy(false);});
 button('Предложить пару для утверждения',async()=>{
  if(busy)return;if(!loaded)return notify('Сначала загрузите текущую версию общего словаря');
  await change({action:'propose',ru:ru.value,zh:zh.value,source:source.value,origin:proposalOrigin},()=>{ru.value='';zh.value='';source.value='';proposalOrigin='operator';});
 });
 button('Обновить общий словарь',()=>refresh());
 const filters=el('div',undefined,{class:'dictionary-review-grid'}),query=el('input',undefined,{type:'search',placeholder:'Фраза или источник'}),filter=el('select');
 for(const [value,text]of[['proposed','Ожидают проверки'],['approved','Утверждённые'],['all','Все состояния']]){const option=el('option',text,{value});filter.append(option);}
 filters.append(label('Поиск исправлений',query),label('Состояние исправлений',filter));body.append(filters);
 const list=el('div',undefined,{class:'dictionary-review-list'}),adoption=el('div',undefined,{class:'dictionary-review-list'}),events=el('div',undefined,{class:'dictionary-review-history'});body.append(list,el('h3','Применить утверждённые пары в этом редакторе'),el('p','Общая версия доступна всем авторизованным устройствам этого Mac. Личные правила не перезаписываются автоматически. Перед применением видна действующая и утверждённая формулировка.',{class:'muted'}),adoption,el('h3','История изменений'),events);
 const names={proposed:'На проверке',approved:'Утверждено',rejected:'Отклонено',superseded:'Заменено новой версией'};
 function compare(parent,before,after){const text=el('div',undefined,{class:'dictionary-review-compare'});text.append(el('p','До: '+(before===null?'нет утверждённой пары':before)),el('p','После: '+after));parent.append(text);}
 function render(){
  status.textContent='Версия общего словаря: '+state.revision+' · утверждённых пар: '+Object.keys(state.rules||{}).length+'.';list.replaceChildren();adoption.replaceChildren();events.replaceChildren();for(let i=buttons.length-1;i>=0;i--)if(!buttons[i].isConnected)buttons.splice(i,1);
  const q=dictionaryReviewKey(query.value),rows=(state.entries||[]).filter(row=>(filter.value==='all'||row.state===filter.value)&&dictionaryReviewKey([row.ru,row.zh,row.source].join(' ')).includes(q));
  for(const row of rows.slice(0,100)){
   const card=el('article',undefined,{class:'dictionary-review-entry'});card.append(el('strong',row.ru+' → '+row.zh),el('p',(names[row.state]||row.state)+' · версия '+row.revision+' · '+(row.reviewed_by||row.proposed_by||'')+' · '+row.source,{class:'muted'}));
   const current=dictionaryPairConflict(state.rules,row.ru,row.zh);compare(card,current.before,row.zh);
   if(row.state==='proposed'){
    const confirm=el('input',undefined,{type:'checkbox'});if(current.conflict)card.append(label('Я сравнил варианты и разрешаю заменить утверждённую пару',confirm));
    button('Утвердить пару',async()=>{if(current.conflict&&!confirm.checked)return notify('Сравните варианты и разрешите замену');await change({action:'approve',id:row.id,...(current.conflict?{replaceApproved:true,expectedPrevious:current.before}:{})});},card);
    const reason=el('input',undefined,{maxlength:1000,placeholder:'Почему предложение отклоняется'});card.append(label('Причина отклонения',reason));button('Отклонить предложение',()=>change({action:'reject',id:row.id,reason:reason.value}),card);
   }
   list.append(card);
  }
  if(!rows.length)list.append(el('p','Предложений с такими условиями нет.',{class:'muted'}));
  if(rows.length>100||state.truncatedEntries)list.append(el('p','Показаны до 100 подходящих записей из последних 500 версий. Уточните поиск.',{class:'muted'}));
  for(const [russian,chinese]of Object.entries(state.rules||{})){
   const current=dictionaryPairConflict(getRules(),russian,chinese),card=el('article',undefined,{class:'dictionary-review-entry'});card.append(el('strong',russian));compare(card,current.before,chinese);
   const confirm=el('input',undefined,{type:'checkbox'});if(current.conflict)card.append(label('Заменить моё действующее правило после сравнения',confirm));
   const apply=button(current.before===chinese?'Уже применяется':'Применить эту пару',async()=>{if(current.before===chinese)return;if(current.conflict&&!confirm.checked)return notify('Подтвердите замену личного правила');if(typeof onUseRule!=='function')return notify('Применение в этом редакторе недоступно');try{await onUseRule(current.existingKey||russian,chinese);render();notify('Утверждённая пара применена в этом редакторе');}catch(error){notify(error.message);}},card);apply.disabled=current.before===chinese||typeof onUseRule!=='function';apply.dataset.reviewDisabled=String(apply.disabled);adoption.append(card);
  }
  if(!Object.keys(state.rules||{}).length)adoption.append(el('p','После явного утверждения здесь появятся доступные команде пары.',{class:'muted'}));
  for(const row of (state.events||[]).slice(0,50)){const item=el('p','v'+row.revision+' · '+row.action+' · '+row.actor+' · '+row.ru+' · '+(row.before_zh??'∅')+' → '+(row.after_zh??'∅')+' · '+row.at);events.append(item);}
 }
 async function change(payload,after){
  if(busy)return false;if(typeof api!=='function')return notify('Утверждение общего словаря работает на сервере Mac');
  const request=++epoch;setBusy(true);
  try{const result=await api('dictionary-reviews',{...payload,expectedRevision:state.revision});if(request!==epoch)return false;state=result;loaded=true;after?.();render();notify('Изменение сохранено как версия '+state.revision);return true;}catch(error){if(request===epoch)notify(error.message);return false;}finally{if(request===epoch)setBusy(false);}
 }
 async function refresh(){if(busy)return false;if(typeof api!=='function'){status.textContent='Версионный общий словарь доступен на сервере Mac. Учебное произношение ниже работает отдельно.';return false;}const request=++epoch;setBusy(true);try{const result=await api('dictionary-reviews');if(request!==epoch)return false;state=result;loaded=true;render();return true;}catch(error){if(request===epoch)notify(error.message);return false;}finally{if(request===epoch)setBusy(false);}}
 query.oninput=render;filter.onchange=render;shell.addEventListener('toggle',()=>{if(shell.open&&!loaded)refresh();});
 const pronunciation=el('details',undefined,{class:'dictionary-review pronunciation-review'});pronunciation.append(el('summary','Учебное произношение · 10 проверенных терминов'));
 const pronunciationBody=el('div',undefined,{class:'dictionary-review-body'}),search=el('input',undefined,{type:'search',placeholder:'RU / 中文 / pinyin'}),termList=el('div',undefined,{class:'pronunciation-list'}),speechStatus=el('p','Пиньинь взят из записей MOE Mandarin. Упрощённая форма показана рядом с исходной. Голос браузера — синтез, не эталонная запись словаря.',{class:'muted',role:'status'});pronunciationBody.append(label('Поиск проверенного произношения',search),speechStatus,termList);pronunciation.append(pronunciationBody);mount.append(pronunciation);
 function renderTerms(){termList.replaceChildren();for(const term of searchPronunciation(search.value)){const row=el('article',undefined,{class:'dictionary-review-entry'}),title=el('strong',term.zh+' · '+term.pinyin),meaning=el('p',term.ru+' · словарная форма '+term.sourceWord),link=el('a','Запись словаря и произношение',{href:term.sourceUrl,target:'_blank',rel:'noopener noreferrer'}),play=el('button','Прослушать голосом браузера',{type:'button',class:'btn'});play.onclick=()=>{const result=pronounceKnownTerm(term.zh);speechStatus.textContent=result.started?'Синтез браузера · '+result.voice:result.reason;};row.append(title,meaning,play,link,el('p',term.source+' · проверено '+term.checkedAt,{class:'muted'}));termList.append(row);}if(!termList.childElementCount)termList.append(el('p','В учебном наборе нет такого термина. Произношение не вычисляется по догадке.'));}
 search.oninput=renderTerms;renderTerms();
 return {refresh,render,pronunciations:()=>searchPronunciation(search.value),
  proposeFromKnowledge:async value=>{if(!value||typeof value.ru!=='string'||typeof value.zh!=='string')throw Error('Нужна пара RU → 中文 из заметки');ru.value=value.ru.slice(0,200);zh.value=value.zh.slice(0,2000);source.value=String(value.source||'Замечание из базы знаний').slice(0,2000);proposalOrigin='knowledge';shell.open=true;if(!loaded)await refresh();draftNote.textContent='Замечание из базы знаний. Проверьте пример до/после, предложите пару и затем отдельно утвердите её.';},
  clear:()=>{epoch++;loaded=false;state={revision:0,entries:[],rules:{},events:[]};ru.value='';zh.value='';source.value='';render();setBusy(false);}};
}
