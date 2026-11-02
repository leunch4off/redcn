import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {defaultPublicationDesign,resolvePublicationData,clearPublicationDataCache,publicationSurfaceLayout} from '../web/publication-model.mjs';
import {publicationDocumentPages,buildPublicationHtml,renderPublicationPage} from '../web/publication-render.mjs';
import {presentationAcceptedCrop} from '../web/photo-ui.mjs';
import {compressPresentationPhotos,presentationCardKey} from '../web/presentation-studio.mjs';

const compactCard={fields:{price:80000,address:'Москва',latitude:55.75,longitude:37.61}};
const compactRouteCards=[compactCard,{fields:{...compactCard.fields,universities:'МГУ | 40 | public'}},{fields:{...compactCard.fields,universities:'МГУ | 40 | public\nРУДН | 50 | bus'}}];
const compactNode=(surface,id)=>surface.nodes.find(node=>node.id===id);

test('zero confirmed routes removes default route zone and expands only the automatic map view',()=>{
 const design=defaultPublicationDesign(),before=structuredClone(design),data=resolvePublicationData(compactCard,design);
 const pdf=publicationSurfaceLayout(design,'pdfLocation',data),outro=publicationSurfaceLayout(design,'outro',data);
 assert.equal(compactNode(pdf,'map').w,1432);assert.equal(compactNode(pdf,'map').h,565);
 assert.equal(compactNode(outro,'outro-map').h,1268);assert.equal(compactNode(outro,'outro-attribution').y,1486);
 assert.equal(compactNode(outro,'routes-title').visible,false);assert.equal(compactNode(outro,'routes-subtitle').visible,false);
 assert(outro.nodes.filter(node=>/^route[12]/.test(node.bind)).every(node=>node.visible===false));
 assert.deepEqual(design,before,'saved positions never change during automatic compaction');
 const unknown=resolvePublicationData({fields:{university:'МГУ'}},design);assert.equal(unknown.route1.confirmedTime,false);
 assert.equal(compactNode(publicationSurfaceLayout(design,'outro',unknown),'outro-map').h,1268);
});

test('one and multiple confirmed routes keep their original geometry and only unconfirmed default cards hide',()=>{
 const design=defaultPublicationDesign();
 for(const card of compactRouteCards.slice(1)){
  const data=resolvePublicationData(card,design),surface=publicationSurfaceLayout(design,'outro',data);
  assert.equal(compactNode(surface,'outro-map').h,666);
  assert.deepEqual(compactNode(surface,'outro-route-1-title'),compactNode(design.surfaces.outro,'outro-route-1-title'));
  assert.equal(compactNode(surface,'outro-route-2-title').visible,data.route2?.confirmedTime===true);
 }
});

test('explicitly moved map attribution route labels and custom content preserve operator geometry',()=>{
 for(const id of ['outro-map','outro-attribution','outro-route-1-title']){
  const design=defaultPublicationDesign(),node=compactNode(design.surfaces.outro,id);node.x+=3;
  const result=publicationSurfaceLayout(design,'outro',resolvePublicationData(compactCard,design));
  assert.equal(compactNode(result,id).x,node.x);assert.equal(compactNode(result,'outro-map').h,666,id);
  if(id==='outro-route-1-title')assert.equal(compactNode(result,id).visible,true);
 }
 const design=defaultPublicationDesign(),node=structuredClone(compactNode(design.surfaces.outro,'outro-route-1-title'));
 Object.assign(node,{id:'operator-note',bind:'',text:'自定义提示',x:70,y:1100});design.surfaces.outro.nodes.push(node);
 assert.equal(compactNode(publicationSurfaceLayout(design,'outro',resolvePublicationData(compactCard,design)),'outro-map').h,666);
 const custom=defaultPublicationDesign();custom.surfaces.outro.width=1200;
 assert.equal(compactNode(publicationSurfaceLayout(custom,'outro',{}),'outro-map').h,666);
});

test('canvas PNG render and HTML/PDF page model consume the same resulting geometry for zero one multiple routes',async()=>{
 const ctx={save(){},restore(){},translate(){},fillRect(){},strokeRect(){},beginPath(){},closePath(){},arc(){},arcTo(){},roundRect(){},fill(){},stroke(){},moveTo(){},lineTo(){},clip(){},drawImage(){},fillText(){},measureText(t){return {width:Array.from(t).length*12};}};
 for(const card of compactRouteCards){
  const design=defaultPublicationDesign(),data=resolvePublicationData(card,design),expected=publicationSurfaceLayout(design,'pdfLocation',data);
  const result=await renderPublicationPage({getContext:()=>ctx},'pdfLocation',design,card,{});
  const document=publicationDocumentPages(card,design,{}),page=document.pages.find(page=>page.surface.id==='pdfLocation');
  assert.deepEqual(result.surface,expected);assert.deepEqual(page.surface,expected);
  const html=buildPublicationHtml(card,design,{}),map=compactNode(expected,'map');
  const markup=html.split('data-node-id="map"')[1].split('>')[0];
  assert(markup.includes('width:'+map.w/expected.width*100+'%'));assert(markup.includes('height:'+map.h/expected.height*100+'%'));
 }
});

test('presentation chooses only an associated manual or positively verified OCR derivative',()=>{
 const originalId='a'.repeat(64),id='b'.repeat(64),selected={id,kind:'crop',originalId,verification:'manual'};
 assert.equal(presentationAcceptedCrop({original:{id:originalId},selected,reason:'manual'},originalId),selected);
 const ocr={...selected,verification:'ocr-checked',verificationEvidence:{knownRegionsExcluded:true,postOcr:'not-detected'}};
 assert.equal(presentationAcceptedCrop({original:{id:originalId},selected:ocr,reason:'ocr-checked'},originalId),ocr);
 for(const value of [{original:{id:originalId},selected:ocr,reason:'review'},{original:{id:originalId},selected:{...ocr,verificationEvidence:{postOcr:'not-detected'}},reason:'ocr-checked'},{original:{id:originalId},selected:{...selected,originalId:'c'.repeat(64)},reason:'manual'},{original:{id:originalId},selected:{...ocr,verificationEvidence:{knownRegionsExcluded:true,postOcr:'mark-detected'}},reason:'ocr-checked'}])assert.equal(presentationAcceptedCrop(value,originalId),null);
});

test('photo preparation preserves order with a maximum of three decodes and stops stale work',async()=>{
 let running=0,peak=0;const input=Array.from({length:12},(_,i)=>i);
 const results=await compressPresentationPhotos(input,async value=>{running++;peak=Math.max(peak,running);await new Promise(resolve=>setTimeout(resolve,(value%3)*2));running--;return 'image-'+value;});
 assert.deepEqual(results,input.map(value=>'image-'+value));assert.equal(peak,3);
 let valid=true,started=0;await compressPresentationPhotos(input,async value=>{started++;valid=false;return 'image-'+value;},()=>valid);assert.equal(started,1);
 assert.notEqual(presentationCardKey({fields:{photoEdits:{originals:['https://example.test/image.jpg']}}}),presentationCardKey({fields:{photoEdits:{originals:[]}}}));
 assert.notEqual(presentationCardKey({fields:{universityRoutes:[{university:'МГУ',time:'40',mode:'public'}]}}),presentationCardKey({fields:{universityRoutes:[{university:'МГУ',time:'50',mode:'public'}]}}));
});

test('concurrent cache hydration waits for the same accepted derivative decision',async()=>{
 const source=await readFile(new URL('../web/photo-ui.mjs',import.meta.url),'utf8');
 const body=source.slice(source.indexOf(' function reconcileObservationOriginal('),source.indexOf(' async function ensure('));
 let release,calls=0;const originalId='a'.repeat(64),item={url:'https://images.cdn-cian.ru/images/fixture.jpg',serverId:originalId,blob:{name:'original'},cropBlob:{name:'cached-manual'},cropMeta:{verification:'manual'},manualCropOverride:false};
 const requested=[],binaries=[],mutations=[];
 const accepted=new Function('api','itemSource','presentationAcceptedCrop','binary','dimensions','cachePut','changed','emitFeatures','const local=true;'+body+';return acceptedCrop;')(async(path,data)=>{calls++;requested.push({path,...data});return new Promise(resolve=>{release=resolve;});},()=>null,presentationAcceptedCrop,async id=>{binaries.push(id);return {name:'verified-crop'};},async()=>({width:560,height:560}),async()=>{},()=>mutations.push('observations'),()=>mutations.push('facts'));
 const first=accepted(item);let secondDone=false;const second=accepted(item).then(()=>{secondDone=true;});
 await new Promise(resolve=>setImmediate(resolve));assert.equal(secondDone,false,'a concurrent publisher must wait for the pending crop decision');
 release({original:{id:originalId},selected:{id:'b'.repeat(64),originalId,kind:'crop',verification:'ocr-checked',verificationEvidence:{knownRegionsExcluded:true,postOcr:'mark-detected'}},reason:'ocr-checked'});
 await Promise.all([first,second]);assert.equal(calls,1);assert.equal(item.cropBlob,null);assert.equal(item.cropMeta,null,'uncertain OCR clears the cached crop for this card');
 assert.deepEqual(requested,[{path:'photo',action:'accepted',url:item.url}]);assert.deepEqual(binaries,[],'an unverified derivative is not downloaded');
 assert.deepEqual(mutations,[],'same original and uncertain crop do not change photo observations');
});

test('an original blob becoming available does not finish a pending saved manual crop',async()=>{
 const source=await readFile(new URL('../web/photo-ui.mjs',import.meta.url),'utf8');
 const body=source.slice(source.indexOf(' function reconcileObservationOriginal('),source.indexOf(' async function acceptedCrop('))+source.slice(source.indexOf(' async function ensure('),source.indexOf(' async function loadAll('));
 let release,entered,binaries=0,selections=0;const originalId='a'.repeat(64),requests=[],cropEntered=new Promise(resolve=>{entered=resolve;}),item={url:'https://images.cdn-cian.ru/images/fixture.jpg',expectedMargins:{bottom:20}};
 const apply=async value=>{entered();await new Promise(resolve=>{release=resolve;});value.cropBlob={name:'manual'};value.cropMeta={verification:'manual',margins:{bottom:20}};};
 const ensure=new Function('cacheGet','api','itemSource','binary','dimensions','cachePut','clientCrop','applyClientCrop','acceptedCrop','status','changed','emitFeatures','const local=true,hosted=false;'+body+';return ensure;')(async()=>null,async(path,data)=>{requests.push({path,...data});assert.equal(path,'photo');assert.equal(data.action,'accepted');return {original:{id:originalId},selected:{id:originalId,kind:'original'},reason:'original'};},()=>null,async id=>{assert.equal(id,originalId);binaries++;return {type:'image/png'};},async()=>({width:600,height:600}),async()=>{},apply,apply,async(value,choice)=>{selections++;assert.equal(value.serverId,originalId);assert.equal(choice.original.id,originalId);},()=>{},()=>{},()=>{});
 const first=ensure(item);await cropEntered;let secondDone=false;const second=ensure(item).then(()=>{secondDone=true;});
 await new Promise(resolve=>setImmediate(resolve));assert.equal(secondDone,false,'manual crop preparation is part of the original preparation');
 release();await Promise.all([first,second]);assert.equal(binaries,1);assert.equal(item.cropMeta.verification,'manual');
 assert.equal(item.serverId,originalId);assert.equal(selections,1);assert.deepEqual(requests,[{path:'photo',action:'accepted',url:item.url}]);
});

test('publication data cache avoids repeated design normalization and invalidates every content dependency',()=>{
 clearPublicationDataCache();let surfaceReads=0;const design=defaultPublicationDesign(),input=new Proxy(design,{get(value,key){if(key==='surfaces')surfaceReads++;return value[key];}}),card={fields:{price:80000,metro:'Тестовая станция'},body:'正文'},rules={'Тестовая станция':'测试站'};
 const first=resolvePublicationData(card,input,{rules});assert.equal(first.metro,'测试站');const reads=surfaceReads;assert(reads>0);
 first.body='caller mutation';assert.equal(resolvePublicationData(card,input,{rules}).body,'正文');assert.equal(surfaceReads,reads,'identical data must reuse the normalized translation result');
 design.surfaces.outro.nodes[0].x+=1;resolvePublicationData(card,input,{rules});assert.equal(surfaceReads,reads,'moving a block does not change translated card data');
 rules['Тестовая станция']='新的站名';assert.equal(resolvePublicationData(card,input,{rules}).metro,'新的站名');
 card.fields.price=90000;assert.equal(resolvePublicationData(card,input,{rules}).priceLarge,'9万');
 design.brand.agentName='新顾问';assert.equal(resolvePublicationData(card,input,{rules}).agentName,'新顾问');
 card.fields.universityRoutes=[{name:'МГУ',time:'40',mode:'bus',method:'manual-confirmed',checkedAt:'2026-11-01'}];
 const originalNow=Date.now;try{Date.now=()=>Date.parse('2026-11-01T10:00:00Z');assert.equal(resolvePublicationData(card,input,{rules}).route1.verification.stale,false);Date.now=()=>Date.parse('2026-12-03T10:00:00Z');assert.equal(resolvePublicationData(card,input,{rules}).route1.verification.stale,true);}finally{Date.now=originalNow;clearPublicationDataCache();}
});
