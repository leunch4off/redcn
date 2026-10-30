import {normalizePublicationDesign,resolvePublicationData,publicationNodeText,PUBLICATION_FONT_STACKS} from './publication-model.mjs';

const publicationEscape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const publicationImageValue=value=>typeof value==='string'?value:value?.dataUrl||'';
export function safePublicationImage(value){const text=publicationImageValue(value);return typeof text==='string'&&/^data:image\/(?:png|jpeg|webp);base64,[a-z0-9+/=\s]+$/i.test(text)&&text.length<=24_000_000?text.replace(/\s/g,''):'';}
const publicationPhotoList=assets=>(Array.isArray(assets?.photos)?assets.photos:[]).map(safePublicationImage).filter(Boolean).slice(0,30);
const publicationImageCache=new Map();
export function clearPublicationImageCache(){publicationImageCache.clear();}
function publicationLoadImage(source){
  if(!source||typeof Image==='undefined')return Promise.resolve(null);
  if(publicationImageCache.has(source))return publicationImageCache.get(source);
  const pending=new Promise(resolve=>{const image=new Image();let settled=false;const finish=value=>{if(settled)return;settled=true;clearTimeout(timer);resolve(value);};const timer=setTimeout(()=>finish(null),10000);image.onload=()=>finish(image.naturalWidth>0?image:null);image.onerror=()=>finish(null);image.src=source;});
  publicationImageCache.set(source,pending);while(publicationImageCache.size>42)publicationImageCache.delete(publicationImageCache.keys().next().value);return pending;
}

function publicationMeasureWidth(text,fontSize){let width=0;for(const c of text)width+=fontSize*(/\s/.test(c)?.3:/[\u0000-\u024f]/.test(c)?.56:1);return width;}
export function publicationWrapText(text,width,fontSize,measure=null){
  const evaluate=measure||((value)=>publicationMeasureWidth(value,fontSize));const lines=[];
  for(const paragraph of String(text??'').replace(/\r/g,'').split('\n')){
    if(!paragraph){lines.push('');continue;}
    let line='';for(const char of Array.from(paragraph)){if(line&&evaluate(line+char)>Math.max(1,width)){lines.push(line);line=char;}else line+=char;}lines.push(line);
  }
  return lines;
}
function publicationFit(text,node){let size=node.style.fontSize;const min=Math.min(size,Math.max(12,size*.58)),height=(lines,font)=>lines.length*font*node.style.lineHeight+(lines.length>1&&node.style.lineHeight<1.3?font*.25:0);while(size>min){const lines=publicationWrapText(text,node.w,size);if(height(lines,size)<=node.h+1)break;size-=1;}const lines=publicationWrapText(text,node.w,size);return {size,lines,overflow:height(lines,size)>node.h+2};}
function publicationNodeImage(node,data,assets,photos){
  if(node.type==='map')return data.latitude!==null&&data.longitude!==null?safePublicationImage(assets.mapImage||assets.map):'';
  if(node.type==='avatar')return safePublicationImage(assets.avatar);
  if(node.type==='logo')return safePublicationImage(assets.logo);
  const match=/^photo(\d+)$/.exec(node.bind||'');const index=match?Number(match[1])-1:node.photoIndex||0;
  // Gallery slots 1–5 flow with each page; explicit selections 6–30 stay
  // attached to the same original photo in both canvas preview and export.
  return (index<5?photos:publicationPhotoList(assets))[index]||'';
}
function publicationBoundEmpty(node,data){const route=/^route([12])(?:Title|Time|Stops|Stop[123])?$/.exec(node.bind||'');if(route&&!data['route'+route[1]])return true;if(/^fact[1-6](?:Label|Value)$/.test(node.bind||'')&&!data[node.bind])return true;return node.type==='route'&&!data[node.bind||'route1'];}
function publicationSurfaceNodes(surface){return surface.nodes.filter(row=>row.visible).map((node,index)=>({...node,_order:index})).sort((a,b)=>a.z-b.z||a._order-b._order);}
function publicationPageData(card,design,assets,surfaceId){const data=resolvePublicationData(card,design,assets);if(surfaceId==='pdfOverview'){const body=design.surfaces.pdfOverview.nodes.find(node=>node.bind==='body'&&node.visible);if(body)data.body=publicationDescriptionSlice(data.body,body)[0];}return data;}
function publicationDescriptionSlice(text,node){
  const chars=Array.from(String(text??'').replace(/\r/g,'')),capacity=Math.max(1,Math.floor(node.h/(node.style.fontSize*node.style.lineHeight)));let line='',lines=1,at=0;
  for(;at<chars.length;at++){
    const c=chars[at],nextLine=c==='\n'||line&&publicationMeasureWidth(line+c,node.style.fontSize)>node.w;
    if(nextLine&&lines>=capacity){if(at===0)at=1;break;}
    if(c==='\n'){lines++;line='';}else if(nextLine){lines++;line=c;}else line+=c;
  }
  return [chars.slice(0,at).join(''),chars.slice(at).join('')];
}
const publicationRounding=(ctx,x,y,w,h,r)=>{const radius=Math.max(0,Math.min(r,w/2,h/2));ctx.beginPath();if(ctx.roundRect)ctx.roundRect(x,y,w,h,radius);else{ctx.moveTo(x+radius,y);ctx.arcTo(x+w,y,x+w,y+h,radius);ctx.arcTo(x+w,y+h,x,y+h,radius);ctx.arcTo(x,y+h,x,y,radius);ctx.arcTo(x,y,x+w,y,radius);}ctx.closePath();};
function publicationCanvasText(ctx,text,node,warnings){
  const fit=publicationFit(text,node);ctx.fillStyle=node.style.color;ctx.font=node.style.fontWeight+' '+fit.size+'px '+PUBLICATION_FONT_STACKS[node.style.fontFamily];ctx.textBaseline='top';ctx.textAlign=node.style.align;
  const lines=publicationWrapText(text,node.w,fit.size,value=>ctx.measureText(value).width);let size=fit.size;
  while(lines.length*size*node.style.lineHeight>node.h+2&&size>12){size-=1;ctx.font=node.style.fontWeight+' '+size+'px '+PUBLICATION_FONT_STACKS[node.style.fontFamily];lines.splice(0,lines.length,...publicationWrapText(text,node.w,size,value=>ctx.measureText(value).width));}
  if(lines.length*size*node.style.lineHeight>node.h+2)warnings.push({nodeId:node.id,code:'text-overflow',message:'文本超出文本框，请增大高度或调整字号。'});
  const x=node.style.align==='center'?node.w/2:node.style.align==='right'?node.w:0;
  lines.forEach((line,index)=>{if((index+1)*size*node.style.lineHeight<=node.h+size*.1)ctx.fillText(line,x,index*size*node.style.lineHeight);});
}
function publicationDrawPlaceholder(ctx,node,kind){ctx.fillStyle=node.style.background;ctx.fillRect(0,0,node.w,node.h);ctx.strokeStyle=node.style.borderColor;ctx.lineWidth=Math.max(1,node.style.borderWidth);ctx.strokeRect(0,0,node.w,node.h);ctx.fillStyle='#9daac0';ctx.font='500 '+Math.max(18,Math.min(32,node.w/15))+'px '+PUBLICATION_FONT_STACKS.sans;ctx.textAlign='center';ctx.textBaseline='middle';const message=kind==='map'?'位置待确认':kind==='image'?'房源照片':'';if(message)ctx.fillText(message,node.w/2,node.h/2);}
function publicationDrawMonogram(ctx,node,avatar=false){const radius=Math.min(node.w,node.h)/2;ctx.fillStyle=node.style.background;ctx.beginPath();ctx.arc(node.w/2,node.h/2,radius,0,Math.PI*2);ctx.fill();ctx.strokeStyle=node.style.borderColor;ctx.lineWidth=Math.max(2,node.style.borderWidth);ctx.stroke();ctx.fillStyle=node.style.color;ctx.font='500 '+Math.min(node.w,node.h)*.52+'px '+PUBLICATION_FONT_STACKS.serif;ctx.textAlign='center';ctx.textBaseline='middle';ctx.fillText('C',node.w/2,node.h/2+radius*.05);if(!avatar){ctx.strokeStyle=node.style.color;ctx.lineWidth=Math.max(2,node.w*.016);ctx.strokeRect(node.w*.24,node.h*.22,node.w*.49,node.h*.56);ctx.beginPath();ctx.moveTo(node.w*.5,node.h*.25);ctx.lineTo(node.w*.5,node.h*.5);ctx.lineTo(node.w*.74,node.h*.5);ctx.stroke();}}
function publicationDrawFacts(ctx,node,data,warnings,theme){
  const rows=data.facts||[],height=node.h/Math.max(1,rows.length);rows.forEach(([label,value],index)=>{const y=index*height;ctx.strokeStyle=node.style.borderColor;ctx.lineWidth=Math.max(1,node.style.borderWidth);ctx.beginPath();ctx.moveTo(0,y+height-1);ctx.lineTo(node.w,y+height-1);ctx.stroke();ctx.save();ctx.translate(0,y+5);publicationCanvasText(ctx,label,{...node,id:node.id+'-label-'+index,w:node.w*.35,h:height-14,style:{...node.style,color:theme.muted,fontSize:Math.min(25,node.style.fontSize),fontWeight:400}},warnings);ctx.translate(node.w*.38,0);publicationCanvasText(ctx,value,{...node,id:node.id+'-value-'+index,w:node.w*.62,h:height-14,style:{...node.style,fontSize:node.style.fontSize}},warnings);ctx.restore();});
}
function publicationDrawRoute(ctx,node,route,theme,warnings){
  publicationRounding(ctx,0,0,node.w,node.h,node.style.radius);ctx.fillStyle=node.style.background;ctx.fill();ctx.strokeStyle=node.style.borderColor;ctx.lineWidth=node.style.borderWidth;ctx.stroke();
  const pad=node.w<700?24:25,small=node.w<700;ctx.save();ctx.translate(pad,pad);publicationCanvasText(ctx,route.title,{...node,id:node.id+'-title',w:node.w-pad*2-(small?0:238),h:small?72:56,style:{...node.style,fontSize:node.style.fontSize,fontWeight:700}},warnings);ctx.restore();
  if(route.time){ctx.save();ctx.translate(small?pad:node.w-258,small?84:25);publicationCanvasText(ctx,route.time,{...node,id:node.id+'-time',w:small?node.w-pad*2:233,h:42,style:{...node.style,fontSize:small?20:node.style.fontSize-2,align:small?'left':'right'}},warnings);ctx.restore();}
  const lineY=small?node.h-62:node.h*.56;ctx.strokeStyle=theme.accent;ctx.lineWidth=small?3:4;ctx.beginPath();ctx.moveTo(pad,lineY);ctx.lineTo(node.w-pad,lineY);ctx.stroke();
  route.stops.slice(0,3).forEach((stop,index)=>{const x=pad+index*(node.w-pad*2)/2;ctx.fillStyle=node.style.background;ctx.strokeStyle='#ffffff';ctx.lineWidth=2;ctx.beginPath();ctx.arc(x,lineY,small?5:6,0,Math.PI*2);ctx.fill();ctx.stroke();ctx.save();const sw=(node.w-pad*2)/3;ctx.translate(Math.max(pad,Math.min(node.w-pad-sw,x-sw/2)),lineY+18);publicationCanvasText(ctx,stop,{...node,id:node.id+'-stop-'+index,w:sw,h:node.h-lineY-24,style:{...node.style,fontSize:small?16:Math.max(19,node.style.fontSize-7),fontWeight:600,align:index===0?'left':index===2?'right':'center'}},warnings);ctx.restore();});
}

export async function renderPublicationPage(canvas,surfaceId,input,card={},assets={}){
  const design=normalizePublicationDesign(input),surface=design.surfaces[surfaceId]||design.surfaces.pdfOverview,data=publicationPageData(card,design,assets,surface.id),warnings=[...data.warnings.map(message=>({code:'translation-review',message}))],photos=publicationPhotoList(assets);
  canvas.width=surface.width;canvas.height=surface.height;const ctx=canvas.getContext('2d');if(!ctx)throw Error('Canvas 2D недоступен');ctx.fillStyle=surface.background;ctx.fillRect(0,0,canvas.width,canvas.height);
  for(const node of publicationSurfaceNodes(surface)){
    if(publicationBoundEmpty(node,data)||surface.id==='pdfGallery'&&node.type==='image'&&!publicationNodeImage(node,data,assets,photos))continue;ctx.save();ctx.translate(node.x,node.y);ctx.globalAlpha=node.style.opacity;
    if(node.type==='text'){if(node.style.borderWidth){publicationRounding(ctx,0,0,node.w,node.h,node.style.radius);ctx.lineWidth=node.style.borderWidth;ctx.strokeStyle=node.style.borderColor;ctx.stroke();}publicationCanvasText(ctx,publicationNodeText(node,data),node,warnings);}
    else if(node.type==='rect'||node.type==='line'){publicationRounding(ctx,0,0,node.w,node.h,node.style.radius);ctx.fillStyle=node.type==='line'?node.style.color:node.style.background;ctx.fill();if(node.style.borderWidth){ctx.strokeStyle=node.style.borderColor;ctx.lineWidth=node.style.borderWidth;ctx.stroke();}}
    else if(node.type==='facts')publicationDrawFacts(ctx,node,data,warnings,surface.id.startsWith('pdf')?design.theme.pdf:design.theme.social);
    else if(node.type==='route'){ctx.translate(25,25);ctx.translate(-25,-25);publicationDrawRoute(ctx,node,data[node.bind||'route1'],surface.id.startsWith('pdf')?design.theme.pdf:design.theme.social,warnings);}
    else {
      const source=publicationNodeImage(node,data,assets,photos),image=await publicationLoadImage(source);
      if(image){publicationRounding(ctx,0,0,node.w,node.h,node.style.radius);ctx.clip();const ratio=node.style.objectFit==='contain'?Math.min(node.w/image.width,node.h/image.height):Math.max(node.w/image.width,node.h/image.height);const w=image.width*ratio,h=image.height*ratio;ctx.drawImage(image,(node.w-w)/2,(node.h-h)/2,w,h);}
      else if(['logo','avatar'].includes(node.type))publicationDrawMonogram(ctx,node,node.type==='avatar');
      else {publicationDrawPlaceholder(ctx,node,node.type);if(source)warnings.push({nodeId:node.id,code:'image-unavailable',message:'图片未能解码，请检查源文件。'});}
    }
    ctx.restore();
  }
  return {warnings,data,width:surface.width,height:surface.height};
}

function publicationNodeCss(node,surface){const s=node.style,fit=publicationFit('',node);return 'left:'+node.x/surface.width*100+'%;top:'+node.y/surface.height*100+'%;width:'+node.w/surface.width*100+'%;height:'+node.h/surface.height*100+'%;opacity:'+s.opacity+';z-index:'+node.z+';border-radius:'+s.radius/surface.width*100+'cqw;color:'+s.color+';font-family:'+PUBLICATION_FONT_STACKS[s.fontFamily]+';font-weight:'+s.fontWeight+';text-align:'+s.align+';line-height:'+s.lineHeight+';font-size:'+fit.size/surface.width*100+'cqw;';}
function publicationHtmlBox(node,surface,text,x,y,w,h,style={}){const block={...node,x:0,y:0,w,h,style:{...node.style,...style}},fit=publicationFit(text,block);return '<div class="pub-inner-text" style="left:'+x/node.w*100+'%;top:'+y/node.h*100+'%;width:'+w/node.w*100+'%;height:'+h/node.h*100+'%;font-size:'+fit.size/surface.width*100+'cqw;color:'+block.style.color+';font-weight:'+block.style.fontWeight+';line-height:'+block.style.lineHeight+';text-align:'+block.style.align+'">'+publicationEscape(text)+'</div>';}
function publicationHtmlRoute(node,route,surface,theme){
  if(!route)return '';const small=node.w<700,pad=small?24:25,lineY=small?node.h-62:node.h*.56;
  let html=publicationHtmlBox(node,surface,route.title,pad,pad,node.w-pad*2-(small?0:238),small?72:56,{fontWeight:700});
  if(route.time)html+=publicationHtmlBox(node,surface,route.time,small?pad:node.w-258,small?84:25,small?node.w-pad*2:233,42,{fontSize:small?20:node.style.fontSize-2,align:small?'left':'right'});
  html+='<div style="position:absolute;left:'+pad/node.w*100+'%;top:'+lineY/node.h*100+'%;width:'+(node.w-pad*2)/node.w*100+'%;height:'+(small?3:4)/node.h*100+'%;background:'+theme.accent+'"></div>';
  route.stops.slice(0,3).forEach((stop,index)=>{const x=pad+index*(node.w-pad*2)/2,r=small?5:6,sw=(node.w-pad*2)/3;
    html+='<i style="position:absolute;left:'+(x-r)/node.w*100+'%;top:'+(lineY-r)/node.h*100+'%;width:'+r*2/node.w*100+'%;height:'+r*2/node.h*100+'%;border:'+2/surface.width*100+'cqw solid #fff;border-radius:50%;background:'+node.style.background+'"></i>';
    html+=publicationHtmlBox(node,surface,stop,Math.max(pad,Math.min(node.w-pad-sw,x-sw/2)),lineY+18,sw,node.h-lineY-24,{fontSize:small?16:Math.max(19,node.style.fontSize-7),fontWeight:600,align:index===0?'left':index===2?'right':'center'});
  });return html;
}
function publicationHtmlNode(node,surface,data,assets,photos,theme){
  if(publicationBoundEmpty(node,data)||surface.id==='pdfGallery'&&node.type==='image'&&!publicationNodeImage(node,data,assets,photos))return '';let content='',css=publicationNodeCss(node,surface),extra='';
  if(node.type==='text'){const text=publicationNodeText(node,data),fit=publicationFit(text,node);css+='font-size:'+fit.size/surface.width*100+'cqw;';content=publicationEscape(text);}
  else if(node.type==='rect'||node.type==='line')css+='background:'+(node.type==='line'?node.style.color:node.style.background)+';';
  else if(node.type==='facts'){const rows=data.facts||[],height=node.h/Math.max(1,rows.length);content=rows.map(([label,value],index)=>publicationHtmlBox(node,surface,label,0,index*height+5,node.w*.35,height-14,{color:theme.muted,fontSize:Math.min(25,node.style.fontSize),fontWeight:400})+publicationHtmlBox(node,surface,value,node.w*.38,index*height+5,node.w*.62,height-14)+'<div style="position:absolute;left:0;top:'+((index+1)*height-1)/node.h*100+'%;width:100%;height:1px;background:'+node.style.borderColor+'"></div>').join('');}
  else if(node.type==='route'){css+='background:'+node.style.background+';';content=publicationHtmlRoute(node,data[node.bind||'route1'],surface,theme);extra=' route-card'+(node.w<700?' route-small':'');}
  else{const source=publicationNodeImage(node,data,assets,photos);if(source)content='<img src="'+source+'" alt="'+(node.type==='map'?'房源位置':node.type==='avatar'?'房产顾问头像':node.type==='logo'?'ciliev':'房源照片')+'" style="object-fit:'+node.style.objectFit+'">';else if(node.type==='logo'||node.type==='avatar'){content='<div class="monogram '+(node.type==='logo'?'brand-monogram':'')+'" style="font-size:'+Math.min(node.w,node.h)*.52/surface.width*100+'cqw">C</div>';css+='background:'+node.style.background+';';}else{content='<span class="image-placeholder">'+(node.type==='map'?'位置待确认<br>请补充真实坐标':'房源照片')+'</span>';css+='background:'+node.style.background+';';}}
  if(node.style.borderWidth)css+='border:'+node.style.borderWidth/surface.width*100+'cqw solid '+node.style.borderColor+';';
  return '<div class="pub-node pub-'+node.type+extra+'" data-node-id="'+publicationEscape(node.id)+'" data-bind="'+publicationEscape(node.bind)+'" style="'+publicationEscape(css)+'">'+content+'</div>';
}

export function publicationDocumentPages(card={},input={},assets={}){
  const design=normalizePublicationDesign(input),data=resolvePublicationData(card,design,assets),photos=publicationPhotoList(assets),pages=[],overview=design.surfaces.pdfOverview,bodyNode=overview.nodes.find(n=>n.visible&&n.bind==='body');
  let remaining='';if(bodyNode){const chunks=publicationDescriptionSlice(data.body,bodyNode);data.body=chunks[0];remaining=chunks[1];}
  pages.push({surface:overview,data:{...data},photos});
  if(remaining.length&&bodyNode){
    const continuation=JSON.parse(JSON.stringify(overview));continuation.nodes=continuation.nodes.filter(n=>['agency','brand','tagline','title','footer','footer-rule'].includes(n.id)||n.bind==='body');const continuationBody=continuation.nodes.find(n=>n.bind==='body');Object.assign(continuationBody,{x:76,y:280,w:1432,h:740});const heading=continuation.nodes.find(n=>n.id==='title');if(heading){heading.bind='';heading.text='房源介绍';heading.h=100;}
    while(remaining){const chunks=publicationDescriptionSlice(remaining,continuationBody);pages.push({surface:continuation,data:{...data,body:chunks[0]},photos});remaining=chunks[1];if(pages.length>40)throw Error('Описание требует слишком много страниц. Уменьшите шрифт или увеличьте текстовый блок.');}
  }
  if(photos.length){for(let at=0;at<photos.length;at+=5)pages.push({surface:design.surfaces.pdfGallery,data:{...data},photos:photos.slice(at,at+5)});}
  if(data.address||data.latitude!==null||data.routes.length)pages.push({surface:design.surfaces.pdfLocation,data:{...data},photos});
  return {design,data,pages};
}

export function buildPublicationHtml(card={},input={},assets={}){
  const document=publicationDocumentPages(card,input,assets),design=document.design,esc=publicationEscape,paper=design.paper==='Letter'?{w:279.4,h:215.9,name:'Letter'}:{w:297,h:210,name:'A4'};
  const pages=document.pages.map(({surface,data,photos},index)=>'<section class="publication-page" aria-label="第'+(index+1)+'页" style="background:'+surface.background+';--page-ratio:'+surface.width+'/'+surface.height+'">'+publicationSurfaceNodes(surface).map(node=>publicationHtmlNode(node,surface,data,assets,photos,design.theme.pdf)).join('')+'<span class="page-number">'+String(index+1).padStart(2,'0')+' / '+String(document.pages.length).padStart(2,'0')+'</span></section>').join('');
  return '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+esc(document.data.title)+' · ciliev</title><style>@page{size:'+paper.name+' landscape;margin:0}*{box-sizing:border-box}html,body{margin:0;padding:0;background:'+design.theme.pdf.background+';color:'+design.theme.pdf.foreground+';font-family:'+PUBLICATION_FONT_STACKS.sans+';-webkit-print-color-adjust:exact;print-color-adjust:exact}.publication-page{position:relative;container-type:inline-size;width:'+paper.w+'mm;height:'+paper.h+'mm;margin:0;overflow:hidden;break-after:page;page-break-after:always}.publication-page:last-child{break-after:auto;page-break-after:auto}.pub-node{position:absolute;white-space:pre-wrap;overflow:hidden;overflow-wrap:anywhere}.pub-inner-text{position:absolute;white-space:pre-wrap;overflow:hidden;overflow-wrap:anywhere}.pub-node img{display:block;width:100%;height:100%}.image-placeholder{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:'+design.theme.pdf.muted+';font-size:2cqw;text-align:center}.monogram{display:flex;align-items:center;justify-content:center;width:100%;height:100%;border-radius:50%;font:500 8cqw Georgia,serif;color:#fff}.brand-monogram{font-size:8cqw}.facts-table{height:100%;display:flex;flex-direction:column}.fact-row{flex:1;display:grid;grid-template-columns:35% 65%;align-items:center;gap:1cqw;min-height:0;border-bottom:1px solid '+design.theme.pdf.line+';font-size:1.55cqw;line-height:1.35}.fact-row span{color:'+design.theme.pdf.muted+';font-weight:400}.fact-row strong{font-weight:600;font-size:1.45cqw;max-height:100%;overflow:hidden}.route-card{padding:1.6cqw}.route-heading{display:flex;justify-content:space-between;gap:1cqw;min-height:2.6cqw;font-size:1.8cqw;line-height:1.25}.route-heading strong{max-width:72%;font-weight:700}.route-heading span{white-space:nowrap;text-align:right;font-size:1.6cqw}.route-line{height:.25cqw;position:relative;display:flex;justify-content:space-between;margin:3cqw .5cqw 1.5cqw}.route-line i{display:block;width:.8cqw;height:.8cqw;border:1.5px solid #fff;border-radius:50%;background:'+design.theme.pdf.panel+';transform:translateY(-35%)}.route-stops{display:flex;justify-content:space-between;gap:1cqw;font-size:1.15cqw}.route-stops span{width:33.333%}.route-stops span:nth-child(2){text-align:center}.route-stops span:last-child{text-align:right}.route-small .route-heading{display:block;font-size:1.5cqw;min-height:5cqw}.route-small .route-heading strong{display:block;max-width:100%}.route-small .route-heading span{display:block;text-align:left;font-size:1.2cqw;margin-top:.5cqw}.route-small .route-line{margin-top:1.5cqw}.route-small .route-stops{font-size:.95cqw}.page-number{position:absolute;bottom:1.8%;right:4.8%;font-size:.9cqw;letter-spacing:.12em;color:'+design.theme.pdf.muted+'}@media screen{body{padding:20px;background:#030b16}.publication-page{width:min('+paper.w+'mm,100%);height:auto;aspect-ratio:'+paper.w+'/'+paper.h+';margin:0 auto 20px;box-shadow:0 10px 40px #0008}}@media print{body{padding:0}.publication-page{margin:0;box-shadow:none}}</style></head><body>'+pages+'</body></html>';
}
