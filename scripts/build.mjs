import {readFile,writeFile,mkdir,copyFile} from 'node:fs/promises';
const root=new URL('../',import.meta.url);
const read=p=>readFile(new URL(p,root),'utf8');
const [html,css,geoCatalog,qrCode,phoneConnectionUi,editorState,furniturePhotos,mediaCore,relatedCore,photoUI,relatedUI,sourceImport,factEvidence,content,engine,editor,historyCore,historyUI,insights,insightsUI,app,server]=await Promise.all(['web/index.html','web/styles.css','web/geo-catalog.mjs','web/qr-code.mjs','web/phone-connection.mjs','web/editor-state.mjs','web/furniture-photos.mjs','web/media-core.mjs','web/related.mjs','web/photo-ui.mjs','web/related-ui.mjs','web/source-import.mjs','web/fact-evidence.mjs','web/content-tools.mjs','web/engine.mjs','web/word-editor.mjs','web/history.mjs','web/history-ui.mjs','web/insights.mjs','web/insights-ui.mjs','web/app.js','worker/server.js'].map(read));
const [cardFiles,cardArchive,presentationStudio,cardArchiveCss,presentationCss]=await Promise.all(['web/card-files.mjs','web/card-archive.mjs','web/presentation-studio.mjs','web/card-archive.css','web/presentation-studio.css'].map(read));
const [rentalLexicon,communityPhrases,ruZhTranslator,templateCatalog,languageStudio,languageCss]=await Promise.all(['web/rental-lexicon.mjs','web/community-phrases.mjs','web/ru-zh-translator.mjs','web/template-catalog.mjs','web/language-studio.mjs','web/language-studio.css'].map(read));
const [universityCatalog,universityUI,universityCss]=await Promise.all(['web/university-catalog.mjs','web/university-ui.mjs','web/university-ui.css'].map(read));
const [publicationModel,publicationRender,designCanvasEditor,designCanvasCss]=await Promise.all(['web/publication-model.mjs','web/publication-render.mjs','web/design-canvas-editor.mjs','web/design-canvas-editor.css'].map(read));
const [universityRoutes,universityRoutesCss,listingWatch,listingWatchCss,importDiagnostics,importDiagnosticsCss,phoneRoutesController]=await Promise.all(['web/university-routes.mjs','web/university-routes.css','web/listing-watch.mjs','web/listing-watch.css','web/import-diagnostics.mjs','web/import-diagnostics.css','web/phone-routes-controller.mjs'].map(read));
const strip=s=>s.replace(/^import .*?;\n/gm,'').replace(/^export /gm,'');
const combined=[cardFiles,cardArchive,geoCatalog,universityCatalog,rentalLexicon,communityPhrases,ruZhTranslator,templateCatalog,qrCode,phoneConnectionUi,editorState,furniturePhotos,mediaCore,relatedCore,photoUI,relatedUI,sourceImport,factEvidence,content,engine,universityRoutes,publicationModel,publicationRender,designCanvasEditor,presentationStudio,editor,historyCore,historyUI,insights,insightsUI,languageStudio,universityUI,listingWatch,importDiagnostics,app].map(strip).join('\n');
// JSON escaping avoids breaking out of the JavaScript string that holds the page.
const page=html.replace('<link rel="stylesheet" href="/styles.css">',()=>'<style>'+css+'\n'+cardArchiveCss+'\n'+presentationCss+'\n'+designCanvasCss+'\n'+languageCss+'\n'+universityCss+'\n'+universityRoutesCss+'\n'+listingWatchCss+'\n'+importDiagnosticsCss+'</style>').replace('<script type="module" src="/app.js"></script>',()=>'<script type="module">'+combined.replace(/<\/script/gi,'<\\/script')+'</script>');
const plan=JSON.parse(await read('plan.json'));
const research=JSON.parse(await read('data/research.json'));
const seed=JSON.parse(await read('data/knowledge-seed.json'));
const worker='const researchData = '+JSON.stringify(research)+';\nconst knowledgeSeed = '+JSON.stringify(seed)+';\nconst roadmapData = '+JSON.stringify(plan)+';\nconst page = '+JSON.stringify(page)+';\n'+strip(mediaCore)+'\n'+strip(relatedCore)+'\n'+strip(sourceImport)+'\n'+server;
await writeFile(new URL('worker/index.js',root),worker);
await mkdir(new URL('dist/server/',root),{recursive:true});await mkdir(new URL('dist/.openai/',root),{recursive:true});
await writeFile(new URL('dist/server/index.js',root),worker);await copyFile(new URL('.openai/hosting.json',root),new URL('dist/.openai/hosting.json',root));
console.log('Built application and Worker');

const pages=page.replace('name="redcn-hosting" content="sites"','name="redcn-hosting" content="github-pages"').replace('♙ &nbsp; Закрытый доступ','GitHub Pages · данные локально');
await writeFile(new URL('docs/index.html',root),pages);
await writeFile(new URL('docs/.nojekyll',root),'');
for(const [source,target] of [['plan.json','docs/plan.json'],['data/research.json','docs/research.json'],['data/knowledge-seed.json','docs/knowledge-seed.json']])await copyFile(new URL(source,root),new URL(target,root));
console.log('Built standalone GitHub Pages site in docs/');

const [phoneHtml,phoneCss,phoneApp,pwaInstall]=await Promise.all(['web/phone.html','web/phone.css','web/phone.mjs','web/pwa-install.mjs'].map(read));
const phonePage=phoneHtml.replace('<link rel="stylesheet" href="/phone/phone.css">',()=>'<style>'+phoneCss+'\n'+cardArchiveCss+'\n'+presentationCss+'\n'+designCanvasCss+'\n'+universityRoutesCss+'\n'+listingWatchCss+'\n'+importDiagnosticsCss+'</style>').replace('<script type="module" src="/phone/phone.mjs"></script>',()=>'<script type="module">'+[cardFiles,cardArchive,pwaInstall,listingWatch,importDiagnostics,phoneRoutesController,phoneApp].map(strip).join('\n').replace(/<\/script/gi,'<\\/script')+'</script>');
await writeFile(new URL('docs/phone.html',root),phonePage);
// The complete vocabulary and visual editor load only when opened on a phone.
const phoneDesigner=[geoCatalog,universityCatalog,rentalLexicon,communityPhrases,ruZhTranslator,templateCatalog,content,engine,universityRoutes,publicationModel,publicationRender,designCanvasEditor,presentationStudio].map(strip).join('\n')+'\nexport {setupPresentationStudio};\n';
await writeFile(new URL('docs/phone-designer.mjs',root),phoneDesigner);
await writeFile(new URL('docs/phone-routes.mjs',root),[universityCatalog,universityRoutes].map(strip).join('\n')+'\nexport {setupUniversityRoutes};\n');
await copyFile(new URL('web/manifest.webmanifest',root),new URL('docs/phone.webmanifest',root));
await copyFile(new URL('web/sw.js',root),new URL('docs/phone-sw.js',root));
await mkdir(new URL('docs/icons/',root),{recursive:true});
for(const name of ['icon-192.png','icon-512.png','apple-touch-icon.png'])await copyFile(new URL('web/icons/'+name,root),new URL('docs/icons/'+name,root));
console.log('Built private Mac phone companion shell and install assets');
