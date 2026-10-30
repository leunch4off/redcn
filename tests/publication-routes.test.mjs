import test from 'node:test';
import assert from 'node:assert/strict';
import {defaultPublicationDesign,normalizePublicationDesign,resolvePublicationData,publicationNodeLink} from '../web/publication-model.mjs';
import {buildPublicationHtml} from '../web/publication-render.mjs';

const fields={price:68000,area:41,latitude:55.63,longitude:37.32,address:'Москва, улица Пример, 8',metro:'Рассказовка',universityRoutes:[{name:'МГУ',latitude:55.7,longitude:37.53,time:'45–60',mode:'public',transport:'Автобус 510 → метро',stops:[{name:'Остановка у дома',latitude:55.631,longitude:37.321},{name:'Пересадка',latitude:55.68,longitude:37.46},{name:'Кампус',latitude:55.7,longitude:37.53}]}]};
const extra=/^route[12](?:Distance|Transport|Yandex|Google|OSM)$/;

test('publication distance transport and every map action use confirmed route fields',()=>{
  const d=resolvePublicationData({fields});assert.match(d.route1Distance,/直线距离约.*公里/);assert.match(d.route1Transport,/510/);assert.equal(d.route1Time,'约45–60分钟');
  for(const suffix of ['Yandex','Google','OSM'])assert.match(publicationNodeLink({bind:'route1'+suffix},d),/^https:\/\//);
  const y=new URL(publicationNodeLink({bind:'route1Yandex'},d));assert.equal(y.searchParams.get('rtext'),'55.63,37.32~55.7,37.53');
  const g=new URL(publicationNodeLink({bind:'route1Google'},d));assert.equal(g.searchParams.get('travelmode'),'transit');
  const stop=new URL(publicationNodeLink({bind:'route1Stop1'},d));assert.equal(stop.searchParams.get('pt'),'37.321,55.631');
  const html=buildPublicationHtml({fields});assert.match(html,/data-bind="route1Yandex"[^>]*><a class="publication-action" href="https:\/\/yandex.ru/);assert.match(html,/data-bind="route1Google"[^>]*><a class="publication-action" href="https:\/\/www.google.com/);assert.ok(html.includes(d.route1Distance));assert.ok(html.includes('510'));
});

test('unconfirmed destination does not acquire invented distance buses or a main-campus route',()=>{
  const d=resolvePublicationData({fields:{university:'МГУ'}});assert.equal(d.route1Distance,'');assert.equal(d.route1Transport,'');assert.match(d.route1.time,/待确认/);
  assert.equal(new URL(publicationNodeLink({bind:'route1Google'},d)).pathname,'/maps/search/');
  const html=buildPublicationHtml({fields:{university:'МГУ'}});assert.ok(!html.includes('data-bind="route1Distance"'));assert.ok(!html.includes('data-bind="route1Transport"'));
});

test('old v2 layouts get only additive route widgets and preserve every configured existing node',()=>{
  const old=defaultPublicationDesign();delete old.revision;
  for(const id of ['pdfLocation','outro'])old.surfaces[id].nodes=old.surfaces[id].nodes.filter(n=>!extra.test(n.bind));
  const title=old.surfaces.pdfLocation.nodes.find(n=>n.id==='route-1-title');title.x=1050;title.style.fontSize=21;title.visible=false;
  const time=old.surfaces.pdfLocation.nodes.find(n=>n.id==='route-1-time');time.w=472;
  const originals=structuredClone(old.surfaces),result=normalizePublicationDesign(old);assert.equal(result.revision,2);
  for(const id of ['pdfLocation','outro'])for(const original of originals[id].nodes)assert.deepEqual(result.surfaces[id].nodes.find(n=>n.id===original.id),original);
  assert.ok(result.surfaces.pdfLocation.nodes.some(n=>n.bind==='route1Google'));assert.deepEqual(normalizePublicationDesign(result),result);
});

test('revision 2 keeps deliberately removed route widgets removed',()=>{
  const d=defaultPublicationDesign();for(const page of Object.values(d.surfaces))page.nodes=page.nodes.filter(n=>!extra.test(n.bind));
  const n=normalizePublicationDesign(d);assert.ok(Object.values(n.surfaces).every(p=>p.nodes.every(row=>!extra.test(row.bind))));
});

test('factory route actions fit the free gap and older tiny route boxes remain bounded and idempotent',()=>{
  const d=defaultPublicationDesign();for(const id of ['pdfLocation','outro']){const nodes=d.surfaces[id].nodes,buttons=nodes.filter(n=>/^route1(?:Yandex|Google|OSM)$/.test(n.bind)),next=nodes.find(n=>n.bind==='route2'&&n.type==='rect');assert.ok(buttons.every(n=>n.y+n.h<=next.y),'Navigation actions cannot cover the next route');}
  const old=defaultPublicationDesign();delete old.revision;for(const id of ['pdfLocation','outro'])old.surfaces[id].nodes=old.surfaces[id].nodes.filter(n=>!extra.test(n.bind));
  const anchor=old.surfaces.pdfLocation.nodes.find(n=>n.bind==='route1'&&n.type==='rect');Object.assign(anchor,{w:4,h:2});
  const n=normalizePublicationDesign(old);for(const p of Object.values(n.surfaces))for(const e of p.nodes){assert.ok(e.w>=4&&e.h>0);assert.ok(e.x>=0&&e.y>=0&&e.x+e.w<=p.width+1e-8&&e.y+e.h<=p.height+1e-8);}
  assert.deepEqual(normalizePublicationDesign(n),n);
});
