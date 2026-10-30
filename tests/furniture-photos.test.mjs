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
