import {serviceClaimChecklist} from './fact-ledger.mjs';
// One client-publication policy. Source facts, drafts and stored cards are private
// inputs and must never be overwritten by this derived projection.
export const PUBLICATION_POLICY_VERSION='client-publication-v2';
const clientCommissionPattern=/комисси[а-яё]*|агентск[а-яё]*\s+(?:вознагражден[а-яё]*|сбор[а-яё]*|услуг[а-яё]*)|вознагражден[а-яё]*\s+(?:агент[а-яё]*|риелтор[а-яё]*)|риелторск[а-яё]*\s+(?:сбор[а-яё]*|услуг[а-яё]*)|中介\s*(?:费|費)|佣金|傭金|免佣|经纪\s*(?:费|費)|經紀\s*(?:费|費)|代理\s*(?:费|費)|\bcommission\b|\b(?:broker(?:age)?|agent|agency)\s+fees?\b/iu;
const clientProtectedPattern=/(?:аренд[ауы]|арендная\s+плата|залог|депозит|предоплат|аванс|коммунал|жку|квартплат|сч[её]тчик|площад|комнат|спальн|мебел|диван|кроват|холодильник|квартир|адрес|метро|этаж|\b(?:rent|deposit|utilities|bedrooms?)\b|租金|月租|押金|定金|预付|預付|物业|物業|水电|水電|面积|面積|卧室|臥室|家具|公寓|精装|精裝|平米|[一两兩二三四五六七八九十]室|[一两兩二三四五六七八九十]厅|地铁|地鐵|\d\s*(?:㎡|м²|м2))/iu;
const clientPrivateKey=/(?:commission|^(?:clientFee|agentFee|brokerFee|agencyFee|initialPayment|paymentCalculation|oneTimeFees|workflow|factLedger|factRevision|factRevisions|serviceChecks|serviceConfirmations|serviceBenefits|publicationApproval|approvals|offer|bargainTerms|source(?:Audit|Description|Ru|Html)?|raw(?:Text|Html|Body|Description)?|original(?:Text|Html|Body|Description)|notes|operatorNotes|internal|rules|prefs|sourceEnvelope)$)/iu;
const clientFold=value=>String(value||'').replace(/[\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]/g,'');
// A matched token alone is insufficient: any leftover property/location words
// make the clause ambiguous. This protects facts outside a finite keyword list.
const clientFeeGrammar=/без|нет|не|комисси[а-яё]*|агентск[а-яё]*|вознагражден[а-яё]*|риелторск[а-яё]*|услуг[а-яё]*|сбор[а-яё]*|оплат[а-яё]*|плат[а-яё]*|отсутств[а-яё]*|взима[а-яё]*|составля[а-яё]*|дополнительн[а-яё]*|услови[а-яё]*|уточн[а-яё]*|обсужда[а-яё]*|оговор[а-яё]*|согласова[а-яё]*|разов[а-яё]*|единоразов[а-яё]*|руб(?:л[а-яё]*)?|процент[а-яё]*|посредник[а-яё]*|арендатор[а-яё]*|собственник[а-яё]*|нанимател[а-яё]*|(?:^|\s)(?:с|со|за|от|для|по|к|на|и|или|до|в|из|у)(?=\s|$)|no|zero|free|waived|payable|paid|pay|by|the|tenant|landlord|fees?|commission|broker(?:age)?|agency|agent|additional|negotiable|contact|details|discuss|request|one[ -]?time|rur|rub|rmb|中介|费|費|佣金|傭金|免佣|经纪|經紀|代理|无需|無需|支付|免|无|無|不收|不另收|收取|另付|另计|另計|费用|費用|服务|服務|元|卢布|盧布|人民币|人民幣|一次性|为|為|是|可议|可議|面议|面議|咨询|諮詢|协商|協商|双方|租客|房东|房東|承担|承擔|待确认|待確認|具体详询|具體詳詢|请|請|私聊|详询|詳詢|确认|確認|联系|聯繫/giu;
function clientCommissionOnly(text){const rest=clientFold(text).replace(clientFeeGrammar,'');return !/[\p{L}]/u.test(rest);}

export class ClientPublicationReviewRequired extends Error{
 constructor(fragment,reason='Перед публикацией разделите комиссию и остальные условия'){super(reason+': «'+fragment.slice(0,300)+'». Исходный текст сохранён.');this.name='ClientPublicationReviewRequired';this.fragment=fragment.slice(0,700);this.code='PUBLICATION_REVIEW_REQUIRED';}
}
export function containsClientCommission(value){return clientCommissionPattern.test(clientFold(value));}
function clientTextClauses(line){
 if(/^\s*#/.test(line))return (line.match(/#[^#]*/g)||[]).map(text=>({text,separator:' '}));
 const rows=[];let start=0;
 for(let index=0;index<line.length;index++){
  const c=line[index],numeric=/\d/.test(line[index-1]||'')&&/\d/.test(line[index+1]||'');
  const delimiter=';；|｜·•。!?！？'.includes(c)||(c===','&&!numeric)||c==='，'||c==='.'&&!numeric&&(index===line.length-1||/\s/.test(line[index+1]||''))||c==='/'&&/\s/.test(line[index-1]||'')&&/\s/.test(line[index+1]||'');
  if(delimiter){rows.push({text:line.slice(start,index),separator:c});start=index+1;}
 }
 rows.push({text:line.slice(start),separator:''});return rows;
}
export function sanitizeClientText(value){
 if(value==null)return '';if(typeof value!=='string')throw TypeError('Текст публикации должен быть строкой');
 if(!containsClientCommission(value))return value;
 const lines=[];
 for(const line of value.replace(/\r\n?/g,'\n').split('\n')){
  if(!containsClientCommission(line)){lines.push(line);continue;}
  let output='',separator='';
  for(const clause of clientTextClauses(line)){
   const text=clause.text.trim();if(!text)continue;
   if(containsClientCommission(text)){
    if(clientProtectedPattern.test(clientFold(text))||!clientCommissionOnly(text))throw new ClientPublicationReviewRequired(text);
    continue;
   }
   if(output)output+=separator+(separator&&!/[\s。，；]/.test(separator)?' ':'');output+=text;separator=clause.separator;
  }
  if(output){if(/[。.!?！？]/.test(separator))output+=separator;lines.push(output);}
 }
 return lines.join('\n').trim();
}
export function assertClientText(value){const cleaned=sanitizeClientText(value);if(cleaned!==String(value??''))throw new ClientPublicationReviewRequired(String(value??''));return cleaned;}
const clientDisputedClaims={deposit:/押金|免押|залог|депозит/iu,buildingType:/кирпич|панел|монолит|блочн|砖|磚|板式|预制|預製|整体浇筑/iu,bathroom:/浴缸|淋浴|ванн|душ/iu,price:/租金|月租|аренд[ауы]|арендная плата/iu,area:/面积|面積|площад|\d\s*(?:㎡|м²|м2)/iu,floor:/楼层|樓層|этаж/iu,rooms:/комнат|卧室|臥室|[一两兩二三四五六七八九十]室/iu};
function clientCheckDisputedFacts(card){
 if(!card||typeof card!=='object')return;
 const fields=card.fields&&typeof card.fields==='object'?card.fields:card,audit=fields.sourceAudit||card.sourceAudit||{},blocked=Array.isArray(audit.blockedFields)?audit.blockedFields:[];
 const texts=[card.title,card.body,card.tags,...(Array.isArray(card.titles)?card.titles:[]),fields.description].filter(value=>typeof value==='string');
 for(const field of blocked){
  if(typeof field!=='string')continue;
  const reason='Перед публикацией проверьте противоречивое поле '+field,value=fields[field];
  if(value!==undefined&&value!==null&&value!==''&&!(Array.isArray(value)&&!value.length))throw new ClientPublicationReviewRequired(field+': '+String(value),reason);
  const expression=clientDisputedClaims[field];if(!expression)continue;
  for(const text of texts)for(const line of text.split(/\n|[。;]/u))if(expression.test(line)){
   if(field==='deposit'&&!/\d|无需|無需|免押|без\s+залога/iu.test(line)&&/待确认|待確認|待核实|уточн|неизвест/iu.test(line))continue;
   throw new ClientPublicationReviewRequired(line.trim(),reason);
  }
 }
}
const clientServiceCanonical=value=>clientFold(value).replace(/[\p{Extended_Pictographic}\p{Regional_Indicator}\uFE0F\u200D]/gu,'').replace(/\s+/gu,' ').trim();
const clientServiceFree='(?:免费|免費|无偿|無償|бесплатн[а-яё]*|\\bfree\\b|\\bcomplimentary\\b)';
const clientServiceRegistration='(?:抵达登记|临时居住登记|臨時居住登記|住宿登记|住宿登記|落地签|落地簽|登记|登記|注册|註冊|регистрац[а-яё]*|\\bregistration\\b)';
const clientServicePickup='(?:接送|接机|接機|接站|搬运行李|搬運行李|трансфер[а-яё]*|встреч[а-яё]*|достав[а-яё]*|\\b(?:airport\\s+)?pickup\\b|\\btransfers?\\b|\\bshuttle\\b)';
const clientServiceArrival='(?:抵达|抵達|到达|到達|手续|手續|прибыт[а-яё]*|процедур[а-яё]*|\\barrival\\b|\\bprocedures?\\b)';
const clientServiceFreePattern=type=>new RegExp(clientServiceFree+'[^\\n。.!?;]{0,45}'+type+'|'+type+'[^\\n。.!?;]{0,45}'+clientServiceFree,'iu');
const clientFreeRegistration=new RegExp(clientServiceFreePattern(clientServiceRegistration).source+'|包落地','iu');
const clientFreePickup=clientServiceFreePattern(clientServicePickup),clientFreeArrival=clientServiceFreePattern(clientServiceArrival);
export function assertClientServiceClaims(card={},options={}){
 const fields=card?.fields&&typeof card.fields==='object'?card.fields:card,benefits=typeof fields?.serviceBenefits==='string'?fields.serviceBenefits:'';
 const checks=serviceClaimChecklist(fields,{prefs:{benefits},receipts:fields?.serviceConfirmations,now:options.now}),confirmed=id=>checks.claims.some(row=>row.id===id&&row.confirmed);
 const texts=options.texts||[card?.title,card?.body,card?.output,card?.tags,...(Array.isArray(card?.titles)?card.titles:[]),fields?.description,fields?.details,fields?.highlight].filter(value=>typeof value==='string');
 const deny=(text,reason)=>{throw new ClientPublicationReviewRequired(text,'Подтвердите актуальные услуги студии перед публикацией: '+reason);};
 for(const text of texts){
  if(typeof text!=='string')continue;
  if(clientFreeRegistration.test(clientFold(text)))deny(text,'бесплатная регистрация не подтверждается обещанием содействия');
  for(const line of text.split(/\n|[。;]/u))if(/登记|登記|注册|註冊|落地签|落地簽|регистрац|registration/iu.test(line)){
   const months=[...line.matchAll(/(\d+(?:[.,]\d+)?)\s*(?:个月|個月|месяц[а-яё]*|months?)/giu)],people=[...line.matchAll(/(\d+)\s*(?:人|человек[а-яё]*|people|persons?)/giu)];
   if((months.length||people.length)&&(!confirmed('registration')||months.some(row=>Number(row[1].replace(',','.'))!==Number(fields.registrationMonths))||people.some(row=>Number(row[1])!==Number(fields.registrationPeople))))deny(line,'срок или количество регистраций');
  }
  let scan=clientServiceCanonical(text),match;
  while((match=/我们的优势\s*[:：]/u.exec(scan))){const expected=clientServiceCanonical(sanitizeClientText(benefits));let start=match.index+match[0].length;while(scan[start]===' ')start++;
   if(!confirmed('support')||!expected||scan.slice(start,start+expected.length)!==expected)deny(text,'изменился блок преимуществ');
   scan=scan.slice(0,match.index)+scan.slice(start+expected.length);
  }
  if(clientFreePickup.test(scan)&&!confirmed('pickup'))deny(text,'бесплатная встреча и трансфер');
  if(clientFreeArrival.test(scan)&&!confirmed('arrival'))deny(text,'бесплатная помощь по прибытии');
 }
 return card;
}
const clientServiceRegistryKey=Symbol.for('redcn.client-service-authority.v2');
const clientServiceRegistry=globalThis[clientServiceRegistryKey]??=new Map();
function clientServiceSignature(card){const active=new Set();function stable(value){if(!value||typeof value!=='object')return value;if(active.has(value))throw TypeError('Циклическая модель публикации');active.add(value);const result=Array.isArray(value)?value.map(stable):Object.fromEntries(Object.keys(value).sort().map(key=>[key,stable(value[key])]));active.delete(value);return result;}const text=JSON.stringify(stable(card));return text?.length<=1_000_000?text:null;}
function clientServiceGrant(card){const signature=clientServiceSignature(card),grant=signature&&clientServiceRegistry.get(signature);if(grant?.kind==='server'&&!(Date.now()<=grant.expiresAt)){clientServiceRegistry.delete(signature);return null;}return grant||null;}
function clientStoreServiceGrant(card,grant){const signature=clientServiceSignature(card);if(!signature)return;clientServiceRegistry.set(signature,grant);while(clientServiceRegistry.size>64)clientServiceRegistry.delete(clientServiceRegistry.keys().next().value);}
function clientServiceTexts(card){const fields=card?.fields||{};return [card?.title,card?.body,card?.output,card?.tags,...(Array.isArray(card?.titles)?card.titles:[]),fields.description,fields.details,fields.highlight].filter(value=>typeof value==='string');}
// Only the authenticated own-server GET path registers transport provenance.
// A serialized policy marker alone never grants authority. Clones must match
// exact approved content and every lookup checks the deadline again.
export function acceptAuthenticatedClientCard(card){
 try{assertClientServiceClaims(card);return card;}catch(error){if(!(error instanceof ClientPublicationReviewRequired))throw error;}
 const expiresAt=typeof card?.serviceAuthorization?.expiresAt==='string'?Date.parse(card.serviceAuthorization.expiresAt):NaN;
 if(card?.publicationPolicyVersion!==PUBLICATION_POLICY_VERSION||card?.serviceAuthorization?.version!==1||!Number.isFinite(expiresAt)||expiresAt<Date.now()||expiresAt>Date.now()+30*86400000)throw new ClientPublicationReviewRequired('Услуги требуют актуального подтверждения на Mac');
 clientStoreServiceGrant(card,{kind:'server',expiresAt,texts:clientServiceTexts(card).map(clientServiceCanonical)});return card;
}
function clientCheckServiceContext(card,context){
 const grant=clientServiceGrant(context);
 if(grant?.kind==='server'){
  for(const text of clientServiceTexts(card)){try{assertClientServiceClaims({body:text,fields:{}});}catch(error){if(!(error instanceof ClientPublicationReviewRequired)||!grant.texts.includes(clientServiceCanonical(text)))throw error;}}
  if(card.fields&&context.fields)for(const key of ['registrationMonths','registrationPeople','pickup','luggage','arrival','support'])if(Object.hasOwn(card.fields,key)&&JSON.stringify(card.fields[key])!==JSON.stringify(context.fields[key]))throw new ClientPublicationReviewRequired(key,'Объём услуги изменился');
  return grant;
 }
 const original=grant?.kind==='private'?grant.context:context,merged={...(original?.fields||original||{}),...(card.fields||{})},privateContext={fields:Object.fromEntries(['url','editorIdentity','resultId','id','registrationMonths','registrationPeople','pickup','luggage','arrival','support','serviceBenefits','serviceConfirmations'].filter(key=>merged[key]!==undefined).map(key=>[key,merged[key]]))};
 assertClientServiceClaims({...card,fields:privateContext.fields});return {kind:'private',context:JSON.parse(JSON.stringify(privateContext))};
}
export function clientPublicationCard(card={},options={}){
 const context=options.serviceContext||card,serviceGrant=clientCheckServiceContext(card,context);
 clientCheckDisputedFacts(card);
 const paymentFields=card?.fields&&typeof card.fields==='object'?card.fields:card;
 if(Number(paymentFields?.price)>0)for(const [key,unit]of[['pricePeriod','month'],['priceCurrency','RUB']])if(paymentFields[key]&&paymentFields[key]!==unit)throw new ClientPublicationReviewRequired(key+': '+paymentFields[key],'Перед публикацией подтвердите месячную ставку в рублях');
 if(Number(paymentFields?.deposit)>0&&paymentFields.depositCurrency&&paymentFields.depositCurrency!=='RUB')throw new ClientPublicationReviewRequired('depositCurrency: '+paymentFields.depositCurrency,'Перед публикацией подтвердите валюту залога');
 const active=new Set();
 function project(value,key='',depth=0){
  if(depth>32)throw TypeError('Слишком глубокая модель публикации');
  if(typeof value==='string'){
   if(/^data:image\/(?:png|jpeg|webp);base64,/i.test(value))return value;
   return sanitizeClientText(value);
  }
  if(value===null||typeof value!=='object')return value;
  if(active.has(value))throw TypeError('Циклическая модель публикации');active.add(value);
  let result;if(Array.isArray(value))result=value.map(item=>project(item,key,depth+1));
  else{result={};for(const [name,item] of Object.entries(value)){if(clientPrivateKey.test(name))continue;const selected=name==='listing'&&item&&typeof item==='object'&&!Array.isArray(item)?Object.fromEntries(['url','images','latitude','longitude','sourceReadiness'].filter(field=>item[field]!==undefined).map(field=>[field,item[field]])):item;result[name]=project(selected,name,depth+1);}}
  active.delete(value);return result;
 }
 const projected=project(card);if(projected&&typeof projected==='object'&&!Array.isArray(projected)){for(const key of ['body','title','tags'])if(key in projected&&typeof projected[key]!=='string')projected[key]='';if('titles' in projected)projected.titles=(Array.isArray(projected.titles)?projected.titles:typeof projected.titles==='string'?[projected.titles]:[]).filter(value=>typeof value==='string'&&value.trim());projected.publicationPolicyVersion=PUBLICATION_POLICY_VERSION;clientStoreServiceGrant(projected,serviceGrant);}return projected;
}
