import test from 'node:test';
import assert from 'node:assert/strict';
import {learningGroundingFields} from '../web/learning-studio.mjs';

test('advertising requests retain restrictions, confirmed terms and conflict evidence',()=>{
 const fields={price:'85000',smokingPolicy:'no',guestPolicy:'no',subletPolicy:'no',shortTermPolicy:'no',
  leaseTerm:'long',minMonths:'6',contractMonths:'12',prepayMonths:'2',occupants:'2',
  registrationMonths:'3',registrationPeople:'2',
  sourceAudit:{blockedFields:['walk']},photoEvidence:{sofa:{status:'confirmed'}},
  source:'PRIVATE_RAW_HTML',sourceDescription:'PRIVATE_RAW_DESCRIPTION',photos:['PRIVATE_PHOTO'],commission:100};
 const result=learningGroundingFields(fields);
 for(const key of ['smokingPolicy','guestPolicy','subletPolicy','shortTermPolicy','leaseTerm','minMonths',
  'contractMonths','prepayMonths','occupants','registrationMonths','registrationPeople','sourceAudit','photoEvidence'])
  assert.deepEqual(result[key],fields[key],key);
 for(const key of ['source','sourceDescription','photos','commission'])assert.equal(Object.hasOwn(result,key),false,key);
 assert.equal(JSON.stringify(result).includes('PRIVATE_'),false);
});
