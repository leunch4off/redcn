// Recommendations are separate candidates, never facts of the current apartment.
export function listingSourceUrl(raw,base='https://www.cian.ru/'){
 try{const u=new URL(raw,base);if(u.protocol!=='https:'||u.username||u.password||u.port)return null;
  if(/(^|\.)cian\.ru$/i.test(u.hostname)&&/^\/rent\/flat\/\d+\/?$/.test(u.pathname))return 'https://www.cian.ru'+u.pathname.replace(/\/?$/,'/');
  if(/(^|\.)avito\.ru$/i.test(u.hostname)&&/\/kvartiry\/[^/]+_\d+\/?$/.test(u.pathname))return 'https://www.avito.ru'+u.pathname;
 }catch{}return null;
}
const relatedDecode=s=>String(s||'').replace(/&amp;/g,'&').replace(/&quot;/g,'"').replace(/&#39;/g,"'").replace(/&nbsp;|&#160;/g,' ');
const relatedText=s=>relatedDecode(s.replace(/<[^>]+>/g,' ')).replace(/\s+/g,' ').trim();
export function readRelatedHtml(markup,base='',current=''){
 const clean=String(markup||'').replace(/<(script|style|svg)\b[^>]*>[\s\S]*?<\/\1\s*>/gi,''),stack=[],candidates=[],seen=new Set(),own=listingSourceUrl(current||base);
 for(const token of clean.matchAll(/<[^>]*>|[^<]+/g)){const s=token[0];if(s[0]!=='<'){for(const n of stack)if(n.parts&&n.parts.length<1500)n.parts.push(s);continue;}
  const end=s.match(/^<\/([\w:-]+)/);if(end){let i=stack.length-1;while(i>=0&&stack[i].tag!==end[1].toLowerCase())i--;if(i>=0){const nodes=stack.splice(i);for(const n of nodes){if(n.anchor){const text=relatedText(n.parts.join(' '));n.anchor.title=text.slice(0,180)||n.anchor.title;}if(n.card){const text=relatedText(n.parts.join(' ')).slice(0,1600);for(const a of n.links)a.text=text;}}}continue;}
  const start=s.match(/^<([\w:-]+)/);if(!start)continue;const tag=start[1].toLowerCase(),attrs={};for(const a of s.matchAll(/([\w:-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')/g))attrs[a[1].toLowerCase()]=relatedDecode(a[2]??a[3]);
  const marker=(attrs['data-name']||'')+' '+(attrs['data-marker']||''),card=/offer.?card|card.?component|catalog.?item|item.?wrapper|recommend.*item|similar.*item/i.test(marker),section=/similar|recommend|похож/i.test(marker)||stack.some(n=>n.section);
  const node={tag,card,section,parts:[],links:[]};
  if(tag==='a'&&attrs.href){const url=listingSourceUrl(attrs.href,base||undefined);if(url&&url!==own){let c=candidates.find(x=>x.url===url);if(!c){c={url,title:(attrs.title||'').slice(0,180),text:'',origin:section?'source-recommendation':'source-link'};candidates.push(c);}node.anchor=c;for(const n of stack)if(n.card)n.links.push(c);}}
  if(!/^(img|meta|link|br|input|hr|source|area|wbr)$/.test(tag)&&!s.endsWith('/>'))stack.push(node);
 }
 return candidates.filter(c=>{if(seen.has(c.url))return false;seen.add(c.url);return true;}).slice(0,40).map(c=>{const text=c.text||c.title,m=text.match(/(?:м[её]тро|м\.)?\s*([А-ЯЁ][а-яё]+(?:[ -][А-ЯЁа-яё]+){0,2})\s*[·|,]?\s*\d+\s*мин/),price=text.match(/(\d[\d\s\u00a0]{2,})\s*(?:₽|руб)/),area=text.match(/(\d+(?:[.,]\d+)?)\s*(?:м²|м2)/);return {...c,title:c.title||'Объявление из источника',metro:m?.[1]||'',price:price?Number(price[1].replace(/\s/g,'')):null,area:area?Number(area[1].replace(',','.')):null,verified:false};});
}
export function relatedRank(current,source=[],objects=[]){
 const norm=s=>String(s||'').toLowerCase().replace(/ё/g,'е').replace(/[^\p{L}\p{N}]+/gu,' ').trim(),stations=v=>[v.metro,...String(v.transit||'').split('\n').map(l=>l.split('|')[0])].map(norm).filter(Boolean),target=stations(current),own=listingSourceUrl(current.url),byUrl=new Map();
 for(const row of [...source.map(r=>({...r,from:'Источник'})),...objects.map(o=>({...o.fields,...o,from:'Библиотека',title:o.title||o.name,libraryId:o.id}))]){const url=listingSourceUrl(row.url||row.fields?.url);if(!url||url===own)continue;const v=row.fields||row,same=stations(v).filter(s=>target.includes(s));const text=norm(row.text||'');for(const s of target)if(s.length>3&&new RegExp('(?:^| )'+s+' [0-9]{1,3} мин(?: |$)').test(text)&&!same.includes(s))same.push(s);
  let distance=null;const nums=[current.latitude,current.longitude,v.latitude,v.longitude];if(nums.every(n=>n!==''&&n!=null&&Number.isFinite(Number(n)))){const [a,b,c,d]=nums.map(Number),rad=x=>x*Math.PI/180,h=Math.sin(rad(c-a)/2)**2+Math.cos(rad(a))*Math.cos(rad(c))*Math.sin(rad(d-b)/2)**2;distance=6371*2*Math.atan2(Math.sqrt(h),Math.sqrt(1-h));}
  if(row.from==='Библиотека'&&!same.length&&!(distance!==null&&distance<=3))continue;
  const candidate={...row,url,sameMetro:same,distanceKm:distance,score:same.length*100+(distance!==null?Math.max(0,30-distance*5):0)};const old=byUrl.get(url);if(!old||candidate.score>old.score)byUrl.set(url,candidate);
 }
 return [...byUrl.values()].sort((a,b)=>b.score-a.score).slice(0,40);
}
