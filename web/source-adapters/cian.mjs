import {photoSources,photoSourceUrl,observedPhotoItem,observedPhotoPicture,mergeObservedPhotoItems} from '../media-core.mjs';
import {readRelatedHtml} from '../related.mjs';
import {rentalNumber,rentalAmount,rentalPeriod,rentalCurrency,rentalCommission,rentalSeller,explicitRentalLayout,retainRentalWitness,rentalWitnessConflicts} from './rental-values.mjs';
// Only data-name / id / data-marker and literal JSON data are read. No page code runs.
const SOURCE_NAMES=new Set(['Description','OfferDescription','OfferTitleNew','OfferTitle','ObjectFactoidsItem','OfferSummaryInfoItem','FeaturesItem','FeaturesLayout','PriceInfo','OfferFactItem','AddressItem','AddressContainer','UndergroundItem','HomeownerInfo','HomeownerLayout','NoFeeLabelContainer','SellerType','AgentType']);
const sourceDecode=s=>String(s||'').replace(/&(#x[\da-f]+|#\d+|nbsp|amp|quot|apos|lt|gt);/gi,(_,key)=>{if(key[0]==='#'){const n=key[1].toLowerCase()==='x'?parseInt(key.slice(2),16):parseInt(key.slice(1),10);return n>0&&n<=0x10ffff?String.fromCodePoint(n):'';}return ({nbsp:' ',amp:'&',quot:'"',apos:"'",lt:'<',gt:'>'})[key.toLowerCase()];});
const sourceClean=s=>sourceDecode(s).replace(/[ \t\r\f\u00a0]+/g,' ').replace(/\n\s*\n/g,'\n').trim();
const sourceVoid=new Set(['area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr']);
const cianTravelMode=value=>({walk:'walk',byFoot:'walk',transport:'public',public:'public',bus:'bus',byCar:'car',car:'car'})[value]||'unknown';
const cianTravelTime=value=>typeof value==='number'||typeof value==='string'?/^(?:\d+(?:[.,]\d+)?)(?:\s*[–—-]\s*\d+(?:[.,]\d+)?)?$/.test(String(value).trim())?String(value).replace(/\s/g,'').replace(/,/g,'.'):null:null;

export function readCianHtml(input) {
  if(typeof input!=='string'||input.length>8_000_000)throw Error('HTML должен быть текстом размером до 8 МБ');
  const at=input.search(/<!doctype\s+html|<html\b/i),html=at>=0?input.slice(at):input;
  const records=[],ld=[],offers=[],images=[],galleryImages=[],galleryItems=[],stack=[],seen=new Set();let title='';
  for(const match of html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script\s*>/gi)){
    const code=match[2];
    if(/application\/ld\+json/i.test(match[1])){try{ld.push(JSON.parse(code));}catch{}}
    if(/_cianConfig\[['"]frontend-offer-card['"]\]/.test(code)){
      const m=code.match(/\.concat\((\[[\s\S]*\])\)\s*;?\s*$/);
      if(m)try{const config=JSON.parse(m[1]);for(const row of config){if(row.key!=='defaultState'||!row.value||typeof row.value!=='object')continue;const offer=row.value.offerData?.offer;if(offer&&typeof offer==='object')offers.push(offer);}}catch{}
    }
  }
  const clean=html.replace(/<!--[^]*?-->/g,'').replace(/<(script|style|noscript)\b[^>]*>[\s\S]*?<\/\1\s*>/gi,'').replace(/<svg\b([^>]*)>[\s\S]*?<\/svg\s*>/gi,'<svg$1></svg>');
  const finish=node=>{if(node.pictureAttrs?.length){const item=observedPhotoPicture(node.pictureAttrs,{platform:'cian',order:galleryItems.length});if(item)galleryItems.push(item);}if(!node.capture)return;const text=sourceClean(node.parts.join('')).replace(/\s*(?:Свернуть|Развернуть)\s*$/,'');if(node.name==='title'&&!title)title=text;const key=node.name+'|'+text+'|'+(node.href||'')+'|'+(node.mode||'');if(text&&!seen.has(key)){seen.add(key);records.push({name:node.name,text,href:node.href||'',...(node.mode?{mode:node.mode}:{})});}};
  for(const token of clean.matchAll(/<[^>]*>|[^<]+/g)){
    const value=token[0];
    if(value[0]!=='<'){for(const node of stack)if(node.capture&&node.length<50000){node.parts.push(value);node.length+=value.length;}continue;}
    const end=value.match(/^<\/([\w:-]+)/);if(end){let i=stack.length-1;while(i>=0&&stack[i].tag!==end[1].toLowerCase())i--;if(i>=0)for(const node of stack.splice(i).reverse())finish(node);continue;}
    const start=value.match(/^<([\w:-]+)/);if(!start)continue;const tag=start[1].toLowerCase(),attrs={};
    for(const a of value.matchAll(/([\w:-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))/g))attrs[a[1].toLowerCase()]=sourceDecode(a[2]??a[3]??a[4]);
    let name=attrs['data-name']||'';if(attrs.id==='description')name='Description';if(tag==='title')name='title';if(tag==='h1'&&!name)name='OfferTitle';
    const excluded=stack.some(n=>n.excluded)||/similar|recommend/i.test((attrs['data-name']||'')+' '+(attrs['data-marker']||''));
    const gallery=!excluded&&(stack.some(n=>n.gallery)||['OfferGallery','Gallery'].includes(name));
    if(!excluded)for(const node of stack)if(node.name==='UndergroundItem'){
      if(tag==='a'&&attrs.href&&!node.href)node.href=attrs.href;
      const icon=(attrs['aria-label']||'')+' '+(attrs.title||'')+' '+(attrs['data-icon-name']||'');
      if(/пешком|walk/i.test(icon))node.mode='walk';else if(/автобус|общественн|transport|bus/i.test(icon))node.mode='public';else if(/автомобил|машине|bycar/i.test(icon))node.mode='car';
    }
    if(tag==='meta'&&/^(og:image|twitter:image)$/i.test(attrs.property||attrs.name||''))images.push(attrs.content);
    // A scoped gallery is a real fallback when the offer JSON is absent or its
    // shape changes. One OG/share image must not silently replace 19 gallery
    // photos. Prefer the largest declared candidate for each image node.
    if(gallery&&['img','source','a'].includes(tag)&&attrs['data-is-layout']!=='true'){
      const srcset=(attrs['data-srcset']||attrs.srcset||'').split(',').map(part=>{const [url,size='0']=part.trim().split(/\s+/);return {url,weight:parseFloat(size)||0};}).sort((a,b)=>b.weight-a.weight);
      const candidates=tag==='a'?[/\.(?:jpe?g|png|webp)(?:[?#]|$)/i.test(attrs.href||'')?attrs.href:null]:[attrs['data-original'],attrs['data-src'],...srcset.map(row=>row.url),attrs.src];
      const candidate=candidates.map(value=>photoSourceUrl(value)).find(Boolean);if(candidate)galleryImages.push(candidate);
      const picture=tag!=='a'?[...stack].reverse().find(node=>node.tag==='picture'):null;
      if(picture)picture.pictureAttrs.push({...picture.pictureIdentity,...attrs});
      else{const observed=observedPhotoItem({...attrs,...(tag==='a'?{imageHref:candidate}:{})},{platform:'cian',order:galleryItems.length});if(observed)galleryItems.push(observed);}
    }
    if(tag==='br'||/^(div|p|li|h[1-6]|span|dt|dd)$/.test(tag))for(const node of stack)if(node.capture)node.parts.push('\n');
    if(!sourceVoid.has(tag)&&!value.endsWith('/>'))stack.push({tag,name,excluded,gallery,...(tag==='picture'?{pictureAttrs:[],pictureIdentity:{'data-photo-id':attrs['data-photo-id'],'data-image-id':attrs['data-image-id']}}:{}),capture:!excluded&&(SOURCE_NAMES.has(name)||name==='title'),parts:[],length:0,href:''});
  }
  for(const node of stack.reverse())finish(node);
  const first=name=>records.find(r=>r.name===name)?.text||'';
  const offer=offers[0]||null;
  const description=first('Description')||offer?.description||'';
  const picked=records.filter(r=>SOURCE_NAMES.has(r.name)&&!['FeaturesLayout','AddressContainer','HomeownerLayout'].includes(r.name));
  let text=picked.map(r=>r.text).join('\n');
  if(!text)for(const item of ld){if(typeof item?.description==='string')text+=item.description+'\n';}
  const mediaItems=[],mediaSeen=new Set();
  const addPhoto=(photo,order,layout=false)=>{
    if(!photo||typeof photo!=='object')return;
    const variants={};for(const key of ['fullUrl','thumbnailUrl','thumbnail2Url','miniUrl']){const value=photoSourceUrl(photo[key]);if(value)variants[key]=value;}
    const url=variants.fullUrl||variants.thumbnailUrl||variants.thumbnail2Url||variants.miniUrl;if(!url||mediaSeen.has(url))return;
    mediaSeen.add(url);mediaItems.push({url,photoId:String(photo.id??'').slice(0,40),order,isLayout:layout||photo.isLayout===true,isCianLayout:layout||photo.isCianLayout===true,variants,source:'CIAN offer'});
  };
  for(const [index,photo]of(Array.isArray(offer?.photos)?offer.photos:[]).entries())addPhoto(photo,index);
  for(const [index,photo]of(Array.isArray(offer?.cianLayouts)?offer.cianLayouts:[]).entries())addPhoto(photo,index,true);
  const layouts=mediaItems.filter(p=>p.isLayout||p.isCianLayout),photoItems=mergeObservedPhotoItems(mediaItems.filter(p=>!p.isLayout&&!p.isCianLayout),galleryItems,layouts);
  if(!photoItems.length)for(const item of ld){if(Array.isArray(item?.image))images.push(...item.image);else if(typeof item?.image==='string')images.push(item.image);}
  const layoutUrls=new Set(layouts.flatMap(p=>Object.values(p.variants)));
  const safeImages=photoItems.length?photoItems.map(p=>p.url):photoSources(galleryImages.length?galleryImages:images).filter(url=>!layoutUrls.has(url));
  const url=ld.find(x=>typeof x?.offers?.url==='string')?.offers.url||(offer?.cianId?'https://www.cian.ru/rent/flat/'+offer.cianId+'/':'');
  // Keep a strict allowlist: no phones, account IDs, tokens, tracking or site config.
  const selected={};if(offer)for(const key of ['cianId','description','geo','isByHomeowner','isByAgent','totalArea','livingArea','kitchenArea','roomsCount','bedroomsCount','livingRoomsCount','layoutType','floorNumber','building','loggiasCount','balconiesCount','windowsViewType','separateWcsCount','combinedWcsCount','repairType','bargainTerms','hasFridge','hasDishwasher','hasWasher','hasFurniture','hasKitchenFurniture','hasConditioner','hasTv','hasInternet','hasBathtub','hasShower','hasLift','creationDate','publicationDate','editDate','timezone'])if(offer[key]!==undefined)selected[key]=offer[key];
  const ldRows=ld.flatMap(item=>Array.isArray(item)?item:Array.isArray(item?.['@graph'])?item['@graph']:[item]);
  const entity=ldRows.find(item=>item&&typeof item==='object'&&(/Apartment|Residence|RealEstateListing|Product/.test(String(item['@type']||''))||item.offers?.price!==undefined));
  const ldOffer=entity?.offers&&!Array.isArray(entity.offers)?entity.offers:{};
  const specification=ldOffer?.priceSpecification||{};
  const jsonLd={};for(const [key,value]of Object.entries({price:ldOffer.price??specification.price,currency:ldOffer.priceCurrency??specification.priceCurrency,paymentPeriod:specification.billingDuration??specification.unitText??specification.unitCode,rooms:entity?.numberOfRooms,bedrooms:entity?.numberOfBedrooms,area:entity?.floorSize?.value}))if(['string','number'].includes(typeof value))jsonLd[key]=value;
  return {version:1,platform:'cian',title,description,text:sourceClean(text).slice(0,50000),url,images:safeImages,photoItems,layouts,related:readRelatedHtml(html,url,url),records:picked,offer:offer?selected:null,jsonLd,stats:{records:records.length,jsonStates:offers.length,duplicateDocuments:(html.match(/<!doctype\s+html/gi)||[]).length,adapter:'cian-v2-gallery-variants'}};
}

export function cianFieldFacts(source) {
  const facts={},evidence=[],warnings=[],conflicts=[],blockedFields=[],o=source.offer||{},records=source.records||[];
  const witness=(field,value,where,raw,scope={})=>{if(value===undefined||value===null||value==='')return;return retainRentalWitness(evidence,{field,value:typeof value==='number'?String(value):value,source:where,sourceUrl:source.url||'',text:String(raw??value).slice(0,700),...scope});};
  const set=(field,value,where,raw,scope)=>{if(value===undefined||value===null||value==='')return;facts[field]=typeof value==='number'?String(value):value;witness(field,facts[field],where,raw,scope);};
  const observe=(field,value,where,raw,scope)=>{if(facts[field]===undefined||facts[field]==='unknown')set(field,value,where,raw,scope);else witness(field,value,where,raw,scope);};
  for(const [field,key] of Object.entries({area:'totalArea',living:'livingArea',kitchen:'kitchenArea',rooms:'roomsCount',bedrooms:'bedroomsCount',livingRooms:'livingRoomsCount',floor:'floorNumber',balconies:'balconiesCount',loggias:'loggiasCount',combinedWcs:'combinedWcsCount',separateWcs:'separateWcsCount'})){
    const raw=o[key];
    if(raw===undefined||raw===null||typeof raw==='boolean'||String(raw).trim()==='')continue;
    const value=Number(String(raw).replace(',','.'));
    if(Number.isFinite(value)&&value>=0&&(!['rooms','bedrooms','livingRooms','floor','balconies','loggias','combinedWcs','separateWcs'].includes(field)||Number.isInteger(value)))set(field,String(value),'JSON offer.'+key,raw);
  }
  if(typeof o.hasLift==='boolean')set('lift',o.hasLift?'yes':'no','JSON hasLift');
  if(o.geo?.coordinates){set('latitude',o.geo.coordinates.lat,'JSON geo.coordinates.lat');set('longitude',o.geo.coordinates.lng,'JSON geo.coordinates.lng');}
  if(o.building?.floorsCount!==undefined)set('floors',o.building.floorsCount,'JSON building.floorsCount');
  const material={brick:'Кирпичный',panel:'Панельный',monolith:'Монолитный',block:'Блочный',monolithBrick:'Монолитно-кирпичный'}[o.building?.materialType||o.building?.houseMaterialType];
  if(material)set('buildingType',material,'JSON building.materialType');
  if(o.building?.buildYear!==undefined)set('buildingYear',o.building.buildYear,'JSON building.buildYear');
  if(o.building?.ceilingHeight!==undefined)set('ceiling',o.building.ceilingHeight,'JSON building.ceilingHeight');

  const b=o.bargainTerms||{};for(const [f,k] of [['price','price'],['deposit','deposit'],['prepayMonths','prepayMonths']]){const value=rentalNumber(b[k]);if(value!=='')set(f,value,'JSON bargainTerms.'+k,b[k],f==='price'?{currency:rentalCurrency(b.currency),period:rentalPeriod(b.paymentPeriod)}:{});}
  if(facts.deposit!==undefined)set('depositCurrency',rentalCurrency(b.depositCurrency??b.currency),'JSON bargainTerms.depositCurrency / currency',b.depositCurrency??b.currency??'unknown');
  const explicitFee=rentalNumber(b.clientFee);
  facts.commission='';facts.commissionStatus='unknown';facts.commissionSource='';
  if(explicitFee!==''){set('commission',explicitFee,'JSON bargainTerms.clientFee',b.clientFee);facts.commissionStatus=Number(explicitFee)===0?'none':'present';facts.commissionSource='JSON bargainTerms.clientFee';}
  facts.seller='unknown';facts.sellerSource='';
  if(o.isByHomeowner===true){set('seller','owner','JSON isByHomeowner',true);facts.sellerSource='JSON isByHomeowner';}
  else if(o.isByAgent===true){set('seller','agent','JSON isByAgent',true);facts.sellerSource='JSON isByAgent';}
  const period=rentalPeriod(b.paymentPeriod),currency=rentalCurrency(b.currency);
  facts.pricePeriod=period;facts.priceCurrency=currency;
  if(period!=='unknown')set('pricePeriod',period,'JSON bargainTerms.paymentPeriod',b.paymentPeriod);
  if(currency!=='unknown')set('priceCurrency',currency,'JSON bargainTerms.currency',b.currency);
  const ld=source.jsonLd||{};
  for(const [field,key]of [['price','price'],['rooms','rooms'],['bedrooms','bedrooms'],['area','area']]){const value=rentalNumber(ld[key]);if(value!=='')observe(field,value,'JSON-LD '+key,ld[key],field==='price'?{currency:rentalCurrency(ld.currency),period:rentalPeriod(ld.paymentPeriod)}:{});}
  if(rentalPeriod(ld.paymentPeriod)!=='unknown')observe('pricePeriod',rentalPeriod(ld.paymentPeriod),'JSON-LD priceSpecification',ld.paymentPeriod);
  if(rentalCurrency(ld.currency)!=='unknown')observe('priceCurrency',rentalCurrency(ld.currency),'JSON-LD priceCurrency',ld.currency);
  const layout=explicitRentalLayout([o.layoutType,...records.filter(r=>['OfferTitle','OfferTitleNew'].includes(r.name)).map(r=>r.text),source.description].filter(Boolean).join('\n'));
  if(layout)set('layout',layout,'CIAN explicit layout');
  if(b.utilitiesTerms){const u=b.utilitiesTerms;if(u.includedInPrice===true)set('utilities',u.flowMetersNotIncludedInPrice?'meters':'included','JSON utilitiesTerms');else if(u.includedInPrice===false)set('utilities','extra','JSON utilitiesTerms');}
  if(b.leaseTermType==='longTerm')set('leaseTerm','long','JSON leaseTermType');
  const addresses=o.geo?.address||[];
  if(addresses.length){set('address',addresses.map(a=>a.shortName||a.fullName||a.name).filter(Boolean).join(', '),'JSON geo.address');const district=addresses.find(a=>a.type==='raion');if(district)set('district',district.fullName||district.name,'JSON geo.address');const city=addresses.find(a=>a.type==='location');if(city)set('city',city.name,'JSON geo.address');}
  if(source.url)set('url',source.url,'JSON-LD / cianId');
  const labels={'Общая площадь':'area','Жилая площадь':'living','Площадь кухни':'kitchen','Количество комнат':'rooms','Количество спален':'bedrooms','Спальни':'bedrooms','Количество гостиных':'livingRooms','Год постройки':'buildingYear','Тип дома':'buildingType','Тип перекрытий':'floorType','Строительная серия':'buildingSeries','Подъезды':'entrances','Отопление':'heating','Аварийность':'emergency','Санузел':'bathroom','Вид из окон':'windows','Ремонт':'renovation','Высота потолков':'ceiling'};
  for(const r of records.filter(r=>['ObjectFactoidsItem','OfferSummaryInfoItem'].includes(r.name))){const text=r.text.replace(/\s+/g,' ').replace(/:\s*/, ' ');if(/^Этаж\s/.test(text)){const m=text.match(/Этаж\s+(\d+)\s+из\s+(\d+)/);if(m){observe('floor',m[1],r.name,text);observe('floors',m[2],r.name,text);}}for(const [label,field] of Object.entries(labels))if(text.startsWith(label+' ')){let value=text.slice(label.length).trim();if(['area','living','kitchen','ceiling','rooms','bedrooms','livingRooms'].includes(field))value=value.match(/\d+(?:[.,]\d+)?/)?.[0]?.replace(',','.');witness(field,value,r.name,text);if(value!==undefined&&facts[field]!==undefined&&String(facts[field]).toLowerCase()!==String(value).toLowerCase()){
      const earlier=evidence.find(e=>e.field===field);conflicts.push({field,status:'conflict',resolution:'operator_required',candidates:[earlier,{field,value,source:r.name,text}]});
      if(!blockedFields.includes(field))blockedFields.push(field);warnings.push('Противоречие '+field+': '+facts[field]+' / '+value+'. Требуется проверка.');
    }else if(facts[field]===undefined)set(field,value,r.name,text);}}
  for(const row of records){const text=row.text.replace(/\s+/g,' ').trim();
    if(row.name==='PriceInfo'){
      const value=rentalAmount(text);if(value!=='')observe('price',value,'PriceInfo',text,{currency:rentalCurrency(text),period:rentalPeriod(text)});
      if(rentalPeriod(text)!=='unknown')observe('pricePeriod',rentalPeriod(text),'PriceInfo',text);
      if(rentalCurrency(text)!=='unknown')observe('priceCurrency',rentalCurrency(text),'PriceInfo',text);
    }
    if(['OfferTitle','OfferTitleNew'].includes(row.name)){const count=text.match(/(?:^|\s)(\d+)\s*[- ]?\s*(?:комн|комнат|к\.)/i);if(count)observe('rooms',count[1],row.name,text);}
    if(row.name==='OfferFactItem'){
      const deposit=text.match(/^(?:Залог|Депозит)\s*:?\s*(.*)$/i);if(deposit&&!/%/.test(deposit[1])){const value=/^(?:нет|без залога)$/i.test(deposit[1])?'0':rentalAmount(deposit[1]),currency=rentalCurrency(deposit[1]);if(value!==''){witness('deposit',value,row.name,text);witness('depositCurrency',currency,row.name,text);}if(value!==''&&facts.deposit===undefined){set('deposit',value,row.name,text);set('depositCurrency',currency,row.name,text);}else if(value!==''&&facts.deposit===value&&currency!=='unknown'&&facts.depositCurrency==='unknown')set('depositCurrency',currency,row.name,text);}
      const fee=text.match(/^Комиссия(?: с арендатора)?\s*:?\s*(.*)$/i);
      if(fee){const parsed=rentalCommission(fee[1]);if(parsed.percent!=='')witness('commission',parsed.percent,row.name,text);if(parsed.amount){witness('commissionAmount',parsed.amount,row.name,text);witness('commissionCurrency',parsed.currency,row.name,text);}if(facts.commissionStatus==='unknown'){if(parsed.percent!=='')set('commission',parsed.percent,row.name,text);facts.commissionStatus=parsed.status;facts.commissionSource=row.name;if(parsed.amount){set('commissionAmount',parsed.amount,row.name,text);set('commissionCurrency',parsed.currency,row.name,text);}}}
    }
    if(['HomeownerInfo','SellerType','AgentType'].includes(row.name)){const role=rentalSeller(text.replace(/^(?:Автор|Продавец|Тип продавца)\s*:?\s*/i,''));if(role!=='unknown'){if(facts.seller==='unknown'){set('seller',role,row.name,text);facts.sellerSource=row.name;}else witness('seller',role,row.name,text);}}
  }
  if(facts.commissionStatus==='unknown')warnings.push('ЦИАН: процент комиссии не указан; ноль не предполагается.');
  if(facts.pricePeriod!=='unknown'&&facts.pricePeriod!=='month')warnings.push('ЦИАН: цена за '+facts.pricePeriod+', а не за месяц. Нужна проверка периода перед публикацией.');
  if(facts.priceCurrency!=='unknown'&&facts.priceCurrency!=='RUB')warnings.push('ЦИАН: исходная валюта '+facts.priceCurrency+'; конвертация в рубли не выполнялась.');
  if(!facts.address){const a=records.filter(r=>r.name==='AddressItem').map(r=>r.text);if(a.length)set('address',[...new Set(a)].join(', '),'AddressItem');}
  const routes=(Array.isArray(o.geo?.undergrounds)?o.geo.undergrounds:[]).filter(r=>r&&typeof r.name==='string'&&r.name.trim());
  if(routes.length){const lines=routes.map(r=>[r.name,cianTravelTime(r.travelTime)??'',cianTravelMode(r.travelType),r.lineType==='mcd1'?'D1':r.lineType||''].join(' | '));set('transit',lines.join('\n'),'JSON geo.undergrounds');set('metro',routes[0].name,'JSON geo.undergrounds[0]');if(cianTravelMode(routes[0].travelType)==='walk')set('walk',cianTravelTime(routes[0].travelTime),'JSON geo.undergrounds[0]');else facts.walk='';}
  else{const details=records.filter(r=>r.name==='UndergroundItem').map(r=>{const m=r.text.match(/^(.*?)\s+(\d+(?:\s*[–—-]\s*\d+)?)\s*мин/);return m?{id:null,name:m[1],time:m[2].replace(/\s/g,''),mode:r.mode||'unknown',line:'',source:'CIAN UndergroundItem',url:r.href||''}:null;}).filter(Boolean);if(details.length){set('transit',[...new Set(details.map(r=>[r.name,r.time,r.mode,''].join(' | ')))].join('\n'),'UndergroundItem');set('metro',details[0].name,'UndergroundItem');if(details[0].mode==='walk')set('walk',details[0].time,'UndergroundItem');else facts.walk='';set('transitDetails',details,'CIAN UndergroundItem');if(details.some(r=>r.mode==='unknown'))warnings.push('Способ пути в текстовом DOM не подписан: требуется проверка, пеший маршрут не предполагается.');}}
  if(o.geo?.railways?.length)set('railways',o.geo.railways.map(r=>[r.name,r.time??'',({byFoot:'walk',byCar:'car'})[r.travelType]||'unknown'].join(' | ')).join('\n'),'JSON geo.railways');
  const percent=source.description?.match(/(?:залог|депозит)\s*(?:составляет\s*)?([0-9]+(?:[.,][0-9]+)?)\s*%/i);
  if(percent&&facts.price&&facts.deposit!==undefined&&facts.priceCurrency!=='unknown'&&facts.priceCurrency===facts.depositCurrency){const amount=Number(facts.price)*Number(percent[1].replace(',','.'))/100;
    if(Number.isFinite(amount)&&Number(facts.deposit)!==amount){conflicts.push({field:'deposit',status:'conflict',resolution:'operator_required',candidates:[evidence.find(e=>e.field==='deposit'),{field:'deposit',value:String(amount),units:'RUB',source:'Описание: процент от месячной аренды',text:percent[0]}]});blockedFields.push('deposit');warnings.push('Залог противоречив: JSON '+facts.deposit+' ₽, '+percent[1]+'% от '+facts.price+' ₽ = '+amount+' ₽. Требуется подтверждение.');}
  }
  if(source.photoItems?.length)set('photoItems',source.photoItems,'CIAN offer.photos');
  if(source.layouts?.length)set('layouts',source.layouts,'CIAN offer.cianLayouts');
  if(o.creationDate||o.publicationDate||o.editDate)set('sourceDates',{created:o.creationDate||null,published:o.publicationDate??null,updated:o.editDate||null,timezone:o.timezone??null},'CIAN source dates');
  if(routes.length)set('transitDetails',routes.map(r=>({id:r.id??null,name:r.name,line:r.lineType||'',color:r.lineColor||'',mode:cianTravelMode(r.travelType),time:cianTravelTime(r.travelTime),source:'CIAN geo.undergrounds',url:typeof r.url==='string'?r.url:''})),'CIAN transport identity');
  for(const field of blockedFields)facts[field]='';
  if(source.description)set('sourceDescription',source.description,'#description / [data-name="Description"]');
  if(facts.address)set('name',facts.address,'Адрес объекта');
  for(const conflict of rentalWitnessConflicts(evidence,'ЦИАН')){if(!conflicts.some(row=>row.field===conflict.field))conflicts.push(conflict);if(!warnings.includes(conflict.message))warnings.push(conflict.message);}
  return {facts,evidence,warnings,conflicts,blockedFields};
}
