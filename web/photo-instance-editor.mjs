// Manual identity across immutable originals. No automatic count or publication.
export function instanceRegion(start,end){
 const clamp=n=>Math.max(0,Math.min(1,Number(n)||0));
 const a=start.map(clamp),b=end.map(clamp);
 return [Math.min(a[0],b[0]),Math.min(a[1],b[1]),Math.abs(a[0]-b[0]),Math.abs(a[1]-b[1])].map(n=>Math.round(n*1e6)/1e6);
}
export function instanceDraft(document,change){
 const value=JSON.parse(JSON.stringify(document));
 if(change.action==='remove')value.observations=value.observations.filter(row=>row.id!==change.id);
 else if(change.action==='add'){
  if(!change.photoId||!change.feature||!change.instanceId||change.region[2]<=0||change.region[3]<=0)throw Error('Выделите предмет и укажите его экземпляр');
  value.observations.push({id:change.id,photoId:change.photoId,feature:change.feature,region:change.region,
   instanceId:change.instanceId,status:'confirmed',linkConfirmed:true});
 }else if(change.action==='count'){
  if(!Number.isInteger(change.count)||change.count<1)throw Error('Количество должно быть положительным целым числом');
  value.countConfirmations[change.feature]=change.count;
 }else throw Error('Неизвестное действие проверки');
 // Any edit invalidates previous count confirmations for the affected category.
 if(change.action!=='count')value.countConfirmations={};
 return value;
}

export function setupPhotoInstanceEditor({mount,api,cardId,loadPhoto,onSaved=()=>{}}){
 let revision=0,documentValue=null,photos=[],categories={},active=0,region=null,start=null,disposed=false,sequence=0,busy=false,requestEpoch=0;
 let imageEpoch=0,imageRequest=null,imageUrl=null;
 function releaseImage(){imageEpoch++;imageRequest?.abort();imageRequest=null;if(imageUrl)URL.revokeObjectURL(imageUrl);imageUrl=null;start=null;}
 async function showImage(image,photo,message){
  if(typeof loadPhoto!=='function'){image.src=photo.url;return;}
  const epoch=imageEpoch,controller=new AbortController();imageRequest=controller;
  message.textContent='Загружаем исходное фото…';
  try{
   const blob=await loadPhoto(photo.id,{signal:controller.signal});
   if(disposed||epoch!==imageEpoch)return;
   imageUrl=URL.createObjectURL(blob);image.src=imageUrl;
   await image.decode();
   if(!disposed&&epoch===imageEpoch)message.textContent='';
  }catch(error){if(!disposed&&epoch===imageEpoch)message.textContent='Не удалось открыть исходное фото. '+(error.message||'Перечитайте проверку.');}
  finally{if(imageRequest===controller)imageRequest=null;}
 }
 const el=(tag,text)=>{const node=document.createElement(tag);if(['input','select','button'].includes(tag))node.style.cssText='max-width:100%;font-size:16px;min-height:44px;white-space:normal;';if(text!==undefined)node.textContent=text;return node;};
 const panel=el('details'),heading=el('summary','Экземпляры мебели · сопоставить ракурсы'),content=el('div'),status=el('p');
 const reload=el('button','Перечитать проверку');reload.type='button';reload.onclick=()=>{if(busy)return;documentValue=null;load();};
 panel.className='photo-instance-editor';panel.style.cssText='min-width:0;max-width:100%;overflow-wrap:anywhere;';
 panel.append(heading,reload,content,status);mount.replaceChildren(panel);status.setAttribute('role','status');
 async function load(){
  if(documentValue||disposed)return;
  const epoch=++requestEpoch;
  status.textContent='Получаем исходные фото и сохранённые связи…';
  try{const value=await api('photo-instances/'+cardId);if(disposed||epoch!==requestEpoch)return;accept(value);}
  catch(error){if(!disposed&&epoch===requestEpoch)status.textContent=error.message;}
 }
 function accept(value){revision=value.revision;documentValue=value.document;photos=value.photos||[];categories=value.categories||{};render(value.summary);panel.dataset.instanceRevision=String(revision);}
 async function save(next){
  if(busy)return;busy=true;const epoch=++requestEpoch;
  status.textContent='Сохраняем проверку…';
  try{const value=await api('photo-instances/'+cardId,{document:next,expectedRevision:revision});if(disposed||epoch!==requestEpoch)return;accept(value);onSaved(value);}
  catch(error){if(!disposed&&epoch===requestEpoch)status.textContent=error.message+' Перезагрузите проверку, если её меняли на другом устройстве.';}
  finally{busy=false;}
 }
 function render(summary){
  releaseImage();content.replaceChildren();status.textContent='';
  const note=el('p','На каждом ракурсе выделите видимый предмет. Для одного предмета выберите одинаковый ID экземпляра, для разных — разные. Модель не подтверждает эту связь. Количество относится только к проверенным видимым экземплярам, а не ко всей квартире.');
  content.append(note);
  if(!photos.length){status.textContent='Сначала сохраните оригиналы фотографий карточки на Mac.';return;}
  active=Math.min(active,photos.length-1);const photo=photos[active];
  const selector=el('select');selector.setAttribute('aria-label','Исходное фото для сопоставления');
  photos.forEach((row,index)=>{const option=el('option','Фото '+(index+1));option.value=String(index);selector.append(option);});selector.value=String(active);
  selector.onchange=()=>{active=Number(selector.value);region=null;render(summary);};content.append(selector);
  const stage=el('div'),image=el('img'),box=el('div'),imageStatus=el('p');stage.style.cssText='position:relative;touch-action:none;max-width:650px;margin:12px 0;';
  image.alt='Оригинал фото '+(active+1)+' — выделите предмет';image.draggable=false;image.style.cssText='display:block;width:100%;height:auto;';
  box.style.cssText='position:absolute;border:3px solid #ba973e;pointer-events:none;display:none;';stage.append(image,box);content.append(stage);
  imageStatus.setAttribute('role','status');content.append(imageStatus);showImage(image,photo,imageStatus);
  const point=e=>{const r=stage.getBoundingClientRect();return [(e.clientX-r.left)/r.width,(e.clientY-r.top)/r.height];};
  const draw=()=>{if(!region)return;box.style.display='block';[box.style.left,box.style.top,box.style.width,box.style.height]=region.map(n=>n*100+'%');};
  draw();stage.onpointerdown=e=>{if(e.button!==0)return;e.preventDefault();start=point(e);stage.setPointerCapture(e.pointerId);};
  stage.onpointermove=e=>{if(!start)return;region=instanceRegion(start,point(e));draw();};
  stage.onpointerup=e=>{if(!start)return;region=instanceRegion(start,point(e));start=null;draw();};
  stage.onpointercancel=stage.onlostpointercapture=()=>{start=null;};
  const feature=el('select');feature.setAttribute('aria-label','Вид предмета');Object.entries(categories).forEach(([id,label])=>{const option=el('option',label);option.value=id;feature.append(option);});
  const identity=el('input');identity.placeholder='Например: chair-1';identity.maxLength=64;identity.setAttribute('aria-label','ID того же экземпляра на разных фото');
  const listId='instances-'+cardId;identity.setAttribute('list',listId);const list=el('datalist');list.id=listId;
  [...new Set(documentValue.observations.map(row=>row.instanceId).filter(Boolean))].forEach(id=>{const option=el('option');option.value=id;list.append(option);});
  const add=el('button','Подтверждаю предмет и связь');add.type='button';add.onclick=()=>{
   try{const id='view-'+Date.now().toString(36)+'-'+(++sequence);save(instanceDraft(documentValue,{action:'add',id,photoId:photo.id,feature:feature.value,instanceId:identity.value.trim(),region:region||[0,0,0,0]}));}
   catch(error){status.textContent=error.message;}
  };content.append(feature,identity,list,add);
  const rows=el('div');for(const row of documentValue.observations){
   const line=el('p'),index=photos.findIndex(p=>p.id===row.photoId),show=el('button','Фото '+(index+1));show.type='button';show.disabled=index<0;show.onclick=()=>{active=index;region=row.region;render(summary);};
   const remove=el('button','Удалить связь');remove.type='button';remove.onclick=()=>save(instanceDraft(documentValue,{action:'remove',id:row.id}));
   line.append(show,el('span',' '+(categories[row.feature]||row.feature)+' → '+row.instanceId+' '),remove);rows.append(line);
  }content.append(rows);
  const counts=summary?.confirmedVisibleCounts||{};content.append(el('p','Проверенные видимые экземпляры: '+(Object.entries(counts).map(([id,n])=>(categories[id]||id)+': '+n).join('; ')||'пока нет')));
  for(const [id,n]of Object.entries(counts)){const confirm=el('button','Подтвердить видимое количество: '+(categories[id]||id)+' — '+n);confirm.type='button';confirm.onclick=()=>save(instanceDraft(documentValue,{action:'count',feature:id,count:n}));content.append(confirm);}
  const issues=summary?.issues||[];for(const issue of issues){const names={'same-frame-distinct-regions':'Разные области одного фото ошибочно объединены','overlapping-distinct-instances':'Одна область ошибочно посчитана разными экземплярами','instance-feature-conflict':'Один экземпляр получил разные категории','photo-not-in-listing':'Фото удалено или изменено','count-confirmation-conflict':'Количество не соответствует проверенным связям'};content.append(el('p',(names[issue.code]||issue.code)+' · '+(issue.observationIds||issue.instanceIds||[issue.instanceId||issue.feature||'']).join(', ')));}
  const confirmed=summary?.manuallyConfirmedVisibleCounts||{};status.textContent=issues.length?'Исправьте отмеченные ошибки сопоставления.':'Сохранено. '+(Object.keys(confirmed).length?'Количество подтверждено для видимых экземпляров.':'Количество ещё не подтверждено.');
 }
 panel.addEventListener('toggle',()=>{if(panel.open)load();});
 return {reload(){if(busy)return;documentValue=null;load();},dispose(){disposed=true;requestEpoch++;releaseImage();mount.replaceChildren();}};
}
