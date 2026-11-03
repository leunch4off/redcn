import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
import {isAutomaticPhotoObservation} from '../web/photo-observation-policy.mjs';

const originalId='a'.repeat(64);
const observation=(id,text)=>({id,status:'auto-visible',autoEligible:true,originalId,photoId:originalId,confidence:.96,
  evidence:[{kind:'model-visual-observation',provider:'ollama',model:'qwen3-vl:2b-instruct-q4_K_M',
    source:'local-photo',originalId,photoId:originalId,confidence:.96,directlyVisible:true,text}]});

// Expected decisions exercise the policy's meaning rather than its expression
// construction. Non-ASCII letters, digits and underscores count as word parts.
const cases=[
  ['oven','Духовой шкаф со стеклянной дверцей и ручкой',true],
  ['oven','Духовой шкаф стоит у стены в кухне',true],
  ['oven','烤箱有透明门和控制面板，位于厨房橱柜中',true],
  ['oven','The oven stands beneath the window',true],
  ['oven','A willow branch stands beside the oven',true],
  ['oven','No visible oven in this photograph',false],
  ['oven','The oven is not visible in the kitchen',false],
  ['oven','The oven will be installed in this cabinet',false],
  ['oven','An oven is going to be installed here',false],
  ['oven','Probably an oven behind the cabinet door',false],
  ['oven','Нет духового шкафа на фотографии',false],
  ['oven','Духовой шкаф не виден на фотографии',false],
  ['oven','Духовку купят в следующем месяце',false],
  ['oven','Планируется установить духовку',false],
  ['oven','Духовой шкаф предположительно скрыт за дверцей',false],
  ['oven','厨房没有烤箱，橱柜门全部关闭',false],
  ['oven','计划购买一个烤箱，放在厨房里',false],
  ['oven','烤箱將在下週安裝到厨房中',false],
  ['tv','A TV screen mounted above the cabinet',true],
  ['tv','🛋️ TV screen mounted above the cabinet',true],
  ['tv','A supertv screen mounted above the cabinet',false],
  ['tv','An éTVé screen mounted above the cabinet',false],
  ['tv','An _TV_ screen mounted above the cabinet',false],
  ['tv','A 1TV2 screen mounted above the cabinet',false],
  ['tv','这是TV屏幕，安装在柜子上方的墙上',false],
  ['tv','电视有黑色边框，安装在柜子上方的墙上',true],
  ['tv','没有电视，柜子上方的墙面是空的',false],
  ['iron','A visible iron resting on the ironing board',true],
  ['iron','An irony sculpture resting on the cabinet',false],
  ['ac','A black AC unit mounted above the window',true],
  ['ac','A black unit mounted above the window',false],
  ['desk','Writing desk with two drawers beside the window',true],
  ['desk','书桌靠近窗户，带有两个抽屉和独立桌面',true],
  ['desk','书桌可能隐藏在房间角落的窗帘后面',false],
];

test('photo evidence boundaries retain Russian, Chinese and English decisions',()=>{
  for(const [id,text,expected]of cases)assert.equal(isAutomaticPhotoObservation(observation(id,text)),expected,text);
});

test('eager photo policy starts without lookbehind or String.replaceAll',async()=>{
  const source=await readFile(new URL('../web/photo-observation-policy.mjs',import.meta.url),'utf8');
  let compiled=0;
  const LegacyRegExp=new Proxy(RegExp,{construct(target,args){
    assert.equal(/\(\?<[/=!]/.test(String(args[0])),false,'Legacy WebKit must not receive lookbehind');
    compiled++;return Reflect.construct(target,args);
  }});
  const sandbox={RegExp:LegacyRegExp};
  vm.runInNewContext('String.prototype.replaceAll=undefined;\n'+source.replace(/^export /gm,'')+'\nglobalThis.check=isAutomaticPhotoObservation;',sandbox);
  assert.ok(compiled>10,'The eager policy expressions were actually compiled');
  for(const [id,text,expected]of cases)assert.equal(sandbox.check(observation(id,text)),expected,text);
});
