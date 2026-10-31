import test from 'node:test';
import assert from 'node:assert/strict';
import {setupCardArchive,archiveValidateAttachmentFile,archiveFormatAttachmentBytes,caMaxAttachmentBytes} from '../web/card-archive.mjs';

class ArchiveTestNode {
  constructor(tag) {this.tagName=tag;this.children=[];this.attributes={};this.value='';this.files=[];this.classList={add(){},toggle(){}};}
  append(...children) {for(const child of children){child.parentNode=this;this.children.push(child);}}
  replaceChildren(...children) {this.children=[];this.append(...children);}
  setAttribute(name,value) {this.attributes[name]=value;}
  querySelectorAll(tag) {return this.children.flatMap(child=>[...(child.tagName===tag?[child]:[]),...child.querySelectorAll(tag)]);}
  focus() {}
  remove() {if(this.parentNode)this.parentNode.children=this.parentNode.children.filter(child=>child!==this);}
}
const archiveDeferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
const archiveTick=async()=>{await Promise.resolve();await Promise.resolve();};
const archivePayload=(notes='',attachments=[])=>({status:'active',notes,attachments});
function archiveHarness(api,{initialId='first',ensureCard}={}) {
  const previous=globalThis.document;
  globalThis.document={createElement:tag=>new ArchiveTestNode(tag)};
  const mount=new ArchiveTestNode('div');let id=initialId;
  const control=setupCardArchive({mount,getCardId:()=>id,api,ensureCard});
  const find=label=>mount.querySelectorAll('textarea').concat(mount.querySelectorAll('select')).find(node=>node.attributes['aria-label']===label);
  return {mount,control,find,setId:value=>{id=value;},cleanup(){control.destroy();globalThis.document=previous;}};
}

test('attachment selection accepts large 3D files and blocks empty, oversized and unsupported input',()=>{
  assert.equal(archiveValidateAttachmentFile({name:'скан квартиры.USDZ',size:caMaxAttachmentBytes}).size,caMaxAttachmentBytes);
  assert.equal(archiveValidateAttachmentFile({name:'план.dxf',size:8}).name,'план.dxf');
  assert.throws(()=>archiveValidateAttachmentFile({name:'план.pdf',size:0}),/пустой/);
  assert.throws(()=>archiveValidateAttachmentFile({name:'план.pdf',size:caMaxAttachmentBytes+1}),/40 МБ/);
  assert.throws(()=>archiveValidateAttachmentFile({name:'договор.pdf.html',size:10}),/Поддерживаются/);
  assert.throws(()=>archiveValidateAttachmentFile({name:'файл.zip',size:NaN}),/размер/);
  assert.equal(archiveFormatAttachmentBytes(1024),'1 КБ');
  assert.equal(archiveFormatAttachmentBytes(3*1024*1024),'3 МБ');
});

test('background refresh retains unsaved private notes while updating attachment metadata',async()=>{
  let response=archivePayload('Сохранённая заметка');
  const harness=archiveHarness(async()=>response);
  try {
    await archiveTick();
    const notes=harness.find('Личные заметки квартиры');
    assert.equal(notes.value,'Сохранённая заметка');
    notes.value='Новый договор — пока не сохранён';notes.oninput();
    response=archivePayload('Другая серверная версия',[{id:'f',name:'договор.pdf',category:'contract',bytes:100,created:1720000000}]);
    await harness.control.refresh();
    assert.equal(notes.value,'Новый договор — пока не сохранён');
    assert.ok(harness.mount.querySelectorAll('strong').some(node=>node.textContent==='договор.pdf'));
  } finally {harness.cleanup();}
});

test('response from previous card cannot reveal its notes after switching cards',async()=>{
  const first=archiveDeferred();
  const harness=archiveHarness(path=>path.endsWith('/first')?first.promise:Promise.resolve(archivePayload('Вторая квартира')));
  try {
    harness.setId('second');await harness.control.refresh();
    first.resolve(archivePayload('Личный договор первой квартиры'));
    await archiveTick();
    assert.equal(harness.find('Личные заметки квартиры').value,'Вторая квартира');
  } finally {harness.cleanup();}
});

test('logout clears private contents and rejects pending successful responses',async()=>{
  const request=archiveDeferred();const harness=archiveHarness(()=>request.promise);
  try {
    harness.control.clear();request.resolve(archivePayload('Секретная заметка',[{name:'секретный-договор.pdf'}]));
    await archiveTick();
    assert.equal(harness.find('Личные заметки квартиры').value,'');
    assert.equal(harness.mount.querySelectorAll('strong').length,0);
  } finally {harness.cleanup();}
});

test('older GET completing after successful save cannot roll notes back',async()=>{
  const stale=archiveDeferred();let getCount=0;
  const harness=archiveHarness((_path,data)=>data?Promise.resolve(archivePayload(data.notes)):++getCount===1?Promise.resolve(archivePayload('Старая заметка')):stale.promise);
  try {
    await archiveTick();const pending=harness.control.refresh();
    const notes=harness.find('Личные заметки квартиры');notes.value='Новое сохранённое значение';notes.oninput();
    await harness.mount.querySelectorAll('form')[0].onsubmit({preventDefault(){}});
    stale.resolve(archivePayload('Старая заметка'));await pending;
    assert.equal(notes.value,'Новое сохранённое значение');
  } finally {harness.cleanup();}
});

test('late save failure from an old card cannot replace current card status',async()=>{
  const save=archiveDeferred();
  const harness=archiveHarness((path,data)=>data?save.promise:Promise.resolve(archivePayload(path.endsWith('/first')?'Первая':'Вторая')));
  try {
    await archiveTick();const pending=harness.mount.querySelectorAll('form')[0].onsubmit({preventDefault(){}});await archiveTick();
    harness.setId('second');await harness.control.refresh();
    save.reject(new Error('Ошибка первой квартиры'));await pending;
    assert.equal(harness.find('Личные заметки квартиры').value,'Вторая');
    assert.ok(!harness.mount.querySelectorAll('p').some(node=>node.textContent==='Ошибка первой квартиры'));
  } finally {harness.cleanup();}
});

test('existing archive metadata cannot be overwritten before loading, and failure offers a retry',async()=>{
  const request=archiveDeferred();let calls=0,writes=0;
  const harness=archiveHarness((_path,data)=>{if(data)writes++;return ++calls===1?request.promise:Promise.resolve({status:'rented',notes:'Действующий договор',attachments:[]});});
  try {
    const notes=harness.find('Личные заметки квартиры'),status=harness.find('Статус квартиры');
    const form=harness.mount.querySelectorAll('form')[0],save=form.querySelectorAll('button')[0];
    assert.equal(notes.disabled,true);assert.equal(status.disabled,true);assert.equal(save.disabled,true);
    await form.onsubmit({preventDefault(){}});
    assert.equal(writes,0);
    request.reject(new Error('Mac недоступен'));await archiveTick();
    const retry=harness.mount.querySelectorAll('button').find(node=>node.attributes['aria-label']==='Повторить загрузку архива квартиры');
    assert.equal(retry.hidden,false);assert.equal(retry.disabled,false);
    assert.equal(notes.disabled,true);assert.equal(save.disabled,true);
    await retry.onclick();
    assert.equal(notes.value,'Действующий договор');assert.equal(status.value,'rented');
    assert.equal(notes.disabled,false);assert.equal(save.disabled,false);assert.equal(retry.hidden,true);
  } finally {harness.cleanup();}
});

test('new unsaved card accepts notes and preserves them when ensureCard creates its ID',async()=>{
  const writes=[];let harness;
  harness=archiveHarness(async(_path,data)=>{writes.push(data);return archivePayload(data.notes);},{initialId:null,ensureCard:async()=>{harness.setId('created');return 'created';}});
  try {
    const notes=harness.find('Личные заметки квартиры');
    assert.equal(notes.disabled,false);
    notes.value='Новая квартира — личная заметка';notes.oninput();
    await harness.mount.querySelectorAll('form')[0].onsubmit({preventDefault(){}});
    assert.equal(writes.length,1);assert.equal(writes[0].notes,'Новая квартира — личная заметка');
    assert.equal(notes.value,'Новая квартира — личная заметка');assert.equal(notes.disabled,false);
  } finally {harness.cleanup();}
});
