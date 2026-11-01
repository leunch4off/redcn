// One client-publication policy. Source facts, drafts and stored cards are private
// inputs and must never be overwritten by this derived projection.
export const PUBLICATION_POLICY_VERSION='client-publication-v1';
const clientCommissionPattern=/комисси[а-яё]*|агентск[а-яё]*\s+(?:вознагражден[а-яё]*|сбор[а-яё]*|услуг[а-яё]*)|вознагражден[а-яё]*\s+(?:агент[а-яё]*|риелтор[а-яё]*)|риелторск[а-яё]*\s+(?:сбор[а-яё]*|услуг[а-яё]*)|中介\s*(?:费|費)|佣金|傭金|免佣|经纪\s*(?:费|費)|經紀\s*(?:费|費)|代理\s*(?:费|費)|\bcommission\b|\b(?:broker(?:age)?|agent|agency)\s+fees?\b/iu;
const clientProtectedPattern=/(?:аренд[ауы]|арендная\s+плата|залог|депозит|предоплат|аванс|коммунал|жку|квартплат|сч[её]тчик|площад|комнат|спальн|мебел|диван|кроват|холодильник|квартир|адрес|метро|этаж|\b(?:rent|deposit|utilities|bedrooms?)\b|租金|月租|押金|定金|预付|預付|物业|物業|水电|水電|面积|面積|卧室|臥室|家具|公寓|精装|精裝|平米|[一两兩二三四五六七八九十]室|[一两兩二三四五六七八九十]厅|地铁|地鐵|\d\s*(?:㎡|м²|м2))/iu;
const clientPrivateKey=/(?:commission|^(?:clientFee|agentFee|brokerFee|agencyFee|initialPayment|paymentCalculation|oneTimeFees|offer|bargainTerms|source(?:Audit|Description|Ru|Html)?|raw(?:Text|Html|Body|Description)?|original(?:Text|Html|Body|Description)|notes|operatorNotes|internal|rules|prefs|sourceEnvelope)$)/iu;
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
export function clientPublicationCard(card={}){
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
 const projected=project(card);if(projected&&typeof projected==='object'&&!Array.isArray(projected)){for(const key of ['body','title','tags'])if(key in projected&&typeof projected[key]!=='string')projected[key]='';if('titles' in projected)projected.titles=(Array.isArray(projected.titles)?projected.titles:typeof projected.titles==='string'?[projected.titles]:[]).filter(value=>typeof value==='string'&&value.trim());projected.publicationPolicyVersion=PUBLICATION_POLICY_VERSION;}return projected;
}
