import test from 'node:test';
import assert from 'node:assert/strict';
import {phoneRoleCapabilities,accessSessionsSnapshot,accessRoleChange} from '../web/access-roles.mjs';
test('legacy or malformed state never pretends server enforces roles',()=>{
 for(const state of [{},{role:'admin',capabilities:{editCards:true}},{role:'owner'},{role:'viewer',capabilities:[]}]){const result=phoneRoleCapabilities(state);assert.equal(result.supported,false);assert.equal(result.editCards,false);}
 const viewer=phoneRoleCapabilities({role:'viewer',capabilities:{readCards:true,manageAccess:true}});assert.equal(viewer.supported,true);assert.equal(viewer.readCards,true);assert.equal(viewer.editCards,false);assert.equal(viewer.manageAccess,false);
});
test('session UI creates explicit compare-and-swap grants without credentials',()=>{
 const input={revision:7,sessions:[{id:'a'.repeat(64),role:'editor',label:'Phone',expires:1000,token:'PRIVATE',csrfToken:'PRIVATE'}]};
 const state=accessSessionsSnapshot(input);assert.equal(JSON.stringify(state).includes('PRIVATE'),false);assert.notEqual(state.sessions,input.sessions);
 assert.deepEqual(accessRoleChange(state,state.sessions[0],'viewer',' Phone '),{action:'set-role',sessionId:'a'.repeat(64),role:'viewer',label:'Phone',expectedRevision:7});
 assert.throws(()=>accessRoleChange(state,state.sessions[0],'admin','Phone'));assert.throws(()=>accessRoleChange(state,state.sessions[0],'owner','bad\nlabel'));assert.throws(()=>accessSessionsSnapshot({sessions:[]}));assert.throws(()=>accessSessionsSnapshot({...state,sessions:[...state.sessions,...state.sessions]}));
});
