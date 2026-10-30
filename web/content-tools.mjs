// User-supplied editorial tag catalogue. This is not a popularity ranking or live analytics.
export const TAG_CATALOG = [
  ['莫斯科','Москва','city'],['莫斯科生活','Жизнь в Москве','city'],['莫斯科留学','Учёба в Москве','student'],
  ['莫斯科留学生','Иностранные студенты в Москве','student'],['莫斯科通勤','Дорога и поездки в Москве','transport'],
  ['莫斯科地铁','Московское метро','transport'],['莫斯科房产','Недвижимость Москвы','city'],['莫斯科外派','Работа по направлению / командировке','professional'],
  ['租','Аренда','general'],['租房','Аренда жилья','general'],['莫斯科租房','Аренда жилья в Москве','base'],
  ['莫斯科租房信息','Информация об аренде жилья','base'],['莫斯科租房服务','Услуги аренды жилья','service'],
  ['莫斯科租房中介','Агент по аренде','manual'],['莫斯科转租','Пересдача / субаренда','manual'],
  ['莫斯科合租','Совместная аренда','sharing'],['留学生租房','Аренда для иностранных студентов','student'],
  ['年轻人租房','Аренда для молодых людей','manual'],['FIT租房','Аренда, связанная с FIT; значение нужно уточнить','manual'],
  ['过上整租生活','Жизнь в отдельно арендованной квартире','general'],['租房也要幸福','Комфортная жизнь в аренде','general'],
  ['公寓','Квартира / апартаменты','general'],['在家','Домашняя атмосфера','general'],
  ['拎包入住','Полностью готово к заселению','ready'],['全新装修','Новый ремонт','renovation'],['近地铁','Рядом с метро','near'],
  ['落地签','Разговорный неоднозначный тег; не выбирается автоматически для регистрации','manual'],
  ['留学生首选','Предпочтительный вариант для студентов; оценочное утверждение','manual'],
  ['莫斯科一居','Однокомнатный вариант в Москве','one'],['莫斯科一居室','Однокомнатная квартира','one'],
  ['莫斯科一室一厅','Спальня и гостиная в Москве','euro2'],['一室一厅','Спальня и гостиная','euro2'],
  ['莫斯科两居','Двухкомнатный вариант','two'],['莫斯科二居','Двухкомнатный вариант','two'],['莫斯科两居室','Двухкомнатная квартира','two'],
  ['两居室','Двухкомнатная квартира','two'],['莫斯科独立两居','Две изолированные комнаты','twoSeparate'],
  ['莫斯科独立两居室','Двухкомнатная квартира с изолированными комнатами','twoSeparate'],
  ['莫斯科两室一厅','Две спальни и гостиная','euro3'],['莫斯科三居','Трёхкомнатный вариант','three'],
  ['莫斯科独立三居','Три изолированные комнаты','threeSeparate'],['莫斯科四居','Четырёхкомнатный вариант','four'],
  ['莫斯科西南','Юго-запад Москвы','southwest'],['莫斯科租房西南','Аренда на юго-западе Москвы','southwest'],
  ['莫斯科西南租房','Аренда на юго-западе Москвы','southwest'],['莫斯科西南区租房','Аренда в юго-западной части Москвы','southwest'],
  ['莫斯科国立大学','МГУ — полное китайское название','msu'],['莫大','МГУ — сокращённое название','msu'],['莫大租房','Аренда для студентов МГУ','msu'],
  ['人民友谊大学','РУДН','rudn'],['俄罗斯人民友谊大学','РУДН — полное название','rudn'],['莫斯科友大租房','Аренда для студентов РУДН','rudn'],
  ['经济学院','Экономический институт / факультет; не конкретный вуз','manual'],['音乐学院','Музыкальный институт / консерватория; не конкретный вуз','manual'],
];

export function suggestTags(v, rules, translateFn, routesFn) {
  const seed=Math.abs(Number(v.tagVariant??v.titleVariant)||0), candidates=[];
  const add=(tag,reason)=>{tag=tag.replace(/^#/,'');if(!candidates.some(x=>x.tag===tag))candidates.push({tag,reason,ru:TAG_CATALOG.find(x=>x[0]===tag)?.[1]||'Тег из указанного названия'});};
  add('莫斯科租房','Основная тема');add('莫斯科租房信息','Информация об объекте');
  const routes=routesFn(v.transit),universities=routesFn(v.universities);
  if(!routes.length&&v.metro)routes.push({name:v.metro,time:v.walk,mode:'walk'});
  if(!universities.length&&v.university)universities.push({name:v.university});
  for(const route of routes){const name=translateFn(route.name,rules).replace(/[\s#]/g,'');if(!/[а-яё]/i.test(name))add(name,'Станция указана в карточке');}
  for(const university of universities){const zh=translateFn(university.name,rules);if(!/[а-яё]/i.test(zh))add(zh.replace(/[\s#]/g,''),'Вуз указан в карточке');
    if(/МГУ|莫斯科国立大学|莫大/i.test(university.name+' '+zh))add('莫大租房','Указан МГУ');
    if(/РУДН|RUDN|人民友谊/i.test(university.name+' '+zh))add('莫斯科友大租房','Указан РУДН');
  }
  if(v.campus){const zh=translateFn(v.campus,rules).replace(/[\s#]/g,'');if(!/[а-яё]/i.test(zh))add(zh,'Кампус указан вручную');}
  if(v.layout==='studio')add('莫斯科Studio','Планировка: студия');
  else if(v.layout==='euro2')add('莫斯科一室一厅','Спальня + кухня-гостиная');
  else if(v.layout==='euro3')add('莫斯科两室一厅','Две спальни + кухня-гостиная');
  else {const groups={1:'one',2:v.separate?'twoSeparate':'two',3:v.separate?'threeSeparate':'three',4:'four'};const options=TAG_CATALOG.filter(x=>x[2]===groups[v.rooms]);if(options.length)add(options[seed%options.length][0],'Планировка из карточки');}
  if(Number(v.rooms)===2&&v.separate===true&&!['studio','euro2'].includes(v.layout))add('莫斯科合租','Две изолированные комнаты; приведён пример деления бюджета, условия заселения требуют согласования');
  if(v.audience==='student')add(seed%2?'留学生租房':'莫斯科留学','Оператор выбрал студенческую аудиторию');
  if(v.audience==='professional')add('莫斯科外派','Выбрана рабочая аудитория');
  if(routes.length)add(seed%2?'莫斯科地铁':'莫斯科通勤','Есть транспортный блок');
  if(routes.some(r=>r.mode==='walk'&&/^\d+$/.test(String(r.time))&&Number(r.time)<=10))add('近地铁','Пешком до метро не более 10 минут по введённым данным');
  if(v.region==='southwest'){const options=TAG_CATALOG.filter(x=>x[2]==='southwest');add(options[seed%options.length][0],'Район отмечен оператором');}
  if(v.ready)add('拎包入住','Готовность к заселению подтверждена отметкой');
  if(v.newRenovation)add('全新装修','Новый ремонт подтверждён отметкой');
  const featureTags={desk:['带书桌','Подтверждён рабочий стол'],piano:['带钢琴','Подтверждено пианино'],
    computer:['配有电脑','Подтверждён компьютер'],oven:['配有烤箱','Подтверждена духовка'],
    washer:['配有洗衣机','Подтверждена стиральная машина'],dishwasher:['配有洗碗机','Подтверждена посудомоечная машина'],
    ac:['配有空调','Подтверждён кондиционер'],closet:['独立衣帽间','Подтверждена гардеробная'],
    pantry:['带储藏室','Подтверждена кладовая'],balcony:['带阳台','Подтверждён балкон']};
  const selected=(v.features||[]).filter(id=>featureTags[id]);
  if(selected.length){const id=selected[seed%selected.length],[tag,reason]=featureTags[id];add(tag,reason);}
  if(v.pets&&!['no','conflict','negotiable'].includes(v.petPolicy))add('可带宠物','Разрешение на животных подтверждено источником или оператором');
  if(v.children&&!['no','conflict'].includes(v.childrenPolicy))add('可带孩子','Разрешение на детей подтверждено источником или оператором');
  add(seed%2?'租房也要幸福':'莫斯科生活','Общий редакционный тег');
  return candidates;
}

export const PHRASES = [
  ['莫斯科','Москва',['俄罗斯莫斯科']],['租房','Аренда жилья',['房屋出租']],['月租','Аренда в месяц',['每月租金']],
  ['租金','Арендная плата',['房租']],['押金','Залог',['租赁押金']],['面积','Площадь',['总面积']],
  ['独立卧室','Отдельная спальня',['单独卧室']],['厨房客厅','Кухня-гостиная',['客餐厨空间']],
  ['独立厨房','Отдельная кухня',['单独厨房']],['一室一厅','Одна спальня и гостиная',['一卧一厅']],
  ['两室一厅','Две спальни и гостиная',['两卧一厅']],['整套公寓','Квартира целиком',['整租公寓']],
  ['双人床','Двуспальная кровать',['双人大床']],['大型衣柜','Большой шкаф',['大衣柜']],
  ['客厅沙发','Диван в гостиной',['客厅里的沙发']],['冰箱','Холодильник',[]],['洗衣机','Стиральная машина',[]],
  ['洗碗机','Посудомоечная машина',[]],['空调','Кондиционер',[]],['电视','Телевизор',[]],['浴缸','Ванна',[]],
  ['烤箱','Духовка',[]],['微波炉','Микроволновая печь',[]],['电热水壶','Электрический чайник',[]],
  ['抽油烟机','Кухонная вытяжка',[]],['灶台','Варочная панель',[]],['水槽','Раковина / мойка',[]],
  ['洗手盆','Раковина в ванной / умывальник',[]],
  ['书桌','Рабочий / письменный стол',[]],['电脑','Компьютер',[]],['钢琴','Пианино / фортепиано',[]],
  ['投影仪','Проектор',[]],['家庭影院','Домашний кинотеатр',[]],['储藏室','Кладовая',[]],['置物架','Стеллаж / полки',[]],
  ['阳台','Балкон',[]],['独立衣帽间','Отдельная гардеробная',['单独衣帽间']],
  ['步行','Пешком',['走路']],['公共交通','Общественный транспорт',[]],['地铁','Метро',[]],['分钟','Минуты',[]],
  ['临时居住登记','Временная регистрация',[]],['免费协助办理','Бесплатно помогаем оформить',[]],
  ['抵达登记（临时居住登记）','Виза прибытия (временная регистрация) — содействие студии оформлению регистрации, без обещания выдачи визы',[]],
  ['免费接送到家','Бесплатный трансфер до дома',['免费接送至住处']],
  ['协助搬运行李','Помогаем с багажом',['协助搬送行李']],['中文沟通','Общение на китайском',['提供中文沟通']],
  ['我们的优势','Наши преимущества',['我们的服务优势']],['欢迎中文咨询','Можно обратиться на китайском',['欢迎用中文咨询']],
  ['预约看房','Запись на просмотр',['预约参观房源']],['独立两居','Две отдельные комнаты',[]],
  ['莫斯科国立大学','Московский государственный университет',['莫大']],['俄罗斯人民友谊大学','РУДН',['人民友谊大学']],
];
export function phraseEntries(rules={},custom=[]) {
  const map=new Map(PHRASES.map(([zh,ru,alternatives])=>[zh,{zh,ru,alternatives}]));
  for(const [ru,zh] of Object.entries(rules))if(zh&&!map.has(zh))map.set(zh,{zh,ru,alternatives:[]});
  for(const entry of custom)if(entry&&typeof entry.zh==='string'&&entry.zh&&typeof entry.ru==='string')map.set(entry.zh,{...entry,alternatives:Array.isArray(entry.alternatives)?entry.alternatives:[]});
  return [...map.values()].sort((a,b)=>b.zh.length-a.zh.length);
}
export function tokenize(text,entries) {
  const tokens=[];let offset=0;
  const segmenter=typeof Intl.Segmenter==='function'?new Intl.Segmenter('zh',{granularity:'word'}):null;
  while(offset<text.length){const known=entries.find(e=>text.startsWith(e.zh,offset));if(known){tokens.push({text:known.zh,start:offset,end:offset+known.zh.length,entry:known});offset+=known.zh.length;continue;}
    let end=offset+String.fromCodePoint(text.codePointAt(offset)).length;
    if(segmenter){const first=segmenter.segment(text.slice(offset))[Symbol.iterator]().next().value?.segment;if(first)end=offset+first.length;}
    // Never allow an unknown segment to consume the start of a known phrase.
    for(let i=offset+1;i<end;i++)if(entries.some(e=>text.startsWith(e.zh,i))){end=i;break;}
    tokens.push({text:text.slice(offset,end),start:offset,end,entry:null});offset=end;
  }return tokens;
}
export function replaceSpan(text,start,end,expected,replacement){if(text.slice(start,end)!==expected)throw Error('Текст изменился. Выберите слово заново.');return text.slice(0,start)+replacement+text.slice(end);}
export function listingEligible(seller,commission){return seller==='owner'||seller==='agent'&&commission!==''&&commission!==null&&commission!==undefined&&Number.isFinite(Number(commission))&&Number(commission)>=0&&Number(commission)<=50;}
