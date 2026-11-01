import http from 'node:http';
import {readFile,lstat} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';

const projectRoot=fileURLToPath(new URL('../',import.meta.url));
const MAX_ASSET_BYTES=32*1024*1024;
const types={'.mjs':'text/javascript; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8'};
// Only these generated/data assets may be served outside the public web source
// directory. This is intentionally not a repository-wide static file server.
const aliases={
 '/':['web/index.html','text/html; charset=utf-8'],
 '/phone/':['docs/phone.html','text/html; charset=utf-8'],
 '/phone/designer.mjs':['docs/phone-designer.mjs',types['.mjs']],
 '/phone/routes.mjs':['docs/phone-routes.mjs',types['.mjs']],
 '/phone/manifest.webmanifest':['docs/phone.webmanifest','application/manifest+json'],
 '/phone/sw.js':['docs/phone-sw.js',types['.js']],
 '/pages/':['docs/index.html','text/html; charset=utf-8'],
 '/api/roadmap':['plan.json','application/json; charset=utf-8'],
 '/moscow-universities.json':['data/moscow-universities.json','application/json'],
 '/ru-zh-open-lexicon.json':['data/ru-zh-open-lexicon.json','application/json'],
};
for(const name of ['icon-192.png','icon-512.png','apple-touch-icon.png'])aliases['/phone/icons/'+name]=['docs/icons/'+name,'image/png'];
for(const [name,source]of [['plan.json','plan.json'],['research.json','data/research.json'],['knowledge-seed.json','data/knowledge-seed.json']]){
 aliases['/'+name]=[source,'application/json; charset=utf-8'];
 aliases['/pages/'+name]=['docs/'+name,'application/json; charset=utf-8'];
}
for(const kind of ['title','description','tags'])aliases['/data/template-catalog/'+kind+'.ndjson.gz']=['data/template-catalog/'+kind+'.ndjson.gz','application/gzip'];
const desktopStyles=['styles','card-archive','presentation-studio','design-canvas-editor','language-studio','university-ui','city-pois','university-routes','listing-watch','import-diagnostics','content-studio','workflow','commute-compare','fact-ledger','dictionary-review','dossier-transfer','table-export','access-roles','import-errors','model-viewer','publication-review','mobile-acceptance'];

async function publicFile(root,relative){
 const parts=relative.split('/');
 if(parts.some(part=>!part||part==='.'||part==='..'))throw Error('Invalid public asset path');
 let current=path.resolve(root);
 for(let index=0;index<parts.length;index++){
  current=path.join(current,parts[index]);const stat=await lstat(current);
  // Reject symlinked files and parent directories, including selected aliases.
  if(stat.isSymbolicLink()||(index<parts.length-1?!stat.isDirectory():!stat.isFile()||stat.size>MAX_ASSET_BYTES))throw Error('Not a bounded public asset');
 }
 return readFile(current);
}

export function createPreviewResponder({root=projectRoot}={}){
 const response=(status,body,type='text/plain; charset=utf-8',extra={})=>({status,body:Buffer.isBuffer(body)?body:Buffer.from(body),headers:{'Content-Type':type,'Cache-Control':'no-store','X-Content-Type-Options':'nosniff',...extra}});
 return async function previewResponse(requestUrl,method='GET'){
  let pathname;
  try{pathname=new URL(requestUrl,'http://localhost').pathname;}catch{return response(400,'Invalid request');}
  if(pathname==='/api/import')return response(422,JSON.stringify({error:'Импорт через Google Chrome доступен в локальном сервере: bash start-redcn.command.'}),'application/json; charset=utf-8');
  if(!['GET','HEAD'].includes(method))return response(405,'Method not allowed','text/plain; charset=utf-8',{'Allow':'GET, HEAD'});
  let asset=Object.hasOwn(aliases,pathname)?aliases[pathname]:null;
  // New frontend modules work automatically, including nested source adapters;
  // encoded paths, hidden files and all other suffixes are denied. URL dot
  // segments are canonicalized before mapping exclusively into web/.
  if(!asset&&/^\/(?:[A-Za-z0-9_-]+\/)*[A-Za-z0-9_-]+\.(?:mjs|js|css)$/u.test(pathname))asset=['web'+pathname,types[path.extname(pathname)]];
  if(!asset)return response(404,'Not found');
  try{
   const content=pathname==='/styles.css'?Buffer.concat(await Promise.all(desktopStyles.map(async name=>Buffer.concat([await publicFile(root,'web/'+name+'.css'),Buffer.from('\n')])))):await publicFile(root,asset[0]);
   return response(200,method==='HEAD'?Buffer.alloc(0):content,asset[1],{'Content-Length':String(content.length)});
  }catch{return response(404,'Public asset not found');}
 };
}

export function createPreviewServer(options={}){
 const respond=createPreviewResponder(options);
 return http.createServer(async(req,res)=>{const value=await respond(req.url,req.method);res.writeHead(value.status,value.headers);res.end(value.body);});
}

if(process.argv[1]&&import.meta.url===pathToFileURL(path.resolve(process.argv[1])).href){
 const index=process.argv.indexOf('--port'),port=Number(process.env.PORT||(index>=0?process.argv[index+1]:3000));
 if(!Number.isInteger(port)||port<0||port>65535)throw Error('Port must be an integer from 0 to 65535');
 const server=createPreviewServer();
 server.listen(port,'127.0.0.1',()=>console.log('MOST preview ready: http://127.0.0.1:'+server.address().port));
}
