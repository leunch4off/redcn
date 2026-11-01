// Photo completion is independent of watermark cropping. An uncropped original
// remains a usable photograph, and one failed item never hides its siblings.
export function photoBatchSummary(card={},available=0,crops=0,prepared=true){
 const processing=card.photoProcessing||{},count=Math.max(0,Number(available)||0),expected=Math.max(count,Number(card.photoCount??card.fields?.photos?.length??count)||0);
 const downloadFailed=Math.max(0,Number(processing.downloadFailed)||0),cropFailed=Math.max(0,Number(processing.failed)||0),deferred=Math.max(0,Number(processing.deferred??(Number(processing.skipped||0)-downloadFailed))||0);
 const parts=[expected?'Оригиналы сохранены: '+count+' из '+expected+'.':'В объявлении нет фотографий.'];
 if(expected){parts.push(prepared?'Готовые фото: '+crops+' обрезано; остальные доступны целиком.':'Показаны исходные фотографии без изменений.');}
 if(downloadFailed)parts.push('Не удалось скачать '+downloadFailed+' фото. Повторите подготовку на Mac.');
 else if(expected>count)parts.push(card.mediaStatus==='partial'?'Часть оригиналов не сохранена. Повторите подготовку на Mac.':'Оставшиеся фотографии ещё готовятся на Mac.');
 if(Number(processing.review)>0)parts.push(processing.review+' фото оставлено целиком: безопасная обрезка не подтверждена.');
 if(cropFailed)parts.push('Автообрезка недоступна для '+cropFailed+' фото; оригиналы сохранены.');
 if(deferred)parts.push('Проверка краёв отложена для '+deferred+' фото по лимиту обработки.');
 const failures=(Array.isArray(card.mediaFailures)?card.mediaFailures:[]).filter(row=>row&&Number.isInteger(row.index)&&row.index>0&&row.index<=30&&typeof row.message==='string').slice(0,30).map(row=>({index:row.index,stage:row.stage==='download'?'download':'crop',originalSaved:row.originalSaved===true,message:row.message.replace(/[\x00-\x1f\x7f]/g,' ').slice(0,300)}));
 return {count,expected,text:parts.join(' '),retry:expected>count||cropFailed>0||deferred>0,failures};
}
