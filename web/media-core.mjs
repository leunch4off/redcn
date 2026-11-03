import {isAutomaticPhotoObservation} from './photo-observation-policy.mjs';
import {teacherReviewed,teacherEvidence} from './photo-teacher-review.mjs';
// Shared URL, crop and archive rules. Originals and edited copies are distinct.
export function photoSourceUrl(raw,base=''){
  if(raw&&typeof raw==='object')raw=raw.sourceUrl||raw.originalUrl||raw.url||raw.fullUrl;
  if(typeof raw!=='string'||!raw.trim())return null;
  try{const u=new URL(raw.startsWith('//')?'https:'+raw:raw,base||undefined);
    if(u.protocol!=='https:'||u.username||u.password||u.port||!/(^|\.)(cian\.ru|cdn-cian\.ru|ciancdn\.ru|avito\.ru|avito\.st)$/i.test(u.hostname))return null;
    u.hash='';return u.href;
  }catch{return null;}
}
export function photoSources(values,base=''){return [...new Set((Array.isArray(values)?values:[]).map(v=>photoSourceUrl(v,base)).filter(Boolean))].slice(0,30);}

// CIAN exposes main and thumbnail renditions as separate gallery nodes. This
// key groups only URLs that were actually observed; it never constructs a URL.
// The complete path stem is part of the key: a numeric ID alone is insufficient.
export function cianPhotoUrlIdentity(value){
 const safe=photoSourceUrl(value);if(!safe)return null;
 const url=new URL(safe);if(url.hostname!=='images.cdn-cian.ru')return null;
 const match=url.pathname.match(/^(\/images\/(?:[^/]+\/)*(?:[^/]+-)?)(\d{1,20})-(\d{1,3})\.(?:jpe?g|png|webp)$/i);if(!match)return null;
 return {key:url.origin+match[1]+match[2],photoId:match[2],rendition:Number(match[3])};
}
const observedPhotoUrls=row=>[row.url,...Object.values(row.variants||{}),...(row.observedVariants||[]).map(item=>item.url)].filter(Boolean);
function cianPhotoGroupKey(row){
 const identities=observedPhotoUrls(row).map(cianPhotoUrlIdentity),key=identities[0]?.key;
 return key&&identities.every(item=>item?.key===key)?key:null;
}
function preferObservedCianMain(row){
 if(row.variants?.fullUrl){row.url=row.variants.fullUrl;return row;}
 if(!cianPhotoGroupKey(row))return row;
 if(![2,3,4].includes(cianPhotoUrlIdentity(row.url)?.rendition))return row;
 const main=observedPhotoUrls(row).find(url=>cianPhotoUrlIdentity(url)?.rendition===1);
 if(main)row.url=main;
 return row;
}

// A group contains only variants literally exposed by one gallery image node.
// A local DOM identity is explicitly distinguished from a platform photo ID.
export function observedPhotoItem(attrs={}, {base='',platform='',order=0}={}){
 const set=['data-srcset','srcset'].flatMap(key=>String(attrs[key]||'').split(',').map(part=>{const [url,size='0']=part.trim().split(/\s+/);return {url,source:key,weight:parseFloat(size)||0};})).sort((a,b)=>b.weight-a.weight);
 const raw=[{url:attrs['data-original'],source:'data-original'},...set.map(row=>({url:row.url,source:row.source})),{url:attrs['data-src'],source:'data-src'},{url:attrs.currentSrc,source:'currentSrc'},{url:attrs.src,source:'src'},{url:attrs.imageHref,source:'href'}];
 const observedVariants=[];for(const row of raw){const url=photoSourceUrl(row.url,base);if(url&&!observedVariants.some(item=>item.url===url))observedVariants.push({url,source:row.source});}
 if(!observedVariants.length)return null;
 const url=observedVariants[0].url,declared=attrs['data-photo-id']||attrs['data-image-id'];let photoId=typeof declared==='string'&&/^[A-Za-z0-9_.:-]{1,100}$/.test(declared)?declared:'';
 const cianIdentity=platform==='cian'&&cianPhotoGroupKey({url,observedVariants})?cianPhotoUrlIdentity(url):null;
 if(!photoId&&cianIdentity)photoId=cianIdentity.photoId;
 if(!photoId){let scope=base;try{const parsed=new URL(base);scope=parsed.origin+parsed.pathname;}catch{}let hash=2166136261;for(const char of platform+'|'+scope+'|'+url){hash^=char.charCodeAt(0);hash=Math.imul(hash,16777619);}photoId='dom-'+(hash>>>0).toString(16).padStart(8,'0');}
 return {url,photoId,order,isLayout:false,isCianLayout:false,variants:{},observedVariants:observedVariants.slice(0,60),identitySource:photoId===declared?'platform-photo-id':cianIdentity?'observed-cian-photo-url':'observed-dom-node',source:platform+' gallery DOM'};
}

export function mergeObservedPhotoItems(existing=[],observations=[],layouts=[]){
 const output=existing.map(row=>({...row,variants:{...row.variants},...(Array.isArray(row.observedVariants)?{observedVariants:row.observedVariants.map(value=>({...value}))}:{})}));
 const urls=observedPhotoUrls;
 const excluded=new Set(layouts.flatMap(urls));
 const excludedGroups=new Set(layouts.map(cianPhotoGroupKey).filter(Boolean));
 for(const row of observations){if(!row||!row.url||urls(row).some(url=>excluded.has(url)))continue;const variants=urls(row),key=cianPhotoGroupKey(row);if(key&&excludedGroups.has(key))continue;
  const match=output.find(item=>{
   const otherKey=cianPhotoGroupKey(item);
   return urls(item).some(url=>variants.includes(url))||key&&key===otherKey||row.identitySource==='platform-photo-id'&&item.photoId===row.photoId&&(item.identitySource==='platform-photo-id'||item.source==='CIAN offer')&&!(key&&otherKey&&key!==otherKey);
  });
  if(match){match.observedVariants??=[];for(const value of row.observedVariants||[])if(!match.observedVariants.some(item=>item.url===value.url))match.observedVariants.push({...value});match.observedVariants=match.observedVariants.slice(0,60);preferObservedCianMain(match);}
  else output.push(preferObservedCianMain({...row,variants:{...row.variants},observedVariants:(row.observedVariants||[]).map(value=>({...value})),order:output.length}));
 }
 return output.slice(0,400);
}

export function observedPhotoPicture(attributeRows=[],options={}){
 const rows=attributeRows.map(attrs=>observedPhotoItem(attrs,options)).filter(Boolean);if(!rows.length)return null;
 const output={...rows[0],observedVariants:[]},declared=rows.filter(row=>row.identitySource==='platform-photo-id').map(row=>row.photoId);
 if(declared.length&&new Set(declared).size===1){output.photoId=declared[0];output.identitySource='platform-photo-id';}
 else output.identitySource='observed-picture';
 for(const row of rows)for(const variant of row.observedVariants)if(!output.observedVariants.some(item=>item.url===variant.url))output.observedVariants.push({...variant});
 output.observedVariants=output.observedVariants.slice(0,60);
 if(!declared.length&&options.platform==='cian'&&cianPhotoGroupKey(output))output.identitySource='observed-cian-photo-url';
 return output;
}
export function listingPhotoSources(value={}){
 const fields=value.fields||{},listing=value.listing||{};
 return photoSources([...(Array.isArray(fields.photos)?fields.photos:[]),
  ...(Array.isArray(listing.images)?listing.images:[]),
  ...(Array.isArray(listing.photoItems)?listing.photoItems:[]).filter(row=>!row?.isLayout)]);
}
export function cropRectangle(width,height,margins={}){
  if(!Number.isInteger(width)||!Number.isInteger(height)||width<32||height<32||width*height>40_000_000)throw Error('Фото должно иметь размер от 32 px, до 40 мегапикселей');
  const result={left:0,top:0,right:0,bottom:0};
  for(const k of Object.keys(result)){const n=Number(margins[k]??0);if(!Number.isFinite(n)||n<0||n>40)throw Error('Каждый отступ: 0–40%');result[k]=n;}
  const x=Math.floor(width*result.left/100),y=Math.floor(height*result.top/100),right=Math.ceil(width*(1-result.right/100)),bottom=Math.ceil(height*(1-result.bottom/100));
  if(right-x<32||bottom-y<32||(right-x)*(bottom-y)<width*height*.55)throw Error('Кадр слишком мал: оставьте не менее 55% площади');
  return {x,y,width:right-x,height:bottom-y};
}
export function photoFileName(index,mime,cropped=false){return String(index+1).padStart(2,'0')+(cropped?'-crop':'-original')+'.'+({'image/jpeg':'jpg','image/png':'png','image/webp':'webp'}[mime]||'jpg');}
export function photoExportAsset(item,prepared=false){
 const useCrop=prepared&&!item.preferOriginal&&!!item.cropBlob;
 const useVariant=prepared&&!item.preferOriginal&&!useCrop&&!!item.variantBlob;
 const blob=useCrop?item.cropBlob:useVariant?item.variantBlob:item.blob;
 if(!blob)throw Error('Оригинал фотографии ещё не загружен');
 return {blob,kind:useCrop?'crop':useVariant?'variant':'original',crop:useCrop?item.cropMeta||null:null,variant:useVariant?item.variantMeta||null:null,
  reason:useCrop?'prepared':useVariant?'observed-variant':item.preferOriginal?'original-selected':prepared?'original-fallback':'original-requested'};
}
const mediaCrcTable=Array.from({length:256},(_,n)=>{for(let i=0;i<8;i++)n=n&1?0xedb88320^(n>>>1):n>>>1;return n>>>0;});
function mediaCrc(bytes){let c=0xffffffff;for(const b of bytes)c=mediaCrcTable[(c^b)&255]^(c>>>8);return(c^0xffffffff)>>>0;}
export async function photoZip(files){
  if(!files.length||files.length>64)throw Error('Архив: от 1 до 64 файлов');
  const local=[],central=[];let offset=0,size=0;
  for(const file of files){if(!/^[\w.-]+$/.test(file.name))throw Error('Неверное имя файла');const name=new TextEncoder().encode(file.name),bytes=new Uint8Array(await file.blob.arrayBuffer());size+=bytes.length;if(size>180_000_000)throw Error('Архив больше 180 МБ: скачайте фотографии частями');
    const crc=mediaCrc(bytes),head=new Uint8Array(30+name.length),v=new DataView(head.buffer);v.setUint32(0,0x04034b50,true);v.setUint16(4,20,true);v.setUint32(14,crc,true);v.setUint32(18,bytes.length,true);v.setUint32(22,bytes.length,true);v.setUint16(26,name.length,true);head.set(name,30);
    const record=new Uint8Array(46+name.length),c=new DataView(record.buffer);c.setUint32(0,0x02014b50,true);c.setUint16(4,20,true);c.setUint16(6,20,true);c.setUint32(16,crc,true);c.setUint32(20,bytes.length,true);c.setUint32(24,bytes.length,true);c.setUint16(28,name.length,true);c.setUint32(42,offset,true);record.set(name,46);local.push(head,bytes);central.push(record);offset+=head.length+bytes.length;
  }
  const end=new Uint8Array(22),v=new DataView(end.buffer);v.setUint32(0,0x06054b50,true);v.setUint16(8,files.length,true);v.setUint16(10,files.length,true);v.setUint32(12,central.reduce((n,c)=>n+c.length,0),true);v.setUint32(16,offset,true);
  return new Blob([...local,...central,end],{type:'application/zip'});
}

// One object may be visible in several photographs. Keep the largest confirmed
// per-photo count instead of adding duplicate views of the same furniture.
export function confirmedPhotoFeatures(items=[]){
 const features=new Set(),featureCounts={},photoEvidence=[];
 for(const item of items){if(!item.selected||!item.analysis)continue;
  for(const row of item.analysis.candidates||[]){if(item.rejected?.[row.id])continue;const confirmation=item.confirmed?.[row.id];
   const teacher=teacherReviewed(item,row.id),automatic=item.analysis.autoApply===true&&isAutomaticPhotoObservation({...row,status:'auto-visible',originalId:item.analysis.originalId,photoId:item.analysis.originalId});if(!confirmation&&!teacher&&!automatic)continue;
   features.add(row.id);const count=confirmation?.count;
   if(Number.isInteger(count)&&count>=1&&count<=12)featureCounts[row.id]=Math.max(featureCounts[row.id]||0,count);
   const operatorKept=row.reviewOrigin==='operator-kept'&&row.reviewOriginalId===item.analysis.originalId;
   photoEvidence.push(teacher&&!confirmation?teacherEvidence(item,row.id):{id:row.id,sourceUrl:item.url,photoId:item.analysis.originalId,originalId:item.analysis.originalId,engine:operatorKept?'operator-kept':item.analysis.engine,...(operatorKept?{reviewOrigin:'operator-kept'}:{}),status:confirmation?'confirmed':'auto-visible',autoEligible:automatic,count:Number.isInteger(count)?count:null,confidence:row.confidence,evidence:row.evidence||[]});
  }
 }
 return {features:[...features],featureCounts,photoEvidence,analyzedSources:items.filter(item=>!item.selected||item.analysis).map(item=>item.url),replaceAll:true};
}
