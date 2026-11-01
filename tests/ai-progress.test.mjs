import assert from 'node:assert/strict';
import test from 'node:test';
import {aiProgressText} from '../web/ai-progress.mjs';

test('MLX counters are measured separately from total request time',()=>{
 const text=aiProgressText({backend:'mlx',model:'mlx-community/fixture',operation:'text',phase:'generating',active:true,elapsedSeconds:8,generatedTokens:25,tokensPerSecond:12.5,generationSeconds:2},{extraElapsed:1});
 assert.match(text.metrics,/Прошло 9 с/);assert.match(text.metrics,/25 токенов/);assert.match(text.metrics,/12,5 токенов\/с/);assert.match(text.metrics,/Генерация 2 с/);
 assert.match(text.note,/загрузка модели/);
});

test('detector never displays token rate even if an invalid response includes one',()=>{
 const text=aiProgressText({backend:'yolox-trained',model:'redcn-furniture-v1',operation:'furniture',phase:'complete',tokenBased:false,photoIndex:3,photoTotal:8,elapsedSeconds:5,inferenceSeconds:2,imagesPerSecond:.5,tokensPerSecond:123,generatedTokens:123});
 assert.match(text.details,/Фото 3 из 8/);assert.match(text.metrics,/0,5 фото\/с/);assert.doesNotMatch(text.metrics,/токенов/);assert.match(text.note,/не генерирует токены/);
});

test('cached analysis cannot pretend old inference speed is current',()=>{
 const text=aiProgressText({backend:'ollama',operation:'furniture',phase:'cached',cached:true,tokensPerSecond:300,generatedTokens:600,imagesPerSecond:9});
 assert.equal(text.metrics,'');assert.match(text.note,/повторно не запускалась/);
});

test('loading shows elapsed time and no invented token count',()=>{
 const text=aiProgressText({backend:'mlx',operation:'text',phase:'loading',active:true,elapsedSeconds:3});
 assert.equal(text.metrics,'Прошло 3 с');assert.match(text.note,/после первых/);
 const unknown=aiProgressText({operation:'furniture',phase:'analyzing',active:true,elapsedSeconds:1});
 assert.doesNotMatch(unknown.note,/не генерирует токены/);
});

test('untrusted paths and malformed counters never become visible',()=>{
 const text=aiProgressText({backend:'mlx',model:'/Users/private/token',phase:'generating',elapsedSeconds:NaN,tokensPerSecond:Infinity,generatedTokens:true,prompt:'private prompt'});
 assert.doesNotMatch(JSON.stringify(text),/private|Infinity|NaN/);assert.equal(text.metrics,'');
 assert.equal(aiProgressText({phase:'<script>private</script>'}),null);
 assert.equal(aiProgressText(null),null);
});

test('stale speed is labeled and a stopped clock does not keep advancing',()=>{
 const text=aiProgressText({backend:'mlx',operation:'text',phase:'generating',active:true,elapsedSeconds:8,generatedTokens:25,tokensPerSecond:12.5,sampleAgeSeconds:9});
 assert.match(text.metrics,/Последнее измерение/);
 const stopped=aiProgressText({backend:'mlx',phase:'generating',active:false,elapsedSeconds:8},{extraElapsed:500});
 assert.match(stopped.metrics,/Затрачено 8 с/);assert.match(stopped.details,/Последний этап/);
});

test('rules fallback labels the final text separately from measured model draft speed',()=>{
 const text=aiProgressText({backend:'mlx',model:'fixture-model',operation:'text',phase:'complete',textSource:'rules',generatedTokens:60,tokensPerSecond:15,generationSeconds:4});
 assert.match(text.title,/проверенные факты/);assert.match(text.details,/Модель черновика/);
 assert.match(text.metrics,/15 токенов\/с/);assert.match(text.note,/относится к черновику/);
 const skipped=aiProgressText({phase:'complete',textSource:'rules'});
 assert.match(skipped.title,/Китайский текст/);assert.doesNotMatch(skipped.title,/мебель/);
 assert.equal(skipped.metrics,'');assert.match(skipped.note,/недоступна/);
});
