import test from 'node:test';
import assert from 'node:assert/strict';
import {scanSecretContent} from '../scripts/check-secrets.mjs';
test('secret scan reports location without echoing credentials',()=>{
 const token='ghp_'+'a'.repeat(36),hits=scanSecretContent('web/config.js','const token="'+token+'";');
 assert.equal(hits.length,1);assert.equal(hits[0].line,1);assert(!JSON.stringify(hits).includes(token));
 assert.equal(scanSecretContent('cli/phone-access.json','{}')[0].reason,'Runtime/private file is tracked');
 assert.equal(scanSecretContent('web/config.js','const token="'+token+'"; // secret-scan-allow: isolated synthetic fixture only').length,0);
});
test('ordinary placeholders, hashes and public URLs are not treated as tokens',()=>{
 for(const file of ['web/engine.mjs','tests/test_pairing_codes.py','docs/VERIFICATION.md'])assert.deepEqual(scanSecretContent(file,'https://example.org 12345678 SHA256 '+ 'a'.repeat(64)),[]);
});
