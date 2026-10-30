"""Owned Chrome checks for canvas geometry, gestures and asynchronous preview isolation."""
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import unittest


ROOT=Path(__file__).resolve().parents[1]
HARNESS='''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/web/design-canvas-editor.css"><style>*{box-sizing:border-box}body{margin:0;font:16px system-ui}#mount{padding:12px;max-width:1500px;margin:auto}.btn{border:1px solid #cbd4c4;border-radius:8px;background:white;color:#28432e;padding:9px;cursor:pointer}input,select,textarea{font:inherit}.muted{font-size:12px;line-height:1.5;color:#687461}</style><div id="mount"></div><script type="module">
import {setupDesignCanvasEditor} from '/web/design-canvas-editor.mjs';
import {defaultPublicationDesign} from '/web/publication-model.mjs';
let design=JSON.parse(localStorage.getItem('canvas-test-design')||'null')||defaultPublicationDesign(),card={id:'a',title:'房源 A',fields:{price:80000,area:54}};
window.canvasTestCommits=0;window.canvasTestDelay=0;
Object.defineProperty(window,'canvasTestDesign',{get:()=>design});Object.defineProperty(window,'canvasTestCard',{get:()=>card});
window.canvasTestSetCard=value=>{card=value;};
window.canvasTestEditor=setupDesignCanvasEditor({mount:document.getElementById('mount'),getDesign:()=>design,getCard:()=>card,getAssets:()=>({}),onChange:(next,meta)=>{design=next;window.canvasTestCommits++;window.canvasTestLastChange=meta;localStorage.setItem('canvas-test-design',JSON.stringify(design));},onSurfaceChange:id=>{window.canvasTestSurface=id;},renderPage:async(canvas,id,template,snapshot)=>{if(window.canvasTestDelay&&snapshot.id==='a')await new Promise(resolve=>setTimeout(resolve,window.canvasTestDelay));const s=template.surfaces[id];canvas.width=s.width;canvas.height=s.height;const ctx=canvas.getContext('2d');ctx.fillStyle=snapshot.id==='a'?'#ff0000':'#00ff00';ctx.fillRect(0,0,s.width,s.height);return {warnings:[]};}});
window.canvasTestReady=true;
</script></html>'''


class CanvasBrowserAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise unittest.SkipTest('Optional canvas acceptance needs Playwright and Chrome')
        cls.runtime=sync_playwright().start()
        try:
            cls.browser=cls.runtime.chromium.launch(channel='chrome',headless=True)
        except Exception as error:
            cls.runtime.stop();raise unittest.SkipTest('Optional Chrome unavailable: '+str(error).splitlines()[0])
        class Handler(SimpleHTTPRequestHandler):
            def __init__(self,*args,**kwargs):super().__init__(*args,directory=str(ROOT),**kwargs)
            def log_message(self,*args):pass
            def do_GET(self):
                if self.path=='/':
                    value=HARNESS.encode();self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(value)));self.end_headers();self.wfile.write(value)
                else:super().do_GET()
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start();cls.url='http://127.0.0.1:'+str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close();cls.runtime.stop();cls.server.shutdown();cls.server.server_close();cls.thread.join()

    def setUp(self):
        self.context=self.browser.new_context(viewport={'width':1440,'height':1050},has_touch=True)
        self.page=self.context.new_page();self.errors=[];self.page.on('pageerror',lambda error:self.errors.append(str(error)));self.page.goto(self.url,wait_until='domcontentloaded');self.page.wait_for_function('window.canvasTestReady===true')
        self.page.wait_for_function("document.getElementById('designCanvasPreview').width===1584")

    def tearDown(self):
        self.context.close();self.assertEqual(self.errors,[])

    def node(self,node_id,surface='pdfOverview'):
        return self.page.evaluate('([surface,id])=>window.canvasTestDesign.surfaces[surface].nodes.find(n=>n.id===id)',[surface,node_id])

    def drag(self,node_id,dx,dy,resize=False):
        box=self.page.locator('#designCanvasOverlay [data-node-id="'+node_id+'"]');box.scroll_into_view_if_needed()
        target=box.locator('.design-canvas-resize') if resize else box
        bounds=target.bounding_box();x=bounds['x']+bounds['width']*.5;y=bounds['y']+bounds['height']*.5
        self.page.mouse.move(x,y);self.page.mouse.down();self.page.mouse.move(x+dx,y+dy,steps=6);self.page.mouse.up()

    def test_drag_commits_once_and_keyboard_undo_keep_export_units_and_bindings(self):
        self.page.locator('#designCanvasSnap').uncheck();self.page.locator('#designCanvasAddText').click();initial=self.node('custom-text-1');self.assertEqual(self.page.evaluate('window.canvasTestCommits'),1)
        box=self.page.locator('#designCanvasOverlay [data-node-id="custom-text-1"]');box.scroll_into_view_if_needed();page_bounds=self.page.locator('#designCanvasPage').bounding_box();bounds=box.bounding_box();x=bounds['x']+bounds['width']*.5;y=bounds['y']+bounds['height']*.5
        self.page.mouse.move(x,y);self.page.mouse.down();self.page.mouse.move(x+20,y+15,steps=8);self.assertEqual(self.page.evaluate('window.canvasTestCommits'),1);self.page.mouse.up()
        moved=self.node('custom-text-1');self.assertAlmostEqual(moved['x'],initial['x']+20*1584/page_bounds['width'],delta=1);self.assertAlmostEqual(moved['y'],initial['y']+15*1120/page_bounds['height'],delta=1);self.assertEqual(self.page.evaluate('window.canvasTestCommits'),2)
        self.page.keyboard.press('ArrowRight');self.page.keyboard.press('Shift+ArrowDown');nudged=self.node('custom-text-1');self.assertAlmostEqual(nudged['x'],moved['x']+1,delta=.01);self.assertAlmostEqual(nudged['y'],moved['y']+10,delta=.01)
        self.page.keyboard.press('ControlOrMeta+z');self.page.keyboard.press('ControlOrMeta+z');self.assertAlmostEqual(self.node('custom-text-1')['x'],moved['x'],delta=.01)
        self.assertEqual(self.page.evaluate('window.canvasTestCard.fields.price'),80000)
        self.page.reload();self.page.wait_for_function('window.canvasTestReady===true');self.assertAlmostEqual(self.node('custom-text-1')['x'],moved['x'],delta=.01)

    def test_resize_photo_change_lock_and_duplicate_preserve_original(self):
        self.page.locator('#designCanvasSnap').uncheck();self.page.locator('#designCanvasAddPhoto').click();initial=self.node('custom-image-1');self.drag('custom-image-1',30,25,resize=True);resized=self.node('custom-image-1');self.assertGreater(resized['w'],initial['w']);self.assertGreater(resized['h'],initial['h'])
        self.page.locator('#designCanvasPhotoIndex').fill('2');self.page.locator('#designCanvasPhotoIndex').dispatch_event('change');self.assertEqual(self.node('custom-image-1')['bind'],'photo2')
        self.page.locator('#designCanvasLocked').check();locked=self.node('custom-image-1');self.drag('custom-image-1',20,20);self.assertEqual(self.node('custom-image-1')['x'],locked['x'])
        self.page.locator('#designCanvasLocked').uncheck();self.page.locator('#designCanvasDuplicate').click();copy=self.node('custom-image-1-copy-1');self.assertEqual(copy['bind'],'photo2');self.page.locator('#designCanvasDelete').click();self.assertIsNone(self.node('custom-image-1-copy-1'));self.assertEqual(self.node('custom-image-1')['bind'],'photo2')

    def test_text_bindings_style_and_alignment_do_not_change_card_data(self):
        self.page.locator('#designCanvasAddText').click();value='自定义 <img src=x onerror=alert(1)>'
        self.page.locator('#designCanvasText').fill(value);self.page.locator('#designCanvasText').dispatch_event('change');self.assertEqual(self.node('custom-text-1')['text'],value);self.assertEqual(self.page.locator('#designCanvasEditor img').count(),0)
        self.page.locator('#designCanvasBinding').select_option('price');self.page.locator('#designCanvasFontSize').fill('67');self.page.locator('#designCanvasFontSize').dispatch_event('change');self.page.locator('#designCanvasColor').evaluate("el=>{el.value='#b79665';el.dispatchEvent(new Event('change',{bubbles:true}));}");self.page.locator('#designCanvasAlign-center').click()
        node=self.node('custom-text-1');self.assertEqual(node['bind'],'price');self.assertEqual(node['style']['fontSize'],67);self.assertEqual(node['style']['color'],'#b79665');self.assertEqual(node['x'],(1584-node['w'])/2);self.assertEqual(self.page.evaluate('window.canvasTestCard.fields'),{'price':80000,'area':54})

    def test_mobile_surface_and_touch_gesture_fit_without_document_overflow(self):
        self.page.set_viewport_size({'width':393,'height':852});self.page.locator('#designCanvasSurface').select_option('intro');self.page.wait_for_function("window.canvasTestSurface==='intro'");self.page.locator('#designCanvasAddText').click();self.page.locator('#designCanvasSnap').uncheck()
        node=self.node('custom-text-1','intro');target=self.page.locator('#designCanvasOverlay [data-node-id="custom-text-1"]');target.scroll_into_view_if_needed();bounds=target.bounding_box();x=bounds['x']+bounds['width']*.5;y=bounds['y']+bounds['height']*.5
        target.dispatch_event('pointerdown',{'pointerId':33,'pointerType':'touch','clientX':x,'clientY':y,'button':0,'bubbles':True})
        self.page.locator('#designCanvasOverlay').dispatch_event('pointermove',{'pointerId':33,'pointerType':'touch','clientX':x+12,'clientY':y+12,'bubbles':True});self.page.locator('#designCanvasOverlay').dispatch_event('pointerup',{'pointerId':33,'pointerType':'touch','clientX':x+12,'clientY':y+12,'bubbles':True})
        self.assertGreater(self.node('custom-text-1','intro')['x'],node['x']);self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'),393);self.assertGreaterEqual(self.page.locator('#designCanvasText').evaluate('el=>parseFloat(getComputedStyle(el).fontSize)'),16);self.assertGreaterEqual(self.page.locator('#designCanvasAddText').bounding_box()['height'],44)

    def test_late_preview_cannot_restore_a_previous_card_and_reset_clears_undo(self):
        self.page.locator('#designCanvasAddText').click();self.page.wait_for_function("document.getElementById('designCanvasPreview').getContext('2d').getImageData(0,0,1,1).data[0]===255")
        self.page.evaluate("window.canvasTestDelay=350;window.canvasTestEditor.refresh({resetHistory:true})");self.page.wait_for_timeout(40)
        self.page.evaluate("window.canvasTestSetCard({id:'b',title:'房源 B',fields:{price:90000}});window.canvasTestEditor.refresh({resetHistory:true})")
        self.assertNotEqual(self.page.evaluate("Array.from(document.getElementById('designCanvasPreview').getContext('2d').getImageData(0,0,1,1).data.slice(0,3))"),[255,0,0])
        self.page.wait_for_function("document.getElementById('designCanvasPreview').getContext('2d').getImageData(0,0,1,1).data[1]===255");self.page.wait_for_timeout(450)
        self.assertEqual(self.page.evaluate("Array.from(document.getElementById('designCanvasPreview').getContext('2d').getImageData(0,0,1,1).data.slice(0,3))"),[0,255,0]);self.assertTrue(self.page.locator('#designCanvasUndo').is_disabled())

    def test_route_title_moves_independently_of_its_time_and_other_inner_nodes(self):
        self.page.locator('#designCanvasSurface').select_option('outro');self.page.locator('#designCanvasSnap').uncheck()
        nodes=self.page.evaluate("window.canvasTestDesign.surfaces.outro.nodes")
        title=next(node for node in nodes if node.get('bind')=='route1Title')
        time=next(node for node in nodes if node.get('bind')=='route1Time')
        self.assertNotEqual(title['id'],time['id'],'Route title and time must be separate editable text nodes')
        self.assertEqual(title['type'],'text');self.assertEqual(time['type'],'text')
        unchanged={node['id']:node for node in nodes if node['id']!=title['id']}
        self.drag(title['id'],18,7)
        moved=self.node(title['id'],'outro');self.assertGreater(moved['x'],title['x']);self.assertGreater(moved['y'],title['y']);self.assertEqual(moved['bind'],'route1Title')
        remaining=self.page.evaluate("window.canvasTestDesign.surfaces.outro.nodes")
        self.assertEqual({node['id']:node for node in remaining if node['id']!=title['id']},unchanged,'Moving one route caption must not move its time, line, stops or background')
        self.page.reload();self.page.wait_for_function('window.canvasTestReady===true');self.page.locator('#designCanvasSurface').select_option('outro')
        self.assertEqual(self.node(title['id'],'outro'),moved);self.assertEqual(self.node(time['id'],'outro'),time)
