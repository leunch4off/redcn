import {METRICS,parseCSV,normalizeMetrics,metricSeriesKey,mergeMetrics,summarizeSeries,rememberContent,safeSource} from './insights.mjs';

export function setupInsights({read,write,toast,onRule}){
 const el=id=>document.getElementById(id);let research=null;
 let knowledge=read('most-knowledge',[]),metrics=read('most-metrics',[]);if(!Array.isArray(knowledge))knowledge=[];knowledge=knowledge.filter(r=>r&&typeof r.text==='string');try{metrics=normalizeMetrics(metrics);}catch{metrics=[];}
 const persistKB=()=>write('most-knowledge',knowledge);
 function capture(item){knowledge=rememberContent(knowledge,item);persistKB();if(!el('knowledge').classList.contains('hidden'))renderKnowledge();}
 function saveKB(){const text=el('kbText').value.trim();if(!text)return toast('Добавьте текст заметки');capture({type:el('kbType').value,title:el('kbTitle').value,text,source:el('kbSource').value,ru:el('kbRussian').value,cn:el('kbChinese').value});for(const k of ['kbText','kbTitle','kbSource','kbRussian','kbChinese'])el(k).value='';renderKnowledge();toast('Запись сохранена. Применение к словарю требует отдельного подтверждения.');}
 el('kbSave').onclick=saveKB;
 function renderKnowledge(){const q=el('kbSearch').value.toLowerCase(),type=el('kbFilter').value;el('kbList').replaceChildren();
  el('kbCount').textContent=knowledge.length+' записей · сохраняются в этом браузере · до 1000 записей';
  for(const item of knowledge.filter(r=>(type==='all'||r.type===type)&&(r.title+' '+r.text+' '+r.ru+' '+r.cn).toLowerCase().includes(q))){
   const card=document.createElement('article'),title=document.createElement('strong'),meta=document.createElement('p'),text=document.createElement('p'),button=document.createElement('button');card.className='task';title.textContent=item.title||item.type;meta.className='muted';meta.textContent=item.type+' · '+(item.status==='reviewed'?'Проверено':'Требует проверки')+' · встречалось '+item.seen+' раз';text.className='kb-body';text.textContent=item.text;card.append(title,meta,text);
   const source=safeSource(item.source);if(source){const a=document.createElement('a');a.href=source;a.target='_blank';a.rel='noopener noreferrer';a.textContent='Источник ↗';card.append(a);}
   button.className='btn';button.textContent=item.status==='reviewed'?'Отметить непроверенным':'Подтвердить запись';button.onclick=()=>{knowledge=knowledge.map(r=>r.id===item.id?{...r,status:r.status==='reviewed'?'unreviewed':'reviewed'}:r);persistKB();renderKnowledge();};card.append(button);
   if(item.ru&&item.cn){const apply=document.createElement('button');apply.className='btn';apply.textContent='Добавить RU → CN в словарь';apply.disabled=item.status!=='reviewed';apply.onclick=()=>{onRule(item.ru,item.cn);toast('Подтверждённая пара добавлена в словарь');};card.append(apply);}
   el('kbList').append(card);
  }
 }
 el('kbSearch').oninput=renderKnowledge;el('kbFilter').onchange=renderKnowledge;
 function downloadJSON(name,data){const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
 el('kbExport').onclick=()=>downloadJSON('redcn-knowledge-and-feedback.json',{version:1,knowledge});
 el('kbImport').onclick=()=>el('kbFile').click();
 el('kbFile').onchange=async()=>{try{const file=el('kbFile').files[0];if(!file)return;if(file.size>5000000)throw Error('Файл больше 5 МБ');const value=JSON.parse(await file.text());if(!Array.isArray(value.knowledge)||!value.knowledge.every(r=>r&&typeof r.text==='string'))throw Error('Неверный формат базы знаний');for(const item of value.knowledge)capture({...item,status:'unreviewed'});renderKnowledge();toast('Импортировано как непроверенные записи');}catch(e){toast(e.message);}finally{el('kbFile').value='';}};
 el('metricsImport').onclick=()=>el('metricsFile').click();
 function importMetricText(text){const parsed=text.trim().startsWith('[')?JSON.parse(text):parseCSV(text);const rows=normalizeMetrics(parsed);metrics=mergeMetrics(metrics,rows);if(write('most-metrics',metrics)){renderSeries();renderAnalytics();toast('Добавлено '+rows.length+' наблюдений. Происхождение указано в каждой серии.');}}
 el('metricsApply').onclick=()=>{try{importMetricText(el('metricsText').value);}catch(e){toast(e.message);}};
 el('metricsFile').onchange=async()=>{try{const file=el('metricsFile').files[0];if(!file)return;if(file.size>8000000)throw Error('Файл больше 8 МБ');importMetricText(await file.text());}catch(e){toast(e.message);}finally{el('metricsFile').value='';}};
 el('metricsTemplate').onclick=()=>{const csv='tag,date,metric,value,kind,scope,entity,source,timezone,sourceSystem\n莫斯科租房,2026-10-29,views,,daily,topic,REPLACE_TOPIC,https://www.xiaohongshu.com/,Asia/Shanghai,creator_export\n';const url=URL.createObjectURL(new Blob(['\uFEFF'+csv],{type:'text/csv;charset=utf-8'})),a=document.createElement('a');a.href=url;a.download='redcn-metrics-template.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
 function renderSeries(){const previous=el('metricSeries').value;el('metricSeries').replaceChildren();const empty=document.createElement('option');empty.value='';empty.textContent='Нет выбранной серии';el('metricSeries').append(empty);const unique=new Map(metrics.map(r=>[metricSeriesKey(r),r]));for(const [key,r] of unique){const option=document.createElement('option');option.value=key;option.textContent='#'+r.tag+' · '+r.scope+' · '+r.entity+' · '+r.kind+' · '+r.sourceSystem+' · '+r.timezone;el('metricSeries').append(option);}if(unique.has(previous))el('metricSeries').value=previous;else if(unique.size===1)el('metricSeries').value=[...unique.keys()][0];}
 function renderAnalytics(){const selected=el('metricSeries').value,rows=metrics.filter(r=>metricSeriesKey(r)===selected),end=el('metricEnd').value;el('metricTableBody').replaceChildren();
  let reports;try{reports=[1,7,30].map(days=>summarizeSeries(rows,end,days));}catch(e){return toast(e.message);}
  for(const [key,label] of Object.entries(METRICS)){const tr=document.createElement('tr'),name=document.createElement('th');name.textContent=label;tr.append(name);for(const report of reports){const td=document.createElement('td'),r=report.metrics[key];td.textContent=r.value===null?'—':r.value.toLocaleString('ru-RU',{maximumFractionDigits:2});const note=document.createElement('small');note.textContent=r.status;td.append(note);tr.append(td);}el('metricTableBody').append(tr);}
  const ctr=document.createElement('tr'),label=document.createElement('th');label.textContent='CTR (клики / показы)';ctr.append(label);for(const report of reports){const td=document.createElement('td');td.textContent=report.ctr===null?'—':report.ctr.toFixed(2)+'%';ctr.append(td);}el('metricTableBody').append(ctr);
  el('metricSource').replaceChildren(document.createTextNode(rows.length?'Серия: '+rows[0].scope+' · '+rows[0].entity+' · '+rows[0].timezone+' · ':'Данные по тегам Rednote пока не получены. Нужен подтверждённый экспорт или доступ к кабинету.'));
  if(rows.length){const a=document.createElement('a');a.href=rows[0].source;a.textContent='Источник ↗';a.target='_blank';a.rel='noopener noreferrer';el('metricSource').append(a);}
 }
 el('metricSeries').onchange=renderAnalytics;el('metricEnd').value=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());el('metricEnd').onchange=renderAnalytics;
 async function loadResearch(){try{const response=await fetch(new URL('research.json',document.baseURI));if(!response.ok)throw Error();research=await response.json();
   el('researchTerms').replaceChildren();for(const item of research.terms){const div=document.createElement('article'),head=document.createElement('strong'),p=document.createElement('p');div.className='task';head.textContent=item.cn+' — '+item.ru;p.textContent=item.evidence+' · '+item.condition;div.append(head,p);el('researchTerms').append(div);}
   el('researchStatus').textContent=research.status;for(const source of research.sources){const a=document.createElement('a');a.href=source.url;a.textContent=source.name+' ↗';a.target='_blank';a.rel='noopener noreferrer';a.className='research-link';el('researchSources').append(a);}
  }catch{el('researchStatus').textContent='Отчёт исследования не загрузился; см. docs/RESEARCH-REDNOTE.md в репозитории.';}}
 el('rememberContent').checked=read('most-remember',true);el('rememberContent').onchange=()=>write('most-remember',el('rememberContent').checked);
 async function loadSeed(){try{const r=await fetch(new URL('knowledge-seed.json',document.baseURI));if(!r.ok)return;const data=await r.json();if(read('most-knowledge-seed-version',null)===data.version)return;for(const item of data.items||[])knowledge=rememberContent(knowledge,{...item,status:'unreviewed'});if(persistKB())write('most-knowledge-seed-version',data.version);renderKnowledge();}catch{}}
 renderSeries();renderAnalytics();renderKnowledge();loadResearch();loadSeed();
 return {capture,renderKnowledge,renderAnalytics,exportData:()=>({knowledge,metrics})};
}
