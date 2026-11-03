import {readCianHtml,cianFieldFacts,CIAN_PARSER_VERSION} from './source-adapters/cian.mjs';
import {readAvitoHtml,avitoFieldFacts} from './source-adapters/avito.mjs';
// Expire only parsed CIAN source snapshots from an older gallery contract.
// Saved cards, media blobs, manual choices and other source caches are untouched.
export function sourceCacheMatchesParser(entry,url=''){
 let platform=entry?.source?.platform||'';
 try{const host=new URL(url).hostname;if(/(^|\.)cian\.ru$/i.test(host))platform='cian';else if(/(^|\.)avito\.ru$/i.test(host))platform='avito';}catch{}
 return platform!=='cian'||entry?.source?.stats?.adapter===CIAN_PARSER_VERSION;
}
// Dispatch only. Platform markup and business labels live in separate adapters.
export function sourcePlatform(input,url=''){
 if(url)try{const host=new URL(url).hostname;if(/(^|\.)avito\.ru$/i.test(host))return 'avito';if(/(^|\.)cian\.ru$/i.test(host))return 'cian';}catch{}
 const avito=input.search(/data-marker\s*=\s*["']item-view\/(?:title-info|item-params|main-gallery|item-description)["']/i),cian=input.search(/data-name\s*=\s*["'](?:OfferTitleNew|OfferTitle|Description|OfferDescription|OfferSummaryInfoItem)["']|_cianConfig\[/i);
 return avito>=0&&(cian<0||avito<cian)?'avito':'cian';
}
export function readSourceHtml(input,{url=''}={}){
 if(typeof input!=='string'||input.length>8_000_000)throw Error('HTML должен быть текстом размером до 8 МБ');
 return sourcePlatform(input,url)==='avito'?readAvitoHtml(input,{url}):readCianHtml(input);
}
export function sourceFieldFacts(source){return source.platform==='avito'?avitoFieldFacts(source):cianFieldFacts(source);}
