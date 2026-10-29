// Only data-name / id / data-marker and literal JSON data are read. No page code runs.
const SOURCE_NAMES=new Set(['Description','OfferDescription','OfferTitleNew','OfferTitle','ObjectFactoidsItem','OfferSummaryInfoItem','FeaturesItem','FeaturesLayout','PriceInfo','OfferFactItem','AddressItem','AddressContainer','UndergroundItem','HomeownerInfo','HomeownerLayout','NoFeeLabelContainer']);
const sourceDecode=s=>String(s||'').replace(/&(#x[\da-f]+|#\d+|nbsp|amp|quot|apos|lt|gt);/gi,(_,key)=>{if(key[0]==='#'){const n=key[1].toLowerCase()==='x'?parseInt(key.slice(2),16):parseInt(key.slice(1),10);return n>0&&n<=0x10ffff?String.fromCodePoint(n):'';}return ({nbsp:' ',amp:'&',quot:'"',apos:"'",lt:'<',gt:'>'})[key.toLowerCase()];});
const sourceClean=s=>sourceDecode(s).replace(/[ \t\r\f\u00a0]+/g,' ').replace(/\n\s*\n/g,'\n').trim();
const sourceVoid=new Set(['area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr']);

export function readSourceHtml(input) {
  if(typeof input!=='string'||input.length>8_000_000)throw Error('HTML должен быть текстом размером до 8 МБ');
  const at=input.search(/<!doctype\s+html|<html\b/i),html=at>=0?input.slice(at):input;
  const records=[],ld=[],offers=[],images=[],stack=[],seen=new Set();let title='';
  for(const match of html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script\s*>/gi)){
    const code=match[2];
    if(/application\/ld\+json/i.test(match[1])){try{ld.push(JSON.parse(code));}catch{}}
    if(/_cianConfig\[['"]frontend-offer-card['"]\]/.test(code)){
      const m=code.match(/\.concat\((\[[\s\S]*\])\)\s*;?\s*$/);
      if(m)try{const config=JSON.parse(m[1]);for(const row of config){if(row.key!=='defaultState'||!row.value||typeof row.value!=='object')continue;const offer=row.value.offerData?.offer;if(offer&&typeof offer==='object')offers.push(offer);}}catch{}
    }
  }
  const clean=html.replace(/<!--[^]*?-->/g,'').replace(/<(script|style|svg|noscript)\b[^>]*>[\s\S]*?<\/\1\s*>/gi,'');
  const finish=node=>{if(!node.capture)return;const text=sourceClean(node.parts.join('')).replace(/\s*(?:Свернуть|Развернуть)\s*$/,'');if(node.name==='title'&&!title)title=text;const key=node.name+'|'+text+'|'+(node.href||'');if(text&&!seen.has(key)){seen.add(key);records.push({name:node.name,text,href:node.href||''});}};
  for(const token of clean.matchAll(/<[^>]*>|[^<]+/g)){
    const value=token[0];
    if(value[0]!=='<'){for(const node of stack)if(node.capture&&node.length<50000){node.parts.push(value);node.length+=value.length;}continue;}
    const end=value.match(/^<\/([\w:-]+)/);if(end){let i=stack.length-1;while(i>=0&&stack[i].tag!==end[1].toLowerCase())i--;if(i>=0)for(const node of stack.splice(i).reverse())finish(node);continue;}
    const start=value.match(/^<([\w:-]+)/);if(!start)continue;const tag=start[1].toLowerCase(),attrs={};
    for(const a of value.matchAll(/([\w:-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))/g))attrs[a[1].toLowerCase()]=sourceDecode(a[2]??a[3]??a[4]);
    let name=attrs['data-name']||'';if(attrs.id==='description')name='Description';if(tag==='title')name='title';if(tag==='h1'&&!name)name='OfferTitle';
    if(attrs['data-marker']?.includes('item-description'))name='Description';if(attrs['data-marker']?.includes('item-price'))name='PriceInfo';if(attrs['data-marker']?.includes('item-params'))name='OfferSummaryInfoItem';
    if(tag==='a'&&attrs.href)for(const node of stack)if(node.name==='UndergroundItem'&&!node.href)node.href=attrs.href;
    if(tag==='meta'&&/^(og:image|twitter:image)$/i.test(attrs.property||attrs.name||''))images.push(attrs.content);
    if(tag==='br'||/^(div|p|li|h[1-6]|span|dt|dd)$/.test(tag))for(const node of stack)if(node.capture)node.parts.push('\n');
    if(!sourceVoid.has(tag)&&!value.endsWith('/>'))stack.push({tag,name,capture:SOURCE_NAMES.has(name)||name==='title',parts:[],length:0,href:''});
  }
  for(const node of stack.reverse())finish(node);
  const first=name=>records.find(r=>r.name===name)?.text||'';
  const offer=offers[0]||null;
  const description=first('Description')||offer?.description||'';
  const picked=records.filter(r=>SOURCE_NAMES.has(r.name)&&!['FeaturesLayout','AddressContainer','HomeownerLayout'].includes(r.name));
  let text=picked.map(r=>r.text).join('\n');
  if(!text)for(const item of ld){if(typeof item?.description==='string')text+=item.description+'\n';}
  for(const item of ld){if(Array.isArray(item?.image))images.push(...item.image);else if(typeof item?.image==='string')images.push(item.image);}
  if(offer?.photos)for(const p of offer.photos)if(typeof p.fullUrl==='string')images.push(p.fullUrl);
  const safeImages=[...new Set(images.filter(v=>typeof v==='string'&&/^https:\/\/([\w-]+\.)*(?:cian\.ru|cdn-cian\.ru|ciancdn\.ru|avito\.ru|avito\.st)\//i.test(v)))].slice(0,30);
  const url=ld.find(x=>typeof x?.offers?.url==='string')?.offers.url||(offer?.cianId?'https://www.cian.ru/rent/flat/'+offer.cianId+'/':'');
  // Keep a strict allowlist: no phones, account IDs, tokens, tracking or site config.
  const selected={};if(offer)for(const key of ['cianId','description','geo','isByHomeowner','totalArea','livingArea','kitchenArea','roomsCount','floorNumber','building','loggiasCount','balconiesCount','windowsViewType','separateWcsCount','combinedWcsCount','repairType','bargainTerms','hasFridge','hasDishwasher','hasWasher','hasFurniture','hasKitchenFurniture','hasConditioner','hasTv','hasInternet','hasBathtub','hasShower','hasLift'])if(offer[key]!==undefined)selected[key]=offer[key];
  return {version:1,title,description,text:sourceClean(text).slice(0,50000),url,images:safeImages,records:picked,offer:offer?selected:null,stats:{records:records.length,jsonStates:offers.length,duplicateDocuments:(html.match(/<!doctype\s+html/gi)||[]).length}};
}

export function sourceFieldFacts(source) {
  const facts={},evidence=[],warnings=[],o=source.offer||{},records=source.records||[];
  const set=(field,value,where,raw)=>{if(value===undefined||value===null||value==='')return;facts[field]=typeof value==='number'?String(value):value;evidence.push({field,value:facts[field],source:where,text:String(raw??value).slice(0,700)});};
  for(const [field,key] of Object.entries({area:'totalArea',living:'livingArea',kitchen:'kitchenArea',rooms:'roomsCount',floor:'floorNumber',balconies:'balconiesCount',loggias:'loggiasCount',combinedWcs:'combinedWcsCount',separateWcs:'separateWcsCount'}))if(o[key]!==undefined)set(field,String(Number(o[key])),'JSON offer.'+key,o[key]);
  if(typeof o.hasLift==='boolean')set('lift',o.hasLift?'yes':'no','JSON hasLift');
  if(o.geo?.coordinates){set('latitude',o.geo.coordinates.lat,'JSON geo.coordinates.lat');set('longitude',o.geo.coordinates.lng,'JSON geo.coordinates.lng');}
  if(o.building?.floorsCount!==undefined)set('floors',o.building.floorsCount,'JSON building.floorsCount');
  const b=o.bargainTerms||{};for(const [f,k] of [['price','price'],['deposit','deposit'],['commission','clientFee'],['prepayMonths','prepayMonths']])if(b[k]!==undefined)set(f,b[k],'JSON bargainTerms.'+k);
  if(o.isByHomeowner!==undefined)set('seller',o.isByHomeowner?'owner':'unknown','JSON isByHomeowner');
  if(b.utilitiesTerms){const u=b.utilitiesTerms;if(u.includedInPrice===true)set('utilities',u.flowMetersNotIncludedInPrice?'meters':'included','JSON utilitiesTerms');else if(u.includedInPrice===false)set('utilities','extra','JSON utilitiesTerms');}
  if(b.leaseTermType==='longTerm')set('leaseTerm','long','JSON leaseTermType');
  const addresses=o.geo?.address||[];
  if(addresses.length){set('address',addresses.map(a=>a.shortName||a.fullName||a.name).filter(Boolean).join(', '),'JSON geo.address');const district=addresses.find(a=>a.type==='raion');if(district)set('district',district.fullName||district.name,'JSON geo.address');const city=addresses.find(a=>a.type==='location');if(city)set('city',city.name,'JSON geo.address');}
  if(source.url)set('url',source.url,'JSON-LD / cianId');
  const labels={'Общая площадь':'area','Жилая площадь':'living','Площадь кухни':'kitchen','Год постройки':'buildingYear','Тип дома':'buildingType','Тип перекрытий':'floorType','Строительная серия':'buildingSeries','Подъезды':'entrances','Отопление':'heating','Аварийность':'emergency','Санузел':'bathroom','Вид из окон':'windows','Ремонт':'renovation','Высота потолков':'ceiling'};
  for(const r of records.filter(r=>['ObjectFactoidsItem','OfferSummaryInfoItem'].includes(r.name))){const text=r.text.replace(/\s+/g,' ');if(/^Этаж\s/.test(text)){const m=text.match(/Этаж\s+(\d+)\s+из\s+(\d+)/);if(m){if(facts.floor===undefined)set('floor',m[1],r.name,text);if(facts.floors===undefined)set('floors',m[2],r.name,text);}}for(const [label,field] of Object.entries(labels))if(text.startsWith(label+' ')&&facts[field]===undefined){let value=text.slice(label.length).trim();if(['area','living','kitchen','ceiling'].includes(field))value=value.match(/\d+(?:[.,]\d+)?/)?.[0]?.replace(',','.');set(field,value,r.name,text);}}
  if(!facts.address){const a=records.filter(r=>r.name==='AddressItem').map(r=>r.text);if(a.length)set('address',[...new Set(a)].join(', '),'AddressItem');}
  const routes=o.geo?.undergrounds||[];
  if(routes.length){const lines=routes.map(r=>[r.name,r.travelTime??'',({walk:'walk',transport:'public',byCar:'car'})[r.travelType]||'unknown',r.lineType==='mcd1'?'D1':r.lineType||''].join(' | '));set('transit',lines.join('\n'),'JSON geo.undergrounds');set('metro',routes[0].name,'JSON geo.undergrounds[0]');if(routes[0].travelType==='walk')set('walk',routes[0].travelTime,'JSON geo.undergrounds[0]');}
  else{const lines=records.filter(r=>r.name==='UndergroundItem').map(r=>{const m=r.text.match(/^(.*?)\s+(\d+(?:[–-]\d+)?)\s*мин/);return m?[m[1],m[2],'unknown',''].join(' | '):null;}).filter(Boolean);if(lines.length){set('transit',[...new Set(lines)].join('\n'),'UndergroundItem');warnings.push('Способ пути в текстовом DOM не подписан: требуется проверка, пеший маршрут не предполагается.');}}
  if(o.geo?.railways?.length)set('railways',o.geo.railways.map(r=>[r.name,r.time??'',({byFoot:'walk',byCar:'car'})[r.travelType]||'unknown'].join(' | ')).join('\n'),'JSON geo.railways');
  if(source.description)set('sourceDescription',source.description,'#description / [data-name="Description"]');
  if(facts.address)set('name',facts.address,'Адрес объекта');
  return {facts,evidence,warnings};
}
