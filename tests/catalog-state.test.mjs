import test from 'node:test';
import assert from 'node:assert/strict';
import {compareCatalogCards,catalogWasViewed,catalogMatchesFilter,mergeCatalogCard,mergeCatalogViewed,saveBrowserCatalogCard,markBrowserCatalogViewed} from '../web/catalog-state.mjs';

test('server catalogue keeps insertion order when old text or photos change',()=>{
  const older={id:'a',catalogOrder:100,updated:100},newer={id:'b',catalogOrder:200,updated:200};
  const edited=mergeCatalogCard(older,{id:'a',catalogOrder:100,updated:999,contentEdited:true});
  assert.deepEqual([edited,newer].sort(compareCatalogCards).map(card=>card.id),['b','a']);
  assert.deepEqual([edited,newer].filter(card=>catalogMatchesFilter(card,'edited')).map(card=>card.id),['a']);
  assert.equal(mergeCatalogCard({id:'a',updated:100},{id:'a',updated:999}).catalogOrder,100);
});

test('browser saves preserve existing position and flag only selected publication edits',()=>{
  let rows=[{id:'new',title:'新',output:'原文',tags:'#莫斯科',updated:'2026-01-02'}, {id:'old',title:'旧',output:'原文',tags:'#莫斯科',titles:['旧','选项']}];
  rows=saveBrowserCatalogCard(rows,{...rows[1],price:'80000',photos:['new-photo'],titles:['unused alternative']},'2026-01-03');
  assert.deepEqual(rows.map(card=>card.id),['new','old']);
  assert.equal(rows[1].contentEdited,false);
  assert.equal(rows[1].addedAt,null,'an unknown historical date is not fabricated');
  rows=saveBrowserCatalogCard(rows,{...rows[1],output:'有独立卧室'},'2026-01-04');
  assert.deepEqual(rows.map(card=>card.id),['new','old']);
  assert.equal(rows[1].contentEdited,true);
  assert.equal(rows[1].contentEditedAt,'2026-01-04');
  rows=saveBrowserCatalogCard(rows,{...rows[1],price:'90000'},'2026-01-05');
  assert.equal(rows[1].contentEditedAt,'2026-01-04');
  rows=saveBrowserCatalogCard(rows,{id:'brand-new',title:'新房源'},'2026-01-06');
  assert.deepEqual(rows.map(card=>card.id),['brand-new','new','old']);
  assert.equal(rows[0].addedAt,'2026-01-06');
});

test('viewed state is durable data without a publication edit or update timestamp',()=>{
  const original=[{id:'a',updated:100,title:'房源'},{id:'b',updated:200}];
  const viewed=markBrowserCatalogViewed(original,'a','2026-01-02');
  assert.equal(original[0].viewed,undefined);
  assert.equal(viewed[0].updated,100);
  assert.equal(viewed[0].contentEdited,undefined);
  assert.equal(catalogWasViewed(viewed[0]),true);
  assert.equal(catalogWasViewed(viewed[1]),false);
  assert.equal(catalogWasViewed({viewedAt:'invalid'}),false);
  assert.equal(markBrowserCatalogViewed(viewed,'a','2026-01-03')[0].viewedAt,'2026-01-02');
});

test('title and tags each mark edits while normalized line endings do not',()=>{
  const initial={id:'a',title:'房源',output:'第一行\r\n第二行',tags:'#莫斯科'};
  assert.equal(saveBrowserCatalogCard([initial],{...initial,output:'第一行\n第二行 '})[0].contentEdited,false);
  for(const update of [{title:'新标题'},{tags:'#莫斯科 #租房'}]){
    assert.equal(saveBrowserCatalogCard([initial],{...initial,...update})[0].contentEdited,true);
  }
});

test('late feed snapshot cannot undo a confirmed view in the same reader scope',()=>{
  const oldFeed={id:'a',catalogOrder:100,updated:200,viewed:false,viewedAt:null,contentEdited:false};
  const acknowledged=mergeCatalogViewed(oldFeed,{id:'a',viewed:true,viewedAt:201});
  const merged=mergeCatalogCard(acknowledged,oldFeed);
  assert.equal(merged.viewed,true);assert.equal(merged.viewedAt,201);
  assert.equal(merged.updated,200);
  const switchedScope={...merged,viewed:false,viewedAt:null};
  assert.equal(mergeCatalogCard(switchedScope,oldFeed).viewed,false,'new role starts with its own reader state');
});

test('late view acknowledgement cannot overwrite a newer publication revision',()=>{
  const newer={id:'a',title:'新标题',catalogOrder:100,updated:202,contentEdited:true,contentEditedAt:202,viewed:false,viewedAt:null};
  const staleAck={id:'a',catalogOrder:50,contentEdited:false,contentEditedAt:null,viewed:true,viewedAt:201};
  const merged=mergeCatalogViewed(newer,staleAck);
  assert.deepEqual(merged,{...newer,viewed:true,viewedAt:201});
  const acknowledgementBeforeFeed=mergeCatalogViewed(undefined,staleAck);
  assert.deepEqual(acknowledgementBeforeFeed,{viewed:true,viewedAt:201});
  assert.deepEqual(mergeCatalogViewed(newer,acknowledgementBeforeFeed),merged);
});
