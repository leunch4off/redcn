import test from 'node:test';
import assert from 'node:assert/strict';
import {photoExportAsset,photoZip,listingPhotoSources} from '../web/media-core.mjs';

test('desktop imports merge the full album before saving back to Mac',()=>{
 const urls=Array.from({length:19},(_,i)=>'https://images.cdn-cian.ru/'+i+'.jpg');
 assert.deepEqual(listingPhotoSources({fields:{photos:[urls[0]]},listing:{images:urls}}),urls);
});

test('prepared archive retains nineteen downloadable originals when only one crop succeeds',async()=>{
 const rows=Array.from({length:19},(_,i)=>({blob:new Blob(['original-'+i],{type:'image/jpeg'}),...(i===0?{cropBlob:new Blob(['crop'],{type:'image/jpeg'}),cropMeta:{verification:'manual'}}:{})}));
 const selected=rows.map(row=>photoExportAsset(row,true));
 assert.equal(selected.length,19);assert.equal(selected.filter(row=>row.kind==='crop').length,1);
 assert.equal(selected.filter(row=>row.reason==='original-fallback').length,18);
 const archive=await photoZip(selected.map((row,i)=>({name:i+'.jpg',blob:row.blob})));
 const bytes=await archive.arrayBuffer(),end=new DataView(bytes,bytes.byteLength-22);
 assert.equal(end.getUint16(10,true),19);
});
test('originals export and explicit original selection never substitute a crop',()=>{
 const original=new Blob(['source']),crop=new Blob(['derivative']);
 assert.equal(photoExportAsset({blob:original,cropBlob:crop},false).blob,original);
 assert.equal(photoExportAsset({blob:original,cropBlob:crop,preferOriginal:true},true).blob,original);
 assert.throws(()=>photoExportAsset({cropBlob:crop},false),/Оригинал/);
});
