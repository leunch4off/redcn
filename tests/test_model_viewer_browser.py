"""Isolated browser acceptance: licensed public scan, never user attachments."""
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import re
from pathlib import Path
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
HARNESS = '''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/web/model-viewer.css"><style>*{box-sizing:border-box}body{margin:0;padding:12px;font:16px system-ui;color:#20352b}main{max-width:1000px;margin:auto;min-width:0}.btn{padding:9px;border:1px solid #cbd6d0;border-radius:7px;background:white}select,input{font:inherit;padding:8px;border:1px solid #cbd6d0;border-radius:6px}.muted{color:#687466}</style><main><div id="mount"></div></main><script type="module">
import {setupModelViewer,parseModelPreview,pickModelPoint,modelViewRotation} from '/web/model-viewer.mjs';
window.modelMessages=[];window.modelBlobCalls=0;
const directory='/tests/fixtures/3d/';
window.modelUI=setupModelViewer({mount:document.getElementById('mount'),getAttachments:()=>[{id:'public-ply',name:'abalone-scan.geometry.ply',category:'lidar',bytes:225393},{id:'public-glb',name:'abalone-scan.geometry.glb',category:'lidar',bytes:110928},{id:'private-contract-not-requested',name:'contract.obj',category:'contract',bytes:12}],getBlob:async row=>{modelBlobCalls++;return await (await fetch(directory+row.name)).blob();},toast:text=>modelMessages.push(text)});
window.modelPickLocations=async()=>{const response=await fetch(directory+'abalone-scan.geometry.ply'),model=parseModelPreview(await response.arrayBuffer(),'scan.ply'),rotation=modelViewRotation(-.45,-.25),hits=[];for(let x=-.65;x<=.65;x+=.08)for(let y=-.65;y<=.65;y+=.08){const surface=pickModelPoint(model,x,y,rotation);if(surface)hits.push({x,y,surface});}const chosen=[hits[0],hits[Math.floor(hits.length*.8)]],canvas=document.querySelector('canvas'),rect=canvas.getBoundingClientRect(),aspect=rect.width/rect.height;return {meters:Math.hypot(...chosen[0].surface.map((value,i)=>value-chosen[1].surface[i])),points:chosen.map(point=>({x:rect.x+(point.x*.82/Math.max(1,aspect)+1)*rect.width/2,y:rect.y+(1-point.y*.82/Math.max(1,1/aspect))*rect.height/2}))};};
window.modelReady=true;
</script></html>'''


class ModelViewerBrowserAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise unittest.SkipTest('Optional model-viewer acceptance needs Playwright and Chrome')
        cls.runtime = sync_playwright().start()
        try:
            cls.browser = cls.runtime.chromium.launch(channel='chrome', headless=True)
        except Exception as error:
            cls.runtime.stop()
            raise unittest.SkipTest('Optional Chrome unavailable: ' + str(error).splitlines()[0])

        class Handler(SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=str(ROOT), **kwargs)

            def log_message(self, *args):
                pass

            def do_GET(self):
                if self.path == '/main':
                    data = (ROOT / 'docs' / 'index.html').read_text().replace('name="redcn-hosting" content="github-pages"', 'name="redcn-hosting" content="local"').replace('<meta charset="utf-8">', '<meta charset="utf-8"><meta name="redcn-token" content="public-fixture-token">').encode()
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/html;charset=utf-8')
                    self.send_header('Content-Length', str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                elif self.path.startswith('/api/local/'):
                    cls.api_requests.append((self.path, self.headers.get('X-Redcn-Token')))
                    card_id, file_id = 'a' * 32, 'b' * 32
                    if self.path == '/api/local/card-attachments/' + card_id + '/' + file_id:
                        data = (ROOT / 'tests/fixtures/3d/abalone-scan.geometry.ply').read_bytes()
                        self.send_response(200 if self.headers.get('X-Redcn-Token') == 'public-fixture-token' else 403)
                        self.send_header('Content-Type', 'application/octet-stream')
                    else:
                        value = {'enabled':False} if self.path == '/api/local/phone' else {'result_id':card_id,'status':'active','notes':'','attachments':[{'id':file_id,'name':'abalone-scan.geometry.ply','category':'lidar','bytes':225393}]} if self.path == '/api/local/card-archive/' + card_id else {'exports':[]} if self.path == '/api/local/card-exports/' + card_id else {'error':'Read-only isolated fixture'}
                        data = json.dumps(value).encode()
                        self.send_response(200)
                        self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                elif self.path == '/':
                    data = HARNESS.encode()
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/html;charset=utf-8')
                    self.send_header('Content-Length', str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                else:
                    super().do_GET()

            def do_POST(self):
                self.send_response(405)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"error":"Fixture never starts imports or writes state"}')

        cls.api_requests = []
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = 'http://127.0.0.1:' + str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.runtime.stop()
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.context = self.browser.new_context(viewport={'width':393, 'height':852}, has_touch=True)
        self.page = self.context.new_page()
        self.errors = []
        self.requests = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.on('request', lambda request: self.requests.append(request.url))
        self.page.goto(self.url, wait_until='domcontentloaded')
        self.page.wait_for_function('window.modelReady===true')
        self.page.locator('details>summary').click()

    def tearDown(self):
        self.context.close()
        self.assertEqual(self.errors, [])
        self.assertTrue(all(url.startswith(self.url + '/') for url in self.requests))

    def open_public(self, kind='ply'):
        self.page.get_by_label('3D-файл из досье', exact=True).select_option('public-' + kind)
        self.page.get_by_role('button', name='Открыть выбранный файл', exact=True).click()
        self.page.wait_for_function("document.querySelector('.model-viewer-status').textContent.includes('6 036') || document.querySelector('.model-viewer-status').textContent.includes('6 036')")
        self.assertTrue(self.page.locator('canvas').is_visible())

    def test_real_scan_render_surface_measure_and_mobile_controls(self):
        self.open_public()
        self.assertEqual(self.page.get_by_label('3D-файл из досье').locator('option').count(), 3)
        self.assertEqual(self.page.get_by_label('Единицы исходной модели', exact=True).input_value(), '')
        self.assertFalse(self.page.get_by_role('button', name='Выбрать две точки', exact=True).is_enabled())
        self.page.get_by_label('Единицы исходной модели', exact=True).select_option('m')
        self.page.get_by_role('button', name='Выбрать две точки', exact=True).click()
        self.assertEqual(self.page.get_by_label('Единицы исходной модели', exact=True).input_value(), 'm')
        self.assertEqual(self.page.get_by_role('button', name='Замер включён · выберите точки', exact=True).get_attribute('aria-pressed'), 'true')
        canvas = self.page.locator('canvas')
        canvas.scroll_into_view_if_needed()
        points = self.page.evaluate('modelPickLocations()')
        canvas_top = canvas.bounding_box()['y']
        for index, point in enumerate(points['points']):
            self.assertTrue(self.page.evaluate("point=>point.x>0&&point.x<innerWidth&&point.y>0&&point.y<innerHeight&&document.elementFromPoint(point.x,point.y)===document.querySelector('canvas')", point), 'A real surface click must reach the visible canvas')
            self.page.mouse.click(point['x'], point['y'])
            if index == 0:
                self.assertIn('Первая точка выбрана', self.page.locator('output').inner_text())
            self.assertAlmostEqual(canvas.bounding_box()['y'], canvas_top, delta=.1, msg='Focusing a surface must not scroll and shift the second measurement point')
        output = self.page.locator('output').inner_text()
        self.assertIn('≈', output)
        self.assertIn('Не инженерный', output)
        measured = float(re.search(r'≈\s*([\d\s,.\u00a0\u202f]+)\s*м', output)[1].replace('\u00a0','').replace('\u202f','').replace(' ','').replace(',','.'))
        self.assertGreater(measured, 0)
        self.assertAlmostEqual(measured, points['meters'], delta=.001)
        self.page.get_by_label('Единицы исходной модели', exact=True).select_option('cm')
        self.assertNotIn('≈', self.page.locator('output').inner_text())
        canvas.focus()
        self.page.keyboard.press('ArrowRight')
        self.page.keyboard.press('Shift+ArrowUp')
        self.page.get_by_role('button', name='Увеличить 3D-модель', exact=True).click()
        self.page.get_by_role('button', name='Уменьшить 3D-модель', exact=True).click()
        self.page.get_by_role('button', name='Исходный ракурс', exact=True).click()
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'), 394)
        self.assertEqual(self.page.evaluate('modelBlobCalls'), 1)

    def test_real_glb_local_obj_invalid_file_and_card_switch_clear(self):
        self.open_public('glb')
        self.page.get_by_label('Локальная 3D-модель', exact=True).set_input_files({'name':'local.obj','mimeType':'text/plain','buffer':b'mtllib https://attacker.invalid/x\nv 0 0 0\nv 3 0 0\nv 0 4 0\nf 1 2 3\n'})
        self.page.wait_for_function("document.querySelector('.model-viewer-status').textContent.includes('local.obj · OBJ')")
        self.assertEqual(self.page.evaluate('modelBlobCalls'), 1)
        self.page.get_by_label('Локальная 3D-модель', exact=True).set_input_files({'name':'broken.ply','mimeType':'text/plain','buffer':b'ply\nformat binary_little_endian 1.0\nend_header\n'})
        self.page.wait_for_function("document.querySelector('.model-viewer-status').textContent.includes('только PLY ASCII')")
        self.assertFalse(self.page.locator('canvas').is_visible())
        self.page.evaluate('modelUI.clear()')
        self.assertEqual(self.page.get_by_label('3D-файл из досье').locator('option').count(), 0)
        self.assertEqual(self.page.get_by_label('Единицы исходной модели', exact=True).input_value(), '')

    def test_built_desktop_uses_signed_explicit_attachment_read_and_clears_on_new_card(self):
        self.page.goto(self.url + '/main', wait_until='domcontentloaded')
        self.page.wait_for_function("document.documentElement.dataset.editorReady==='true'")
        payload = {'fields':{'resultId':'a' * 32,'price':'80000','area':'52','rooms':'2'},'titles':['Public model fixture'],'body':'Public fixture only','tags':'#莫斯科租房'}
        self.page.locator('#cliFile').set_input_files({'name':'public-card.json','mimeType':'application/json','buffer':json.dumps(payload).encode()})
        self.page.wait_for_function("document.querySelector('#resultLink').getAttribute('href')==='/listing/'+'a'.repeat(32)")
        self.assertFalse(any('/card-attachments/' in path for path, token in self.api_requests))
        viewer = self.page.locator('#modelViewerMount')
        viewer.locator('summary').click()
        viewer.get_by_label('3D-файл из досье', exact=True).select_option('b' * 32)
        self.assertFalse(any('/card-attachments/' in path for path, token in self.api_requests))
        viewer.get_by_role('button', name='Открыть выбранный файл', exact=True).click()
        self.page.wait_for_function("document.querySelector('#modelViewerMount .model-viewer-status').textContent.includes('вершин')")
        self.assertTrue(viewer.locator('canvas').is_visible())
        reads = [(path, token) for path, token in self.api_requests if '/card-attachments/' in path]
        self.assertEqual(reads, [('/api/local/card-attachments/' + 'a' * 32 + '/' + 'b' * 32, 'public-fixture-token')])
        self.page.locator('#newObject').click()
        self.assertFalse(viewer.locator('canvas').is_visible())
        self.assertEqual(viewer.get_by_label('3D-файл из досье', exact=True).locator('option').count(), 0)


if __name__ == '__main__':
    unittest.main()
