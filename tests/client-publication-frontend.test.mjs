import test from 'node:test';
import assert from 'node:assert/strict';
import {ClientPublicationReviewRequired,containsClientCommission} from '../web/client-publication.mjs';
import {defaultPublicationDesign,resolvePublicationData,publicationNodeText} from '../web/publication-model.mjs';
import {buildPublicationHtml,renderPublicationPage} from '../web/publication-render.mjs';
import {presentationCardData,presentationCardKey,buildPresentationHtml,expandDesignText} from '../web/presentation-studio.mjs';
import {historyEntry,historyHtml,publicationPng} from '../web/history.mjs';

const rawCard=()=>({titles:['莫斯科公寓'],body:'租金：65000 ₽\n🤝 中介费：无需支付中介费\n押金：35000 ₽\n水电按表另付',tags:'#莫斯科租房 #免中介费',fields:{price:'65000',deposit:'35000',commission:'0',area:'52',rooms:'2',metro:'Лианозово',source:'Аренда 65000. Комиссия 50%.',sourceAudit:{description:'Raw source: commission 50%',fields:[{field:'commission',value:'50'}]}}});

test('old card publication projection removes fees and private source while preserving editable original and costs',()=>{
 const card=rawCard(),before=structuredClone(card),key=presentationCardKey(card),data=presentationCardData(card);
 assert.doesNotMatch(data.body,/中介费|佣金|комисси|commission/i);assert.match(data.body,/65000/);assert.match(data.body,/35000/);assert.match(data.body,/水电/);
 const html=buildPublicationHtml(card);assert(!containsClientCommission(html));assert.match(html,/65,000|6.5/);assert.match(html,/35000/);
 assert.deepEqual(card,before);assert.equal(presentationCardKey(card),key);
});

test('new and legacy custom template text is checked after placeholder expansion without overwriting the template',()=>{
 const card=rawCard(),design=defaultPublicationDesign();design.brand.footer='免中介费';
 design.surfaces.pdfOverview.nodes.push({...design.surfaces.pdfOverview.nodes.find(n=>n.type==='text'),id:'commission',bind:'',text:'中介费：50%'});
 const before=structuredClone(design),html=buildPublicationHtml(card,design);
 assert(!containsClientCommission(html));assert.deepEqual(design,before);
 const legacy={version:1,preset:'minimal',footer:'Без комиссии',intro:{subtitle:'Средняя площадь {{area}} м²'}};
 assert(!containsClientCommission(buildPresentationHtml(card,legacy)));assert.equal(legacy.footer,'Без комиссии');
 assert.equal(expandDesignText('中介费：{{price}}',card),'');
});

test('translated data and per-node rendered strings cannot reintroduce a fee',()=>{
 const card={fields:{name:'Тестовое имя',address:'Тестовое имя',price:65000}},data=resolvePublicationData(card,{}, {rules:{'Тестовое имя':'免中介费'}});
 assert(!containsClientCommission(JSON.stringify(data)));
 assert.equal(publicationNodeText({text:'{{a}}',bind:''},{a:'中介费：50%'}),'');
 assert.match(publicationNodeText({text:'Аренда 65000 ₽, комиссия 50%, залог 35000 ₽',bind:''},{}),/65000/);
 assert.match(publicationNodeText({text:'Аренда 65000 ₽, комиссия 50%, залог 35000 ₽',bind:''},{}),/35000/);
});

test('inseparable commission and rent fail with the original fragment instead of erasing another payment',()=>{
 const card={...rawCard(),body:'Аренда 65000 ₽ и комиссия 50%'};
 assert.throws(()=>buildPublicationHtml(card),error=>error instanceof ClientPublicationReviewRequired&&error.fragment.includes('65000'));
 assert.throws(()=>publicationNodeText({text:'租金65000且佣金50%',bind:''},{}),ClientPublicationReviewRequired);
 // Computing the internal CAS key must still work so the UI can display the
 // publication-review error inside render's guarded operation.
 assert.equal(typeof presentationCardKey(card),'string');assert.equal(card.body,'Аренда 65000 ₽ и комиссия 50%');
});

function canvasFixture(){
 const drawn=[],context=new Proxy({measureText:text=>({width:String(text).length*12}),fillText:text=>drawn.push(String(text))},{get:(value,key)=>key in value?value[key]:()=>{},set:(value,key,content)=>(value[key]=content,true)});
 const canvas={width:0,height:0,getContext:()=>context,toBlob:callback=>callback(new Blob(['png'],{type:'image/png'})),toDataURL:()=> 'data:image/png;base64,cG5n'};
 return {drawn,canvas};
}

test('history HTML/PNG are derived safe materials and raw restoration history retains commission evidence',async()=>{
 const card=rawCard(),entry=historyEntry('save',{title:card.titles[0],body:card.body,tags:card.tags,fields:card.fields}),before=structuredClone(entry);
 const html=historyHtml(entry);assert(!containsClientCommission(html));assert.match(html,/35000/);assert.doesNotMatch(html,/sourceAudit|Raw source/);
 const {drawn,canvas}=canvasFixture(),prior=globalThis.document;globalThis.document={createElement:()=>canvas};
 try{const image=await publicationPng(entry);assert.equal(image.type,'image/png');assert(!containsClientCommission(drawn.join('\n')));assert.match(drawn.join('\n'),/35000/);}
 finally{globalThis.document=prior;}
 assert.deepEqual(entry,before);
});

test('canvas uses the same checked custom nodes as HTML',async()=>{
 const card=rawCard(),design=defaultPublicationDesign();design.surfaces.intro.nodes.push({...design.surfaces.intro.nodes.find(n=>n.type==='text'),id:'custom',bind:'',text:'免中介费'});
 const {drawn,canvas}=canvasFixture();await renderPublicationPage(canvas,'intro',design,card,{});
 assert(!containsClientCommission(drawn.join('\n')));assert.match(drawn.join('\n'),/6.5|65,000/);
});


test('old client body with a disputed deposit stays blocked at render/snapshot boundaries without altering evidence',async()=>{
 const card=rawCard();card.body='租金65000 ₽\n押金35000 ₽';card.fields.deposit='';card.fields.sourceAudit.blockedFields=['deposit'];
 const before=structuredClone(card);
 assert.throws(()=>presentationCardData(card),error=>error instanceof ClientPublicationReviewRequired&&error.fragment.includes('35000'));
 assert.throws(()=>buildPublicationHtml(card),ClientPublicationReviewRequired);
 const entry=historyEntry('copy-all',{title:card.titles[0],body:card.body,tags:card.tags,fields:card.fields});
 assert.throws(()=>historyHtml(entry),ClientPublicationReviewRequired);await assert.rejects(publicationPng(entry),ClientPublicationReviewRequired);
 assert.deepEqual(card,before);assert.equal(entry.fields.sourceAudit.blockedFields[0],'deposit');
});
