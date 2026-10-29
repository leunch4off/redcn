export const METRICS={searches:'Поисковые запросы',search_index:'Индекс поиска',impressions:'Показы',views:'Просмотры / чтения',clicks:'Клики',likes:'Лайки',saves:'Сохранения'};
export function safeSource(value){try{const u=new URL(value);if(!['https:','http:'].includes(u.protocol)||u.username||u.password)return '';return u.href;}catch{return '';}}
export function parseCSV(input){
 const rows=[];let row=[],field='',quoted=false;
 for(let i=0;i<input.length;i++){const ch=input[i];if(ch==='"'){if(quoted&&input[i+1]==='"'){field+='"';i++;}else quoted=!quoted;}else if(ch===','&&!quoted){row.push(field);field='';}else if((ch==='\n'||ch==='\r')&&!quoted){if(ch==='\r'&&input[i+1]==='\n')i++;row.push(field);if(row.some(x=>x.trim()))rows.push(row);row=[];field='';}else field+=ch;}
 if(quoted)throw Error('Незакрытая кавычка CSV');row.push(field);if(row.some(x=>x.trim()))rows.push(row);if(!rows.length)return [];
 const headers=rows.shift().map(x=>x.trim().replace(/^\uFEFF/,''));return rows.map((r,i)=>{if(r.length!==headers.length)throw Error('CSV: неверное число колонок в строке '+(i+2));return Object.fromEntries(headers.map((h,j)=>[h,r[j].trim()]));});
}
function validDate(date){return /^\d{4}-\d{2}-\d{2}$/.test(date)&&Number.isFinite(Date.parse(date+'T00:00:00Z'))&&new Date(date+'T00:00:00Z').toISOString().slice(0,10)===date;}
export function normalizeMetrics(records){
 if(!Array.isArray(records)||records.length>50000)throw Error('Нужен массив до 50 000 строк');
 if(records.some(r=>!r||typeof r!=='object'||Array.isArray(r)))throw Error('Неверная строка статистики');
 return records.filter(r=>r.value!==''&&r.value!==null&&r.value!==undefined).map((r,index)=>{
  if(!r||!validDate(String(r.date))||!METRICS[r.metric]||!['daily','cumulative'].includes(r.kind)||!['keyword','topic','note'].includes(r.scope))throw Error('Некорректная схема статистики, строка '+(index+1));
  const source=safeSource(r.source),value=Number(r.value);if(!source||!Number.isFinite(value)||value<0||!r.tag||!r.entity)throw Error('Нужны источник, объект, тег и неотрицательное значение');
  if(r.metric!=='search_index'&&!Number.isInteger(value))throw Error('Счётчик должен быть целым числом');
  const timezone=String(r.timezone||'Asia/Shanghai');try{new Intl.DateTimeFormat('en',{timeZone:timezone});}catch{throw Error('Неизвестный часовой пояс источника');}
  return {tag:String(r.tag).replace(/^#/,''),date:r.date,metric:r.metric,value,kind:r.kind,scope:r.scope,entity:String(r.entity),source,timezone,sourceSystem:String(r.sourceSystem||'import'),importedAt:new Date().toISOString()};
 });
}
export function metricSeriesKey(r){return JSON.stringify([r.tag,r.scope,r.entity,r.source,r.timezone,r.sourceSystem,r.kind]);}
export function mergeMetrics(old,incoming){const map=new Map();for(const r of [...old,...incoming])map.set(metricSeriesKey(r)+'\u001f'+r.metric+'\u001f'+r.date,r);return [...map.values()];}
function dayShift(date,amount){const d=new Date(date+'T00:00:00Z');d.setUTCDate(d.getUTCDate()+amount);return d.toISOString().slice(0,10);}
export function summarizeSeries(records,end,days){
 if(!validDate(end)||![1,7,30].includes(Number(days)))throw Error('Нужны дата окончания и период 1/7/30');
 const keys=new Set(records.map(metricSeriesKey));if(keys.size>1)throw Error('Нельзя складывать разные темы, посты, источники или часовые пояса');
 const start=dayShift(end,1-days),prior=dayShift(start,-1),result={start,end,days,metrics:{}};
 for(const metric of Object.keys(METRICS)){
  const rows=records.filter(r=>r.metric===metric),kind=records[0]?.kind||'daily';
  if(kind==='cumulative'){
   const a=rows.find(r=>r.date===prior),b=rows.find(r=>r.date===end);
   if(metric==='search_index'){result.metrics[metric]={value:null,status:'Индекс нельзя получать разностью накопительных счётчиков',coverage:0};continue;}
   if(!a||!b){result.metrics[metric]={value:null,status:'Нужны снимки на '+prior+' и '+end,coverage:0};continue;}
   const delta=b.value-a.value;result.metrics[metric]=delta<0?{value:null,status:'Счётчик уменьшился: нужна проверка',coverage:0}:{value:delta,status:'Разность двух снимков',coverage:days};
  }else{
   const unique=new Map(rows.filter(r=>r.date>=start&&r.date<=end).map(r=>[r.date,r.value]));const values=[...unique.values()];
   const value=values.length?values.reduce((a,b)=>a+b,0)/(metric==='search_index'?values.length:1):null;
   result.metrics[metric]={value,status:values.length===0?'Нет данных':values.length<days?'Частичные данные '+values.length+'/'+days:metric==='search_index'?'Средний индекс за период':'Полный период',coverage:values.length};
  }
 }
 const clicks=result.metrics.clicks,impressions=result.metrics.impressions;
 const clickDays=new Set(records.filter(r=>r.metric==='clicks'&&r.date>=start&&r.date<=end).map(r=>r.date));
 const impressionDays=new Set(records.filter(r=>r.metric==='impressions'&&r.date>=start&&r.date<=end).map(r=>r.date));
 const aligned=records[0]?.kind==='cumulative'||clickDays.size===impressionDays.size&&[...clickDays].every(d=>impressionDays.has(d));
 result.ctr=aligned&&clicks.coverage===days&&impressions.coverage===days&&impressions.value>0?clicks.value/impressions.value*100:null;
 return result;
}
export function rememberContent(records,item){
 const text=String(item.text||'').trim();if(!text)return records;
 const existing=records.find(r=>r.text===text&&r.type===item.type&&r.source===(item.source||''));
 const now=new Date().toISOString();if(existing)return records.map(r=>r===existing?{...r,lastSeen:now,seen:(r.seen||1)+1}:r);
 const id=globalThis.crypto?.randomUUID?.()||'kb-'+Date.now().toString(36)+'-'+Math.random().toString(36).slice(2);
 return [{id,type:item.type||'example',title:String(item.title||text.slice(0,60)),text,source:item.source||'',ru:String(item.ru||''),cn:String(item.cn||''),status:item.status||'unreviewed',createdAt:now,lastSeen:now,seen:1},...records].slice(0,1000);
}
