import test from 'node:test';
import assert from 'node:assert/strict';
import {mobileKeyboardAdjustment,setupMobileKeyboard} from '../web/mobile-keyboard.mjs';
import {acceptanceClientEnvironment} from '../web/acceptance-studio.mjs';

test('focus adjustment uses the visible viewport and actual footer overlap',()=>{
 const viewport={top:25,height:500};
 assert.equal(mobileKeyboardAdjustment({top:60,bottom:110,height:50},viewport),0);
 assert.equal(mobileKeyboardAdjustment({top:10,bottom:60,height:50},viewport),-27);
 assert.equal(mobileKeyboardAdjustment({top:440,bottom:490,height:50},viewport,{obstruction:60}),37);
 assert.equal(mobileKeyboardAdjustment({top:300,bottom:1000,height:700},viewport),263);
 assert.equal(mobileKeyboardAdjustment({top:0,bottom:20,height:20},{top:0,height:0}),0);
});

test('viewport guard follows focus/visible resize and disposes every listener',()=>{
 const callbacks=new Map(),listeners=new Set(),moves=[],styles=new Map();let raf;
 const bind=(key)=>({addEventListener:(name,fn)=>{listeners.add(key+name);callbacks.set(key+name,fn);},removeEventListener:(name)=>listeners.delete(key+name)});
 const viewport={...bind('visual:'),height:852,width:393,offsetTop:0};
 const root={dataset:{},style:{setProperty:(k,v)=>styles.set(k,v),removeProperty:k=>styles.delete(k)}};
 const active={matches:()=>true,disabled:false,readOnly:false,getBoundingClientRect:()=>({top:450,bottom:510,height:60})};
 const doc={...bind('doc:'),documentElement:root,activeElement:active,getElementById:()=>null};
 const win={...bind('win:'),innerWidth:393,innerHeight:852,visualViewport:viewport,requestAnimationFrame:fn=>{raf=fn;return 1;},cancelAnimationFrame:()=>{raf=null;},scrollBy:value=>moves.push(value)};
 const guard=setupMobileKeyboard({document:doc,window:win});raf();
 viewport.height=500;callbacks.get('visual:resize')();raf();
 assert.deepEqual(moves,[{top:22,left:0,behavior:'instant'}]);
 assert.equal(guard.snapshot().reducedViewportObserved,true);
 assert.equal(root.dataset.mobileKeyboard,'open');
 assert.equal(styles.get('--redcn-visible-height'),'500px');
 guard.destroy();assert.equal(listeners.size,0);assert.equal(styles.size,0);
});

test('browser observations do not infer hardware or mobile network from user agent',()=>{
 const value=acceptanceClientEnvironment({window:{location:{origin:'https://fixture.invalid'},innerWidth:393,innerHeight:852,isSecureContext:true,visualViewport:{height:500},matchMedia:()=>({matches:true})},navigator:{userAgent:'iPhone test',share(){},canShare(){}},keyboard:{reducedViewportObserved:true}});
 assert.equal(value.standalone,true);assert.equal(value.reducedViewportObserved,true);assert.equal(value.visualViewportHeight,500);
 assert.equal(value.shareAvailable,true);assert.equal(value.fileShareAvailable,true);
 assert.equal('model' in value,false);assert.equal('network' in value,false);
});
