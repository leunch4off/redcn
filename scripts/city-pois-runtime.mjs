const cityPoiFold=value=>String(value??'').normalize('NFC').toLocaleLowerCase('ru').replaceAll('ё','е').replace(/\s+/g,' ').trim();
export const CITY_POI_CATEGORIES=Object.freeze({'chinese-grocery':'Азиатские продукты, включая китайские','chinese-restaurant':'Китайская кухня',park:'Парки · опубликованные ориентиры'});
export function cityPoiStats(){return {places:CITY_POIS.places.length,categories:Object.fromEntries(Object.keys(CITY_POI_CATEGORIES).map(key=>[key,CITY_POIS.places.filter(row=>row.category===key).length])),verifiedAt:CITY_POIS.verifiedAt,exhaustive:false};}
export function searchCityPois(query='',{category='',offset=0,limit=30}={}){
 const q=cityPoiFold(query);if(q.length>200)throw Error('Поиск мест: не более 200 символов.');if(category&&!Object.hasOwn(CITY_POI_CATEGORIES,category))throw Error('Неизвестная категория мест.');
 const start=Math.max(0,Math.min(10000,Math.trunc(Number(offset)||0))),size=Math.max(1,Math.min(100,Math.trunc(Number(limit)||30))),sources=new Map(CITY_POIS.sources.map(row=>[row.id,row]));
 const matches=CITY_POIS.places.filter(row=>(!category||row.category===category)&&(!q||[row.ru,row.zh,row.addressRu,...row.aliases].some(value=>cityPoiFold(value).includes(q))));
 return {entries:matches.slice(start,start+size).map(row=>({...row,aliases:[...row.aliases],sourceIds:[...row.sourceIds],coordinateEvidence:{...row.coordinateEvidence,sourceIds:[...row.coordinateEvidence.sourceIds]},sources:row.sourceIds.map(id=>({...sources.get(id)})),sourceId:'city-pois',reviewed:false})),total:matches.length,offset:start,limit:size,stats:cityPoiStats()};
}
export function cityPoiMapLinks(place){
 if(!place||typeof place.latitude!=='number'||typeof place.longitude!=='number'||!Number.isFinite(place.latitude)||!Number.isFinite(place.longitude)||Math.abs(place.latitude)>90||Math.abs(place.longitude)>180)throw Error('У места нет проверенной пары координат.');
 const lat=String(place.latitude),lon=String(place.longitude),point=lat+','+lon;
 return {yandex:'https://yandex.ru/maps/?'+new URLSearchParams({pt:lon+','+lat,z:'17'}),google:'https://www.google.com/maps/search/?'+new URLSearchParams({api:'1',query:point}),osm:'https://www.openstreetmap.org/?'+new URLSearchParams({mlat:lat,mlon:lon})+'#map=17/'+lat+'/'+lon};
}
export function setupCityPois({mount}={}){
 if(!mount)return {destroy(){},refresh(){}};
 let destroyed=false;const make=(tag,text='',className='')=>{const node=document.createElement(tag);node.textContent=text;if(className)node.className=className;return node;};
 const section=make('section','','city-pois-panel'),heading=make('h2','Городские места · источники и координаты'),intro=make('p','Китайские продукты, кухня и парки. Это небольшой проверенный справочник; он не подтверждает, что место рядом с выбранной квартирой.','muted'),filters=make('div','','city-poi-filters'),search=make('input'),category=make('select'),count=make('p','','muted'),list=make('div','','city-poi-results');
 search.type='search';search.maxLength=200;search.placeholder='Название, адрес или 中文';search.setAttribute('aria-label','Поиск городских мест');category.setAttribute('aria-label','Категория городских мест');category.append(new Option('Все категории',''));for(const [value,label]of Object.entries(CITY_POI_CATEGORIES))category.append(new Option(label,value));filters.append(search,category);count.setAttribute('role','status');section.append(heading,intro,filters,count,list);mount.replaceChildren(section);
 const scopeLabel={'named-store-marker':'Метка магазина, опубликованная владельцем','restaurant-route-destination':'Конечная точка маршрута владельца','historical-park-meeting-point':'Место сбора из городского документа','historical-park-main-entrance-point':'Главный вход, указанный в городском документе'};
 function render(){if(destroyed)return;const result=searchCityPois(search.value,{category:category.value});count.textContent='Найдено: '+result.total+' · проверено '+CITY_POIS.verifiedAt+' · справочник не исчерпывающий';list.replaceChildren();for(const row of result.entries){
  const card=make('article','','city-poi-card');card.append(make('h3',row.ru),make('p',row.zh),make('p',row.addressRu,'muted'),make('p',CITY_POI_CATEGORIES[row.category]+' · китайская подпись категории — редакционная','muted'),make('p',row.noteRu,'muted'));
  card.append(make('p',scopeLabel[row.coordinateEvidence.scope]+' · '+row.latitude+', '+row.longitude+' · проверено '+row.coordinateEvidence.verifiedAt,'muted'),make('p','Точность в метрах не опубликована. Текущий режим работы и доступность входа не подтверждены.','muted'));
  const actions=make('div','','city-poi-links');for(const [key,label]of [['yandex','Яндекс · точка'],['google','Google · точка'],['osm','OpenStreetMap']]){const link=make('a',label);link.href=cityPoiMapLinks(row)[key];link.target='_blank';link.rel='noopener noreferrer';actions.append(link);}card.append(actions);
  const details=make('details'),summary=make('summary','Источники и область проверки');details.append(summary);for(const source of row.sources){const p=make('p'),link=make('a',source.publisher+' · '+source.verifiedAt);link.href=source.url;link.target='_blank';link.rel='noopener noreferrer';p.append(link);if(source.locator)p.append(make('span',' — '+source.locator));details.append(p);}card.append(details);list.append(card);
 }if(!result.total)list.append(make('p','В проверенном наборе нет подходящих мест.','muted'));}
 search.addEventListener('input',render);category.addEventListener('change',render);render();return {refresh:render,destroy(){destroyed=true;mount.replaceChildren();}};
}
