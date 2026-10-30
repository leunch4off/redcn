import {translate,FEATURES,DICTIONARY} from './engine.mjs';
import {translateGeo,resolveGeo} from './geo-catalog.mjs';
import {resolveUniversity,resolveCampus} from './university-catalog.mjs';

// Publication-only address labels; provenance and editorial status are recorded
// in docs/CILIEV-DESIGN-REFERENCE.md. Keep historical source names intact.
const publicationAddressTerms={
  'НАО (Новомосковский)':'新莫斯科行政区',
  'Новомосковский административный округ':'新莫斯科行政区',
  'Внуковское поселение':'弗努科沃乡镇（原行政名称）',
  'поселение Внуковское':'弗努科沃乡镇（原行政名称）',
  'улица Бориса Пастернака':'鲍里斯·帕斯捷尔纳克街',
  'ул. Бориса Пастернака':'鲍里斯·帕斯捷尔纳克街'
};

// One logical-coordinate model drives the editor, raster cards and printed HTML.
export const PUBLICATION_SURFACES=['pdfOverview','pdfGallery','pdfLocation','intro','outro'];
export const PUBLICATION_BINDINGS=[
  ['title','Название'],['body','Описание'],['brandName','Название агентства'],['agency','Подпись агентства'],['tagline','Слоган'],['agentName','Имя консультанта'],['agentRole','Должность'],['contact','Контакты'],['phone','Телефон'],['email','Почта'],['footer','Подвал'],['metro','Метро'],['metroLine','Линия метро'],['walk','Время до метро'],['price','Аренда'],['priceLarge','Цена крупно'],['priceUnit','Единица цены'],['area','Площадь'],['layout','Планировка'],['floor','Этаж'],['address','Адрес'],['city','Город'],['features','Мебель и техника'],['service','Регистрация'],['introFacts','Краткие параметры'],['mapTitle','Расположение'],['mapMetro','Подпись метро'],['routesTitle','Заголовок маршрутов'],['route1Title','Вуз 1'],['route1Time','Время до вуза 1'],['route1Stops','Маршрут 1'],['route2Title','Вуз 2'],['route2Time','Время до вуза 2'],['route2Stops','Маршрут 2'],['disclaimer','Примечание к маршрутам'],['mapAttribution','Авторство карты'],['facts','Таблица параметров'],['route1','Первая поездка'],['route2','Вторая поездка'],['map','Карта'],['logo','Логотип'],['avatar','Аватар консультанта'],...Array.from({length:30},(_,i)=>['photo'+(i+1),'Фото '+(i+1)])
].concat(Array.from({length:6},(_,i)=>[['fact'+(i+1)+'Label','Параметр '+(i+1)+' · подпись'],['fact'+(i+1)+'Value','Параметр '+(i+1)+' · значение']]).flat(),Array.from({length:2},(_,r)=>Array.from({length:3},(_,s)=>['route'+(r+1)+'Stop'+(s+1),'Маршрут '+(r+1)+' · пункт '+(s+1)])).flat()).map(([id,label])=>({id,label}));
export const PUBLICATION_FONT_STACKS={sans:'"PingFang SC", "Noto Sans CJK SC", "Microsoft YaHei", Arial, sans-serif',serif:'"Songti SC", "Noto Serif CJK SC", Georgia, serif'};
const publicationBindingIds=new Set(PUBLICATION_BINDINGS.map(row=>row.id));
const publicationTypes=['text','image','rect','line','logo','avatar','map','route','facts'];
const publicationSafeString=(v,max=1000)=>typeof v==='string'?v.slice(0,max):'';
const publicationNumber=(v,min,max,fallback)=>typeof v!=='boolean'&&v!==''&&v!=null&&Number.isFinite(Number(v))?Math.min(max,Math.max(min,Number(v))):fallback;
const publicationColor=(v,fallback)=>/^#[0-9a-f]{6}$/i.test(v||'')?v:fallback;
const publicationChoice=(v,values,fallback)=>values.includes(v)?v:fallback;
const publicationId=(v,fallback)=>typeof v==='string'&&/^[a-zA-Z0-9_-]{1,80}$/.test(v)?v:fallback;
const publicationAssetId=v=>typeof v==='string'&&/^[a-f0-9]{32}$/.test(v)?v:'';
const publicationThemeDefaults={pdf:{background:'#07162b',foreground:'#ffffff',accent:'#b79665',muted:'#9daac0',panel:'#0e223c',line:'#42516a'},social:{background:'#06152e',foreground:'#ffffff',accent:'#ef4b69',muted:'#a8b8d2',panel:'#0b2242',line:'#294568'}};
const publicationStyle=(extra={})=>({fontSize:32,color:'#ffffff',fontFamily:'sans',align:'left',opacity:1,background:'#06152e',borderColor:'#294568',borderWidth:0,radius:0,fontWeight:600,objectFit:'cover',lineHeight:1.35,...extra});
const publicationNode=(id,type,x,y,w,h,bind='',text='',style={})=>({id,type,x,y,w,h,bind,text,photoIndex:0,visible:true,locked:false,z:0,style:publicationStyle(style)});
const publicationText=(id,x,y,w,h,bind='',text='',style={})=>publicationNode(id,'text',x,y,w,h,bind,text,style);
const publicationLine=(id,x,y,w,color='#b79665',h=2)=>publicationNode(id,'line',x,y,w,h,'','',{color,background:color});

function publicationFactNodes(node){
  const nodes=[],height=node.h/6;
  for(let index=0;index<6;index++){const slot=index+1,y=node.y+index*height,bind='fact'+slot;
    nodes.push(publicationText('facts-row-'+slot+'-label',node.x,y+5,node.w*.35,height-14,bind+'Label','',{...node.style,color:'#9daac0',fontSize:Math.min(25,node.style.fontSize),fontWeight:400,borderWidth:0,radius:0}),publicationText('facts-row-'+slot+'-value',node.x+node.w*.38,y+5,node.w*.62,height-14,bind+'Value','',{...node.style,borderWidth:0,radius:0}),{...publicationLine('facts-row-'+slot+'-rule',node.x,y+height-1,node.w,node.style.borderColor,1),bind:bind+'Value'});
  }
  return nodes;
}
function publicationRouteNodes(node,accent){
  const {x,y,w,h,bind}=node,pad=w<700?24:25,small=w<700,lineY=small?h-62:h*.56;
  const nodes=[{...node,type:'rect'},publicationText(node.id+'-title',x+pad,y+pad,w-pad*2-(small?0:238),small?72:56,bind+'Title','',{...node.style,fontWeight:700,borderWidth:0,radius:0}),publicationText(node.id+'-time',x+(small?pad:w-258),y+(small?84:25),small?w-pad*2:233,42,bind+'Time','',{...node.style,fontSize:small?20:node.style.fontSize-2,align:small?'left':'right',borderWidth:0,radius:0}),{...publicationLine(node.id+'-track',x+pad,y+lineY,w-pad*2,accent,small?3:4),bind}];
  for(let index=0;index<3;index++){const cx=pad+index*(w-pad*2)/2,r=small?5:6,sw=(w-pad*2)/3;
    nodes.push(publicationNode(node.id+'-stop-'+(index+1)+'-dot','rect',x+cx-r,y+lineY-r,r*2,r*2,bind,'',{background:node.style.background,borderColor:'#ffffff',borderWidth:2,radius:r}),publicationText(node.id+'-stop-'+(index+1),x+Math.max(pad,Math.min(w-pad-sw,cx-sw/2)),y+lineY+18,sw,h-lineY-24,bind+'Stop'+(index+1),'',{...node.style,fontSize:small?16:Math.max(19,node.style.fontSize-7),fontWeight:600,align:index===0?'left':index===2?'right':'center',borderWidth:0,radius:0}));
  }
  return nodes;
}

function publicationBaseSurfaces(){
  const pdfOverview={id:'pdfOverview',width:1584,height:1120,background:'#07162b',nodes:[
    publicationText('agency',76,48,370,40,'agency','',{fontSize:21,color:'#aab6c9',fontWeight:500}),
    publicationText('brand',600,35,384,55,'brandName','',{fontSize:42,color:'#b79665',align:'center',fontWeight:800}),
    publicationText('tagline',1110,49,398,38,'tagline','',{fontSize:20,color:'#aab6c9',align:'right',fontWeight:500}),
    publicationText('title',76,134,1020,122,'title','',{fontSize:53,fontWeight:800,lineHeight:1.18}),
    publicationText('address',78,258,1040,52,'address','',{fontSize:23,color:'#9daac0',fontWeight:400}),
    publicationText('price',1110,144,400,70,'price','',{fontSize:38,align:'right',fontWeight:700}),
    publicationText('unit',1110,220,400,40,'priceUnit','',{fontSize:22,color:'#9daac0',align:'right',fontWeight:400}),
    publicationLine('top-rule',76,332,1432),
    publicationNode('agent-avatar','avatar',80,392,174,174,'avatar','',{radius:87,borderColor:'#b79665',borderWidth:2}),
    publicationText('agent-name',80,596,438,55,'agentName','',{fontSize:31,fontWeight:700}),
    publicationText('agent-role',80,652,438,42,'agentRole','',{fontSize:22,color:'#9daac0',fontWeight:400}),
    publicationText('agent-phone',80,711,438,45,'phone','',{fontSize:26,color:'#b79665'}),
    publicationText('agent-contact',80,767,438,95,'contact','',{fontSize:23,color:'#aab6c9',fontWeight:400}),
    publicationNode('facts','facts',616,385,891,402,'facts','',{fontSize:26,color:'#ffffff',borderColor:'#42516a',borderWidth:1}),
    publicationText('description-heading',616,834,891,42,'','房源介绍',{fontSize:27,color:'#b79665',fontWeight:700}),
    publicationText('body',616,892,891,139,'body','',{fontSize:23,fontWeight:400,lineHeight:1.5}),
    publicationLine('footer-rule',76,1050,1432,'#42516a',1),
    publicationText('footer',76,1072,1432,28,'footer','',{fontSize:17,color:'#9daac0',fontWeight:400})
  ]};
  const pdfGallery={id:'pdfGallery',width:1584,height:1120,background:'#07162b',nodes:[
    publicationText('agency',76,48,370,40,'agency','',{fontSize:21,color:'#aab6c9',fontWeight:500}),
    publicationText('brand',600,35,384,55,'brandName','',{fontSize:42,color:'#b79665',align:'center',fontWeight:800}),
    publicationText('gallery-heading',180,124,1224,72,'','房源影像',{fontSize:52,fontWeight:800,align:'center'}),
    publicationText('gallery-title',160,211,1264,53,'title','',{fontSize:26,color:'#9daac0',align:'center',fontWeight:400}),
    publicationNode('gallery-photo-1','image',76,294,346,421,'photo1'),
    publicationNode('gallery-photo-2','image',438,294,708,421,'photo2'),
    publicationNode('gallery-photo-3','image',1162,294,346,421,'photo3'),
    publicationNode('gallery-photo-4','image',426,733,358,228,'photo4'),
    publicationNode('gallery-photo-5','image',800,733,358,228,'photo5'),
    publicationNode('gallery-price-box','rect',598,982,388,92,'','',{background:'#07162b',borderColor:'#b79665',borderWidth:1}),
    publicationText('gallery-price',614,995,356,36,'price','',{fontSize:25,align:'center',fontWeight:700}),
    publicationText('gallery-unit',614,1041,356,25,'priceUnit','',{fontSize:17,color:'#9daac0',align:'center',fontWeight:400})
  ]};
  const pdfLocation={id:'pdfLocation',width:1584,height:1120,background:'#07162b',nodes:[
    publicationText('agency',76,48,370,40,'agency','',{fontSize:21,color:'#aab6c9',fontWeight:500}),
    publicationText('brand',600,35,384,55,'brandName','',{fontSize:42,color:'#b79665',align:'center',fontWeight:800}),
    publicationText('location-heading',76,137,1300,72,'','地理位置 / 大学通勤',{fontSize:51,fontWeight:800}),
    publicationText('location-address',76,228,1432,65,'address','',{fontSize:25,color:'#9daac0',fontWeight:400}),
    publicationNode('map','map',76,326,855,532,'map','',{radius:18,borderColor:'#42516a',borderWidth:1}),
    publicationText('map-attribution',76,874,855,33,'mapAttribution','',{fontSize:17,color:'#9daac0',fontWeight:400}),
    publicationText('routes-heading',987,327,520,55,'routesTitle','',{fontSize:29,fontWeight:700}),
    publicationNode('route-1','route',987,411,520,214,'route1','',{fontSize:24,background:'#0e223c',borderColor:'#42516a',borderWidth:1,radius:16}),
    publicationNode('route-2','route',987,650,520,214,'route2','',{fontSize:24,background:'#0e223c',borderColor:'#42516a',borderWidth:1,radius:16}),
    publicationText('location-note',76,944,1432,52,'disclaimer','',{fontSize:19,color:'#9daac0',fontWeight:400}),
    publicationLine('footer-rule',76,1030,1432,'#42516a',1),
    publicationText('location-service',76,1053,1432,37,'service','',{fontSize:23,color:'#b79665',fontWeight:600})
  ]};
  const intro={id:'intro',width:1080,height:1920,background:'#06152e',nodes:[
    publicationNode('hero-photo','image',0,0,1080,957,'photo1'),
    publicationNode('small-photo-left','image',0,966,535,553,'photo2'),
    publicationNode('small-photo-right','image',544,966,536,553,'photo3'),
    publicationNode('brand-mark','logo',30,30,155,155,'logo','',{radius:78,borderColor:'#294568',borderWidth:2}),
    publicationLine('intro-accent',50,1549,120,'#ef4b69',7),
    publicationText('metro',50,1581,560,60,'metro','',{fontSize:43,fontWeight:800}),
    publicationText('walk',654,1588,376,50,'walk','',{fontSize:30,color:'#a8b8d2',align:'right'}),
    publicationText('price',45,1676,430,132,'priceLarge','',{fontSize:105,fontWeight:800,lineHeight:1.05}),
    publicationText('price-unit',405,1711,370,65,'priceUnit','',{fontSize:45,fontWeight:700}),
    publicationText('intro-brand',795,1713,235,60,'brandName','',{fontSize:38,align:'right',fontWeight:800}),
    publicationText('facts',50,1821,984,42,'introFacts','',{fontSize:29,fontWeight:700}),
    publicationNode('service-box','rect',51,1862,979,46,'','',{background:'#06152e',borderColor:'#ef4b69',borderWidth:2,radius:11}),
    publicationText('service',65,1870,950,31,'service','',{fontSize:22,fontWeight:700})
  ]};
  // Exact reference proportions: map first, two route cards, service footer.
  const outro={id:'outro',width:1080,height:1920,background:'#06152e',nodes:[
    publicationLine('outro-accent',50,20,122,'#ef4b69',7),
    publicationText('outro-title',52,55,784,62,'','地铁 / 大学通勤',{fontSize:44,fontWeight:800}),
    publicationText('outro-brand',830,55,200,54,'brandName','',{fontSize:31,align:'right',fontWeight:800}),
    publicationText('outro-address',51,134,978,47,'mapTitle','',{fontSize:28,color:'#a8b8d2'}),
    publicationNode('outro-map','map',42,197,1001,666,'map','',{radius:29,borderColor:'#294568',borderWidth:1}),
    publicationNode('map-callout','rect',71,227,498,67,'','',{background:'#06152e',borderColor:'#294568',borderWidth:1,radius:14}),
    publicationText('map-callout-title',91,242,458,40,'mapTitle','',{fontSize:28,fontWeight:700}),
    publicationNode('metro-callout','rect',550,314,460,91,'','',{background:'#06152e',borderColor:'#294568',borderWidth:1,radius:14}),
    publicationText('metro-callout-title',570,328,421,66,'mapMetro','',{fontSize:26,fontWeight:700}),
    publicationText('outro-attribution',51,884,984,31,'mapAttribution','',{fontSize:17,color:'#a8b8d2',fontWeight:500}),
    publicationText('routes-title',50,941,984,63,'routesTitle','',{fontSize:39,fontWeight:800}),
    publicationText('routes-subtitle',51,999,984,46,'','从住处出发 · 按已确认的路线信息',{fontSize:26,color:'#a8b8d2'}),
    publicationNode('outro-route-1','route',50,1067,986,210,'route1','',{fontSize:29,background:'#0b2242',borderColor:'#294568',borderWidth:1,radius:26}),
    publicationNode('outro-route-2','route',50,1306,986,210,'route2','',{fontSize:29,background:'#0b2242',borderColor:'#294568',borderWidth:1,radius:26}),
    publicationText('outro-disclaimer',51,1553,984,60,'disclaimer','',{fontSize:22,color:'#a8b8d2',fontWeight:500}),
    publicationLine('outro-footer-rule',50,1626,984,'#294568',1),
    publicationNode('outro-brand-mark','logo',50,1660,135,135,'logo','',{radius:68,borderColor:'#294568',borderWidth:1}),
    publicationText('outro-features',214,1674,820,113,'features','',{fontSize:29,fontWeight:700}),
    publicationNode('outro-service-box','rect',50,1833,984,64,'','',{background:'#06152e',borderColor:'#ef4b69',borderWidth:2,radius:14}),
    publicationText('outro-service',72,1850,942,38,'service','',{fontSize:27,fontWeight:700})
  ]};
  pdfOverview.nodes=pdfOverview.nodes.flatMap(node=>node.type==='facts'?publicationFactNodes(node):[node]);
  pdfLocation.nodes=pdfLocation.nodes.flatMap(node=>node.type==='route'?publicationRouteNodes(node,'#b79665'):[node]);
  outro.nodes=outro.nodes.flatMap(node=>node.type==='route'?publicationRouteNodes(node,'#ef4b69'):[node]);
  return {pdfOverview,pdfGallery,pdfLocation,intro,outro};
}

export function defaultPublicationDesign(){return {version:2,preset:'ciliev-elite',paper:'A4',orientation:'landscape',brand:{name:'ciliev',agency:'房地产',tagline:'莫斯科房产服务',agentName:'房产顾问',agentRole:'专属房产顾问',phone:'',email:'',contact:'',footer:'ciliev 房地产 · 莫斯科',logoAssetId:'',avatarAssetId:''},theme:JSON.parse(JSON.stringify(publicationThemeDefaults)),surfaces:publicationBaseSurfaces()};}

export function normalizePublicationDesign(input={}){
  const source=input&&typeof input==='object'&&!Array.isArray(input)?input:{},base=defaultPublicationDesign();
  const brand=source.brand&&typeof source.brand==='object'?source.brand:{};
  for(const key of Object.keys(base.brand))base.brand[key]=key.endsWith('AssetId')?publicationAssetId(brand[key]):publicationSafeString(brand[key]??base.brand[key],['contact','footer'].includes(key)?1000:500);
  base.preset=publicationChoice(source.preset,['ciliev-elite','ciliev-navy'],'ciliev-elite');base.paper=publicationChoice(source.paper,['A4','Letter'],'A4');
  for(const group of ['pdf','social'])for(const key of Object.keys(base.theme[group]))base.theme[group][key]=publicationColor(source.theme?.[group]?.[key],base.theme[group][key]);
  for(const [id,surface] of Object.entries(base.surfaces)){const group=id.startsWith('pdf')?'pdf':'social';surface.background=base.theme[group].background;for(const node of surface.nodes)for(const prop of ['color','background','borderColor'])for(const key of Object.keys(publicationThemeDefaults[group]))if(node.style[prop]===publicationThemeDefaults[group][key]){node.style[prop]=base.theme[group][key];break;}}
  if(source.version===1){
    for(const [from,to] of [['background','background'],['foreground','foreground'],['accent','accent']])base.theme.pdf[to]=publicationColor(source[from],base.theme.pdf[to]);
    if(typeof source.contact==='string')base.brand.contact=source.contact.slice(0,1000);if(typeof source.footer==='string'&&source.footer!=='МОСТ · Подбор жилья в Москве')base.brand.footer=source.footer.slice(0,1000);
    const disabled=new Set((source.blocks||[]).filter(row=>row?.enabled===false).map(row=>row.id));
    for(const [id,surface] of Object.entries(base.surfaces)){
      if(id.startsWith('pdf'))surface.background=base.theme.pdf.background;
      for(const node of surface.nodes){if(id.startsWith('pdf')){node.style.color=node.style.color==='#b79665'?base.theme.pdf.accent:node.style.color==='#ffffff'?base.theme.pdf.foreground:node.style.color;node.style.fontFamily=source.font==='serif'?'serif':'sans';}
        if(disabled.has('description')&&['body','description-heading'].includes(node.id)||disabled.has('facts')&&(node.type==='facts'||node.id.startsWith('facts-row-'))||disabled.has('contact')&&node.id.startsWith('agent-')||disabled.has('photos')&&id==='pdfGallery'||disabled.has('location')&&id==='pdfLocation')node.visible=false;
      }
    }
    if(source.intro?.headline&&source.intro.headline!=='{{title}}'){const node=base.surfaces.intro.nodes.find(n=>n.id==='metro');node.bind='';node.text=publicationSafeString(source.intro.headline,600);}
  }
  for(const id of PUBLICATION_SURFACES){
    const original=source.surfaces?.[id];if(!original||typeof original!=='object')continue;
    const surface=base.surfaces[id];surface.width=publicationNumber(original.width,320,4096,surface.width);surface.height=publicationNumber(original.height,320,4096,surface.height);surface.background=publicationColor(original.background,id.startsWith('pdf')?base.theme.pdf.background:base.theme.social.background);
    if(!Array.isArray(original.nodes))continue;
    const seen=new Set();surface.nodes=original.nodes.slice(0,80).filter(n=>n&&typeof n==='object').map((n,index)=>{
      const baseId=publicationId(n.id,'node-'+index);let nodeId=baseId,duplicate=0;while(seen.has(nodeId)){const suffix='-'+index+'-'+(++duplicate);nodeId=baseId.slice(0,80-suffix.length)+suffix;}seen.add(nodeId);
      const s=n.style&&typeof n.style==='object'?n.style:{},type=publicationChoice(n.type,publicationTypes,'text'),def=publicationStyle();
      const w=publicationNumber(n.w,4,surface.width,surface.width/2),h=publicationNumber(n.h,type==='line'?1:2,surface.height,100);
      return {id:nodeId,type,x:publicationNumber(n.x,0,Math.max(0,surface.width-w),0),y:publicationNumber(n.y,0,Math.max(0,surface.height-h),0),w,h,bind:publicationBindingIds.has(n.bind)?n.bind:'',text:publicationSafeString(n.text,20000),photoIndex:Math.round(publicationNumber(n.photoIndex,0,29,0)),visible:n.visible!==false,locked:n.locked===true,z:Math.round(publicationNumber(n.z,-100,100,0)),style:{fontSize:publicationNumber(s.fontSize,10,200,def.fontSize),color:publicationColor(s.color,def.color),fontFamily:publicationChoice(s.fontFamily,['sans','serif'],def.fontFamily),align:publicationChoice(s.align,['left','center','right'],def.align),opacity:publicationNumber(s.opacity,0,1,1),background:publicationColor(s.background,def.background),borderColor:publicationColor(s.borderColor,def.borderColor),borderWidth:publicationNumber(s.borderWidth,0,20,0),radius:publicationNumber(s.radius,0,400,0),fontWeight:publicationChoice(Number(s.fontWeight),[400,500,600,700,800],600),objectFit:publicationChoice(s.objectFit,['cover','contain'],'cover'),lineHeight:publicationNumber(s.lineHeight,1,2.4,1.35)}};
    });
  }
  return base;
}

export function publicationPage(design,id){return normalizePublicationDesign(design).surfaces[PUBLICATION_SURFACES.includes(id)?id:'pdfOverview'];}
const publicationNumeric=value=>value!==''&&value!=null&&typeof value!=='boolean'&&Number.isFinite(Number(value))?Number(value):null;
const publicationZh=(value,rules)=>translate(publicationSafeString(String(value??''),40000),rules);
const publicationMode={walk:'步行',metro:'地铁',bus:'公交',public:'公共交通',car:'驾车',unknown:'出行方式待确认','步行':'步行','乘坐地铁':'地铁','乘坐公交':'公交'};

export function resolvePublicationData(card={},input={},assets={}){
  const design=normalizePublicationDesign(input),f=card.fields&&typeof card.fields==='object'?card.fields:{},rules={...DICTIONARY,...publicationAddressTerms,...(assets.rules||{})},warnings=[];
  const zh=value=>{const result=publicationZh(value,rules);if(/[А-Яа-яЁё]/.test(result))warnings.push('部分名称仍为俄文，请检查中文翻译。');return result;};
  const price=publicationNumeric(f.price),area=publicationNumeric(f.area),floor=publicationNumeric(f.floor),floors=publicationNumeric(f.floors),lat=publicationNumeric(f.latitude??card.listing?.latitude),lon=publicationNumeric(f.longitude??card.listing?.longitude);
  const coordinates=lat!==null&&lon!==null&&Math.abs(lat)<=85.0511287798066&&Math.abs(lon)<=180;
  const layout=f.layout==='studio'?'开放式公寓':f.layout==='euro2'?'一室一厅':f.layout==='euro3'?'两室一厅':({1:'一居室',2:'两居室',3:'三居室',4:'四居室'})[String(f.rooms)]||(f.rooms?zh(f.rooms)+'居室':'公寓');
  const metro=zh(f.metro||''),address=zh(f.address||f.district||f.complex||''),city=zh(f.city||'Москва');
  const mapPlace=metro||address;
  const mapTitle=mapPlace===city||(city&&mapPlace.startsWith(city)&&/^[\s,，·]/u.test(mapPlace.slice(city.length)))?mapPlace:[city,mapPlace].filter(Boolean).join(' · ');
  const routeRows=String(f.universities||'').split('\n').map(value=>value.trim()).filter(Boolean).slice(0,10).map(value=>{const [name,time='',mode='',line='']=value.split('|').map(v=>v.trim());return {name,time,mode,line};});
  if(!routeRows.length&&f.university)routeRows.push({name:String(f.university),time:String(f.uniTime||''),mode:String(f.uniMode||''),line:''});
  const routes=routeRows.map(row=>{const university=resolveUniversity(row.name),campus=resolveCampus(row.name),personal=Object.keys(assets.rules||{}).some(key=>key.toLocaleLowerCase('ru').replaceAll('ё','е')===row.name.toLocaleLowerCase('ru').replaceAll('ё','е')),name=personal?zh(row.name):university?.zh||campus?.zh||zh(row.name);const time=/^\d{1,3}(?:\s*[-–—]\s*\d{1,3})?$/.test(row.time)?'约'+row.time.replace(/[–—]/g,'-')+'分钟':row.time?zh(row.time):'通勤时间待确认';return {title:name,time,stops:[metro||'住处',row.line?zh(row.line):publicationMode[row.mode]||'出行方式待确认',name],confirmedTime:!!row.time};});
  const featureSet=new Set(Array.isArray(f.features)?f.features:[]);const features=FEATURES.filter(row=>featureSet.has(row[0])).map(row=>{const count=publicationNumeric(f.featureCounts?.[row[0]]);return(count!==null&&count>1?count+'件 ':'')+row[1];}).join(' / ');
  const title=zh(card.title||card.titles?.[0]||f.name||f.complex||'房源详情'),body=zh(card.body||f.description||'');
  const walk=f.walk!==''&&f.walk!=null?(/^[\d\s–—-]+$/.test(String(f.walk))?'步行'+String(f.walk).replace(/[–—]/g,'-')+'分钟':zh(f.walk)):'距离待确认';
  const priceNumber=price===null?'':price.toLocaleString('zh-CN',{maximumFractionDigits:2});
  const priceLarge=price===null?'租金待确认':price>=10000?Number((price/10000).toFixed(2))+'万':priceNumber;
  const data={title,body,brandName:design.brand.name,agency:design.brand.agency,tagline:design.brand.tagline,agentName:design.brand.agentName,agentRole:design.brand.agentRole,phone:design.brand.phone,email:design.brand.email,contact:design.brand.contact||design.brand.email,footer:design.brand.footer,metro,metroLine:zh(f.metroLine||f.line||''),walk,price:priceNumber||'租金待确认',priceLarge,priceUnit:'卢布 / 月',area:area===null?'':String(area)+'㎡',layout,floor:floor===null?'':String(floor)+(floors!==null?' / '+floors:'')+'层',address,city,features,service:'抵达登记（临时居住登记）· 提供办理协助',introFacts:[layout,area!==null?String(area)+'㎡':''].filter(Boolean).join(' / '),mapTitle,mapMetro:[metro,walk].filter(Boolean).join('\n'),routesTitle:'大学通勤参考',route1Title:routes[0]?.title||'院校信息待确认',route1Time:routes[0]?.time||'',route1Stops:routes[0]?.stops.join(' → ')||'',route2Title:routes[1]?.title||'更多院校',route2Time:routes[1]?.time||'',route2Stops:routes[1]?.stops.join(' → ')||'',route1:routes[0]||null,route2:routes[1]||null,routes,facts:[['房屋类型',layout],['月租金',priceNumber?priceNumber+' 卢布':''],['面积',area!==null?String(area)+'㎡':''],['楼层',floor===null?'':String(floor)+(floors!==null?' / '+floors:'')+'层'],['地铁',metro],['家具家电',features]].filter(row=>row[1]),disclaimer:routes.some(row=>row.confirmedTime)?'通勤时间按已提供资料整理，具体以校区和实际路线为准。':'通勤时间及路线尚待确认。',mapAttribution:assets.mapAttribution?'地图数据 © OpenStreetMap 贡献者':coordinates?'地图数据 © OpenStreetMap 贡献者':'尚未确认房源坐标',latitude:coordinates?lat:null,longitude:coordinates?lon:null,mapUrl:coordinates?'https://www.openstreetmap.org/?mlat='+lat+'&mlon='+lon+'#map=16/'+lat+'/'+lon:'',warnings:[...new Set(warnings)]};
  const fixedFacts=[['房屋类型',layout],['月租金',priceNumber?priceNumber+' 卢布':''],['面积',area!==null?String(area)+'㎡':''],['楼层',data.floor],['地铁',metro],['家具家电',features]];
  fixedFacts.forEach(([label,value],index)=>{data['fact'+(index+1)+'Label']=value?label:'';data['fact'+(index+1)+'Value']=value||'';});
  for(let r=0;r<2;r++)for(let s=0;s<3;s++)data['route'+(r+1)+'Stop'+(s+1)]=routes[r]?.stops[s]||'';
  return data;
}

export function publicationNodeText(node,data){const value=node.bind?data[node.bind]:node.text;if(value==null||typeof value==='object')return '';return String(value).replace(/\{\{([a-zA-Z0-9]+)\}\}/g,(_,key)=>typeof data[key]==='string'?data[key]:'');}
