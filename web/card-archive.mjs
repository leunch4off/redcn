const caArchiveStatuses = {active:'В работе',rented:'Сдана',archived:'В архиве'};
const caAttachmentCategories = {contract:'Договор',floorplan:'Планировка',lidar:'LiDAR / 3D',other:'Другой файл'};
const caAttachmentExtensions = new Set(['pdf','jpg','jpeg','png','webp','txt','docx','usdz','obj','glb','gltf','ply','stl','zip','dxf','ifc']);
export const caMaxAttachmentBytes = 40 * 1024 * 1024;

export function archiveValidateAttachmentFile(file) {
  if (!file || typeof file.name !== 'string' || !file.name.trim()) throw new Error('Выберите файл.');
  if (!Number.isSafeInteger(file.size) || file.size < 1) throw new Error('Файл пустой или его размер неизвестен.');
  if (file.size > caMaxAttachmentBytes) throw new Error('Один файл может занимать не больше 40 МБ.');
  const extension = file.name.split('.').at(-1).toLowerCase();
  if (!caAttachmentExtensions.has(extension)) throw new Error('Поддерживаются PDF, фотографии, DOCX, TXT, ZIP и файлы 3D-планировок.');
  return file;
}

export function archiveFormatAttachmentBytes(value) {
  const bytes = Number(value);
  if (!Number.isFinite(bytes) || bytes < 0) return 'Размер неизвестен';
  if (bytes < 1024) return Math.round(bytes) + ' Б';
  if (bytes < 1024 * 1024) return (bytes / 1024).toLocaleString('ru-RU',{maximumFractionDigits:1}) + ' КБ';
  return (bytes / (1024 * 1024)).toLocaleString('ru-RU',{maximumFractionDigits:1}) + ' МБ';
}

function caReadBase64(file) {
  return new Promise((resolve,reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = reader.result;
      if (typeof result !== 'string' || !result.includes(',')) reject(new Error('Не удалось прочитать файл.'));
      else resolve(result.slice(result.indexOf(',') + 1));
    };
    reader.onerror = () => reject(new Error('Не удалось прочитать файл. Выберите его повторно.'));
    reader.onabort = () => reject(new Error('Чтение файла отменено.'));
    reader.readAsDataURL(file);
  });
}

export function setupCardArchive({mount,getCardId,ensureCard,api,downloadAttachment,toast=()=>{}}) {
  if (!mount || typeof api !== 'function' || typeof getCardId !== 'function') throw new Error('Не удалось открыть архив карточки.');
  const make = (tag,text='',className='') => {
    const node = document.createElement(tag);
    node.textContent = text;
    if (className) node.className = className;
    return node;
  };
  const button = (text,primary=false) => {
    const node = make('button',text,'archive-button' + (primary?' archive-primary':''));
    node.type = 'button';
    return node;
  };
  const panel = make('section','','card-archive');
  const heading = make('h2','Архив квартиры');
  const privateBadge = make('span','Только для вас','archive-private');
  const titleRow = make('div','','archive-title-row');
  titleRow.append(heading,privateBadge);
  const intro = make('p','Сохраните договор, планировку или скан квартиры для будущего заселения. Вложения и заметки остаются на вашем Mac и доступны подключённому телефону.','archive-help');
  const status = make('p','','archive-status');
  status.setAttribute('role','status');
  status.setAttribute('aria-live','polite');
  const retry = button('Повторить загрузку архива');
  retry.hidden=true;
  retry.setAttribute('aria-label','Повторить загрузку архива квартиры');
  const form = make('form','','archive-form');
  const statusLabel = make('label','Статус квартиры');
  const statusSelect = make('select');
  statusSelect.setAttribute('aria-label','Статус квартиры');
  for (const [value,label] of Object.entries(caArchiveStatuses)) {
    const option = make('option',label); option.value=value; statusSelect.append(option);
  }
  statusLabel.append(statusSelect);
  const notesLabel = make('label','Личные заметки');
  const notes = make('textarea');
  notes.rows=4; notes.maxLength=20000;
  notes.placeholder='Например: дата сдачи, особенности квартиры, что проверить перед следующим заселением';
  notes.setAttribute('aria-label','Личные заметки квартиры');
  notesLabel.append(notes);
  const save = button('Сохранить статус и заметки',true);
  save.type='submit';
  const dirtyNote = make('p','','archive-dirty');
  dirtyNote.setAttribute('aria-live','polite');
  form.append(statusLabel,notesLabel,save,dirtyNote);

  const upload = make('div','','archive-upload');
  const uploadHeading = make('h3','Добавить файл');
  const categoryLabel = make('label','Тип вложения');
  const category = make('select');
  category.setAttribute('aria-label','Тип вложения');
  for (const [value,label] of Object.entries(caAttachmentCategories)) {
    const option=make('option',label); option.value=value; category.append(option);
  }
  categoryLabel.append(category);
  const fileLabel = make('label','Файл · до 40 МБ');
  const fileInput = make('input');
  fileInput.type='file';
  fileInput.accept=[...caAttachmentExtensions].map(extension=>'.'+extension).join(',');
  fileInput.setAttribute('aria-label','Файл для архива квартиры');
  fileLabel.append(fileInput);
  const uploadButton = button('Прикрепить файл');
  uploadButton.disabled=true;
  const formats = make('p','PDF, JPG, PNG, WEBP, DOCX, TXT, ZIP; USDZ, OBJ, GLB, GLTF, PLY, STL, DXF, IFC. LiDAR и 3D-файлы хранятся в исходном формате.','archive-help');
  const confidentiality = make('p','Эти файлы не добавляются в рекламные фото, HTML и PDF-презентацию квартиры.','archive-confidentiality');
  upload.append(uploadHeading,categoryLabel,fileLabel,uploadButton,formats,confidentiality);
  const filesHeading = make('h3','Файлы квартиры');
  const list = make('ul','','archive-file-list');
  list.setAttribute('aria-label','Вложения к карточке');
  const empty = make('p','Пока нет прикреплённых файлов.','archive-empty');
  panel.append(titleRow,intro,status,retry,form,upload,filesHeading,list,empty);
  mount.append(panel);

  let activeId=null, viewEpoch=0, refreshSequence=0, destroyed=false, loaded=false;
  let busy=false, dirty=false, metadataRevision=0, refreshing=false;
  const currentId = () => getCardId() || null;
  const isCurrent = (id,epoch) => !destroyed && epoch===viewEpoch && currentId()===id && activeId===id;

  function setStatus(message,error=false) {
    status.textContent=message;
    status.classList.toggle('archive-error',error);
  }
  function setDirty(value) {
    dirty=value;
    dirtyNote.textContent=value?'Есть несохранённые изменения.':'';
    syncControls();
  }
  function syncControls() {
    const unavailable=!activeId && typeof ensureCard!=='function';
    const metadataUnavailable=unavailable || !!activeId&&!loaded;
    statusSelect.disabled=notes.disabled=save.disabled=busy || metadataUnavailable;
    category.disabled=fileInput.disabled=busy || unavailable;
    uploadButton.disabled=busy || unavailable || !fileInput.files?.length;
    retry.disabled=busy || refreshing;
  }
  function setBusy(value) {
    busy=value;
    if(value){refreshSequence++;refreshing=false;}
    panel.setAttribute('aria-busy',String(value));
    syncControls();
    for (const control of list.querySelectorAll('button')) control.disabled=value;
  }
  function reset(id) {
    activeId=id; viewEpoch++; refreshSequence++; loaded=false;
    metadataRevision++; busy=false;refreshing=false;retry.hidden=true;
    statusSelect.value='active'; notes.value=''; fileInput.value='';
    list.replaceChildren(); empty.hidden=false; setDirty(false); setBusy(false);
    setStatus(id?'Загружаем архив квартиры…':'Сохраните карточку или прикрепите файл — квартира появится в архиве на Mac.');
  }
  function bindCurrent() {
    const id=currentId();
    if (id!==activeId) reset(id);
    return id;
  }
  function renderFiles(attachments,id,epoch) {
    list.replaceChildren();
    empty.hidden=attachments.length>0;
    for (const asset of attachments) {
      const item=make('li','','archive-file');
      const info=make('div','','archive-file-info');
      const name=make('strong',asset.name||'Файл');
      const date=asset.created ? new Date(Number(asset.created)*1000) : null;
      const dateText=date && !Number.isNaN(date.getTime()) ? date.toLocaleDateString('ru-RU') : '';
      const details=make('p',[caAttachmentCategories[asset.category]||'Файл',archiveFormatAttachmentBytes(asset.bytes),dateText].filter(Boolean).join(' · '),'archive-help');
      info.append(name,details);
      const actions=make('div','','archive-file-actions');
      const download=button('Скачать');
      download.setAttribute('aria-label','Скачать '+(asset.name||'файл'));
      download.disabled=busy;
      download.onclick=async () => {
        if (!isCurrent(id,epoch) || busy) return;
        download.disabled=true;
        try {
          if (typeof downloadAttachment!=='function') throw new Error('Загрузка недоступна. Откройте карточку на локальном сайте Mac.');
          await downloadAttachment(asset,id);
        } catch (error) {
          if (isCurrent(id,epoch)) {setStatus(error.message,true);toast(error.message);}
        } finally {if(isCurrent(id,epoch))download.disabled=busy;}
      };
      const remove=button('Удалить'); remove.classList.add('archive-remove');
      remove.setAttribute('aria-label','Удалить '+(asset.name||'файл'));
      const confirmation=make('div','','archive-remove-confirm');
      confirmation.hidden=true;
      const prompt=make('p','Удалить файл «'+(asset.name||'Файл')+'» из этой карточки?','archive-help');
      const confirm=button('Да, удалить'),cancel=button('Отмена');
      confirm.classList.add('archive-remove');
      confirmation.append(prompt,confirm,cancel);
      remove.onclick=()=>{if(!busy&&isCurrent(id,epoch)){confirmation.hidden=false;confirm.focus();}};
      cancel.onclick=()=>{confirmation.hidden=true;remove.focus();};
      confirm.onclick=async () => {
        if (!isCurrent(id,epoch) || busy) return;
        setBusy(true); setStatus('Удаляем вложение…');
        try {
          const result=await api('card-archive/'+id,{action:'remove',attachment_id:asset.id});
          if(!isCurrent(id,epoch))return;
          apply(result,id,epoch);setStatus('Файл удалён из этой карточки.');
        } catch(error) {if(isCurrent(id,epoch)){setStatus(error.message,true);toast(error.message);}}
        finally {if(isCurrent(id,epoch))setBusy(false);}
      };
      actions.append(download,remove); item.append(info,actions,confirmation); list.append(item);
    }
  }
  function apply(data,id,epoch,{preserveDirty=true}={}) {
    if (!isCurrent(id,epoch)) return;
    loaded=true;
    if (!preserveDirty || !dirty) {
      statusSelect.value=Object.hasOwn(caArchiveStatuses,data.status)?data.status:'active';
      notes.value=typeof data.notes==='string'?data.notes:'';
      setDirty(false);
    }
    renderFiles(Array.isArray(data.attachments)?data.attachments:[],id,epoch);
    retry.hidden=true;syncControls();
  }
  async function actionId() {
    bindCurrent();
    const startingEpoch=viewEpoch;
    let id=activeId;
    if (!id && typeof ensureCard==='function') {
      const saved=await ensureCard();
      id=typeof saved==='string'?saved:saved?.result_id;
      if (destroyed || startingEpoch!==viewEpoch || !id || currentId()!==id) throw new Error('Карточка изменилась. Повторите действие для открытой квартиры.');
      // A new ID refers to the same editor contents; preserve the user's pending notes.
      const pendingNotes=notes.value,pendingStatus=statusSelect.value,pendingDirty=dirty;
      reset(id); notes.value=pendingNotes; statusSelect.value=pendingStatus; setDirty(pendingDirty);
    }
    if (!id) throw new Error('Сначала сохраните карточку на Mac.');
    return {id,epoch:viewEpoch};
  }
  statusSelect.onchange=notes.oninput=()=>{metadataRevision++;setDirty(true);};
  form.onsubmit=async event => {
    event.preventDefault(); if(busy)return;
    bindCurrent();
    if(activeId&&!loaded){setStatus('Сначала дождитесь загрузки архива. Если нет связи, нажмите «Повторить загрузку архива».',true);retry.hidden=false;return;}
    let target=null;
    const initialId=currentId(),initialEpoch=viewEpoch;
    const belongs=()=>target?isCurrent(target.id,target.epoch):!destroyed&&initialEpoch===viewEpoch&&currentId()===initialId;
    setBusy(true);
    try {
      target=await actionId();
      if(!isCurrent(target.id,target.epoch))return;
      const revision=metadataRevision;
      const value={action:'update',status:statusSelect.value,notes:notes.value};
      setBusy(true);setStatus('Сохраняем статус и заметки…');
      const result=await api('card-archive/'+target.id,value);
      if(!isCurrent(target.id,target.epoch))return;
      if(metadataRevision===revision)setDirty(false);
      apply(result,target.id,target.epoch);
      setStatus('Статус и заметки сохранены на Mac.');toast('Архив квартиры сохранён.');
    } catch(error) {if(belongs()){setStatus(error.message,true);toast(error.message);}}
    finally {if(belongs())setBusy(false);}
  };
  fileInput.onchange=()=>{
    try {if(fileInput.files?.[0])archiveValidateAttachmentFile(fileInput.files[0]);uploadButton.disabled=busy||!fileInput.files?.length;setStatus(fileInput.files?.[0]?'Файл выбран. Нажмите «Прикрепить файл».':'');}
    catch(error){fileInput.value='';uploadButton.disabled=true;setStatus(error.message,true);}
  };
  uploadButton.onclick=async () => {
    if(busy)return;
    const file=fileInput.files?.[0]; let target=null;
    const initialId=currentId(),initialEpoch=viewEpoch;
    const belongs=()=>target?isCurrent(target.id,target.epoch):!destroyed&&initialEpoch===viewEpoch&&currentId()===initialId;
    try {
      archiveValidateAttachmentFile(file);
      const selectedCategory=category.value;
      setBusy(true);
      target=await actionId();
      if(!isCurrent(target.id,target.epoch))return;
      setBusy(true);setStatus('Читаем «'+file.name+'»…');
      const data=await caReadBase64(file);
      if(!isCurrent(target.id,target.epoch))return;
      setStatus('Сохраняем файл на Mac… Не закрывайте страницу.');
      const result=await api('card-archive/'+target.id,{action:'attach',name:file.name,category:selectedCategory,data});
      if(!isCurrent(target.id,target.epoch))return;
      apply(result,target.id,target.epoch); fileInput.value='';
      setStatus('Файл сохранён на Mac.');toast('Файл прикреплён к квартире.');
    } catch(error) {if(belongs()){setStatus(error.message,true);toast(error.message);}}
    finally {if(belongs())setBusy(false);}
  };
  async function refresh() {
    if(destroyed)return;
    const id=bindCurrent();
    if(!id || busy)return;
    const epoch=viewEpoch,sequence=++refreshSequence;
    refreshing=true;syncControls();
    if(!loaded)setStatus('Загружаем архив квартиры…');
    try {
      const data=await api('card-archive/'+id);
      if(!isCurrent(id,epoch)||sequence!==refreshSequence)return;
      const wasLoaded=loaded;
      apply(data,id,epoch);
      if(!wasLoaded)setStatus('Архив загружен. Изменения сохраняются кнопкой ниже.');
    } catch(error) {if(isCurrent(id,epoch)&&sequence===refreshSequence){setStatus(error.message,true);retry.hidden=false;}}
    finally {if(isCurrent(id,epoch)&&sequence===refreshSequence){refreshing=false;syncControls();}}
  }
  retry.onclick=()=>refresh();
  function clear() {
    // Called on logout / switching away: retain no private names or notes in the DOM.
    reset(null); setStatus('Откройте карточку квартиры, чтобы увидеть её архив.');
  }
  function destroy() {destroyed=true;viewEpoch++;refreshSequence++;panel.remove();}
  reset(currentId());
  refresh();
  return {refresh,clear,destroy};
}
