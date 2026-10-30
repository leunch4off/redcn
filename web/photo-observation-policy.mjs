// Positive photo observations only. Keep these object terms aligned with
// cli/local_photo_ai.py; no model loading, network or generator dependency.
export const PHOTO_OBJECT_TERMS=Object.freeze({
  "bedAny": "кроват|bed|床",
  "mattress": "матрас|mattress|床垫",
  "wardrobe": "шкаф|wardrobe|armoire|衣柜",
  "slidingWardrobe": "купе|sliding|推拉",
  "nightstands": "тумб|nightstand|bedside|床头柜",
  "desk": "стол|desk|书桌",
  "sofa": "диван|sofa|couch|沙发",
  "armchair": "кресл|armchair|recliner|扶手椅",
  "tvStand": "тумб|stand|电视柜",
  "tv": "телевиз|\\btv\\b|television|电视",
  "computer": "компьют|моноблок|ноутбук|computer|laptop|电脑",
  "projector": "проектор|projector|投影",
  "piano": "пианино|фортепиано|роял|piano|钢琴",
  "ac": "кондиционер|air.?condition|\\bac\\b|空调",
  "fridge": "холодильник|fridge|refrigerator|冰箱",
  "stove": "плит|варочн|cooktop|stove|hob|炉|灶",
  "oven": "духов|духовк|oven|烤箱",
  "hood": "вытяжк|hood|extractor|油烟机",
  "sink": "мойк|раковин|sink|水槽",
  "microwave": "микроволн|свч|microwave|微波炉",
  "dishwasher": "посудомо|dishwasher|洗碗机",
  "kettle": "чайник|kettle|水壶",
  "table": "стол|table|餐桌",
  "chairs": "стул|стуль|chair|椅",
  "kitchenFurniture": "кухон|шкаф|cabinet|橱柜",
  "washer": "стираль|washing|washer|洗衣机",
  "dryer": "сушиль|dryer|烘干机",
  "bath": "ванн|bath|浴缸",
  "shower": "душ|shower|淋浴",
  "washbasin": "раковин|умывальник|basin|sink|洗手盆",
  "shelves": "полк|полок|стеллаж|shel|rack|架",
  "coffeeTable": "журнальн|кофейн|coffee.?table|茶几",
  "dresser": "комод|dresser|chest.?of.?drawers|斗柜",
  "cabinet": "тумб|буфет|шкафчик|cabinet|sideboard|边柜",
  "shoeCabinet": "обувниц|обувн|shoe|鞋柜",
  "ottoman": "пуф|ottoman|pouf|软凳",
  "bench": "скам|лавк|bench|长凳",
  "mirror": "зеркал|mirror|镜",
  "curtains": "штор|занавес|гардин|curtain|drape|窗帘",
  "dryingRack": "сушилк|бель|drying.?rack|clothes.?rack|晾衣架",
  "iron": "утюг|\\biron\\b|熨斗",
  "vacuum": "пылесос|vacuum|吸尘器",
  "coffeeMachine": "кофемаш|кофеварк|coffee.?machine|coffee.?maker|咖啡机",
  "toaster": "тостер|toaster|烤面包机",
  "router": "роутер|маршрутизатор|router|路由器",
  "monitor": "монитор|monitor|显示器",
  "speakers": "колон|динамик|speaker|音箱",
  "waterHeater": "водонагрев|бойлер|water.?heater|boiler|热水器",
  "waterFilter": "фильтр|water.?filter|净水器"
});
export const PHOTO_REVIEW_ONLY=Object.freeze(['bed','internet','closet','pantry','balcony','loggia','homeCinema','roomFurniture']);
export const PHOTO_FEATURE_IDS=Object.freeze([...new Set([...Object.keys(PHOTO_OBJECT_TERMS),...PHOTO_REVIEW_ONLY])]);
const photoPolicyReviewOnly=new Set(PHOTO_REVIEW_ONLY);
// Python's \w and \b are Unicode-aware; JavaScript's are ASCII-only with /u.
const photoPolicyWord='[\\p{L}\\p{N}_]';
const photoPolicyBoundary='(?:(?<='+photoPolicyWord+')(?!'+photoPolicyWord+')|(?<!'+photoPolicyWord+')(?='+photoPolicyWord+'))';
const photoPolicyUnicode=pattern=>pattern.replaceAll('\\w',photoPolicyWord).replaceAll('\\b',photoPolicyBoundary);
export const PHOTO_NON_DIRECT_PATTERN="\\b(?:no|not|absent|missing|cannot|without|unseen|hidden|inferred|assum\\w*|probab\\w*|perhaps|maybe|might|possibly|uncertain|unclear|likely|suggests?|could|will|planned|planning|plans?|intend\\w*|upcoming|future|pending|scheduled|going\\s+to)\\b|(?<![а-яё])(?:не|нет|без|возможно|вероятно|скрыт\\w*|предполож\\w*|долж\\w*|скорее|похож\\w*|кажется|сомн\\w*|отсутств\\w*|будет|будут|буду|будем|будете|будешь|планир\\w*|запланир\\w*|планах|намерен\\w*|собира(?:ется|ются|емся|юсь)|предстоит|скоро|купят|докупят|установят|привезут|доставят|появится|появятся)(?![а-яё])|没有|沒有|未见|未見|看不到|不可见|不可見|不确定|不確定|可能|推测|推測|隐藏|隱藏|将|將|计划|計劃|准备|準備|打算|待安装|待安裝|待购|待購|待添置|尚未|还未|還未";
const photoPolicyNonDirect=new RegExp(photoPolicyUnicode(PHOTO_NON_DIRECT_PATTERN),'iu');
const photoPolicyObjects=new Map(Object.entries(PHOTO_OBJECT_TERMS).map(([id,expression])=>[id,new RegExp(photoPolicyUnicode(expression),'iu')]));
const photoPolicyHash=value=>typeof value==='string'&&value.length===64&&/^[a-f0-9]{64}$/u.test(value);
function photoPolicyLocalModel(value){
  return typeof value==='string'&&value.length>=1&&value.length<=128&&/^[A-Za-z0-9]/u.test(value)&&!/[^A-Za-z0-9_.:/-]/u.test(value)&&!/cloud|:\/\//iu.test(value);
}
function photoPolicyDirectPart(part,row){
  if(!part||typeof part!=='object'||Array.isArray(part)||part.kind!=='model-visual-observation'||part.provider!=='ollama'||part.source!=='local-photo'||part.originalId!==row.originalId||part.photoId!==row.originalId||part.directlyVisible!==true||part.confidence!==row.confidence||!photoPolicyLocalModel(part.model))return false;
  if(typeof part.text!=='string')return false;
  const text=part.text.trim();if(text.length<8||text.length>600)return false;
  const length=[...text].length;
  return length>=8&&length<=300&&!/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/u.test(text)&&!photoPolicyNonDirect.test(text)&&photoPolicyObjects.get(row.id).test(text);
}

/** Strict acceptance of one directly visible, local, original-bound object.
 * Missing observations prove nothing; repeated views and counts are handled
 * by the caller. Structural categories and double-bed width require a human.
 */
export function isAutomaticPhotoObservation(row){
  return !!row&&typeof row==='object'&&!Array.isArray(row)&&photoPolicyObjects.has(row.id)&&!photoPolicyReviewOnly.has(row.id)&&row.status==='auto-visible'&&row.autoEligible===true&&typeof row.confidence==='number'&&Number.isFinite(row.confidence)&&row.confidence>=.9&&row.confidence<=1&&photoPolicyHash(row.originalId)&&row.photoId===row.originalId&&Array.isArray(row.evidence)&&row.evidence.slice(0,8).some(part=>photoPolicyDirectPart(part,row));
}
