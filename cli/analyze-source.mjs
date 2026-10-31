import {readSourceHtml} from '../web/source-import.mjs';
import {analyzeListingSource} from '../web/engine.mjs';
let raw='';for await(const chunk of process.stdin)raw+=chunk;
const source=readSourceHtml(raw,{url:process.argv[2]||''});
const analyzed=analyzeListingSource(source);
process.stdout.write(JSON.stringify({url:source.url,text:source.text,images:source.images,related:source.related,platform:source.platform,parserVersion:source.stats?.adapter||'',sections:source.sections,fields:analyzed.facts,warnings:analyzed.warnings}));
