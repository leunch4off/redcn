import {photoSources} from '../media-core.mjs';
import {listingDocument,listingText,listingDescendants,listingClean} from './html.mjs';

// Avito-specific selectors/labels stay here; generated CSS names are never used.
const AVITO_GROUPS={'О квартире':'apartment','Условия аренды':'rental','Правила':'rules','О доме':'building','Расположение':'location','Особенности':'highlights','Описание':'description'};
const avitoMark=node=>node.attrs['data-marker']||'';
const avitoHeading=node=>listingDescendants(node).find(n=>/^h[1-3]$/.test(n.tag));
const avitoNumber=text=>String(text||'').match(/\d[\d \u00a0]*(?:[.,]\d+)?/)?.[0]?.replace(/[ \u00a0]/g,'').replace(',','.');
function avitoUrl(value){try{const u=new URL(value);if(u.protocol!=='https:'||u.username||u.password||u.port||!/(^|\.)avito\.ru$/i.test(u.hostname))return '';u.search='';u.hash='';return u.href;}catch{return '';}}

export function readAvitoHtml(input,{url=''}={}){
 const {nodes}=listingDocument(input),records=[],groups=[],images=[],seen=new Set();
 const add=(name,text,extra={})=>{text=listingClean(text);const key=name+'|'+text;if(text&&!seen.has(key)){seen.add(key);records.push({name,text,href:'',...extra});}};
 const marker=(name)=>nodes.filter(n=>avitoMark(n)===name);
 const title=listingText(marker('item-view/title-info')[0]||nodes.find(n=>n.tag==='h1')||{tag:'div',attrs:{},children:[]});
 add('OfferTitle',title);
 const priceNodes=marker('item-view/item-price');
 for(const n of priceNodes.length?priceNodes:marker('item-view/item-price-container'))add('PriceInfo',listingText(n),{section:'price'});
 if(!records.some(r=>r.name==='PriceInfo'))for(const n of nodes.filter(n=>n.attrs.id==='item-price'||n.attrs.itemprop==='price'))add('PriceInfo',n.attrs.content||listingText(n),{section:'price'});
 for(const n of marker('item-view/item-description'))add('Description',listingText(n),{section:'description'});
 const groupNodes=new Set(marker('item-view/item-params'));
 for(const n of nodes.filter(n=>/^h[1-3]$/.test(n.tag))){if(AVITO_GROUPS[listingText(n)]&&n.parent)groupNodes.add(n.parent);}
 for(const node of groupNodes){
  const heading=avitoHeading(node),label=heading?listingText(heading):'',section=AVITO_GROUPS[label]||'parameters';
  const descendants=listingDescendants(node);
  // Header containers can be larger than a semantic section. Never capture a
  // whole page or a parent with multiple different section headings.
  if(descendants.filter(n=>/^h[1-3]$/.test(n.tag)&&AVITO_GROUPS[listingText(n)]).length>1)continue;
  const text=listingText(node);if(!text)continue;
  const signature=section+'|'+text;if(groups.some(g=>g.signature===signature))continue;
  const rows=descendants.filter(n=>n.tag==='li'||/^item-view\/item-params\//.test(avitoMark(n))).map(listingText).filter(Boolean);
  groups.push({section,label,text,rows,signature});
  if(section==='description')add('Description',text.replace(/^Описание\s*/,''),{section});
  else if(section==='location'){
   const withoutMap=node.children.filter(n=>typeof n!=='string'&&n!==heading&&avitoMark(n)!=='item-map-wrapper');
   const locationNodes=withoutMap.flatMap(n=>[n,...listingDescendants(n)]);
   // The shortest explicit text ending in minutes is one transit entry.
   const routes=locationNodes.filter(n=>/\d+\s*(?:[–—-]\s*\d+)?\s*мин\.?$/.test(listingText(n))&&!listingDescendants(n).some(child=>/\d+\s*(?:[–—-]\s*\d+)?\s*мин\.?$/.test(listingText(child))&&/[а-я]/i.test(listingText(child).replace(/мин\.?$/,''))));
   for(const route of routes){const raw=listingText(route).replace(/\n/g,' '),m=raw.match(/^(.*?)\s*(\d+(?:\s*[–—-]\s*\d+)?)\s*мин\.?$/);if(!m||!m[1].trim())continue;
    const icons=[route,...listingDescendants(route)],walking=icons.some(n=>n.attrs['data-icon-name']==='walkingroute'||/пешком|walking/i.test(n.attrs['aria-label']||''));
    add('UndergroundItem',m[1].trim()+' '+m[2].replace(/\s/g,'')+' мин.',{section,mode:walking?'walk':'unknown'});
   }
   const address=locationNodes.find(n=>n.attrs.itemprop==='address')||locationNodes.find(n=>n.tag==='span'&&/^(?:Москва|Московская обл)/i.test(listingText(n))&&!listingDescendants(n).length);
   if(address)add('AddressItem',listingText(address),{section});
   add('Location',withoutMap.map(listingText).join('\n'),{section});
  }else if(section==='highlights')add('Highlights',text,{section});
  else for(const row of rows.length?rows:[text])add(section==='rules'?'Rules':section==='apartment'&&/^(?:Мебель|Техника|Интернет и ТВ)\s*:/.test(row)?'FeaturesItem':'OfferSummaryInfoItem',row,{section});
 }
 for(const n of marker('item-map-wrapper')){
  const lat=Number(n.attrs['data-map-lat']),lon=Number(n.attrs['data-map-lon']);
  if(n.attrs['data-map-lat']&&n.attrs['data-map-lon']&&Number.isFinite(lat)&&Math.abs(lat)<=90&&Number.isFinite(lon)&&Math.abs(lon)<=180)add('Coordinates',lat+'|'+lon,{section:'location'});
 }
 for(const gallery of marker('item-view/main-gallery'))for(const n of listingDescendants(gallery)){
  if(!['img','source','a'].includes(n.tag))continue;
  for(const key of ['data-original','data-src'])if(n.attrs[key])images.push(n.attrs[key]);
  for(const key of ['data-srcset','srcset'])for(const part of (n.attrs[key]||'').split(','))if(part.trim())images.push(part.trim().split(/\s+/)[0]);
  if(n.tag!=='a'&&n.attrs.src)images.push(n.attrs.src);
  if(n.tag==='a'&&/\.(?:jpe?g|png|webp)(?:[?#]|$)/i.test(n.attrs.href||''))images.push(n.attrs.href);
 }
 // OG/share images may be composites or recommendation content. Use only the
 // primary gallery; an empty gallery is reported rather than filled with avatars.
 const canonical=nodes.find(n=>n.tag==='link'&&(n.attrs.rel||'').toLowerCase()==='canonical');
 const sourceUrl=avitoUrl(url)||avitoUrl(canonical?.attrs.href||'');
 const description=records.find(r=>r.name==='Description')?.text||'';
 if(description&&!groups.some(g=>g.section==='description'))groups.push({section:'description',label:'Описание',text:description,rows:[]});
 const selected=records.filter(r=>!['Coordinates'].includes(r.name));
 const safeGroups=groups.map(({signature,...g})=>g);
 const source={version:1,platform:'avito',title,description,url:sourceUrl,text:listingClean(selected.map(r=>r.text).join('\n')).slice(0,50000),images:photoSources(images,sourceUrl),related:[],records,offer:null,sections:safeGroups,stats:{records:records.length,jsonStates:0,duplicateDocuments:(input.match(/<!doctype\s+html/gi)||[]).length,adapter:'avito-v1'}};
 const extracted=avitoFieldFacts(source);source.permissions=extracted.permissions;source.equipment=extracted.equipment;
 return source;
}

export function avitoFieldFacts(source){
 const facts={},evidence=[],warnings=[],permissions={},equipment={},records=source.records||[];
 const put=(field,value,row)=>{if(value===undefined||value===null||value==='')return;if(facts[field]!==undefined&&String(facts[field])!==String(value)){warnings.push('Авито: разные значения поля '+field+'; требуется проверка.');return;}facts[field]=value;evidence.push({field,value,source:'Авито · '+(row?.section||row?.name||'HTML'),text:String(row?.text??value).slice(0,700)});};
 const number=(field,value,row)=>{const n=avitoNumber(value);if(n!==undefined)put(field,n,row);};
 const fields={'Количество комнат':'rooms','Общая площадь':'area','Площадь кухни':'kitchen','Жилая площадь':'living','Тип дома':'buildingType','Год постройки':'buildingYear','Этажей в доме':'floors','Санузел':'bathroom','Ремонт':'renovation','Парковка':'parking','Балкон или лоджия':'balconyType','Тип комнат':'roomType','Количество жильцов':'occupants','Мебель':'furnitureText','Техника':'equipmentText','Интернет и ТВ':'internetText','Пассажирский лифт':'passengerLifts','Грузовой лифт':'freightLifts','Высота потолков':'ceiling'};
 for(const row of records){
  const text=row.text.replace(/\s+/g,' ').trim();
  if(row.name==='PriceInfo')number('price',text,row);
  if(row.name==='OfferTitle'){
   const rooms=text.match(/(?:^|\s)(\d+)\s*-?\s*к[. -]/i),area=text.match(/(\d+(?:[.,]\d+)?)\s*м[²2]/i),floor=text.match(/(\d+)\s*\/\s*(\d+)\s*эт/i);
   if(rooms)put('rooms',rooms[1],row);if(area)put('area',area[1].replace(',','.'),row);if(floor){put('floor',floor[1],row);put('floors',floor[2],row);}if(/студи/i.test(text))put('layout','studio',row);
  }
  if(row.name==='AddressItem'){put('address',text,row);if(/^Москва\b/u.test(text)||text.startsWith('Москва,'))put('city','Москва',row);}
  if(row.name==='Coordinates'){const [lat,lon]=text.split('|');put('latitude',lat,row);put('longitude',lon,row);}
  if(!['OfferSummaryInfoItem','Rules','FeaturesItem'].includes(row.name))continue;
  const m=text.match(/^([^:]+):\s*(.*)$/);if(!m)continue;const key=m[1].trim(),value=m[2].trim();
  const field=fields[key];if(field){if(['rooms','area','kitchen','living','buildingYear','floors','occupants','passengerLifts','freightLifts','ceiling'].includes(field))number(field,value,row);else put(field,value,row);}
  if(key==='Этаж'){const f=value.match(/(\d+)\s*(?:из|\/)\s*(\d+)/);if(f){put('floor',f[1],row);put('floors',f[2],row);}}
  if(key==='Залог')number('deposit',value,row);
  if(key==='Комиссия'){if(/%/.test(value)||/^(?:нет|без комиссии)$/i.test(value))put('commission',/нет|без/i.test(value)?'0':avitoNumber(value),row);else{facts.commission='';number('commissionAmount',value,row);warnings.push('Авито: комиссия указана суммой, а не процентом.');}}
  if(key==='Тип комнат'&&/изолир/i.test(value))put('separate',true,row);
  if(key==='Можно с детьми'||key==='Можно с питомцем'||key==='Можно с животными'||key==='Можно курить'){
   const policy=/^(?:нет|нельзя|запрещено)$/i.test(value)?false:/^(?:да|можно|разрешено)$/i.test(value)?true:null;
   const which=key==='Можно с детьми'?'children':key==='Можно курить'?'smoking':'pets';if(policy!==null){put(which==='pets'?'petPolicy':which+'Policy',policy?'yes':'no',row);if(which!=='smoking'){permissions[which==='pets'?'petsAllowed':'childrenAllowed']=policy;put(which,policy,row);}}else{put(which==='pets'?'petPolicy':which+'Policy',/соглас|обсужд/i.test(value)?'negotiable':'unknown',row);if(which!=='smoking')put(which,false,row);}
  }
  if(key==='Мебель'&&/(?:^|,\s*)кухня(?:,|$)/i.test(value))equipment.hasKitchenFurniture=true;
  if(key==='Техника')for(const [pattern,flag] of [[/холодильник/i,'hasFridge'],[/стиральн/i,'hasWasher'],[/посудомоеч/i,'hasDishwasher'],[/телевизор/i,'hasTv'],[/кондиционер/i,'hasConditioner']])if(pattern.test(value)&&!/^нет|^без\b/i.test(value))equipment[flag]=true;
  if(key==='Интернет и ТВ'&&/wi[ -]?fi|интернет/i.test(value)&&!/^нет|^без\b/i.test(value))equipment.hasInternet=true;
 }
 const meters=records.find(r=>/^По сч[её]тчикам\s*:/.test(r.text)),other=records.find(r=>/^Другие ЖКУ\s*:/.test(r.text));
 if(other&&/:\s*включено/i.test(other.text))put('utilities',meters&&/отдельно/i.test(meters.text)?'meters':'included',other);
 else if(other&&/отдельно|не включено/i.test(other.text))put('utilities','extra',other);
 const routes=records.filter(r=>r.name==='UndergroundItem').map(r=>{const m=r.text.match(/^(.*?)\s+(\d+(?:[–—-]\d+)?)\s*мин/);return m?{name:m[1],time:m[2],mode:r.mode||'unknown'}:null;}).filter(Boolean);
 if(routes.length){put('metro',routes[0].name,{name:'UndergroundItem'});put('transit',[...new Set(routes.map(r=>[r.name,r.time,r.mode,''].join(' | ')))].join('\n'),{name:'UndergroundItem'});if(routes[0].mode==='walk')put('walk',routes[0].time,{name:'UndergroundItem'});else facts.walk='';if(routes.some(r=>r.mode==='unknown'))warnings.push('Авито: у части маршрутов не подписан способ пути.');}
 if(source.description)put('sourceDescription',source.description,{name:'Description'});
 if(source.url)put('url',source.url,{name:'URL'});if(facts.address)put('name',facts.address,{name:'AddressItem'});
 if(!source.images?.length)warnings.push('Авито: фото основного альбома пока не найдены; нужна загрузка галереи в Chrome.');
 return {facts,evidence,warnings,permissions,equipment};
}
