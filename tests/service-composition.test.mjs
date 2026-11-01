import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {generateListing} from '../web/engine.mjs';
import {factLedgerKey,serviceClaimChecklist} from '../web/fact-ledger.mjs';
import {acceptAuthenticatedClientCard,clientPublicationCard,ClientPublicationReviewRequired} from '../web/client-publication.mjs';
import {clearPublicationDataCache,resolvePublicationData} from '../web/publication-model.mjs';

function confirmedCard(){
 const fields={editorIdentity:'composition-fixture',price:'80000',area:'52',pickup:true,features:[]};
 const claim=serviceClaimChecklist(fields).claims.find(row=>row.id==='pickup');
 fields.serviceConfirmations={pickup:{value:claim.value,cardKey:factLedgerKey(fields),actor:'PRIVATE_OPERATOR',at:new Date(Date.now()-1000).toISOString()}};
 return {fields,...generateListing(fields)};
}

test('actual publication resolver composes private receipt validation without leaking its context',()=>{
 const card=confirmedCard(),before=JSON.stringify(card),data=resolvePublicationData(card);
 assert.match(data.body,/免费接送/);assert(!JSON.stringify(data).includes('PRIVATE_OPERATOR'));
 assert(!JSON.stringify(data).includes('serviceConfirmations'));assert.equal(JSON.stringify(card),before);
 const saved={...card,fields:{...card.fields,resultId:'a'.repeat(32)}};
 assert.match(resolvePublicationData(saved).body,/免费接送/);
 const {resultId,...presentationFields}=saved.fields;
 assert.match(resolvePublicationData({...saved,fields:presentationFields}).body,/免费接送/);
 const projected=clientPublicationCard(card);
 assert.doesNotThrow(()=>clientPublicationCard(projected));
 projected.body+='\n免费协助抵达手续。';
 assert.throws(()=>clientPublicationCard(projected),ClientPublicationReviewRequired);
 const now=Date.now;try{Date.now=()=>now()+31*86400000;assert.throws(()=>resolvePublicationData(card),ClientPublicationReviewRequired);}finally{Date.now=now;clearPublicationDataCache();}
});

test('JSON policy flags cannot authorize forged text, receipt expiry, or a changed service scope',()=>{
 const forged={fields:{editorIdentity:'forged-service',price:'80000',pickup:true},body:'免费接送到家。',publicationPolicyVersion:'client-publication-v2',serviceAuthorization:{version:1,expiresAt:new Date(Date.now()+86400000).toISOString()}};
 assert.throws(()=>clientPublicationCard(forged),ClientPublicationReviewRequired);
 assert.throws(()=>resolvePublicationData(forged),ClientPublicationReviewRequired);
 const card=confirmedCard(),copy=JSON.parse(JSON.stringify(clientPublicationCard(card)));
 copy.fields.pickup=false;
 assert.throws(()=>clientPublicationCard(copy),ClientPublicationReviewRequired);
});

test('real server public projection supports exact authenticated clones across designer bundles and expires',async()=>{
 const source=`import json,sys,tempfile\nfrom pathlib import Path\nfrom datetime import datetime,timezone,timedelta\nsys.path.insert(0,'cli')\nimport local_server,phone_relay\nfrom service_claims import service_card_key\nf={'editorIdentity':'authenticated-composition','price':'80000','pickup':True,'photos':[]}\nf['serviceConfirmations']={'pickup':{'value':{'included':True,'luggage':False},'cardKey':service_card_key(f),'actor':'PRIVATE_SERVER_OPERATOR','at':(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()}}\nwith tempfile.TemporaryDirectory() as d:\n i=local_server.save_result(Path(d),{'fields':f,'titles':['公寓'],'body':'免费接送到入住地点。','tags':''})\n print(json.dumps(phone_relay.public_card(Path(d),i),ensure_ascii=False))\n`;
 const result=spawnSync('python',['-c',source],{cwd:new URL('..',import.meta.url),encoding:'utf8',timeout:10000});
 assert.equal(result.status,0,result.stderr);const server=JSON.parse(result.stdout);
 assert(!JSON.stringify(server).includes('PRIVATE_SERVER_OPERATOR'));assert(!server.fields.serviceConfirmations);
 assert(Number.isFinite(Date.parse(server.serviceAuthorization?.expiresAt)));
 assert.equal(acceptAuthenticatedClientCard(server),server);
 assert.match(resolvePublicationData(server).body,/免费接送/);
 const designer=await import('../web/client-publication.mjs?separate-designer-bundle');
 const exactClone=JSON.parse(JSON.stringify(server));
 assert.doesNotThrow(()=>designer.clientPublicationCard(exactClone));
 assert.match(resolvePublicationData(exactClone).body,/免费接送/);
 const edited=JSON.parse(JSON.stringify(server));edited.body+='\n免费协助抵达手续。';
 assert.throws(()=>designer.clientPublicationCard(edited),{code:'PUBLICATION_REVIEW_REQUIRED'});
 const originalNow=Date.now;try{Date.now=()=>Date.parse(server.serviceAuthorization.expiresAt)+1;assert.throws(()=>designer.clientPublicationCard(exactClone),{code:'PUBLICATION_REVIEW_REQUIRED'});assert.throws(()=>resolvePublicationData(server),ClientPublicationReviewRequired);}finally{Date.now=originalNow;clearPublicationDataCache();}
});
