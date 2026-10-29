import http from 'node:http';
import {readFile} from 'node:fs/promises';
const root=new URL('../',import.meta.url);
const files={'/api/roadmap':['plan.json','application/json; charset=utf-8'],'/':['web/index.html','text/html; charset=utf-8'],'/app.js':['web/app.js','text/javascript; charset=utf-8'],'/engine.mjs':['web/engine.mjs','text/javascript; charset=utf-8'],'/content-tools.mjs':['web/content-tools.mjs','text/javascript; charset=utf-8'],'/word-editor.mjs':['web/word-editor.mjs','text/javascript; charset=utf-8'],'/styles.css':['web/styles.css','text/css; charset=utf-8']};
http.createServer(async(req,res)=>{const pathname=new URL(req.url,'http://localhost').pathname;
  try{if(pathname==='/api/import'){res.writeHead(422,{'Content-Type':'application/json'});res.end(JSON.stringify({error:'Импорт ссылок доступен на опубликованном сайте.'}));return;}
    if(!files[pathname]){res.writeHead(404);res.end('Not found');return;}
    const [path,type]=files[pathname];res.writeHead(200,{'Content-Type':type});res.end(await readFile(new URL(path,root)));
  }catch{res.writeHead(500);res.end('Preview error');}
}).listen(Number(process.env.PORT||(process.argv.includes('--port')?process.argv[process.argv.indexOf('--port')+1]:3000)),'0.0.0.0',()=>console.log('MOST preview ready'));
