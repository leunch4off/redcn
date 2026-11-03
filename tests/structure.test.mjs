import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const root=new URL('../',import.meta.url),read=p=>readFile(new URL(p,root),'utf8');
test('all editor controls referenced in application have a unique DOM id',async()=>{
 const [html,app,editor,insightsUI,historyUI]=await Promise.all(['web/index.html','web/app.js','web/word-editor.mjs','web/insights-ui.mjs','web/history-ui.mjs'].map(read));
 const ids=[...html.matchAll(/id="([^"]+)"/g)].map(x=>x[1]);assert.equal(new Set(ids).size,ids.length);
 const fields=[...app.match(/const fields=\[(.*?)\];/s)[1].matchAll(/'([^']+)'/g)].map(x=>x[1]);
 const controls=[...[...app.matchAll(/\$\('([^']+)'\)/g)].map(x=>x[1]),...[...(editor+insightsUI+historyUI).matchAll(/el\('([^']+)'\)/g)].map(x=>x[1]),...fields];
 for(const id of controls)assert(ids.includes(id),'Missing DOM control '+id);
});
test('roadmap preserves permanent IDs, declared total and proof for completed work',async()=>{
 const plan=JSON.parse(await read('plan.json'));assert.equal(plan.tasks.length,plan.total);assert(plan.total>=200);
 assert.deepEqual(plan.tasks.map(t=>t.id),Array.from({length:plan.total},(_,i)=>'RCN-'+String(i+1).padStart(3,'0')));
 for(const task of plan.tasks){assert(task.title&&task.acceptance);assert(['P0','P1','P2'].includes(task.priority));assert(Array.isArray(task.depends_on));for(const dep of task.depends_on)assert(plan.tasks.some(t=>t.id===dep));if(task.status==='done')assert(task.evidence.length>0);}
 const markdown=await read('ROADMAP.md');assert.equal((markdown.match(/^- \[[ x]\]/gm)||[]).length,plan.total);
 assert.equal((markdown.match(/^- \[x\]/gm)||[]).length,plan.tasks.filter(t=>t.status==='done').length);
});
