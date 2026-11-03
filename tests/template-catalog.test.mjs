import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {readFile} from 'node:fs/promises';
import {CATALOG_SIZE,catalogStats,getTemplate,renderTemplate,nextTemplateIndex,TEMPLATE_AUDIT_CONTEXT as full} from '../web/template-catalog.mjs';

const normalized=text=>text.replace(/[^\p{L}\p{N}]/gu,'');
test('ready catalog downloads match the audited module and compressed manifests',async()=>{
  const root=new URL('../',import.meta.url),manifest=JSON.parse(await readFile(new URL('data/template-catalog/manifest.json',root),'utf8'));
  const hash=bytes=>createHash('sha256').update(bytes).digest('hex');
  assert.equal(manifest.sourceSha256,hash(await readFile(new URL('web/template-catalog.mjs',root))));
  for(const kind of ['title','description','tags']){
    const item=manifest.kinds[kind],bytes=await readFile(new URL('data/template-catalog/'+item.file,root));
    assert.equal(item.skeletons,CATALOG_SIZE);assert.equal(item.distinctNormalizedFixtureTexts,CATALOG_SIZE);
    assert.equal(bytes.length,item.gzipBytes);assert.equal(hash(bytes),item.gzipSha256);
  }
});
test('catalog distinguishes composed templates from base language and hashtag counts',()=>{
  const stats=catalogStats();
  assert.equal(CATALOG_SIZE,262144);
  for(const key of ['titles','descriptions','tagSets'])assert(stats[key]>200000);
  assert.equal(stats.total,786432);assert.equal(stats.baseLanguageBlocks,128);
  assert.equal(stats.genericTagAtoms,48);assert.equal(stats.mode,'indexed-compositions');
  assert(stats.sparseContextMayReduceDistinctOutputs);
  assert.match(stats.countsDescribe,/not-handwritten/);
});

test('ordinals resolve reproducibly and rotate through a bounded complete index space',()=>{
  for(const kind of ['title','description','tags']){
    assert.deepEqual(getTemplate(kind,452),getTemplate(kind,452));
    assert.equal(getTemplate(kind,-1).index,CATALOG_SIZE-1);
    assert.equal(getTemplate(kind,CATALOG_SIZE+6).index,6);
    assert.equal(getTemplate(kind,Infinity).index,0);
    assert.equal(getTemplate(kind,0).axes.length,6);
    assert(getTemplate(kind,0).axes.every(value=>value>=0&&value<8));
  }
  assert.equal(nextTemplateIndex(CATALOG_SIZE-1),0);
  assert.throws(()=>getTemplate('arbitrary',0),TypeError);
});

test('successive complete-context choices change substantive wording and structure',()=>{
  for(const kind of ['title','description','tags']){
    const values=new Set();
    for(let i=0;i<1024;i++){
      const rendered=renderTemplate(kind,i,full);
      const key=kind==='tags'?rendered.split(/\s+/).sort().join(' '):normalized(rendered);
      assert(!values.has(key),kind+':'+i);values.add(key);
    }
  }
});

test('title only renders supplied facts and omits empty labels with missing fields',()=>{
  for(let i=0;i<80;i++){
    const title=renderTemplate('title',i,{city:'莫斯科',price:'8W/月'});
    assert.match(title,/莫斯科/);assert.match(title,/8W\/月/);
    assert.doesNotMatch(title,/户型|房型|面积|地铁|家具|空调|undefined|\{\{|\[\[/);
    assert(!/^[｜·/,，]|[｜·/,，]$/.test(title));
  }
});

test('description retains every trusted block and registration service without invention',()=>{
  for(const i of [0,1,7,64,1000,CATALOG_SIZE-1]){
    const body=renderTemplate('description',i,full);
    for(const key of ['location','facts','equipment','transport','conditions','service','contact'])assert(body.includes(full[key]),key+':'+i);
    assert.equal(body.split(full.service).length,2);
    assert.doesNotMatch(body,/可带宠物|可带孩子|全新装修|随时入住|押一付一|落地签|保证签证/);
  }
  assert.equal(renderTemplate('description',0,{}),'');
  const only=renderTemplate('description',17,{equipment:'书桌、电脑'});
  assert.match(only,/书桌、电脑/);assert.doesNotMatch(only,/租赁条件|交通|地址|面积/);
});

test('rendered titles preserve price and area and select one confirmed detail',()=>{
  for(let i=0;i<256;i++){
    const title=renderTemplate('title',i,full);
    for(const key of ['city','layout','area','price'])assert(title.includes(full[key]),key+':'+i);
    assert(title.includes(full.metro)||title.includes(full.feature),'one confirmed detail: '+i);
    assert.doesNotMatch(title,/免费|豪华|新装修|近地铁|独立大床|宠物|留学|无中介/);
  }
});

test('a missing detail family falls back to the other confirmed family',()=>{
  for(let i=0;i<100;i++){
    const equipment=renderTemplate('title',i,{city:'莫斯科',feature:'钢琴'});
    assert(equipment.includes('钢琴'));assert.doesNotMatch(equipment,/地铁|交通|出行/);
    const route=renderTemplate('title',i,{city:'莫斯科',metro:'大学站'});
    assert(route.includes('大学站'));assert.doesNotMatch(route,/配有|已配|配置/);
  }
});

test('tag schemes preserve factual and brand tags without inventing eligibility',()=>{
  const factual=['#西南站','#两居室','#书桌','#n1estate'];
  for(let i=0;i<100;i++){
    const tags=renderTemplate('tags',i,{tags:factual});const rows=tags.split(/\s+/);
    for(const tag of factual)assert(rows.includes(tag),tag);
    assert.equal(new Set(rows).size,rows.length);
    assert.doesNotMatch(tags,/宠物|学生|留学|无中介|地铁房|全新|豪华/);
    assert(rows.length>=factual.length+5);
  }
});
