"""Observed album completeness, independent of page/visible-image readiness."""
from urllib.parse import urljoin, urlsplit
import re


def cian_photo_url_identity(value):
    """Read a literal CDN family; never derive a URL from its filename.

    Directories and the complete filename stem are part of the key. The numeric
    ID alone is insufficient, and the host/protocol boundary is deliberately
    narrower than the general image URL allowlist.
    """
    if not isinstance(value,str):return None
    try:
        parsed=urlsplit(value)
        if parsed.scheme!='https' or parsed.hostname!='images.cdn-cian.ru' or parsed.username or parsed.password or parsed.port is not None:return None
    except ValueError:return None
    match=re.fullmatch(r'(/images/(?:[^/]+/)*(?:[^/]+-)?)(\d{1,20})-(\d{1,3})\.(?:jpe?g|png|webp)',parsed.path,re.I)
    if not match:return None
    return {'key':'https://images.cdn-cian.ru'+match[1]+match[2],
            'photoId':match[2],'rendition':int(match[3])}


def _urls(row):
    if not isinstance(row,dict):return []
    variants=row.get('variants') if isinstance(row.get('variants'),dict) else {}
    observed=row.get('observedVariants') if isinstance(row.get('observedVariants'),list) else []
    return [value for value in [row.get('url'),*variants.values(),*[item.get('url') for item in observed if isinstance(item,dict)]] if isinstance(value,str) and value]


def _family(values):
    identities=[cian_photo_url_identity(value) for value in values]
    return identities[0] if identities and all(item and item['key']==identities[0]['key'] for item in identities) else None


def _canonical(row):
    values=_urls(row)
    if not values:return None
    full=row.get('variants',{}).get('fullUrl') if isinstance(row.get('variants'),dict) else None
    if full in values:return full
    current=values[0];identity=cian_photo_url_identity(current)
    # These preview roles were observed in the source's own serialized photo
    # list. An unknown rendition (e.g. 0/8) is not presumed smaller than -1.
    if identity and identity['rendition'] in (2,3,4):
        for value in values:
            other=cian_photo_url_identity(value)
            if other and other['key']==identity['key'] and other['rendition']==1:return value
    return current


def _validated(value,validate_url,base_url):
    if not isinstance(value,str) or not value:return None
    try:
        absolute=urljoin(base_url,value)
        checked=validate_url(absolute,images=True,resolve=False)
    except (ValueError,TypeError):return None
    # Validators used by the browser capture boundary may assert and return
    # None. Their input is already a literal observed URL, not a generated one.
    return checked if isinstance(checked,str) else absolute


def observed_images(values, validate_url, base_url, photo_items=(), layouts=(), limit=180):
    """Canonicalize explicit source variants or an observed CIAN URL family.

    No URL suffixes are generated and visually similar images are not merged.
    The source's chosen URL remains canonical; availability/quality selection is
    a later media operation and cannot change what the page actually exposed.
    """
    aliases={};excluded=set();excluded_families=set();families={};explicit_families={}
    for row in [*(photo_items if isinstance(photo_items,list) else []),*(layouts if isinstance(layouts,list) else [])]:
        if not isinstance(row,dict):continue
        safe=[]
        for value in _urls(row):
            url=_validated(value,validate_url,base_url)
            if url and url not in safe:safe.append(url)
        if not safe:continue
        if row.get('isLayout') is True or row.get('isCianLayout') is True:
            excluded.update(safe);excluded_families.update(item['key'] for url in safe if (item:=cian_photo_url_identity(url)));continue
        canonical=_validated(_canonical(row),validate_url,base_url) or safe[0]
        if row.get('photoId'):aliases.update({url:canonical for url in safe})
        family=_family(safe)
        if family:
            previous=families.get(family['key'])
            candidates=[*(previous or []),canonical,*safe]
            # Explicit source fullUrl wins over DOM order.
            full=row.get('variants',{}).get('fullUrl') if isinstance(row.get('variants'),dict) else None
            if full in safe:candidates=[full,*candidates];explicit_families.setdefault(family['key'],full)
            families[family['key']]=list(dict.fromkeys(candidates))
    validated=[]
    for value in values if isinstance(values,list) else []:
        url=_validated(value,validate_url,base_url)
        if not url:continue
        identity=cian_photo_url_identity(url)
        if url in excluded or identity and identity['key'] in excluded_families:continue
        validated.append(url)
        if identity:
            family=families.setdefault(identity['key'],[])
            if url not in family:family.append(url)
    family_aliases={key:explicit_families.get(key) or _canonical({'url':urls[0],'observedVariants':[{'url':url} for url in urls]}) for key,urls in families.items()}
    output=[]
    for url in validated:
        url=aliases.get(url,url)
        identity=cian_photo_url_identity(url)
        if identity:url=family_aliases.get(identity['key'],url)
        if url not in output:output.append(url)
    # Source photos[] or verified DOM ordinals are normalized by the merger.
    # The initially visible last slide must not become a cover just because its
    # URL was observed before the scan reached the first slide.
    order={_canonical(row):index for index,row in enumerate(photo_items) if isinstance(row,dict) and row.get('photoId') and type(row.get('order')) is int and row['order']>=0} if isinstance(photo_items,list) else {}
    output=sorted(output,key=lambda url:order.get(url,len(order)+output.index(url)))
    return output[:limit]


def merge_gallery_items(existing, observations, validate_url, base_url, layouts=()):
    """Bind DOM/srcset witnesses to known IDs without deriving unknown URLs."""
    output=[]
    excluded={url for row in layouts if isinstance(row,dict) for url in _urls(row)} if isinstance(layouts,list) else set()
    excluded_families={item['key'] for url in excluded if (item:=cian_photo_url_identity(url))}
    def append(row):
        candidates=set(_urls(row));family=_family(list(candidates))
        if not candidates or candidates.intersection(excluded) or family and family['key'] in excluded_families:return
        def matches(item):
            other=_family(_urls(item))
            if family and other and family['key']!=other['key']:return False
            return bool(candidates.intersection(_urls(item)) or family and other or
                row.get('identitySource')=='platform-photo-id' and item.get('photoId')==row.get('photoId') and (item.get('identitySource')=='platform-photo-id' or item.get('source')=='CIAN offer'))
        match=next((item for item in output if matches(item)),None)
        if match is None:
            row=dict(row);row['url']=_canonical(row);row['order']=len(output);output.append(row);return
        if '_captureOrder' in row and ('_captureOrder' not in match or row['_captureOrder']<match['_captureOrder']):match['_captureOrder']=row['_captureOrder']
        merged=[*(match.get('observedVariants') or []),*(row.get('observedVariants') or [])]
        witnessed=[]
        for item in merged:
            if isinstance(item,dict) and isinstance(item.get('url'),str) and not any(old['url']==item['url'] for old in witnessed):witnessed.append(dict(item))
        # Existing source variants remain source evidence even when they were
        # exposed before the corresponding DOM slide was mounted.
        variants={**(row.get('variants') or {}),**(match.get('variants') or {})}
        match['variants']=variants;match['observedVariants']=witnessed[:60]
        match['url']=_canonical(match)
    for row in existing if isinstance(existing,list) else []:
        if isinstance(row,dict):append(dict(row))
    for raw in observations if isinstance(observations,list) else []:
        if not isinstance(raw,dict):continue
        witnessed=[]
        for item in raw.get('observedVariants',[]) if isinstance(raw.get('observedVariants'),list) else []:
            if not isinstance(item,dict) or item.get('source') not in ('data-original','data-src','data-srcset','srcset','currentSrc','src','href'):continue
            url=_validated(item.get('url'),validate_url,base_url)
            if not url:continue
            if not any(row['url']==url for row in witnessed):witnessed.append({'url':url,'source':item['source']})
        if not witnessed or any(item['url'] in excluded for item in witnessed):continue
        identity=raw.get('photoId');family=_family([item['url'] for item in witnessed])
        marker=raw.get('identitySource')
        if family and marker!='platform-photo-id':identity=family['photoId'];marker='observed-cian-photo-url'
        elif marker=='observed-cian-photo-url' and not family:marker='observed-dom-node'
        if not isinstance(identity,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}',identity):continue
        position=raw.get('galleryIndex');order_source=raw.get('galleryIndexSource')
        order={'_captureOrder':(0 if order_source=='thumbnail-rail' else 1,position)} if type(position) is int and 0<=position<400 and order_source in ('thumbnail-rail','aria-posinset') else {}
        append({'url':witnessed[0]['url'],'photoId':identity,'order':len(output),
            'isLayout':False,'isCianLayout':False,'variants':{},'observedVariants':witnessed[:60],
            'source':str(raw.get('source','gallery DOM'))[:80],
            'identitySource':marker if marker in ('platform-photo-id','observed-picture','observed-cian-photo-url') else 'observed-dom-node',**order})
        if len(output)>=400:break
    output.sort(key=lambda row:(0,row['order']) if row.get('source')=='CIAN offer' else (1,*row['_captureOrder']) if '_captureOrder' in row else (2,row['order']))
    for index,row in enumerate(output):
        for field in ('_captureOrder','galleryIndex','galleryIndexSource'):row.pop(field,None)
        row['order']=index
    return output


def gallery_coverage(states, observed, *, structured_count=None, limit=30, photo_items=()):
    """Return counts/evidence only. Unknown totals never become complete."""
    evidence=[]
    for state in states if isinstance(states,list) else []:
        if not isinstance(state,dict):continue
        rows=state.get('countEvidence',[])
        for row in rows if isinstance(rows,list) else []:
            if not isinstance(row,dict):continue
            count=row.get('count');source=row.get('source')
            if type(count) is int and 0<count<=400 and source in ('aria-setsize','aria-label','gallery-text','data-photo-count','data-image-count','data-total'):
                item={'count':count,'source':source}
                if item not in evidence:evidence.append(item)
    if type(structured_count) is int and 0<structured_count<=400:evidence.append({'count':structured_count,'source':'structured-photo-list'})
    evidence=evidence[:30];counts={row['count'] for row in evidence};expected=max(counts) if counts else None
    canonical=observed_images(observed,lambda value,**_kwargs:value,'',photo_items,limit=1800)
    collected=len(canonical)
    explicit_full={row['variants']['fullUrl'] for row in photo_items if isinstance(row,dict) and isinstance(row.get('variants'),dict) and isinstance(row['variants'].get('fullUrl'),str)} if isinstance(photo_items,list) else set()
    roles=[cian_photo_url_identity(url) for url in canonical]
    check_roles=any(roles)
    family_roles={};full_families=set()
    witnesses=[*(observed if isinstance(observed,list) else []),*[url for row in photo_items for url in _urls(row)]] if isinstance(photo_items,list) else list(observed if isinstance(observed,list) else [])
    for url in witnesses:
        identity=cian_photo_url_identity(url)
        if identity:
            family_roles.setdefault(identity['key'],set()).add(identity['rendition'])
            if url in explicit_full or identity['rendition']==1:full_families.add(identity['key'])
    is_main=lambda url,role:url in explicit_full or bool(role and role['key'] in full_families)
    main=sum(is_main(url,role) for url,role in zip(canonical,roles))
    preview=sum(not is_main(url,role) and bool(role and family_roles.get(role['key'],set()) and family_roles[role['key']]<={2,3,4}) for url,role in zip(canonical,roles))
    missing=max(0,expected-collected) if expected is not None else None
    conflict=len(counts)>1 or expected is not None and collected>expected
    state='partial' if missing or check_roles and preview else 'unknown' if expected is None or conflict or check_roles and main+preview<collected else 'complete'
    return {'version':2 if check_roles else 1,'state':state,'observed':collected,'expected':expected,'missing':missing,
        'countConflict':conflict,'countEvidence':evidence,'limit':limit,
        'retained':min(collected,limit),'overLimit':max(0,collected-limit),
        'completenessVerified':state=='complete',
        **({'mainLinksObserved':main,'previewOnly':preview} if check_roles else {})}


def normalized_coverage(value):
    """Keep only small, reproducible count evidence in saved/public cards."""
    if not isinstance(value,dict):return None
    observed=value.get('observed');expected=value.get('expected');limit=value.get('limit',30)
    if type(observed) is not int or not 0<=observed<=1800:return None
    if expected is not None and (type(expected) is not int or not 0<expected<=400):return None
    if type(limit) is not int or not 1<=limit<=180:limit=30
    check_roles=value.get('version')==2 or 'mainLinksObserved' in value or 'previewOnly' in value
    main=value.get('mainLinksObserved');preview=value.get('previewOnly')
    roles_valid=check_roles and type(main) is int and type(preview) is int and min(main,preview)>=0 and main+preview<=observed
    conflict=value.get('countConflict') is True or expected is not None and observed>expected
    missing=max(0,expected-observed) if expected is not None else None
    state='partial' if missing or roles_valid and preview else 'unknown' if expected is None or conflict or check_roles and (not roles_valid or main+preview<observed) else 'complete'
    evidence=[]
    for row in value.get('countEvidence',[]) if isinstance(value.get('countEvidence'),list) else []:
        if isinstance(row,dict) and type(row.get('count')) is int and 0<row['count']<=400 and row.get('source') in ('aria-setsize','aria-label','gallery-text','data-photo-count','data-image-count','data-total','structured-photo-list'):
            item={'count':row['count'],'source':row['source']}
            if item not in evidence:evidence.append(item)
    return {'version':2 if check_roles else 1,'state':state,'observed':observed,'expected':expected,'missing':missing,
        'countConflict':conflict,'countEvidence':evidence[:30],'limit':limit,
        'retained':min(observed,limit),'overLimit':max(0,observed-limit),'completenessVerified':state=='complete',
        **({'mainLinksObserved':main,'previewOnly':preview} if roles_valid else {})}
