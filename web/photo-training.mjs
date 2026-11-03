// Explicit operator labels for training; listing facts are not changed here.
export function parseTrainingBoxes(text){
 const lines=String(text||'').split('\n').map(s=>s.trim()).filter(Boolean);
 if(lines.length>100)throw Error('Не более 100 рамок');
 return lines.map(line=>{const parts=line.split(/[\s,;]+/),region=parts.slice(0,4).map(Number);if(parts.length<4||parts.length>5||region.length!==4||region.some(n=>!Number.isFinite(n)||n<0||n>1)||region[2]<=0||region[3]<=0||region[0]+region[2]>1||region[1]+region[3]>1)throw Error('Рамка: x y ширина высота от 0 до 1; пятое поле — общий ID предмета');if(parts[4]&&!/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(parts[4]))throw Error('Неверный ID предмета');return {region,...(parts[4]?{instanceId:parts[4],linkConfirmed:true}:{})};});
}

export function setupPhotoTraining({mount,getCardId,ensureCard,api,toast=()=>{}}){
 if(!mount||typeof api!=='function')return {refresh:async()=>{},dispose(){}};
 const make=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;};
 const panel=make('details'),summary=make('summary','Разметка фото для обучения'),body=make('div'),status=make('p');status.setAttribute('role','status');
 const explain=make('p','Проверьте оригинал и рамки. «Отклонить находку» не означает отсутствие предмета. В учебный экспорт попадут только явно проверенные наличие или отсутствие.');
 const reload=make('button','Загрузить разметку карточки');reload.type='button';reload.className='btn';
 panel.className='photo-training-studio';panel.append(summary,explain,reload,status,body);mount.replaceChildren(panel);
 let current=null,cardId=null,epoch=0,photoRequest=0,photoUrl=null,disposed=false;
 const report=error=>{status.textContent=error.message||String(error);toast(status.textContent);};
 const field=(label,input)=>{const row=make('label');input.setAttribute('aria-label',label.trim());row.append(make('span',label+' '),input);row.style.display='block';row.style.margin='8px 0';body.append(row);return input;};
 const select=(values,value)=>{const n=make('select');for(const [v,t] of values){const o=make('option',t);o.value=v;n.append(o);}n.value=value;return n;};
 const check=value=>{const n=make('input');n.type='checkbox';n.checked=value===true;return n;};
 async function showPhoto(photo,img){
  if(photoUrl){URL.revokeObjectURL(photoUrl);photoUrl=null;}img.removeAttribute('src');
  const request=++photoRequest;if(!photo)return;const expected=epoch;
  if(!/^\/api\/(local|phone)\/media\/[a-f0-9]{64}$/.test(photo.url))throw Error('Неверный адрес оригинала');
  const token=document.querySelector('meta[name="redcn-token"]')?.content;
  const response=await fetch(photo.url,{headers:token?{'X-Redcn-Token':token}:{},credentials:'same-origin',cache:'no-store'});
  if(!response.ok)throw Error('Оригинал недоступен');
  if(Number(response.headers.get('Content-Length'))>12000000)throw Error('Оригинал превышает лимит');
  const blob=await response.blob();if(blob.size>12000000)throw Error('Оригинал превышает лимит');
  if(disposed||request!==photoRequest||expected!==epoch||cardId!==getCardId())return;
  photoUrl=URL.createObjectURL(blob);img.src=photoUrl;
 }
 function render(){
  body.replaceChildren();panel.dataset.reviewRevision=String(current.revision);const doc=current.document;
  const group=field('Общая группа квартиры',make('input'));group.value=doc.groupId;group.placeholder='apartment-001';group.maxLength=64;
  const split=field('Часть корпуса',select([['unassigned','Не назначена'],['train','Обучение'],['calibration','Подбор порогов'],['test','Отложенная приёмка']],doc.split));
  const rights=field('Разрешаю использовать эти оригиналы для обучения',check(doc.rights.allowed));
  const basis=field('Основание использования',select([['','Не указано'],['owner-authorized','Разрешение правообладателя'],['licensed','Подтверждённая лицензия']],doc.rights.basis));
  const duplicates=field('Ракурсы, кропы и повторные объявления сверены; одна квартира находится в одной группе',check(doc.duplicatesReviewed));
  const photo=field('Оригинал',select(current.photos.map((p,i)=>[p.id,'Фото '+(i+1)]),current.photos[0]?.id||''));
  const imageFrame=make('div'),img=make('img'),overlay=make('div');imageFrame.style.position='relative';imageFrame.style.display='inline-block';imageFrame.style.maxWidth='100%';img.alt='Оригинал для разметки';img.style.maxWidth='100%';img.style.maxHeight='420px';img.style.display='block';img.style.touchAction='none';img.draggable=false;overlay.style.cssText='position:absolute;pointer-events:none;border:2px solid #ed5a3a;box-sizing:border-box;display:none';imageFrame.append(img,overlay);body.append(imageFrame,make('p','Обведите видимый предмет на оригинале. Рамку можно уточнить числами ниже; координаты отсчитываются от левого верхнего угла.'));
  const category=field('Категория предмета',select(current.categories.map(k=>[k,current.categoryLabels?.[k]||k]),current.categories[0]||''));
  const state=field('Решение',select([['pending','Ожидает проверки'],['present','Предмет виден'],['absent','Проверено: предмета нет'],['unknown','Нельзя определить'],['rejected','Находка модели отклонена']], 'pending'));
  const boxes=field('Рамки: x y ширина высота; по одной на строку. Необязательное пятое поле — общий ID предмета между ракурсами',make('textarea'));boxes.rows=4;boxes.placeholder='0.1 0.2 0.3 0.4 chair-one';
  const exhaustive=field('Проверил всю эту категорию на данном фото',check(false));
  const verified=field('Подтверждаю метку для обучения',check(false));
  const provenance=make('p');body.append(provenance);
  function selected(){return doc.rows.find(r=>r.photoId===photo.value&&r.category===category.value);}
  function draw(region){overlay.style.display=region?'block':'none';if(region){overlay.style.left=region[0]*100+'%';overlay.style.top=region[1]*100+'%';overlay.style.width=region[2]*100+'%';overlay.style.height=region[3]*100+'%';}}
  function fill(){const row=selected();state.value=row?.state||'pending';boxes.value=(row?.boxes||[]).map(b=>[...b.region,...(b.linkConfirmed&&b.instanceId?[b.instanceId]:[])].join(' ')).join('\n');exhaustive.checked=row?.exhaustive===true;verified.checked=row?.verified===true;draw(row?.boxes?.[0]?.region);provenance.textContent=row?.reviewedBy?'Последняя проверка: '+row.reviewedBy+' · '+new Date(row.reviewedAt*1000).toLocaleString():row?.sourceDecision?'Решение в журнале: '+row.sourceDecision+'; учебная метка требует отдельной проверки.':'Новая ручная метка';}
  let start=null;const point=e=>{const r=img.getBoundingClientRect();return [Math.max(0,Math.min(1,(e.clientX-r.x)/r.width)),Math.max(0,Math.min(1,(e.clientY-r.y)/r.height))];};
  img.onpointerdown=e=>{if(!img.naturalWidth)return;start=point(e);img.setPointerCapture(e.pointerId);e.preventDefault();};
  img.onpointermove=e=>{if(!start)return;const end=point(e);draw([Math.min(start[0],end[0]),Math.min(start[1],end[1]),Math.abs(end[0]-start[0]),Math.abs(end[1]-start[1])]);};
  img.onpointerup=e=>{if(!start)return;const end=point(e),region=[Math.min(start[0],end[0]),Math.min(start[1],end[1]),Math.abs(end[0]-start[0]),Math.abs(end[1]-start[1])].map(x=>Number(x.toFixed(6)));start=null;if(region[2]<.001||region[3]<.001)return;region[2]=Math.min(region[2],1-region[0]);region[3]=Math.min(region[3],1-region[1]);boxes.value=[boxes.value.trim(),region.join(' ')].filter(Boolean).join('\n');state.value='present';verified.checked=false;exhaustive.checked=false;draw(region);};
  img.onpointercancel=()=>{start=null;draw(null);};
  category.onchange=fill;photo.onchange=()=>{++epoch;fill();showPhoto(current.photos.find(p=>p.id===photo.value),img).catch(report);};fill();showPhoto(current.photos[0],img).catch(report);
  const save=make('button','Сохранить решение и параметры');save.type='button';save.className='btn';
  save.onclick=async()=>{try{
   if(cardId!==getCardId())throw Error('Открыта другая карточка. Загрузите её разметку.');
   if(!photo.value)throw Error('В карточке нет сохранённых оригиналов');
   const input={photoId:photo.value,category:category.value,state:state.value,boxes:parseTrainingBoxes(boxes.value),verified:verified.checked,exhaustive:exhaustive.checked};
   const previous=selected();const rows=doc.rows.filter(r=>!r.stale&&!(r.photoId===input.photoId&&r.category===input.category));rows.push({...previous,...input});
   const document={...doc,groupId:group.value.trim(),split:split.value,rights:{allowed:rights.checked,basis:basis.value,photoIds:current.photos.map(item=>item.id).sort()},duplicatesReviewed:duplicates.checked,rows};
   save.disabled=true;const expectedId=cardId,expected=epoch;const value=await api('photo-training/'+cardId,{action:'save',document,expectedRevision:current.revision});
   if(disposed||expected!==epoch||expectedId!==getCardId())return;
   current=value;status.textContent='Разметка сохранена. Версия '+value.revision;render();
  }catch(error){report(error);}finally{save.disabled=false;}};
  const exportButton=make('button','Скачать проверенные метки этой карточки');exportButton.type='button';exportButton.className='btn';
  exportButton.onclick=async()=>{try{
   if(cardId!==getCardId())throw Error('Открыта другая карточка.');
   const value=await api('photo-training/'+cardId,{action:'export'}),manifest=value.manifest;
   if(!Object.values(manifest.splits||{}).some(rows=>rows.length))throw Error('Пока нет меток с полной проверкой, группой, частью корпуса и разрешением использования.');
   const url=URL.createObjectURL(new Blob([JSON.stringify(manifest,null,2)],{type:'application/json'}));const a=make('a');a.href=url;a.download='redcn-private-photo-labels.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
   status.textContent='Приватные проверенные метки экспортированы. Это ещё не допуск модели.';
  }catch(error){report(error);}};
  body.append(save,exportButton,make('p','Непроверенные, неизвестные и отклонённые находки остаются в очереди. Права на новые или заменённые оригиналы нужно подтвердить заново.'));
 }
 async function refresh(){
  try{const expected=++epoch;cardId=getCardId()||await ensureCard?.();if(!cardId)throw Error('Сначала сохраните карточку на Mac.');const id=cardId;status.textContent='Загружаем разметку…';const value=await api('photo-training/'+id);if(disposed||expected!==epoch||id!==getCardId())return;current=value;status.textContent='Версия '+value.revision+' · строк в очереди: '+value.document.rows.length;render();}catch(error){report(error);}
 }
 reload.onclick=refresh;
 return {refresh,dispose(){disposed=true;++epoch;if(photoUrl)URL.revokeObjectURL(photoUrl);mount.replaceChildren();}};
}
