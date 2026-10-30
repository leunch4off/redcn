import test from 'node:test';
import assert from 'node:assert/strict';
import {safeWatchOfferUrl,safeWatchSearchUrl,formatWatchDate,normalizeWatchItem} from '../web/listing-watch.mjs';

test('watch links use only canonical Cian offers and discard tracking fragments',()=>{
  assert.equal(safeWatchOfferUrl('https://cian.ru/rent/flat/12345/?utm_campaign=tracking#photos'),'https://www.cian.ru/rent/flat/12345/');
  assert.equal(safeWatchOfferUrl('https://www.cian.ru/sale/flat/98765'),'https://www.cian.ru/sale/flat/98765/');
  for(const input of ['javascript:alert(1)','http://www.cian.ru/rent/flat/123/','https://www.cian.ru.evil.test/rent/flat/123/','https://user:password@cian.ru/rent/flat/123/','https://cian.ru:444/rent/flat/123/','https://cian.ru/rent/flat/123/extra','https://cian.ru/cat.php?offer_type=flat'])assert.equal(safeWatchOfferUrl(input),'',input);
});

test('watch configuration accepts search URLs without treating an offer as a search',()=>{
  assert.equal(safeWatchSearchUrl(' https://www.cian.ru/cat.php?deal_type=rent&region=1#page '),'https://www.cian.ru/cat.php?deal_type=rent&region=1');
  assert.equal(safeWatchSearchUrl('https://www.cian.ru/snyat-kvartiru-moskva/'),'https://www.cian.ru/snyat-kvartiru-moskva/');
  for(const input of ['https://www.cian.ru/rent/flat/123/','https://evil.test/cat.php','https://www.cian.ru/login/','https://pass@www.cian.ru/cat.php','http://www.cian.ru/cat.php'])assert.equal(safeWatchSearchUrl(input),'',input);
});

test('publication dates never fall back to first seen or infer a missing publication time',()=>{
  const row=normalizeWatchItem({id:'cian:123',url:'https://www.cian.ru/rent/flat/123/',firstSeen:1793401200,publishedAt:null,publishedLabel:'Сегодня',isNew:true});
  assert.equal(row.publishedAt,null);assert.equal(row.publishedLabel,'Сегодня');assert.equal(row.firstSeen,1793401200);
  assert.equal(formatWatchDate(row.publishedAt),'Дата не указана');
  assert.equal(formatWatchDate('2026-10-31'),'31.10.2026');
  assert.equal(formatWatchDate('2026-10-31T08:30:00'),'31.10.2026');
  assert.match(formatWatchDate('2026-10-31T05:30:00Z'),/31\.10\.2026.*08:30 МСК/);
});

test('invalid source dates remain unknown and milliseconds and seconds agree',()=>{
  for(const input of [null,undefined,'yesterday','2026-02-31','2026-13-01',NaN,Infinity,{},[]])assert.equal(formatWatchDate(input),'Дата не указана');
  assert.equal(formatWatchDate(1793424600),formatWatchDate(1793424600000));
});

test('untrusted feed fields stay bounded text and cannot provide a scan URL',()=>{
  const row=normalizeWatchItem({id:'cian:1',title:'<img src=x onerror=alert(1)>',url:'javascript:alert(1)',price:68000,currency:'RUB',area:41,metro:'Рассказовка',firstSeen:'2026-10-31T00:00:00+03:00',publishedAt:'bad',publishedLabel:'',read:true,isNew:false});
  assert.equal(row.title,'<img src=x onerror=alert(1)>');assert.equal(row.url,'');assert.equal(row.publishedAt,null);assert.equal(row.read,true);assert.equal(row.isNew,false);assert.match(row.price,/68.*000 ₽/);assert.equal(row.area,'41 м²');
  assert.equal(normalizeWatchItem({id:'<bad>',title:'x'.repeat(1000)}).id,'');assert.equal(normalizeWatchItem({title:'x'.repeat(1000)}).title.length,400);
  assert(!normalizeWatchItem({price:1200}).price.includes('₽'));assert.match(normalizeWatchItem({price:1200,currency:'EUR'}).price,/€$/);
});
