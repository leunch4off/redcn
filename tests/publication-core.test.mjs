import test from 'node:test';
import assert from 'node:assert/strict';
import {defaultPublicationDesign,normalizePublicationDesign,resolvePublicationData,publicationNodeText,PUBLICATION_SURFACES} from '../web/publication-model.mjs';
import {buildPublicationHtml,publicationDocumentPages,safePublicationImage,publicationWrapText,renderPublicationPage} from '../web/publication-render.mjs';

const publicationTestImage=n=>'data:image/png;base64,'+Buffer.from('fixture-'+n).toString('base64');
const publicationTestCard={titles:['明亮的莫斯科公寓'],body:'独立的中文房源说明。\n家具与家电以已确认的资料为准。',fields:{price:75000,area:40,rooms:1,metro:'Саларьево',walk:'15',floor:7,floors:12,features:['oven','desk'],universities:'РУДН | 40–55 | public\nМГУ | 45–60 | metro'}};

test('Ciliev defaults use the reference landscape PDF and separate navy portrait canvases',()=>{
  const d=defaultPublicationDesign();assert.equal(d.version,2);assert.equal(d.orientation,'landscape');assert.equal(d.brand.name,'ciliev');assert.equal(d.brand.agency,'房地产');assert.equal(d.theme.pdf.accent,'#b79665');assert.equal(d.theme.social.accent,'#ef4b69');
  assert.deepEqual(Object.keys(d.surfaces),PUBLICATION_SURFACES);assert.deepEqual([d.surfaces.pdfOverview.width,d.surfaces.pdfOverview.height],[1584,1120]);assert.deepEqual([d.surfaces.intro.width,d.surfaces.intro.height],[1080,1920]);
  assert.equal(d.surfaces.intro.nodes.find(n=>n.id==='hero-photo').h,957);assert.equal(d.surfaces.outro.nodes.find(n=>n.type==='map').h,666);
  assert.ok(!/[А-Яа-яЁё]/.test(JSON.stringify(d)));assert.ok(!JSON.stringify(d).includes('Малафеев'));assert.equal(d.brand.phone,'');
});

test('normalization is idempotent, bounded and excludes executable image or style data',()=>{
  const d=normalizePublicationDesign();assert.deepEqual(normalizePublicationDesign(d),d);
  const bad=JSON.parse(JSON.stringify(d));bad.brand.avatarAssetId='../../private';bad.surfaces.intro.nodes=[{id:'a'.repeat(80),type:'text',x:-1,y:99999,w:99999,h:99999,text:'<script>x</script>',bind:'unknown',style:{fontSize:9999,color:'red;url(x)',fontFamily:'evil',fontWeight:999,opacity:9}},{id:'a'.repeat(80),type:'iframe',style:{}},{id:'a'.repeat(74)+'-1-1',type:'text',style:{}}];
  const n=normalizePublicationDesign(bad),nodes=n.surfaces.intro.nodes;assert.equal(n.brand.avatarAssetId,'');assert.equal(nodes[0].x,0);assert.equal(nodes[0].w,1080);assert.equal(nodes[0].style.fontSize,200);assert.equal(nodes[0].style.opacity,1);assert.equal(nodes[0].style.color,'#ffffff');assert.equal(nodes[0].bind,'');assert.equal(nodes[1].type,'text');assert.equal(new Set(nodes.map(x=>x.id)).size,3);assert.ok(nodes.every(x=>x.id.length<=80));
});

test('v1 migration retains personal branding and colors while replacing the old system footer',()=>{
  const upgraded=normalizePublicationDesign({version:1,background:'#121212',accent:'#ccaa44',contact:'微信:ciliev',footer:'МОСТ · Подбор жилья в Москве',blocks:[{id:'facts',enabled:false}]});
  assert.equal(upgraded.brand.contact,'微信:ciliev');assert.match(upgraded.brand.footer,/ciliev/);assert.equal(upgraded.theme.pdf.background,'#121212');assert.ok(upgraded.surfaces.pdfOverview.nodes.filter(n=>n.id.startsWith('facts-row-')).every(n=>n.visible===false));
  assert.equal(normalizePublicationDesign({version:1,footer:'自定义公司'}).brand.footer,'自定义公司');
});

test('publication facts never infer appliances, permissions or room separation',()=>{
  const d=resolvePublicationData(publicationTestCard);assert.equal(d.priceLarge,'7.5万');assert.equal(d.layout,'一居室');assert.match(d.features,/烤箱/);assert.match(d.features,/书桌/);assert.doesNotMatch(d.features,/冰箱|微波炉|宠物|孩子|齐全/);assert.doesNotMatch(d.introFacts,/独立卧室|一室一厅/);assert.match(d.service,/抵达登记（临时居住登记）/);assert.doesNotMatch(d.service,/免费|落地签/);
});

test('university route times and methods use only supplied facts and custom names win',()=>{
  const d=resolvePublicationData(publicationTestCard,{}, {rules:{РУДН:'我选择的大学'}});assert.equal(d.route1.title,'我选择的大学');assert.equal(d.route1.time,'约40–55分钟');assert.equal(d.route1.stops[1],'公共交通');
  const unknown=resolvePublicationData({fields:{university:'МГУ'}});assert.equal(unknown.route1.time,'通勤时间待确认');assert.equal(unknown.route1.stops[1],'出行方式待确认');assert.doesNotMatch(unknown.route1.stops.join(' '),/步行|15/);
});

test('coordinate validation preserves zero and never renders a supplied map for unknown coordinates',()=>{
  const invalid={...publicationTestCard,fields:{...publicationTestCard.fields,latitude:91,longitude:37}};const assets={mapImage:publicationTestImage('map')};const html=buildPublicationHtml(invalid,{},assets);assert.ok(!html.includes(assets.mapImage));assert.match(html,/位置待确认/);
  const zero=resolvePublicationData({fields:{latitude:0,longitude:0}});assert.equal(zero.latitude,0);assert.match(zero.mapUrl,/mlat=0&mlon=0/);
});

test('static HTML escapes text and CSS attribute font quotes, and rejects active image URLs',()=>{
  const design=defaultPublicationDesign();design.brand.agentName='"><script>alert(1)</script>';const html=buildPublicationHtml({...publicationTestCard,body:'<img src=x onerror=alert(1)>'},design,{photos:[publicationTestImage(1),'https://evil/photo.png','data:image/svg+xml;base64,PHN2Zz4=']});
  assert.ok(html.includes('&lt;script&gt;'));assert.ok(html.includes('&lt;img src=x onerror=alert(1)&gt;'));assert.ok(!html.includes('<script>'));assert.ok(!html.includes('src="https://'));assert.ok(!html.includes('data:image/svg'));assert.ok(html.includes('&quot;PingFang SC&quot;'));assert.match(html,/<html lang="zh-CN">/);assert.match(html,/@page\{size:A4 landscape;margin:0\}/);
  assert.equal(safePublicationImage('file:///private/x'),'');assert.equal(safePublicationImage('data:image/svg+xml;base64,PHN2Zz4='),'');
});

test('long Chinese descriptions continue in a clear large text page with every character preserved',()=>{
  const body='这是完整的中文说明，所有内容均应出现在最终文件中。'.repeat(120)+'\n最后一行必须保留。';const document=publicationDocumentPages({body});const texts=document.pages.filter(p=>p.surface.id==='pdfOverview').map(p=>p.data.body);assert.equal(texts.join(''),body);assert.ok(texts.length>1);const continuation=document.pages[1].surface.nodes.find(n=>n.bind==='body');assert.equal(continuation.w,1432);assert.equal(continuation.h,740);assert.ok(continuation.style.fontSize>=20);
});

test('all supplied property images paginate through the editable five-frame gallery',()=>{
  const photos=Array.from({length:20},(_,i)=>publicationTestImage(i));const doc=publicationDocumentPages(publicationTestCard,{}, {photos});const gallery=doc.pages.filter(p=>p.surface.id==='pdfGallery');assert.equal(gallery.length,4);assert.deepEqual(gallery.flatMap(p=>p.photos),photos);
  const html=buildPublicationHtml(publicationTestCard,{}, {photos:photos.slice(0,9)});for(const image of photos.slice(0,9))assert.ok(html.includes(image));assert.equal((html.match(/data-node-id="gallery-photo-5"/g)||[]).length,1);
});

test('fixed gallery bindings above five keep the same source on every exported page',()=>{
  const photos=Array.from({length:20},(_,i)=>publicationTestImage(i)),design=defaultPublicationDesign(),sample=design.surfaces.pdfGallery.nodes.find(n=>n.type==='image');
  design.surfaces.pdfGallery.nodes.push({...structuredClone(sample),id:'fixed-photo-20',bind:'photo20',photoIndex:0},{...structuredClone(sample),id:'fixed-index-19',bind:'',photoIndex:19});
  const html=buildPublicationHtml(publicationTestCard,design,{photos}),galleries=html.match(/<section[^>]*>[\s\S]*?<\/section>/g).filter(section=>section.includes('data-node-id="gallery-photo-1"'));
  assert.equal(galleries.length,4);
  for(const section of galleries){for(const id of ['fixed-photo-20','fixed-index-19']){const nodeMarkup=section.split('data-node-id="'+id+'"')[1]?.split('</div>')[0]||'';assert.ok(nodeMarkup.includes('src="'+photos[19]+'"'),id+' must resolve photo20 on every gallery page');}}
  const lastHtml=buildPublicationHtml(publicationTestCard,design,{photos:photos.slice(0,9)}),lastGallery=lastHtml.match(/<section[^>]*>[\s\S]*?<\/section>/g).filter(section=>section.includes('data-node-id="gallery-photo-1"')).at(-1);
  assert.ok(lastGallery.includes('data-node-id="gallery-photo-4"'));assert.ok(!lastGallery.includes('data-node-id="gallery-photo-5"'));assert.ok(!lastGallery.includes('data-node-id="fixed-photo-20"'));
});

test('tight multiline emoji titles reserve ink height while single-line prices keep their font size',()=>{
  const design=defaultPublicationDesign(),title=design.surfaces.pdfOverview.nodes.find(n=>n.id==='title');assert.equal(title.h,122);
  design.surfaces.pdfOverview.nodes.push({...structuredClone(title),id:'single-line-price',bind:'priceLarge',x:0,y:800,w:500,h:154,style:{...title.style,fontSize:116,lineHeight:1.1}});
  const html=buildPublicationHtml({...publicationTestCard,titles:['🔥莫斯科6.8W独立一居｜41㎡｜拉斯卡佐夫卡 步行14分钟']},design),fittedSize=id=>{const markup=html.split('data-node-id="'+id+'"')[1].split('>')[0];return [...markup.matchAll(/font-size:([\d.]+)cqw/g)].at(-1)[1]*1584/100;};
  assert.ok(Math.abs(fittedSize('title')-47)<1e-8,'The title must reserve height beyond its two tight line boxes');assert.ok(Math.abs(fittedSize('single-line-price')-116)<1e-8,'A single price line keeps its configured size');
});

test('a saved text binding uses each new card while deliberate manual text is retained',()=>{
  const design=defaultPublicationDesign(),node=design.surfaces.intro.nodes.find(n=>n.bind==='priceLarge');node.x=100;node.style.color='#ccaa66';const saved=normalizePublicationDesign(design);assert.equal(saved.surfaces.intro.nodes.find(n=>n.id===node.id).x,100);
  assert.equal(publicationNodeText(node,resolvePublicationData({fields:{price:80000}},saved)),'8万');assert.equal(publicationNodeText(node,resolvePublicationData({fields:{price:95000}},saved)),'9.5万');
  assert.equal(publicationNodeText({...node,bind:'',text:'自定义 {{area}}'},resolvePublicationData({fields:{area:54}},saved)),'自定义 54㎡');
});

test('default fact values and every route label are independent editable text nodes and absent facts hide cleanly',()=>{
  const d=defaultPublicationDesign(),overview=d.surfaces.pdfOverview,outro=d.surfaces.outro;
  assert.ok(overview.nodes.some(n=>n.type==='text'&&n.bind==='fact4Label'));assert.ok(overview.nodes.some(n=>n.type==='text'&&n.bind==='fact4Value'));
  for(const binding of ['route1Title','route1Time','route1Stop1','route1Stop2','route1Stop3'])assert.ok(outro.nodes.some(n=>n.type==='text'&&n.bind===binding));
  assert.ok(!outro.nodes.some(n=>n.type==='route'));assert.ok(!overview.nodes.some(n=>n.type==='facts'));
  const empty=buildPublicationHtml({fields:{address:'Москва'}});assert.ok(!empty.includes('data-node-id="route-1-title"'));assert.ok(!empty.includes('data-node-id="facts-row-4-label"'));assert.ok(!empty.includes('data-node-id="facts-row-6-rule"'));
  const before=structuredClone(d),title=outro.nodes.find(n=>n.bind==='route1Title'),time=outro.nodes.find(n=>n.bind==='route1Time');title.x+=20;assert.deepEqual(time,before.surfaces.outro.nodes.find(n=>n.id===time.id));assert.ok(Object.values(d.surfaces).every(s=>s.nodes.length<80));
});

test('canvas render uses independent fact rows and no network request for missing asset images',async()=>{
  const calls=[],ctx={fillStyle:'',font:'',textBaseline:'',textAlign:'',globalAlpha:1,save(){},restore(){},translate(x,y){calls.push(['translate',x,y]);},fillRect(){},strokeRect(){},beginPath(){},closePath(){},arc(){},arcTo(){},roundRect(){},fill(){},stroke(){},moveTo(){},lineTo(){},clip(){},drawImage(){},fillText(text){calls.push(['text',text]);},measureText(t){return{width:Array.from(t).length*12};}};const canvas={getContext:()=>ctx};
  const result=await renderPublicationPage(canvas,'pdfOverview',{},publicationTestCard,{});assert.equal(canvas.width,1584);assert.equal(canvas.height,1120);assert.ok(calls.filter(x=>x[0]==='translate'&&x[2]>5).length>5);assert.ok(calls.some(x=>x[0]==='text'&&x[1]==='月租金'));assert.ok(Array.isArray(result.warnings));
});
