import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
const source=await readFile(new URL('../web/phone.mjs',import.meta.url),'utf8');
const helpers=source.slice(source.indexOf('function phoneDossierAccess()'),source.indexOf('function phoneInitializeCardTools()'));
const card='a'.repeat(32),other='b'.repeat(32);
function fixture(role='owner'){
 const requests=[],downloads=[],mount={classList:{toggle(){}}};
 const state={authEpoch:4,paired:true,detailId:card,access:{role},csrf:'synthetic-csrf'};
 const context={phoneState:state,phoneDossierUI:null,phoneDossierCard:null,phoneDossierRole:null,ph:()=>mount,Error,
  withRequestDeadline:async(ms,operation)=>{assert.equal(ms,120000);return operation(new AbortController().signal,{throwIfAborted(){}});},
  fetch:async(path,options)=>{requests.push({path,options});return {ok:true,status:200,json:async()=>({result_id:other}),blob:async()=>new Blob(['ZIP'],{type:'application/zip'})};},
  phoneAuth:paired=>{state.paired=paired;state.authEpoch++;},phoneEnsurePresentationCard:async()=>state.detailId,cardDownloadBlob:(...args)=>downloads.push(args),phoneToast(){},
  setupDossierTransfer:options=>{context.options=options;return {clear(){},updateAccess(){}};},
  phoneRefreshCards:async()=>requests.push('refresh'),phoneOpenCard:async id=>{requests.push({open:id});state.detailId=id;},Blob,AbortController};
 vm.createContext(context);vm.runInContext(helpers,context);return {context,state,requests,downloads};
}
test('phone dossier owner blob uses bound card, same origin, CSRF, and covers body await',async()=>{
 const {context,state,requests}=fixture();const blob=await context.phoneDossierRequest('dossier-client/'+card,{attachmentIds:[],exportIds:[]},{binary:true});
 assert.equal(blob.type,'application/zip');assert.equal(requests[0].path,'/api/phone/dossier-client/'+card);assert.equal(requests[0].options.credentials,'same-origin');assert.equal(requests[0].options.headers['X-Phone-CSRF'],'synthetic-csrf');
 await assert.rejects(context.phoneDossierRequest('dossier-backup/'+other),/открытой квартире/);
 context.fetch=async()=>({ok:true,status:200,blob:async()=>{state.authEpoch++;return blob;}});
 await assert.rejects(context.phoneDossierRequest('dossier-backup/'+card,undefined,{binary:true}),/Подключение изменилось/);
});
test('editor gets client package only; viewer and unexpected paths fail before a network request',async()=>{
 const {context,state,requests}=fixture('editor');
 await context.phoneDossierRequest('dossier-options/'+card);assert.equal(requests.length,1);
 for(const path of ['dossier-backup/'+card,'dossier-import','photo-duplicates','https://example.invalid/'])await assert.rejects(context.phoneDossierRequest(path),/прав|адрес/);
 assert.equal(requests.length,1);state.access.role='viewer';await assert.rejects(context.phoneDossierRequest('dossier-client/'+card,{}),/прав/);assert.equal(requests.length,1);
});
test('switching card or demoting during response prevents delivery and restore opens new server ID',async()=>{
 const {context,state,requests}=fixture();context.fetch=async()=>({ok:true,status:200,json:async()=>{state.detailId=other;return {};}});
 await assert.rejects(context.phoneDossierRequest('dossier-options/'+card),/другая квартира/);
 state.detailId=card;context.fetch=async()=>({ok:true,status:200,blob:async()=>{state.access.role='editor';return new Blob(['ZIP']);}});
 await assert.rejects(context.phoneDossierRequest('dossier-backup/'+card,undefined,{binary:true}),/прав/);
 state.access.role='owner';context.phoneInitializeDossierTransfer();assert.equal(context.options.access().private,true);await context.options.onRestored({result_id:other});assert.equal(state.detailId,other);assert.deepEqual(requests,['refresh',{open:other}]);
});
