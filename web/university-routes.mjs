import {UNIVERSITY_CATALOG,resolveUniversity,resolveCampus,verifiedCampusPoint,universityCampusAvailability} from './university-catalog.mjs';

// Map links ask the provider for a current route. They are not a local routing
// result and never establish bus numbers, travelling time, or road distance.
export const UNIVERSITY_ROUTE_LIMIT=10;
export const UNIVERSITY_ROUTE_STOP_LIMIT=3;
export const UNIVERSITY_ROUTE_SEGMENT_LIMIT=8;
export const UNIVERSITY_ROUTE_METHODS=Object.freeze(['unknown','manual-confirmed','provider','estimate']);
const universityRouteModes={unknown:'出行方式待确认',public:'公共交通',metro:'地铁',bus:'公交',walk:'步行',car:'驾车',bike:'骑行'};
const universityRouteText=(value,max=240)=>typeof value==='string'?value.trim().slice(0,max):'';
const universityRouteNumber=value=>{
  if(typeof value==='number')return Number.isFinite(value)?value:null;
  if(typeof value!=='string'||!value.trim()||!/^[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[-+]?\d+)?$/i.test(value.trim()))return null;
  const number=Number(value);return Number.isFinite(number)?number:null;
};
const universityRouteClone=value=>JSON.parse(JSON.stringify(value));
const universityRouteCampusById=new Map(UNIVERSITY_CATALOG.campuses.map(row=>[row.id,row]));
const universityRouteSchoolById=new Map(UNIVERSITY_CATALOG.universities.map(row=>[row.id,row]));
function universityRouteMode(value){const aliases={'步行':'walk','乘坐地铁':'metro','地铁':'metro','乘坐公交':'bus','公交':'bus','公共交通':'public','驾车':'car','骑行':'bike'};const mode=aliases[value]||value;return Object.hasOwn(universityRouteModes,mode)?mode:'unknown';}
function universityRouteInteger(value,maximum){const n=universityRouteNumber(value);return n!==null&&Number.isInteger(n)&&n>=0&&n<=maximum?n:'';}
function universityRouteEvidenceUrl(value){const text=universityRouteText(value,2048);try{const url=new URL(text);return url.protocol==='https:'&&url.hostname&&!url.username&&!url.password&&['', '443'].includes(url.port)?text:'';}catch{return '';}}
function universityRouteDate(value){const text=universityRouteText(value,10);if(!/^\d{4}-\d{2}-\d{2}$/.test(text))return '';const stamp=Date.parse(text+'T00:00:00Z');return Number.isFinite(stamp)&&new Date(stamp).toISOString().slice(0,10)===text?text:'';}
function universityRouteMoscowDay(value){if(!Number.isFinite(value))return '';const parts=new Intl.DateTimeFormat('en-CA',{timeZone:'Europe/Moscow',year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(new Date(value));return ['year','month','day'].map(key=>parts.find(part=>part.type===key)?.value||'').join('-');}
function universityRouteAddressKey(value){return universityRouteText(value,500).toLocaleLowerCase('ru').replace(/ё/g,'е').replace(/[^\p{L}\d]/gu,'');}

export function universityRouteVerification(row={},options={}){
  const method=UNIVERSITY_ROUTE_METHODS.includes(row.method)?row.method:'unknown',checkedAt=universityRouteDate(row.checkedAt),sourceUrl=universityRouteEvidenceUrl(row.sourceUrl);
  const now=options.now===undefined?Date.now():typeof options.now==='string'?Date.parse(options.now):Number(options.now),nowDay=universityRouteMoscowDay(now),stamp=checkedAt?Date.parse(checkedAt+'T00:00:00Z'):NaN;
  const ageDays=Number.isFinite(stamp)&&nowDay?Math.floor((Date.parse(nowDay+'T00:00:00Z')-stamp)/86400000):null,maxAgeDays=Number.isFinite(options.maxAgeDays)&&options.maxAgeDays>=0?options.maxAgeDays:30;
  const future=ageDays!==null&&ageDays<0,calculation=universityRouteCalculation(row.calculation),expired=!!calculation&&Date.parse(calculation.expiresAt)<=now,stale=ageDays!==null&&ageDays>maxAgeDays||expired,estimated=method==='estimate'||!!calculation;
  const issues=[];if(method==='unknown')issues.push('Способ проверки маршрута не указан.');if(!checkedAt)issues.push('Дата проверки маршрута не указана.');if(!nowDay)issues.push('Текущая дата недоступна: актуальность маршрута не подтверждена.');if(future)issues.push('Дата проверки маршрута находится в будущем.');if(expired)issues.push('Срок маршрутного расчёта истёк: повторите запрос сервиса.');else if(stale)issues.push('Маршрут старше '+maxAgeDays+' дней: требуется повторная проверка.');if(calculation)issues.push('Время сервиса — оценка по дорожной сети, а не измеренная поездка и не расписание транспорта.');if(method==='provider'&&!sourceUrl)issues.push('Для результата сервиса маршрутов нужен URL источника.');
  const names={unknown:'核验方式待确认','manual-confirmed':'人工核验',provider:'路线服务资料',estimate:'估算'},ru={unknown:'Способ не указан','manual-confirmed':'Ручная проверка',provider:'Данные маршрутного сервиса',estimate:'Оценка'};
  return {method,checkedAt,sourceUrl,ageDays,estimated,stale,future,verified:['manual-confirmed','provider'].includes(method)&&!!checkedAt&&!!nowDay&&!future&&!stale&&(method!=='provider'||!!sourceUrl),issues,
    label:names[method]+(checkedAt?' · '+checkedAt:' · 日期待确认')+(stale?' · 需重新核验':''),summary:ru[method]+(checkedAt?' · '+checkedAt:' · дата не указана')+(stale?' · устарел':'')};
}

export function universityRouteCoordinates(latitude,longitude){
  const lat=universityRouteNumber(latitude),lon=universityRouteNumber(longitude);
  return lat!==null&&lon!==null&&Math.abs(lat)<=90&&Math.abs(lon)<=180?{latitude:lat,longitude:lon}:null;
}

export function universityStraightLineKm(origin,destination){
  const a=universityRouteCoordinates(origin?.latitude,origin?.longitude),b=universityRouteCoordinates(destination?.latitude,destination?.longitude);
  if(!a||!b)return null;
  const radians=value=>value*Math.PI/180,deltaLat=radians(b.latitude-a.latitude),deltaLon=radians(b.longitude-a.longitude);
  const haversine=Math.sin(deltaLat/2)**2+Math.cos(radians(a.latitude))*Math.cos(radians(b.latitude))*Math.sin(deltaLon/2)**2;
  return 6371.0088*2*Math.asin(Math.sqrt(Math.max(0,Math.min(1,haversine))));
}

function universityRouteCoordinateText(point){return point.latitude.toFixed(6).replace(/\.?0+$/,'')+','+point.longitude.toFixed(6).replace(/\.?0+$/,'');}
function universityRouteUrl(base,parameters){const value=new URL(base);for(const [name,text]of Object.entries(parameters))if(text!==''&&text!=null)value.searchParams.set(name,String(text));const result=value.href;return result.length<=2048?result:'';}
function universityRouteQuery(value){
  // A pasted contact number is not an address and must not become a hidden
  // phone number inside an exported map hyperlink. Stored input is unchanged.
  return universityRouteText(value,500).replace(/\+\d(?:[\s().-]*\d){9,14}/g,'').replace(/(?:^|[^\d])8(?:[\s().-]*\d){10}(?!\d)/g,match=>/^8/.test(match)?'':match[0]).replace(/\s+/g,' ').trim();
}

export function universityStopLinks(stop={},city='Москва'){
  const coordinates=universityRouteCoordinates(stop.latitude,stop.longitude),raw=universityRouteQuery(stop.address||stop.name),place=universityRouteText(city),fold=value=>value.toLocaleLowerCase('ru'),alreadyPrefixed=place&&(fold(raw)===fold(place)||fold(raw).startsWith(fold(place)+',')||fold(raw).startsWith(fold(place)+' '));
  const query=coordinates?universityRouteCoordinateText(coordinates):alreadyPrefixed?raw:[place,raw].filter(Boolean).join(', ');
  if(!query||!coordinates&&!universityRouteText(stop.address||stop.name))return {yandex:'',google:'',osm:''};
  return {
    yandex:coordinates?universityRouteUrl('https://yandex.ru/maps/',{pt:coordinates.longitude+','+coordinates.latitude,z:17}):universityRouteUrl('https://yandex.ru/maps/',{text:query}),
    google:universityRouteUrl('https://www.google.com/maps/search/',{api:1,query}),
    osm:coordinates?universityRouteUrl('https://www.openstreetmap.org/',{mlat:coordinates.latitude,mlon:coordinates.longitude})+'#map=17/'+coordinates.latitude+'/'+coordinates.longitude:universityRouteUrl('https://www.openstreetmap.org/search',{query})
  };
}

export function universityMapLinks(origin={},destination={},mode='public',{city='Москва',confirmedDestination=true}={}){
  const from=universityRouteCoordinates(origin.latitude,origin.longitude),to=universityRouteCoordinates(destination.latitude,destination.longitude);
  const originQuery=from?universityRouteCoordinateText(from):universityRouteQuery(origin.address);
  const destinationQuery=to?universityRouteCoordinateText(to):universityRouteQuery(destination.address||destination.name);
  const stopLinks=universityStopLinks({...destination,name:destinationQuery},city);
  const links={...stopLinks,yandexKind:'search',googleKind:'search',osmKind:'position'};
  if(!confirmedDestination||!originQuery||!destinationQuery)return links;
  const normalMode=universityRouteMode(mode),googleMode=normalMode==='walk'?'walking':normalMode==='car'?'driving':normalMode==='bike'?'bicycling':'transit';
  links.google=universityRouteUrl('https://www.google.com/maps/dir/',{api:1,origin:originQuery,destination:destinationQuery,travelmode:googleMode});links.googleKind='route';
  // Yandex documents routing coordinates as latitude,longitude, while pt is
  // longitude,latitude. Text-only destinations use an honest search link.
  if(from&&to){links.yandex=universityRouteUrl('https://yandex.ru/maps/',{rtext:universityRouteCoordinateText(from)+'~'+universityRouteCoordinateText(to),rtt:normalMode==='walk'?'pd':normalMode==='car'?'auto':normalMode==='bike'?'bc':'mt'});links.yandexKind='route';}
  return links;
}

function universityRoutePoint(value){
  if(!value||typeof value!=='object'||Array.isArray(value))return null;
  const coordinate=(n,bound)=>{const x=universityRouteNumber(n);return x!==null&&Math.abs(x)<=bound?x:'';};
  return {latitude:coordinate(value.latitude,90),longitude:coordinate(value.longitude,180),name:universityRouteText(value.name,160),kind:['point','building-entrance','metro-exit'].includes(value.kind)?value.kind:'point',availability:['available','closed','unknown'].includes(value.availability)?value.availability:'unknown',checkedAt:universityRouteDate(value.checkedAt),sourceUrl:universityRouteEvidenceUrl(value.sourceUrl).slice(0,1200)};
}
function universityRouteCalculation(value){
  if(!value||typeof value!=='object'||Array.isArray(value)||value.provider!=='valhalla-fossgis-demo'||value.travelTimeKind!=='network-estimate')return null;
  const start=Date.parse(value.calculatedAt),end=Date.parse(value.expiresAt),seconds=universityRouteNumber(value.durationSeconds),distance=universityRouteNumber(value.distanceKm);
  if(!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$/.test(value.calculatedAt)||!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$/.test(value.expiresAt)||!Number.isFinite(start)||!Number.isFinite(end)||end<=start||end-start>600000||seconds===null||seconds<0||seconds>172800||distance===null||distance<0||distance>2000||!/^[a-f0-9]{64}$/.test(value.responseSha256))return null;
  return {provider:value.provider,calculatedAt:value.calculatedAt,expiresAt:value.expiresAt,durationSeconds:seconds,distanceKm:distance,travelTimeKind:value.travelTimeKind,responseSha256:value.responseSha256};
}

export function normalizeUniversityRoutes(fields={}){
  const f=fields&&typeof fields==='object'?fields:{};let rows;
  // An explicit empty array means the operator removed all routes. Legacy
  // fields must not resurrect them after a save/reload.
  if(Array.isArray(f.universityRoutes))rows=f.universityRoutes;
  else{
    rows=universityRouteText(f.universities,6000).split('\n').map(line=>line.trim()).filter(Boolean).map(line=>{const [name,time='',mode='',transport='']=line.split('|').map(value=>value.trim());return {name,time,mode,transport};});
    if(rows.length&&universityRouteText(f.campus))rows[0].campus=f.campus;
    if(!rows.length&&universityRouteText(f.university))rows=[{name:f.university,campus:f.campus,time:f.uniTime,mode:f.uniMode,transport:f.route}];
  }
  return rows.slice(0,UNIVERSITY_ROUTE_LIMIT).filter(row=>row&&typeof row==='object'&&!Array.isArray(row)).map(row=>{
    const coordinate=(value,limit)=>{const number=universityRouteNumber(value);return number!==null&&Math.abs(number)<=limit?number:'';},distance=universityRouteNumber(row.routeDistanceKm),campusId=universityRouteText(row.campusId,100);
    const stops=(Array.isArray(row.stops)?row.stops:typeof row.stops==='string'?row.stops.split('\n').map(line=>{const [name,latitude='',longitude='']=line.split('|').map(value=>value.trim());return {name,latitude,longitude};}):[]).slice(0,UNIVERSITY_ROUTE_STOP_LIMIT).map(stop=>typeof stop==='string'?{name:stop}:stop).filter(stop=>stop&&typeof stop==='object'&&!Array.isArray(stop)).map(stop=>({name:universityRouteText(stop.name),latitude:coordinate(stop.latitude,90),longitude:coordinate(stop.longitude,180)})).filter(stop=>stop.name);
    const segments=(Array.isArray(row.segments)?row.segments:[]).slice(0,UNIVERSITY_ROUTE_SEGMENT_LIMIT).filter(segment=>segment&&typeof segment==='object'&&!Array.isArray(segment)).map(segment=>({mode:universityRouteMode(segment.mode),transport:universityRouteText(segment.transport,160),from:universityRouteText(segment.from,160),to:universityRouteText(segment.to,160),minutes:universityRouteInteger(segment.minutes,1440)})).filter(segment=>segment.transport||segment.from||segment.to||segment.minutes!=='');
    return {name:universityRouteText(row.name||row.university),campus:universityRouteText(row.campus),campusId:universityRouteCampusById.has(campusId)?campusId:'',address:universityRouteText(row.address,500),latitude:coordinate(row.latitude,90),longitude:coordinate(row.longitude,180),time:universityRouteText(row.time,80),mode:universityRouteMode(row.mode),transport:universityRouteText(row.transport||row.line,600),routeDistanceKm:distance!==null&&distance>=0&&distance<=20000?distance:'',stops,
      method:UNIVERSITY_ROUTE_METHODS.includes(row.method)?row.method:'unknown',checkedAt:universityRouteDate(row.checkedAt),sourceUrl:universityRouteEvidenceUrl(row.sourceUrl),accessMinutes:universityRouteInteger(row.accessMinutes,180),egressMinutes:universityRouteInteger(row.egressMinutes,180),transfers:universityRouteInteger(row.transfers,12),segments,originPoint:universityRoutePoint(row.originPoint),destinationPoint:universityRoutePoint(row.destinationPoint),useEntrances:row.useEntrances===true,calculation:universityRouteCalculation(row.calculation)};
  }).filter(row=>row.name||row.campus||row.campusId||row.address);
}

function universityRouteDistanceLabel(distance,kind){
  if(distance===null)return '';
  const length=distance<1?Math.round(distance*1000/10)*10+'米':Number(distance.toFixed(1))+'公里';
  return (kind==='straight-line'?'直线距离约':'路线距离约')+length+(kind==='provided-route'?'（按已填资料）':'');
}
function universityRouteTimeLabel(value,translate){
  if(!value)return {label:'通勤时间待确认',confirmed:false};
  const match=/^(\d{1,3})(?:\s*[-–—]\s*(\d{1,3}))?$/.exec(value);
  if(match){if(match[2]&&Number(match[2])<Number(match[1]))return {label:'通勤时间待确认',confirmed:false,invalid:true};return {label:'约'+match[1]+(match[2]?'–'+match[2]:'')+'分钟',confirmed:true};}
  return {label:translate(value),confirmed:true};
}

export function resolveUniversityRoutes(fields={},options={}){
  const f=fields&&typeof fields==='object'?fields:{},translate=typeof options.translate==='function'?options.translate:value=>String(value??''),city=universityRouteText(f.city)||'Москва',origin={...universityRouteCoordinates(f.latitude,f.longitude),address:universityRouteText(f.address,500)};
  return normalizeUniversityRoutes(f).map(row=>{
    const warnings=[],university=resolveUniversity(row.name),namedCampus=resolveCampus(row.campus||row.name),campus=universityRouteCampusById.get(row.campusId)||namedCampus,addressMismatch=!!(campus&&row.address&&campus.addressRu&&universityRouteAddressKey(row.address)!==universityRouteAddressKey(campus.addressRu)),mismatch=!!(university&&campus&&university.id!==campus.universityId||namedCampus&&campus&&namedCampus.id!==campus.id||addressMismatch);
    if(mismatch)warnings.push('Вуз и выбранный кампус не совпадают. Уточните место назначения.');
    if(addressMismatch)warnings.push('Адрес отличается от выбранного корпуса. Выберите правильный корпус либо явно задайте свой адрес без чужого campusId.');
    const campusBlocked=!!campus&&!universityCampusAvailability(campus).selectable;if(campusBlocked)warnings.push('Этот учебный адрес закрыт или прежний. Выберите актуальный корпус; прежние координаты не используются.');
    const selectedCampus=mismatch||campusBlocked?null:campus,parent=selectedCampus?universityRouteSchoolById.get(selectedCampus.universityId):null;
    // Only an explicitly resolved building may supply a catalogue point. A
    // university name alone must never silently choose its main/legal address.
    // A partial manually entered pair must not borrow its other half either.
    const hasManualPoint=row.latitude!==''||row.longitude!=='',manualPoint=universityRouteCoordinates(row.latitude,row.longitude),catalogEvidence=selectedCampus?.coordinateEvidence;
    const catalogueVerified=!hasManualPoint?verifiedCampusPoint(selectedCampus):null,catalogPoint=catalogueVerified?universityRouteCoordinates(catalogueVerified.latitude,catalogueVerified.longitude):null;
    const selectedDestination=row.destinationPoint?universityRouteCoordinates(row.destinationPoint.latitude,row.destinationPoint.longitude):null,selectedOrigin=row.originPoint?universityRouteCoordinates(row.originPoint.latitude,row.originPoint.longitude):null,routeOrigin=row.originPoint?{...selectedOrigin,address:selectedOrigin?row.originPoint.name:''}:origin,selectedPointIncomplete=!!row.originPoint&&!selectedOrigin||!!row.destinationPoint&&!selectedDestination;if(selectedPointIncomplete)warnings.push('Выбранная точка задана не полностью; адрес или центр вместо неё не подставляется.');
    const destinationPoint=mismatch||campusBlocked?null:row.destinationPoint?selectedDestination:manualPoint||catalogPoint,destinationAddress=mismatch||campusBlocked||row.destinationPoint&&!selectedDestination?'':row.address||selectedCampus?.addressRu||'',destinationPointSource=destinationPoint?(row.destinationPoint?'selected-point':manualPoint?'manual':'catalogue'):'';
    const destinationPointEvidence=destinationPointSource==='catalogue'?universityRouteClone(catalogEvidence):null;
    if(destinationPointSource==='catalogue')warnings.push('Использована опубликованная точка выбранного корпуса; точность и доступный вход не подтверждены.');
    const rawTitle=row.name||(parent?.ru)||row.campus||'院校/校区',translatedTitle=translate(rawTitle),title=translatedTitle!==rawTitle?translatedTitle:university?.zh||parent?.zh||selectedCampus?.zh||translatedTitle;
    const rawCampus=row.campus||selectedCampus?.ru||'',translatedCampus=translate(rawCampus),campusTitle=translatedCampus!==rawCampus?translatedCampus:selectedCampus?.zh||translatedCampus,destination={name:selectedCampus?.ru||row.campus||row.name,address:destinationAddress,...destinationPoint};
    const confirmedDestination=!!(destinationPoint||destinationAddress),time=universityRouteTimeLabel(row.time,translate);
    if(time.invalid)warnings.push('Проверьте диапазон времени поездки: начало больше конца.');
    const computed=confirmedDestination?universityStraightLineKm(routeOrigin,destination):null,provided=mismatch||campusBlocked||selectedPointIncomplete?null:universityRouteNumber(row.routeDistanceKm),distanceKind=provided!==null?'provided-route':computed!==null?'straight-line':'unknown',distanceKm=provided??computed;
    const modeLabel=universityRouteModes[row.mode],transport=translate(row.transport),links=mismatch||campusBlocked||selectedPointIncomplete?{yandex:'',google:'',osm:'',yandexKind:'search',googleKind:'search',osmKind:'position'}:universityMapLinks(routeOrigin,destination,row.mode,{city,confirmedDestination});
    const stopDetails=row.stops.length?row.stops.map(stop=>({name:stop.name,label:translate(stop.name),latitude:stop.latitude,longitude:stop.longitude,links:universityStopLinks(stop,city)})):[
      {name:universityRouteText(f.metro)||'住处',label:translate(universityRouteText(f.metro))||'住处',links:universityRouteText(f.metro)?universityStopLinks({name:'метро '+f.metro},city):universityStopLinks(routeOrigin,city)},
      {name:row.transport,label:transport||modeLabel,links:{yandex:'',google:'',osm:''}},
      {name:destination.name,label:campusTitle||title,links:{yandex:links.yandexKind==='search'?links.yandex:universityStopLinks(destination,city).yandex,google:universityStopLinks(destination,city).google,osm:links.osm}}
    ];
    const verification=universityRouteVerification(row,options);
    if(row.time||row.routeDistanceKm!==''||row.segments.length)warnings.push(...verification.issues);
    const segmentLabels=row.segments.map(segment=>[universityRouteModes[segment.mode],segment.transport,segment.from&&segment.to?segment.from+' → '+segment.to:segment.from||segment.to,segment.minutes!==''?segment.minutes+'分钟':''].filter(Boolean).join(' · '));
    const partsLabel=[row.accessMinutes!==''?'到站步行'+row.accessMinutes+'分钟':'',row.egressMinutes!==''?'下车后步行'+row.egressMinutes+'分钟':'',row.transfers!==''?'换乘'+row.transfers+'次':''].filter(Boolean).join(' · ');
    const timeLabel=verification.estimated&&time.confirmed?'预估'+time.label:time.label;
    const nowStamp=options.now===undefined?Date.now():typeof options.now==='string'?Date.parse(options.now):Number(options.now),calculatedAt=universityRouteMoscowDay(nowStamp);
    const distanceMethod=distanceKind==='straight-line'?'haversine':distanceKind==='provided-route'?'provided':'unknown',distanceCheckedAt=distanceMethod==='haversine'?calculatedAt:verification.checkedAt;
    const distanceEvidenceLabel=distanceMethod==='haversine'?'直线估算 · Haversine公式 · 计算日期'+(distanceCheckedAt||'待确认')+'（非实际路线）':distanceMethod==='provided'?'路线距离（已填资料）· 资料日期'+(distanceCheckedAt||'待确认'):'';
    return {...row,title,campusTitle,origin:routeOrigin,destination,destinationPointSource,destinationPointEvidence,selectedCampusId:selectedCampus?.id||'',confirmedDestination,time:timeLabel,timeLabel,confirmedTime:time.confirmed&&!verification.estimated,modeLabel,transport,transportLabel:transport?modeLabel+' · '+transport:'',distanceKm,distanceKind,distanceLabel:universityRouteDistanceLabel(distanceKm,distanceKind),distanceMethod,distanceCheckedAt,distanceEvidenceLabel,straightLineKm:computed,stops:stopDetails.map(stop=>stop.label),stopDetails,links,warnings,verification,verificationLabel:verification.label,partsLabel,segmentLabels};
  });
}

export function setupUniversityRoutes({mount,getFields=()=>({}),onChange=()=>{},translate=value=>String(value??''),lookupCached=null,rememberCached=null,calculateRoute=null}={}){
  if(!mount)return {value:()=>[],refresh:()=>{},setPrimary:()=>{},destroy:()=>{}};
  let rows=[],destroyed=false;
  const cacheFingerprint=row=>{const fields=getFields()||{};return JSON.stringify({row,origin:[fields.latitude??'',fields.longitude??'',fields.address??'']});};
  const make=(tag,text='',className='')=>{const element=document.createElement(tag);element.textContent=text;if(className)element.className=className;return element;};
  const shell=make('section','','university-route-editor'),heading=make('h3','Вузы, транспорт и кнопки карт'),note=make('p','Укажите конкретный кампус. Время, номера автобусов и остановки сохраняются только из ваших подтверждённых данных. Расстояние по прямой отделено от маршрута. Пеший и автомобильный расчёт можно явно запросить у Valhalla; автобусы и пересадки берутся из ваших проверенных данных.','muted'),list=make('div','','university-route-list'),add=make('button','Добавить вуз / маршрут','btn');add.type='button';shell.append(heading,note,list,add);mount.replaceChildren(shell);
  const emit=()=>{if(!destroyed)onChange(universityRouteClone(normalizeUniversityRoutes({universityRoutes:rows})));};
  function preview(container){
    const f=getFields()||{},resolved=resolveUniversityRoutes({...f,universityRoutes:rows},{translate});container.replaceChildren();
    for(const route of resolved){const block=make('div','','university-route-preview-row');block.append(make('strong',route.title),make('p',[route.campusTitle||route.destination.address,route.distanceLabel,route.timeLabel,route.transportLabel].filter(Boolean).join(' · '),'muted'),make('p',route.verification.summary,'muted'));
      if(route.distanceEvidenceLabel)block.append(make('p',route.distanceEvidenceLabel,'muted'));
      if(route.partsLabel)block.append(make('p',route.partsLabel));
      for(const segment of route.segmentLabels)block.append(make('p',segment,'muted'));
      if(route.verification.sourceUrl){const source=make('a','Источник проверки маршрута');source.href=route.verification.sourceUrl;source.target='_blank';source.rel='noopener noreferrer';block.append(source);}
      for(const [kind,label]of [['yandex',route.links.yandexKind==='route'?'Яндекс: маршрут':'Яндекс: найти кампус'],['google',route.links.googleKind==='route'?'Google: маршрут':'Google: найти кампус'],['osm','OpenStreetMap: место']])if(route.links[kind]){const a=make('a',label);a.href=route.links[kind];a.target='_blank';a.rel='noopener noreferrer';block.append(a);}
      for(const stop of route.stopDetails.filter(value=>value.name&&value.links.yandex)){const a=make('a','Ориентир: '+stop.label);a.href=stop.links.yandex;a.target='_blank';a.rel='noopener noreferrer';block.append(a);}
      for(const warning of route.warnings)block.append(make('p',warning,'university-route-warning'));container.append(block);
    }
    if(!resolved.length)container.append(make('p','Маршруты пока не заполнены.','muted'));
  }
  function render(){
    if(destroyed)return;list.replaceChildren();add.disabled=rows.length>=UNIVERSITY_ROUTE_LIMIT;
    const previews=make('div','','university-route-previews');
    rows.forEach((row,index)=>{
      const detail=make('details','','university-route-entry'),summary=make('summary',(row.name||row.campus||'Новый вуз')+' · маршрут '+(index+1)),grid=make('div','','university-route-fields');detail.open=!row.name;detail.append(summary,grid);
      const cacheButtons=new Set(),cachePending=new Set(),refreshCacheButtons=()=>{for(const button of cacheButtons)button.disabled=cachePending.has(button)||!row.campusId||row.mode==='unknown';};
      const update=()=>{summary.textContent=(row.name||row.campus||'Новый вуз')+' · маршрут '+(index+1);refreshCacheButtons();emit();preview(previews);};
      const invalidateProvider=(pointChanged=false)=>{if(!row.calculation&&!pointChanged)return;row.calculation=null;row.time=row.routeDistanceKm=row.checkedAt=row.sourceUrl=row.transport='';row.method='unknown';row.segments=[];};
      function input(key,label,{placeholder='',type='text',area=false}={}){const wrapper=make('label',label),control=make(area?'textarea':'input');if(!area)control.type=type;control.value=row[key]??'';control.placeholder=placeholder;control.setAttribute('aria-label',label+' · маршрут '+(index+1));if(type==='number'){control.step='any';control.inputMode='decimal';}if(area)control.rows=3;control.addEventListener('input',()=>{invalidateProvider();row[key]=control.value;update();});wrapper.append(control);grid.append(wrapper);return control;}
      input('name','Вуз',{placeholder:'МГУ / РУДН / своё название'});
      const selectLabel=make('label','Адрес из справочника'),selection=make('select');selection.setAttribute('aria-label','Кампус из справочника · маршрут '+(index+1));selection.append(new Option('Свой кампус / адрес',''));
      for(const school of UNIVERSITY_CATALOG.universities){const campuses=UNIVERSITY_CATALOG.campuses.filter(value=>value.universityId===school.id);if(!campuses.length)continue;const group=make('optgroup');group.label=school.ru;for(const campus of campuses){const available=universityCampusAvailability(campus).selectable;if(!available&&row.campusId!==campus.id)continue;const option=new Option(campus.ru+(available?'':' · прежний адрес'),campus.id);option.disabled=!available;group.append(option);}selection.append(group);}selection.value=row.campusId||'';
      selection.onchange=()=>{const selected=universityRouteCampusById.get(selection.value);row.campusId=selection.value;if(selected){row.name=universityRouteSchoolById.get(selected.universityId)?.ru||row.name;row.campus=selected.ru;row.address=selected.addressRu||'';row.latitude=row.longitude=row.routeDistanceKm=row.time=row.transport=row.checkedAt=row.sourceUrl=row.accessMinutes=row.egressMinutes=row.transfers='';row.method='unknown';row.stops=[];row.segments=[];row.destinationPoint=null;row.calculation=null;}emit();render();};selectLabel.append(selection);grid.append(selectLabel);
      input('campus','Кампус / корпус',{placeholder:'Конкретное место обучения'});input('address','Адрес кампуса',{placeholder:'Официальный адрес выбранного корпуса'});
      input('latitude','Широта кампуса',{type:'number',placeholder:'55.702…'});input('longitude','Долгота кампуса',{type:'number',placeholder:'37.530…'});
      input('time','Подтверждённое время, мин',{placeholder:'40–55'});
      const modeLabel=make('label','Способ поездки'),mode=make('select');mode.setAttribute('aria-label','Способ поездки · маршрут '+(index+1));for(const [value,label]of [['unknown','Не указан'],['public','Общественный транспорт'],['metro','Метро'],['bus','Автобус'],['walk','Пешком'],['car','Автомобиль'],['bike','Велосипед']])mode.append(new Option(label,value));mode.value=row.mode||'unknown';mode.onchange=()=>{invalidateProvider(true);row.mode=mode.value;update();};modeLabel.append(mode);grid.append(modeLabel);
      input('transport','Автобусы / пересадки',{placeholder:'Автобус №… → метро…',area:true});input('routeDistanceKm','Проверенная длина маршрута, км',{type:'number',placeholder:'Из карты; пусто — считать только прямую'});
      const methodLabel=make('label','Как проверено'),method=make('select');method.setAttribute('aria-label','Способ проверки · маршрут '+(index+1));for(const [value,label]of [['unknown','Не записан'],['manual-confirmed','Проверено оператором'],['provider','Данные маршрутного сервиса'],['estimate','Приблизительная оценка']])method.append(new Option(label,value));method.value=row.method||'unknown';method.onchange=()=>{row.method=method.value;update();};methodLabel.append(method);grid.append(methodLabel);
      input('checkedAt','Дата проверки',{type:'date'});input('sourceUrl','HTTPS-источник маршрута',{type:'url',placeholder:'Ссылка на проверенный маршрут'});
      input('accessMinutes','Пешком от дома до транспорта, мин',{type:'number'});input('egressMinutes','Пешком от транспорта до корпуса, мин',{type:'number'});input('transfers','Количество пересадок',{type:'number'});
      const segmentLabel=make('label','Участки: до 8 строк «walk/bus/metro/public/car/bike | минуты | откуда | куда | транспорт»'),segments=make('textarea');segments.rows=4;segments.setAttribute('aria-label','Участки маршрута · маршрут '+(index+1));segments.value=(row.segments||[]).map(segment=>[segment.mode,segment.minutes,segment.from,segment.to,segment.transport].join(' | ')).join('\n');segments.oninput=()=>{row.segments=segments.value.split('\n').filter(line=>line.trim()).slice(0,UNIVERSITY_ROUTE_SEGMENT_LIMIT).map(line=>{const [mode='unknown',minutes='',from='',to='',transport='']=line.split('|').map(value=>value.trim());return {mode,minutes,from,to,transport};});update();};segmentLabel.append(segments);grid.append(segmentLabel);
      const campusRecord=universityRouteCampusById.get(row.campusId)||resolveCampus(row.campus);if(campusRecord){const refs=(campusRecord.sourceIds||[]).map(id=>UNIVERSITY_CATALOG.sources.find(source=>source.id===id)).filter(Boolean);const bibliography=make('div','','university-route-campus-sources');bibliography.append(make('p','Адрес и название корпуса · срез справочника '+UNIVERSITY_CATALOG.asOf,'muted'));for(const ref of refs){const a=make('a',ref.publisher+' · проверено '+ref.verifiedAt+(ref.evidenceType==='official-university'?' · сайт учреждения':' · официальный образовательный портал'));a.href=ref.url;a.target='_blank';a.rel='noopener noreferrer';bibliography.append(a);}grid.append(bibliography);}
      const stopLabel=make('label','Остановки / ориентиры: до трёх строк «название | широта | долгота»'),stops=make('textarea');stops.rows=3;stops.setAttribute('aria-label','Остановки и координаты · маршрут '+(index+1));stops.value=(Array.isArray(row.stops)?row.stops:[]).map(stop=>[stop.name,stop.latitude??'',stop.longitude??''].join(' | ')).join('\n');stops.oninput=()=>{row.stops=stops.value.split('\n').slice(0,UNIVERSITY_ROUTE_STOP_LIMIT).map(line=>{const [name,latitude='',longitude='']=line.split('|').map(value=>value.trim());return {name,latitude,longitude};});update();};stopLabel.append(stops);grid.append(stopLabel);
      if(typeof calculateRoute==='function'){
        const pointsPanel=make('details','','university-route-provider'),pointsTitle=make('summary','Выбранные точки и расчёт по дорожной сети'),pointGrid=make('div','','university-route-fields'),status=make('p','','muted');pointsPanel.append(pointsTitle,make('p','Запрос передаст только две выбранные координаты сервису Valhalla/FOSSGIS. Время — оценка по дорожной сети, без расписания общественного транспорта.','muted'),pointGrid);
        for(const [key,label,fallback]of [['originPoint','Начало',getFields()||{}],['destinationPoint','Назначение',row]]){
          const resolved=key==='destinationPoint'?resolveUniversityRoutes({...getFields(),universityRoutes:[row]})[0]?.destination:null,base=resolved||fallback;
          const value=row[key]||{latitude:base.latitude??'',longitude:base.longitude??'',name:label,kind:'point',availability:'unknown',checkedAt:'',sourceUrl:''};
          const changed=()=>{invalidateProvider(true);row[key]={...value};update();};
          const title=make('strong',label);pointGrid.append(title);
          for(const [field,caption,type]of [['name','Название','text'],['latitude','Широта','number'],['longitude','Долгота','number'],['checkedAt','Проверено','date'],['sourceUrl','Источник проверки входа','url']]){
            const wrap=make('label',label+' · '+caption),control=make('input');control.type=type;if(type==='number'){control.step='any';control.inputMode='decimal';}control.value=value[field]??'';control.setAttribute('aria-label',label+' · '+caption+' · маршрут '+(index+1));control.oninput=()=>{value[field]=type==='number'&&control.value!==''?Number(control.value):control.value;changed();};wrap.append(control);pointGrid.append(wrap);
          }
          for(const [field,caption,choices]of [['kind','Тип точки',[['point','Координата / центр'],['building-entrance','Конкретный вход дома'],['metro-exit','Конкретный выход метро']]],['availability','Доступность',[['unknown','Не проверена'],['available','Доступен'],['closed','Закрыт']]]]){
            const wrap=make('label',label+' · '+caption),control=make('select');for(const [v,t]of choices)control.append(new Option(t,v));control.value=value[field];control.setAttribute('aria-label',label+' · '+caption+' · маршрут '+(index+1));control.onchange=()=>{value[field]=control.value;changed();};wrap.append(control);pointGrid.append(wrap);
          }
        }
        const useWrap=make('label','Использовать только проверенные доступные входы'),use=make('input');use.type='checkbox';use.checked=row.useEntrances===true;use.onchange=()=>{invalidateProvider(true);row.useEntrances=use.checked;update();};useWrap.prepend(use);pointsPanel.append(useWrap);
        const calculate=make('button','Рассчитать выбранный пеший / автомобильный маршрут','btn');calculate.type='button';calculate.onclick=async()=>{
          const current=cacheFingerprint(row);calculate.disabled=true;status.textContent='Сервис рассчитывает выбранные точки · до 12 секунд…';
          try{const fields=universityRouteClone(getFields()||{}),destination=resolveUniversityRoutes({...fields,universityRoutes:[row]})[0]?.destination||row,requestRow={...universityRouteClone(row),originPoint:universityRoutePoint(row.originPoint||{...fields,name:'Начало'}),destinationPoint:universityRoutePoint(row.destinationPoint||{...destination,name:'Назначение'})};const result=await calculateRoute(requestRow,fields);if(destroyed||!rows.includes(row))return;if(cacheFingerprint(row)!==current){status.textContent='Точки или поля изменились: результат не применён.';return;}
            row.time=result.time;row.routeDistanceKm=result.routeDistanceKm;row.method='provider';row.checkedAt=result.checkedAt;row.sourceUrl=result.sourceUrl;row.originPoint=result.origin;row.destinationPoint=result.destination;row.useEntrances=result.useEntrances;row.transport='';row.segments=[];row.transfers=row.accessMinutes=row.egressMinutes='';row.calculation=universityRouteCalculation(result);
            emit();render();
          }catch(error){if(!destroyed)status.textContent=error?.message||'Расчёт недоступен. Ранее записанные данные сохранены.';}finally{if(!destroyed)calculate.disabled=false;}
        };pointsPanel.append(calculate,status,make('p','© OpenStreetMap contributors · Valhalla / FOSSGIS e.V. Публичный демонстрационный сервис без гарантии доступности. Повтор одного маршрута берётся из кеша на 10 минут.','muted'));
        if(row.calculation){const when=new Date(row.calculation.calculatedAt).toLocaleString('ru-RU',{timeZone:'Europe/Moscow'});pointsPanel.append(make('p','Рассчитано '+when+' МСК · '+row.calculation.distanceKm+' км · '+Math.ceil(row.calculation.durationSeconds/60)+' мин · оценка, не фактическая поездка.','muted'));}
        detail.append(pointsPanel);
      }
      const remove=make('button','Убрать маршрут','btn');remove.type='button';remove.onclick=()=>{rows.splice(index,1);emit();render();};detail.append(remove);list.append(detail);
      if(typeof lookupCached==='function'||typeof rememberCached==='function'){
        const cacheStatus=make('p','','muted');
        for(const [callback,label,reuse]of [[lookupCached,'Взять недавний проверенный маршрут',true],[rememberCached,'Запомнить проверенный маршрут на 1 час',false]])if(typeof callback==='function'){
          const button=make('button',label,'btn');button.type='button';cacheButtons.add(button);refreshCacheButtons();
          button.onclick=async()=>{cachePending.add(button);refreshCacheButtons();cacheStatus.textContent='Проверяем кеш маршрута…';const requested=cacheFingerprint(row);try{const result=await callback(universityRouteClone(row),universityRouteClone(getFields()||{}));if(destroyed||!rows.includes(row))return;if(cacheFingerprint(row)!==requested){cacheStatus.textContent='Поля изменились во время запроса. Результат кеша не применён.';return;}if(reuse){if(result?.hit&&result.route){const cached=normalizeUniversityRoutes({universityRoutes:[result.route]})[0];if(!cached)throw Error('В кеше нет допустимого маршрута.');rows[rows.indexOf(row)]=cached;emit();render();}else cacheStatus.textContent=result?.reason==='expired'?'Срок кеша истёк. Проверьте маршрут в картах.':'Для этой исходной точки, корпуса, транспорта и источника нет свежего маршрута.';}else cacheStatus.textContent='Маршрут сохранён на 1 час; дата проверки и источник сохраняются.';}catch(error){if(!destroyed)cacheStatus.textContent=error?.message||'Кеш маршрутов недоступен.';}finally{cachePending.delete(button);if(!destroyed&&rows.includes(row))refreshCacheButtons();}};detail.append(button);
        }detail.append(cacheStatus);
      }
    });list.append(previews);preview(previews);
  }
  function refresh(fields=getFields()){rows=normalizeUniversityRoutes(fields);render();}
  add.onclick=()=>{if(rows.length>=UNIVERSITY_ROUTE_LIMIT)return;rows.push({name:'',campus:'',campusId:'',address:'',latitude:'',longitude:'',time:'',mode:'unknown',transport:'',routeDistanceKm:'',stops:[]});render();};
  refresh();return {value:()=>universityRouteClone(normalizeUniversityRoutes({universityRoutes:rows})),refresh,setPrimary({university='',campus=''}={}){const old=rows[0],next={name:university,campus};if(old&&old.name===university&&old.campus===campus)return;const row={...next,time:'',mode:'unknown',transport:'',stops:[]};if(rows.length)rows[0]=row;else if(university||campus)rows.push(row);emit();render();},destroy(){destroyed=true;rows=[];mount.replaceChildren();}};
}
