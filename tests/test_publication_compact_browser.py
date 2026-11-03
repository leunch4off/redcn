"""Isolated static Chrome acceptance: layout overlays and prepared photo copies."""
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import io
from pathlib import Path
import threading
import unittest

ROOT=Path(__file__).resolve().parents[1]
PHOTO_SCRIPT='''<script type="module">
import {setupPhotos} from '/web/photo-ui.mjs';
const url='https://images.cdn-cian.ru/images/accepted-fixture.jpg',originalId='a'.repeat(64),manualId='b'.repeat(64),ocrId='c'.repeat(64);
const original={id:originalId,kind:'original',width:600,height:600,mime:'image/png'};
window.fixtureUrl=url;window.photoCalls=[];window.photoMessages=[];window.pauseAccepted=false;
window.photoChoice={original,selected:{id:manualId,kind:'crop',originalId,verification:'manual',margins:{top:0,left:0,right:0,bottom:0},width:500,height:500,mime:'image/png'},reason:'manual'};
const read=(key,fallback)=>['most-photo-analyze','most-photo-autoload'].includes(key)?false:JSON.parse(localStorage.getItem(key)||'null')??fallback;
const write=(key,value)=>{localStorage.setItem(key,JSON.stringify(value));return true;};
window.photoUI=setupPhotos({local:true,hosted:false,read,write,api:async(path,data)=>{photoCalls.push(data);if(data.action==='load')return original;if(data.action==='accepted'){const value=photoChoice;if(pauseAccepted){pauseAccepted=false;return new Promise(resolve=>{window.releaseAccepted=()=>resolve(value);});}return value;}throw Error('Unexpected fixture action');},toast:value=>photoMessages.push(value),onChange:()=>write('fixture-edits',photoUI.state()),getPost:()=>({title:'实拍房源',body:'房源资料',fields:{}}),onFeatures:()=>{}});
photoUI.setSources([url],read('fixture-edits',{}));
window.photoPrepare=async()=>{const rows=await photoUI.presentationPhotos();photoUI.refresh();const bitmap=await createImageBitmap(await(await fetch(rows[0].dataUrl)).blob());const result={width:bitmap.width,height:bitmap.height,selection:rows[0].selection,originalId:rows[0].originalId,selectedId:rows[0].selectedId};bitmap.close();return result;};
window.photoReady=true;
</script>'''
LAYOUT_HARNESS='''<!doctype html><html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/web/design-canvas-editor.css"><style>*{box-sizing:border-box}body{margin:0;font:16px system-ui}#mount{padding:12px;max-width:1300px;margin:auto}.btn{padding:8px}input,select,textarea{font:inherit}</style><div id="mount"></div><script type="module">
import {setupDesignCanvasEditor} from '/web/design-canvas-editor.mjs';
import {defaultPublicationDesign,resolvePublicationData,publicationSurfaceLayout} from '/web/publication-model.mjs';
let design=defaultPublicationDesign(),card={fields:{price:80000,address:'Москва'}};
window.compactEditor=setupDesignCanvasEditor({mount:document.getElementById('mount'),getDesign:()=>design,getCard:()=>card,getAssets:()=>({}),onChange:next=>{design=next;}});
window.compactEditor.setSurface('outro');
window.compactSetRoutes=value=>{card.fields.universities=value;compactEditor.refresh();};
window.compactDesign=()=>design;
window.compactGeometry=()=>publicationSurfaceLayout(design,'outro',resolvePublicationData(card,design));
window.compactReady=true;
</script></html>'''
STUDIO_HARNESS='''<!doctype html><html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/web/design-canvas-editor.css"><link rel="stylesheet" href="/web/presentation-studio.css"><style>*{box-sizing:border-box}body{margin:0;font:16px system-ui}#mount{padding:12px;max-width:1300px;margin:auto}.btn{padding:8px}input,select,textarea{font:inherit}</style><div id="mount"></div><script type="module">
import {setupPresentationStudio} from '/web/presentation-studio.mjs';
import {cardPhotoDataUrl} from '/web/card-files.mjs';
const nativeEncode=HTMLCanvasElement.prototype.toDataURL;
window.rasterEncodes=[];HTMLCanvasElement.prototype.toDataURL=function(type,...args){if(type==='image/png'&&this.width===1080&&this.height===1920)rasterEncodes.push(document.getElementById('presentationTabintro')?.classList.contains('chosen')?'intro':'outro');return nativeEncode.call(this,type,...args);};
window.studioPhoto=await cardPhotoDataUrl(await(await fetch('/api/local/media/'+'a'.repeat(64))).blob());
window.studioCard={titles:['实拍房源'],body:'公寓介绍',fields:{price:80000,area:54,metro:'Юго-Западная'}};
window.pauseStudioPhotos=false;
window.replaceStudioPhoto=async key=>{studioPhoto=await cardPhotoDataUrl(await(await fetch('/api/local/media/'+key.repeat(64))).blob());};
window.studio=setupPresentationStudio({mount:document.getElementById('mount'),getCard:()=>studioCard,getPhotos:async()=>{const value=studioPhoto;if(pauseStudioPhotos){pauseStudioPhotos=false;await new Promise(resolve=>{window.releaseStudioPhotos=resolve;});}return [{dataUrl:value}];}});
await studio.refresh();window.studioReady=true;
</script></html>'''
REDRAW_HARNESS='''<!doctype html><html><meta charset="utf-8"><link rel="stylesheet" href="/web/design-canvas-editor.css"><div id="mount"></div><script type="module">
import {setupDesignCanvasEditor} from '/web/design-canvas-editor.mjs';
import {defaultPublicationDesign} from '/web/publication-model.mjs';
window.redrawDesign=defaultPublicationDesign();window.redrawCard={body:'first'};window.redrawAssets={photos:['#ff0000']};window.redrawPaints=[];window.pauseRedraw=false;
window.redrawEditor=setupDesignCanvasEditor({mount:document.getElementById('mount'),getDesign:()=>redrawDesign,getCard:()=>redrawCard,getAssets:()=>redrawAssets,renderPage:async(canvas,id,design,card,assets)=>{const color=assets.photos[0];redrawPaints.push({color,body:card.body});if(pauseRedraw){pauseRedraw=false;await new Promise(resolve=>{window.releaseRedraw=resolve;});}const context=canvas.getContext('2d');context.fillStyle=color;context.fillRect(0,0,canvas.width,canvas.height);return {};}});
window.redrawPixel=()=>Array.from(document.getElementById('designCanvasPreview').getContext('2d').getImageData(10,10,1,1).data);
</script></html>'''


class PublicationCompactBrowserAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
            from PIL import Image
        except ImportError:
            raise unittest.SkipTest('Optional compact/photo acceptance needs Chrome, Playwright and Pillow')
        cls.runtime=sync_playwright().start()
        try:cls.browser=cls.runtime.chromium.launch(channel='chrome',headless=True)
        except Exception as error:
            cls.runtime.stop();raise unittest.SkipTest('Optional Chrome unavailable: '+str(error).splitlines()[0])
        cls.images={}
        for key,size,color in [('a',600,'#ff0000'),('b',500,'#0000ff'),('c',560,'#00ff00')]:
            stream=io.BytesIO();Image.new('RGB',(size,size),color).save(stream,'PNG');cls.images[key*64]=stream.getvalue()
        cls.photo_html=(ROOT/'web/index.html').read_text().replace('<script type="module" src="/app.js"></script>',PHOTO_SCRIPT)
        class Handler(SimpleHTTPRequestHandler):
            def __init__(self,*args,**kwargs):super().__init__(*args,directory=str(ROOT),**kwargs)
            def log_message(self,*args):pass
            def do_GET(self):
                if self.path in ('/layout','/photos','/studio','/redraw'):
                    body=({'/layout':LAYOUT_HARNESS,'/photos':cls.photo_html,'/studio':STUDIO_HARNESS,'/redraw':REDRAW_HARNESS}[self.path]).encode();self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body);return
                if self.path.startswith('/api/local/media/'):
                    body=cls.images.get(self.path.rsplit('/',1)[-1]);self.send_response(200 if body else 404);self.send_header('Content-Type','image/png');self.end_headers();self.wfile.write(body or b'');return
                if self.path=='/styles.css':self.path='/web/styles.css'
                super().do_GET()
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start();cls.url='http://127.0.0.1:'+str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close();cls.runtime.stop();cls.server.shutdown();cls.server.server_close();cls.thread.join()

    def setUp(self):
        self.context=self.browser.new_context(viewport={'width':393,'height':852});self.page=self.context.new_page();self.errors=[]
        self.page.on('pageerror',lambda error:self.errors.append(str(error)))
        self.page.route('https://images.cdn-cian.ru/**',lambda route:route.abort())

    def tearDown(self):
        self.context.close();self.assertEqual(self.errors,[])

    def open_photos(self):
        self.page.goto(self.url+'/photos',wait_until='domcontentloaded');self.page.wait_for_function('window.photoReady===true')

    def test_effective_map_selection_and_inspector_match_canvas_then_manual_geometry_stays(self):
        self.page.goto(self.url+'/layout',wait_until='domcontentloaded');self.page.wait_for_function("document.getElementById('designCanvasEditor')?.dataset.previewReady==='true'")
        # CSSOM serializes percentages to roughly six significant digits. This
        # tolerance is below 0.002 output px; saved/inspector values stay exact.
        self.assertAlmostEqual(float(self.page.locator('[data-node-id="outro-map"]').first.evaluate('el=>parseFloat(el.style.height)')),1268/1920*100,delta=.0001)
        self.page.locator('#designCanvasLayers [data-node-id="outro-map"]').click()
        self.assertEqual(self.page.locator('#designCanvas-h').input_value(),'1268')
        self.page.locator('#designCanvas-h').fill('1100');self.page.locator('#designCanvas-h').dispatch_event('change')
        self.page.evaluate("compactSetRoutes('МГУ | 40 | public')")
        self.assertEqual(self.page.evaluate("compactGeometry().nodes.find(node=>node.id==='outro-map').h"),1100)
        self.assertAlmostEqual(float(self.page.locator('#designCanvasOverlay [data-node-id="outro-map"]').evaluate('el=>parseFloat(el.style.height)')),1100/1920*100,delta=.0001)
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'),394)

    def test_backend_manual_crop_is_used_without_replacing_original_and_reset_survives_reload(self):
        self.open_photos();result=self.page.evaluate('photoPrepare()')
        self.assertEqual((result['width'],result['height'],result['selection']),(500,500,'manual'))
        self.page.locator('#photoGallery .photo-open').click();self.page.wait_for_function("document.getElementById('photoOriginalView').naturalWidth===600&&document.getElementById('photoCropView').naturalWidth===500")
        self.page.locator('#photoReset').click();self.assertIn(self.page.evaluate('fixtureUrl'),self.page.evaluate('photoUI.state().originals'))
        self.assertEqual(self.page.evaluate('photoPrepare()')['width'],600)
        self.page.reload(wait_until='domcontentloaded');self.page.wait_for_function('window.photoReady===true')
        self.assertEqual(self.page.evaluate('photoPrepare()')['selection'],'original')
        self.assertEqual(self.page.evaluate('photoPrepare()')['width'],600)

    def test_saved_manual_margins_override_backend_copy_and_uncertain_ocr_is_never_selected(self):
        self.page.add_init_script("localStorage.setItem('fixture-edits',JSON.stringify({crops:{'https://images.cdn-cian.ru/images/accepted-fixture.jpg':{top:0,left:0,right:0,bottom:20}}}))")
        media_reads=[]
        self.page.on('request',lambda request:media_reads.append(request.url.rsplit('/',1)[-1]) if '/api/local/media/' in request.url else None)
        self.open_photos();result=self.page.evaluate('photoPrepare()')
        self.assertEqual((result['width'],result['height'],result['selection']),(600,480,'manual'))
        self.assertEqual(result['originalId'],'a'*64)
        # Canonical metadata must be checked even for a saved manual crop.
        # That read does not authorize replacing the operator's 20% margins
        # with the backend's different 500px manual derivative.
        self.assertEqual(self.page.evaluate('photoCalls'),[{'action':'accepted','url':self.page.evaluate('fixtureUrl')}])
        self.assertEqual(media_reads,['a'*64],'Only canonical pixels may supply the saved manual crop')
        self.page.evaluate("photoChoice={original:{id:'a'.repeat(64)},selected:{kind:'crop',id:'c'.repeat(64),originalId:'a'.repeat(64),verification:'ocr-checked',verificationEvidence:{knownRegionsExcluded:true,postOcr:'mark-detected'}},reason:'ocr-checked'};photoUI.setSources([fixtureUrl],{})")
        result=self.page.evaluate('photoPrepare()');self.assertEqual((result['width'],result['selection']),(600,'original'))
        self.assertNotIn('c'*64,media_reads,'An uncertain OCR derivative must not even be downloaded')
        self.page.evaluate("photoChoice.selected.verificationEvidence.postOcr='not-detected';photoUI.setSources([fixtureUrl],{})")
        result=self.page.evaluate('photoPrepare()');self.assertEqual((result['width'],result['selection']),(560,'ocr-checked'))
        self.assertIn('c'*64,media_reads);self.assertNotIn('b'*64,media_reads)

    def test_publication_waits_for_pending_cache_crop_decision_instead_of_leaking_old_manual_copy(self):
        self.open_photos()
        self.assertEqual(self.page.evaluate('photoPrepare()')['selection'],'manual')
        self.page.evaluate("photoChoice={original:{id:'a'.repeat(64)},selected:{kind:'crop',id:'c'.repeat(64),originalId:'a'.repeat(64),verification:'ocr-checked',verificationEvidence:{knownRegionsExcluded:true,postOcr:'mark-detected'}},reason:'ocr-checked'};pauseAccepted=true;photoUI.setSources([fixtureUrl],{})")
        self.page.wait_for_function("typeof releaseAccepted==='function'")
        self.page.evaluate("() => {window.pendingPreparedPhoto=photoPrepare();return true;}")
        self.assertEqual(self.page.evaluate("async()=>Promise.race([pendingPreparedPhoto.then(()=> 'finished'),new Promise(resolve=>setTimeout(()=>resolve('pending'),60))])"),'pending')
        result=self.page.evaluate('async()=>{releaseAccepted();return await pendingPreparedPhoto;}')
        self.assertEqual((result['width'],result['height'],result['selection']),(600,600,'original'))
        self.assertEqual(result['originalId'],'a'*64)

    def test_default_pdf_encodes_no_social_png_and_tabs_prepare_their_selected_image_only(self):
        self.page.goto(self.url+'/studio',wait_until='domcontentloaded')
        self.page.wait_for_function('window.studioReady===true')
        self.assertEqual(self.page.evaluate('rasterEncodes'),[])
        self.page.locator('#presentationTabintro').click()
        self.page.wait_for_function("rasterEncodes.join(',')==='intro'&&document.getElementById('presentationImagePreview').naturalWidth===1080")
        intro=self.page.locator('#presentationImagePreview').get_attribute('src')
        self.page.locator('#presentationTaboutro').click()
        self.page.wait_for_function("previous=>rasterEncodes.join(',')==='intro,outro'&&document.getElementById('presentationImagePreview').src!==previous&&document.getElementById('presentationImagePreview').naturalWidth===1080",arg=intro)
        self.page.locator('#presentationTabpdf').click()
        self.page.locator('#presentationTabintro').click()
        self.page.evaluate('studio.refresh()')
        self.assertEqual(self.page.evaluate('rasterEncodes'),['intro','outro'])
        self.assertEqual(self.page.locator('#presentationImagePreview').get_attribute('src'),intro)

    def test_closed_html_is_unparsed_then_opens_current_card_and_asset_refresh_keeps_layers(self):
        self.page.goto(self.url+'/studio',wait_until='domcontentloaded')
        self.page.wait_for_function("window.studioReady===true&&document.getElementById('designCanvasEditor')?.dataset.previewReady==='true'")
        frame=self.page.locator('#presentationPreview')
        self.assertIsNone(frame.get_attribute('srcdoc'))
        self.assertEqual(self.page.frame_locator('#presentationPreview').locator('.publication-page').count(),0)
        self.page.locator('#presentationHtmlPreview summary').click()
        self.page.frame_locator('#presentationPreview').locator('body').filter(has_text='公寓介绍').wait_for()
        self.page.locator('#presentationHtmlPreview summary').click()
        self.page.wait_for_function("!document.getElementById('presentationPreview').hasAttribute('srcdoc')")
        self.page.locator('#designCanvasSurface').select_option('pdfGallery')
        self.page.wait_for_function("document.getElementById('designCanvasEditor').dataset.previewReady==='true'")
        self.page.evaluate("window.retainedLayer=document.querySelector('#designCanvasLayers button');window.galleryPixel=()=>Array.from(document.getElementById('designCanvasPreview').getContext('2d').getImageData(249,504,1,1).data)")
        red=self.page.evaluate('galleryPixel()');self.assertGreater(red[0],200);self.assertLess(red[2],50)
        self.page.evaluate('studio.refresh()')
        self.page.wait_for_function("document.getElementById('designCanvasEditor').dataset.previewReady==='true'")
        self.assertTrue(self.page.evaluate("retainedLayer===document.querySelector('#designCanvasLayers button')&&retainedLayer.isConnected"))
        self.page.evaluate("async()=>{await replaceStudioPhoto('b');await studio.refresh();}")
        self.page.wait_for_function("document.getElementById('designCanvasEditor').dataset.previewReady==='true'&&galleryPixel()[2]>200&&galleryPixel()[0]<50")
        self.assertTrue(self.page.evaluate("retainedLayer===document.querySelector('#designCanvasLayers button')&&retainedLayer.isConnected"))
        self.assertIsNone(frame.get_attribute('srcdoc'))
        # Open while a new card is still loading: the previous HTML may not reappear.
        self.page.evaluate("()=>{studioCard={...studioCard,body:'全新卡片内容'};pauseStudioPhotos=true;window.pendingStudio=studio.refresh();}")
        self.page.wait_for_function("typeof releaseStudioPhotos==='function'")
        self.page.locator('#presentationHtmlPreview summary').click()
        self.assertIsNone(frame.get_attribute('srcdoc'))
        self.page.evaluate('async()=>{releaseStudioPhotos();await pendingStudio;}')
        self.page.frame_locator('#presentationPreview').locator('body').filter(has_text='全新卡片内容').wait_for()
        self.assertNotIn('公寓介绍',self.page.frame_locator('#presentationPreview').locator('body').inner_text())
        self.page.locator('#presentation-brand-agentName').fill('专属顾问')
        self.page.locator('#presentation-brand-agentName').dispatch_event('change')
        self.page.frame_locator('#presentationPreview').locator('body').filter(has_text='专属顾问').wait_for()
        self.assertEqual(self.page.evaluate('rasterEncodes'),[])

    def test_identical_refresh_skips_redraw_but_reverted_assets_cancel_pending_old_paint(self):
        self.page.goto(self.url+'/redraw',wait_until='domcontentloaded')
        self.page.wait_for_function("document.getElementById('designCanvasEditor')?.dataset.previewReady==='true'")
        self.page.evaluate("window.retainedLayer=document.querySelector('#designCanvasLayers button');window.initialPaintCount=redrawPaints.length;redrawEditor.refresh();")
        self.page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
        self.assertTrue(self.page.evaluate("redrawPaints.length===initialPaintCount&&retainedLayer===document.querySelector('#designCanvasLayers button')"))
        self.page.evaluate("redrawAssets={photos:['#0000ff']};pauseRedraw=true;redrawEditor.refresh();")
        self.page.wait_for_function("typeof releaseRedraw==='function'")
        self.assertEqual(self.page.locator('#designCanvasEditor').get_attribute('data-preview-ready'),'false')
        self.page.evaluate("redrawAssets={photos:['#ff0000']};redrawEditor.refresh();")
        self.page.wait_for_function("document.getElementById('designCanvasEditor').dataset.previewReady==='true'&&redrawPaints.length===initialPaintCount+2")
        self.page.evaluate('releaseRedraw()')
        self.page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
        self.assertEqual(self.page.evaluate('redrawPixel()'),[255,0,0,255])
        self.page.evaluate("redrawAssets={photos:['#00ff00']};redrawEditor.refresh();")
        self.page.wait_for_function("document.getElementById('designCanvasEditor').dataset.previewReady==='true'&&redrawPixel()[1]===255")
        self.assertTrue(self.page.evaluate("retainedLayer===document.querySelector('#designCanvasLayers button')&&retainedLayer.isConnected"))
        self.page.evaluate("redrawCard={body:'second'};redrawEditor.refresh();")
        self.page.wait_for_function("document.getElementById('designCanvasEditor').dataset.previewReady==='true'&&redrawPaints.at(-1).body==='second'")
        self.page.evaluate("redrawDesign.surfaces.pdfOverview.background='#112233';redrawEditor.refresh();")
        self.page.wait_for_function("document.getElementById('designCanvasEditor').dataset.previewReady==='true'&&document.getElementById('designCanvasBackground').value==='#112233'")
        self.assertFalse(self.page.evaluate('retainedLayer.isConnected'))


if __name__=='__main__':unittest.main()
