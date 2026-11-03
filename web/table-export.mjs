import {hasOwnKey} from './own-property.mjs';
import {calculateInitialPayment} from './initial-payment.mjs';

export const TABLE_EXPORT_COLUMNS=Object.freeze([
 ['id','ID'],['title','Заголовок'],['url','Источник'],['district','Район'],['address','Адрес'],['metro','Метро'],
 ['rooms','Комнаты'],['area','Площадь, м²'],['floor','Этаж'],['floors','Этажей'],['price','Арендная ставка'],['pricePeriod','Период ставки'],['priceCurrency','Валюта аренды'],
 ['deposit','Залог'],['depositCurrency','Валюта залога'],['commission','Комиссия, % · внутри команды'],['commissionAmount','Комиссия фиксированной суммой'],['commissionCurrency','Валюта фиксированной комиссии'],
 ['prepayMonths','Предоплата, месяцев'],['otherFees','Прочие известные сборы, ₽'],['firstSubtotal','Известная часть первого платежа, ₽'],['firstTotal','Полный первый платёж, ₽'],['paymentStatus','Полнота расчёта'],['status','Рабочий статус']
]);
const teStates={new:'Новый',verified:'Проверен',ready:'Готов',published:'Опубликован',archive:'Архив'};
const tePeriods={month:'Месяц',day:'Сутки',week:'Неделя',year:'Год',unknown:'Неизвестно'};
const teObject=value=>value&&typeof value==='object'&&!Array.isArray(value);
const teFields=new Set(['id','title','url','district','address','metro','rooms','area','floor','floors','price','pricePeriod','priceCurrency','deposit','depositCurrency','commission','commissionAmount','commissionCurrency','commissionStatus','prepayMonths','utilities','status','oneTimeFees']);
const tePhone=/(?<!\d)(?:\+?7|8)[\s().-]*(?:\d[\s().-]*){10}(?!\d)|\+[1-9](?:[\s().-]*\d){8,14}(?!\d)/gu;
const teFormula=/^[\s\ufeff\u200b-\u200f\u202a-\u202e\u2060-\u206f]*[=+\-@]/u;
function teText(value,max,label){if(value===undefined||value===null)return '';if(typeof value!=='string'||value.length>max||/[\uD800-\uDFFF\uFFFE\uFFFF]/u.test(value))throw Error('Неверное поле выгрузки: '+label);return value.replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/gu,'').replace(tePhone,'[телефон скрыт]');}
function teNumber(value,label,{max=1_000_000_000,places=2,integer=false}={}){if(value===''||value===undefined||value===null)return null;if(!['string','number'].includes(typeof value))throw Error('Неверное число выгрузки: '+label);const text=String(value).replace(/[\s\u00a0\u202f]/gu,'').replace(',','.');if(!(new RegExp(integer?'^\\d+$':'^\\d+(?:\\.\\d{1,'+places+'})?$','u')).test(text))throw Error('Неверное число выгрузки: '+label);const number=Number(text);if(!Number.isFinite(number)||number>max)throw Error('Число вне диапазона: '+label);return number;}
function teEnum(value,choices,fallback,label){const selected=value===undefined||value===null||value===''?fallback:value;if(typeof selected!=='string'||!hasOwnKey(choices,selected))throw Error('Неверная единица или категория: '+label);return selected;}
function teCurrency(value,label){return teEnum(value,{RUB:1,USD:1,EUR:1,unknown:1},'RUB',label);}
function teSource(value){try{const url=new URL(teText(value,2048,'источник'));if(url.protocol!=='https:'||url.username||url.password||url.port&&!['443'].includes(url.port))return '';if(/(^|\.)cian\.ru$/iu.test(url.hostname)&&/^\/rent\/(?:flat|room)\/\d+\/?$/u.test(url.pathname)||/(^|\.)avito\.ru$/iu.test(url.hostname)&&/\/(?:kvartiry|komnaty)\/[^/]+_\d+\/?$/u.test(url.pathname))return url.origin+url.pathname;}catch{}return '';}
function teFacts(card){return teObject(card?.fields)?card.fields:card;}
function teId(card){return teText(card?.id??card?.resultId??card?.fields?.resultId,120,'id');}

export function projectTableRow(card){
 if(!teObject(card))throw Error('Неверная карточка выгрузки');const fields=teFacts(card),row={id:teId(card),title:teText(card.title??card.titles?.[0]??fields.name,300,'title'),url:teSource(fields.url??card.url)};
 if(!row.id)throw Error('Нужен ID карточки');for(const [key,max]of[['district',200],['address',500],['metro',200]])row[key]=teText(fields[key],max,key);
 for(const key of ['price','deposit','commissionAmount'])row[key]=teNumber(fields[key],key);row.commission=teNumber(fields.commission,'commission',{max:1000,places:4});row.area=teNumber(fields.area,'area',{max:1_000_000});
 for(const [key,max]of[['rooms',100],['floor',10000],['floors',10000]])row[key]=teNumber(fields[key],key,{max,integer:true});
 row.pricePeriod=teEnum(fields.pricePeriod,tePeriods,'month','pricePeriod');for(const key of ['priceCurrency','depositCurrency','commissionCurrency'])row[key]=teCurrency(fields[key],key);
 row.prepayMonths=teNumber(fields.prepayMonths,'prepayMonths',{max:36,integer:true})??1;if(row.prepayMonths<1)throw Error('Предоплата должна быть от 1 до 36 месяцев');
 row.commissionStatus=teEnum(fields.commissionStatus,{present:1,none:1,unknown:1},'unknown','commissionStatus');row.utilities=teEnum(fields.utilities,{included:1,meters:1,extra:1,unknown:1},'unknown','utilities');
 row.status=teEnum(card.workflow?.status??fields.workflow?.status,teStates,'new','status');
 const fees=fields.oneTimeFees??[];if(!Array.isArray(fees)||fees.length>30)throw Error('Допускается до 30 разовых сборов');row.oneTimeFees=fees.map(fee=>{if(!teObject(fee))throw Error('Неверный разовый сбор');return {label:teText(fee.label,150,'сбор'),amount:teNumber(fee.amount,'сумма сбора'),currency:teCurrency(fee.currency,'валюта сбора')};});
 return row;
}

export function normalizeTableFilters(value={}){
 if(!teObject(value))throw Error('Неверные фильтры выгрузки');const minRent=teNumber(value.minRent,'минимальная аренда'),maxRent=teNumber(value.maxRent,'максимальная аренда');if(minRent!==null&&maxRent!==null&&minRent>maxRent)throw Error('Минимальная аренда больше максимальной');
 return {minRent,maxRent,district:teText(value.district,200,'район').trim().toLocaleLowerCase('ru'),rooms:teNumber(value.rooms,'комнаты',{max:100,integer:true}),status:teEnum(value.status,{all:1,...teStates},'all','status')};
}

export function filterTableRows(cards,filters={}){
 if(!Array.isArray(cards)||cards.length>5000)throw Error('Допускается до 5000 объектов');const query=normalizeTableFilters(filters),rows=[],excluded=[];
 for(const card of cards){if(!teObject(card))throw Error('Неверная карточка библиотеки');const fields=teFacts(card),id=teId(card),reasons=[];
  if(query.district&&!String(fields.district||'').toLocaleLowerCase('ru').includes(query.district))reasons.push('район');
  if(query.rooms!==null&&String(fields.rooms)!==String(query.rooms))reasons.push('комнаты');
  if(query.status!=='all'&&(card.workflow?.status??fields.workflow?.status??'new')!==query.status)reasons.push('рабочий статус');
  if(query.minRent!==null||query.maxRent!==null){let rent=null;try{rent=teNumber(fields.price,'аренда');}catch{}const monthly=(fields.pricePeriod||'month')==='month'&&(fields.priceCurrency||'RUB')==='RUB';if(rent===null||!monthly)reasons.push('месячная рублёвая ставка не подтверждена');else if(query.minRent!==null&&rent<query.minRent||query.maxRent!==null&&rent>query.maxRent)reasons.push('бюджет');}
  if(reasons.length){excluded.push({id,reasons});continue;}
  rows.push(projectTableRow(card));
 }
 if(new Set(rows.map(row=>row.id)).size!==rows.length)throw Error('ID строк выгрузки должны быть уникальны');
 if(new TextEncoder().encode(JSON.stringify({format:'xlsx',rows})).length>8_000_000)throw Error('Выгрузка превышает 8 МБ исходных полей');return {rows,excluded,filters:query};
}

export function tableExportValues(row){const payment=calculateInitialPayment(row),values={...row,pricePeriod:tePeriods[row.pricePeriod],status:teStates[row.status],otherFees:payment.lines.filter(line=>line.key==='oneTime').reduce((sum,line)=>sum+line.amount,0),firstSubtotal:payment.subtotal,firstTotal:payment.total,paymentStatus:payment.complete?'Полный расчёт':'Требует уточнения'};return TABLE_EXPORT_COLUMNS.map(([key])=>values[key]??null);}
export function tableCsv(rows){if(!Array.isArray(rows)||rows.length>5000)throw Error('Допускается до 5000 строк');const safe=rows.map(row=>{if(!teObject(row)||Object.keys(row).some(key=>!teFields.has(key)))throw Error('Нужны только разрешённые поля выгрузки');return projectTableRow({...row,workflow:{status:row.status||'new'}});});const encode=value=>{let text=value===null||value===undefined?'':String(value);if(typeof value==='string'&&teFormula.test(text))text="'"+text;return '"'+text.replace(/"/gu,'""')+'"';};return '\ufeff'+[TABLE_EXPORT_COLUMNS.map(([,label])=>label),...safe.map(tableExportValues)].map(row=>row.map(encode).join(';')).join('\r\n')+'\r\n';}

export function setupTableExport({mount,getObjects,getXlsx,toast=()=>{}}={}){
 if(!mount)return null;const make=(tag,text,props={})=>{const node=document.createElement(tag);if(text!==undefined)node.textContent=text;Object.assign(node,props);return node;};
 const shell=make('details',undefined,{className:'table-export'}),form=make('div',undefined,{className:'table-export-form'}),status=make('p','Настройте фильтры или выгрузите всю библиотеку.',{className:'muted'}),preview=make('div',undefined,{className:'table-export-preview'}),actions=make('div',undefined,{className:'table-export-actions'});status.setAttribute('role','status');status.setAttribute('aria-live','polite');shell.append(make('summary','Внутренние таблицы · CSV / XLSX'),make('p','Выгрузка для команды содержит комиссию. Исходный HTML, фотографии, договоры, телефоны и личные заметки не входят в таблицу. Это снимок: после изменения данных создайте новый файл.',{className:'muted'}),form,status,preview,actions);mount.replaceChildren(shell);
 const control=(label,type='text')=>{const wrapper=make('label',label),input=make(type==='select'?'select':'input');if(type!=='select')input.type=type;input.setAttribute('aria-label',label);wrapper.append(input);form.append(wrapper);return input;};
 const min=control('Аренда от, ₽ / месяц','number'),max=control('Аренда до, ₽ / месяц','number'),district=control('Район содержит'),rooms=control('Комнат · точно','number'),state=control('Рабочий статус выгрузки','select');for(const node of [min,max,rooms])node.min='0';for(const [value,label]of Object.entries({all:'Все статусы',...teStates}))state.append(make('option',label,{value}));
 const filter=()=>({minRent:min.value,maxRent:max.value,district:district.value,rooms:rooms.value,status:state.value});
 const download=(name,blob)=>{const url=URL.createObjectURL(blob),a=make('a',undefined,{href:url,download:name});a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
 const button=(text,handler)=>{const node=make('button',text,{className:'btn',type:'button'});node.onclick=handler;actions.append(node);return node;};
 const snapshot=()=>filterTableRows(getObjects(),filter());
 function refresh(){try{const result=snapshot();status.textContent='Выбрано '+result.rows.length+' из '+(result.rows.length+result.excluded.length)+' объектов. По бюджету учитываются только подтверждённые месячные ставки в RUB.';preview.replaceChildren();if(result.rows.length){const table=make('table'),head=make('tr'),body=make('tbody');for(const text of ['Заголовок','Аренда','Период','Валюта','Залог','Комиссия, %','Первый платёж, ₽'])head.append(make('th',text));const thead=make('thead');thead.append(head);for(const row of result.rows.slice(0,10)){const tr=make('tr'),payment=calculateInitialPayment(row);for(const text of [row.title,row.price,tePeriods[row.pricePeriod],row.priceCurrency,row.deposit,row.commission,payment.total===null?'Не подтверждён':payment.total])tr.append(make('td',text??'Неизвестно'));body.append(tr);}table.append(thead,body);preview.append(table);}if(result.rows.length>10)preview.append(make('p','Показаны первые 10 строк; в файле будут все '+result.rows.length+'.',{className:'muted'}));}catch(error){status.textContent=error.message;preview.replaceChildren();}}
 const date=()=>new Intl.DateTimeFormat('en-CA',{timeZone:'Europe/Moscow',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date()).replace(/-/gu,'');
 button('Скачать внутренний CSV',()=>{try{const {rows}=snapshot();download('redcn-operator-library-'+date()+'.csv',new Blob([tableCsv(rows)],{type:'text/csv;charset=utf-8'}));status.textContent='CSV: '+rows.length+' объектов, фильтры применены.';}catch(error){status.textContent=error.message;toast(error.message);}});
 const xlsx=button('Скачать внутренний XLSX',async()=>{xlsx.disabled=true;try{if(!getXlsx)throw Error('XLSX создаётся через запущенный сервер Mac; CSV доступен без сервера.');const {rows}=snapshot(),blob=await getXlsx(rows);if(!(blob instanceof Blob))throw Error('Сервер не вернул файл XLSX');download('redcn-operator-library-'+date()+'.xlsx',blob);status.textContent='XLSX: '+rows.length+' объектов, фильтры применены.';}catch(error){status.textContent=error.message;toast(error.message);}finally{xlsx.disabled=false;}});
 if(!getXlsx)xlsx.title='Нужен сервер Mac';for(const node of [min,max,district,rooms,state])node.addEventListener('input',refresh);refresh();return {refresh};
}
