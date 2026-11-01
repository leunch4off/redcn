// Private encrypted backup transport. Cipher keys never reach this Worker.
const CB_MAX=64*1024*1024;
const cbHex=(v,n)=>typeof v==='string'&&new RegExp('^[a-f0-9]{'+n+'}$','u').test(v);
const cbHash=async value=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(value)))).map(b=>b.toString(16).padStart(2,'0')).join('');
const cbEqual=(a,b)=>{if(typeof a!=='string'||typeof b!=='string'||a.length!==b.length)return false;let d=0;for(let i=0;i<a.length;i++)d|=a.charCodeAt(i)^b.charCodeAt(i);return d===0;};
const cbJson=(value,status=200)=>Response.json(value,{status,headers:{'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'}});
export async function cloudBackupApi(request,env={}){
 const url=new URL(request.url),match=url.pathname.match(/^\/api\/cloud-backup\/v1\/owners\/([a-f0-9]{64})(?:\/backups(?:\/([a-f0-9]{32}))?)?$/u);
 if(!url.pathname.startsWith('/api/cloud-backup/'))return null;
 if(!match||url.search)return cbJson({error:'Unknown backup route'},404);
 if(request.headers.get('Origin')&&request.headers.get('Origin')!==url.origin)return cbJson({error:'Foreign origin'},403);
 if(!env.BUCKET)return cbJson({error:'Private backup storage is not connected'},503);
 const owner=match[1],id=match[2],key=request.headers.get('X-Redcn-Backup-Key'),ownerPath='redcn-backups/owners/'+owner+'.json',prefix='redcn-backups/blobs/'+owner+'/';
 if(!cbHex(key,64))return cbJson({error:'Backup owner authorization required'},403);
 const keyHash=await cbHash(key),isOwnerRoot=!url.pathname.includes('/backups');
 try{
  if(isOwnerRoot&&request.method==='POST'){
   const registration=request.headers.get('X-Redcn-Registration-Key');
   if(!cbHex(env.REDCN_BACKUP_REGISTRATION_KEY,64))return cbJson({error:'Backup registration is not configured'},503);
   if(!cbEqual(registration,env.REDCN_BACKUP_REGISTRATION_KEY))return cbJson({error:'Registration authorization required'},403);
   const value=JSON.stringify({version:1,owner,keyHash});
   const created=await env.BUCKET.put(ownerPath,value,{onlyIf:new Headers({'If-None-Match':'*'}),httpMetadata:{contentType:'application/json',cacheControl:'private, no-store'}});
   if(!created){const previous=await env.BUCKET.get(ownerPath);const metadata=previous?await previous.json():null;if(!metadata||!cbEqual(metadata.keyHash,keyHash))return cbJson({error:'Owner namespace already exists'},409);}
   return cbJson({version:1,ownerId:owner,registered:true},created?201:200);
  }
  const stored=await env.BUCKET.get(ownerPath),metadata=stored?await stored.json():null;
  if(!metadata||metadata.version!==1||metadata.owner!==owner||!cbEqual(metadata.keyHash,keyHash))return cbJson({error:'Backup owner authorization required'},403);
  if(!isOwnerRoot&&!id&&request.method==='GET'){
   const result=await env.BUCKET.list({prefix,limit:50,include:['customMetadata']});
   const items=result.objects.filter(o=>cbHex(o.key.slice(prefix.length),32)&&o.size<=CB_MAX&&cbHex(o.customMetadata?.sha256,64)).map(o=>({id:o.key.slice(prefix.length),bytes:o.size,sha256:o.customMetadata.sha256,uploaded:o.uploaded?.toISOString?.()||null}));
   return cbJson({version:1,ownerId:owner,items,truncated:result.truncated===true});
  }
  if(id&&request.method==='PUT'){
   const length=request.headers.get('Content-Length'),size=Number(length),sha=request.headers.get('X-Redcn-Cipher-Sha256');
   if(request.headers.get('Content-Type')!=='application/octet-stream'||!length||!/^\d+$/u.test(length)||!Number.isSafeInteger(size)||size<32||size>CB_MAX||!cbHex(sha,64)||!request.body)return cbJson({error:'A bounded encrypted binary backup and checksum are required'},400);
   const expected=Uint8Array.from(sha.match(/../gu),s=>parseInt(s,16)).buffer;
   // Preserve the HTTP body's native known length. R2 validates SHA-256 while
   // streaming; buffering or wrapping this body would add up to 64 MiB to RAM.
   const stored=await env.BUCKET.put(prefix+id,request.body,{onlyIf:new Headers({'If-None-Match':'*'}),sha256:expected,httpMetadata:{contentType:'application/octet-stream',cacheControl:'private, no-store'},customMetadata:{sha256:sha,bytes:String(size),format:'redcn-encrypted-workspace-v1'}});
   if(!stored){const old=await env.BUCKET.head(prefix+id);if(old?.size===size&&old.customMetadata?.sha256===sha)return cbJson({version:1,id,ownerId:owner,sha256:sha,bytes:size,stored:true,idempotent:true});return cbJson({error:'This backup ID is already used'},409);}
   if(stored.size!==size)return cbJson({error:'Cloud object size did not match the upload'},502);
   return cbJson({version:1,id,ownerId:owner,sha256:sha,bytes:size,stored:true},201);
  }
  if(id&&request.method==='GET'){
   const value=await env.BUCKET.get(prefix+id);
   if(!value)return cbJson({error:'Backup not found'},404);
   if(!cbHex(value.customMetadata?.sha256,64)||value.size>CB_MAX||value.customMetadata?.format!=='redcn-encrypted-workspace-v1')return cbJson({error:'Invalid cloud object metadata'},502);
   return new Response(value.body,{headers:{'Content-Type':'application/octet-stream','Content-Length':String(value.size),'X-Redcn-Cipher-Sha256':value.customMetadata.sha256,'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'}});
  }
  return cbJson({error:'Method not allowed'},405);
 }catch{return cbJson({error:'Cloud backup operation failed; no plaintext or credentials were logged'},502);}
}
