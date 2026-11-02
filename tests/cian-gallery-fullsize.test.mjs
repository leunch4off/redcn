import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {readCianHtml} from '../web/source-adapters/cian.mjs';
import {sourceCacheMatchesParser} from '../web/source-import.mjs';
import {cianPhotoUrlIdentity,observedPhotoItem,mergeObservedPhotoItems,photoExportAsset} from '../web/media-core.mjs';
import {presentationAcceptedCrop} from '../web/photo-ui.mjs';
import {sourceEditorMetadata} from '../web/source-readiness.mjs';

const url=(id,version,stem='fictional-apartment')=>'https://images.cdn-cian.ru/images/'+stem+'-'+id+'-'+version+'.jpg';
const observation=(id,version,stem)=>observedPhotoItem({src:url(id,version,stem)},{platform:'cian'});
const gallery=html=>'<div data-name="OfferGallery"><div data-name="GalleryInnerComponent"><div data-name="Scroller">'+html+'</div></div></div>';
const manifest=JSON.parse(await readFile(new URL('fixtures/cian-fullsize-v29-webarchive-manifest.json',import.meta.url),'utf8'));

test('the observed nested slider counts thirteen apartments photos, never thirteen more thumbnails',()=>{
 const ids=Array.from({length:13},(_,index)=>String(9400000001+index));
 const thumbs='<ul data-name="PaginationThumbsComponent">'+ids.map(id=>'<li data-name="ThumbComponent"><a><img src="'+url(id,2)+'"></a></li>').join('')+'</ul>';
 const main=ids.map((id,index)=>'<li><div><img '+(index?'loading="lazy" data-src=':'src=')+'"'+url(id,1)+'"></div></li>').join('');
 for(const html of [gallery(main+thumbs),gallery(thumbs+main)]){
  const parsed=readCianHtml(html);
  assert.deepEqual(parsed.images,ids.map(id=>url(id,1)));
  assert.deepEqual(parsed.photoItems.map(row=>row.photoId),ids);
  for(const row of parsed.photoItems){
   assert.equal(row.identitySource,'observed-cian-photo-url');
   assert.deepEqual(new Set(row.observedVariants.map(value=>value.url)),new Set([url(row.photoId,1),url(row.photoId,2)]));
  }
 }
});

test('three loaded mains and thirteen thumbnails retain thirteen groups without inventing ten main URLs',()=>{
 const ids=Array.from({length:13},(_,index)=>String(9400000001+index));
 const html=gallery(ids.slice(0,3).map(id=>'<img src="'+url(id,1)+'">').join('')+ids.map(id=>'<img src="'+url(id,2)+'">').join(''));
 const parsed=readCianHtml(html);
 assert.equal(parsed.images.length,13);
 assert.deepEqual(parsed.images,ids.map((id,index)=>url(id,index<3?1:2)));
 assert.equal(parsed.photoItems.filter(row=>row.observedVariants.some(value=>value.url.endsWith('-1.jpg'))).length,3);
 assert.equal(JSON.stringify(parsed).includes(url(ids[3],1)),false);
});

test('same numeric ID in a different path, stem or host never joins a CIAN URL group',()=>{
 const first=observation('9400000001',1),thumb=observation('9400000001',2);
 const other=observation('9400000001',2,'different-apartment');
 const otherPath=observedPhotoItem({src:url('9400000001',2).replace('/images/','/images/99/')} ,{platform:'cian'});
 const otherHost=observedPhotoItem({src:url('9400000001',2).replace('images.cdn-cian.ru','other.cdn-cian.ru')},{platform:'cian'});
 assert.equal(mergeObservedPhotoItems([],[first,thumb,other,otherPath,otherHost]).length,4);
 const declared=[url('9400000001',1),url('9400000001',2,'different-apartment')].map(src=>observedPhotoItem({'data-photo-id':'9400000001',src},{platform:'cian'}));
 assert.equal(mergeObservedPhotoItems([],declared).length,2,'a reused numeric DOM ID cannot override contradictory exact URL groups');
 for(const value of [url('9400000001',1).replace('https:','http:'),url('9400000001',1).replace('images.cdn-cian.ru','images.cdn-cian.ru.evil.test'),url('9400000001',1).replace('https://','https://user:password@')])assert.equal(cianPhotoUrlIdentity(value),null);
 assert.equal(cianPhotoUrlIdentity(url('9400000001',1)).key,cianPhotoUrlIdentity(url('9400000001',2)).key);
 const numericOnly='https://images.cdn-cian.ru/images/04/104/204/9500000100-1.jpg';
 assert.equal(cianPhotoUrlIdentity(numericOnly).photoId,'9500000100');
 assert.equal(cianPhotoUrlIdentity(numericOnly).key,cianPhotoUrlIdentity(numericOnly.replace('-1.jpg','-2.jpg')).key);
});

test('repeated witnessed groups preserve explicit canonical fullUrl and do not mutate source objects',()=>{
 const id='9400000001',canonical=url(id,4),existing=[{url:canonical,photoId:id,order:0,source:'CIAN offer',variants:{fullUrl:canonical,thumbnailUrl:url(id,2)}}];
 const observed=[observation(id,2),observation(id,1)],before=structuredClone({existing,observed});
 const first=mergeObservedPhotoItems(existing,observed),again=mergeObservedPhotoItems(first,[...observed].reverse());
 assert.equal(first.length,1);assert.equal(first[0].url,canonical);assert.equal(first[0].variants.fullUrl,canonical);
 assert.deepEqual(again,first);assert.deepEqual({existing,observed},before);
 const saved=sourceEditorMetadata(sourceEditorMetadata({photoItems:mergeObservedPhotoItems([],observed)}));
 assert.equal(saved.photoItems[0].identitySource,'observed-cian-photo-url');
 assert.equal(saved.photoItems[0].photoId,id);assert.equal(saved.photoItems[0].url,url(id,1));
 assert.deepEqual(saved.photoItems[0].observedVariants,mergeObservedPhotoItems([],observed)[0].observedVariants);
});

test('an unknown rendition is not downgraded by assuming every suffix other than one is a thumbnail',()=>{
 const id='9400000001';
 assert.equal(mergeObservedPhotoItems([],[observation(id,0),observation(id,1)])[0].url,url(id,0));
 for(const rendition of [2,3,4])assert.equal(mergeObservedPhotoItems([],[observation(id,rendition),observation(id,1)])[0].url,url(id,1));
});

test('a known layout excludes its other observed renditions without excluding a different path',()=>{
 const id='9400000001',layout={url:url(id,1),photoId:id,isLayout:true,variants:{fullUrl:url(id,1)}};
 const rows=mergeObservedPhotoItems([],[observation(id,2),observation(id,4),observation(id,2,'different-apartment')],[layout]);
 assert.equal(rows.length,1);assert.equal(rows[0].url,url(id,2,'different-apartment'));
});

test('lazy srcset wins over a lazy thumbnail while explicit data-original remains authoritative',()=>{
 const thumb='https://images.cdn-cian.ru/images/fictional-preview.jpg',full='https://images.cdn-cian.ru/images/fictional-full.jpg',medium='https://images.cdn-cian.ru/images/fictional-medium.jpg';
 const attributes='data-src="'+thumb+'" data-srcset="'+medium+' 400w, '+full+' 1600w" src="data:image/gif;base64,AA"';
 const parsed=readCianHtml(gallery('<img loading="lazy" '+attributes+'>'));
 assert.deepEqual(parsed.images,[full]);assert.deepEqual(new Set(parsed.photoItems[0].observedVariants.map(row=>row.url)),new Set([thumb,medium,full]));
 const declared='https://images.cdn-cian.ru/images/fictional-declared-original.jpg';
 assert.deepEqual(readCianHtml(gallery('<img data-original="'+declared+'" '+attributes+'>')).images,[declared]);
});

test('recommendation photos remain excluded even if their URL resembles an apartment rendition',()=>{
 const markup=gallery('<img src="'+url('9400000001',1)+'"><div data-name="SimilarOffers"><img src="'+url('9400000002',1)+'"></div>')+'<div data-name="Recommendation"><div data-name="Gallery"><img src="'+url('9400000003',1)+'"></div></div>';
 assert.deepEqual(readCianHtml(markup).images,[url('9400000001',1)]);
});

test('old CIAN parsed albums miss the browser source cache while current CIAN and Avito caches remain usable',()=>{
 const address='https://www.cian.ru/rent/flat/9400000999/',source=readCianHtml(gallery('<img src="'+url('9400000001',1)+'">'));
 assert.equal(source.stats.adapter,'cian-v3-observed-main-gallery');
 const current={at:Date.now(),source},old={at:Date.now(),source:{...source,images:[url('9400000001',2)],stats:{adapter:'cian-v2-gallery-variants'}}};
 const before=structuredClone({current,old});
 assert.equal(sourceCacheMatchesParser(old,address),false);assert.equal(sourceCacheMatchesParser({at:Date.now(),text:'legacy CIAN text'},address),false);
 assert.equal(sourceCacheMatchesParser(current,address),true);
 assert.equal(sourceCacheMatchesParser({at:Date.now(),text:'legacy Avito text'},'https://www.avito.ru/moskva/kvartiry/fixture_9400000999'),true);
 assert.equal(sourceCacheMatchesParser({source:{platform:'avito',stats:{adapter:'avito-v2-gallery-variants'}}},'https://www.avito.ru/moskva/kvartiry/fixture_9400000999'),true);
 assert.deepEqual({current,old},before);
});

test('re-fetch keeps the literal URL, variant passport and manual original/crop choices when source bytes are unchanged',async()=>{
 const source=await readFile(new URL('../web/photo-ui.mjs',import.meta.url),'utf8');
 const body=source.slice(source.indexOf(' function reconcileObservationOriginal('),source.indexOf(' async function loadAll('));
 const group=mergeObservedPhotoItems([],[observation('9400000001',2),observation('9400000001',1)])[0],before=structuredClone(group),digest='a'.repeat(64);
 for(const manual of ['original','crop']){
  const blob=new Blob(['unchanged-original-webp-bytes'],{type:'image/webp'}),cropBlob=new Blob(['manual-copy'],{type:'image/jpeg'}),calls=[],cropCalls=[];
  const margins={top:0,bottom:5,left:0,right:0};
  const item={url:group.url,serverId:digest,blob:new Blob(['old-browser-cache']),selected:true,preferOriginal:manual==='original',manualCropOverride:manual==='crop',expectedMargins:manual==='crop'?margins:undefined,
   analysis:{originalId:digest,candidates:[{id:'fridge'}]},confirmed:{fridge:{count:1}},rejected:{sofa:true}};
  const previous=structuredClone({analysis:item.analysis,confirmed:item.confirmed,rejected:item.rejected});
  const dependencies={presentationAcceptedCrop,itemSource:key=>{assert.equal(key,group.url);return group;},status:()=>{},
   api:async(path,data)=>{calls.push({path,data});assert.equal(data.action,'load');return {id:digest,width:1280,height:960,mime:'image/webp'};},
   binary:async id=>{assert.equal(id,digest);return blob;},dimensions:async value=>{assert.equal(value,blob);return {width:1280,height:960};},
   cachePut:async()=>{},cacheGet:async()=>assert.fail('explicit re-fetch must bypass stale browser bytes'),changed:()=>assert.fail('same bytes cannot invalidate human observations'),emitFeatures:()=>assert.fail('same bytes cannot invalidate human observations'),
   applyClientCrop:async(target,requested)=>{assert.equal(target.blob,blob);cropCalls.push(requested);target.cropBlob=cropBlob;target.cropMeta={verification:'manual',margins:requested};}};
  const load=new Function('dependencies','const local=true;const {presentationAcceptedCrop,itemSource,status,api,binary,dimensions,cachePut,cacheGet,changed,emitFeatures,applyClientCrop}=dependencies;'+body+';return loadOriginal;')(dependencies);
  await load(item,true);
  assert.deepEqual(calls,[{path:'photo',data:{url:group.url,refresh:true,action:'load',source:group}}]);
  assert.equal(item.url,group.url);assert.equal(item.blob,blob);assert.equal(item.mime,'image/webp');assert.equal(item.preferOriginal,manual==='original');
  assert.deepEqual({analysis:item.analysis,confirmed:item.confirmed,rejected:item.rejected},previous);
  assert.equal(photoExportAsset(item,false).blob,blob);
  assert.equal(photoExportAsset(item,true).blob,manual==='original'?blob:cropBlob);
  assert.deepEqual(cropCalls,manual==='crop'?[margins]:[]);
 }
 assert.deepEqual(group,before);
});

for(const fixture of manifest.cases){
 test('redacted observed '+fixture.caseId+' keeps every lazy main frame and excludes external recommendations',async()=>{
  const html=await readFile(new URL('fixtures/'+fixture.fixture,import.meta.url),'utf8'),parsed=readCianHtml(html);
  const expected=fixture.serializedPhotoState.offerData.offer.photos;
  assert.deepEqual(parsed.images,expected.map(row=>row.fullUrl));
  assert.deepEqual(parsed.photoItems.map(row=>row.photoId),fixture.observedMainOrder.map(String));
  assert(parsed.photoItems.every(row=>row.observedVariants.length===2));
  for(const [index,row] of parsed.photoItems.entries())assert.deepEqual(new Set(row.observedVariants.map(value=>value.url)),new Set([expected[index].fullUrl,expected[index].thumbnailUrl]));
 });
 test('literal escaped concat state restores '+fixture.caseId+' full URLs when lazy main nodes are placeholders',async()=>{
  const html=await readFile(new URL('fixtures/'+fixture.fixture,import.meta.url),'utf8');
  const incomplete=html.replace(/(<img\b[^>]*\bsrc=")(https[^\"]+-1\.jpg)(")/g,'$1data:image/gif;base64,AA$3');
  const state=JSON.stringify([{key:'defaultState',value:fixture.serializedPhotoState}]).replaceAll('/','\\u002F');
  const script="<script>window._cianConfig['frontend-offer-card']=(window._cianConfig['frontend-offer-card']||[]).concat("+state+");</script>";
  const parsed=readCianHtml(incomplete.replace('</body>',script+'</body>')),offer=fixture.serializedPhotoState.offerData.offer;
  assert.deepEqual(parsed.images,offer.photos.map(row=>row.fullUrl));
  assert.deepEqual(parsed.photoItems.map(row=>row.photoId),offer.photos.map(row=>String(row.id)));
  assert.equal(parsed.stats.jsonStates,1);
  assert.equal(parsed.layouts.length,fixture.observedCounts.separateLayouts);
  for(const [index,row] of parsed.photoItems.entries())assert.equal(row.variants.fullUrl,offer.photos[index].fullUrl);
 });
}
