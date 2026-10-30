"""Ciliev studio persistence, private branding assets and v1 migration in Chrome."""
import io
import json
from pathlib import Path
import threading
import unittest

import test_phone_browser as acceptance


DELAYED_STUDIO_HARNESS='''<!doctype html><html lang="ru"><meta charset="utf-8"><div id="mount"></div><script type="module">
import {setupPresentationStudio} from '/phone/designer.mjs';
const state={photos:{},maps:{},mapImages:{},results:{}};
const card=id=>({identity:id,titles:['慢速照片房源 '+id],body:'已确认的房源条件。',fields:{price:80000,area:54,latitude:55.7558,longitude:37.6173}});
let current=card('A'),resolveDesign;
const raster=color=>{const canvas=document.createElement('canvas');canvas.width=64;canvas.height=48;const context=canvas.getContext('2d');context.fillStyle=color;context.fillRect(0,0,64,48);return canvas.toDataURL('image/png');};
const ui=setupPresentationStudio({mount:document.getElementById('mount'),getCard:()=>current,api:()=>new Promise(resolve=>{resolveDesign=resolve;}),getPhotos:()=>{const id=current.identity;return new Promise(resolve=>{state.photos[id]=resolve;});},getMapImage:value=>new Promise(resolve=>{state.maps[value.identity]=resolve;})});
state.design=()=>resolveDesign({template:null,updated:null,rules:{}});
state.start=id=>{current=card(id);ui.refresh().then(ok=>{state.results[id]=ok;});};
state.photo=(id,count)=>state.photos[id](Array.from({length:count},(_,index)=>raster('hsl('+index*37+' 40% 50%)')));
state.map=id=>{const dataUrl=raster(id==='A'?'#173b59':'#395c21');state.mapImages[id]=dataUrl;state.maps[id]({dataUrl,attribution:'© OpenStreetMap'});};
window.studioRace=state;state.start('A');
</script></html>'''


class CilievStudioAcceptance(unittest.TestCase):
    setUpClass=classmethod(acceptance.PhoneBrowserAcceptance.setUpClass.__func__)
    tearDownClass=classmethod(acceptance.PhoneBrowserAcceptance.tearDownClass.__func__)

    def setUp(self):
        acceptance.PhoneBrowserAcceptance.setUp(self)
        self.desktop_thread=threading.Thread(target=self.controller.serve_forever,daemon=True)
        self.desktop_thread.start()
        self.desktop='http://127.0.0.1:'+str(self.controller.server_port)
        self.page.set_viewport_size({'width':1440,'height':1100})

    def tearDown(self):
        self.controller.shutdown();self.desktop_thread.join()
        acceptance.PhoneBrowserAcceptance.tearDown(self)

    def open_studio(self):
        self.page.goto(self.desktop+'/listing/'+self.card_id,wait_until='domcontentloaded')
        self.page.wait_for_function("document.getElementById('output').value.includes('两间独立')")
        self.page.locator('#openPresentation').click()
        self.page.locator('#designCanvasEditor').wait_for()
        self.page.wait_for_function("document.getElementById('presentationStatus').textContent.includes('2 фото')")

    def token(self):
        return self.page.locator('meta[name="redcn-token"]').get_attribute('content')

    def saved_design(self):
        return self.context.request.get(self.desktop+'/api/local/design',headers={'X-Redcn-Token':self.token()}).json()['template']

    def save(self):
        self.page.locator('#presentationSave').click()
        self.page.wait_for_function("document.getElementById('presentationStatus').textContent.includes('сохранён на Mac')")

    def test_default_chinese_landscape_export_and_permanent_theme(self):
        self.open_studio()
        self.assertEqual(self.page.locator('#presentation-brand-name').input_value(),'ciliev')
        self.assertEqual(self.page.locator('#presentation-theme-pdf-background').input_value(),'#07162b')
        self.page.locator('#designCanvasOverlay [data-node-id="title"]').click()
        self.page.locator('#designCanvas-x').fill('137')
        self.page.locator('#designCanvas-x').dispatch_event('change')
        self.page.wait_for_function("JSON.parse(localStorage.getItem('most-presentation-design-v2')).template.surfaces.pdfOverview.nodes.find(n=>n.id==='title').x===137")
        self.page.locator('#presentation-preset').select_option('ciliev-navy')
        self.page.wait_for_function("JSON.parse(localStorage.getItem('most-presentation-design-v2')).template.preset==='ciliev-navy'")
        draft=self.page.evaluate("JSON.parse(localStorage.getItem('most-presentation-design-v2')).template")
        self.assertEqual(next(node for node in draft['surfaces']['pdfOverview']['nodes'] if node['id']=='title')['x'],137,'A palette must retain the configured canvas geometry')
        self.page.locator('#presentation-contact').fill('微信: CILIEV-TEST')
        self.page.locator('#presentation-contact').dispatch_event('change')
        self.page.locator('.presentation-theme-settings > summary').click()
        self.page.locator('#presentation-theme-pdf-accent').fill('#a38252')
        self.save()
        saved=self.saved_design()
        self.assertEqual(saved['version'],2)
        self.assertEqual(saved['brand']['contact'],'微信: CILIEV-TEST')
        self.assertEqual(saved['theme']['pdf']['accent'],'#a38252')
        self.assertEqual(next(node for node in saved['surfaces']['pdfOverview']['nodes'] if node['id']=='title')['x'],137)
        self.assertTrue(any(node['style']['color']=='#a38252' for node in saved['surfaces']['pdfOverview']['nodes']))
        with self.page.expect_download(timeout=30000) as output:self.page.locator('#presentationHtml').click()
        destination=Path(self.directory)/'ciliev-checked.html';output.value.save_as(str(destination));markup=destination.read_text()
        self.assertIn('<html lang="zh-CN">',markup)
        self.assertIn('@page{size:A4 landscape',markup)
        self.assertIn('ciliev',markup);self.assertIn('房地产',markup);self.assertIn('CILIEV-TEST',markup)
        self.assertNotIn('redcn-token',markup);self.assertNotIn('<script',markup)
        self.page.reload();self.page.locator('#openPresentation').click()
        self.page.wait_for_function("document.getElementById('presentation-contact').value.includes('CILIEV-TEST')")
        self.assertEqual(self.page.locator('#presentation-theme-pdf-accent').input_value(),'#a38252')
        self.page.locator('#presentationTabintro').click()
        self.page.wait_for_function("document.getElementById('presentationImagePreview').naturalWidth===1080")
        self.assertEqual(self.page.locator('#presentationImagePreview').evaluate('image=>image.naturalHeight'),1920)

    def test_logo_upload_saves_only_private_asset_reference_and_survives_reload(self):
        from PIL import Image
        self.open_studio()
        stream=io.BytesIO();Image.new('RGB',(48,48),'#b79665').save(stream,'PNG')
        self.page.locator('#presentation-logoUpload').set_input_files({'name':'ciliev-logo.png','mimeType':'image/png','buffer':stream.getvalue()})
        self.page.wait_for_function("document.getElementById('presentation-logoPreview').naturalWidth>0")
        self.save();saved=self.saved_design();asset_id=saved['brand']['logoAssetId']
        self.assertRegex(asset_id,r'^[a-f0-9]{32}$');self.assertNotIn('data:image',json.dumps(saved))
        path=self.desktop+'/api/local/design-images/'+asset_id
        self.assertEqual(self.context.request.get(path).status,403)
        response=self.context.request.get(path,headers={'X-Redcn-Token':self.token()})
        self.assertEqual(response.status,200);self.assertTrue(response.json()['dataUrl'].startswith('data:image/png;base64,'))
        self.page.reload();self.page.locator('#openPresentation').click()
        self.page.wait_for_function("document.getElementById('presentation-logoPreview').naturalWidth>0")
        self.assertEqual(self.saved_design()['brand']['logoAssetId'],asset_id)

    def test_old_template_migrates_without_losing_custom_contact_or_backup(self):
        import card_assets
        legacy={'version':1,'preset':'night','background':'#102030','foreground':'#f0eedd','accent':'#aa8844','contact':'微信: LEGACY-TEST','footer':'旧版品牌','blocks':[{'id':'description','enabled':False}]}
        card_assets.save_design(self.directory,legacy)
        self.open_studio()
        self.page.wait_for_function("document.getElementById('presentation-contact').value.includes('LEGACY-TEST')")
        self.assertEqual(self.page.locator('#presentation-theme-pdf-background').input_value(),'#102030')
        self.assertTrue(self.page.locator('#presentationLegacyExport').is_visible())
        with self.page.expect_download() as output:self.page.locator('#presentationLegacyExport').click()
        original=Path(output.value.path()).read_text();self.assertEqual(json.loads(original),legacy)
        self.save();saved=self.saved_design()
        self.assertEqual(saved['version'],2);self.assertEqual(saved['brand']['contact'],'微信: LEGACY-TEST')
        self.assertEqual(saved['theme']['pdf']['background'],'#102030')
        body=next(node for node in saved['surfaces']['pdfOverview']['nodes'] if node['bind']=='body')
        self.assertFalse(body['visible'])

    def test_remote_conflict_survives_reload_and_explicit_load_keeps_local_backup(self):
        import card_assets
        self.open_studio();self.save()
        self.page.locator('#presentation-contact').fill('微信: LOCAL-DRAFT')
        self.page.locator('#presentation-contact').dispatch_event('change')
        remote=self.saved_design();remote['brand']['contact']='微信: REMOTE-SAVED'
        card_assets.save_design(self.directory,remote)
        self.page.reload();self.page.locator('#openPresentation').click()
        self.page.wait_for_function("document.getElementById('presentation-contact').value.includes('LOCAL-DRAFT')")
        self.page.locator('#presentationSave').click()
        self.page.wait_for_function("document.getElementById('presentationStatus').textContent.includes('другом устройстве')")
        self.assertEqual(self.saved_design()['brand']['contact'],'微信: REMOTE-SAVED')
        self.page.locator('#presentationTemplateReload').click()
        self.page.wait_for_function("document.getElementById('presentation-contact').value.includes('REMOTE-SAVED')")
        with self.page.expect_download() as output:self.page.locator('#presentationDraftExport').click()
        previous=json.loads(Path(output.value.path()).read_text())
        self.assertEqual(previous['brand']['contact'],'微信: LOCAL-DRAFT')

    def test_delayed_initial_template_photos_and_map_finish_without_losing_newer_card(self):
        self.page.route('**/studio-delay-test',lambda route:route.fulfill(status=200,content_type='text/html',body=DELAYED_STUDIO_HARNESS))
        self.page.goto(self.base+'/studio-delay-test',wait_until='domcontentloaded')
        self.page.wait_for_function('!!window.studioRace')
        self.page.evaluate('window.studioRace.design()')
        self.page.wait_for_function('!!window.studioRace.photos.A')
        # Both assets remain pending longer than the template's 250ms repaint.
        self.page.wait_for_timeout(400)
        self.page.evaluate("window.studioRace.photo('A',9)")
        self.page.wait_for_function('!!window.studioRace.maps.A')
        self.page.wait_for_timeout(300)
        self.page.evaluate("window.studioRace.map('A')")
        self.page.wait_for_function('window.studioRace.results.A===true')
        self.assertIn('9 фото',self.page.locator('#presentationStatus').inner_text())
        self.assertTrue(self.page.evaluate("document.getElementById('presentationPreview').srcdoc.includes(window.studioRace.mapImages.A)"),'The delayed map raster must be embedded')
        self.page.evaluate("window.studioRace.start('B')")
        self.page.wait_for_function('!!window.studioRace.photos.B')
        self.page.evaluate("window.studioRace.start('C')")
        self.page.wait_for_function('!!window.studioRace.photos.C')
        self.page.evaluate("window.studioRace.photo('C',2)")
        self.page.wait_for_function('!!window.studioRace.maps.C')
        self.page.evaluate("window.studioRace.map('C')")
        self.page.wait_for_function('window.studioRace.results.C===true')
        self.page.evaluate("window.studioRace.photo('B',0)")
        self.page.wait_for_function('window.studioRace.results.B===false')
        # An obsolete empty photo response must not clear C's committed assets.
        self.page.locator('#presentation-brand-tagline').fill('持久预览')
        self.page.locator('#presentation-brand-tagline').dispatch_event('change')
        self.page.wait_for_function("document.getElementById('presentationPreview').srcdoc.includes('持久预览')")
        self.assertIn('2 фото',self.page.locator('#presentationStatus').inner_text())
        checks=self.page.evaluate("(()=>{const html=document.getElementById('presentationPreview').srcdoc;return {current:html.includes('慢速照片房源 C'),old:html.includes('慢速照片房源 B'),map:html.includes(window.studioRace.mapImages.C),oldMap:html.includes(window.studioRace.mapImages.A)}})()")
        self.assertEqual(checks,{'current':True,'old':False,'map':True,'oldMap':False})
