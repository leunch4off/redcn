// Room vocabulary describes supplied layout data, never the presence of a bed.
// Audience copy is editorial framing and does not grant permission to rent.
export const DESCRIPTION_AUDIENCES=Object.freeze([
  {id:'general',label:'Общая'},
  {id:'student',label:'Студенты'},
  {id:'family',label:'Семья'},
  {id:'professional',label:'Работающие / командированные'},
]);
const listingPositiveRoomInteger=value=>value!==''&&value!==null&&value!==undefined&&Number.isInteger(Number(value))&&Number(value)>0?Number(value):null;
const listingRoomCountLabel=(value,noun)=>listingPositiveRoomInteger(value)===null?'':listingPositiveRoomInteger(value)+'间'+noun;
function listingSeparateBedroomClaim(text){
  for(const match of String(text).matchAll(/(?:独立|单独|单独的|封闭式)\s*卧室|(?:отдельн[а-яё]*|изолированн[а-яё]*)\s+спальн[а-яё]*|separate\s+bedroom/giu)){
    const before=String(text).slice(Math.max(0,match.index-35),match.index),after=String(text).slice(match.index+match[0].length,match.index+match[0].length+30);
    if(/(?:нет|без|не предусмотрена|no|without)\s*$/iu.test(before)||/(?:没有|无|不含)\s*$/u.test(before)||/^\s*(?:нет|отсутствует|не предусмотрена|不存在)/iu.test(after))continue;
    return true;
  }
  return false;
}
export function roomTerminology(fields={}){
  const value=fields&&typeof fields==='object'&&!Array.isArray(fields)?fields:{};
  const studio=value.layout==='studio',warnings=[];
  const bedrooms=listingPositiveRoomInteger(value.bedrooms),livingRooms=listingPositiveRoomInteger(value.livingRooms);
  if(studio&&(bedrooms!==null||value.separateBedroom===true||value.separate===true||listingPositiveRoomInteger(value.rooms)>1))warnings.push('Планировка противоречива: Studio указана вместе с отдельными комнатами или спальней. Проверьте планировку; отдельная спальня не выводится из кровати.');
  const manual=[value.description,value.highlight,value.sourceDescription].filter(item=>typeof item==='string').join('\n');
  if(studio&&listingSeparateBedroomClaim(manual))warnings.push('В авторском тексте Studio заявлена отдельная спальня. Сверьте текст с планировкой перед публикацией.');
  if(value.layout==='euro2'&&bedrooms!==null&&bedrooms!==1)warnings.push('Для евро-2 ожидается одна спальня, но в полях указано '+bedrooms+'. Уточните планировку; число комнат не заменяет число спален.');
  if(value.layout==='euro3'&&bedrooms!==null&&bedrooms!==2)warnings.push('Для евро-3 ожидаются две спальни, но в полях указано '+bedrooms+'. Уточните планировку; число комнат не заменяет число спален.');
  if(bedrooms!==null&&listingPositiveRoomInteger(value.rooms)!==null&&!['euro2','euro3','studio'].includes(value.layout)&&bedrooms>Number(value.rooms))warnings.push('Спален больше, чем комнат в объявлении. Проверьте независимые поля комнат и спален.');
  return {rooms:listingPositiveRoomInteger(value.rooms),bedrooms,livingRooms,
    roomTerm:'房间',bedroomTerm:'卧室',livingRoomTerm:'客厅',livingAreaTerm:'居住面积',
    bedroomLabel:studio?'':listingRoomCountLabel(value.bedrooms,'卧室'),
    livingRoomLabel:studio?'':listingRoomCountLabel(value.livingRooms,'客厅'),
    separateRooms:!studio&&value.separate===true,warnings};
}

const listingRoomCountValue=raw=>({одна:'1',один:'1',одной:'1',две:'2',два:'2',двух:'2',три:'3',трех:'3',трёх:'3',четыре:'4'})[String(raw).toLowerCase()]||String(raw);
export function extractRoomCounts(text=''){
  const value=String(text),out={};
  const count='(\\d{1,2}|одна|один|одной|две|два|двух|три|трех|трёх|четыре)';
  for(const [field,noun] of [['bedrooms','(?:спальн(?:я|и|ей|ь|е|ю)?|спален)'],['livingRooms','гостин(?:ая|ые|ых|ой|ую)?']]){
    const label=new RegExp('(?:^|[\\s,;\\n])(?:количество\\s+)?'+noun+'\\s*[:—-]\\s*'+count+'(?!\\d)(?!\\s*(?:м[²2]?|кв|㎡))','iu').exec(value);
    const inline=new RegExp('(?<![\\p{L}\\d])'+count+'\\s*(?:отдельн[а-яё]*\\s+|изолированн[а-яё]*\\s+)?'+noun+'(?![а-яё])','iu').exec(value);
    const match=label||inline;
    if(match&&!/(?:^|\s)(?:не|нет|без)\s*$/iu.test(value.slice(Math.max(0,match.index-15),match.index)))out[field]=listingRoomCountValue(match[1]);
  }
  return out;
}

export function descriptionAudience(value='general'){
  const aliases={worker:'professional',working:'professional',family:'family',student:'student',professional:'professional',general:'general'};
  return typeof value==='string'&&Object.hasOwn(aliases,value)?aliases[value]:'general';
}
const listingAudienceIntroductions=Object.freeze({
  student:[
    '在莫斯科读书找住处，可以先按自己的预算核对下面的户型、配置和出行信息。',
    '给关注莫斯科租房的同学：先看房屋参数，再核对所需院校的实际通勤。',
    '留学租房先整理需求：预算、生活配置与上课路线，下面是这套房源已提供的信息。',
  ],
  family:[
    '与家人一起找住处，可以先核对房间安排、生活配置和租赁条件；具体入住要求需另行确认。',
    '关注共同居住空间的家庭，可以从户型与收纳配置开始了解这套房源；入住条件请逐项核对。',
    '为家人找住处，先把面积、房间安排与日常费用看清楚，具体入住要求再向房东确认。',
  ],
  professional:[
    '在莫斯科工作找住处，可以先核对预算、室内配置与工作地点的实际通勤。',
    '关注工作与生活安排的朋友，可以按下面的房屋资料逐项比较租住需求。',
    '工作期间选择住处，先看房屋配置与费用，再确认租期和到工作地点的路线。',
  ],
});
export function audienceIntroduction(audience='general',variation=0){
  const rows=listingAudienceIntroductions[descriptionAudience(audience)];
  if(!rows)return '';
  const index=Number.isFinite(Number(variation))?Math.abs(Math.trunc(Number(variation))):0;
  return rows[index%rows.length];
}

export function normalizeListingStudioProfile(profile={}){
  const value=profile&&typeof profile==='object'&&!Array.isArray(profile)?profile:{};
  return {id:typeof value.id==='string'?value.id.slice(0,80):'',name:typeof value.name==='string'?value.name.slice(0,100):'',
    punctuation:['standard','compact','fullwidth'].includes(value.punctuation)?value.punctuation:'standard',
    emoji:['source','none'].includes(value.emoji)?value.emoji:'source',
    addressing:['neutral','polite','friendly'].includes(value.addressing)?value.addressing:'neutral'};
}
export function applyStudioProfile(text='',profile={}){
  const selected=normalizeListingStudioProfile(profile);let output=String(text??'');
  if(selected.addressing!=='neutral'){
    const pronoun=selected.addressing==='polite'?'您':'你';
    output=output.replace(/欢迎(?:用)?中文/gu,'欢迎'+pronoun+'用中文');
  }
  if(selected.emoji==='none')output=output.replace(/[\p{Extended_Pictographic}\p{Regional_Indicator}\uFE0F\u200D]/gu,'').replace(/^[ \t]+/gm,'').replace(/[ \t]+$/gm,'');
  if(selected.punctuation==='compact')output=output.replace(/\n[ \t]*\n+/g,'\n');
  if(selected.punctuation==='fullwidth'){
    // URLs, e-mail, identifiers and numeric punctuation must remain usable.
    output=output.split(/(https?:\/\/[^\s]+|[a-zA-Z][a-zA-Z0-9_.@/+:-]*|\d+(?:[:./-]\d+)+)/gu)
      .map((part,index)=>index%2?part:part.replace(/[:;()]/g,char=>({':':'：',';':'；','(':'（',')':'）'})[char])).join('');
  }
  return output.trim();
}
