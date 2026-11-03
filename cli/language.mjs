// The editor and Python API use the same engine and reviewed translation core.
import {readFile,stat} from 'node:fs/promises';
import {DICTIONARY,DEFAULT_PREFS,FACTUAL_REFERENCE_VERSION,generateListing,generationCapabilities,translateDraft} from '../web/engine.mjs';
import {setExtendedLexicon,getLexiconStats} from '../web/ru-zh-translator.mjs';
import {RENTAL_LEXICON} from '../web/rental-lexicon.mjs';
import {COMMUNITY_RENTAL_ENTRIES} from '../web/community-phrases.mjs';
import {GEO_CATALOG} from '../web/geo-catalog.mjs';
import {CITY_POIS} from '../web/city-pois.mjs';
import {normalizeCopyHistory,rememberPublication,publicationDigest} from '../web/rental-copy.mjs';
import {detectConditions} from '../web/fact-evidence.mjs';

const fold=value=>String(value??'').normalize('NFC').toLocaleLowerCase('ru').replaceAll('ё','е').replace(/\s+/g,' ').trim();

async function openLexicon(){
  const url=new URL('../data/ru-zh-open-lexicon.json',import.meta.url);
  try{
    const info=await stat(url);if(info.size>64_000_000)throw Error('Открытый словарь превышает ограничение размера');
    const data=JSON.parse(await readFile(url,'utf8'));
    if(!Array.isArray(data.entries))throw Error('Открытый словарь имеет неверный формат');
    const entries=[];
    for(const row of data.entries){
      if(!Array.isArray(row)||row.length<2||typeof row[0]!=='string'||typeof row[1]!=='string')continue;
      const ru=row[0].trim(),zh=row[1].trim();if(!ru||!zh)continue;
      entries.push([ru,zh,...row.slice(2,4)]);
      if(entries.length>500_000)throw Error('Открытый словарь превышает ограничение записей');
    }
    setExtendedLexicon({...data,entries});return {entries,forms:data.forms||[],source:data.source||null,license:data.license||null,available:true};
  }catch(error){if(error.code==='ENOENT')return {entries:[],source:null,license:null,available:false};throw error;}
}

async function universityRows(){
  // Trusted repository data, loaded only for an explicit dictionary request.
  const url=new URL('../data/moscow-universities.json',import.meta.url);
  try{
    const info=await stat(url);if(info.size>6_000_000)throw Error('Справочник вузов превышает ограничение размера');
    const data=JSON.parse(await readFile(url,'utf8'));
    if(data.version!==1||!Array.isArray(data.universities)||!Array.isArray(data.campuses)||!Array.isArray(data.sources))throw Error('Неверный формат справочника вузов');
    const sources=new Map(data.sources.map(row=>[row.id,row])),parents=new Map(data.universities.map(row=>[row.id,row]));
    return [...data.universities.map(row=>({...row,kind:'university'})),...data.campuses.map(row=>({...row,kind:'campus'}))].map(row=>{
      const parent=parents.get(row.universityId),related=(row.sourceIds||[]).map(id=>sources.get(id)).filter(Boolean),accountNames=(row.chinaStudentEvidence?.accounts||[]).map(account=>account.personName);
      return {...row,searchAliases:accountNames,sourceId:'moscow-universities',reviewed:row.translationStatus==='official',sources:related,sourceUrl:related[0]?.url,
        ...(parent?{parentRu:parent.ru,parentZh:parent.zh,searchAliases:[parent.ru,parent.zh,...(parent.aliases||[])]}:{})};
    });
  }catch(error){if(error.code==='ENOENT')return [];throw error;}
}

try{
  const chunks=[];let size=0;for await(const chunk of process.stdin){size+=chunk.length;if(size>1_000_000)throw Error('Запрос текста слишком большой');chunks.push(chunk);}
  const input=JSON.parse(Buffer.concat(chunks).toString('utf8'));if(!input||Array.isArray(input)||typeof input!=='object')throw Error('Нужен JSON-объект');
  const rules={...DICTIONARY,...(input.rules||{})};let result;
  if(input.action==='source-term-index'){
    result={terms:[...new Set(Object.keys(rules).map(fold))],normalization:'nfc-lowercase-yo-space-v1',weightsUpdated:false};
  }else if(input.action==='capabilities'){
    const open=await openLexicon(),pairs=new Set(),headwords=new Set(),openPairs=new Set(),openHeadwords=new Set(),corePairs=new Set();
    for(const [ru,zh] of [...Object.entries(rules),...open.entries]){if(typeof zh!=='string')continue;pairs.add(fold(ru)+'\0'+zh.normalize('NFC').trim());headwords.add(fold(ru));}
    for(const [ru,zh] of open.entries){openPairs.add(fold(ru)+'\0'+zh.normalize('NFC').trim());openHeadwords.add(fold(ru));}
    for(const [ru,zh] of Object.entries(DICTIONARY))corePairs.add(fold(ru)+'\0'+zh.normalize('NFC').trim());
    result={...generationCapabilities(),dictionary:{...getLexiconStats(),entries:pairs.size,ruZhPairs:pairs.size,headwords:headwords.size,
      curatedRules:Object.keys(DICTIONARY).length,activeRules:Object.keys(rules).length,openPairs:openPairs.size,
      openRows:open.entries.length,openHeadwords:openHeadwords.size,openReviewedPairs:0,
      coreNormalizedPairs:corePairs.size,coreOpenOverlap:[...corePairs].filter(key=>openPairs.has(key)).length,
      countMeaning:'dictionary-records-not-learned-words',weightsUpdated:false,
      sourceAttribution:'Wiktionary contributors; Kaikki.org / Wiktextract; project rental rules',
      openAvailable:open.available,source:open.source,license:open.license}};
  }else if(input.action==='search'){
    const query=fold(input.query);if(!query||query.length>200)throw Error('Поиск: от 1 до 200 символов');
    const scope=input.scope||'all';if(!['all','open','rental','geo','custom','universities','city-pois'].includes(scope))throw Error('Неизвестная область словаря');
    const limit=Math.max(1,Math.min(100,Number(input.limit)||40)),offset=Math.max(0,Math.min(1_000_000,Number(input.offset)||0)),rows=[];
    const include=kind=>scope==='all'||scope===kind;
    if(include('custom'))for(const [ru,zh] of Object.entries(input.rules||{}))if(DICTIONARY[ru]!==zh)rows.push({ru,zh,sourceId:'custom',reviewed:true});
    if(include('rental')){const merged=new Map();for(const [sourceId,entries] of [['community-rental',COMMUNITY_RENTAL_ENTRIES],['rental-core',RENTAL_LEXICON]])for(const [ru,zh,category] of entries)if(!merged.has(fold(ru)))merged.set(fold(ru),{ru,zh,category,sourceId,reviewed:true});rows.push(...merged.values());}
    if(include('geo'))for(const row of GEO_CATALOG.records)rows.push({...row,sourceId:row.source||'geo-catalog',reviewed:row.translationStatus==='reviewed'});
    if(include('universities'))rows.push(...await universityRows());
    if(include('city-pois')){const sources=new Map(CITY_POIS.sources.map(row=>[row.id,row]));for(const row of CITY_POIS.places)rows.push({...row,sourceId:'city-pois',sourceUrl:sources.get(row.sourceIds[0])?.url,reviewed:false,sources:row.sourceIds.map(id=>sources.get(id))});}
    let lemmas=new Set();
    if(include('open')){const open=await openLexicon();lemmas=new Set(open.forms.filter(([form])=>fold(form)===query).map(([,lemma])=>fold(lemma)));for(const [ru,zh,sourceId,sourcePage] of open.entries)rows.push({ru,zh,sourceId:sourceId||'wiktionary',sourcePage:sourcePage||null,reviewed:false});}
    const seen=new Set(),matches=rows.filter(row=>{const ru=fold(row.ru),zh=fold(row.zh),aliases=[...(row.aliases||[]),...(row.searchAliases||[]),row.addressRu||''].map(fold);if(!ru.includes(query)&&!zh.includes(query)&&!lemmas.has(ru)&&!aliases.some(alias=>alias.includes(query)))return false;const key=ru+'\0'+zh+'\0'+row.sourceId;if(seen.has(key))return false;seen.add(key);return true;});
    const exact=row=>fold(row.ru)===query||fold(row.zh)===query||(row.aliases||[]).some(alias=>fold(alias)===query);
    matches.sort((a,b)=>Number(exact(b))-Number(exact(a))||Number(b.sourceId==='custom')-Number(a.sourceId==='custom')||a.ru.localeCompare(b.ru,'ru'));
    result={entries:matches.slice(offset,offset+limit),total:matches.length,offset};
  }else if(input.action==='translate'){
    if(typeof input.text!=='string'||input.text.length>20_000)throw Error('Русский текст: до 20 000 символов');
    const open=await openLexicon();result=translateDraft(input.text,{rules,entries:open.entries,mode:'draft'});
  }else if(input.action==='individual-brief'){
    if(!input.fields||Array.isArray(input.fields)||typeof input.fields!=='object')throw Error('Нужны поля карточки');
    const fields={...input.fields},sourceConditions=detectConditions(fields.sourceDescription||fields.sourceAudit?.description||''),source=sourceConditions.facts;
    const utilityValues=new Set(['included','meters','extra']);
    if(utilityValues.has(source.utilities)&&utilityValues.has(fields.utilities)&&source.utilities!==fields.utilities)throw Error('В описании и полях расходятся коммунальные расходы. Уточните в карточке, какие платежи входят в аренду и какие оплачиваются отдельно.');
    const conditionKeys=['petPolicy','childrenPolicy','registrationPolicy','smokingPolicy','guestPolicy','subletPolicy','shortTermPolicy','shortTerm','contractMonths','prepayMonths','minMonths','leaseTerm','utilities'];
    for(const key of conditionKeys)if((fields[key]===undefined||fields[key]===null||fields[key]==='')&&source[key]!==undefined)fields[key]=source[key];
    for(const [key,flag] of [['petPolicy','pets'],['childrenPolicy','children']])if(source[key]==='no'&&!['no','conflict'].includes(fields[key])){fields[key]='conflict';fields[flag]=false;}
    result=generateListing({...fields,templateMode:'catalog',titleVariant:0,copyVariant:0,tagVariant:0},rules,
      {...DEFAULT_PREFS,benefits:'',contact:'',brandTags:''},{referenceVersion:FACTUAL_REFERENCE_VERSION,includeFactualBrief:true});
    if(result.problems?.length)throw Error(result.problems.join(' · '));
    result.factualBrief.effectiveConditions=Object.fromEntries(conditionKeys.filter(key=>fields[key]!==undefined).map(key=>[key,fields[key]]));
    result.factualBrief.sourceRestrictions=sourceConditions.restrictions;
  }else if(input.action==='factual-reference'){
    if(input.referenceVersion!==FACTUAL_REFERENCE_VERSION)throw Error('Неизвестная версия фактического контекста');
    if(!input.fields||Array.isArray(input.fields)||typeof input.fields!=='object')throw Error('Нужны поля карточки');
    result=generateListing({...input.fields,templateMode:'catalog'},rules,{...DEFAULT_PREFS,...(input.prefs||{})},{referenceVersion:input.referenceVersion});
    if(result.problems?.length)throw Error(result.problems.join(' · '));
  }else if(input.action==='generate'){
    if(!input.fields||Array.isArray(input.fields)||typeof input.fields!=='object')throw Error('Нужны поля карточки');
    const fields={...input.fields,templateMode:'catalog'},prefs={...DEFAULT_PREFS,...(input.prefs||{})};
    const requested=input.copyRewrite;
    let history=normalizeCopyHistory(fields.copyHistory),parts=[];
    const keys={title:'titleVariant',body:'copyVariant',tags:'tagVariant'},previous=requested?.previous||{};
    if(requested){
      if(!Array.isArray(requested.parts)||!requested.parts.length||requested.parts.some(part=>!Object.hasOwn(keys,part)))throw Error('Неверные части перегенерации');
      parts=[...new Set(requested.parts)];
      for(const part of parts){if(typeof previous[part]!=='string'||previous[part].length>20000)throw Error('Неверный предыдущий текст');history=rememberPublication(history,part,previous[part]);}
    }
    const output=part=>part==='title'?result.titles?.[0]||'':result[part];
    for(let attempt=0;attempt<=64;attempt++){
      result=generateListing(fields,rules,prefs);
      if(result.problems?.length)throw Error(result.problems.join(' · '));
      const duplicate=parts.filter(part=>history[part].includes(publicationDigest(output(part),part)));
      if(!duplicate.length)break;
      if(attempt===64)throw Error(duplicate.includes('title')&&fields.titleFormula?'Формула закрепляет этот заголовок. Измените формулу для другой подачи.':'Для этих фактов новый вариант пока не найден. Текущий текст сохранён.');
      for(const part of duplicate)fields[keys[part]]=(Math.max(0,Math.trunc(Number(fields[keys[part]])||0))+1)%(2**31);
    }
    if(parts.length){
      for(const part of parts)history=rememberPublication(history,part,output(part));
      result.copyHistory=history;
      result.copyCounters=Object.fromEntries(parts.map(part=>[part,fields[keys[part]]]));
    }
  }else throw Error('Неизвестная операция с текстом');
  process.stdout.write(JSON.stringify(result));
}catch(error){process.stderr.write(String(error.message||error).slice(0,1000));process.exitCode=2;}
