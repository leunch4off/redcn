const HISTORY_LIMIT=200;
export function historyFingerprint(value){const s=JSON.stringify([value.title,value.body,value.tags,value.fields]);let hash=2166136261;for(let i=0;i<s.length;i++)hash=Math.imul(hash^s.charCodeAt(i),16777619);return (hash>>>0).toString(16);}
export function historyEntry(kind,value,now=new Date().toISOString()){
 if(!['generate','save','copy-title','copy-body','copy-all','restore','import'].includes(kind))throw Error('Неизвестное действие истории');
 if(!value||typeof value.title!=='string'||typeof value.body!=='string'||typeof value.tags!=='string'||!value.fields||typeof value.fields!=='object')throw Error('Неверный снимок карточки');
 const frozen=JSON.parse(JSON.stringify(value));
 return {version:1,id:globalThis.crypto?.randomUUID?.()||'event-'+Date.now().toString(36)+'-'+Math.random().toString(36).slice(2),kind,at:now,...frozen,fingerprint:historyFingerprint(frozen)};
}
export function historyHtml(entry){const escape=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const rows=Object.entries(entry.fields).filter(([k,v])=>!['sourceAudit','photos'].includes(k)&&v!==''&&v!==undefined).map(([k,v])=>'<tr><th>'+escape(k)+'</th><td><pre>'+escape(typeof v==='object'?JSON.stringify(v,null,2):v)+'</pre></td></tr>').join('');
 return '<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>'+escape(entry.title)+'</title><style>body{max-width:1050px;margin:40px auto;padding:25px;font:16px/1.7 system-ui;background:#fafaf6;color:#28382e}article,details{padding:24px;background:white;border:1px solid #dbe2d8;border-radius:12px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit}table{width:100%;border-collapse:collapse}th,td{padding:12px;border:1px solid #ddd;text-align:left;vertical-align:top}th{width:220px}small{color:#657763}</style><small>МОСТ · '+escape(entry.at)+' · '+escape(entry.kind)+'</small><article><h1>'+escape(entry.title)+'</h1><pre>'+escape(entry.body)+'</pre><pre>'+escape(entry.tags)+'</pre></article><details open><summary>Поля редактора на момент действия</summary><table>'+rows+'</table></details><details><summary>Доказательства разбора источника</summary><pre>'+escape(JSON.stringify(entry.fields.sourceAudit||{},null,2))+'</pre></details></html>';
}
export async function publicationPng(entry){
 const width=1200,padding=64,lineHeight=38,canvas=document.createElement('canvas'),ctx=canvas.getContext('2d');if(!ctx)throw Error('Браузер не поддерживает PNG-снимок');
 ctx.font='26px system-ui, sans-serif';const wrapped=[];
 const segments=text=>globalThis.Intl?.Segmenter?[...new Intl.Segmenter('zh',{granularity:'grapheme'}).segment(text)].map(x=>x.segment):Array.from(text);
 function wrap(text,font){ctx.font=font;const lines=[];for(const paragraph of text.split('\n')){let row='';for(const ch of segments(paragraph)){if(ctx.measureText(row+ch).width>width-padding*2&&row){lines.push(row);row=ch;}else row+=ch;}lines.push(row);}return lines;}
 wrapped.push(...wrap(entry.title,'bold 36px system-ui').map(text=>({text,font:'bold 36px system-ui',color:'#253a2c',height:52})),{text:'',height:25});
 wrapped.push(...wrap(entry.body,'26px system-ui').map(text=>({text,font:'26px system-ui',color:'#29382e',height:lineHeight})),{text:'',height:22});
 wrapped.push(...wrap(entry.tags,'22px system-ui').map(text=>({text,font:'22px system-ui',color:'#58734e',height:34})));
 const height=padding*2+90+wrapped.reduce((n,r)=>n+r.height,0)+60;if(height>16000)throw Error('Текст слишком длинный для одного PNG; полный HTML-снимок сохранён в истории');
 canvas.width=width;canvas.height=height;ctx.fillStyle='#fafaf6';ctx.fillRect(0,0,width,height);ctx.fillStyle='#c14f3c';ctx.font='bold 21px system-ui';ctx.fillText('МОСТ  ·  REDNOTE',padding,padding+15);ctx.fillStyle='#8a9185';ctx.font='18px system-ui';ctx.fillText(new Date(entry.at).toLocaleString('ru-RU'),padding,padding+49);
 let y=padding+108;for(const row of wrapped){ctx.font=row.font||'26px system-ui';ctx.fillStyle=row.color||'#29382e';if(row.text)ctx.fillText(row.text,padding,y);y+=row.height;}ctx.fillStyle='#8a9185';ctx.font='16px system-ui';ctx.fillText('Снимок готовой публикации · '+entry.fingerprint,padding,height-35);
 return await new Promise((resolve,reject)=>canvas.toBlob(blob=>blob?resolve(blob):reject(Error('Не удалось создать PNG')),'image/png'));
}

export function createHistoryStore(){let dbPromise;
 const db=()=>dbPromise??=new Promise((resolve,reject)=>{const request=indexedDB.open('redcn-history',1);request.onupgradeneeded=()=>request.result.createObjectStore('events',{keyPath:'id'});request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error);});
 const request=async(mode,work)=>{const database=await db();return new Promise((resolve,reject)=>{const tx=database.transaction('events',mode),s=tx.objectStore('events');let result;const op=work(s);op.onsuccess=()=>{result=op.result;};tx.oncomplete=()=>resolve(result);tx.onerror=()=>reject(tx.error);tx.onabort=()=>reject(tx.error||Error('История не сохранена'));});};
 const list=async()=>{const all=await request('readonly',s=>s.getAll());return all.sort((a,b)=>b.at.localeCompare(a.at));};
 return {list,get:id=>request('readonly',s=>s.get(id)),put:async entry=>{await request('readwrite',s=>s.put(entry));const all=await list();for(const old of all.slice(HISTORY_LIMIT))await request('readwrite',s=>s.delete(old.id));return entry;},import:async entries=>{for(const item of entries.slice(0,HISTORY_LIMIT)){if(item?.version===1&&typeof item.id==='string'&&typeof item.title==='string'&&typeof item.body==='string'&&typeof item.at==='string')await request('readwrite',s=>s.put(item));}}};
}
