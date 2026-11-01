import test from 'node:test';
import assert from 'node:assert/strict';
import {importFailureInfo,importErrorSourceUrl} from '../web/import-errors.mjs';

test('Avito protection errors have a reason and manual paste path without an automatic replay',()=>{
 for(const status of [401,403,429]){const value=importFailureInfo({status,message:'Источник ограничил доступ'},{url:'https://www.avito.ru/moskva/kvartiry/test_123?context=tracking',stage:'navigate'});assert.equal(value.code,'attention');assert.equal(value.retryable,false);assert.equal(value.manualRequired,true);assert.match(value.title,/Авито/);assert.match(value.suggestion,/вставьте текст/);assert.equal(value.sourceUrl,'https://www.avito.ru/moskva/kvartiry/test_123');assert.equal(value.stage,'Загрузка страницы');}
});
test('timeout, network, TLS, runtime, schema, missing HTML and removed listing remain distinct',()=>{
 const cases=[['HTTP-загрузка не завершилась за 30 с','timeout'],['HTTP Error 503: service unavailable','server'],['<urlopen error connection reset>','network'],['CERTIFICATE_VERIFY_FAILED','tls'],['Node.js 22+ не найден','runtime'],['Неверные поля объявления: price требуется число','schema'],['Источник вернул не HTML','not-html'],['HTTP Error 410: Gone','unavailable'],['Импорт отменён','cancelled'],['AbortSignal.timeout is not a function','browser']];
 for(const [message,code]of cases)assert.equal(importFailureInfo(Error(message)).code,code,message);
 assert.equal(importFailureInfo(Error('Пройдите проверку браузера')).retryable,false);
});
test('error UI only links validated source URLs and strips headers/secrets from diagnostic text',()=>{
 assert.equal(importErrorSourceUrl('[Авито](https://www.avito.ru/flat/1?token=PRIVATE)'), 'https://www.avito.ru/flat/1');
 for(const url of ['javascript:alert(1)','https://user:pass@cian.ru/flat/1','https://cian.ru.evil.invalid/flat/1','http://cian.ru/x','https://localhost/x'])assert.equal(importErrorSourceUrl(url),'');
 const value=importFailureInfo(Error('Network error\nAuthorization: Bearer PRIVATE_TOKEN\nCookie: PRIVATE_SESSION\nsecret=abcdefgh123456'));
 assert.doesNotMatch(value.message,/PRIVATE_|abcdefgh123456/);assert.match(value.message,/скрыт/);
});
