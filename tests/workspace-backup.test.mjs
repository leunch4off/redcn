import test from 'node:test';
import assert from 'node:assert/strict';
import {validateWorkspaceBackupFile,workspaceBackupSummary,WORKSPACE_INSPECT_MAX_BYTES} from '../web/workspace-backup.mjs';
test('workspace inspection bounds are explicit and no restore path is accepted',()=>{
 const file={name:'private.ZIP',size:30};assert.equal(validateWorkspaceBackupFile(file),file);
 for(const value of [null,{name:'a.html',size:2},{name:'a.zip',size:0},{name:'a.zip',size:WORKSPACE_INSPECT_MAX_BYTES+1}])assert.throws(()=>validateWorkspaceBackupFile(value));
});
test('only complete integrity receipts are shown as verified',()=>{
 const value={version:1,type:'redcn-private-workspace',sha256:'a'.repeat(64),cards:2,photos:19,photoVariantDecisions:1,attachments:1,exports:3,dictionaryRules:8,learningExamples:4,bytes:1234,requiresNewWorkspace:true,devicesMustPairAgain:true,clientMaterialsRequireRegeneration:true};
 assert.match(workspaceBackupSummary(value),/19 фото/u);assert.match(workspaceBackupSummary(value),/SHA-256/u);
 for(const invalid of [{...value,photos:-1},{...value,cards:2.5},{...value,sha256:'bad'},{...value,devicesMustPairAgain:false},null])assert.throws(()=>workspaceBackupSummary(invalid));
});
