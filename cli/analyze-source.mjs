import {readSourceHtml} from '../web/source-import.mjs';
import {analyzeListingSource} from '../web/engine.mjs';
let raw='';for await(const chunk of process.stdin)raw+=chunk;
const source=readSourceHtml(raw);
const analyzed=analyzeListingSource(source);
process.stdout.write(JSON.stringify({url:source.url,text:source.text,images:source.images,related:source.related,fields:analyzed.facts,warnings:analyzed.warnings}));
