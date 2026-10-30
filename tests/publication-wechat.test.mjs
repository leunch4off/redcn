import test from 'node:test';
import assert from 'node:assert/strict';
import {defaultPublicationDesign,normalizePublicationDesign,resolvePublicationData,publicationNodeText,publicationNodeLink,publicationWeChat,safePublicationLink,redactPublicationPhones} from '../web/publication-model.mjs';
import {buildPublicationHtml} from '../web/publication-render.mjs';
import {buildPresentationHtml} from '../web/presentation-studio.mjs';

const card={titles:['中文公寓'],body:'月租68,000卢布。联系手机: +7 (999) 123-45-67\n面积41㎡，2026-10-31。',fields:{price:68000,area:41,rooms:1}};

test('Ciliev uses explicit WeChat contact bindings and never a factory telephone',()=>{
  const d=defaultPublicationDesign();assert.equal(d.brand.wechatLabel,'微信联系');
  assert.ok(d.surfaces.pdfOverview.nodes.some(n=>n.bind==='wechatLabel'));
  assert.ok(d.surfaces.pdfOverview.nodes.some(n=>n.bind==='wechat'));
  assert.ok(!Object.values(d.surfaces).some(p=>p.nodes.some(n=>n.bind==='phone'||n.bind==='email')));
  assert.equal(publicationWeChat({wechatId:'ciliev',wechatUrl:''}).text,'微信: ciliev');
  assert.equal(publicationNodeLink({bind:'wechat'},resolvePublicationData({},d)),'');
});

test('a provided WeChat HTTPS link becomes an escaped active button, not a fabricated PM URI',()=>{
  const d=defaultPublicationDesign();Object.assign(d.brand,{wechatId:'ciliev_moscow',wechatUrl:'https://weixin.qq.com/r/official-contact?from=pdf&scene=1',wechatLabel:'微信咨询',phone:'+7 (999) 111-22-33',email:'owner@example.com',contact:'Телефон +7 (999) 222-33-44'});
  const source=JSON.stringify(d),data=resolvePublicationData(card,d),html=buildPublicationHtml(card,d);
  assert.equal(data.wechatId,'ciliev_moscow');assert.equal(data.phone,'');assert.equal(data.email,'');
  assert.equal(publicationNodeLink({bind:'wechatLabel'},data),'https://weixin.qq.com/r/official-contact?from=pdf&scene=1');
  assert.match(html,/<a class="publication-action" href="https:\/\/weixin.qq.com\/r\/official-contact\?from=pdf&amp;scene=1">微信咨询<\/a>/);
  assert.ok(html.includes('微信: ciliev_moscow'));assert.ok(!html.includes('weixin://'));assert.ok(!html.includes('tel:'));
  for(const text of ['111-22-33','222-33-44','123-45-67','owner@example.com'])assert.ok(!html.includes(text));
  assert.equal(JSON.stringify(d),source,'Publishing must not mutate private branding');
});

test('ID-only contacts stay selectable text and old WeChat captions migrate without inventing a link',()=>{
  const d=defaultPublicationDesign();d.brand.contact='WeChat: CILIEV-TEST\nТелефон: +7 999 123 45 67';
  const data=resolvePublicationData(card,d),html=buildPublicationHtml(card,d);
  assert.equal(data.wechat,'微信: CILIEV-TEST');assert.equal(data.wechatUrl,'');assert.ok(html.includes('微信: CILIEV-TEST'));
  assert.ok(!html.includes('class="publication-action"'));assert.ok(!html.includes('data-node-id="agent-wechat-button"'));
  const plain=resolvePublicationData(card,{...d,brand:{...d.brand,contact:'Call me +7 999 123 45 67'}});assert.equal(plain.contact,'');
});

test('phones disappear from manual/old phone nodes and body while prices dates and identifiers survive',()=>{
  const d=defaultPublicationDesign(),n=d.surfaces.pdfOverview.nodes.find(n=>n.type==='text');
  d.brand.phone='+7 999 123 45 67';d.surfaces.pdfOverview.nodes.push({...structuredClone(n),id:'old-phone',bind:'phone'},{...structuredClone(n),id:'literal-phone',bind:'',text:'Телефон: +7 (999) 987-65-43. 68,000卢布 / 41㎡'});
  const data=resolvePublicationData(card,d),html=buildPublicationHtml(card,d);
  assert.equal(publicationNodeText({bind:'',text:'联系 +86 138 1234 5678'},data),'联系 ');
  assert.ok(!html.includes('data-node-id="old-phone"'));assert.ok(!html.includes('987-65-43'));assert.ok(!html.includes('123-45-67'));
  assert.ok(html.includes('68,000'));assert.ok(html.includes('2026-10-31'));assert.ok(html.includes('41㎡'));
  assert.equal(redactPublicationPhones('微信: wxid_89991234567; 月租68,000; 2026-10-31'),'微信: wxid_89991234567; 月租68,000; 2026-10-31');
});

test('contact URLs reject active schemes credentials and local hosts without fetching anything',()=>{
  for(const url of ['javascript:alert(1)','data:text/html,a','weixin://dl/chat?ciliev','file:///etc/passwd','http://weixin.qq.com/r/x','https://user:pass@weixin.qq.com/r/x','https://127.0.0.1/x','https://[::1]/x','https://localhost/x','https://foo.local/x','https://a.test/x','https://safe.com/\nheader'])assert.equal(safePublicationLink(url),'',url);
  assert.equal(safePublicationLink(' https://weixin.qq.com/r/contact '),'https://weixin.qq.com/r/contact');
  const d=defaultPublicationDesign();d.brand.wechatUrl='javascript:alert(1)';assert.equal(normalizePublicationDesign(d).brand.wechatUrl,'');
});

test('explicit legacy consumers also suppress arbitrary phone contacts while keeping WeChat ID text',()=>{
  const raw='Contact +7 999 123 45 67';const html=buildPresentationHtml(card,{version:1,contact:raw});assert.ok(!html.includes('123 45 67'));assert.ok(!html.includes('123-45-67'));
  const wechat=buildPresentationHtml(card,{version:1,contact:'WeChat: CILIEV-TEST'});assert.ok(wechat.includes('CILIEV-TEST'));assert.ok(!wechat.includes('tel:'));
});

test('redaction does not consume a rent amount immediately after a phone or a large money value',()=>{assert.equal(redactPublicationPhones('联系 +7 999 123 45 67 68,000卢布'), '联系  68,000卢布');assert.equal(redactPublicationPhones('价格 89991234567 卢布'), '价格 89991234567 卢布');});

test('phone redaction handles fullwidth copies and narrow nonbreaking spaces without altering fullwidth prices',()=>{assert.equal(redactPublicationPhones('联系 ＋７（９９９）１２３－４５－６７'), '联系 ');assert.equal(redactPublicationPhones('价格８９９９１２３４５６７卢布'), '价格８９９９１２３４５６７卢布');assert.ok(!redactPublicationPhones('联系 +7\u202f999\u202f123\u202f45\u202f67').includes('999'));});

test('URL-only configuration has one visible CTA without a fake login or duplicate caption',()=>{
  const d=defaultPublicationDesign();d.brand.wechatUrl='https://weixin.qq.com/r/contact';
  const data=resolvePublicationData({},d),html=buildPublicationHtml({},d);assert.equal(data.wechat,'');assert.equal(data.wechatId,'');assert.equal(data.wechatLabel,'微信联系');
  assert.ok(!html.includes('data-node-id="agent-contact"'));assert.equal((html.match(/>微信联系<\/a>/g)||[]).length,1);
});

test('old v2 contact geometry gains an additive CTA while new layouts respect deliberate CTA deletion',()=>{
  const old=defaultPublicationDesign();delete old.revision;
  old.surfaces.pdfOverview.nodes=old.surfaces.pdfOverview.nodes.filter(n=>!['wechat','wechatLabel'].includes(n.bind));
  const seed=old.surfaces.pdfOverview.nodes.find(n=>n.id==='agent-name'),phone={...structuredClone(seed),id:'old-telephone',bind:'phone',x:95,y:712,w:400,h:45};old.surfaces.pdfOverview.nodes.push(phone);
  old.brand.phone='+7 999 123 45 67';old.brand.wechatUrl='https://weixin.qq.com/r/contact';const n=normalizePublicationDesign(old);
  assert.deepEqual(n.surfaces.pdfOverview.nodes.find(v=>v.id==='old-telephone'),phone);assert.equal(n.brand.phone,old.brand.phone);
  const cta=n.surfaces.pdfOverview.nodes.find(v=>v.bind==='wechatLabel'&&v.type==='rect');assert.equal(cta.x,phone.x);assert.equal(cta.y,phone.y);assert.equal(cta.w,phone.w);assert.equal(cta.h,phone.h);
  assert.deepEqual(normalizePublicationDesign(n),n);
  n.surfaces.pdfOverview.nodes=n.surfaces.pdfOverview.nodes.filter(v=>v.bind!=='wechatLabel');assert.ok(!normalizePublicationDesign(n).surfaces.pdfOverview.nodes.some(v=>v.bind==='wechatLabel'));
});

test('legacy WeChat ID captions do not turn an ID label or a URL prefix into a fake account',()=>{assert.equal(publicationWeChat({contact:'WeChat ID: ciliev'}).id,'ciliev');assert.equal(publicationWeChat({contact:'微信号：ciliev'}).id,'ciliev');assert.equal(publicationWeChat({contact:'WeChat: https://weixin.qq.com/r/x'}).id,'');});
