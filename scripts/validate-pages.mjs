import {readFile,stat} from 'node:fs/promises';
import {spawnSync} from 'node:child_process';
import assert from 'node:assert/strict';
const root=new URL('../',import.meta.url),html=await readFile(new URL('docs/index.html',root),'utf8');
assert(html.includes('name="redcn-hosting" content="github-pages"'));
assert(!/<script[^>]+src=/.test(html),'Standalone page must not depend on root-path scripts');
assert(!/href="\/(styles|app)/.test(html));
const script=html.match(/<script type="module">([\s\S]*?)<\/script>/)[1];
const check=spawnSync(process.execPath,['--check','--input-type=module'],{input:script,encoding:'utf8'});assert.equal(check.status,0,check.stderr);
for(const file of ['docs/plan.json','docs/research.json','docs/knowledge-seed.json'])JSON.parse(await readFile(new URL(file,root),'utf8'));
await stat(new URL('docs/.nojekyll',root));console.log('Standalone Pages application and all public datasets are valid');

const phone=await readFile(new URL('docs/phone.html',root),'utf8');
assert(!/<script[^>]+src=/.test(phone),'Phone shell must be self-contained');
assert(!phone.includes('name="redcn-token"'),'Public phone shell must not contain the desktop token');
const phoneScript=phone.match(/<script type="module">([\s\S]*?)<\/script>/)?.[1];assert(phoneScript);
const bootstrap=phone.match(/<script>([\s\S]*?)<\/script>/)?.[1];assert(bootstrap,'Independent startup guard is required');
assert(phone.indexOf(bootstrap)<phone.indexOf(phoneScript),'Guard must precede module parsing');
assert(bootstrap.includes('BOOT_NOT_STARTED')&&bootstrap.includes('XMLHttpRequest'));
assert(!/\b(?:const|let|async)\s|=>|\?\./.test(bootstrap),'Guard must use old browser syntax');
assert.equal(spawnSync(process.execPath,['--check','--input-type=commonjs'],{input:bootstrap,encoding:'utf8'}).status,0);
const phoneCheck=spawnSync(process.execPath,['--check','--input-type=module'],{input:phoneScript,encoding:'utf8'});assert.equal(phoneCheck.status,0,phoneCheck.stderr);
const manifest=JSON.parse(await readFile(new URL('docs/phone.webmanifest',root),'utf8'));
assert.equal(manifest.start_url,'/phone/');assert.equal(manifest.display,'standalone');
for(const [name,size] of [['apple-touch-icon.png',180],['icon-192.png',192],['icon-512.png',512]]){
 const bytes=await readFile(new URL('docs/icons/'+name,root));assert.equal(bytes.readUInt32BE(16),size);assert.equal(bytes.readUInt32BE(20),size);
}
console.log('Phone shell, manifest and iPhone icons are valid');

const phoneDesigner=await readFile(new URL('docs/phone-designer.mjs',root),'utf8');
const designerCheck=spawnSync(process.execPath,['--check','--input-type=module'],{input:phoneDesigner,encoding:'utf8'});
assert.equal(designerCheck.status,0,designerCheck.stderr);
assert(phoneDesigner.includes('export {setupPresentationStudio}'));
assert(!phoneDesigner.includes('name="redcn-token"'),'Public designer module must not contain a desktop token');
assert(phoneScript.includes("import('/phone/designer.mjs')"),'Designer must load on demand');
assert(!phoneScript.includes('UNIVERSITY_CATALOG ='),'Phone startup must not embed the large designer vocabulary');
console.log('Lazy phone designer is valid and separate from startup');

const phoneRoutes=await readFile(new URL('docs/phone-routes.mjs',root),'utf8');
const routeCheck=spawnSync(process.execPath,['--check','--input-type=module'],{input:phoneRoutes,encoding:'utf8'});
assert.equal(routeCheck.status,0,routeCheck.stderr);
assert(phoneRoutes.includes('export {setupUniversityRoutes}'));
assert(!phoneRoutes.includes('name="redcn-token"'));
assert(phoneScript.includes("import('/phone/routes.mjs')"),'University route form must be lazy');
console.log('Lazy route form is valid and separate from phone startup');

const phoneServiceWorker=await readFile(new URL('docs/phone-sw.js',root),'utf8');
const swCheck=spawnSync(process.execPath,['--check','--input-type=commonjs'],{input:phoneServiceWorker,encoding:'utf8'});
assert.equal(swCheck.status,0,swCheck.stderr);
for(const [name,source]of [['main',script],['phone',phoneScript],['service worker',phoneServiceWorker]]){
 assert(!/AbortSignal\.(?:timeout|any)\s*\(/.test(source),name+' must work without AbortSignal static helpers');
 assert(source.includes('async function withRequestDeadline'),name+' must include its scoped request deadline');
}
console.log('Safari-compatible request deadlines are bundled in phone, main and service worker');
