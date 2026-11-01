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
                if self.path in ('/layout','/photos'):
                    body=(LAYOUT_HARNESS if self.path=='/layout' else cls.photo_html).encode();self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body);return
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
        self.open_photos();result=self.page.evaluate('photoPrepare()')
        self.assertEqual((result['width'],result['height'],result['selection']),(600,480,'manual'))
        self.assertEqual(self.page.evaluate("photoCalls.filter(row=>row.action==='accepted').length"),0)
        self.page.evaluate("photoChoice={original:{id:'a'.repeat(64)},selected:{kind:'crop',id:'c'.repeat(64),originalId:'a'.repeat(64),verification:'ocr-checked',verificationEvidence:{knownRegionsExcluded:true,postOcr:'mark-detected'}},reason:'ocr-checked'};photoUI.setSources([fixtureUrl],{})")
        result=self.page.evaluate('photoPrepare()');self.assertEqual((result['width'],result['selection']),(600,'original'))
        self.page.evaluate("photoChoice.selected.verificationEvidence.postOcr='not-detected';photoUI.setSources([fixtureUrl],{})")
        result=self.page.evaluate('photoPrepare()');self.assertEqual((result['width'],result['selection']),(560,'ocr-checked'))

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


if __name__=='__main__':unittest.main()
