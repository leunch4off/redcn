import {UNIVERSITY_CATALOG,resolveUniversity,resolveCampus} from './university-catalog.mjs';

// Map links ask the provider for a current route. They are not a local routing
// result and never establish bus numbers, travelling time, or road distance.
export const UNIVERSITY_ROUTE_LIMIT=10;
export const UNIVERSITY_ROUTE_STOP_LIMIT=3;
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
    return {name:universityRouteText(row.name||row.university),campus:universityRouteText(row.campus),campusId:universityRouteCampusById.has(campusId)?campusId:'',address:universityRouteText(row.address,500),latitude:coordinate(row.latitude,90),longitude:coordinate(row.longitude,180),time:universityRouteText(row.time,80),mode:universityRouteMode(row.mode),transport:universityRouteText(row.transport||row.line,600),routeDistanceKm:distance!==null&&distance>=0&&distance<=20000?distance:'',stops};
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
    const warnings=[],university=resolveUniversity(row.name),namedCampus=resolveCampus(row.campus||row.name),campus=universityRouteCampusById.get(row.campusId)||namedCampus,mismatch=!!(university&&campus&&university.id!==campus.universityId||namedCampus&&campus&&namedCampus.id!==campus.id);
    if(mismatch)warnings.push('Вуз и выбранный кампус не совпадают. Уточните место назначения.');
    const selectedCampus=mismatch?null:campus,parent=selectedCampus?universityRouteSchoolById.get(selectedCampus.universityId):null;
    const destinationPoint=mismatch?null:universityRouteCoordinates(row.latitude,row.longitude),destinationAddress=mismatch?'':row.address||selectedCampus?.addressRu||'';
    const rawTitle=row.name||(parent?.ru)||row.campus||'院校/校区',translatedTitle=translate(rawTitle),title=translatedTitle!==rawTitle?translatedTitle:university?.zh||parent?.zh||selectedCampus?.zh||translatedTitle;
    const rawCampus=row.campus||selectedCampus?.ru||'',translatedCampus=translate(rawCampus),campusTitle=translatedCampus!==rawCampus?translatedCampus:selectedCampus?.zh||translatedCampus,destination={name:selectedCampus?.ru||row.campus||row.name,address:destinationAddress,...destinationPoint};
    const confirmedDestination=!!(destinationPoint||destinationAddress),time=universityRouteTimeLabel(row.time,translate);
    if(time.invalid)warnings.push('Проверьте диапазон времени поездки: начало больше конца.');
    const computed=confirmedDestination?universityStraightLineKm(origin,destination):null,provided=mismatch?null:universityRouteNumber(row.routeDistanceKm),distanceKind=provided!==null?'provided-route':computed!==null?'straight-line':'unknown',distanceKm=provided??computed;
    const modeLabel=universityRouteModes[row.mode],transport=translate(row.transport),links=mismatch?{yandex:'',google:'',osm:'',yandexKind:'search',googleKind:'search',osmKind:'position'}:universityMapLinks(origin,destination,row.mode,{city,confirmedDestination});
    const stopDetails=row.stops.length?row.stops.map(stop=>({name:stop.name,label:translate(stop.name),latitude:stop.latitude,longitude:stop.longitude,links:universityStopLinks(stop,city)})):[
      {name:universityRouteText(f.metro)||'住处',label:translate(universityRouteText(f.metro))||'住处',links:universityRouteText(f.metro)?universityStopLinks({name:'метро '+f.metro},city):universityStopLinks(origin,city)},
      {name:row.transport,label:transport||modeLabel,links:{yandex:'',google:'',osm:''}},
      {name:destination.name,label:campusTitle||title,links:{yandex:links.yandexKind==='search'?links.yandex:universityStopLinks(destination,city).yandex,google:universityStopLinks(destination,city).google,osm:links.osm}}
    ];
    return {...row,title,campusTitle,destination,confirmedDestination,time:time.label,timeLabel:time.label,confirmedTime:time.confirmed,modeLabel,transport,transportLabel:transport?modeLabel+' · '+transport:'',distanceKm,distanceKind,distanceLabel:universityRouteDistanceLabel(distanceKm,distanceKind),straightLineKm:computed,stops:stopDetails.map(stop=>stop.label),stopDetails,links,warnings};
  });
}

export function setupUniversityRoutes({mount,getFields=()=>({}),onChange=()=>{},translate=value=>String(value??'')}={}){
  if(!mount)return {value:()=>[],refresh:()=>{},setPrimary:()=>{},destroy:()=>{}};
  let rows=[],destroyed=false;
  const make=(tag,text='',className='')=>{const element=document.createElement(tag);element.textContent=text;if(className)element.className=className;return element;};
  const shell=make('section','','university-route-editor'),heading=make('h3','Вузы, транспорт и кнопки карт'),note=make('p','Укажите конкретный кампус. Время, номера автобусов и остановки сохраняются только из ваших подтверждённых данных. Расчёт по координатам — расстояние по прямой, а актуальный маршрут откроется в картах.','muted'),list=make('div','','university-route-list'),add=make('button','Добавить вуз / маршрут','btn');add.type='button';shell.append(heading,note,list,add);mount.replaceChildren(shell);
  const emit=()=>{if(!destroyed)onChange(universityRouteClone(normalizeUniversityRoutes({universityRoutes:rows})));};
  function preview(container){
    const f=getFields()||{},resolved=resolveUniversityRoutes({...f,universityRoutes:rows},{translate});container.replaceChildren();
    for(const route of resolved){const block=make('div','','university-route-preview-row');block.append(make('strong',route.title),make('p',[route.distanceLabel,route.timeLabel,route.transportLabel].filter(Boolean).join(' · '),'muted'));
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
      const update=()=>{summary.textContent=(row.name||row.campus||'Новый вуз')+' · маршрут '+(index+1);emit();preview(previews);};
      function input(key,label,{placeholder='',type='text',area=false}={}){const wrapper=make('label',label),control=make(area?'textarea':'input');if(!area)control.type=type;control.value=row[key]??'';control.placeholder=placeholder;control.setAttribute('aria-label',label+' · маршрут '+(index+1));if(type==='number'){control.step='any';control.inputMode='decimal';}if(area)control.rows=3;control.addEventListener('input',()=>{row[key]=control.value;update();});wrapper.append(control);grid.append(wrapper);return control;}
      input('name','Вуз',{placeholder:'МГУ / РУДН / своё название'});
      const selectLabel=make('label','Адрес из справочника'),selection=make('select');selection.setAttribute('aria-label','Кампус из справочника · маршрут '+(index+1));selection.append(new Option('Свой кампус / адрес',''));
      for(const school of UNIVERSITY_CATALOG.universities){const campuses=UNIVERSITY_CATALOG.campuses.filter(value=>value.universityId===school.id);if(!campuses.length)continue;const group=make('optgroup');group.label=school.ru;for(const campus of campuses)group.append(new Option(campus.ru,campus.id));selection.append(group);}selection.value=row.campusId||'';
      selection.onchange=()=>{const selected=universityRouteCampusById.get(selection.value);row.campusId=selection.value;if(selected){row.name=universityRouteSchoolById.get(selected.universityId)?.ru||row.name;row.campus=selected.ru;row.address=selected.addressRu||'';row.latitude=row.longitude=row.routeDistanceKm=row.time=row.transport='';row.stops=[];}emit();render();};selectLabel.append(selection);grid.append(selectLabel);
      input('campus','Кампус / корпус',{placeholder:'Конкретное место обучения'});input('address','Адрес кампуса',{placeholder:'Официальный адрес выбранного корпуса'});
      input('latitude','Широта кампуса',{type:'number',placeholder:'55.702…'});input('longitude','Долгота кампуса',{type:'number',placeholder:'37.530…'});
      input('time','Подтверждённое время, мин',{placeholder:'40–55'});
      const modeLabel=make('label','Способ поездки'),mode=make('select');mode.setAttribute('aria-label','Способ поездки · маршрут '+(index+1));for(const [value,label]of [['unknown','Не указан'],['public','Общественный транспорт'],['metro','Метро'],['bus','Автобус'],['walk','Пешком'],['car','Автомобиль'],['bike','Велосипед']])mode.append(new Option(label,value));mode.value=row.mode||'unknown';mode.onchange=()=>{row.mode=mode.value;update();};modeLabel.append(mode);grid.append(modeLabel);
      input('transport','Автобусы / пересадки',{placeholder:'Автобус №… → метро…',area:true});input('routeDistanceKm','Проверенная длина маршрута, км',{type:'number',placeholder:'Из карты; пусто — считать только прямую'});
      const stopLabel=make('label','Остановки / ориентиры: до трёх строк «название | широта | долгота»'),stops=make('textarea');stops.rows=3;stops.setAttribute('aria-label','Остановки и координаты · маршрут '+(index+1));stops.value=(Array.isArray(row.stops)?row.stops:[]).map(stop=>[stop.name,stop.latitude??'',stop.longitude??''].join(' | ')).join('\n');stops.oninput=()=>{row.stops=stops.value.split('\n').slice(0,UNIVERSITY_ROUTE_STOP_LIMIT).map(line=>{const [name,latitude='',longitude='']=line.split('|').map(value=>value.trim());return {name,latitude,longitude};});update();};stopLabel.append(stops);grid.append(stopLabel);
      const remove=make('button','Убрать маршрут','btn');remove.type='button';remove.onclick=()=>{rows.splice(index,1);emit();render();};detail.append(remove);list.append(detail);
    });list.append(previews);preview(previews);
  }
  function refresh(fields=getFields()){rows=normalizeUniversityRoutes(fields);render();}
  add.onclick=()=>{if(rows.length>=UNIVERSITY_ROUTE_LIMIT)return;rows.push({name:'',campus:'',campusId:'',address:'',latitude:'',longitude:'',time:'',mode:'unknown',transport:'',routeDistanceKm:'',stops:[]});render();};
  refresh();return {value:()=>universityRouteClone(normalizeUniversityRoutes({universityRoutes:rows})),refresh,setPrimary({university='',campus=''}={}){const old=rows[0],next={name:university,campus};if(old&&old.name===university&&old.campus===campus)return;const row={...next,time:'',mode:'unknown',transport:'',stops:[]};if(rows.length)rows[0]=row;else if(university||campus)rows.push(row);emit();render();},destroy(){destroyed=true;rows=[];mount.replaceChildren();}};
}
