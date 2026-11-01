// Import failures remain actionable without exposing arbitrary request headers.
function importErrorText(value){
 const text=typeof value==='string'?value:value&&typeof value.message==='string'?value.message:'Не удалось завершить импорт';
 return text.replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/gu,'').replace(/\b(?:authorization|proxy-authorization|cookie|set-cookie)\s*[:=][^\r\n]*/giu,'[служебные данные скрыты]').replace(/\b(?:bearer|token|password|secret)\s*[:= ]\s*[a-zA-Z0-9_./+=-]{8,}/giu,'[секрет скрыт]').slice(0,1200);
}
export function importErrorSourceUrl(value){
 if(typeof value!=='string')return '';
 const match=value.match(/\]\((https:\/\/[^\s)]+)\)|https:\/\/[^\s<>"\]]+/u);if(match)value=match[1]||match[0];
 try{const url=new URL(value);return url.protocol==='https:'&&!url.username&&!url.password&&['','443'].includes(url.port)&&/(?:^|\.)(?:cian\.ru|avito\.ru)$/iu.test(url.hostname)?url.origin+url.pathname:'';}catch{return '';}
}
export function importFailureInfo(error,options={}){
 const message=importErrorText(error),statusValue=error&&typeof error==='object'?(error.status??error.statusCode??error.httpStatus):null,status=Number(statusValue)||(Number(message.match(/\b(?:HTTP(?:\s*Error)?|status|код)\s*[:=]?\s*(\d{3})/iu)?.[1])||null);
 const sourceUrl=importErrorSourceUrl(options.url||''),platform=/avito\.ru/iu.test(sourceUrl)?'Авито':/cian\.ru/iu.test(sourceUrl)?'ЦИАН':'источника';
 let code='unknown',title='Импорт не завершён',reason='Обработчик не смог завершить подготовку объявления.',suggestion='Исходную карточку можно открыть отдельно и вставить описание вручную.',retryable=false,manualRequired=false;
 if(/отмен[её]н|cancelled|canceled/iu.test(message)){code='cancelled';title='Импорт отменён';reason='Подготовка остановлена по запросу.';suggestion='Можно продолжить вручную или позже запустить новый импорт.';}
 else if(/captcha|вы не робот|проверка браузера|проверку браузера|проверка HTTPS|доступ ограничен|ручн.{0,20}проверк|автоматический повтор остановлен/iu.test(message)||[401,403,429].includes(status)){code='attention';title=platform+' требует внимания';reason=status?'Источник ответил HTTP '+status+'. Автоматический повтор остановлен.':'Источник запросил проверку или не подтвердил доступ.';suggestion='Откройте страницу в своём обычном Chrome и выполните предложенную площадкой проверку, либо вставьте текст. Защита автоматически не обходится.';manualRequired=true;}
 else if(/certificate|сертификат|CERT_|SSL|TLS/iu.test(message)){code='tls';title='Не подтверждён HTTPS источника';reason='Не удалось установить проверенное защищённое соединение.';suggestion='Проверьте дату Mac, сеть и сертификаты. Небезопасное подключение не включается; можно вставить текст вручную.';manualRequired=true;}
 else if([404,410].includes(status)||/снято|архивн|больше недоступно|удалено/iu.test(message)){code='unavailable';title='Объявление недоступно';reason='Источник сообщил, что объект снят, удалён или не найден.';suggestion='Проверьте ссылку и актуальность объекта. Старый текст не подтверждает, что квартира доступна.';}
 else if(/не HTML|not HTML|не поддерживаем.{0,15}формат|неподдерживаем.{0,15}формат/iu.test(message)){code='not-html';title='Получен неподдерживаемый ответ';reason='В ответе нет поддерживаемой HTML-карточки объявления.';suggestion='Откройте страницу источника и вставьте её текст либо загрузите сохранённый HTML.';}
 else if(/Node\.js|NodeRuntime|генератор.{0,20}недоступ|запустить.{0,20}Node/iu.test(message)){code='runtime';title='На Mac недоступен генератор';reason='Сервер не запустил обработчик китайского текста.';suggestion='Проверьте Node.js на Mac. Устанавливать Node.js на телефон не нужно; исходный текст можно сохранить в редакторе.';manualRequired=true;}
 else if(/AbortSignal|is not a function|undefined is not/iu.test(message)){code='browser';title='Ошибка совместимости браузера';reason='Браузер не выполнил необходимую операцию.';suggestion='Обновите страницу приложения и сохраните эту причину для диагностики. Текст объявления можно вставить вручную.';manualRequired=true;}
 else if(/поля объявления|Неверные поля|схем[аы]|требуется (?:число|текст|список|объект)/iu.test(message)){code='schema';title='Нужно проверить поля карточки';reason='Тип или значение полученных данных не подходит для сохранения.';suggestion='Исправьте указанные поля. Неверное значение не заменяется нулём и не сохраняется как готовый факт.';manualRequired=true;}
 else if(/не содержит площади|не найдено описани|недостаточно.{0,20}(?:текста|данных)|нужен JavaScript/iu.test(message)){code='incomplete';title='Страница загрузилась частично';reason='Не хватает описания или основных характеристик для надёжного разбора.';suggestion='Выберите обычный Chrome в способе загрузки или вставьте доступное описание вручную.';manualRequired=true;}
 else if(/timeout|timed out|тайм.?аут|срок.{0,15}(?:ист[её]к|HTTP)|не завершилась за|не ответил.{0,30}врем/iu.test(message)){code='timeout';title='Истекло время ожидания';reason='Mac не получил полный ответ за отведённый срок.';suggestion='Посмотрите этап, на котором остановился импорт. Можно повторить запрос вручную или вставить текст.';retryable=true;}
 else if(status&&status>=500&&status<=599){code='server';title='Временная ошибка '+platform;reason='Источник ответил HTTP '+status+'. Число попыток ограничено.';suggestion='Повторите позже вручную либо вставьте описание; бесконечных автоматических повторов нет.';retryable=[500,502,503,504].includes(status);}
 else if(/network|сетевая|urlopen|failed to fetch|ECONN|сброс соединени|не удалось подключ|связь.{0,15}Mac/iu.test(message)){code='network';title='Нет ответа по сети';reason='Не удалось обменяться данными между браузером, Mac или источником.';suggestion='Проверьте соединение Mac и открытый сервер. Можно повторить вручную или вставить текст объявления.';retryable=true;}
 const stageLabels={preflight:'Проверка генератора',browser:'Запуск Chrome',navigate:'Загрузка страницы',scroll:'Прокрутка и догрузка',extract:'Извлечение фактов',generate:'Создание китайского текста',photos:'Фотографии',save:'Сохранение'};
 return {code,title,reason,suggestion,message,status,sourceUrl,retryable,manualRequired,stage:stageLabels[options.stage]||''};
}
export function setupImportErrors({mount,getUrl=()=>'',onPaste,onRetry,toast=()=>{}}={}){
 if(!mount)return null;
 let revision=0;
 const make=(tag,text,properties={})=>{const node=document.createElement(tag);if(text!==undefined)node.textContent=text;Object.assign(node,properties);return node;};
 mount.classList.add('import-errors');mount.hidden=true;mount.setAttribute('aria-live','polite');
 function clear(){revision++;mount.hidden=true;mount.replaceChildren();}
 function show(error,options={}){
  const current=++revision,info=importFailureInfo(error,{url:getUrl(),...options});mount.hidden=false;mount.replaceChildren();mount.dataset.reason=info.code;
  mount.append(make('h3',info.title),make('p',info.reason),make('p',info.suggestion));if(info.stage)mount.append(make('p','Последний этап: '+info.stage,{className:'small'}));
  const detail=make('details'),summary=make('summary','Точная причина для диагностики');detail.append(summary,make('pre',info.message));mount.append(detail);
  const actions=make('div',undefined,{className:'import-errors-actions'});mount.append(actions);
  if(onPaste){const paste=make('button','Вставить текст вручную',{type:'button',className:'btn'});paste.onclick=()=>onPaste(info);actions.append(paste);}
  if(info.sourceUrl){const link=make('a','Открыть источник',{href:info.sourceUrl,target:'_blank',rel:'noopener noreferrer',className:'btn'});actions.append(link);}
  if(onRetry&&info.retryable){const retry=make('button','Повторить вручную',{type:'button',className:'btn'});retry.onclick=async()=>{retry.disabled=true;try{await onRetry(info);}catch(next){if(current===revision)show(next,options);}finally{if(current===revision)retry.disabled=false;}};actions.append(retry);}
  return info;
 }
 return {show,clear};
}
