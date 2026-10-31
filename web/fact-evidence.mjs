import {isAutomaticPhotoObservation} from './photo-observation-policy.mjs';
export function normalizeRussian(text){const map={a:'а',b:'в',c:'с',e:'е',h:'н',k:'к',m:'м',o:'о',p:'р',t:'т',x:'х',y:'у'};return String(text||'').toLowerCase().replace(/ё/g,'е').replace(/[a-zа-я]+/g,word=>/[а-я]/.test(word)?word.replace(/[abcehkmoptxy]/g,c=>map[c]):word);}
const furniturePatterns={
 bed:'двуспальн[а-я]*\\s+кроват[а-я]*|двойн[а-я]*\\s+кроват[а-я]*',bedAny:'кроват(?:ь|и|ью|ей)',wardrobe:'шкаф(?:ы|а|ом|ов)?',slidingWardrobe:'шкаф(?:а|ы|ов)?[ -]?купе',desk:'(?:письменн|рабоч|учебн)[а-я]*\\s+стол[а-я]*',
 sofa:'диван[а-я]*',armchair:'кресл[а-я]*',tv:'телевизор[а-я]*|тв',tvStand:'тв[ -]?тумб[а-я]*|тумб[а-я]*\\s+(?:под|для)\\s+(?:тв|телевизор[а-я]*)',nightstands:'прикроватн[а-я]*\\s+тумб[а-я]*',
 ac:'кондиционер[а-я]*',internet:'wi[ -]?fi|wifi|интернет[а-я]*',fridge:'холодильник[а-я]*',stove:'(?:электро|газо)?плит[а-я]*|варочн[а-я]*\\s+(?:панел[а-я]*|плит[а-я]*)',oven:'духовк[а-я]*|духов[а-я]*\\s+шкаф[а-я]*',hood:'вытяжк[а-я]*',sink:'(?:кухонн[а-я]*\\s+)?(?:раковин[а-я]*|мойк[а-я]*)',washbasin:'раковин[а-я]*|умывальник[а-я]*|рукомойник[а-я]*',microwave:'микроволнов[а-я]*(?:\\s+печ[а-я]*)?|свч',dishwasher:'посудомо[а-я]*(?:\\s+машин[а-я]*)?',kettle:'чайник[а-я]*',table:'обеденн[а-я]*\\s+стол[а-я]*|стол(?:а|ом|ы|ов)?',chairs:'стул[а-я]*|стуль[а-я]*',washer:'стиральн[а-я]*(?:\\s+машин[а-я]*)?|стиралк[а-я]*',dryer:'сушильн[а-я]*(?:\\s+машин[а-я]*)?',bath:'ванн(?:а|у|ой|ы|е)',shower:'душ(?:а|ем)?|душев[а-я]*\\s+кабин[а-я]*',closet:'гардеробн[а-я]*',pantry:'кладов[а-я]*',shelves:'стеллаж[а-я]*|полк(?:а|и|ами|ах|ок|у)',computer:'компьютер[а-я]*|моноблок[а-я]*',projector:'проектор[а-я]*',homeCinema:'домашн[а-я]*\\s+кинотеатр[а-я]*',piano:'пианино|фортепиано|роял[а-я]*|цифров[а-я]*\\s+пианино',balcony:'балкон[а-я]*',loggia:'лоджи[а-я]*',kitchenFurniture:'мебел[а-я]*\\s+на\\s+кухне|кухонн[а-я]*\\s+(?:мебел[а-я]*|гарнитур[а-я]*|шкаф[а-я]*)|шкаф[а-я]*\\s+на\\s+кухн[а-я]*',roomFurniture:'мебел[а-я]*\\s+в\\s+комнат[а-я]*',mattress:'матрас[а-я]*'
};
const sourceBooleans={fridge:'hasFridge',dishwasher:'hasDishwasher',washer:'hasWasher',ac:'hasConditioner',tv:'hasTv',internet:'hasInternet',bath:'hasBathtub',shower:'hasShower',kitchenFurniture:'hasKitchenFurniture',roomFurniture:'hasFurniture'};
Object.assign(furniturePatterns,{
 coffeeTable:'журнальн[а-я]*\\s+стол(?:ик[а-я]*|[а-я]*)',dresser:'комод[а-я]*',cabinet:'буфет[а-я]*|тумб[а-я]*',shoeCabinet:'обувниц[а-я]*|шкаф[а-я]*\\s+для\\s+обуви',ottoman:'пуф(?:ик)?[а-я]*|банкетк[а-я]*',bench:'скам(?:ья|ьи|ей|ью|ейк[а-я]*)',mirror:'зеркал[а-я]*',curtains:'штор[а-я]*',dryingRack:'сушилк[а-я]*\\s+для\\s+белья',iron:'утюг[а-я]*',vacuum:'пылесос[а-я]*',coffeeMachine:'кофемашин[а-я]*|кофевар[а-я]*',toaster:'тостер[а-я]*',router:'роутер[а-я]*|маршрутизатор[а-я]*',monitor:'монитор[а-я]*',speakers:'колонк[а-я]*|акустическ[а-я]*\\s+систем[а-я]*',waterHeater:'водонагревател[а-я]*|бойлер[а-я]*',waterFilter:'(?:фильтр[а-я]*|систем[а-я]*\\s+фильтрации)\\s+(?:для\\s+)?вод[а-я]*'
});
export function detectFurniture(text,features,offer={},origin='JSON'){
 const raw=String(text||''),t=normalizeRussian(raw),negative=[];
 for(const m of t.matchAll(/(?<![а-яa-z])(?:кроме|за исключением|без|нет|отсутствует|не будет|не предусмотрен[а-я]*)\s+/gu)){
  const rest=t.slice(m.index+m[0].length),end=rest.search(/[);\n.!?]|(?:,?\s+)(?:но|зато|есть|имеется)(?![а-я])/u);
  negative.push([m.index,m.index+m[0].length+(end<0?rest.length:end)]);
 }
 const rows=features.map(([id,,label,,,])=>{
  const expression=furniturePatterns[id];const matches=[];
  if(expression)for(const m of t.matchAll(new RegExp('(?<![а-яa-z])(?:'+expression+')(?![а-яa-z])','gu'))){
   const before=t.slice(Math.max(0,m.index-75),m.index),after=t.slice(m.index+m[0].length,m.index+m[0].length+65);
   const clauseBefore=before.split(/[.!?;\n]/).at(-1),clauseAfter=after.split(/[.!?;\n]/)[0],clause=clauseBefore+m[0]+clauseAfter;
   const kitchenContext=/кухн[а-я]*|кухонн[а-я]*/.test(clause),bathContext=/ванн[а-я]*|сануз[а-я]*|душев[а-я]*|туалет[а-я]*|умывальник[а-я]*|рукомойник[а-я]*/.test(clause);
   if(id==='tv'&&(/^[ -]?тумб/.test(after)||/тумб[а-я]*\s+(?:под|для)\s+$/.test(before)))continue;
   if(id==='wardrobe'&&/(?:духов[а-я]*|кухонн[а-я]*)\s+$/.test(before))continue;
   if(['wardrobe','slidingWardrobe'].includes(id)&&kitchenContext&&!/спальн[а-я]*|гардероб[а-я]*|одежд[а-я]*|в\s+комнат[а-я]*/.test(clause))continue;
   if(id==='sink'&&(bathContext||/раковин/.test(m[0])&&!kitchenContext))continue;
   if(id==='washbasin'&&(!bathContext||kitchenContext))continue;
   if(id==='table'&&/(?:письменн|рабоч|учебн)[а-я]*\s+$/.test(before))continue;
   if(id==='table'&&/журнальн[а-я]*\s+$/.test(before))continue;
   if(id==='cabinet'&&/(?:прикроватн[а-я]*|тв[ -]?|телевизионн[а-я]*)\s*$/.test(before))continue;
   let status='present';
   if(negative.some(([a,b])=>m.index>=a&&m.index<b)||/^\s*(?:отсутствует|нет|не будет|не предусмотрен[а-я]*)/.test(after))status='absent';
   else if(/(?:может|можно|планир[а-я]*|готов[аы]?)\s+(?:[а-я]+\s*){0,3}(?:установить|купить|привезти|поставить)\s*$|(?:будет установлен[ао]?|установит|установят|купим|привезем|поставим)\s*$/.test(before)||/^\s*(?:будет (?:установлен[ао]?|куплен[ао]?|привезен[ао]?|поставлен[ао]?)|установят|привезут|купят|(?:можно|планир[а-я]*)\s+(?:установить|купить|привезти|поставить))/.test(after))status='planned';
   const number=before.match(/(?:^|\s)(\d+)\s+(?:кухонн[а-я]*\s+|обеденн[а-я]*\s+)?$/)?.[1];
   const words={один:'1',одна:'1',одно:'1',два:'2',две:'2',три:'3',четыре:'4',пять:'5',шесть:'6',семь:'7',восемь:'8'};
   const written=before.match(/(?:^|\s)(один|одна|одно|два|две|три|четыре|пять|шесть|семь|восемь)\s+(?:кухонн[а-я]*\s+|обеденн[а-я]*\s+)?$/)?.[1];
   const minimum=/(?:минимум|не менее|как минимум)\s+(?:\d+|два|две|три|четыре)\s+(?:кухонн[а-я]*\s+|обеденн[а-я]*\s+)?$/.test(before);
   matches.push({status,text:raw.slice(Math.max(0,m.index-24),m.index+m[0].length+38).trim(),source:'Описание',count:number||words[written],countKind:minimum?'minimum':'exact'});
  }
  if(id==='kitchenFurniture'&&/из мебели\s*:\s*кухня/.test(t))matches.push({status:'present',source:'Описание',text:'Из мебели: кухня…'});
  const key=sourceBooleans[id];if(key&&typeof offer[key]==='boolean')matches.push({status:offer[key]?'present':'absent',source:origin+' '+key,text:key+' = '+offer[key]});
  const states=new Set(matches.map(m=>m.status));let status=states.has('absent')&&states.has('present')?'conflict':states.has('present')?'present':states.has('planned')?'planned':states.has('absent')?'absent':'unknown';
  const counted=matches.find(m=>m.count&&m.status==='present');
  return {id,label,status,matches,count:counted?.count||'',countKind:counted?.countKind||'exact'};
 });
 if(rows.find(r=>r.id==='bed')?.status==='present')rows.find(r=>r.id==='bedAny').status='covered';
 if(rows.find(r=>r.id==='slidingWardrobe')?.status==='present')rows.find(r=>r.id==='wardrobe').status='covered';
 return rows;
}

// Missing observations are unknown, never proof of absence. Keep the original
// source audit, while resolving positively identified objects independently.
export function resolvePhotoFurniture(fields={},catalog=[]){
 const allowed=new Set(catalog.map(row=>row[0])),features=new Set((Array.isArray(fields.features)?fields.features:[]).filter(id=>allowed.has(id)));
 const rejected=new Set(Array.isArray(fields.photoRejectedFeatures)?fields.photoRejectedFeatures:[]);
 for(const id of rejected)features.delete(id);
 const counts={...(fields.featureCounts||{})},visual=new Set(),confirmed=new Set(),evidence=[],conflicts=[];
 const reviewOnly=new Set(['internet','roomFurniture','closet','pantry','balcony','loggia','bed','homeCinema']);
 for(const row of Array.isArray(fields.photoEvidence)?fields.photoEvidence:[]){
  if(!row||!allowed.has(row.id)||rejected.has(row.id))continue;
  const human=row.status==='confirmed';
  const automatic=!reviewOnly.has(row.id)&&isAutomaticPhotoObservation(row);
  if(!human&&!automatic)continue;
  features.add(row.id);evidence.push(row);(human?confirmed:visual).add(row.id);
  // Different photographs can show the same chair. Never sum their counts.
  if(human&&Number.isInteger(row.count)&&row.count>=1&&row.count<=12)counts[row.id]=Math.max(Number(counts[row.id])||0,row.count);
 }
 for(const id of confirmed)visual.delete(id);
 for(const row of fields.sourceAudit?.furniture||[]){
  if(!features.has(row.id))continue;
  if(row.status==='present')visual.delete(row.id);
  if(['absent','planned','conflict'].includes(row.status)&&evidence.some(item=>item.id===row.id))conflicts.push({id:row.id,label:row.label,status:row.status,source:'photo/source'});
 }
 if(features.has('bed'))features.delete('bedAny');
 if(features.has('slidingWardrobe'))features.delete('wardrobe');
 return {features:[...features],featureCounts:counts,visualFeatures:[...visual],photoEvidence:evidence,conflicts};
}

export function detectConditions(text){const t=normalizeRussian(text),facts={},evidence=[],warnings=[];const put=(field,value,match)=>{facts[field]=value;evidence.push({field,value,source:'Описание / условия',text:match[0]});};let m;
 if((m=t.match(/(?:^|[\n.!?])\s*(\d+)\s*человек/)))put('occupants',m[1],m);
 if((m=t.match(/(?:договор[а-я]*\s+на|срок[а-я]*\s+(?:аренды\s+)?)\s*(\d+)\s*месяц/)))put('contractMonths',m[1],m);
 if((m=t.match(/срок аренды\s*от года|от\s*12\s*месяц/)))put('minMonths','12',m);
 if((m=t.match(/(?:долгосрочн[а-я]*\s+аренда|длительн[а-я]*\s+срок)/)))put('leaseTerm','long',m);
 if((m=t.match(/регистраци[а-я]*\s+(?:не\s+(?:сделаем|делаем|оформляем|предоставляем|будет)|невозможна)|без регистрации/)))put('registrationPolicy','not_available',m);
 if((m=t.match(/без\s+(?:животных|питомцев)|(?:животные|питомцы|с\s+животными|с\s+питомцами)\s*(?:запрещены|нельзя|не допускаются|не разрешено)|(?:нельзя|не разрешено|не допускается)\s+с\s+(?:животными|питомцами)/))){put('petPolicy','no',m);facts.pets=false;}
 else if((m=t.match(/(?<!не\s)(?:можно|разрешено|допускается)\s+(?:с\s+)?(?:детьми\s+и\s+)?(?:животными|питомцами)|(?:животные|питомцы)\s+разрешены/))){put('petPolicy','yes',m);facts.pets=true;}
 else if((m=t.match(/обсужда[а-я]+\s+(?:с\s+)?(?:животными|питомцами)/)))put('petPolicy','negotiable',m);
 if((m=t.match(/без\s+детей|(?:с\s+детьми|дети)\s*(?:нельзя|запрещены|не допускаются|не разрешено)|(?:нельзя|не разрешено|не допускается)\s+с\s+детьми/))){put('childrenPolicy','no',m);facts.children=false;}
 else if((m=t.match(/(?:можно|разрешено|допускается)\s+с\s+детьми|дети\s+разрешены/))){put('childrenPolicy','yes',m);facts.children=true;}
 if((m=t.match(/(?:две|2)\s+(?:отдельн[а-я]*|изолированн[а-я]*)\s+комнат[а-я]*|комнат[а-я]*\s+(?:изолированн[а-я]*|раздельн[а-я]*)/)))put('separate',true,m);
 if((m=t.match(/(?:некурящ[а-я]*|без вредных привычек|курени[а-я]*\s+запрещено)/)))put('smokingPolicy','no',m);
 if((m=t.match(/без\s+(?:компаний\s*[/,]\s*)?гостей|без компаний\s*[/,]\s*гостей/)))put('guestPolicy','no',m);
 if((m=t.match(/(?:субаренда|пересдача)[^\n]{0,100}?(?:исключена|запрещена|не допускается)/)))put('subletPolicy','no',m);
 if((m=t.match(/(?:посуточно|краткосрочн[а-я]*)[^.!?\n]{0,30}(?:исключена|запрещена)/))){put('shortTermPolicy','no',m);facts.shortTerm=false;}
 if((m=t.match(/без комиссии|комиссия с арендатора\s*(?:нет|0)/)))put('commission','0',m);
 if((m=t.match(/предоплата\s*(\d+)\s*месяц/)))put('prepayMonths',m[1],m);
 if((m=t.match(/оплата жкх включена\s*\(без счетчиков\)/)))put('utilities','meters',m);
 return {facts,evidence,warnings};}
