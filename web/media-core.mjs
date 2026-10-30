import {isAutomaticPhotoObservation} from './photo-observation-policy.mjs';
// Shared URL, crop and archive rules. Originals and edited copies are distinct.
export function photoSourceUrl(raw,base=''){
  if(raw&&typeof raw==='object')raw=raw.sourceUrl||raw.originalUrl||raw.url||raw.fullUrl;
  if(typeof raw!=='string')return null;
  try{const u=new URL(raw.startsWith('//')?'https:'+raw:raw,base||undefined);
    if(u.protocol!=='https:'||u.username||u.password||u.port||!/(^|\.)(cian\.ru|cdn-cian\.ru|ciancdn\.ru|avito\.ru|avito\.st)$/i.test(u.hostname))return null;
    u.hash='';return u.href;
  }catch{return null;}
}
export function photoSources(values,base=''){return [...new Set((Array.isArray(values)?values:[]).map(v=>photoSourceUrl(v,base)).filter(Boolean))].slice(0,30);}
export function cropRectangle(width,height,margins={}){
  if(!Number.isInteger(width)||!Number.isInteger(height)||width<32||height<32||width*height>40_000_000)throw Error('Фото должно иметь размер от 32 px, до 40 мегапикселей');
  const result={left:0,top:0,right:0,bottom:0};
  for(const k of Object.keys(result)){const n=Number(margins[k]??0);if(!Number.isFinite(n)||n<0||n>40)throw Error('Каждый отступ: 0–40%');result[k]=n;}
  const x=Math.floor(width*result.left/100),y=Math.floor(height*result.top/100),right=Math.ceil(width*(1-result.right/100)),bottom=Math.ceil(height*(1-result.bottom/100));
  if(right-x<32||bottom-y<32||(right-x)*(bottom-y)<width*height*.55)throw Error('Кадр слишком мал: оставьте не менее 55% площади');
  return {x,y,width:right-x,height:bottom-y};
}
export function photoFileName(index,mime,cropped=false){return String(index+1).padStart(2,'0')+(cropped?'-crop':'-original')+'.'+({'image/jpeg':'jpg','image/png':'png','image/webp':'webp'}[mime]||'jpg');}
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
   const automatic=item.analysis.autoApply===true&&isAutomaticPhotoObservation({...row,status:'auto-visible',originalId:item.analysis.originalId,photoId:item.analysis.originalId});if(!confirmation&&!automatic)continue;
   features.add(row.id);const count=confirmation?.count;
   if(Number.isInteger(count)&&count>=1&&count<=12)featureCounts[row.id]=Math.max(featureCounts[row.id]||0,count);
   photoEvidence.push({id:row.id,sourceUrl:item.url,photoId:item.analysis.originalId,originalId:item.analysis.originalId,engine:item.analysis.engine,status:confirmation?'confirmed':'auto-visible',autoEligible:automatic,count:Number.isInteger(count)?count:null,confidence:row.confidence,evidence:row.evidence||[]});
  }
 }
 return {features:[...features],featureCounts,photoEvidence,analyzedSources:items.filter(item=>!item.selected||item.analysis).map(item=>item.url),replaceAll:true};
}
