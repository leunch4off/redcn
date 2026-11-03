import test from 'node:test';
import assert from 'node:assert/strict';
import {editorStartup} from '../web/editor-state.mjs';

test('first launch starts with an empty editor, never a fabricated sample',()=>{
  assert.deepEqual(editorStartup(),{kind:'empty',resultId:null,fields:{},draft:null});
  assert.equal(editorStartup({draft:'bad storage'}).kind,'empty');
});

test('saved-card URL takes priority without exposing or overwriting the browser draft',()=>{
  const draft={version:2,fields:{price:'85000',name:'Another apartment',isDemo:true},output:'Edited draft'};
  const result=editorStartup({local:true,pathname:'/listing/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',draft});
  assert.equal(result.kind,'remote');
  assert.equal(result.resultId,'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa');
  assert.deepEqual(result.fields,{});
  assert.equal(result.draft,null);
  assert.equal(draft.output,'Edited draft');
  assert.equal(draft.fields.isDemo,true);
});

test('real draft and legacy fields are restored without guessing sample provenance',()=>{
  const fields={name:'Примерный район',price:'85000',features:['oven'],isDemo:false};
  const draft={version:2,id:'saved',fields,title:'Manually edited',output:'Exact content',tags:'#tag'};
  const result=editorStartup({local:true,pathname:'/',draft});
  assert.equal(result.fields,fields);
  assert.equal(result.draft,draft);
  assert.equal(result.fields.isDemo,false);
  const legacy={name:'Legacy apartment',source:'Exact user input'};
  assert.equal(editorStartup({draft:legacy}).fields,legacy);
});

test('sample provenance is retained only when the selected draft explicitly has it',()=>{
  const sample={version:2,fields:{isDemo:true,price:'85000'},output:'Example'};
  assert.equal(editorStartup({draft:sample}).fields.isDemo,true);
  assert.equal(editorStartup({draft:{version:2,fields:{name:'Пример · Прокшино, 52 м²',price:'85000'}}}).fields.isDemo,undefined);
  assert.equal(editorStartup({local:false,pathname:'/listing/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',draft:sample}).kind,'draft');
});
