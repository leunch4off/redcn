export function normalizeRussian(text){const map={a:'а',b:'в',c:'с',e:'е',h:'н',k:'к',m:'м',o:'о',p:'р',t:'т',x:'х',y:'у'};return String(text||'').toLowerCase().replace(/ё/g,'е').replace(/[a-zа-я]+/g,word=>/[а-я]/.test(word)?word.replace(/[abcehkmoptxy]/g,c=>map[c]):word);}
const furniturePatterns={
 bed:'двуспальн[а-я]*\\s+кроват[а-я]*|двойн[а-я]*\\s+кроват[а-я]*',bedAny:'кроват(?:ь|и|ью|ей)',wardrobe:'шкаф(?:ы|а|ом|ов)?',slidingWardrobe:'шкаф(?:а|ы|ов)?[ -]?купе',desk:'(?:письменн|рабоч|учебн)[а-я]*\\s+стол[а-я]*',
 sofa:'диван[а-я]*',armchair:'кресл[а-я]*',tv:'телевизор[а-я]*|тв',tvStand:'тв[ -]?тумб[а-я]*|тумб[а-я]*\\s+(?:под|для)\\s+(?:тв|телевизор[а-я]*)',nightstands:'прикроватн[а-я]*\\s+тумб[а-я]*',
 ac:'кондиционер[а-я]*',internet:'wi[ -]?fi|wifi|интернет[а-я]*',fridge:'холодильник[а-я]*',stove:'плит[а-я]*|варочн[а-я]*\\s+панел[а-я]*',oven:'духовк[а-я]*|духов[а-я]*\\s+шкаф[а-я]*',microwave:'микроволнов[а-я]*(?:\\s+печ[а-я]*)?|свч',dishwasher:'посудомо[а-я]*(?:\\s+машин[а-я]*)?',kettle:'чайник[а-я]*',table:'обеденн[а-я]*\\s+стол[а-я]*|стол(?:а|ом|ы|ов)?',chairs:'стул[а-я]*|стуль[а-я]*',washer:'стиральн[а-я]*(?:\\s+машин[а-я]*)?|стиралк[а-я]*',dryer:'сушильн[а-я]*(?:\\s+машин[а-я]*)?',bath:'ванн(?:а|у|ой|ы|е)',shower:'душ(?:а|ем)?|душев[а-я]*\\s+кабин[а-я]*',closet:'гардеробн[а-я]*',balcony:'балкон[а-я]*',loggia:'лоджи[а-я]*',kitchenFurniture:'мебел[а-я]*\\s+на\\s+кухне|кухонн[а-я]*\\s+(?:мебел[а-я]*|гарнитур[а-я]*)',roomFurniture:'мебел[а-я]*\\s+в\\s+комнат[а-я]*',mattress:'матрас[а-я]*'
};
const sourceBooleans={fridge:'hasFridge',dishwasher:'hasDishwasher',washer:'hasWasher',ac:'hasConditioner',tv:'hasTv',internet:'hasInternet',bath:'hasBathtub',shower:'hasShower',kitchenFurniture:'hasKitchenFurniture',roomFurniture:'hasFurniture'};
export function detectFurniture(text,features,offer={}){
 const raw=String(text||''),t=normalizeRussian(raw),negative=[];
 for(const m of t.matchAll(/(?<![а-яa-z])(?:кроме|за исключением|без|нет|отсутствует)\s+/gu)){
  const rest=t.slice(m.index+m[0].length),end=rest.search(/[);\n.!?]|(?:,?\s+)(?:но|зато|есть|имеется)(?![а-я])/u);
  negative.push([m.index,m.index+m[0].length+(end<0?rest.length:end)]);
 }
 const rows=features.map(([id,,label,,,])=>{
  const expression=furniturePatterns[id];const matches=[];
  if(expression)for(const m of t.matchAll(new RegExp('(?<![а-яa-z])(?:'+expression+')(?![а-яa-z])','gu'))){
   const before=t.slice(Math.max(0,m.index-75),m.index),after=t.slice(m.index+m[0].length,m.index+m[0].length+65);
   if(id==='tv'&&(/^[ -]?тумб/.test(after)||/тумб[а-я]*\s+(?:под|для)\s+$/.test(before)))continue;
   if(id==='wardrobe'&&/духов[а-я]*\s+$/.test(before))continue;
   if(id==='table'&&/(?:письменн|рабоч|учебн)[а-я]*\s+$/.test(before))continue;
   let status='present';
   if(negative.some(([a,b])=>m.index>=a&&m.index<b)||/^\s*(?:отсутствует|нет|не будет)/.test(after))status='absent';
   else if(/(?:может|можно|планирует|планируется|готов[аы]?)\s+(?:[а-я]+\s*){0,3}(?:установить|купить|привезти)\s*$|(?:будет установлен[ао]?|установит|установят)\s*$/.test(before)||/^\s*(?:будет установлен[ао]?|установят|привезут|купят)/.test(after))status='planned';
   matches.push({status,text:raw.slice(Math.max(0,m.index-24),m.index+m[0].length+38).trim(),source:'Описание',count:before.match(/(?:^|\s)(\d+)\s+$/)?.[1]});
  }
  if(id==='kitchenFurniture'&&/из мебели\s*:\s*кухня/.test(t))matches.push({status:'present',source:'Описание',text:'Из мебели: кухня…'});
  const key=sourceBooleans[id];if(key&&typeof offer[key]==='boolean')matches.push({status:offer[key]?'present':'absent',source:'JSON '+key,text:key+' = '+offer[key]});
  const states=new Set(matches.map(m=>m.status));let status=states.has('absent')&&states.has('present')?'conflict':states.has('planned')?'planned':states.has('absent')?'absent':states.has('present')?'present':'unknown';
  return {id,label,status,matches,count:matches.find(m=>m.count)?.count||''};
 });
 if(rows.find(r=>r.id==='bed')?.status==='present')rows.find(r=>r.id==='bedAny').status='covered';
 if(rows.find(r=>r.id==='slidingWardrobe')?.status==='present')rows.find(r=>r.id==='wardrobe').status='covered';
 return rows;
}

export function detectConditions(text){const t=normalizeRussian(text),facts={},evidence=[],warnings=[];const put=(field,value,match)=>{facts[field]=value;evidence.push({field,value,source:'Описание / условия',text:match[0]});};let m;
 if((m=t.match(/(?:^|[\n.!?])\s*(\d+)\s*человек/)))put('occupants',m[1],m);
 if((m=t.match(/(?:договор[а-я]*\s+на|срок[а-я]*\s+(?:аренды\s+)?)\s*(\d+)\s*месяц/)))put('contractMonths',m[1],m);
 if((m=t.match(/срок аренды\s*от года|от\s*12\s*месяц/)))put('minMonths','12',m);
 if((m=t.match(/(?:долгосрочн[а-я]*\s+аренда|длительн[а-я]*\s+срок)/)))put('leaseTerm','long',m);
 if((m=t.match(/регистраци[а-я]*\s+(?:не\s+(?:сделаем|делаем|оформляем|предоставляем|будет)|невозможна)|без регистрации/))){put('registrationPolicy','not_available',m);facts.registration='none';}
 if((m=t.match(/без\s+(?:животных|питомцев)|(?:животные|питомцы)\s*(?:запрещены|не допускаются)/))){put('petPolicy','no',m);facts.pets=false;}
 else if((m=t.match(/(?:можно|разрешено|обсужда[а-я]+)\s+(?:с\s+)?(?:животными|питомцами)/))){put('petPolicy','negotiable',m);facts.pets=true;}
 if((m=t.match(/(?:некурящ[а-я]*|без вредных привычек|курени[а-я]*\s+запрещено)/)))put('smokingPolicy','no',m);
 if((m=t.match(/без\s+(?:компаний\s*[/,]\s*)?гостей|без компаний\s*[/,]\s*гостей/)))put('guestPolicy','no',m);
 if((m=t.match(/(?:субаренда|пересдача)[^\n]{0,100}?(?:исключена|запрещена|не допускается)/)))put('subletPolicy','no',m);
 if((m=t.match(/(?:посуточно|краткосрочн[а-я]*)[^.!?\n]{0,30}(?:исключена|запрещена)/))){put('shortTermPolicy','no',m);facts.shortTerm=false;}
 if((m=t.match(/без комиссии|комиссия с арендатора\s*(?:нет|0)/)))put('commission','0',m);
 if((m=t.match(/предоплата\s*(\d+)\s*месяц/)))put('prepayMonths',m[1],m);
 if((m=t.match(/оплата жкх включена\s*\(без счетчиков\)/)))put('utilities','meters',m);
 return {facts,evidence,warnings};}
