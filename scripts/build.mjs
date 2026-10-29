import {readFile,writeFile,mkdir,copyFile} from 'node:fs/promises';
const root=new URL('../',import.meta.url);
const read=p=>readFile(new URL(p,root),'utf8');
const [html,css,sourceImport,factEvidence,content,engine,editor,historyCore,historyUI,insights,insightsUI,app,server]=await Promise.all(['web/index.html','web/styles.css','web/source-import.mjs','web/fact-evidence.mjs','web/content-tools.mjs','web/engine.mjs','web/word-editor.mjs','web/history.mjs','web/history-ui.mjs','web/insights.mjs','web/insights-ui.mjs','web/app.js','worker/server.js'].map(read));
const strip=s=>s.replace(/^import .*?;\n/gm,'').replace(/^export /gm,'');
const combined=[sourceImport,factEvidence,content,engine,editor,historyCore,historyUI,insights,insightsUI,app].map(strip).join('\n');
// JSON escaping avoids breaking out of the JavaScript string that holds the page.
const page=html.replace('<link rel="stylesheet" href="/styles.css">',()=>'<style>'+css+'</style>').replace('<script type="module" src="/app.js"></script>',()=>'<script type="module">'+combined.replace(/<\/script/gi,'<\\/script')+'</script>');
const plan=JSON.parse(await read('plan.json'));
const research=JSON.parse(await read('data/research.json'));
const seed=JSON.parse(await read('data/knowledge-seed.json'));
const worker='const researchData = '+JSON.stringify(research)+';\nconst knowledgeSeed = '+JSON.stringify(seed)+';\nconst roadmapData = '+JSON.stringify(plan)+';\nconst page = '+JSON.stringify(page)+';\n'+strip(sourceImport)+'\n'+server;
await writeFile(new URL('worker/index.js',root),worker);
await mkdir(new URL('dist/server/',root),{recursive:true});await mkdir(new URL('dist/.openai/',root),{recursive:true});
await writeFile(new URL('dist/server/index.js',root),worker);await copyFile(new URL('.openai/hosting.json',root),new URL('dist/.openai/hosting.json',root));
console.log('Built application and Worker');

const pages=page.replace('name="redcn-hosting" content="sites"','name="redcn-hosting" content="github-pages"').replace('♙ &nbsp; Закрытый доступ','GitHub Pages · данные локально');
await writeFile(new URL('docs/index.html',root),pages);
await writeFile(new URL('docs/.nojekyll',root),'');
for(const [source,target] of [['plan.json','docs/plan.json'],['data/research.json','docs/research.json'],['data/knowledge-seed.json','docs/knowledge-seed.json']])await copyFile(new URL(source,root),new URL(target,root));
console.log('Built standalone GitHub Pages site in docs/');
