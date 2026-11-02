"""Shared DOM mechanics; site selectors and headings live in source_adapters/."""
import json

SNAPSHOT_BODY = r'''() => {
  const began=performance.now();
  const body = document.body?.innerText || '';
  const selectors = config.fields.join(',');
  const unrelated = '[data-name*="Similar"],[data-name*="Recommend"],[data-marker*="recommend"],[data-marker*="similar"]';
  const headingNodes=[...document.querySelectorAll('h2,h3')].filter(e=>config.headings[e.innerText?.trim()]&&!e.closest(unrelated)).map(e=>e.parentElement);
  const fields = [...new Set([...document.querySelectorAll(selectors),...headingNodes,...document.querySelectorAll(config.gallery.join(','))])].filter(e=>!e.closest(unrelated));
  const blocks = fields.map(e=>e.innerText?.trim()).filter(Boolean),text=[...new Set(blocks)].join('\n');
  const minimum=text.length>=80&&/\d[\d\s\xa0,.]*\s*(?:м²|м2|кв\.?\s*м|㎡)/i.test(text)&&/\d[\d\s\xa0]*\s*(?:₽|руб)/i.test(text);
  // The pruned full-page clone is a fallback, not mandatory work on every poll.
  // Avito never consumes it; a recognized CIAN summary uses the scoped blocks.
  let fallback='';
  if(!config.strict&&!minimum){
    const sourceRoot=document.querySelector('main')||document.body;
    const root=sourceRoot?sourceRoot.cloneNode(true):document.createElement('div');
    root.querySelectorAll('script,style,nav,header,footer,[data-name*="Similar"],[data-name*="Recommendation"],[data-marker*="recommend"],[data-marker*="similar"]').forEach(e=>e.remove());
    root.querySelectorAll('p,div,li,section,br,dt,dd').forEach(e=>e.append('\n'));
    fallback=(root.innerText||root.textContent||'').slice(0,40000);
  }
  // Only the apartment gallery is used: recommendation thumbnails are unrelated
  // apartments. Lazy attributes and srcset often contain the full-size image.
  const gallery = [...document.querySelectorAll(config.gallery.join(','))].filter(e=>!e.closest(unrelated));
  const images = [],galleryPhotos=[],seenPictureNodes=new Set();
  const thumbnailOrder=new Map();
  if(config.id==='cian')for(const box of gallery)for(const thumb of box.querySelectorAll('[data-name="PaginationThumbsComponent"] img')){
    const key=cianPhotoIdentity(thumb.getAttribute('src')||thumb.currentSrc||'')?.key;
    if(key&&!thumbnailOrder.has(key))thumbnailOrder.set(key,thumbnailOrder.size);
  }
  const add = s => {if(s && !/^(?:data|blob|javascript):/i.test(s)) images.push(s);};
  for(const box of gallery)for(const e of box.querySelectorAll('img,source,a[href]')){
    if(e.closest(unrelated))continue;
    const picture=e.tagName==='A'?null:e.closest('picture');if(picture&&seenPictureNodes.has(picture))continue;if(picture)seenPictureNodes.add(picture);
    const members=picture?[...picture.querySelectorAll('source,img')]:[e];
    const set=members.flatMap(node=>['data-srcset','srcset'].flatMap(a=>(node.getAttribute(a)||'').split(',').map(part=>{const [url,size='0']=part.trim().split(/\s+/);return {url,source:a,weight:parseFloat(size)||0};}))).sort((a,b)=>b.weight-a.weight);
    if(e.tagName==='A'){
      const href=e.getAttribute('href')||'';if(/\.(?:jpe?g|png|webp)(?:[?#]|$)/i.test(href))add(href);
    }else{
      const candidate=[e.getAttribute('data-original'),...set.map(row=>row.url),e.getAttribute('data-src'),e.currentSrc,e.getAttribute('src')].find(s=>s&&!/^(?:data|blob|javascript):/i.test(s));add(candidate);
      const raw=[...members.map(node=>({url:node.getAttribute('data-original'),source:'data-original'})),...set.map(row=>({url:row.url,source:row.source})),...members.map(node=>({url:node.getAttribute('data-src'),source:'data-src'})),...members.flatMap(node=>[{url:node.currentSrc,source:'currentSrc'},{url:node.getAttribute('src'),source:'src'}])];
      const observedVariants=[];for(const row of raw){if(!row.url||!row.url.trim())continue;try{const u=new URL(row.url,location.href);if(u.protocol!=='https:'||u.username||u.password||u.port||!/(^|\.)(cian\.ru|cdn-cian\.ru|ciancdn\.ru|avito\.ru|avito\.st)$/i.test(u.hostname))continue;u.hash='';if(!observedVariants.some(item=>item.url===u.href))observedVariants.push({url:u.href,source:row.source});}catch{}}
      if(observedVariants.length){const url=observedVariants[0].url,declared=e.getAttribute('data-photo-id')||e.getAttribute('data-image-id')||(picture||e.closest('[data-photo-id],[data-image-id]'))?.getAttribute('data-photo-id')||(picture||e.closest('[data-photo-id],[data-image-id]'))?.getAttribute('data-image-id')||'';let photoId=/^[A-Za-z0-9_.:-]{1,100}$/.test(declared)?declared:'',identitySource=photoId?'platform-photo-id':picture?'observed-picture':'observed-dom-node';const families=config.id==='cian'?observedVariants.map(row=>cianPhotoIdentity(row.url)):[],first=families[0],family=first&&families.every(row=>row?.key===first.key)?first:null;if(!photoId&&family){photoId=family.photoId;identitySource='observed-cian-photo-url';}if(!photoId){let hash=2166136261;for(const char of config.id+'|'+location.origin+location.pathname+'|'+url){hash^=char.charCodeAt(0);hash=Math.imul(hash,16777619);}photoId='dom-'+(hash>>>0).toString(16).padStart(8,'0');}const pos=Number(e.closest('[aria-posinset]')?.getAttribute('aria-posinset')),thumbIndex=family?thumbnailOrder.get(family.key):undefined,sourceOrder=Number.isInteger(thumbIndex)?{galleryIndex:thumbIndex,galleryIndexSource:'thumbnail-rail'}:Number.isInteger(pos)&&pos>=1&&pos<=400?{galleryIndex:pos-1,galleryIndexSource:'aria-posinset'}:{};galleryPhotos.push({url,photoId,identitySource,source:config.id+' gallery DOM',observedVariants:observedVariants.slice(0,60),order:galleryPhotos.length,...sourceOrder});}
    }
  }
  if(config.ogFallback&&!images.length)for(const e of document.querySelectorAll('meta[property="og:image"],meta[name="twitter:image"]'))add(e.content);
  const sectionStates=Object.fromEntries(Object.keys(config.sections).map(kind=>[kind,readSection(kind)]));
  const stabilityKey=JSON.stringify([...document.querySelectorAll(config.stability.join(','))].filter(e=>!e.closest(unrelated)).map(e=>(e.innerText||'').trim()).filter(Boolean));
  const result={title: document.title, sectionStates, stabilityKey:stabilityKey==='[]'?'':stabilityKey, body: body.slice(0,12000),
    text,fallback,
    sourceHtml: fields.map(e=>e.outerHTML).join('\n').slice(0,1500000),
    metadata: [...document.querySelectorAll('script[type="application/ld+json"],script:not([src]),meta[property="og:image"],meta[property="og:description"],meta[property="og:title"],link[rel="canonical"]')].filter(e=>e.tagName!=='SCRIPT'||e.type==='application/ld+json'||config.metadataTokens.some(token=>e.textContent.includes(token))).map(e=>e.outerHTML).join('\n'),
    relatedHtml: [...document.querySelectorAll('[data-name*="Similar"], [data-name*="Recommend"], [data-marker*="similar"], [data-marker*="recommend"]')].map(e=>e.outerHTML).join('\n').slice(0,1500000),
    galleryState:galleryState(gallery),galleryPhotos:galleryPhotos.slice(0,120),images: [...new Set(images)].slice(0,120)};
  result.domReadMs=performance.now()-began;
  const serializedAt=performance.now(),serialized=JSON.stringify(result);
  result.serializationProbeMs=performance.now()-serializedAt;result.serializedChars=serialized.length;
  return result;
}'''

SECTION_HELPER = r'''  const sectionGroups = config.sections;
  const sectionUnrelated='[data-name*="Similar"],[data-name*="Recommend"],[data-marker*="recommend"],[data-marker*="similar"]';
  const sectionVisible=e=>!!e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden';
  const cianPhotoIdentity=value=>{try{const url=new URL(value);if(url.protocol!=='https:'||url.hostname!=='images.cdn-cian.ru'||url.username||url.password||url.port)return null;const match=url.pathname.match(/^(\/images\/(?:[^/]+\/)*(?:[^/]+-)?)(\d{1,20})-(\d{1,3})\.(?:jpe?g|png|webp)$/i);return match?{key:url.origin+match[1]+match[2],photoId:match[2],rendition:Number(match[3])}:null;}catch{return null;}};
  const galleryScrollers=nodes=>{
    const candidates=[...new Set(nodes.flatMap(root=>[root,...root.querySelectorAll('*')]))].filter(e=>!e.closest(sectionUnrelated)&&sectionVisible(e)&&e.clientWidth>32&&e.scrollWidth>e.clientWidth+4&&/auto|scroll/.test(getComputedStyle(e).overflowX));
    // The observed CIAN outer Scroller is overflow:hidden; its nested main UL
    // scrolls. The separate 64px thumbnail rail must not win by width ratio.
    const main=config.id==='cian'?candidates.filter(e=>e.tagName==='UL'&&e.closest('[data-name="Scroller"]')&&!e.closest('[data-name="PaginationThumbsComponent"]')):[];
    const relevant=main.length?main:candidates.filter(e=>!e.closest('[data-name="PaginationThumbsComponent"]'));
    return relevant.sort((a,b)=>(b.scrollWidth/b.clientWidth)-(a.scrollWidth/a.clientWidth));
  };
  const galleryState = nodes => {
    const evidence=[];const addCount=(value,source)=>{const count=Number(value);if(Number.isInteger(count)&&count>0&&count<=400&&!evidence.some(row=>row.count===count&&row.source===source))evidence.push({count,source});};
    for(const root of nodes){
      for(const node of [root,...root.querySelectorAll('[aria-setsize],[aria-label],[data-photo-count],[data-image-count],[data-total]')]){
        if(node.closest(sectionUnrelated))continue;
        if(node.hasAttribute('aria-setsize'))addCount(node.getAttribute('aria-setsize'),'aria-setsize');
        for(const name of ['data-photo-count','data-image-count','data-total'])if(/^\d{1,3}$/.test(node.getAttribute(name)||''))addCount(node.getAttribute(name),name);
        const label=node.getAttribute('aria-label')||'',counter=label.match(/\b\d{1,3}\s*(?:из|of|\/)\s*(\d{1,3})\b/i);if(counter)addCount(counter[1],'aria-label');
      }
      const texts=[],walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);let textNode,size=0;
      while((textNode=walker.nextNode())&&size<1500){if(textNode.parentElement?.closest(sectionUnrelated+',script,style'))continue;const part=(textNode.nodeValue||'').slice(0,1500-size);texts.push(part);size+=part.length;}
      const text=texts.join(' ');
      for(const match of text.matchAll(/(?:^|\s)(\d{1,3})\s*(?:фото(?:графий|графии)?|photos?)(?:\s|$|[.,:])/gi))addCount(match[1],'gallery-text');
      const counter=text.match(/(?:^|\s)\d{1,3}\s*(?:из|of|\/)\s*(\d{1,3})(?:\s|$)/i);if(counter)addCount(counter[1],'gallery-text');
    }
    const scrollers=galleryScrollers(nodes);
    return {countEvidence:evidence.slice(0,30),scrollable:scrollers.length>0};
  };
  const sectionFind=kind=>{
    const headingNodes=[...document.querySelectorAll('h2,h3')].filter(e=>config.headings[e.innerText?.trim()]===kind).map(e=>e.parentElement);
    const nodes=[...new Set([...document.querySelectorAll(sectionGroups[kind].join(',')),...headingNodes])].filter(e=>sectionVisible(e)&&!e.closest(sectionUnrelated));
    // A gallery wrapper already includes its nested carousel. Empty detail
    // children remain in the readiness check: a loaded heading must not hide
    // furniture rows which are still waiting for their own viewport trigger.
    return kind==='gallery'?nodes.filter(e=>!nodes.some(other=>other!==e&&other.contains(e))):nodes;
  };
  const readSection = (section,scroll=false,index=0) => {
  const nodes=sectionFind(section),target=nodes[index];
  if(scroll&&target)target.scrollIntoView({block:section==='gallery'?'start':'center',inline:'nearest',behavior:'auto'});
  const value=target?.innerText?.trim()||'';
  const imgs=target?[...target.querySelectorAll('img')].filter(e=>!e.closest(sectionUnrelated)):[];
  const inView=imgs.filter(e=>{const box=e.getBoundingClientRect();return sectionVisible(e)&&box.bottom>0&&box.top<innerHeight&&box.right>0&&box.left<innerWidth;});
  const loaded=inView.filter(e=>e.complete&&e.naturalWidth>0).length;
  const candidates=new Set(imgs.flatMap(e=>[e.currentSrc,e.getAttribute('src'),e.getAttribute('data-original'),e.getAttribute('data-src')]).filter(s=>s&&!/^(?:data|blob|javascript):/i.test(s))).size;
  const textReady=e=>{const heading=e.querySelector('h2,h3')?.innerText?.trim()||'';const text=(e.innerText?.trim()||'').replace(heading,'').trim();return !!text&&!/^(?:загружаем|загрузка|loading)[.\s…]*$/i.test(text);};
  const targetReady=section==='gallery'?loaded>0&&loaded===inView.length:!!target&&textReady(target);
  const nextPendingIndex=section==='gallery'?-1:nodes.findIndex(e=>!textReady(e));
  const ready=section==='gallery'?targetReady:nodes.length>0&&nextPendingIndex<0;
  return {found:!!target,ready:!!target&&ready,targetReady,nextPendingIndex,index,
    recognized:Object.keys(sectionGroups).some(kind=>sectionFind(kind).length),
    blocks:nodes.length,visibleImages:inView.length,loadedVisibleImages:loaded,
    pendingVisibleImages:inView.length-loaded,imageCandidates:candidates,textLength:value.length,
    ...(section==='gallery'?{album:galleryState(nodes)}:{})};
  };
'''

SECTION_BODY = r'''({section,scroll=false,index=0}) => {
  const began=performance.now();
  const result=section==='all'?{sections:Object.fromEntries(Object.keys(config.sections).map(kind=>[kind,readSection(kind)]))}:readSection(section,scroll,index);
  result.domReadMs=performance.now()-began;
  return result;
}'''


def source_scripts(config):
    literal=json.dumps(config,ensure_ascii=False,separators=(',',':'))
    def bind(body):
        index=body.index('{')+1
        if body.startswith('({'):
            index=body.index('=> {')+4
        return body[:index]+'\n  const config = '+literal+';\n'+SECTION_HELPER+body[index:]
    return bind(SNAPSHOT_BODY),bind(SECTION_BODY)


def gallery_scroll_script(config):
    """Only scroll an observed horizontal gallery container; never click UI."""
    literal=json.dumps(config,ensure_ascii=False,separators=(',',':'))
    return r'''({restore=null}) => {
      const config=CONFIG;
      HELPERS
      const roots=[...document.querySelectorAll(config.gallery.join(','))].filter(e=>!e.closest(sectionUnrelated));
      const target=galleryScrollers(roots)[0];
      if(!target)return {scrollable:false,moved:false,atEnd:false};
      const before=target.scrollLeft;
      target.scrollLeft=restore===null?Math.min(target.scrollWidth-target.clientWidth,before+Math.max(32,Math.floor(target.clientWidth*.9))):restore;
      return {scrollable:true,moved:Math.abs(target.scrollLeft-before)>1,before,after:target.scrollLeft,atEnd:target.scrollLeft+target.clientWidth>=target.scrollWidth-4};
    }'''.replace('CONFIG',literal).replace('HELPERS',SECTION_HELPER)


def gallery_sweep_script(config):
    """Collect a virtual horizontal strip in one bounded browser roundtrip."""
    literal=json.dumps(config,ensure_ascii=False,separators=(',',':'))
    image_read=SNAPSHOT_BODY[SNAPSHOT_BODY.index('  const gallery ='):SNAPSHOT_BODY.index('  if(config.ogFallback')]
    return '''async ({budgetMs=1200,maxMoves=40}) => {
      const config = '''+literal+';\n'+SECTION_HELPER+'''
      const began=performance.now(),end=began+Math.max(1,Math.min(1800,budgetMs));
      let readsMs=0,moves=0,restored=false;const images=[],photos=[],states=[];
      const collect=()=>{const start=performance.now(),unrelated=sectionUnrelated;
      '''+image_read+'''
        const state=galleryState(gallery);readsMs+=performance.now()-start;return {images,galleryPhotos,galleryState:state};
      };
      const roots=[...document.querySelectorAll(config.gallery.join(','))].filter(e=>!e.closest(sectionUnrelated));
      const target=galleryScrollers(roots)[0],before=target?.scrollLeft||0;
      let exhausted=false;
      const frame=()=>new Promise(resolve=>{let settled=false;const finish=()=>{if(!settled){settled=true;clearTimeout(timer);resolve();}};const timer=setTimeout(finish,Math.min(40,Math.max(1,end-performance.now())));requestAnimationFrame(finish);});
      try{
        if(target){
          // Lazy source callbacks may ignore offscreen rails. Show the actual
          // main scroller without another browser roundtrip, then scan from
          // the beginning even when the user initially opened the last slide.
          target.scrollIntoView({block:'start',inline:'nearest',behavior:'instant'});
          if(performance.now()<end)await frame();
          target.scrollLeft=0;
          if(performance.now()<end)await frame();
        }
        for(let step=0;step<=Math.min(64,maxMoves);step++){
          const value=collect();images.push(...value.images);photos.push(...value.galleryPhotos);states.push(value.galleryState);
          if(!target||target.scrollLeft+target.clientWidth>=target.scrollWidth-4)break;
          if(performance.now()>=end||step===Math.min(64,maxMoves)){exhausted=true;break;}
          const previous=target.scrollLeft;target.scrollLeft=Math.min(target.scrollWidth-target.clientWidth,previous+Math.max(32,Math.floor(target.clientWidth*.9)));
          if(Math.abs(target.scrollLeft-previous)<1)break;moves++;
          await frame();
        }
      }finally{if(target){target.scrollLeft=before;restored=true;}}
      return {images:[...new Set(images)].slice(0,180),galleryPhotos:photos.slice(0,1200),galleryStates:states.slice(0,65),moves,restored,bounded:exhausted,domReadMs:readsMs,elapsedMs:performance.now()-began};
    }'''
