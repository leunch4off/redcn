import {UNIVERSITY_CATALOG} from './university-catalog.mjs';
import {normalizeUniversityRoutes,resolveUniversityRoutes} from './university-routes.mjs';
import {calculateInitialPayment} from './initial-payment.mjs';

// This is an operator comparison of saved evidence. It neither geocodes homes
// nor calculates journeys, and never substitutes a university's main building.
const commuteSchools=new Map(UNIVERSITY_CATALOG.universities.map(row=>[row.id,row]));
const commuteSources=new Map(UNIVERSITY_CATALOG.sources.map(row=>[row.id,row]));
const commuteCampusMap=new Map(UNIVERSITY_CATALOG.campuses.map(row=>[row.id,row]));
const commuteKindNames={teaching_building:'Учебный корпус',teaching_campus:'Учебный кампус',main_address:'Главный адрес · назначение уточнить',contact_address:'Контактный адрес · не подтверждён как учебный корпус',administrative:'Административный корпус · учебное назначение не подтверждено',student_center:'Студенческий центр',dormitory:'Общежитие'};
const commuteText=(value,max=240)=>typeof value==='string'?value.trim().slice(0,max):'';
function commuteNumeric(value){if(!['number','string'].includes(typeof value)||!String(value).trim()||!/^\d+(?:\.\d{1,2})?$/.test(String(value).trim()))return null;const number=Number(value);return Number.isFinite(number)?number:null;}
function commuteFields(object){return object?.fields&&typeof object.fields==='object'&&!Array.isArray(object.fields)?object.fields:object&&typeof object==='object'?object:{};}

export function universityCampusRegistry({query='',universityId='',teachingOnly=false}={}){
 const needle=commuteText(query,300).toLocaleLowerCase('ru');
 return UNIVERSITY_CATALOG.campuses.filter(row=>(!universityId||row.universityId===universityId)&&(!teachingOnly||['teaching_building','teaching_campus'].includes(row.locationType))&&(!needle||[row.ru,row.zh,row.addressRu,...row.aliases,commuteSchools.get(row.universityId)?.ru,commuteSchools.get(row.universityId)?.zh].join(' ').toLocaleLowerCase('ru').includes(needle))).map(row=>({
  id:row.id,universityId:row.universityId,university:commuteSchools.get(row.universityId)?.ru||row.universityId,ru:row.ru,zh:row.zh,address:row.addressRu,locationType:row.locationType,locationLabel:commuteKindNames[row.locationType]||'Назначение адреса уточнить',snapshotAt:UNIVERSITY_CATALOG.asOf,
  sources:(row.sourceIds||[]).map(id=>commuteSources.get(id)).filter(Boolean).map(source=>({id:source.id,url:source.url,publisher:source.publisher,title:source.title,checkedAt:source.verifiedAt,evidenceType:source.evidenceType,status:source.status,retrievalMethod:source.retrievalMethod||'',note:source.note||'',directFetchStatus:source.directFetchStatus||''})),
  addressVerified:['official','institution-verified'].includes(row.addressStatus),addressEvidence:row.addressEvidence?JSON.parse(JSON.stringify(row.addressEvidence)):null,institutionUrlVerified:(row.sourceIds||[]).some(id=>commuteSources.get(id)?.evidenceType==='official-university'&&commuteSources.get(id)?.status==='verified')
 }));
}

export function commuteTimeRange(value){
 if(typeof value!=='string')return null;const match=/^\s*(\d{1,4})(?:\s*[–—-]\s*(\d{1,4}))?\s*$/.exec(value);if(!match)return null;
 const min=Number(match[1]),max=Number(match[2]||match[1]);return min>=0&&max>=min&&max<=1440?{min,max}:null;
}

export function rentalHousingContext(object={}){
 const fields=commuteFields(object),value=commuteText(object.workflow?.housingType||fields.workflow?.housingType||fields.housingType||fields.accommodationType||object.housingType,40);
 const kinds={apartment:'apartment',flat:'apartment',квартира:'apartment',room:'room',комната:'room',dormitory:'dormitory',общежитие:'dormitory'};
 const kind=Object.hasOwn(kinds,value.toLowerCase())?kinds[value.toLowerCase()]:'unknown';
 const mentioned=/общежити|宿舍/iu.test([fields.details,fields.sourceDescription,fields.description].filter(text=>typeof text==='string').join('\n'));
 return {kind,label:{apartment:'Квартира',room:'Комната',dormitory:'Общежитие · явно указанный тип',unknown:'Тип жилья не подтверждён'}[kind],note:mentioned&&kind!=='dormitory'?'Упоминание общежития в описании окружения не меняет тип жилья.':'',district:commuteText(fields.district,180)||'Район не указан'};
}

export function districtHousingSummary(objects=[]){
 const groups=new Map();for(const object of Array.isArray(objects)?objects:[]){const housing=rentalHousingContext(object),key=housing.district;if(!groups.has(key))groups.set(key,{district:key,total:0,apartment:0,room:0,dormitory:0,unknown:0});const row=groups.get(key);row.total++;row[housing.kind]++;}
 return [...groups.values()].sort((a,b)=>a.district.localeCompare(b.district,'ru'));
}

export function compareCommutes(objects=[],options={}){
 const campusId=commuteText(options.campusId,100),campus=commuteCampusMap.get(campusId),limit=options.maxMinutes===''||options.maxMinutes===undefined||options.maxMinutes===null?null:commuteNumeric(options.maxMinutes);
 if(options.maxMinutes!==''&&options.maxMinutes!==undefined&&options.maxMinutes!==null&&(limit===null||!Number.isInteger(limit)||limit>1440))throw Error('Лимит дороги должен быть целым числом от 0 до 1440 минут.');
 const modes=options.mode==='all'?new Set(['walk','public','bus','metro','bike','car']):options.mode==='walk'?new Set(['walk']):new Set(['public','bus','metro']);
 const rows=(Array.isArray(objects)?objects:[]).map((object,index)=>{
  const fields=commuteFields(object),normalized=normalizeUniversityRoutes(fields),resolved=resolveUniversityRoutes(fields,{now:options.now,maxAgeDays:options.maxAgeDays});
  const matching=resolved.map((route,i)=>({...route,rawTime:normalized[i]?.time||''})).filter(route=>campus&&(route.selectedCampusId===campusId||route.campusId===campusId));
  const candidates=matching.map(route=>{const range=commuteTimeRange(route.rawTime),reasons=[];if(!route.confirmedDestination)reasons.push('Место назначения противоречиво или не подтверждено.');if(!modes.has(route.mode))reasons.push('Другой или неподтверждённый вид транспорта.');if(!range)reasons.push('Нет числового подтверждённого диапазона времени.');if(!route.verification.verified&&!(options.includeEstimates===true&&route.verification.estimated&&route.verification.checkedAt&&!route.verification.future&&!route.verification.stale))reasons.push('Нет актуальной проверки маршрута.');if(limit!==null&&range&&range.max>limit)reasons.push(range.min>limit?'Дорога превышает лимит.':'Верхняя граница времени превышает лимит.');return {route,range,reasons};});
  candidates.sort((a,b)=>a.reasons.length-b.reasons.length||(a.range?.max??Infinity)-(b.range?.max??Infinity));const best=candidates[0]||null,reasons=best?[...best.reasons]:[campus?'Маршрут к выбранному корпусу не записан.':'Сначала выберите конкретный кампус / корпус.'];
  const price=commuteNumeric(fields.price),rent=(fields.pricePeriod||'month')==='month'&&(fields.priceCurrency||'RUB')==='RUB'?price:null;
  const housing=rentalHousingContext(object),payment=calculateInitialPayment(fields);
  if(options.housingKind&&options.housingKind!=='all'&&housing.kind!==options.housingKind)reasons.push('Другой или неподтверждённый тип жилья.');
  if(options.district&&housing.district!==options.district)reasons.push('Другой район.');
  return {id:commuteText(object.id||fields.resultId||fields.editorIdentity,100)||'row-'+index,title:commuteText(object.title||object.titles?.[0]||fields.name||fields.address,500)||'Без названия',sourceUrl:commuteText(fields.url,2048),monthlyRent:rent,payment,housing,route:best?.route||null,timeRange:best?.range||null,status:reasons.length?'review':'included',reasons};
 });
 return {campus:campus?universityCampusRegistry({universityId:campus.universityId}).find(row=>row.id===campusId):null,rows,included:rows.filter(row=>row.status==='included'),review:rows.filter(row=>row.status!=='included'),districts:districtHousingSummary(objects)};
}

export function setupCommuteCompare({mount,getObjects=()=>[],onOpen=()=>{},toast=()=>{}}={}){
 if(!mount)return {refresh(){},destroy(){}};
 const make=(tag,text='',className='')=>{const node=document.createElement(tag);node.textContent=text;if(className)node.className=className;return node;},shell=make('section','','commute-compare');
 const heading=make('h2','Сравнение квартир по конкретному кампусу'),note=make('p','Выберите учебный корпус. Здесь сравниваются только введённые проверенные сведения: автобусы, пересадки и время не вычисляются из расстояния. Внутренние платежи клиенту не публикуются.','muted');shell.append(heading,note);
 const filters=make('div','','commute-compare-filters');
 function label(text,node){const wrapper=make('label',text);node.setAttribute('aria-label',text);wrapper.append(node);filters.append(wrapper);return node;}
 function select(text,items){const node=label(text,make('select'));for(const [value,name]of items)node.append(new Option(name,value));return node;}
 const school=select('Вуз для сравнения',[['','Все вузы'],...UNIVERSITY_CATALOG.universities.map(row=>[row.id,row.ru])]),campus=select('Конкретный кампус / корпус',[['','Выберите корпус']]);
 function campusOptions(){const previous=campus.value;campus.replaceChildren(new Option('Выберите корпус',''));for(const row of universityCampusRegistry({universityId:school.value}))campus.append(new Option(row.ru+' · '+row.locationLabel,row.id));campus.value=[...campus.options].some(option=>option.value===previous)?previous:'';}
 const max=label('Максимальная дорога, минут',make('input'));max.type='number';max.min='0';max.max='1440';max.step='1';max.placeholder='Без лимита';
 const mode=select('Транспорт сравнения',[['public','Общественный транспорт'],['walk','Только пешком'],['all','Любой явно указанный транспорт']]),kind=select('Тип жилья',[['all','Все типы'],['apartment','Квартиры'],['room','Комнаты'],['dormitory','Только явные общежития'],['unknown','Тип не подтверждён']]);
 const district=select('Район сравнения',[['','Все районы']]);
 const estimate=label('Включить явно помеченные свежие оценки',make('input'));estimate.type='checkbox';
 const unknown=label('Показать отложенные и неподтверждённые записи',make('input'));unknown.type='checkbox';unknown.checked=true;
 shell.append(filters);const status=make('p','','commute-compare-status');status.setAttribute('role','status');status.setAttribute('aria-live','polite');const tableWrap=make('div','','commute-compare-table-wrap'),districts=make('div','','commute-districts');shell.append(status,tableWrap,districts);
 const registry=make('details','','commute-campus-registry'),summary=make('summary','Справочник корпусов: адреса, назначение и первичные источники'),search=make('input'),entries=make('div');search.setAttribute('aria-label','Поиск корпуса в справочнике');search.placeholder='Вуз, адрес или китайское название';registry.append(summary,search,entries);shell.append(registry);mount.replaceChildren(shell);
 const money=value=>value===null?'Не подтверждено':new Intl.NumberFormat('ru-RU',{style:'currency',currency:'RUB',maximumFractionDigits:2}).format(value);
 function paintRegistry(){
  entries.replaceChildren();const rows=universityCampusRegistry({query:search.value,universityId:school.value});entries.append(make('p',rows.length+' адресных записей · срез '+UNIVERSITY_CATALOG.asOf+' · не полный реестр факультетов','muted'));
  for(const row of rows.slice(0,40)){
   const article=make('article','','commute-campus-row');article.append(make('strong',row.ru+' / '+row.zh),make('p',row.address),make('p',row.locationLabel,'muted'));
   if(row.addressEvidence)article.append(make('p','Проверка адреса '+row.addressEvidence.checkedAt+' · '+row.addressEvidence.note,'muted'));
   for(const source of row.sources){
    const link=make('a',source.publisher+' · '+source.checkedAt+' · '+(source.evidenceType==='official-university'?'сайт учреждения':'официальный образовательный портал'));link.href=source.url;link.target='_blank';link.rel='noopener noreferrer';article.append(link);
    if(source.retrievalMethod==='primary-search-excerpt')article.append(make('p','Подтверждено по индексированному фрагменту сайта учреждения; полная страница не прочитана.','muted'));
   }
   if(!row.institutionUrlVerified)article.append(make('p','URL учреждения для этой записи ещё не подтверждён: источник — образовательный портал.','commute-review'));
   const choose=make('button','Сравнить дорогу к этому адресу','btn');choose.type='button';choose.onclick=()=>{school.value=row.universityId;campusOptions();campus.value=row.id;render();};article.append(choose);entries.append(article);
  }
  if(rows.length>40)entries.append(make('p','Уточните запрос, чтобы увидеть остальные адреса.','muted'));
 }
 function render(){try{const result=compareCommutes(getObjects(),{campusId:campus.value,maxMinutes:max.value,mode:mode.value,housingKind:kind.value,district:district.value,includeEstimates:estimate.checked});status.textContent=result.campus?'Подходят: '+result.included.length+' · на проверку / вне фильтра: '+result.review.length+' · '+result.campus.ru:'Выберите конкретный корпус: общий вуз не подставляется вместо кампуса.';tableWrap.replaceChildren();const table=make('table'),head=make('thead'),header=make('tr');for(const text of ['Объект / район','Жильё','Аренда / месяц','Первый платёж · внутренний','Дорога до корпуса','Проверка / решение'])header.append(make('th',text));head.append(header);table.append(head);const body=make('tbody');for(const row of result.rows.filter(row=>unknown.checked||row.status==='included')){const tr=make('tr','','commute-'+row.status);const title=make('td');title.append(make('strong',row.title),make('p',row.housing.district,'muted'));const open=make('button','Открыть карточку','btn');open.type='button';open.onclick=()=>onOpen(row.id);title.append(open);tr.append(title);const housing=make('td',row.housing.label);if(row.housing.note)housing.append(make('p',row.housing.note,'muted'));tr.append(housing,make('td',money(row.monthlyRent)));const payment=make('td',row.payment.complete?money(row.payment.total):'Не окончательный · известно '+money(row.payment.subtotal));if(!row.payment.complete)payment.append(make('p',[...row.payment.missing,...row.payment.warnings].join(' · '),'muted'));tr.append(payment);const route=make('td',row.route?row.route.timeLabel:'Нет маршрута к этому корпусу');if(row.route){route.append(make('p',[row.route.transportLabel,row.route.partsLabel].filter(Boolean).join(' · ')));for(const text of row.route.segmentLabels)route.append(make('p',text,'muted'));for(const [provider,name]of [['yandex','Яндекс'],['google','Google']])if(row.route.links[provider]){const a=make('a',name);a.href=row.route.links[provider];a.target='_blank';a.rel='noopener noreferrer';route.append(a);}}tr.append(route);const decision=make('td',row.status==='included'?'В пределах выбранного лимита':'Требует проверки / вне фильтра');if(row.route)decision.append(make('p',row.route.verification.summary,'muted'));for(const reason of row.reasons)decision.append(make('p',reason,'muted'));tr.append(decision);body.append(tr);}table.append(body);tableWrap.append(table);districts.replaceChildren(make('h3','Состав сохранённой библиотеки по районам'));for(const row of result.districts)districts.append(make('p',row.district+': квартир '+row.apartment+', комнат '+row.room+', общежитий с явной меткой '+row.dormitory+', тип не уточнён '+row.unknown));districts.append(make('p','Это состав ваших карточек, а не статистика района или доказательство близости к общежитиям.','muted'));}catch(error){status.textContent=error.message;toast(error.message);}}
 function refresh(){const selected=district.value;district.replaceChildren(new Option('Все районы',''));for(const row of districtHousingSummary(getObjects()))district.append(new Option(row.district,row.district));district.value=[...district.options].some(option=>option.value===selected)?selected:'';render();paintRegistry();}
 school.onchange=()=>{campus.value='';campusOptions();render();paintRegistry();};for(const node of [campus,max,mode,kind,district,estimate,unknown])node.addEventListener('input',render);search.oninput=paintRegistry;campusOptions();refresh();return {refresh,destroy(){mount.replaceChildren();}};
}
