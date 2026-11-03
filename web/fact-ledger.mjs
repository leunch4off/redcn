import {rentalWitnessComparable} from './source-adapters/rental-values.mjs';
// Private operator facts. No source HTML, image bytes or credentials are stored
// in revision snapshots. This module also supplies the shared JS schema guard.
const factNumericFields=new Set('price deposit commission commissionAmount area rooms bedrooms livingRooms kitchen living bedroomArea occupants floor floors combinedWcs separateWcs buildingYear entrances ceiling balconies loggias prepayMonths contractMonths rentFreeMonths minMonths registrationMonths registrationPeople latitude longitude'.split(' '));
const factBooleanFields=new Set('ready newRenovation separate children pets shortTerm pickup luggage arrival support isDemo eligibleForNew'.split(' '));
const factListFields=new Set('features photos photoItems layouts transitDetails sourceDatesList photoEvidence photoAutoFeatures photoRejectedFeatures relatedOffers universityRoutes oneTimeFees'.split(' '));
const factMapFields=new Set('featureCounts photoEdits sourceAudit sourceReadiness sourceDates photoFeatureBaseline studioProfile serviceConfirmations'.split(' '));
const factTextFields=new Set('url source name district complex districtZh metro walk university uniTime uniMode route details style layout transit universities highlight description utilities registration registrationPolicy childrenPolicy petPolicy smokingPolicy guestPolicy subletPolicy shortTermPolicy campus region audience seller commissionStatus commissionSource commissionCurrency depositCurrency pricePeriod priceCurrency phraseNotes city address renovation windows bathroom buildingType buildingSeries floorType heating emergency leaseTerm sourceDescription railways lift resultId editorIdentity titleFormula sellerSource publicationStatus serviceBenefits'.split(' '));
const factTextOrNumberFields=new Set(['walk','uniTime']);
export const FACT_TRACKED_FIELDS=Object.freeze('price pricePeriod priceCurrency deposit depositCurrency commission commissionStatus commissionAmount commissionCurrency utilities prepayMonths contractMonths leaseTerm area living kitchen bedrooms livingRooms rooms floor floors layout metro walk transit university uniTime uniMode route campus universities universityRoutes latitude longitude address district city seller buildingType buildingYear renovation bathroom ceiling windows balconies loggias ready newRenovation features featureCounts childrenPolicy petPolicy smokingPolicy guestPolicy subletPolicy registrationPolicy registration registrationMonths registrationPeople pickup luggage arrival support'.split(' '));
const factLabels={price:'Аренда',pricePeriod:'Период ставки',priceCurrency:'Валюта ставки',deposit:'Залог',depositCurrency:'Валюта залога',commission:'Комиссия, % · внутри команды',commissionAmount:'Фиксированная комиссия · внутри команды',commissionCurrency:'Валюта комиссии',commissionStatus:'Статус комиссии',utilities:'Коммунальные услуги',area:'Общая площадь',living:'Жилая площадь',kitchen:'Площадь кухни',rooms:'Комнаты',bedrooms:'Спальни',livingRooms:'Гостиные',floor:'Этаж',floors:'Этажность',layout:'Планировка',metro:'Основная станция',walk:'Пешком, минуты',transit:'Маршруты',features:'Мебель и техника',featureCounts:'Количество предметов',childrenPolicy:'Дети',petPolicy:'Животные',registrationPolicy:'Условия регистрации источника',registration:'Услуга регистрации',address:'Адрес',district:'Район',city:'Город',universities:'Вузы и маршруты',universityRoutes:'Маршруты к кампусам',contractMonths:'Срок договора',prepayMonths:'Месяцев предоплаты',leaseTerm:'Тип аренды',smokingPolicy:'Курение',guestPolicy:'Гости',subletPolicy:'Субаренда',registrationMonths:'Срок регистрации',registrationPeople:'Число регистраций',pickup:'Встреча',luggage:'Багаж',arrival:'Помощь по прибытии',support:'Поддержка'};
const factLedgerStorageKey='most-fact-ledger-v1';
const factClone=value=>JSON.parse(JSON.stringify(value));
const factOwn=(value,key)=>Object.prototype.hasOwnProperty.call(value,key);
const factIsObject=value=>!!value&&typeof value==='object'&&!Array.isArray(value)&&(Object.getPrototypeOf(value)===Object.prototype||Object.getPrototypeOf(value)===null);
function factJsonProblems(value,problems,depth=0,active=new Set()){
 if(depth>20){problems.push('Слишком глубокая структура карточки');return;}
 if(value===null||typeof value==='boolean')return;
 if(typeof value==='string'){if(value.length>8_000_000||value.includes('\0'))problems.push('Слишком длинный или некорректный текст карточки');return;}
 if(typeof value==='number'){if(!Number.isFinite(value))problems.push('Нечисловое значение карточки');return;}
 if(!Array.isArray(value)&&!factIsObject(value)){problems.push('Карточка должна содержать только JSON-значения');return;}
 if(active.has(value)){problems.push('Циклическая структура карточки');return;}
 if(Object.keys(value).length>5000){problems.push('Слишком большая коллекция карточки');return;}
 active.add(value);
 for(const [key,item]of Object.entries(value)){
  if(item===undefined&&!Array.isArray(value))continue; // Omitted JSON properties in legacy callers.
  if(['__proto__','constructor','prototype'].includes(key)){problems.push('Неверное имя поля карточки');continue;}
  factJsonProblems(item,problems,depth+1,active);if(problems.length>30)break;
 }
 active.delete(value);
}
export function validateListingFields(fields){
 const problems=[];
 if(!factIsObject(fields))return {valid:false,problems:['Поля объявления должны быть объектом']};
 factJsonProblems(fields,problems);
 for(const [key,value]of Object.entries(fields)){
  if(value===null||value===undefined)continue;
  if(factNumericFields.has(key)){
   if(!['string','number'].includes(typeof value)){problems.push(key+': требуется число или пустое значение');continue;}
   // Old text controls may contain source/manual prose. Preserve it for
   // operator review; only an actual numeric literal is a numeric fact.
   const number=factNumericValue(value);
   if(typeof value==='string'&&/^[+-]?(?:nan|inf(?:inity)?)$/iu.test(value.trim()))problems.push(key+': требуется конечное число');
   else if(['latitude','longitude'].includes(key)&&typeof value==='string'&&value.trim()&&number===undefined)problems.push(key+': требуются числовые координаты');
   else if(number!==undefined){
    const [minimum,maximum]=key==='latitude'?[-90,90]:key==='longitude'?[-180,180]:[0,1_000_000_000];
    if(!Number.isFinite(number)||number<minimum||number>maximum)problems.push(key+': число вне допустимого диапазона');
   }
  }else if(factBooleanFields.has(key)&&typeof value!=='boolean')problems.push(key+': требуется логическая отметка');
  else if(factListFields.has(key)&&!Array.isArray(value))problems.push(key+': требуется список');
  else if(factMapFields.has(key)&&!factIsObject(value))problems.push(key+': требуется объект');
  else if(factTextOrNumberFields.has(key)&&!['string','number'].includes(typeof value))problems.push(key+': требуется текст или число минут');
  else if(factTextFields.has(key)&&!factTextOrNumberFields.has(key)&&typeof value!=='string')problems.push(key+': требуется текст');
 }
 for(const key of ['features','photos','photoAutoFeatures','photoRejectedFeatures'])if(Array.isArray(fields[key])&&fields[key].some(value=>typeof value!=='string'))problems.push(key+': список должен содержать текстовые значения');
 if(factIsObject(fields.featureCounts)&&Object.values(fields.featureCounts).some(value=>!['number','string'].includes(typeof value)||!/^\d+(?:\.\d+)?$/u.test(String(value))))problems.push('featureCounts: неверное количество предметов');
 if(!problems.length&&JSON.stringify(fields).length>16_000_000)problems.push('Поля объявления больше 16 МБ');
 return {valid:!problems.length,problems:[...new Set(problems)]};
}
export function assertListingFields(fields){const result=validateListingFields(fields);if(!result.valid)throw TypeError('Неверные поля объявления: '+result.problems.join(' · '));return fields;}
function factStoredValue(value,key){
 if(key!=='universityRoutes'||!Array.isArray(value))return value;
 const scalar=['name','university','campus','campusId','address','latitude','longitude','time','mode','transport','line','routeDistanceKm','method','checkedAt','sourceUrl','accessMinutes','egressMinutes','transfers'];
 const keep=(row,keys)=>Object.fromEntries(keys.filter(name=>row&&['string','number','boolean'].includes(typeof row[name])).map(name=>[name,typeof row[name]==='string'?row[name].slice(0,2000):row[name]]));
 return value.slice(0,10).filter(factIsObject).map(row=>({...keep(row,scalar),...(Array.isArray(row.stops)?{stops:row.stops.slice(0,3).filter(factIsObject).map(stop=>keep(stop,['name','address','latitude','longitude']))}:{}),...(Array.isArray(row.segments)?{segments:row.segments.slice(0,8).filter(factIsObject).map(segment=>keep(segment,['mode','transport','from','to','minutes']))}:{})}));
}
function factSnapshot(fields){assertListingFields(fields);return factClone(Object.fromEntries(FACT_TRACKED_FIELDS.filter(key=>fields[key]!==undefined).map(key=>[key,factStoredValue(fields[key],key)])));}
function factCanonical(value,key){
 if(factNumericFields.has(key)||factTextOrNumberFields.has(key)){const number=factNumericValue(value);if(number!==undefined)return number;}
 if(key==='features'&&Array.isArray(value))return [...new Set(value)].sort();
 if(key==='featureCounts'&&factIsObject(value))return Object.fromEntries(Object.keys(value).sort().map(name=>[name,Number(value[name])]));
 return value;
}
function factNumericValue(value){if(typeof value==='number')return value;if(typeof value==='string'){const literal=value.trim();if(/^-?(?:\d+|\d{1,3}(?:\s+\d{3})+)(?:[.,]\d+)?$/u.test(literal))return Number(literal.replace(/\s+/gu,'').replace(',','.'));}return undefined;}
function factEqual(left,right,key){return JSON.stringify(factCanonical(left,key))===JSON.stringify(factCanonical(right,key));}
export function compareFactSnapshots(previous={},current={}){
 return FACT_TRACKED_FIELDS.filter(key=>!factEqual(previous[key],current[key],key)).map(field=>({field,before:previous[field]??null,after:current[field]??null}));
}
function factTimestamp(value){if(typeof value!=='string'||!/^\d{4}-\d\d-\d\dT/u.test(value)||!/(?:Z|[+-]\d\d:\d\d)$/u.test(value)||!Number.isFinite(Date.parse(value)))throw Error('Неверная дата версии фактов');return value;}
function factSourceUrl(value){try{const url=new URL(value);return ['http:','https:'].includes(url.protocol)&&!url.username&&!url.password?url.origin+url.pathname:'';}catch{return '';}}
export function factLedgerKey(fields={}){
 const url=factSourceUrl(fields.url);if(url)return 'url:'+url.replace(/\/+$/u,'');
 for(const key of ['resultId','editorIdentity','id'])if(typeof fields[key]==='string'&&/^[a-zA-Z0-9_-]{1,100}$/u.test(fields[key]))return key+':'+fields[key];
 return '';
}
export function sourceFactEvidence(fields={},options={}){
 const rows=Array.isArray(fields.sourceAudit?.fields)?fields.sourceAudit.fields:[],confirmed=factIsObject(options.confirmed)?options.confirmed:{},output=[];
 const observedAt=options.observedAt||fields.sourceObservedAt||null;
 for(const key of FACT_TRACKED_FIELDS){
  if(fields[key]===undefined)continue;
  const witnesses=rows.filter(row=>row&&row.field===key),matching=witnesses.find(row=>factEqual(row.value,fields[key],key)&&rentalWitnessComparable(row,{currency:fields.priceCurrency,period:fields.pricePeriod},key));
  const confirmation=confirmed[key]&&factEqual(confirmed[key].value,fields[key],key)?confirmed[key]:null;
  output.push({field:key,value:factClone(factStoredValue(fields[key],key)),origin:matching?'source':'manual',source:matching?String(matching.source||'Источник').slice(0,200):'Ручной ввод',sourceUrl:matching?factSourceUrl(matching.sourceUrl||fields.url):'',
   text:matching?String(matching.text??'').slice(0,700):'',observedAt:typeof observedAt==='string'?observedAt:null,checkedAt:confirmation?.at||null,checkedBy:confirmation?.actor||null});
 }
 for(const row of Array.isArray(fields.sourceAudit?.furniture)?fields.sourceAudit.furniture:[]){
  if(!row||typeof row.id!=='string')continue;
  const present=Array.isArray(fields.features)&&fields.features.includes(row.id),value=present;
  const witness=(Array.isArray(row.matches)?row.matches:[]).find(item=>item&&typeof item.text==='string'),field='features.'+row.id;
  const confirmation=confirmed[field]&&confirmed[field].value===value?confirmed[field]:null;
  const matched=witness&&(row.status==='present'&&present||row.status==='absent'&&!present);
  output.push({field,label:String(row.label||row.id).slice(0,150),value,origin:matched?'source':'manual',source:matched?String(witness.source||'Источник').slice(0,200):'Ручная/фото отметка',sourceUrl:matched?factSourceUrl(fields.url):'',text:witness?String(witness.text).slice(0,700):'',observedAt:typeof observedAt==='string'?observedAt:null,checkedAt:confirmation?.at||null,checkedBy:confirmation?.actor||null});
 }
 return output;
}
export function findFactConflicts(fields={}){
 const conflicts=[],rows=Array.isArray(fields.sourceAudit?.fields)?fields.sourceAudit.fields:[];
 const add=row=>{const key=JSON.stringify([row.field,row.kind,row.values]);if(!conflicts.some(value=>value.signature===key))conflicts.push({...row,signature:key});};
 for(const key of FACT_TRACKED_FIELDS){
  const witnesses=rows.filter(row=>row&&row.field===key&&row.value!==undefined),values=[];
  for(const row of witnesses)if(!values.some(value=>factEqual(value.value,row.value,key)&&value.currency===row.currency&&value.period===row.period))values.push({value:factClone(row.value),source:String(row.source||'Источник').slice(0,200),text:String(row.text??'').slice(0,700),...(row.currency?{currency:row.currency}:{}),...(row.period?{period:row.period}:{})});
  const disagreements=values.filter(row=>values.some(other=>rentalWitnessComparable(row,other,key)&&!factEqual(row.value,other.value,key)));
  if(disagreements.length>1)add({field:key,kind:'sources',message:'Источники расходятся',values:disagreements});
  const comparable=values.filter(row=>rentalWitnessComparable(row,{currency:fields.priceCurrency,period:fields.pricePeriod},key));
  if(fields[key]!==undefined&&fields[key]!==''&&fields[key]!==null&&comparable.length&&!comparable.some(value=>factEqual(value.value,fields[key],key)))add({field:key,kind:'manual',message:'Ручное значение отличается от источника',values:[...comparable,{value:factClone(fields[key]),source:'Текущее поле',text:''}]});
 }
 for(const conflict of Array.isArray(fields.sourceAudit?.conflicts)?fields.sourceAudit.conflicts:[]){
  if(conflict&&typeof conflict.field==='string')add({field:conflict.field,kind:'source-audit',message:String(conflict.message||conflict.reason||'Противоречие источника').slice(0,500),values:[]});
 }
 for(const row of Array.isArray(fields.sourceAudit?.furniture)?fields.sourceAudit.furniture:[]){
  if(!row||typeof row.id!=='string')continue;
  if(row.status==='conflict'||['absent','planned'].includes(row.status)&&fields.features?.includes(row.id))add({field:'features.'+row.id,kind:'equipment',message:(row.label||row.id)+': отметка мебели расходится с исходными свидетельствами',values:(row.matches||[]).slice(0,20).map(item=>({value:row.status,source:String(item.source||'Источник'),text:String(item.text||'').slice(0,700)}))});
 }
 return conflicts.map(({signature,...row})=>row);
}
export function createFactRevision(fields,options={}){
 const key=factLedgerKey(fields);if(!key)throw Error('Сначала создайте карточку или укажите ссылку источника');
 const actor=typeof options.actor==='string'?options.actor.trim():'';if(!actor||actor.length>100)throw Error('Укажите автора версии до 100 знаков');
 const kind=options.kind||'manual';if(!['save','import','manual','confirm','restore'].includes(kind))throw Error('Неверный тип версии фактов');
 const at=factTimestamp(options.at||new Date().toISOString()),snapshot=factSnapshot(fields),previous=options.previous?.fields||{},changes=compareFactSnapshots(previous,snapshot);
 const version=Math.max(0,Number(options.previous?.revision)||0)+1;
 const changed=new Set(changes.map(row=>row.field));if(options.confirmedField)changed.add(options.confirmedField);
 return {version:1,id:key+':'+version,key,revision:version,kind,actor,at,fields:snapshot,changes,
  evidence:sourceFactEvidence(fields,{observedAt:kind==='import'?at:options.observedAt,confirmed:options.confirmed}).filter(row=>changed.has(row.field))};
}
export function serviceCardKey(fields={}){if(typeof fields.url==='string'&&factSourceUrl(fields.url))return factLedgerKey(fields);for(const key of ['editorIdentity','resultId','id'])if(typeof fields[key]==='string'&&/^[a-zA-Z0-9_-]{1,100}$/u.test(fields[key]))return key+':'+fields[key];return '';}
export function serviceClaimChecklist(fields={},options={}){
 const positive=value=>['number','string'].includes(typeof value)&&Number.isFinite(Number(value))&&Number(value)>0?String(Number(value)):'';
 const prefs=options.prefs||{},receipts=factIsObject(options.receipts)?options.receipts:{},now=options.now===undefined?Date.now():Date.parse(options.now),claims=[{id:'registration',label:'Содействие регистрации по прибытии',value:{service:'arrival-registration-assistance',months:positive(fields.registrationMonths),people:positive(fields.registrationPeople)}}];
 if(fields.pickup===true)claims.push({id:'pickup',label:'Бесплатная встреча и доставка до заселения',value:{included:true,luggage:fields.luggage===true}});
 if(fields.arrival===true)claims.push({id:'arrival',label:'Бесплатная помощь с процедурами по прибытии',value:{included:true}});
 if(fields.support===true)claims.push({id:'support',label:'Поддержка и преимущества студии',value:{included:true,text:typeof prefs.benefits==='string'?prefs.benefits.slice(0,5000):''}});
 const key=serviceCardKey(fields),rows=claims.map(claim=>{const receipt=receipts[claim.id],stamp=typeof receipt?.at==='string'&&/(?:Z|[+-]\d\d:\d\d)$/u.test(receipt.at)?Date.parse(receipt.at):NaN,actor=typeof receipt?.actor==='string'?receipt.actor.trim():'',ageDays=Number.isFinite(stamp)&&Number.isFinite(now)?(now-stamp)/86400000:null;
  const signature=value=>factIsObject(value)?JSON.stringify(Object.fromEntries(Object.keys(value).sort().map(name=>[name,value[name]]))):null;const same=!!receipt&&signature(receipt.value)===signature(claim.value),card=!!key&&receipt?.cardKey===key,confirmed=same&&card&&!!actor&&actor.length<=100&&ageDays!==null&&ageDays>=0&&ageDays<=30;
  return {...claim,confirmed,checkedAt:confirmed?receipt.at:null,checkedBy:confirmed?actor:null,reason:!receipt?'Нет подтверждения':!card?'Подтверждение относится к другой карточке':!same?'Объём услуги изменился':ageDays===null?'Неверная дата':ageDays<0?'Дата находится в будущем':ageDays>30?'Подтверждение старше 30 дней':'Не указан автор'};
 });
 const missing=rows.filter(row=>!row.confirmed);return {ready:!missing.length,claims:rows,missing:missing.map(row=>({id:row.id,label:row.label,reason:row.reason}))};
}
export function setupFactLedger({mount,read,write,getFields,getPreferences=()=>({}),onServiceReceiptsChange,onApplyField,clearServerCache,getServerRevisions,toast=()=>{}}={}){
 if(!mount)return null;
 const make=(tag,text,attributes={})=>{const node=document.createElement(tag);if(text!==undefined)node.textContent=text;Object.assign(node,attributes);return node;};
 let storage=read(factLedgerStorageKey,{version:1,entries:{},confirmations:{}}),remote=[],remoteEpoch=0,lastKey='',lastServiceReceipts={};
 if(!factIsObject(storage)||!factIsObject(storage.entries)||!factIsObject(storage.confirmations))storage={version:1,entries:{},confirmations:{}};
 const shell=make('details'),summary=make('summary','Факты: версии, источники и противоречия'),body=make('div',undefined,{className:'fact-ledger-body'});shell.append(summary,body);mount.classList.add('fact-ledger');mount.replaceChildren(shell);
 const actorLabel=make('label','Автор изменения'),actor=make('input',undefined,{value:read('most-fact-author','Оператор Mac'),maxLength:100,type:'text'});actor.setAttribute('aria-label','Автор изменения');actorLabel.append(actor);body.append(actorLabel);
 actor.onchange=()=>write('most-fact-author',actor.value.trim());
 const status=make('p','Снимки цен и условий не содержат исходный HTML или фотографии.',{className:'small'});status.setAttribute('role','status');body.append(status);
 const toolbar=make('div',undefined,{className:'fact-ledger-actions'});body.append(toolbar);
 const action=(parent,title,callback)=>{const button=make('button',title,{type:'button',className:'btn'});button.onclick=callback;parent.append(button);return button;};
 const versions=make('select');versions.setAttribute('aria-label','Версия фактов для сравнения');body.append(versions);
 const differences=make('div',undefined,{className:'fact-ledger-diff'}),servicesBox=make('div',undefined,{className:'fact-service-checklist'}),conflictsBox=make('div'),evidenceBox=make('div');body.append(differences,servicesBox,conflictsBox,evidenceBox);
 const show=value=>value===undefined||value===null||value===''?'Не указано':typeof value==='object'?JSON.stringify(value):String(value);
 function notify(message){status.textContent=message;toast(message);}
 function rows(){const fields=getFields(),key=factLedgerKey(fields);const local=Array.isArray(storage.entries[key])?storage.entries[key]:[];return [...local,...remote.filter(row=>row.resultId===fields.resultId&&!local.some(item=>item.at===row.at&&JSON.stringify(item.fields)===JSON.stringify(row.fields)))].sort((a,b)=>String(b.at).localeCompare(String(a.at))).slice(0,100);}
 function drawDifference(){differences.replaceChildren();const previous=rows().find(row=>row.id===versions.value);if(!previous)return;const changes=compareFactSnapshots(previous.fields,factSnapshot(getFields()));if(!changes.length){differences.textContent='Цена и условия совпадают с выбранным снимком.';return;}const list=make('dl');for(const row of changes){list.append(make('dt',factLabels[row.field]||row.field),make('dd',show(row.before)+' → '+show(row.after)));}differences.append(list);}
 function render(){
  const fields=getFields(),key=factLedgerKey(fields),entries=rows(),selectedVersion=versions.value;lastKey=key;lastServiceReceipts=storage.serviceConfirmations?.[key]||fields.serviceConfirmations||{};versions.replaceChildren();for(const row of entries)versions.append(make('option',new Date(row.at).toLocaleString('ru-RU')+' · '+row.actor+' · '+row.kind,{value:row.id}));if(entries.some(row=>row.id===selectedVersion))versions.value=selectedVersion;drawDifference();
  servicesBox.replaceChildren(make('h3','Услуги перед публикацией'),make('p','Это подтверждение услуг нашей студии. Отказ собственника в регистрации не считается подтверждением или опровержением нашей помощи. Даты сохраняются по каждой услуге; объём и срок действия проверяются заново при изменении.',{className:'small'}));
  const serviceState=checkServices();
  for(const claim of serviceState.claims){const label=make('label'),check=make('input',undefined,{type:'checkbox',checked:claim.confirmed});check.setAttribute('aria-label','Подтвердить услугу: '+claim.label);label.append(check,document.createTextNode(claim.label+' · '+(claim.confirmed?new Date(claim.checkedAt).toLocaleString('ru-RU')+' · '+claim.checkedBy:claim.reason)));check.onchange=()=>confirmService(claim.id,claim.value,check.checked,key);servicesBox.append(label);}
  conflictsBox.replaceChildren(make('h3','Противоречия'));
  const conflicts=findFactConflicts(fields);if(!conflicts.length)conflictsBox.append(make('p','Среди доступных структурированных свидетельств расхождения не найдены. Это не проверка всей исходной страницы.',{className:'small'}));
  for(const row of conflicts){const article=make('article',undefined,{className:'fact-ledger-conflict'});article.append(make('strong',(factLabels[row.field]||row.field)+' · '+row.message));for(const value of row.values)article.append(make('p',value.source+': '+show(value.value)+(value.text?' · '+value.text:'')));conflictsBox.append(article);}
  evidenceBox.replaceChildren(make('h3','Источники и дата проверки'));
  const evidence=sourceFactEvidence(fields,{confirmed:storage.confirmations[key]||{},observedAt:storage.observations?.[key]||entries.find(row=>row.kind==='import')?.at});
  const table=make('table'),thead=make('thead'),tr=make('tr');for(const title of ['Факт','Текущее значение','Источник','Проверка'])tr.append(make('th',title));thead.append(tr);table.append(thead);const tbody=make('tbody');table.append(tbody);
  for(const row of evidence){const tr=make('tr'),value=make('td',show(row.value)),source=make('td'),checked=make('td');tr.append(make('th',row.label||factLabels[row.field]||row.field),value,source,checked);source.append(make('span',row.source));if(row.sourceUrl){const link=make('a','Открыть источник',{href:row.sourceUrl,target:'_blank',rel:'noopener noreferrer'});source.append(link);}if(row.text)source.append(make('p',row.text,{className:'small'}));checked.append(make('p',row.checkedAt?new Date(row.checkedAt).toLocaleString('ru-RU')+' · '+row.checkedBy:'Не подтверждено оператором',{className:'small'}));action(checked,'Подтвердить текущий факт',()=>{if(factLedgerKey(getFields())!==key)return notify('Карточка изменилась. Обновите источники фактов.');confirmField(row.field,row.value);});tbody.append(tr);}
  const scroll=make('div',undefined,{className:'fact-ledger-table'});scroll.append(table);evidenceBox.append(scroll);
  if(onApplyField&&conflicts.some(row=>row.kind==='manual')){
   const choose=make('div');choose.append(make('p','Значение источника принимается только явным действием. Ручной ввод автоматически не перезаписывается.',{className:'small'}));
   for(const row of conflicts.filter(item=>item.kind==='manual'))for(const witness of row.values.filter(item=>item.source!=='Текущее поле'&&['string','number','boolean'].includes(typeof item.value)))action(choose,'Принять '+(factLabels[row.field]||row.field)+': '+show(witness.value),async()=>{try{if(factLedgerKey(getFields())!==key)throw Error('Карточка изменилась. Обновите источники фактов.');await onApplyField(row.field,factClone(witness.value));if(factLedgerKey(getFields())!==key)throw Error('Карточка изменилась. Повторите проверку фактов.');observe('restore');}catch(error){notify(error.message);}});
   conflictsBox.append(choose);
  }
 }
 function persist(){return write(factLedgerStorageKey,storage)!==false;}
 function serviceReceipts(){return factClone(lastServiceReceipts);}
 function checkServices(){const fields=getFields(),key=factLedgerKey(fields),receipts=storage.serviceConfirmations?.[key]||fields.serviceConfirmations||{};return serviceClaimChecklist(fields,{prefs:getPreferences(),receipts});}
 function confirmService(id,expectedValue,checked,key){try{if(factLedgerKey(getFields())!==key)throw Error('Карточка изменилась. Обновите перечень услуг.');const claim=checkServices().claims.find(row=>row.id===id);if(!claim||JSON.stringify(claim.value)!==JSON.stringify(expectedValue))throw Error('Объём услуги изменился. Проверьте актуальные условия.');const who=actor.value.trim();if(!who||who.length>100)throw Error('Укажите автора подтверждения');const values={...(storage.serviceConfirmations?.[key]||getFields().serviceConfirmations||{})};if(checked)values[id]={value:factClone(claim.value),actor:who,at:new Date().toISOString(),cardKey:serviceCardKey(getFields())};else delete values[id];storage.serviceConfirmations={...(storage.serviceConfirmations||{}),[key]:values};if(!persist())return;lastKey=key;onServiceReceiptsChange?.(factClone(values));render();notify(checked?'Услуга подтверждена с датой и автором':'Подтверждение услуги снято');}catch(error){render();notify(error.message);}}
 function observe(kind='manual',who=actor.value,confirmedField){
  try{const fields=getFields(),key=factLedgerKey(fields),previous=storage.entries[key]?.[0],revision=createFactRevision(fields,{kind,actor:who,previous,confirmed:storage.confirmations[key]||{},confirmedField,observedAt:storage.observations?.[key]});if(kind==='import')storage.observations={...(storage.observations||{}),[key]:revision.at};if(previous&&!revision.changes.length&&kind!=='confirm'){if(kind==='import')persist();render();return previous;}
   const entries=Object.fromEntries(Object.entries(storage.entries).filter(([name])=>name!==key).slice(-39));entries[key]=[revision,...(storage.entries[key]||[])].slice(0,100);storage={...storage,entries};if(!persist())return null;render();return revision;
  }catch(error){notify(error.message);return null;}
 }
 function confirmField(field,expectedValue){try{const fields=getFields(),key=factLedgerKey(fields),evidence=sourceFactEvidence(fields).find(row=>row.field===field);if(!key||!evidence)throw Error('Факт недоступен');if(arguments.length>1&&!factEqual(expectedValue,evidence.value,field)){render();throw Error('Факт изменился. Проверьте актуальное значение перед подтверждением.');}const who=actor.value.trim();if(!who||who.length>100)throw Error('Укажите автора проверки');storage.confirmations[key]={...(storage.confirmations[key]||{}),[field]:{value:evidence.value,actor:who,at:new Date().toISOString()}};if(!persist())return;observe('confirm',who,field);notify('Текущий факт подтверждён. После изменения значения подтверждение потребуется заново.');}catch(error){notify(error.message);}}
 function refresh(){const epoch=++remoteEpoch,id=getFields().resultId;remote=[];render();if(getServerRevisions&&id)Promise.resolve(getServerRevisions(id)).then(value=>{if(epoch!==remoteEpoch)return;remote=Array.isArray(value?.revisions)?value.revisions:Array.isArray(value)?value:[];render();}).catch(error=>{if(epoch===remoteEpoch)status.textContent='Локальные версии доступны. Серверная история: '+error.message;});}
 versions.onchange=drawDifference;action(toolbar,'Зафиксировать текущие факты',()=>{if(observe('manual'))notify('Снимок цены и условий сохранён с автором');});action(toolbar,'Обновить версии и сравнение',refresh);
 const cache=make('details'),cacheTitle=make('summary','Очистить только кеш импорта'),cacheNote=make('p','Карточки, словарь, история, настройки и фотографии сохраняются. Следующий импорт перечитает исходную страницу.',{className:'small'}),cacheLabel=make('label'),cacheCheck=make('input',undefined,{type:'checkbox'});cacheCheck.setAttribute('aria-label','Подтверждаю очистку только кеша импорта');cacheLabel.append(cacheCheck,document.createTextNode('Подтверждаю очистку только кеша импорта'));cache.append(cacheTitle,cacheNote,cacheLabel);body.append(cache);
 action(cache,'Очистить кеш этого браузера',()=>{if(!cacheCheck.checked)return notify('Подтвердите очистку кеша');if(write('most-cache',{})!==false)notify('Кеш исходных страниц браузера очищен. Карточки и словарь сохранены.');});
 if(clearServerCache)action(cache,'Очистить кеш исходных страниц на Mac',async()=>{if(!cacheCheck.checked)return notify('Подтвердите очистку кеша');try{const result=await clearServerCache();notify('Кеш Mac очищен: '+(result.cleared||0)+' записей. Карточки, словарь и фото сохранены.');}catch(error){notify(error.message);}});
 render();return {observe,refresh,confirmField,checkServices,serviceReceipts,open:()=>{shell.open=true;servicesBox.scrollIntoView?.({block:'nearest',behavior:'smooth'});},history:()=>factClone(rows()),validate:()=>validateListingFields(getFields())};
}
