"""Observed album completeness, independent of page/visible-image readiness."""
from urllib.parse import urljoin
import re


def observed_images(values, validate_url, base_url, photo_items=(), layouts=(), limit=180):
    """Canonicalize only variant URLs explicitly grouped by a source photo ID.

    No URL suffixes are generated and visually similar images are not merged.
    The source's chosen URL remains canonical; availability/quality selection is
    a later media operation and cannot change what the page actually exposed.
    """
    aliases={};excluded=set()
    for row in [*(photo_items if isinstance(photo_items,list) else []),*(layouts if isinstance(layouts,list) else [])]:
        if not isinstance(row,dict):continue
        variants=row.get('variants') if isinstance(row.get('variants'),dict) else {}
        witnessed=row.get('observedVariants') if isinstance(row.get('observedVariants'),list) else []
        candidates=[row.get('url'),*variants.values(),*[value.get('url') for value in witnessed if isinstance(value,dict)]]
        safe=[]
        for value in candidates:
            try:url=validate_url(urljoin(base_url,value),images=True,resolve=False)
            except (ValueError,TypeError):continue
            if url not in safe:safe.append(url)
        if not safe:continue
        if row.get('isLayout') is True or row.get('isCianLayout') is True:
            excluded.update(safe);continue
        if row.get('photoId'):aliases.update({url:safe[0] for url in safe})
    output=[]
    for value in values if isinstance(values,list) else []:
        try:url=validate_url(urljoin(base_url,value),images=True,resolve=False)
        except (ValueError,TypeError):continue
        if url in excluded:continue
        url=aliases.get(url,url)
        if url not in output:output.append(url)
    return output[:limit]


def merge_gallery_items(existing, observations, validate_url, base_url, layouts=()):
    """Bind DOM/srcset witnesses to known IDs without deriving unknown URLs."""
    output=[dict(row) for row in existing if isinstance(row,dict)] if isinstance(existing,list) else []
    def urls(row):
        return [row.get('url'),*(row.get('variants',{}).values() if isinstance(row.get('variants'),dict) else []),*[item.get('url') for item in row.get('observedVariants',[]) if isinstance(item,dict)]]
    excluded={url for row in layouts if isinstance(row,dict) for url in urls(row)} if isinstance(layouts,list) else set()
    for raw in observations if isinstance(observations,list) else []:
        if not isinstance(raw,dict):continue
        witnessed=[]
        for item in raw.get('observedVariants',[]) if isinstance(raw.get('observedVariants'),list) else []:
            if not isinstance(item,dict) or item.get('source') not in ('data-original','data-src','data-srcset','srcset','currentSrc','src','href'):continue
            try:url=validate_url(urljoin(base_url,item.get('url')),images=True,resolve=False)
            except (ValueError,TypeError):continue
            if not any(row['url']==url for row in witnessed):witnessed.append({'url':url,'source':item['source']})
        if not witnessed or any(item['url'] in excluded for item in witnessed):continue
        candidate_urls={item['url'] for item in witnessed}
        match=next((row for row in output if candidate_urls.intersection(urls(row)) or raw.get('identitySource')=='platform-photo-id' and row.get('photoId')==raw.get('photoId') and (row.get('identitySource')=='platform-photo-id' or row.get('source')=='CIAN offer')),None)
        if match is not None:
            merged=[*(match.get('observedVariants') or []),*witnessed]
            match['observedVariants']=[]
            for row in merged:
                if not any(item['url']==row['url'] for item in match['observedVariants']):match['observedVariants'].append(dict(row))
            match['observedVariants']=match['observedVariants'][:60]
        else:
            identity=raw.get('photoId')
            if not isinstance(identity,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}',identity):continue
            output.append({'url':witnessed[0]['url'],'photoId':identity,'order':len(output),
                'isLayout':False,'isCianLayout':False,'variants':{},'observedVariants':witnessed[:60],
                'source':str(raw.get('source','gallery DOM'))[:80],
                'identitySource':raw['identitySource'] if raw.get('identitySource') in ('platform-photo-id','observed-picture') else 'observed-dom-node'})
        if len(output)>=400:break
    return output


def gallery_coverage(states, observed, *, structured_count=None, limit=30):
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
    collected=len(set(value for value in observed if isinstance(value,str))) if isinstance(observed,list) else 0
    missing=max(0,expected-collected) if expected is not None else None
    conflict=len(counts)>1 or expected is not None and collected>expected
    state='partial' if missing else 'unknown' if expected is None or conflict else 'complete'
    return {'version':1,'state':state,'observed':collected,'expected':expected,'missing':missing,
        'countConflict':conflict,'countEvidence':evidence,'limit':limit,
        'retained':min(collected,limit),'overLimit':max(0,collected-limit),
        'completenessVerified':state=='complete'}


def normalized_coverage(value):
    """Keep only small, reproducible count evidence in saved/public cards."""
    if not isinstance(value,dict):return None
    observed=value.get('observed');expected=value.get('expected');limit=value.get('limit',30)
    if type(observed) is not int or not 0<=observed<=1800:return None
    if expected is not None and (type(expected) is not int or not 0<expected<=400):return None
    if type(limit) is not int or not 1<=limit<=180:limit=30
    conflict=value.get('countConflict') is True or expected is not None and observed>expected
    missing=max(0,expected-observed) if expected is not None else None
    state='partial' if missing else 'unknown' if expected is None or conflict else 'complete'
    evidence=[]
    for row in value.get('countEvidence',[]) if isinstance(value.get('countEvidence'),list) else []:
        if isinstance(row,dict) and type(row.get('count')) is int and 0<row['count']<=400 and row.get('source') in ('aria-setsize','aria-label','gallery-text','data-photo-count','data-image-count','data-total','structured-photo-list'):
            item={'count':row['count'],'source':row['source']}
            if item not in evidence:evidence.append(item)
    return {'version':1,'state':state,'observed':observed,'expected':expected,'missing':missing,
        'countConflict':conflict,'countEvidence':evidence[:30],'limit':limit,
        'retained':min(observed,limit),'overLimit':max(0,observed-limit),'completenessVerified':state=='complete'}
