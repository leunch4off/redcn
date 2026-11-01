// Literal rental values shared by the two source adapters. No phrase here is
// evidence of a different field (in particular, zero commission is not owner).
export function rentalNumber(value){
 if(typeof value==='number')return Number.isFinite(value)&&value>=0?String(value):'';
 if(typeof value!=='string'||!/^\s*\d[\d \u00a0\u202f]*(?:[.,]\d+)?\s*$/.test(value))return '';
 const number=Number(value.replace(/[ \u00a0\u202f]/g,'').replace(',','.'));
 return Number.isFinite(number)&&number>=0?String(number):'';
}
export function rentalAmount(text){
 const value=String(text??'').trim();
 const match=value.match(/(?:^|[^\d])([0-9][\d \u00a0\u202f]*(?:[.,]\d+)?)\s*(тыс\.?|млн\.?)?/i);
 if(!match)return '';
 const number=rentalNumber(match[1]);if(!number)return '';
 return String(Number(number)*(match[2]?/^млн/i.test(match[2])?1_000_000:1000:1));
}
export function rentalPeriod(value){
 const text=String(value??'').toLowerCase().trim();
 if(/^(?:monthly|month|p1m|mon)$/i.test(text)||/(?:\/|в|за)\s*(?:мес(?:яц)?[а-я.]*|月)|в месяц/i.test(text))return 'month';
 if(/^(?:daily|day|p1d|d|daytime)$/i.test(text)||/(?:\/|в|за)\s*(?:сут(?:ки|ок)?[а-я.]*|день|дня|дней)|посуточ/i.test(text))return 'day';
 if(/^(?:weekly|week|p1w|w)$/i.test(text)||/(?:\/|в|за)\s*недел[а-я.]*/i.test(text))return 'week';
 if(/^(?:yearly|annual|year|p1y|yr)$/i.test(text)||/(?:\/|в|за)\s*(?:год|лет|года)/i.test(text))return 'year';
 return 'unknown';
}
export function rentalCurrency(value){
 const text=String(value??'').trim();
 if(/(?:^|[^a-z])(?:rur|rub)(?:$|[^a-z])/i.test(text)||/₽|руб(?:\.|лей|ля)?(?:\s|$)/i.test(text))return 'RUB';
 if(/(?:^|[^a-z])usd(?:$|[^a-z])/i.test(text)||/\$|доллар/i.test(text))return 'USD';
 if(/(?:^|[^a-z])eur(?:$|[^a-z])/i.test(text)||/€|евро(?:\s|$)/i.test(text))return 'EUR';
 return 'unknown';
}
export function rentalCommission(text){
 const value=String(text??'').trim();
 if(/^(?:нет|без комиссии|не взимается|отсутствует)$/i.test(value))return {percent:'0',status:'none'};
 const percent=value.match(/^\s*(\d+(?:[.,]\d+)?)\s*%\s*$/);
 if(percent){const number=rentalNumber(percent[1]);return {percent:number,status:Number(number)===0?'none':'present'};}
 const currency=rentalCurrency(value),amount=currency!=='unknown'&&!/%/.test(value)?rentalAmount(value):'';
 return {percent:'',status:'unknown',...(amount?{amount,currency}: {})};
}
export function rentalSeller(value){
 const text=String(value??'').trim();
 if(/^(?:собственник|от собственника|владелец|owner)$/i.test(text))return 'owner';
 if(/^(?:агент|агентство(?: недвижимости)?|ри[еэ]лтор|agent|agency)$/i.test(text))return 'agent';
 return 'unknown';
}
export function explicitRentalLayout(value){
 const text=String(value??'').toLowerCase();
 if(['studio','euro2','euro3'].includes(text.trim()))return text.trim();
 const found=new Set();
 for(const [layout,pattern]of [['euro2',/(?:^|[^\p{L}\p{N}])евро[ -]?(?:2|двушк[аиуой]*)(?=$|[^\p{L}\p{N}])/gu],['euro3',/(?:^|[^\p{L}\p{N}])евро[ -]?(?:3|тр[её]шк[аиуой]*)(?=$|[^\p{L}\p{N}])/gu],['studio',/(?:^|[^\p{L}\p{N}])(?:студи[яи]|studio)(?=$|[^\p{L}\p{N}])/gu]]){
  for(const match of text.matchAll(pattern))if(!/(?:^|\s)не\s*$/u.test(text.slice(Math.max(0,match.index-12),match.index)))found.add(layout);
 }
 return found.size===1?[...found][0]:'';
}
