import {UNIVERSITY_CATALOG,searchUniversities,resolveUniversity,resolveCampus,universityStats,universityCampusAvailability,verifiedCampusPoint} from './university-catalog.mjs';

// Reference suggestions do not geocode or change apartment/route facts.
export function setupUniversityUI({local=false,api,onSelect=()=>{},toast=()=>{}}={}){
  const byId=id=>document.getElementById(id),university=byId('university'),campus=byId('campus'),mount=byId('universityKnowledge');
  if(!university||!campus||!mount)return {openKnowledge:()=>{},clearSuggestions:()=>{}};
  const make=(tag,text='',className='')=>{const element=document.createElement(tag);element.textContent=text;if(className)element.className=className;return element;};
  const button=(text,action)=>{const element=make('button',text,'btn');element.type='button';element.onclick=action;return element;};
  const sourceMap=new Map(UNIVERSITY_CATALOG.sources.map(row=>[row.id,row]));
  const schoolMap=new Map(UNIVERSITY_CATALOG.universities.map(row=>[row.id,row]));
  const campusMap=new Map(UNIVERSITY_CATALOG.campuses.map(row=>[row.id,row]));
  const suggestions=new Map();let fieldEpoch=0,knowledgeEpoch=0,timer=null,offset=0,total=0,knowledgeLoaded=false;
  const status=byId('universityReferenceStatus'),query=byId('universitySearch'),kind=byId('universityKind'),results=byId('universityResults'),paging=byId('universityPageStatus'),previous=byId('universityPrev'),next=byId('universityNext');
  function evidence(row){
    const fragment=document.createDocumentFragment();
    const types={teaching_campus:'Учебный кампус',teaching_building:'Учебный корпус',main_address:'Основной / контактный адрес; учебный статус уточните у вуза',administrative:'Административный адрес',contact_address:'Контактный адрес; учебный статус уточните у вуза',student_center:'Студенческий центр',dormitory:'Адрес общежития'};
    if(row.locationType==='faculty_contact')types.faculty_contact='Контактный адрес факультета · расписание занятий уточняется';if(row.locationType==='historical_faculty_address')types.historical_faculty_address='Исторический адрес факультета · не выбирать для текущей поездки';
    const availability=universityCampusAvailability(row);
    if(row.locationLifecycle)fragment.append(make('p',(availability.selectable?'Текущий опубликованный адрес':availability.status==='former-address'?'Прежний адрес подразделения':row.locationLifecycle?.closureScope==='building'?'Корпус закрыт по отдельному источнику':'Подразделение закрыто по отдельному источнику')+' · проверено '+availability.checkedAt,'muted'),make('p',availability.noteRu,'muted'));
    const point=verifiedCampusPoint(row);
    if(point)fragment.append(make('p','Опубликованная точка конкретного корпуса: '+point.latitude+', '+point.longitude,'muted'),make('p','Область точности — точка выбранного адресного объекта. Метрическая погрешность: '+(point.evidence.accuracyMeters===null?'не опубликована':point.evidence.accuracyMeters+' м')+'. Конкретный доступный вход не подтверждён. Проверено '+point.evidence.checkedAt+'.','muted'));
    if(row.locationType)fragment.append(make('p',types[row.locationType]||row.locationType,'muted'));
    if(row.translationStatus==='editorial')fragment.append(make('p','Китайское название — редакционный перевод, проверьте по источнику.','muted'));
    if(row.chinaEvidence){
      const kinds={'official-enrollment':'Официальное упоминание студентов КНР','official-student-testimonials':'Истории китайских студентов на сайте вуза','official-chinese-page':'Официальная китайская страница','official-international-admissions':'Международный приём; наличие студентов КНР не установлено','institution-social-channel':'Официальный канал вуза'};
      const details=make('details','','university-evidence');details.append(make('summary','Основание включения: '+(kinds[row.chinaEvidence.type]||row.chinaEvidence.type)),make('p',row.chinaEvidence.note||'См. указанные источники.','muted'));fragment.append(details);
    }
    if(row.academicUnitHistory?.length){
      const history=make('details','','university-unit-history');history.append(make('summary','Проверка закрытых подразделений'));
      for(const unit of row.academicUnitHistory){history.append(make('h4',unit.ru),make('p',unit.noteRu,'muted'),make('p','Дата документа: '+unit.documentDate+' · проверено '+unit.checkedAt,'muted'));for(const id of unit.sourceIds){const source=sourceMap.get(id);if(source){const link=make('a','Первичный акт о подразделении','university-source');link.href=source.url;link.target='_blank';link.rel='noopener noreferrer';history.append(link);}}}
      fragment.append(history);
    }
    if(row.chinaStudentEvidence){
      const history=row.chinaStudentEvidence,details=make('details','','university-evidence university-china-data');details.append(make('summary','Китайские студенты · исторические данные и личные истории'),make('p','Публикации относятся к разным годам и не имеют общего знаменателя. Эти числа не используются как рейтинг популярности.','muted'));
      const links=record=>{for(const id of record.sourceIds){const source=sourceMap.get(id);if(!source?.url)continue;const link=make('a',source.publisher+' · публикация '+record.publishedAt+' · проверено '+record.verifiedAt,'university-source');link.href=source.url;link.target='_blank';link.rel='noopener noreferrer';details.append(link);}};
      for(const record of history.statistics){const value=new Intl.NumberFormat('ru-RU').format(record.value);details.append(make('h4',(record.relation==='greater-than'?'Более ':'')+value+' студентов из КНР'),make('p','Число сообщено вузом в публикации '+record.publishedAt+'. Отдельная дата измерения не указана; разбивки по кампусам и уровням нет.','muted'),make('p',record.noteRu,'muted'));links(record);}
      const programs={'degree-student':'Обучение по программе высшего образования','degree-alumnus':'История выпускницы / выпускника','visiting-summer-school':'Гостевая летняя школа · краткосрочная программа'};
      for(const record of history.accounts){details.append(make('h4',record.personName),make('p',programs[record.programType],'muted'),make('p',record.summaryRu),make('p',record.scopeNoteRu,'muted'));links(record);}
      fragment.append(details);
    }
    const address=[row.addressRu,row.region].filter(Boolean).join(' · ');
    if(address)fragment.append(make('p',address,'muted'));
    const ids=row.sourceIds||[];
    for(const id of ids.slice(0,3)){
      const source=sourceMap.get(id);if(!source?.url)continue;
      let url;try{url=new URL(source.url);if(!['https:','http:'].includes(url.protocol))continue;}catch{continue;}
      const link=make('a',source.title||source.id);link.href=url.href;link.target='_blank';link.rel='noopener noreferrer';link.className='university-source';fragment.append(link);
    }
    return fragment;
  }
  function choose(row){
    if(!universityCampusAvailability(row).selectable)return toast('Этот адрес исторический или закрыт. Выберите актуальный адрес подразделения.');
    if(row.kind==='campus'||row.universityId){
      const parent=schoolMap.get(row.universityId);if(!parent)return toast('Не найден вуз этого кампуса; проверьте справочник.');
      university.value=parent.ru;campus.value=row.ru;
    }else{
      const oldCampus=resolveCampus(campus.value);
      university.value=row.ru;
      if(oldCampus&&oldCampus.universityId!==row.id)campus.value='';
    }
    clearSuggestions();onSelect({university:university.value,campus:campus.value});
    toast('Выбран вуз или справочный адрес. Время, маршрут и метро квартиры заполните по подтверждённым данным.');
  }
  function clearSuggestions(){++fieldEpoch;for(const element of suggestions.values()){element.replaceChildren();element.hidden=true;}}
  function showField(input,isCampus){
    const epoch=++fieldEpoch;let container=suggestions.get(input);
    if(!container){container=make('div','','university-suggestions');container.setAttribute('aria-live','polite');container.id=isCampus?'campusSuggestions':'universitySuggestions';input.after(container);suggestions.set(input,container);}
    container.hidden=false;container.replaceChildren();
    const selected=resolveUniversity(university.value);
    let matches=searchUniversities(input.value,{limit:100,includeCampuses:isCampus}).filter(row=>isCampus?row.kind==='campus'||!!row.universityId:row.kind!=='campus'&&!row.universityId);
    if(isCampus&&selected)matches=matches.filter(row=>row.universityId===selected.id);
    for(const row of matches.slice(0,10)){
      const element=button(row.ru+' · '+row.zh,()=>{if(epoch===fieldEpoch)choose(row);});element.classList.add('university-suggestion');container.append(element);
    }
    if(!matches.length)container.append(make('p',input.value?'Нет точного справочного варианта. Можно оставить своё название для проверки.':'Введите название вуза / кампуса.','muted'));
    const exact=isCampus?resolveCampus(input.value):resolveUniversity(input.value);
    if(exact){const detail=make('div','','university-field-evidence');detail.append(evidence(exact));container.append(detail);}
  }
  for(const [input,isCampus] of [[university,false],[campus,true]]){
    input.autocomplete='off';input.setAttribute('aria-controls',isCampus?'campusSuggestions':'universitySuggestions');
    input.addEventListener('focus',()=>showField(input,isCampus));input.addEventListener('input',()=>showField(input,isCampus));
    input.addEventListener('keydown',event=>{if(event.key==='Escape')clearSuggestions();});
  }
  document.addEventListener('click',event=>{if(![university,campus,...suggestions.values()].some(element=>element===event.target||element.contains(event.target)))clearSuggestions();});
  const number=value=>new Intl.NumberFormat('ru-RU').format(Number(value)||0);
  function renderStats(value=universityStats()){
    const schools=value.universities??value.institutions??UNIVERSITY_CATALOG.universities.length,campuses=value.campuses??UNIVERSITY_CATALOG.campuses.length;
    status.textContent=`Вузов: ${number(schools)} · кампусов, корпусов и адресов: ${number(campuses)}. Тип площадки указан отдельно. Близость к квартире и время в пути проверяются отдельно. Указаны основания включения и источники; это не рейтинг популярности.`;
  }
  function renderRows(rows){
    results.replaceChildren();
    for(const row of rows.slice(0,40)){
      const card=make('article','','university-reference-row');card.dataset.referenceId=row.id;card.dataset.referenceKind=row.kind||(row.universityId?'campus':'university');card.append(make('h3',row.ru),make('p',row.zh,'university-zh'));
      if(row.universityId){const parent=schoolMap.get(row.universityId);if(parent)card.append(make('p',parent.ru,'muted'));}
      const action=row.universityId?(row.locationType==='teaching_campus'?'Выбрать этот кампус':'Выбрать этот адрес'):'Выбрать этот вуз';
      const availability=universityCampusAvailability(row),pick=button(action,()=>choose(row));pick.disabled=!availability.selectable;card.append(evidence(row),pick);if(!availability.selectable&&campusMap.has(availability.currentCampusId))card.append(button('Выбрать актуальный адрес факультета',()=>choose(campusMap.get(availability.currentCampusId))));results.append(card);
    }
    if(!rows.length)results.append(make('p','Совпадений нет. Попробуйте сокращение, русское или китайское название.','muted'));
  }
  async function search(at=0){
    const epoch=++knowledgeEpoch;offset=at;previous.disabled=next.disabled=true;paging.textContent='Ищем в справочнике…';
    try{
      const q=query.value.trim(),filter=kind.value;let data;
      if(local&&api&&q)data=await api('universities/search',{query:q,kind:filter,offset,limit:40});
      else{const all=searchUniversities(q,{limit:10000,includeCampuses:true,includeInactive:true}).filter(row=>filter==='all'||(row.kind|| (row.universityId?'campus':'university'))===filter);data={entries:all.slice(at,at+40),total:all.length,offset:at};}
      if(epoch!==knowledgeEpoch)return;
      total=Number(data.total)||0;renderRows(data.entries||[]);
      paging.textContent=total?`Найдено ${number(total)} · ${number(at+1)}–${number(Math.min(at+40,total))}`:'Совпадений нет';
    }catch(error){if(epoch===knowledgeEpoch){results.replaceChildren();paging.textContent=error.message;total=0;}}
    finally{if(epoch===knowledgeEpoch){previous.disabled=offset<=0;next.disabled=offset+40>=total;}}
  }
  async function openKnowledge(){
    if(knowledgeLoaded)return;knowledgeLoaded=true;renderStats();search(0);
    if(local&&api)try{const value=await api('universities');renderStats(value.stats||value);}catch(error){toast('Справочник доступен локально. База данных: '+error.message);}
  }
  query.addEventListener('input',()=>{clearTimeout(timer);++knowledgeEpoch;timer=setTimeout(()=>search(0),300);});
  kind.addEventListener('change',()=>{clearTimeout(timer);search(0);});previous.onclick=()=>search(Math.max(0,offset-40));next.onclick=()=>search(offset+40);
  renderStats();
  return {openKnowledge,clearSuggestions};
}
