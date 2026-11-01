import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {cloudBackupApi} from '../worker/cloud-backup.mjs';

const OWNER='a'.repeat(64),KEY='b'.repeat(64),REG='c'.repeat(64),ID='d'.repeat(32);
const base='https://fixture.invalid/api/cloud-backup/v1/owners/';
const sha=bytes=>createHash('sha256').update(bytes).digest('hex');
const blobKey=id=>'redcn-backups/blobs/'+OWNER+'/'+id;

// The in-memory fake collects tiny test payloads. Production passes the native
// HTTP stream to R2, whose storage service checks the supplied SHA-256 itself.
class Bucket{
 constructor(){this.rows=new Map();this.puts=[];this.lastGetBody=null;}
 async put(key,data,options={}){
  this.puts.push({key,data,options});
  if(this.rows.has(key)&&options.onlyIf)return null;
  let bytes;
  if(typeof data==='string')bytes=new TextEncoder().encode(data);
  else{
   assert.ok(data instanceof ReadableStream,'R2 receives an actual stream');
   const chunks=[];const reader=data.getReader();let length=0;
   while(true){const {done,value}=await reader.read();if(done)break;chunks.push(value);length+=value.byteLength;}
   bytes=new Uint8Array(length);let offset=0;
   for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.byteLength;}
  }
  if(options.sha256){
   assert.ok(options.sha256 instanceof ArrayBuffer);
   assert.equal(options.sha256.byteLength,32);
   if(Buffer.from(options.sha256).toString('hex')!==sha(bytes))throw Error('Invalid checksum');
  }
  const row={key,bytes,size:bytes.length,customMetadata:options.customMetadata,uploaded:new Date(0)};
  this.rows.set(key,row);return row;
 }
 async get(key){
  const row=this.rows.get(key);if(!row)return null;
  const body=new ReadableStream({start(controller){controller.enqueue(row.bytes);controller.close();}});
  this.lastGetBody=body;
  return{...row,json:async()=>JSON.parse(new TextDecoder().decode(row.bytes)),body};
 }
 async head(key){return this.rows.get(key)||null;}
 async list({prefix}){return{objects:[...this.rows.values()].filter(r=>r.key.startsWith(prefix)),truncated:false};}
}
function request(method,tail='',{key=KEY,owner=OWNER,body,headers={}}={}){
 return new Request(base+owner+tail,{method,headers:{'X-Redcn-Backup-Key':key,...headers},...(body!==undefined?{body,duplex:'half'}:{})});
}
function stream(bytes){let offset=0;return new ReadableStream({pull(controller){if(offset===bytes.length){controller.close();return;}const end=Math.min(offset+17,bytes.length);controller.enqueue(bytes.slice(offset,end));offset=end;}},{highWaterMark:0});}
function upload(bytes,{id=ID,checksum=sha(bytes),length=String(bytes.length),body=stream(bytes)}={}){
 const r=request('PUT','/backups/'+id,{body,headers:{'Content-Type':'application/octet-stream',...(length===null?{}:{'Content-Length':length}),'X-Redcn-Cipher-Sha256':checksum}});
 for(const name of['arrayBuffer','bytes','blob','text','json','formData','clone'])Object.defineProperty(r,name,{value(){throw Error('Worker must not buffer or clone the request');}});
 return r;
}
async function register(env,owner=OWNER,key=KEY){return cloudBackupApi(request('POST','',{owner,key,body:'{}',headers:{'X-Redcn-Registration-Key':REG}}),env);}
function environment(){return{BUCKET:new Bucket(),REDCN_BACKUP_REGISTRATION_KEY:REG};}

test('native request stream goes unchanged to R2 with conditional SHA-256, download stays streaming',async()=>{
 const env=environment();assert.equal((await register(env)).status,201);
 const data=new Uint8Array(80).fill(7),digest=sha(data),r=upload(data),body=r.body;
 let response=await cloudBackupApi(r,env);assert.equal(response.status,201);assert.equal((await response.json()).sha256,digest);
 const call=env.BUCKET.puts.at(-1);
 assert.equal(call.data,body);assert.equal(call.options.onlyIf.get('If-None-Match'),'*');
 assert.equal(Buffer.from(call.options.sha256).toString('hex'),digest);
 assert.equal(call.options.customMetadata.bytes,'80');
 response=await cloudBackupApi(request('GET','/backups/'+ID),env);
 assert.equal(response.status,200);assert.equal(response.body,env.BUCKET.lastGetBody);
 assert.deepEqual(new Uint8Array(await response.arrayBuffer()),data);
 assert.equal(response.headers.get('x-redcn-cipher-sha256'),digest);
 assert.equal((await(await cloudBackupApi(request('GET','/backups'),env)).json()).items.length,1);
});

test('immutable uploads are idempotent and a different checksum never replaces the original',async()=>{
 const env=environment();await register(env);const data=new Uint8Array(80).fill(7);
 assert.equal((await cloudBackupApi(upload(data),env)).status,201);
 const original=env.BUCKET.rows.get(blobKey(ID));
 let response=await cloudBackupApi(upload(data),env);assert.equal(response.status,200);assert.equal((await response.json()).idempotent,true);
 response=await cloudBackupApi(upload(new Uint8Array(80).fill(8)),env);assert.equal(response.status,409);
 assert.equal(env.BUCKET.rows.get(blobKey(ID)),original);assert.deepEqual(original.bytes,data);
});

test('tampered stream fails provider checksum validation without storing a partial object',async()=>{
 const env=environment();await register(env);const data=new Uint8Array(80).fill(7),bad=data.slice();bad[37]=8;
 const response=await cloudBackupApi(upload(bad,{checksum:sha(data)}),env);
 assert.equal(response.status,502);assert.equal(env.BUCKET.rows.has(blobKey(ID)),false);
 const error=JSON.stringify(await response.json());assert.equal(error.includes(KEY),false);assert.equal(error.includes('Invalid checksum'),false);
});

test('only a bounded decimal Content-Length is accepted before R2 consumes a body',async()=>{
 const env=environment();await register(env);const puts=env.BUCKET.puts.length,data=new Uint8Array(80);
 for(const length of[null,'0','31','80.0','8e1','+80','-80',String(64*1024*1024+1),'9007199254740993']){
  assert.equal((await cloudBackupApi(upload(data,{length}),env)).status,400,'length '+length);
 }
 assert.equal(env.BUCKET.puts.length,puts);
});

test('64 MiB known-length HTTP body is handed to R2 without allocating or reading that body',async()=>{
 const env=environment();await register(env);let pulls=0;
 const body=new ReadableStream({pull(){pulls++;throw Error('HTTP stream must be consumed only by R2');}},{highWaterMark:0});
 const r=upload(new Uint8Array(32),{body,length:String(64*1024*1024)}),native=r.body;
 env.BUCKET.put=async(key,value,options)=>{assert.equal(value,native);assert.equal(options.sha256.byteLength,32);return{size:64*1024*1024};};
 assert.equal((await cloudBackupApi(r,env)).status,201);assert.equal(pulls,0);
});

test('stream/provider failures and inconsistent object sizes return a bounded private error',async()=>{
 const env=environment();await register(env);const data=new Uint8Array(80);
 let response=await cloudBackupApi(upload(data,{body:new ReadableStream({pull(){throw Error('Secret '+KEY);}})}),env);
 assert.equal(response.status,502);assert.equal(env.BUCKET.rows.has(blobKey(ID)),false);
 env.BUCKET.put=async()=>{throw Error('Provider secret '+KEY);};
 response=await cloudBackupApi(upload(data),env);assert.equal(response.status,502);assert.equal((await response.text()).includes(KEY),false);
 env.BUCKET.put=async()=>({size:79});
 assert.equal((await cloudBackupApi(upload(data),env)).status,502);
});

test('foreign capability/owner cannot read or overwrite and bootstrap requires registration authorization',async()=>{
 const env=environment();assert.equal((await cloudBackupApi(request('POST','',{body:'{}'}),env)).status,403);
 await register(env);assert.equal((await register(env,OWNER,'e'.repeat(64))).status,409);
 for(const options of[{key:'e'.repeat(64)},{owner:'f'.repeat(64)}])assert.equal((await cloudBackupApi(request('GET','/backups',options),env)).status,403);
 assert.equal((await cloudBackupApi(request('DELETE','/backups/'+ID),env)).status,405);
 assert.equal((await cloudBackupApi(request('GET','/backups',{headers:{Origin:'https://other.invalid'}}),env)).status,403);
 assert.equal((await cloudBackupApi(request('GET','/backups'),{})).status,503);
});
