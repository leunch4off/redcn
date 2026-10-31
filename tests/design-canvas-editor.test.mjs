import test from 'node:test';
import assert from 'node:assert/strict';
import {designCanvasDelta,moveDesignCanvasNodes,resizeDesignCanvasNode,alignDesignCanvasNodes,designCanvasHistory} from '../web/design-canvas-editor.mjs';
import {defaultPublicationDesign,normalizePublicationDesign} from '../web/publication-model.mjs';

const surface=()=>({width:1080,height:1920,background:'#06152e',nodes:[
 {id:'a',type:'text',x:40,y:50,w:300,h:80,bind:'price',text:'',z:2,style:{color:'#ffffff'}},
 {id:'b',type:'image',x:400,y:400,w:500,h:900,bind:'photo1',z:1},
 {id:'locked',type:'logo',x:10,y:10,w:50,h:50,locked:true,z:3},
]});

test('pointer coordinates retain export units at mobile and zoomed display sizes',()=>{
 assert.deepEqual(designCanvasDelta({x:20,y:30},{x:35,y:60},{width:270,height:480},surface()),{x:60,y:120});
 assert.deepEqual(designCanvasDelta({x:20,y:30},{x:35,y:60},{width:1080,height:1920},surface()),{x:15,y:30});
 assert.deepEqual(designCanvasDelta({x:0,y:0},{x:9,y:9},{width:0,height:0},surface()),{x:0,y:0});
});

test('group movement clamps the whole group, keeps relative positions and leaves locked nodes and source intact',()=>{
 const original=surface(),copy=structuredClone(original),moved=moveDesignCanvasNodes(original,['a','b','locked'],{x:4000,y:-600});
 assert.deepEqual(original,copy);assert.equal(moved.nodes[0].x,220);assert.equal(moved.nodes[1].x,580);
 assert.equal(moved.nodes[0].y,0);assert.equal(moved.nodes[1].y,350);assert.deepEqual(moved.nodes[2],original.nodes[2]);
 assert.equal(moved.nodes[0].bind,'price');assert.equal(moved.nodes[1].bind,'photo1');
});

test('snapping changes position at export scale while resizing stays within the page and preserves thin lines',()=>{
 const moved=moveDesignCanvasNodes(surface(),['a'],{x:13,y:17},{snap:10});assert.equal(moved.nodes[0].x,50);assert.equal(moved.nodes[0].y,70);
 const enlarged=resizeDesignCanvasNode(surface(),'b',{x:4000,y:4000});assert.equal(enlarged.nodes[1].w,680);assert.equal(enlarged.nodes[1].h,1520);
 const reduced=resizeDesignCanvasNode(surface(),'a',{x:-1000,y:-1000});assert.equal(reduced.nodes[0].w,12);assert.equal(reduced.nodes[0].h,12);
 const line={width:100,height:100,nodes:[{id:'line',type:'line',x:5,y:5,w:80,h:2}]};assert.equal(resizeDesignCanvasNode(line,'line',{x:0,y:-30}).nodes[0].h,2);
 assert.deepEqual(resizeDesignCanvasNode(surface(),'locked',{x:100,y:100}),surface());
});

test('alignment centers one object on its page and several objects within their original shared bounds',()=>{
 const single=alignDesignCanvasNodes(surface(),['a'],'center');assert.equal(single.nodes[0].x,390);assert.equal(single.nodes[0].y,50);
 const group=alignDesignCanvasNodes(surface(),['a','b'],'right');assert.equal(group.nodes[0].x+group.nodes[0].w,900);assert.equal(group.nodes[1].x+group.nodes[1].w,900);
 assert.equal(group.nodes[0].bind,'price');assert.equal(group.nodes[1].h,900);assert.deepEqual(group.nodes[2],surface().nodes[2]);
});

test('undo and redo preserve complete designs, reject external mutation and clear a superseded future branch',()=>{
 const first=defaultPublicationDesign(),history=designCanvasHistory(first),second=structuredClone(first);second.brand.name='changed';history.push(second);second.brand.name='mutated outside';
 assert.equal(history.value().brand.name,'changed');assert.equal(history.undo().brand.name,first.brand.name);assert.equal(history.redo().brand.name,'changed');
 history.undo();const third=structuredClone(first);third.brand.name='branch';history.push(third);assert.equal(history.canRedo(),false);assert.equal(history.undo().brand.name,first.brand.name);
});

test('moving a bound node round-trips through template normalization without changing binding, brand, other pages or source card facts',()=>{
 const first=defaultPublicationDesign(),second=structuredClone(first),id=second.surfaces.intro.nodes.find(node=>node.type==='text'&&node.bind).id;
 second.surfaces.intro=moveDesignCanvasNodes(second.surfaces.intro,[id],{x:10,y:10});const normalized=normalizePublicationDesign(second);
 assert.deepEqual(normalized.brand,first.brand);assert.deepEqual(normalized.surfaces.pdfOverview,first.surfaces.pdfOverview);
 assert.equal(normalized.surfaces.intro.nodes.find(node=>node.id===id).bind,first.surfaces.intro.nodes.find(node=>node.id===id).bind);
 assert.deepEqual(normalizePublicationDesign(normalized),normalized);
});
