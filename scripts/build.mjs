import {readFile,writeFile,mkdir,copyFile} from 'node:fs/promises';
const root=new URL('../',import.meta.url);
const read=p=>readFile(new URL(p,root),'utf8');
const [html,css,engine,app,server]=await Promise.all(['web/index.html','web/styles.css','web/engine.mjs','web/app.js','worker/server.js'].map(read));
const combined=engine.replace(/^export /gm,'')+'\n'+app.replace(/^import .*?;\n/,'');
// JSON escaping avoids breaking out of the JavaScript string that holds the page.
const page=html.replace('<link rel="stylesheet" href="/styles.css">','<style>'+css+'</style>').replace('<script type="module" src="/app.js"></script>','<script type="module">'+combined.replace(/<\/script/gi,'<\\/script')+'</script>');
const worker='const page = '+JSON.stringify(page)+';\n'+server;
await writeFile(new URL('worker/index.js',root),worker);
await mkdir(new URL('dist/server/',root),{recursive:true});await mkdir(new URL('dist/.openai/',root),{recursive:true});
await writeFile(new URL('dist/server/index.js',root),worker);await copyFile(new URL('.openai/hosting.json',root),new URL('dist/.openai/hosting.json',root));
console.log('Built application and Worker');
