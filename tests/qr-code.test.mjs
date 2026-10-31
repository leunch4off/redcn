import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {pairingQrMatrix,pairingQrSvg,renderPairingQr} from '../web/qr-code.mjs';

const links=[
  'http://192.168.1.23:8767/phone/pair#A42bf60ee3d179dc2978877cabb1bc0c',
  'http://mac-mini.local:8767/phone/pair#подключение-手机-2026',
];

test('local pairing QR uses medium correction and a four-module quiet zone',()=>{
  const qr=pairingQrMatrix(links[0]),svg=pairingQrSvg(links[0]);
  assert.equal(qr.errorCorrection,'M');assert.equal(qr.quietZone,4);
  assert(qr.size>=21&&(qr.size-17)%4===0);assert.equal(qr.modules.length,qr.size);
  assert(qr.modules.every(row=>row.length===qr.size&&row.every(v=>typeof v==='boolean')));
  assert.equal(svg.size,qr.size+8);assert(svg.path.startsWith('M4,4'));
  assert.throws(()=>pairingQrMatrix(''),TypeError);
  assert.throws(()=>pairingQrMatrix('a'.repeat(1801)),RangeError);
});

test('QR renders as local scanner-friendly SVG with no embedded pairing URL',()=>{
  const doc={createElementNS:(_ns,name)=>({name,attrs:{},style:{},children:[],setAttribute(k,v){this.attrs[k]=v;},append(...nodes){this.children.push(...nodes);}})};
  const container={ownerDocument:doc,children:[],replaceChildren(...nodes){this.children=nodes;}};
  const svg=renderPairingQr(container,links[0]);
  assert.equal(container.children[0],svg);assert.equal(svg.name,'svg');assert.equal(svg.attrs.width,'220');
  assert.equal(svg.attrs.height,'220');assert.equal(svg.attrs['shape-rendering'],'crispEdges');
  assert.equal(svg.children[0].attrs.fill,'#fff');assert.equal(svg.children[1].attrs.fill,'#000');
  assert(!JSON.stringify(svg).includes(links[0]));
});

const python=process.env.CODEX_PRIMARY_RUNTIME_PYTHON||'python3';
const decoder=spawnSync(python,['-c','import PIL,zxingcpp'],{encoding:'utf8'});
test('independent ZXing decoder recovers complete ASCII and Unicode pairing data',{skip:decoder.status!==0},()=>{
  const input=links.map(text=>({text,qr:pairingQrMatrix(text)}));
  const script=`import sys,json
from PIL import Image,ImageDraw
import zxingcpp
results=[]
for row in json.load(sys.stdin):
 qr=row['qr']; scale=8; width=(qr['size']+8)*scale
 image=Image.new('L',(width,width),255);draw=ImageDraw.Draw(image)
 for y,line in enumerate(qr['modules']):
  for x,dark in enumerate(line):
   if dark:draw.rectangle(((x+4)*scale,(y+4)*scale,(x+5)*scale-1,(y+5)*scale-1),fill=0)
 result=zxingcpp.read_barcode(image)
 assert result is not None,'Independent decoder could not read QR'
 assert result.text==row['text'],(result.text,row['text'])
 results.append(result.text)
print(json.dumps(results,ensure_ascii=False))`;
  const result=spawnSync(python,['-c',script],{input:JSON.stringify(input),encoding:'utf8'});
  assert.equal(result.status,0,result.stderr);assert.deepEqual(JSON.parse(result.stdout),links);
});
