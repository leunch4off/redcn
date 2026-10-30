import test from 'node:test';
import assert from 'node:assert/strict';
import {normalizeDesignTemplate,buildPresentationHtml,presentationCardData,expandDesignText,safePresentationImage,PRESENTATION_BLOCKS,presentationCardKey} from '../web/presentation-studio.mjs';
const card={titles:['明亮公寓 <script>alert(1)</script>'],body:'第一行\nSecond line & <img src=x onerror=alert(1)>',tags:'#莫斯科',fields:{price:80000,area:54,rooms:2,metro:'Юго-Западная',address:'Москва',latitude:55.7,longitude:37.5}};
test('presentation templates preserve valid order and restrict colors fonts paper and text bounds',()=>{
 const t=normalizeDesignTemplate({preset:'night',accent:'red; background:url(https://evil)',font:'evil',paper:'evil',contact:'x'.repeat(1000),blocks:[{id:'photos',enabled:false},{id:'photos'},{id:'bad'},{id:'title'}],intro:{fontSize:300,overlay:-10,position:'bad',photoIndex:500}});
 assert.equal(t.accent,'#dfad72');assert.equal(t.font,'serif');assert.equal(t.paper,'A4');assert.equal(t.contact.length,500);assert.deepEqual(t.blocks.slice(0,2),[{id:'photos',enabled:false},{id:'title',enabled:true}]);assert.equal(new Set(t.blocks.map(x=>x.id)).size,PRESENTATION_BLOCKS.length);assert.equal(t.intro.fontSize,100);assert.equal(t.intro.overlay,0);assert.equal(t.intro.photoIndex,29);
});
test('export HTML escapes all card and template text and embeds only trusted data photos',()=>{
 const html=buildPresentationHtml(card,{footer:'<iframe>brand</iframe>',contact:'<script>x</script>'},{photos:['https://evil/a.png','data:image/svg+xml;base64,PHN2Zz4=','data:image/png;base64,aGVsbG8='],intro:'javascript:alert(1)'});
 assert.ok(html.includes('&lt;script&gt;'));assert.ok(html.includes('&lt;img src=x onerror=alert(1)&gt;'));assert.ok(html.includes('data:image/png;base64,aGVsbG8='));assert.ok(!html.includes('<script>'));assert.ok(!html.includes('<iframe>'));assert.ok(!html.includes('src="https://'));assert.ok(!html.includes('data:image/svg'));assert.ok(html.includes('https://www.openstreetmap.org/'));assert.ok(html.includes('@page{size:A4;'));
});
test('PDF export uses exact selected block order and omitted sections stay omitted',()=>{
 const t=normalizeDesignTemplate({blocks:[{id:'description',enabled:true},{id:'title',enabled:true},{id:'facts',enabled:false},{id:'photos',enabled:false},{id:'location',enabled:false},{id:'cover',enabled:false},{id:'contact',enabled:false}]});
 const html=buildPresentationHtml(card,t);assert.ok(html.indexOf('<section class="description">')<html.indexOf('<section class="title">'));assert.ok(!html.includes('<section class="facts">'));assert.ok(!html.includes('<section class="location">'));
});
test('coordinate links require actual valid coordinate pairs and zero is a valid coordinate',()=>{
 assert.equal(presentationCardData({fields:{latitude:91,longitude:37}}).mapUrl,'');assert.equal(presentationCardData({fields:{latitude:'',longitude:37}}).mapUrl,'');assert.match(presentationCardData({fields:{latitude:0,longitude:0}}).mapUrl,/mlat=0&mlon=0/);assert.equal(presentationCardData({fields:{}}).price,'');assert.equal(expandDesignText('{{title}}: {{area}} / {{unknown}}',card),'明亮公寓 <script>alert(1)</script>: 54 / {{unknown}}');
});
test('template normalization is idempotent and safe image validator rejects active formats',()=>{
 const t=normalizeDesignTemplate();assert.deepEqual(normalizeDesignTemplate(t),t);assert.equal(safePresentationImage('data:image/svg+xml;base64,PHN2Zz4='),'');assert.equal(safePresentationImage('data:image/png;base64,<script>'),'');assert.equal(safePresentationImage('file:///private/secret'),'');assert.equal(normalizeDesignTemplate(null).version,2);
});

test('card fingerprints reject changed text or photo selection while ignoring assigned storage id',()=>{
 const original=presentationCardKey(card);assert.equal(presentationCardKey({...card,id:'new-storage-id'}),original);assert.notEqual(presentationCardKey({...card,body:'changed'}),original);assert.notEqual(presentationCardKey({...card,fields:{...card.fields,photoSelection:['second']}}),original);assert.notEqual(presentationCardKey({...card,listing:{images:['https://source/other.jpg']}}),original);
});

test('presentation identity ignores cache hydration but detects object changes and crop edits',()=>{const baseline={...card,identity:7,fields:{...card.fields,photoEdits:{order:[],excluded:[],crops:{}}}};assert.equal(presentationCardKey(baseline),presentationCardKey({...baseline,fields:{...baseline.fields,resultId:'new-id',photoEvidence:[{id:'desk'}],photoEdits:{...baseline.fields.photoEdits,observations:{cached:true}}}}));assert.notEqual(presentationCardKey(baseline),presentationCardKey({...baseline,identity:8}));assert.notEqual(presentationCardKey(baseline),presentationCardKey({...baseline,fields:{...baseline.fields,photoEdits:{...baseline.fields.photoEdits,crops:{photo:{bottom:20}}}}}));});


test('new default studio design produces Chinese Ciliev landscape pages with safe built-in branding',()=>{
 const design=normalizeDesignTemplate();assert.equal(design.version,2);assert.equal(design.brand.name,'ciliev');assert.equal(design.theme.pdf.background,'#07162b');assert.equal(design.theme.pdf.accent,'#b79665');
 const html=buildPresentationHtml(card,design,{photos:['data:image/png;base64,aGVsbG8=']});
 assert.match(html,/<html lang="zh-CN">/);assert.match(html,/@page\{size:A4 landscape/);assert.match(html,/ciliev/);assert.match(html,/房地产/);assert.match(html,/房产顾问/);assert.ok(!/[А-Яа-яЁё]/.test(html));assert.ok(!html.includes('<script>'));assert.ok(!html.includes('Малафеев'));assert.ok(!html.includes('m16'));
});
test('explicit v1 callers keep their bounded legacy document while a fresh default uses v2',()=>{
 const old=normalizeDesignTemplate({version:1,preset:'minimal',contact:'Custom contact',blocks:[{id:'description',enabled:false}]});assert.equal(old.version,1);assert.equal(old.contact,'Custom contact');assert.equal(old.blocks[0].enabled,false);
 const html=buildPresentationHtml(card,old);assert.ok(!html.includes('<section class="description">'));assert.equal(normalizeDesignTemplate().version,2);
});
test('publication identity includes university commute and confirmed equipment changes',()=>{
 const base={...card,fields:{...card.fields,features:['desk'],universities:'МГУ | 40 | metro',campus:''}};const key=presentationCardKey(base);
 for(const patch of [{universities:'РУДН | 50 | bus'},{features:['desk','fridge']},{featureCounts:{desk:2}},{campus:'Новый кампус'}])assert.notEqual(presentationCardKey({...base,fields:{...base.fields,...patch}}),key);
});
