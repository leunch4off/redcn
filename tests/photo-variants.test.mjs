import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {observedPhotoItem,observedPhotoPicture,mergeObservedPhotoItems,photoExportAsset,photoSourceUrl} from '../web/media-core.mjs';
import {readSourceHtml} from '../web/source-import.mjs';
import {presentationAcceptedCrop} from '../web/photo-ui.mjs';
import {presentationCardKey} from '../web/presentation-studio.mjs';
import {defaultPublicationDesign} from '../web/publication-model.mjs';
import {renderPublicationPage,buildPublicationHtml,clearPublicationImageCache} from '../web/publication-render.mjs';

const originalId='a'.repeat(64),variantId='b'.repeat(64),cropId='c'.repeat(64),url='https://images.cdn-cian.ru/images/observed-large.jpg';
function choice(){
 const original={id:originalId,kind:'original',mime:'image/png',width:600,height:600},selectedOriginal={id:variantId,kind:'original',mime:'image/png',width:800,height:800};
 return {original,selectedOriginal,selected:{...selectedOriginal},reason:'observed-variant',variantSelection:{photoId:'fixture-1',canonicalOriginalId:originalId,sourceOriginalId:variantId,selectedUrl:url,groupSha:'d'.repeat(64),decisionSha:'e'.repeat(64),policy:'observed-variants-v1-resolution-480-400000',quality:{status:'resolution-checked',resolutionEligible:true,pixels:640000,shortSide:800},cleanlinessConfirmed:false}};
}

test('DOM/srcset grouping preserves only literal URLs and never resolves an empty candidate into the listing page',()=>{
 const row=observedPhotoItem({'data-photo-id':'p17',src:'https://10.img.avito.st/image/small.jpg',srcset:'https://10.img.avito.st/image/medium.jpg 500w, https://10.img.avito.st/image/large.jpg 1600w'},{base:'https://www.avito.ru/moskva/kvartiry/fixture_17',platform:'avito'});
 assert.equal(row.url,'https://10.img.avito.st/image/large.jpg');assert.equal(row.photoId,'p17');assert.equal(row.identitySource,'platform-photo-id');assert.equal(row.observedVariants.length,3);
 assert.equal(photoSourceUrl('', 'https://www.avito.ru/fixture'),null);assert.equal(photoSourceUrl('  ', 'https://www.avito.ru/fixture'),null);
 const derived=observedPhotoItem({src:url},{platform:'cian'});assert.match(derived.photoId,/^dom-/);assert.equal(derived.identitySource,'observed-dom-node');
 assert.deepEqual(mergeObservedPhotoItems([{url,photoId:'real-17',variants:{thumbnailUrl:row.url}}],[row]).map(item=>item.photoId),['real-17']);
});

test('accepted resolution-qualified variants keep primary and selected-source content identities separate',()=>{
 const value=choice(),selected=presentationAcceptedCrop(value,originalId);
 assert.equal(selected.id,variantId);assert.equal(selected.kind,'original');assert.equal(selected.variantSelection.cleanlinessConfirmed,false);
 for(const mutate of [v=>v.variantSelection.quality.resolutionEligible=false,v=>v.selectedOriginal.width=100,v=>v.variantSelection.sourceOriginalId='f'.repeat(64),v=>v.variantSelection.policy='unreviewed',v=>v.variantSelection.selectedUrl='https://evil.test/image.jpg']){const invalid=choice();mutate(invalid);assert.equal(presentationAcceptedCrop(invalid,originalId),null);}
 const cropped=choice();cropped.reason='manual';cropped.selected={id:cropId,kind:'crop',originalId:variantId,verification:'manual'};
 assert.equal(presentationAcceptedCrop(cropped,originalId).id,cropId);
});

test('literal platform identity merges new observed URLs, while local hash collisions cannot merge unrelated photos',()=>{
 const other='https://images.cdn-cian.ru/images/new-format.webp';
 const existing=[{url,photoId:'123',source:'CIAN offer',variants:{fullUrl:url}}];
 const literal=observedPhotoItem({'data-photo-id':'123',src:other},{platform:'cian'});
 const merged=mergeObservedPhotoItems(existing,[literal]);assert.equal(merged.length,1);assert.equal(merged[0].url,url);assert.equal(merged[0].observedVariants[0].url,other);
 const local={...literal,identitySource:'observed-dom-node'};assert.equal(mergeObservedPhotoItems(existing,[local]).length,2);
});

test('observed passport retains every bounded srcset witness while probing policy may check only six',()=>{
 const urls=Array.from({length:10},(_,index)=>'https://images.cdn-cian.ru/images/observed-'+index+'.webp');
 const row=observedPhotoItem({'data-photo-id':'many-variants','data-srcset':urls.map((url,index)=>url+' '+(index+1)*100+'w').join(',')},{platform:'cian'});
 assert.equal(row.observedVariants.length,10);assert(row.observedVariants.every(value=>value.source==='data-srcset'));
 const merged=mergeObservedPhotoItems([{photoId:'many-variants',source:'CIAN offer',url:urls[0]}],[row]);assert.equal(merged.length,1);assert.equal(merged[0].observedVariants.length,10);
});

test('one picture element proves WebP and JPEG variants belong to one photo for both source adapters',()=>{
 const webp='https://10.img.avito.st/image/picture.webp',jpeg='https://10.img.avito.st/image/picture.jpg',picture='<picture><source type="image/webp" srcset="'+webp+' 1200w"><img src="'+jpeg+'"></picture>';
 const avito=readSourceHtml('<h1 data-marker="item-view/title-info">Квартира</h1><div data-marker="item-view/main-gallery">'+picture+'</div>',{url:'https://www.avito.ru/moskva/kvartiry/fixture_1'});
 assert.equal(avito.images.length,1);assert.equal(avito.photoItems.length,1);assert.equal(avito.photoItems[0].identitySource,'observed-picture');assert.deepEqual(new Set(avito.photoItems[0].observedVariants.map(row=>row.url)),new Set([webp,jpeg]));
 const cian=readSourceHtml('<h1 data-name="OfferTitle">Квартира</h1><div data-name="OfferGallery">'+picture.replaceAll('10.img.avito.st','images.cdn-cian.ru')+'</div>');
 assert.equal(cian.images.length,1);assert.equal(cian.photoItems.length,1);assert.equal(cian.photoItems[0].observedVariants.length,2);
});

test('original export remains primary; prepared export uses the same accepted source and manual crop wins',()=>{
 const original=new Blob(['primary']),variant=new Blob(['observed']),crop=new Blob(['manual']),item={blob:original,variantBlob:variant,variantMeta:presentationAcceptedCrop(choice(),originalId)};
 assert.equal(photoExportAsset(item,false).blob,original);assert.equal(photoExportAsset(item,true).blob,variant);assert.equal(photoExportAsset(item,true).reason,'observed-variant');
 item.cropBlob=crop;item.cropMeta={id:cropId,verification:'manual'};assert.equal(photoExportAsset(item,true).blob,crop);
 item.preferOriginal=true;assert.equal(photoExportAsset(item,true).blob,original);
});

test('phone prepared selection and observed group changes invalidate designer photo caches',()=>{
 const card={fields:{photos:[url]},media:[{id:originalId,prepared:{id:variantId}}]},other=structuredClone(card);other.media[0].prepared.id=cropId;
 assert.notEqual(presentationCardKey(card),presentationCardKey(other));
 const source=structuredClone(card);source.fields.photoItems=[{photoId:'fixture-1',url,observedVariants:[{url:url+'?actual=2',source:'srcset'}]}];assert.notEqual(presentationCardKey(card),presentationCardKey(source));
});

test('a changed original clears furniture decisions tied to the previous raster before publication',async()=>{
 const source=await readFile(new URL('../web/photo-ui.mjs',import.meta.url),'utf8'),body=source.slice(source.indexOf(' async function acceptedCrop('),source.indexOf(' async function ensure('));
 const item={url,serverId:originalId,blob:{name:'old'},analysis:{originalId,candidates:[{id:'fridge'}]},confirmed:{fridge:{}},rejected:{sofa:true}};let saved=0,changed=0,emitted=0;
 const accepted=new Function('api','presentationAcceptedCrop','binary','dimensions','cachePut','changed','emitFeatures','const local=true;'+body+';return acceptedCrop;')(async()=>({original:{id:variantId},selected:{id:variantId,kind:'original'},reason:'original'}),presentationAcceptedCrop,async()=>new Blob(['replacement'],{type:'image/png'}),async()=>({width:800,height:800}),async()=>{saved++;},()=>{changed++;},()=>{emitted++;});
 await accepted(item);assert.equal(item.serverId,variantId);assert.equal(item.analysis,null);assert.deepEqual(item.confirmed,{});assert.deepEqual(item.rejected,{});assert.equal(saved,1);assert.equal(changed,1);assert.equal(emitted,1);
});

test('intro outro canvas and HTML used by PDF consume exactly the accepted photo input',async()=>{
 // This is a renderer-wiring contract. Native Chrome acceptance separately
 // decodes actual raster bytes; this fake Image does not measure image quality.
 const source='data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jK1sAAAAASUVORK5CYII=',seen=[],previous=globalThis.Image;
 class ImageFixture{constructor(){this.width=this.height=this.naturalWidth=this.naturalHeight=800;}set src(value){this._src=value;queueMicrotask(()=>this.onload?.());}get src(){return this._src;}}
 globalThis.Image=ImageFixture;clearPublicationImageCache();
 try{
  const ctx={save(){},restore(){},translate(){},fillRect(){},strokeRect(){},beginPath(){},closePath(){},arc(){},arcTo(){},roundRect(){},fill(){},stroke(){},moveTo(){},lineTo(){},clip(){},drawImage(image){seen.push(image.src);},fillText(){},measureText(text){return {width:text.length*12};}};
  const design=defaultPublicationDesign(),photo=structuredClone(design.surfaces.intro.nodes.find(node=>node.type==='image'));photo.z=100;for(const surface of ['outro','pdfOverview'])design.surfaces[surface].nodes.push({...structuredClone(photo),id:'explicit-'+surface+'-photo'});
  for(const surface of ['intro','outro','pdfOverview','pdfGallery']){seen.length=0;await renderPublicationPage({getContext:()=>ctx},surface,design,{titles:['房源'],fields:{price:80000,area:54}},{photos:[source]});assert(seen.length>0,surface);assert(seen.every(value=>value===source),surface);}
  const html=buildPublicationHtml({titles:['房源'],fields:{price:80000,area:54}},design,{photos:[source]});assert(html.includes(source));
 }finally{globalThis.Image=previous;clearPublicationImageCache();}
});
