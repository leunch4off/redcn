import {DICTIONARY,DEFAULT_PREFS,extractFacts,generateListing} from '../web/engine.mjs';
let raw='';for await(const chunk of process.stdin)raw+=chunk;
const input=JSON.parse(raw);
const rules={...DICTIONARY,...input.rules};
const extracted=extractFacts(input.text||'',rules);
const fields={style:'detailed',registration:'assist',support:true,...extracted.facts,...input.data};
fields.copyVariant ??= fields.titleVariant || 0;
const result=generateListing(fields,rules,{...DEFAULT_PREFS,...input.prefs});
if(result.problems.length){process.stderr.write(result.problems.join('\n'));process.exitCode=2;}
else process.stdout.write(JSON.stringify({fields,...result,warnings:[...extracted.warnings,...(fields.sourceAudit?.warnings||[])]}));
