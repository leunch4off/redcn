import {photoSources} from '../media-core.mjs';
import {listingDocument,listingText,listingDescendants,listingClean} from './html.mjs';
import {rentalAmount,rentalPeriod,rentalCurrency,rentalCommission,rentalSeller,explicitRentalLayout} from './rental-values.mjs';

// Avito-specific selectors/labels stay here; generated CSS names are never used.
const AVITO_GROUPS={'О квартире':'apartment','Условия аренды':'rental','Правила':'rules','О доме':'building','Расположение':'location','Особенности':'highlights','Описание':'description'};
const avitoMark=node=>node.attrs['data-marker']||'';
const avitoHeading=node=>listingDescendants(node).find(n=>/^h[1-3]$/.test(n.tag));
const avitoNumber=text=>String(text||'').match(/\d[\d \u00a0]*(?:[.,]\d+)?/)?.[0]?.replace(/[ \u00a0]/g,'').replace(',','.');
function avitoUrl(value){try{const u=new URL(value);if(u.protocol!=='https:'||u.username||u.password||u.port||!/(^|\.)avito\.ru$/i.test(u.hostname))return '';u.search='';u.hash='';return u.href;}catch{return '';}}

export function avitoPublicationStatus(source){
 const statuses=(source.records||[]).filter(row=>row.name==='PublicationStatus').map(row=>row.text).join('\n');
 if(/в архиве|архивное|архивировано|archived/i.test(statuses))return 'archived';
 if(/снято|снят[ао]? с публикации|удалено|удал[её]н|недоступно|не существует|сдано|закрыто|removed|unavailable|not available/i.test(statuses))return 'removed';
 if(/активно|опубликовано|active|published/i.test(statuses))return 'active';
 // A title and a stale price alone are insufficient. The observed ordinary
 // listing needs apartment/rental details, not recommendations or a skeleton.
 return source.title&&(source.records||[]).some(row=>row.name==='PriceInfo')&&(source.sections||[]).some(section=>['apartment','rental'].includes(section.section))?'active':'unknown';
}
export const isAvitoNewCandidate=source=>avitoPublicationStatus(source)==='active';

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
 for(const n of nodes.filter(n=>/^item-view\/(?:item-status(?:\/[^ ]+)?|status|closed-warning|closed-message)$/.test(avitoMark(n))))add('PublicationStatus',listingText(n),{section:'status'});
 for(const n of nodes.filter(n=>/^item-view\/(?:seller-type|author-type)$/.test(avitoMark(n))))add('SellerType',listingText(n),{section:'author'});
 for(const n of marker('item-view/item-address'))add('AddressItem',listingText(n),{section:'location'});
 for(const n of nodes.filter(n=>/^item-view\/(?:district|item-district)$/.test(avitoMark(n))))add('District',listingText(n),{section:'location'});
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
    const icons=[route,...listingDescendants(route)],icon=icons.map(n=>(n.attrs['data-icon-name']||'')+' '+(n.attrs['aria-label']||'')+' '+(n.attrs.title||'')).join(' '),mode=/walkingroute|пешком|walking/i.test(icon)?'walk':/автобус|общественн|publictransport|busroute/i.test(icon)?'public':/на машине|автомобил|drivingroute|carroute/i.test(icon)?'car':'unknown';
    const link=icons.find(n=>n.tag==='a'&&n.attrs.href);
    add('UndergroundItem',m[1].trim()+' '+m[2].replace(/\s/g,'')+' мин.',{section,mode,href:link?.attrs.href||''});
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
 source.publicationStatus=avitoPublicationStatus(source);
 const extracted=avitoFieldFacts(source);source.permissions=extracted.permissions;source.equipment=extracted.equipment;
 return source;
}

export function avitoFieldFacts(source){
 const facts={commission:'',commissionStatus:'unknown',commissionSource:'',seller:'unknown',sellerSource:'',pricePeriod:'unknown',priceCurrency:'unknown'},evidence=[],warnings=[],permissions={},equipment={},records=source.records||[],oneTimeFees=[];
 const put=(field,value,row)=>{if(value===undefined||value===null||value==='')return;if(facts[field]!==undefined&&!['','unknown'].includes(facts[field])&&String(facts[field])!==String(value)){warnings.push('Авито: разные значения поля '+field+'; требуется проверка.');return;}facts[field]=value;evidence.push({field,value,source:'Авито · '+(row?.section||row?.name||'HTML'),sourceUrl:source.url||'',text:String(row?.text??value).slice(0,700)});};
 const number=(field,value,row)=>{const n=avitoNumber(value);if(n!==undefined)put(field,n,row);};
 const fields={'Количество комнат':'rooms','Количество спален':'bedrooms','Спальни':'bedrooms','Количество гостиных':'livingRooms','Общая площадь':'area','Площадь кухни':'kitchen','Жилая площадь':'living','Тип дома':'buildingType','Год постройки':'buildingYear','Этажей в доме':'floors','Санузел':'bathroom','Ремонт':'renovation','Парковка':'parking','Балкон или лоджия':'balconyType','Тип комнат':'roomType','Количество жильцов':'occupants','Мебель':'furnitureText','Техника':'equipmentText','Интернет и ТВ':'internetText','Пассажирский лифт':'passengerLifts','Грузовой лифт':'freightLifts','Высота потолков':'ceiling','Район':'district','Город':'city','Адрес':'address'};
 for(const row of records){
  const text=row.text.replace(/\s+/g,' ').trim();
  if(row.name==='PriceInfo'){const amount=rentalAmount(text);if(amount!=='')put('price',amount,row);put('pricePeriod',rentalPeriod(text),row);put('priceCurrency',rentalCurrency(text),row);}
  if(row.name==='OfferTitle'){
   const rooms=text.match(/(?:^|\s)(\d+)\s*-?\s*к[. -]/i),area=text.match(/(\d+(?:[.,]\d+)?)\s*м[²2]/i),floor=text.match(/(\d+)\s*\/\s*(\d+)\s*эт/i);
   if(rooms)put('rooms',rooms[1],row);if(area)put('area',area[1].replace(',','.'),row);if(floor){put('floor',floor[1],row);put('floors',floor[2],row);}const layout=explicitRentalLayout(text);if(layout)put('layout',layout,row);
  }
  if(row.name==='AddressItem'){put('address',text,row);if(/^Москва\b/u.test(text)||text.startsWith('Москва,'))put('city','Москва',row);const district=text.match(/(?:^|,\s*)((?:р-н|район)\s+[^,]+)/i);if(district)put('district',district[1],row);}
  if(row.name==='District')put('district',text,row);
  if(row.name==='SellerType'){const seller=rentalSeller(text);if(seller!=='unknown'){put('seller',seller,row);facts.sellerSource='Авито · '+(row.section||row.name);}}
  if(row.name==='Coordinates'){const [lat,lon]=text.split('|');put('latitude',lat,row);put('longitude',lon,row);}
  if(!['OfferSummaryInfoItem','Rules','FeaturesItem'].includes(row.name))continue;
  const m=text.match(/^([^:]+):\s*(.*)$/);if(!m)continue;const key=m[1].trim(),value=m[2].trim();
  const field=fields[key];if(field){if(['rooms','bedrooms','livingRooms','area','kitchen','living','buildingYear','floors','occupants','passengerLifts','freightLifts','ceiling'].includes(field))number(field,value,row);else put(field,value,row);}
  if(['Количество комнат','Тип планировки','Планировка'].includes(key)){const layout=explicitRentalLayout(value);if(layout)put('layout',layout,row);}
  if(key==='Этаж'){const f=value.match(/(\d+)\s*(?:из|\/)\s*(\d+)/);if(f){put('floor',f[1],row);put('floors',f[2],row);}}
  if(['Залог','Депозит','Обеспечительный платёж','Обеспечительный платеж'].includes(key)){
   if(/^(?:нет|без залога|не требуется)$/i.test(value)){put('deposit','0',row);put('depositCurrency',rentalCurrency(value),row);}
   else if(/%/.test(value)){const percentage=avitoNumber(value);if(percentage!==undefined)put('depositPercent',percentage,row);}
   else {const amount=rentalAmount(value);if(amount!==''){put('deposit',amount,row);put('depositCurrency',rentalCurrency(value),row);}}
  }
  if(key==='Комиссия'){const parsed=rentalCommission(value);if(parsed.percent!=='')put('commission',parsed.percent,row);facts.commissionStatus=parsed.status;facts.commissionSource='Авито · '+(row.section||row.name);if(parsed.amount){put('commissionAmount',parsed.amount,row);put('commissionCurrency',parsed.currency,row);warnings.push('Авито: комиссия указана суммой, а не процентом.');}}
  if(['Тип объявления','Продавец','Тип продавца','Автор объявления'].includes(key)){const seller=rentalSeller(value);if(seller!=='unknown'){put('seller',seller,row);facts.sellerSource='Авито · '+(row.section||row.name);}}
  if(/^(?:Уборка|Финальная уборка|Сервисный сбор|Разовый плат[её]ж|Разовый сбор|Плата за заселение)$/i.test(key)){
   const amount=rentalAmount(value);if(amount!==''&&!/%/.test(value))oneTimeFees.push({label:key,amount,currency:rentalCurrency(value),source:'Авито · '+(row.section||row.name)});
  }
  if(key==='Тип комнат'&&/изолир/i.test(value))put('separate',true,row);
  if(key==='Можно с детьми'||key==='Можно с питомцем'||key==='Можно с животными'||key==='Можно курить'){
   const policy=/^(?:нет|нельзя|запрещено)$/i.test(value)?false:/^(?:да|можно|разрешено)$/i.test(value)?true:null;
   const which=key==='Можно с детьми'?'children':key==='Можно курить'?'smoking':'pets';if(policy!==null){put(which==='pets'?'petPolicy':which+'Policy',policy?'yes':'no',row);if(which!=='smoking'){permissions[which==='pets'?'petsAllowed':'childrenAllowed']=policy;put(which,policy,row);}}else{put(which==='pets'?'petPolicy':which+'Policy',/соглас|обсужд/i.test(value)?'negotiable':'unknown',row);if(which!=='smoking')put(which,false,row);}
  }
  if(key==='Мебель'&&/(?:^|,\s*)кухня(?:,|$)/i.test(value))equipment.hasKitchenFurniture=true;
  if(key==='Техника')for(const [pattern,flag] of [[/холодильник/i,'hasFridge'],[/стиральн/i,'hasWasher'],[/посудомоеч/i,'hasDishwasher'],[/телевизор/i,'hasTv'],[/кондиционер/i,'hasConditioner']])if(pattern.test(value)&&!/^нет|^без\b/i.test(value))equipment[flag]=true;
  if(key==='Интернет и ТВ'&&/wi[ -]?fi|интернет/i.test(value)&&!/^нет|^без\b/i.test(value))equipment.hasInternet=true;
 }
 if(facts.deposit===undefined&&facts.depositPercent!==undefined&&facts.pricePeriod==='month'&&facts.priceCurrency==='RUB'&&facts.price!==undefined){const row={name:'Deposit percentage',text:facts.depositPercent+'% от '+facts.price+' ₽ за месяц'};put('deposit',String(Number(facts.price)*Number(facts.depositPercent)/100),row);put('depositCurrency','RUB',row);}
 if(oneTimeFees.length)put('oneTimeFees',oneTimeFees,{name:'rental',text:oneTimeFees.map(row=>row.label+': '+row.amount+' '+row.currency).join('; ')});
 facts.publicationStatus=avitoPublicationStatus(source);facts.eligibleForNew=isAvitoNewCandidate(source);
 evidence.push({field:'publicationStatus',value:facts.publicationStatus,source:'Авито · status',sourceUrl:source.url||'',text:records.filter(row=>row.name==='PublicationStatus').map(row=>row.text).join('; ')||'Наблюдаемые заголовок, цена и раздел параметров'});
 if(facts.commissionStatus==='unknown')warnings.push('Авито: процент комиссии не указан; ноль не предполагается.');
 if(facts.pricePeriod!=='unknown'&&facts.pricePeriod!=='month')warnings.push('Авито: цена за '+facts.pricePeriod+', а не за месяц. Нужна проверка периода перед публикацией.');
 if(facts.priceCurrency!=='unknown'&&facts.priceCurrency!=='RUB')warnings.push('Авито: исходная валюта '+facts.priceCurrency+'; конвертация в рубли не выполнялась.');
 if(['archived','removed'].includes(facts.publicationStatus))warnings.push('Авито: объявление '+(facts.publicationStatus==='archived'?'в архиве':'снято или недоступно')+'; в новые объекты оно не включается.');
 const meters=records.find(r=>/^По сч[её]тчикам\s*:/.test(r.text)),other=records.find(r=>/^Другие ЖКУ\s*:/.test(r.text));
 if(other&&/:\s*включено/i.test(other.text))put('utilities',meters&&/отдельно/i.test(meters.text)?'meters':'included',other);
 else if(other&&/отдельно|не включено/i.test(other.text))put('utilities','extra',other);
 const routes=records.filter(r=>r.name==='UndergroundItem').map(r=>{const m=r.text.match(/^(.*?)\s+(\d+(?:[–—-]\d+)?)\s*мин/);return m?{id:null,name:m[1],time:m[2],mode:r.mode||'unknown',line:'',source:'Авито · UndergroundItem',url:r.href||''}:null;}).filter(Boolean);
 if(routes.length){put('metro',routes[0].name,{name:'UndergroundItem'});put('transit',[...new Set(routes.map(r=>[r.name,r.time,r.mode,''].join(' | ')))].join('\n'),{name:'UndergroundItem'});if(routes[0].mode==='walk')put('walk',routes[0].time,{name:'UndergroundItem'});else facts.walk='';if(routes.some(r=>r.mode==='unknown'))warnings.push('Авито: у части маршрутов не подписан способ пути.');}
 if(routes.length)put('transitDetails',routes,{name:'UndergroundItem'});
 if(source.description)put('sourceDescription',source.description,{name:'Description'});
 if(source.url)put('url',source.url,{name:'URL'});if(facts.address)put('name',facts.address,{name:'AddressItem'});
 if(!source.images?.length)warnings.push('Авито: фото основного альбома пока не найдены; нужна загрузка галереи в Chrome.');
 return {facts,evidence,warnings,permissions,equipment};
}
