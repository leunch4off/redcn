import test from 'node:test';
import assert from 'node:assert/strict';
import {validateDossierFile,dossierClientSelection,DOSSIER_MAX_BYTES} from '../web/dossier-transfer.mjs';
test('private upload bounds reject wrong extension empty or oversized input',()=>{
 assert.equal(validateDossierFile({name:'my.zip',size:100}).size,100);
 for(const file of [null,{name:'x.txt',size:10},{name:'x.zip',size:0},{name:'x.zip',size:DOSSIER_MAX_BYTES+1}])assert.throws(()=>validateDossierFile(file));
});
test('client files are empty by default and selection never grants contracts or stale exports',()=>{
 const options={attachments:[{id:'a'.repeat(32),category:'floorplan'},{id:'b'.repeat(32),category:'contract'}],exports:[{id:'c'.repeat(32),requiresRegeneration:false},{id:'d'.repeat(32),requiresRegeneration:true}]};
 assert.deepEqual(dossierClientSelection(options),{attachmentIds:[],exportIds:[]});
 assert.deepEqual(dossierClientSelection(options,{attachmentIds:['a'.repeat(32)],exportIds:['c'.repeat(32)]}),{attachmentIds:['a'.repeat(32)],exportIds:['c'.repeat(32)]});
 for(const selected of [{attachmentIds:['b'.repeat(32)]},{exportIds:['d'.repeat(32)]},{attachmentIds:['e'.repeat(32)]},{attachmentIds:[{}]}])assert.throws(()=>dossierClientSelection(options,selected));
});
