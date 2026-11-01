// Observations describe a whole publication. They never establish tag causality.
export function postStatsDraft(values){
 for(const key of ['views','rooms','area','rent'])if(values[key]===undefined||values[key]===null||String(values[key]).trim()==='')throw Error('Заполните просмотры и все характеристики для сопоставления');
 const tags=String(values.tags||'').split(/[\s,，]+/u).filter(Boolean);
 return {postId:String(values.postId||'').trim(),observedAt:String(values.observedAt||''),
  views:Number(values.views),tags,approximate:values.approximate!==false,
  facts:{metro:String(values.metro||'').trim(),rooms:Number(values.rooms),area:Number(values.area),rent:Number(values.rent)}};
}
export function setupTagPostStats({mount,api,getTags=()=>'',onSaved=()=>{}}){
 let epoch=0;const root=document.createElement('section'),inputs={};
 const make=(tag,text)=>{const n=document.createElement(tag);if(text)n.textContent=text;return n;};
 root.append(make('h3','Наблюдения публикаций и сопоставимые посты'),make('p','Просмотры относятся ко всему посту. Сравнение похожих квартир не доказывает эффект отдельного тега. Приблизительные значения и неизвестное время накопления просмотров будут отмечены.'));
 for(const [key,name,type]of [['postId','ID или ссылка опубликованного поста','text'],['observedAt','Дата наблюдения поста','date'],['views','Просмотры всего поста','number'],['tags','Все теги опубликованного поста','text'],['metro','Метро для сопоставления постов','text'],['rooms','Число комнат · 0 для студии','number'],['area','Площадь для сопоставления, м²','number'],['rent','Месячная аренда для сопоставления, ₽','number']]){
  const label=make('label',name),input=make('input');input.type=type;input.style.cssText='font-size:16px;min-height:44px;max-width:100%;';
  if(type==='number'){input.min='0';input.step=key==='area'?'0.1':'1';}label.append(input);root.append(label);inputs[key]=input;
 }
 inputs.observedAt.value=new Intl.DateTimeFormat('en-CA',{timeZone:'Europe/Moscow',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
 const approximateLabel=make('label','Просмотры приблизительные'),approximate=make('input');approximate.type='checkbox';approximate.checked=true;approximateLabel.prepend(approximate);root.append(approximateLabel);
 const save=make('button','Подтверждаю наблюдение поста'),status=make('p');save.type='button';status.setAttribute('role','status');
 save.onclick=async()=>{const current=++epoch;save.disabled=true;try{
  const values=Object.fromEntries(Object.entries(inputs).map(([key,input])=>[key,input.value]));
  if(!values.postId||!values.observedAt||values.views==='')throw Error('Заполните ID, дату и просмотры поста');
  const post=postStatsDraft({...values,approximate:approximate.checked});
  await api('learning',{action:'record-post-stat',post,approved:true});if(current!==epoch)return;status.textContent='Наблюдение сохранено. Все теги связаны с одним постом, а не с отдельными выдуманными просмотрами.';onSaved();
 }catch(error){if(current===epoch)status.textContent=error.message;}finally{if(current===epoch)save.disabled=false;}};
 root.append(save,status);
 const eligibleLabel=make('label','Подходящие по фактам теги для сравнения'),eligible=make('input');eligible.type='text';eligible.value=getTags()||'';eligible.style.cssText='font-size:16px;min-height:44px;max-width:100%;';eligibleLabel.append(eligible);
 const verifiedLabel=make('label','Я проверил, что эти теги соответствуют фактам текущей квартиры'),verified=make('input');verified.type='checkbox';verifiedLabel.prepend(verified);
 const compare=make('button','Сравнить с подходящими контрольными постами'),output=make('div');compare.type='button';
 compare.onclick=async()=>{const current=++epoch;try{if(!verified.checked)throw Error('Сначала подтвердите соответствие тегов фактам квартиры');
  const tags=eligible.value.split(/[\s,，]+/u).filter(Boolean);const result=await api('learning',{action:'compare-post-stats',eligibleTags:tags});if(current!==epoch)return;output.replaceChildren();
  output.append(make('p','Уникальных постов: '+result.uniquePosts+'. Повторных наблюдений исключено: '+result.repeatedObservationsExcluded+'.'));
  for(const row of result.tags||[]){output.append(make('p',row.tag+' · сопоставимых пар: '+row.matchedPairs+(row.medianPairedViewDifference===null?' · данных для сравнения нет':' · медианная разница просмотров постов: '+row.medianPairedViewDifference)+(row.smallSample?' · малая выборка':'')+(row.approximatePairs?' · приблизительных пар: '+row.approximatePairs:'')+(row.exposureUnmatchedPairs?' · время экспозиции не подтверждено для '+row.exposureUnmatchedPairs+' пар':'')));}
  output.append(make('p','Это описательное сравнение постов. Причинный эффект и популярность отдельных тегов не установлены.'));
 }catch(error){if(current===epoch)output.textContent=error.message;}};
 root.append(eligibleLabel,verifiedLabel,compare,output);mount.append(root);
 return {clear(){epoch++;for(const input of Object.values(inputs))input.value='';eligible.value='';verified.checked=false;approximate.checked=true;status.textContent='';output.replaceChildren();save.disabled=false;},destroy(){epoch++;root.remove();}};
}
