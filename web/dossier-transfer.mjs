export const DOSSIER_MAX_BYTES=128*1024*1024;
export function validateDossierFile(file){
 if(!file||typeof file.name!=='string'||!/\.zip$/iu.test(file.name)||!Number.isSafeInteger(file.size)||file.size<1||file.size>DOSSIER_MAX_BYTES)throw Error('Выберите приватную ZIP-копию redcn от 1 байта до 128 МБ');
 return file;
}
export function dossierClientSelection(options={},selected={}){
 const result={attachmentIds:[],exportIds:[]};
 for(const [key,rows]of[['attachmentIds',options.attachments],['exportIds',options.exports]]){
  const input=selected[key]||[];if(!Array.isArray(input)||input.length>100||input.some(id=>typeof id!=='string'||! /^[a-f0-9]{32}$/u.test(id)))throw Error('Неверный выбор файлов');
  for(const id of new Set(input)){
   const row=(Array.isArray(rows)?rows:[]).find(item=>item.id===id);
   if(!row||key==='attachmentIds'&&(row.category==='contract'||row.shareable===false)||key==='exportIds'&&row.requiresRegeneration)throw Error('Файл недоступен для клиентской копии');
   result[key].push(id);
  }
 }
 return result;
}
function dossierReadBase64(file){return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(Error('Не удалось прочитать ZIP'));reader.onabort=()=>reject(Error('Чтение ZIP отменено'));reader.readAsDataURL(file);});}
export function setupDossierTransfer({mount,getCardId,ensureCard,api,getBlob,downloadBlob,onRestored=()=>{},toast=()=>{},access=()=>({private:true,client:true})}={}){
 if(!mount)return null;
 const make=(tag,text='',className='')=>{const node=document.createElement(tag);node.textContent=text;if(className)node.className=className;return node;};
 const panel=make('details','','dossier-transfer'),summary=make('summary','Резервная копия досье и выбранные файлы клиенту');
 const inner=make('div','','dossier-transfer-body'),status=make('p','Приватная копия содержит личные заметки и договоры. Храните её у себя.','small');status.setAttribute('role','status');
 const actions=make('div','','dossier-transfer-actions'),backup=make('button','Скачать приватную ZIP-копию','btn'),restore=make('button','Восстановить ZIP в новую карточку','btn'),savedDesign=make('button','Скачать восстановленный шаблон дизайна','btn'),input=make('input');
 input.type='file';input.accept='.zip,application/zip';input.hidden=true;backup.type=restore.type=savedDesign.type='button';actions.append(backup,restore,savedDesign,input);
 const client=make('section'),clientHeading=make('h3','Клиентская копия · только выбранные файлы'),note=make('p','По умолчанию вложения не выбраны. Личные заметки и договоры не выдаются. Готовые материалы требуют действующей проверки публикации.','small'),refreshButton=make('button','Выбрать файлы текущей квартиры','btn'),choices=make('div','','dossier-transfer-choices'),clientExport=make('button','Скачать клиентский ZIP','btn');
 refreshButton.type=clientExport.type='button';clientExport.disabled=true;client.append(clientHeading,note,refreshButton,choices,clientExport);
 const duplicates=make('section'),duplicateButton=make('button','Найти точные дубликаты фото между карточками','btn'),duplicateList=make('div');duplicateButton.type='button';duplicates.append(duplicateButton,duplicateList);
 inner.append(status,actions,client,duplicates);panel.append(summary,inner);mount.replaceChildren(panel);
 let version=0,options=null,loadedId=null,busy=false;const buttons=[backup,restore,savedDesign,refreshButton,clientExport,duplicateButton];
 function requireAccess(kind){if(access()?.[kind]!==true)throw Error('Для этого действия недостаточно прав. Обновите подключение.');}
 function updateAccess(){const rights=access()||{};actions.hidden=rights.private!==true;duplicates.hidden=rights.private!==true;client.hidden=rights.client!==true;panel.hidden=rights.private!==true&&rights.client!==true;summary.textContent=rights.private===true?'Резервная копия досье и выбранные файлы клиенту':'Выбранные файлы клиенту';if(rights.private!==true)status.textContent='Клиентская копия содержит только явно выбранные материалы. Личные заметки и договоры остаются приватными.';}
 function message(text){status.textContent=text;toast(text);}
 function setBusy(value){busy=value;for(const button of buttons)button.disabled=value;clientExport.disabled=value||!options;}
 async function run(operation){if(busy)return;setBusy(true);try{await operation();}catch(error){message(error.message||'Операция не завершена');}finally{setBusy(false);}}
 async function cardId(){const value=getCardId?.()||await ensureCard?.();if(!/^[a-f0-9]{32}$/u.test(value||''))throw Error('Сначала сохраните квартиру на Mac');return value;}
 function current(id,token){if(token!==version||getCardId?.()!==id)throw Error('Открыта другая квартира. Повторите действие для нужной карточки.');}
 backup.onclick=()=>run(async()=>{requireAccess('private');const id=await cardId(),token=version;status.textContent='Собираем приватную копию карточки, фото, вложений, дизайна и материалов…';const blob=await getBlob('dossier-backup/'+id);current(id,token);requireAccess('private');downloadBlob(blob,'redcn-private-'+id.slice(0,8)+'.zip');message('Приватная копия готова. Не отправляйте её клиентам: в ней есть личные данные.');});
 restore.onclick=()=>{try{requireAccess('private');input.click();}catch(error){message(error.message);}};
 savedDesign.onclick=()=>run(async()=>{requireAccess('private');const id=await cardId(),token=version,data=await api('dossier-design/'+id);current(id,token);requireAccess('private');if(!data.template)throw Error('Для этой карточки нет восстановленного шаблона');downloadBlob(new Blob([JSON.stringify(data.template,null,2)],{type:'application/json'}),'ciliev-restored-design-'+id.slice(0,8)+'.json');message('Шаблон сохранён. Загрузите JSON через «Дизайн → Импорт шаблона»; общий шаблон не изменён автоматически.');});
 input.onchange=()=>run(async()=>{const file=input.files[0];try{requireAccess('private');validateDossierFile(file);const origin=getCardId?.();status.textContent='Проверяем контрольные суммы и восстанавливаем отдельную новую карточку…';const result=await api('dossier-import',{data:await dossierReadBase64(file)});requireAccess('private');message('Новая карточка восстановлена: '+result.result_id+'. Существующие квартиры и общий дизайн сохранены.'+(result.clientMaterialsRequireRegeneration?' Клиентские материалы пересоздайте в «Дизайне».':''));if(getCardId?.()===origin)await onRestored(result);if(getCardId?.()!==origin){version++;options=null;loadedId=null;choices.replaceChildren();clientExport.disabled=true;}}finally{input.value='';}});
 async function refresh(){updateAccess();requireAccess('client');const token=++version,id=getCardId?.();options=null;loadedId=null;choices.replaceChildren();clientExport.disabled=true;if(!id){choices.textContent='Сохраните карточку, чтобы выбрать её вложения.';return;}
  const data=await api('dossier-options/'+id);current(id,token);options=data;loadedId=id;
  for(const [key,rows]of[['attachmentIds',data.attachments||[]],['exportIds',data.exports||[]]])for(const row of rows){const label=make('label'),box=make('input');box.type='checkbox';box.dataset.kind=key;box.value=row.id;box.checked=false;box.disabled=key==='attachmentIds'&&(row.category==='contract'||row.shareable===false)||key==='exportIds'&&row.requiresRegeneration;label.append(box,document.createTextNode(row.name+(box.disabled?(row.category==='contract'?' · договор остаётся приватным':' · сначала пересоздайте материал'):'')));choices.append(label);}
  if(!choices.childNodes.length)choices.textContent='Вложений пока нет. ZIP будет содержать только клиентский текст объявления.';clientExport.disabled=busy;
 }
 refreshButton.onclick=()=>run(refresh);
 clientExport.onclick=()=>run(async()=>{requireAccess('client');const id=await cardId(),token=version;if(!options||loadedId!==id)throw Error('Сначала загрузите список файлов нужной квартиры');const selected={attachmentIds:[],exportIds:[]};for(const box of choices.querySelectorAll('input:checked'))selected[box.dataset.kind].push(box.value);const selection=dossierClientSelection(options,selected);status.textContent='Готовим клиентский пакет: '+(selection.attachmentIds.length+selection.exportIds.length)+' выбранных файлов и текст объявления…';const blob=await getBlob('dossier-client/'+id,selection);current(id,token);requireAccess('client');downloadBlob(blob,'ciliev-client-'+id.slice(0,8)+'.zip');message('Клиентский ZIP готов. Личные заметки и договоры не добавлены.');});
 duplicateButton.onclick=()=>run(async()=>{requireAccess('private');status.textContent='Проверяем SHA-256 сохранённых оригиналов…';const data=await api('photo-duplicates');requireAccess('private');duplicateList.replaceChildren();for(const group of data.groups||[]){const row=make('p','SHA-256 '+group.sha256+' · '+new Set(group.references.map(item=>item.cardId)).size+' квартиры · файлы сохранены','small');for(const ref of group.references){const link=make('a','Карточка '+ref.cardId.slice(0,8));link.href='/listing/'+ref.cardId;row.append(document.createTextNode(' · '),link);}duplicateList.append(row);}message('Точных групп: '+(data.groups||[]).length+'; проверено карточек: '+data.cardsChecked+(data.truncated?'. Показана ограниченная выборка.':'.')+' Ничего не удалено и не объединено.');});
 updateAccess();return {refresh,updateAccess,clear(){version++;options=null;loadedId=null;choices.replaceChildren();duplicateList.replaceChildren();clientExport.disabled=true;}};
}
