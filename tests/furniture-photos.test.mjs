import test from 'node:test';
import assert from 'node:assert/strict';
import {furniturePhotoIndex} from '../web/furniture-photos.mjs';

test('photo scroll stays bounded before/after the furniture list and advances through every photo',()=>{
  assert.equal(furniturePhotoIndex(1200,1100,900,6),0);
  assert.equal(furniturePhotoIndex(-2000,1100,900,6),5);
  const indexes=new Set();
  for(let top=300;top>=-600;top-=5)indexes.add(furniturePhotoIndex(top,1100,900,6));
  assert.deepEqual([...indexes],[0,1,2,3,4,5]);
  assert.equal(furniturePhotoIndex(10,50,800,1),0);
  assert.equal(furniturePhotoIndex(10,50,800,0),0);
});

test('expanded furniture catalog advances at its measured segment boundary, not a fixed pixel cadence',()=>{
  const viewport=1000,height=1800,count=3,start=100;
  assert.equal(furniturePhotoIndex(start,height,viewport,count),0);
  assert.equal(furniturePhotoIndex(start-240,height,viewport,count),0,'240 px may remain within one segment of a long catalog');
  const distance=height-viewport*.35,targetTop=viewport*.25-distance*1.15/count;
  assert.equal(furniturePhotoIndex(targetTop,height,viewport,count),1);
});
