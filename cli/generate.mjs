import {DICTIONARY,DEFAULT_PREFS,extractFacts,generateListing,translateDraft} from '../web/engine.mjs';
let raw='';for await(const chunk of process.stdin)raw+=chunk;
const input=JSON.parse(raw);
const rules={...DICTIONARY,...input.rules};
if(input.operation==='translate'){process.stdout.write(JSON.stringify(translateDraft(input.text||'',{rules,mode:input.mode||'draft',entries:input.entries||[]})));process.exit(0);}
const extracted=extractFacts(input.text||'',rules);
const fields={style:'detailed',registration:'assist',support:true,...extracted.facts,...input.data};
fields.templateMode ??= 'catalog';
fields.copyVariant ??= fields.titleVariant || 0;
fields.tagVariant ??= fields.titleVariant || 0;
const result=generateListing(fields,rules,{...DEFAULT_PREFS,...input.prefs});
if(result.problems.length){process.stderr.write(result.problems.join('\n'));process.exitCode=2;}
else process.stdout.write(JSON.stringify({fields,...result,warnings:[...extracted.warnings,...(fields.sourceAudit?.warnings||[])]}));
