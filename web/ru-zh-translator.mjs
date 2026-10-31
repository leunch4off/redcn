import {COMMUNITY_RENTAL_ENTRIES,COMMUNITY_RENTAL_META} from './community-phrases.mjs';
import {translateGeo} from './geo-catalog.mjs';
import {RENTAL_LEXICON,RENTAL_RULES,RENTAL_LEXICON_META} from './rental-lexicon.mjs';
import {UNIVERSITY_CATALOG,universityRules,resolveUniversity,resolveCampus} from './university-catalog.mjs';

export {RENTAL_LEXICON,RENTAL_RULES,RENTAL_LEXICON_META};
export const rentalRules=RENTAL_RULES;
const ruZhFold=value=>String(value??'').normalize('NFC').toLowerCase().replace(/\u0301/g,'').replaceAll('ё','е').replace(/[‐‑–—]/g,'-').replace(/\s+/g,' ').trim();
const ruZhCyrillic=/[\u0400-\u052f]/u;
const ruZhWordChar=/[\p{L}\p{N}_]/u;
let ruZhCoreIndex=null,ruZhOpenRows=[],ruZhOpenForms=new Map(),ruZhOpenIndex=null,ruZhOpenMeta=null,ruZhLoading=null;
function ruZhIndex(rows){const map=new Map(),prefix=new Map();for(const row of rows){const key=ruZhFold(row.ru);if(!key)continue;if(!map.has(key))map.set(key,row);const first=key[0];if(!prefix.has(first))prefix.set(first,[]);prefix.get(first).push({...row,key});}for(const list of prefix.values())list.sort((a,b)=>b.key.length-a.key.length);return {map,prefix};}
function ruZhAcademicEntries(){return Object.entries(universityRules()).map(([ru,zh])=>{const row=resolveCampus(ru)||resolveUniversity(ru);return {ru,zh,category:row?.kind||'university',source:'university-catalog',sourceIds:row?.sourceIds||[],reviewed:row?.translationStatus==='official'};});}
function ruZhCore(){return ruZhCoreIndex||(ruZhCoreIndex=ruZhIndex([...ruZhAcademicEntries(),...COMMUNITY_RENTAL_ENTRIES.map(([ru,zh,category])=>({ru,zh,category,source:'community-rental',reviewed:true})),...RENTAL_LEXICON.map(([ru,zh,category])=>({ru,zh,category,source:'rental-core',reviewed:true}))]));}
function ruZhUser(rules){return ruZhIndex(Object.entries(rules||{}).filter(([ru,zh])=>typeof zh==='string'&&zh.trim()&&ruZhCore().map.get(ruZhFold(ru))?.zh!==zh).map(([ru,zh])=>({ru,zh,source:'user',reviewed:true})));}
function ruZhWhole(value,ctx){const key=ruZhFold(value);return ctx.user.map.get(key)||ruZhCore().map.get(key)||null;}
function ruZhMatch(row,text,start=0){return {ru:text,zh:row.zh,start,end:start+text.length,source:row.source||'rental-core',reviewed:row.reviewed!==false};}
function ruZhResult(text,unknown=[],matches=[],warnings=[]){return {text,unknown,matches,warnings};}
function ruZhNumber(value){const values={'один':'1','одна':'1','одно':'1','одну':'1','одной':'1','одного':'1','два':'2','две':'2','двух':'2','двумя':'2','три':'3','трех':'3','трёх':'3','тремя':'3','четыре':'4','четырех':'4','четырёх':'4','пять':'5','пяти':'5','шесть':'6','шести':'6','семь':'7','семи':'7','восемь':'8','восьми':'8','девять':'9','девяти':'9','десять':'10','десяти':'10'};return values[ruZhFold(value)]||String(value).replace(',','.').replace(/[\s\u00a0]/g,'');}
function ruZhMoney(number,multiplier=''){
 const raw=String(number).replace(/[\s\u00a0]/g,'');let normalized;
 // In monetary fields three-digit groups mean thousands; decimal fractions
 // have at most two digits. Never silently turn 80.000 rubles into 80.
 if(/^\d{1,3}([.,])\d{3}(?:\1\d{3})*$/u.test(raw))normalized=raw.replace(/[.,]/g,'');
 else{const grouped=raw.match(/^(\d{1,3}([.,])\d{3}(?:\2\d{3})*)([.,])(\d{1,2})$/u);
  if(grouped&&grouped[2]!==grouped[3])normalized=grouped[1].replace(/[.,]/g,'')+'.'+grouped[4];
  else if(/^\d+(?:[.,]\d{1,2})?$/u.test(raw))normalized=raw.replace(',','.');
  else return null;
 }
 const n=Number(normalized)*(/тыс|тысяч/i.test(multiplier)?1000:1);return Number.isFinite(n)?n.toLocaleString('zh-CN',{maximumFractionDigits:2}):null;
}
function ruZhObject(value,ctx){
 const raw=value.trim(),direct=ruZhWhole(raw,ctx);if(direct)return ruZhResult(direct.zh,[],[ruZhMatch(direct,raw)]);
 let match=raw.match(/^(\d+|один|одна|одно|одну|одной|два|две|двух|двумя|три|трех|трёх|четыре|четырех|четырёх|пять|пяти|шесть|семь|восемь|девять|десять)\s+(.+)$/iu);
 if(match){const item=ruZhObject(match[2],ctx);if(!item.unknown.length){const counter=/椅/.test(item.text)?'把':/(?:床|床垫|桌|茶几)$/.test(item.text)?'张':/(?:冰箱|洗衣机|烘干机|电视|电脑|烤箱|空调|微波炉|投影仪|热水器|咖啡机|电灶|电磁炉)$/.test(item.text)?'台':/(?:房间|卧室|书房|浴室)$/.test(item.text)?'间':/(?:窗户|门)$/.test(item.text)?'扇':'个';return ruZhResult(ruZhNumber(match[1])+counter+item.text,[],item.matches);}}
 match=raw.match(/^(просторн(?:ая|ый|ое|ую|ой|ые|ые|ыми)|светл(?:ая|ый|ое|ую|ой)|уютн(?:ая|ый|ое|ую|ой)|нов(?:ая|ый|ое|ую|ой)|больш(?:ая|ой|ое|ую|ие|ими)|встроенн(?:ая|ый|ое|ую|ой|ые|ыми))\s+(.+)$/iu);
 if(match){const item=ruZhWhole(match[2],ctx);if(item&&['housing','rooms','furniture','storage','appliances','electronics'].includes(item.category)){const adjective=/^просторн/iu.test(match[1])?'宽敞的':/^светл/iu.test(match[1])?'采光明亮的':/^уютн/iu.test(match[1])?'温馨的':/^нов/iu.test(match[1])?'新的':/^больш/iu.test(match[1])?'大型':'嵌入式';return ruZhResult(adjective+item.zh,[],[ruZhMatch(item,raw)]);}}
 const geo=translateGeo(raw,{}, {includeRussian:false});if(!ruZhCyrillic.test(geo)&&geo!==raw)return ruZhResult(geo,[],[{ru:raw,zh:geo,start:0,end:raw.length,source:'geo-catalog',reviewed:false}]);
 return ruZhResult(raw,ruZhCyrillic.test(raw)?[raw]:[]);
}
function ruZhList(value,ctx){const parts=value.split(/\s*(?:,|;|\s+и\s+|\s+а также\s+)\s*/iu).filter(Boolean),results=parts.map(part=>ruZhObject(part,ctx));return ruZhResult(results.map(r=>r.text).join('、'),results.flatMap(r=>r.unknown),results.flatMap(r=>r.matches));}
function ruZhPattern(raw,ctx){
 const direct=ruZhWhole(raw,ctx);if(direct&&direct.category!=='inventory')return ruZhResult(direct.zh,[],[ruZhMatch(direct,raw)]);
 let m;
 const num='([0-9]+(?:[.,][0-9]+)?)',money='([0-9](?:[0-9\\s\\u00a0.,]*[0-9])?)',unit='(?:м²|м2|кв\\.?\\s*м|квадратных?\\s+метр(?:а|ов)?|㎡)';
 const pattern=(expression,fn)=>{const found=raw.match(new RegExp(expression,'iu'));return found?fn(found):null;};
 const moneyResult=(prefix,m)=>{const amount=ruZhMoney(m[1],m[2]);return amount===null?ruZhResult(raw,[raw],[],['Неоднозначный формат денежной суммы сохранён для проверки.']):ruZhResult(prefix+amount+'卢布');};
 let result=pattern('^(?:цена|аренда|арендная плата|стоимость аренды|месячная аренда)\\s*:?\\s*'+money+'\\s*(тыс\\.?|тысяч[аи]?)?\\s*(?:руб(?:лей|ля|ль)?\\.?|₽)?(?:\\s*(?:в месяц|/\\s*мес(?:яц)?\\.?))?$' ,m=>moneyResult('月租',m));
 if(result)return result;
 result=pattern('^(?:залог|депозит)\\s*:?\\s*'+money+'\\s*(тыс\\.?|тысяч[аи]?)?\\s*(?:руб(?:лей|я|ль)?\\.?|₽)?$',m=>moneyResult('押金',m));if(result)return result;
 result=pattern('^комиссия\\s*:?\\s*'+num+'\\s*%$',m=>ruZhResult('中介费为月租金的'+ruZhNumber(m[1])+'%'));if(result)return result;
 result=pattern('^(?:общая площадь|площадь квартиры|площадь)\\s*:?\\s*'+num+'\\s*'+unit+'$',m=>ruZhResult('总面积'+ruZhNumber(m[1])+'平方米'));if(result)return result;
 result=pattern('^(?:кухня|площадь кухни)\\s*:?\\s*'+num+'\\s*'+unit+'$',m=>ruZhResult('厨房面积'+ruZhNumber(m[1])+'平方米'));if(result)return result;
 result=pattern('^(?:жилая площадь)\\s*:?\\s*'+num+'\\s*'+unit+'$',m=>ruZhResult('居住面积'+ruZhNumber(m[1])+'平方米'));if(result)return result;
 result=pattern('^(?:потолки|высота потолков)\\s*:?\\s*'+num+'\\s*(?:м|метр(?:а|ов)?)$',m=>ruZhResult('层高'+ruZhNumber(m[1])+'米'));if(result)return result;
 result=pattern('^(?:этаж\\s*)?'+num+'\\s*(?:из|/)\\s*'+num+'(?:\\s*этаж(?:ей|а)?)?$',m=>ruZhResult('第'+ruZhNumber(m[1])+'层，共'+ruZhNumber(m[2])+'层'));if(result)return result;
 result=pattern('^'+num+'\\s*'+unit+'$',m=>ruZhResult(ruZhNumber(m[1])+'平方米'));if(result)return result;
 result=pattern('^(?:срок аренды|минимальный срок|аренда от|сдается от|сдаётся от)\\s*:?\\s*'+num+'\\s*месяц(?:а|ев)?$',m=>ruZhResult('租期'+ruZhNumber(m[1])+'个月'));if(result)return result;
 result=pattern('^залог (?:можно )?(?:разбить|разделить) на '+num+' месяца?$',m=>ruZhResult('押金可分'+ruZhNumber(m[1])+'个月支付'));if(result)return result;
 m=raw.match(/^(?:до метро|до станции метро)\s*(.*?)\s*(\d+(?:[–-]\d+)?)\s*мин(?:ут|уты|ута|\.)?\s*(пешком|на автобусе|автобусом|на машине)?$/iu);
 if(m){let placeText=m[1].trim(),travel=m[3]||'';const before=placeText.match(/^(пешком|на автобусе|автобусом|на машине)(?:\s+|$)(.*)$/iu),after=placeText.match(/^(.*?)\s+(пешком|на автобусе|автобусом|на машине)$/iu);
  const embedded=before?.[1]||after?.[2]||'';
  const modeOf=value=>/автобус/iu.test(value)?'乘公交':/машин/iu.test(value)?'驾车':/пешком/iu.test(value)?'步行':'';
  if(embedded&&travel&&modeOf(embedded)!==modeOf(travel))return ruZhResult(raw,[raw],[],['В маршруте указаны разные способы поездки: проверьте исходный текст.']);
  if(embedded){travel=travel||embedded;placeText=(before?.[2]??after?.[1]??'').trim();}
  const place=placeText?ruZhObject(placeText,ctx):ruZhResult(''),mode=modeOf(travel)||'约';return ruZhResult(mode+m[2]+'分钟到'+place.text+'地铁站',place.unknown,place.matches);}
 m=raw.match(/^метро\s+(.+?)\s*[-—:]?\s*(\d+(?:[–-]\d+)?)\s*мин(?:ут|уты|ута|\.)?\s*пешком$/iu);
 if(m){const place=ruZhObject(m[1],ctx);return ruZhResult('步行'+m[2]+'分钟到'+place.text+'地铁站',place.unknown,place.matches);}
 m=raw.match(/^(?:в квартире|в комнате|в доме|на кухне)?\s*(?:нет|не имеется|не предусмотрено)\s+(.+)$/iu);
 if(m){const list=ruZhList(m[1],ctx);return list.unknown.length?ruZhResult(raw,[raw],[],['Отрицание сохранено целиком: в нём есть неизвестные слова.']):ruZhResult('不配备'+list.text,[],list.matches);}
 m=raw.match(/^(.+?)\s+(?:нет|не предусмотрен[аоы]?)$/iu);
 if(m){const list=ruZhList(m[1],ctx);return list.unknown.length?ruZhResult(raw,[raw],[],['Отрицание сохранено целиком: в нём есть неизвестные слова.']):ruZhResult('不配备'+list.text,[],list.matches);}
 m=raw.match(/^(?:(в квартире|в комнате|в доме|на кухне)\s+)?(?:есть|имеется|имеются|установлен[аоы]?)\s+(.+)$/iu);
 if(m){const list=ruZhList(m[2],ctx);return ruZhResult((m[1]&&/кухне/iu.test(m[1])?'厨房配有':'配有')+list.text,list.unknown,list.matches);}
 m=raw.match(/^(?:мебель|техника|оснащение|в квартире|на кухне)\s*:\s*(.+)$/iu);
 if(m){const list=ruZhList(m[1],ctx);return ruZhResult((/^мебель/iu.test(raw)?'家具：':/^техника/iu.test(raw)?'家电：':/^на кухне/iu.test(raw)?'厨房：':'配置：')+list.text,list.unknown,list.matches);}
 m=raw.match(/^(?:сда[её]тся|сдам|сдаю|предлагается в аренду)\s+(.+)$/iu);
 if(m){const object=ruZhObject(m[1],ctx);if(!object.unknown.length)return ruZhResult('出租'+object.text,[],object.matches);}
 m=raw.match(/^(?:квартира|комната|студия)\s+(с|со|без)\s+(.+)$/iu);
 if(m){const base=/^комната/iu.test(raw)?'房间':/^студия/iu.test(raw)?'开间公寓':'公寓';
  if(m[1].toLowerCase()==='без'){const restriction=ruZhWhole('без '+m[2],ctx);if(restriction&&restriction.category!=='inventory')return ruZhResult(base+'，'+restriction.zh,[],[ruZhMatch(restriction,'без '+m[2])]);}
  // People and pets are tenancy conditions, never apartment equipment.
  if(/(?:^|\s)(?:животн\S*|питом\S*|детей|детьми|дети)(?:\s|$)/iu.test(m[2]))return ruZhResult(raw,[raw],[],['Условие о жильцах или питомцах требует точного перевода и проверки.']);
  const list=ruZhList(m[2],ctx);return ruZhResult(base+(m[1].toLowerCase()==='без'?'不配备':'配有')+list.text,list.unknown,list.matches);}
 m=raw.match(/^(одна|две|двух|двумя|три|трех|трёх|четыре|\d+)\s+(отдельн(?:ая|ые|ых|ыми)|изолированн(?:ая|ые|ых|ыми))\s+комнат(?:а|ы|ами)?$/iu);
 if(m)return ruZhResult(ruZhNumber(m[1])+'间独立房间');
 const object=ruZhObject(raw,ctx);if(!object.unknown.length&&object.text!==raw)return object;
 return null;
}
function ruZhScan(raw,ctx){
 let output='',matches=[],unknown=[],pending='',pendingStart=0;
 const flush=()=>{if(!pending)return;const geo=translateGeo(pending,{}, {includeRussian:false});output+=geo;if(geo!==pending)matches.push({ru:pending,zh:geo,start:pendingStart,end:pendingStart+pending.length,source:'geo-catalog',reviewed:false});const remaining=geo.match(/[\p{Script=Cyrillic}]+(?:[-'’][\p{Script=Cyrillic}]+)*/gu)||[];unknown.push(...remaining);pending='';};
 const candidates=index=>{const char=ruZhFold(raw[index]);return [...(ctx.user.prefix.get(char)||[]),...(ruZhCore().prefix.get(char)||[]),...(ctx.mode==='lookup'?(ctx.open.prefix.get(char)||[]):[])];};
 for(let i=0;i<raw.length;){let row=null;for(const candidate of candidates(i)){const part=raw.slice(i,i+candidate.key.length);if(ruZhFold(part)!==candidate.key)continue;if((i&&ruZhWordChar.test(raw[i-1])&&ruZhWordChar.test(candidate.key[0]))||(ruZhWordChar.test(raw[i+part.length]||'')&&ruZhWordChar.test(candidate.key.at(-1))))continue;row={...candidate,actual:part};break;}
  if(row){flush();output+=row.zh;matches.push(ruZhMatch(row,row.actual,i));i+=row.actual.length;}
  else{if(!pending)pendingStart=i;pending+=raw[i++];}}
 flush();return ruZhResult(output,unknown,matches);
}
function ruZhProtectedPieces(input,ctx){
 // Initials inside a known full name are part of that name, not sentence
 // boundaries. Protect delimiter positions without changing source text or
 // introducing markers that a later translator could alter.
 const ranges=[],prefix=new Map();
 for(const index of [ctx.user,ruZhCore()])for(const [first,candidates] of index.prefix){
  const filtered=candidates.filter(row=>/[.!?;。！？]/u.test(row.key));
  if(filtered.length)prefix.set(first,[...(prefix.get(first)||[]),...filtered]);
 }
 for(let index=0;index<input.length;index++){
  const first=ruZhFold(input[index]),candidates=prefix.get(first)||[];
  for(const row of candidates){
   const actual=input.slice(index,index+row.key.length);
   if(ruZhFold(actual)!==row.key)continue;
   if((index&&ruZhWordChar.test(input[index-1])&&ruZhWordChar.test(row.key[0]))||(ruZhWordChar.test(input[index+actual.length]||'')&&ruZhWordChar.test(row.key.at(-1))))continue;
   ranges.push([index,index+actual.length]);index+=actual.length-1;break;
  }
 }
 const pieces=[],boundary=/([\n\r]+|(?<!тыс|руб|мин|кв)\.(?=\s|$)|[!?。！？](?=\s|$)|;)/giu;let start=0;
 for(const match of input.matchAll(boundary)){
  if(ranges.some(([left,right])=>match.index>=left&&match.index<right))continue;
  pieces.push(input.slice(start,match.index),match[0]);start=match.index+match[0].length;
 }
 pieces.push(input.slice(start));return pieces;
}
export function translateRussian(text,{rules={},entries=null,mode='safe',preservePunctuation=false}={}){
 const input=String(text??'').slice(0,20000),user=ruZhUser(rules),open=entries?ruZhIndex(entries.map(row=>Array.isArray(row)?{ru:row[0],zh:row[1],source:row[2]||'open-lexicon',reviewed:false}:row)):(ruZhOpenIndex||(ruZhOpenIndex=ruZhIndex(ruZhOpenRows))),ctx={user,open,mode};
 const pieces=ruZhProtectedPieces(input,ctx);let output='',unknown=[],matches=[],warnings=[],sourceOffset=0;
 for(const part of pieces){const partStart=sourceOffset;sourceOffset+=part.length;if(!part)continue;if(/^[\n\r.!?。！？;]+$/u.test(part)){output+=preservePunctuation?part:part==='.'?'。':part===';'?'；':part==='!'?'！':part==='?'?'？':part;continue;}
  // Native trimming also handles astral characters and lone UTF-16 surrogates
  // without depending on WebKit's zero-width Unicode regexp edge matching.
  const raw=part.trim();if(!raw){output+=part;continue;}
  const left=part.slice(0,part.length-part.trimStart().length),right=part.slice(part.trimEnd().length);
  const exact=ruZhWhole(raw,ctx);let result=exact&&exact.category!=='inventory'?ruZhResult(exact.zh,[],[ruZhMatch(exact,raw)]):/^(?:есть ли|можно ли|будет ли|можно ли будет|разрешено ли)/iu.test(raw)?ruZhResult(raw,[raw],[],['Вопрос сохранён как вопрос и не превращён в утверждение.']):ruZhPattern(raw,ctx);
  if(!result&&/(?:^|\s)(?:нет|без|не|нельзя|запрещ\S*|отсутств\S*|если|возможно|возможно,|только если)(?:\s|$)/iu.test(raw)&&ruZhCyrillic.test(raw))result=ruZhResult(raw,[raw],[],['Условие или отрицание сохранено целиком: нужен перевод или проверка.']);
  if(!result)result=ruZhScan(raw,ctx);
  const offset=partStart+left.length;let matchCursor=0;matches.push(...result.matches.map(m=>{const found=raw.indexOf(m.ru,matchCursor),position=found>=0?found:m.start;matchCursor=position+m.ru.length;return {...m,start:position+offset,end:position+m.ru.length+offset};}));unknown.push(...result.unknown);warnings.push(...result.warnings);output+=left+result.text+right;
 }
 const unique=[...new Set(unknown.filter(Boolean))],totalWords=(input.match(/[\p{Script=Cyrillic}]+/gu)||[]).length,remainingWords=unknown.reduce((n,s)=>n+(s.match(/[\p{Script=Cyrillic}]+/gu)||[]).length,0);
 if(matches.some(m=>m.reviewed===false&&!['geo-catalog','university-catalog'].includes(m.source)))warnings.push('Открытый словарь содержит непроверенные значения и неоднозначные слова; это подсказка для проверки, а не машинный перевод предложения.');
 if(matches.some(m=>m.reviewed===false&&m.source==='university-catalog'))warnings.push('Китайское название вуза или кампуса содержит редакционный перевод; проверьте его по указанным источникам.');
 return {text:output,unknown:unique,matches,coverage:{totalWords,translatedWords:Math.max(0,totalWords-remainingWords),ratio:totalWords?Math.max(0,Math.min(1,(totalWords-remainingWords)/totalWords)):1},warnings:[...new Set(warnings)],needsReview:unique.length>0||matches.some(m=>m.reviewed===false&&m.source!=='geo-catalog'),backend:'rental-phrases',limited:input.length!==String(text??'').length};
}
export function setExtendedLexicon(data){const rows=Array.isArray(data)?data:data?.entries||[];ruZhOpenRows=rows.filter(r=>Array.isArray(r)&&typeof r[0]==='string'&&typeof r[1]==='string'&&ruZhCyrillic.test(r[0])&&/\p{Script=Han}/u.test(r[1])).map(r=>({ru:r[0],zh:r[1],source:r[2]||'wiktionary',sourcePage:r[3]||null,sourceUrl:(Array.isArray(data?.sources)?data.sources.find(s=>s.id===r[2])?.pageUrlPrefix:'')?data.sources.find(s=>s.id===r[2]).pageUrlPrefix+encodeURIComponent(r[3]||r[0]):null,reviewed:false}));ruZhOpenForms=new Map();for(const [form,lemma] of Array.isArray(data?.forms)?data.forms:[]){const key=ruZhFold(form),value=ruZhFold(lemma);if(!ruZhOpenForms.has(key))ruZhOpenForms.set(key,[]);if(!ruZhOpenForms.get(key).includes(value))ruZhOpenForms.get(key).push(value);}ruZhOpenMeta=Array.isArray(data)?null:data;ruZhOpenIndex=null;return getLexiconStats();}
export async function loadOpenLexicon(url='/ru-zh-open-lexicon.json',fetcher=globalThis.fetch){if(ruZhOpenMeta)return getLexiconStats();if(!ruZhLoading)ruZhLoading=(async()=>{if(typeof fetcher!=='function')throw Error('Для загрузки словаря нужен fetch');const response=await fetcher(url);if(!response.ok)throw Error('Не удалось загрузить открытый словарь: HTTP '+response.status);return setExtendedLexicon(await response.json());})().catch(error=>{ruZhLoading=null;throw error;});return ruZhLoading;}
export const loadExtendedLexicon=loadOpenLexicon;
export function getLexiconStats(){return {curatedEntries:ruZhCore().map.size,communityEntries:COMMUNITY_RENTAL_META.entryCount,curatedHeadwords:RENTAL_LEXICON_META.baseHeadwords,grammaticalForms:RENTAL_LEXICON_META.grammaticalInventoryForms,openEntries:ruZhOpenRows.length,openRussianKeys:ruZhOpenMeta?.stats?.russianKeys??ruZhOpenMeta?.stats?.uniqueRussianKeys??new Set(ruZhOpenRows.map(r=>ruZhFold(r.ru))).size,openWordForms:ruZhOpenForms.size,attestedFormLinks:ruZhOpenMeta?.stats?.attestedFormLinks||0,openLookupKeys:ruZhOpenMeta?.stats?.uniqueLookupKeys||0,openLoaded:ruZhOpenMeta!==null,source:ruZhOpenMeta?(ruZhOpenMeta.source||'Wiktionary / Kaikki'):null,sources:ruZhOpenMeta?.sources||[],license:ruZhOpenMeta?.license||null};}
export const getTranslationStats=getLexiconStats;
export function searchLexiconPage(query,{limit=40,offset=0,includeExtended=true}={}){const q=ruZhFold(query);if(!q)return {entries:[],total:0,offset:0,hasMore:false};const lemmas=ruZhOpenForms.get(q)||[];const rows=[...ruZhCore().map.values(),...(includeExtended?ruZhOpenRows:[])];const found=rows.filter(row=>ruZhFold(row.ru).includes(q)||row.zh.includes(query)||lemmas.includes(ruZhFold(row.ru)));const start=Math.max(0,Number(offset)||0),count=Math.min(100,Math.max(1,Number(limit)||40));return {entries:found.slice(start,start+count),total:found.length,offset:start,hasMore:found.length>start+count};}
export function searchLexicon(query,options={}){return searchLexiconPage(query,options).entries;}
