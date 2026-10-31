"""Shared DOM mechanics; site selectors and headings live in source_adapters/."""
import json

SNAPSHOT_BODY = r'''() => {
  const body = document.body?.innerText || '';
  const selectors = config.fields.join(',');
  const unrelated = '[data-name*="Similar"],[data-name*="Recommend"],[data-marker*="recommend"],[data-marker*="similar"]';
  const headingNodes=[...document.querySelectorAll('h2,h3')].filter(e=>config.headings[e.innerText?.trim()]&&!e.closest(unrelated)).map(e=>e.parentElement);
  const fields = [...new Set([...document.querySelectorAll(selectors),...headingNodes,...document.querySelectorAll(config.gallery.join(','))])].filter(e=>!e.closest(unrelated));
  const blocks = fields.map(e=>e.innerText?.trim()).filter(Boolean);
  const sourceRoot = document.querySelector('main') || document.body;
  const root = sourceRoot ? sourceRoot.cloneNode(true) : document.createElement('div');
  root.querySelectorAll('script,style,nav,header,footer,[data-name*="Similar"],[data-name*="Recommendation"],[data-marker*="recommend"],[data-marker*="similar"]').forEach(e=>e.remove());
  root.querySelectorAll('p,div,li,section,br,dt,dd').forEach(e=>e.append('\n'));
  // Only the apartment gallery is used: recommendation thumbnails are unrelated
  // apartments. Lazy attributes and srcset often contain the full-size image.
  const gallery = [...document.querySelectorAll(config.gallery.join(','))].filter(e=>!e.closest(unrelated));
  const images = [];
  const add = s => {if(s && !/^(?:data|blob|javascript):/i.test(s)) images.push(s);};
  for(const box of gallery)for(const e of box.querySelectorAll('img,source,a[href]')){
    add(e.getAttribute('data-original'));add(e.getAttribute('data-src'));
    for(const a of ['data-srcset','srcset'])for(const part of (e.getAttribute(a)||'').split(','))add(part.trim().split(/\s+/)[0]);
    if(e.tagName==='A'){
      const href=e.getAttribute('href')||'';if(/\.(?:jpe?g|png|webp)(?:[?#]|$)/i.test(href))add(href);
    }else{add(e.currentSrc);add(e.getAttribute('src'));}
  }
  if(config.ogFallback)for(const e of document.querySelectorAll('meta[property="og:image"],meta[name="twitter:image"]'))add(e.content);
  return {title: document.title, body: body.slice(0,12000),
    text: [...new Set(blocks)].join('\n'), fallback: config.strict?'':(root.innerText || root.textContent || '').slice(0,40000),
    sourceHtml: fields.map(e=>e.outerHTML).join('\n').slice(0,1500000),
    metadata: [...document.querySelectorAll('script[type="application/ld+json"],script:not([src]),meta[property="og:image"],meta[property="og:description"],meta[property="og:title"],link[rel="canonical"]')].filter(e=>e.tagName!=='SCRIPT'||e.type==='application/ld+json'||config.metadataTokens.some(token=>e.textContent.includes(token))).map(e=>e.outerHTML).join('\n'),
    relatedHtml: [...document.querySelectorAll('[data-name*="Similar"], [data-name*="Recommend"], [data-marker*="similar"], [data-marker*="recommend"]')].map(e=>e.outerHTML).join('\n').slice(0,1500000),
    images: [...new Set(images)].slice(0,120)};
}'''

SECTION_BODY = r'''({section,scroll=false,index=0}) => {
  const groups = config.sections;
  const unrelated='[data-name*="Similar"],[data-name*="Recommend"],[data-marker*="recommend"],[data-marker*="similar"]';
  const visible=e=>!!e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden';
  const find=kind=>{
    const headingNodes=[...document.querySelectorAll('h2,h3')].filter(e=>config.headings[e.innerText?.trim()]===kind).map(e=>e.parentElement);
    const nodes=[...new Set([...document.querySelectorAll(groups[kind].join(',')),...headingNodes])].filter(e=>visible(e)&&!e.closest(unrelated));
    // A gallery wrapper already includes its nested carousel. Empty detail
    // children remain in the readiness check: a loaded heading must not hide
    // furniture rows which are still waiting for their own viewport trigger.
    return kind==='gallery'?nodes.filter(e=>!nodes.some(other=>other!==e&&other.contains(e))):nodes;
  };
  const nodes=find(section),target=nodes[index];
  if(scroll&&target)target.scrollIntoView({block:section==='gallery'?'start':'center',inline:'nearest',behavior:'auto'});
  const value=target?.innerText?.trim()||'';
  const imgs=target?[...target.querySelectorAll('img')]:[];
  const inView=imgs.filter(e=>{const box=e.getBoundingClientRect();return visible(e)&&box.bottom>0&&box.top<innerHeight&&box.right>0&&box.left<innerWidth;});
  const loaded=inView.filter(e=>e.complete&&e.naturalWidth>0).length;
  const candidates=new Set(imgs.flatMap(e=>[e.currentSrc,e.getAttribute('src'),e.getAttribute('data-original'),e.getAttribute('data-src')]).filter(s=>s&&!/^(?:data|blob|javascript):/i.test(s))).size;
  const textReady=e=>{const heading=e.querySelector('h2,h3')?.innerText?.trim()||'';const text=(e.innerText?.trim()||'').replace(heading,'').trim();return !!text&&!/^(?:загружаем|загрузка|loading)[.\s…]*$/i.test(text);};
  const targetReady=section==='gallery'?loaded>0&&loaded===inView.length:!!target&&textReady(target);
  const nextPendingIndex=section==='gallery'?-1:nodes.findIndex(e=>!textReady(e));
  const ready=section==='gallery'?targetReady:nodes.length>0&&nextPendingIndex<0;
  return {found:!!target,ready:!!target&&ready,targetReady,nextPendingIndex,index,
    recognized:Object.keys(groups).some(kind=>find(kind).length),
    blocks:nodes.length,visibleImages:inView.length,loadedVisibleImages:loaded,
    pendingVisibleImages:inView.length-loaded,imageCandidates:candidates,textLength:value.length};
}'''

def source_scripts(config):
    literal=json.dumps(config,ensure_ascii=False,separators=(',',':'))
    def bind(body):
        index=body.index('{')+1
        if body.startswith('({'):
            index=body.index('=> {')+4
        return body[:index]+'\n  const config = '+literal+';\n'+body[index:]
    return bind(SNAPSHOT_BODY),bind(SECTION_BODY)
