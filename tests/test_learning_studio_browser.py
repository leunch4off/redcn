"""Isolated learning panel acceptance; fixture responses never load real models."""
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'cli'))
import learning_service

HARNESS = '''<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>*{box-sizing:border-box}body{padding:12px;font:16px system-ui}input,textarea,select{display:block;max-width:100%;width:100%}label{display:block;margin:10px 0}button{padding:9px;margin:5px}main{max-width:900px;margin:auto}summary{padding:15px}</style><main id="mount"></main><script type="module">
import {setupLearningStudio} from '/web/learning-studio.mjs';
window.fields={price:80000,metro:'Прокшино'};window.output={title:'Старое название',body:'Старое описание',tags:'#莫斯科租房'};window.applied=[];
window.studio=setupLearningStudio({mount:document.getElementById('mount'),getGroup:()=>'listing-one',getFields:()=>fields,getOutput:()=>output,onApply:async candidate=>{applied.push(candidate);Object.assign(output,candidate);},api:async(path,data)=>{const response=await fetch('/api/'+path,{method:data?'POST':'GET',headers:{'Content-Type':'application/json'},...(data?{body:JSON.stringify(data)}:{})});const value=await response.json();if(!response.ok)throw Error(value.error);return value;}});window.ready=true;
</script>'''


class LearningStudioBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise unittest.SkipTest('Optional learning acceptance needs Playwright and Chrome')
        cls.runtime = sync_playwright().start()
        try:
            cls.browser = cls.runtime.chromium.launch(channel='chrome', headless=True)
        except Exception as error:
            cls.runtime.stop()
            raise unittest.SkipTest('Optional Chrome unavailable: '+str(error).splitlines()[0])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.runtime.stop()

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        directory = Path(self.folder.name)
        class Handler(SimpleHTTPRequestHandler):
            def __init__(handler, *args, **kwargs):
                super().__init__(*args, directory=str(ROOT), **kwargs)
            def log_message(handler, *args):
                pass
            def reply(handler, status, payload):
                raw = json.dumps(payload, ensure_ascii=False).encode()
                handler.send_response(status)
                handler.send_header('Content-Type', 'application/json; charset=utf-8')
                handler.send_header('Content-Length', str(len(raw)))
                handler.end_headers()
                handler.wfile.write(raw)
            def do_GET(handler):
                if handler.path == '/api/learning':
                    result=learning_service.dispatch(directory,'learning')
                    handler.reply(result[0],result[1])
                elif handler.path == '/':
                    raw = HARNESS.encode()
                    handler.send_response(200)
                    handler.send_header('Content-Type', 'text/html; charset=utf-8')
                    handler.end_headers()
                    handler.wfile.write(raw)
                else:
                    super().do_GET()
            def do_POST(handler):
                data = json.loads(handler.rfile.read(int(handler.headers['Content-Length'])))
                if data.get('action') == 'generate-preview':
                    handler.reply(200, {'status': 'review', 'title': '近地铁独立两居', 'body': '租金80000卢布，位于普罗克希诺。', 'tags': '#莫斯科租房', 'warnings': ['fixture review']})
                elif data.get('action') == 'translate-preview':
                    handler.reply(200, {'status': 'unavailable', 'warnings': ['fixture unavailable']})
                else:
                    try:
                        result = learning_service.dispatch(directory, 'learning', data)
                        handler.reply(result[0], result[1])
                    except ValueError as error:
                        handler.reply(400, {'error': str(error)})
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.context = self.browser.new_context(viewport={'width': 393, 'height': 852})
        self.page = self.context.new_page()
        self.errors = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.goto('http://127.0.0.1:'+str(self.server.server_port), wait_until='domcontentloaded')
        self.page.wait_for_function('window.ready===true')
        self.page.locator('summary').click()
        self.page.wait_for_function("document.body.textContent.includes('На проверке: 0')")

    def tearDown(self):
        self.context.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.folder.cleanup()
        self.assertEqual(self.errors, [])

    def test_manual_tag_statistics_wait_for_approval(self):
        self.page.get_by_label('Что запомнить', exact=True).select_option('tag_stat')
        self.page.get_by_label('Хештег', exact=True).fill('#莫斯科租房')
        self.page.get_by_label('Просмотры · ваша приблизительная оценка', exact=True).fill('25000')
        self.page.get_by_role('button', name='Запомнить', exact=True).click()
        self.page.get_by_role('button', name='Утвердить', exact=True).wait_for()
        self.assertIn('На проверке: 1', self.page.locator('body').inner_text())
        self.page.get_by_role('button', name='Утвердить', exact=True).click()
        self.page.wait_for_function("document.body.textContent.includes('утверждено: 1')")
        self.assertFalse(self.page.evaluate('document.documentElement.scrollWidth>innerWidth'))

    def test_preview_requires_explicit_apply_and_rejects_stale_card(self):
        self.page.get_by_role('button', name='Создать ИИ-черновик по карточке', exact=True).click()
        apply = self.page.get_by_role('button', name='Применить проверенный черновик', exact=True)
        self.page.wait_for_function("!Array.from(document.querySelectorAll('button')).find(e=>e.textContent==='Применить проверенный черновик').disabled")
        self.assertEqual(self.page.evaluate('applied.length'), 0)
        apply.click()
        self.page.wait_for_function('applied.length===1')
        self.page.get_by_role('button', name='Создать ИИ-черновик по карточке', exact=True).click()
        self.page.wait_for_function("!Array.from(document.querySelectorAll('button')).find(e=>e.textContent==='Применить проверенный черновик').disabled")
        self.page.evaluate('fields.price=95000')
        apply.click()
        self.page.wait_for_function("document.body.textContent.includes('Карточка изменилась')")
        self.assertEqual(self.page.evaluate('applied.length'), 1)

    def test_unavailable_translation_cannot_be_applied(self):
        self.page.get_by_label('Ваш исходный текст', exact=True).fill('Есть холодильник')
        self.page.get_by_role('button', name='Перевести локальной моделью', exact=True).click()
        self.page.wait_for_function("document.body.textContent.includes('fixture unavailable')")
        self.assertTrue(self.page.get_by_role('button', name='Применить проверенный перевод', exact=True).is_disabled())
        self.assertEqual(self.page.evaluate('applied.length'), 0)

    def test_auto_accept_toggle_explains_gate_and_does_not_enable_rollback_without_acceptance(self):
        toggle = self.page.get_by_role('checkbox', name='Принимать новые веса после проверки качества', exact=True)
        self.assertFalse(toggle.is_checked())
        toggle.check()
        self.page.wait_for_function("document.body.textContent.includes('Автоприёмка включена: только после полного сравнения качества')")
        self.assertTrue(toggle.is_checked())
        self.assertTrue(self.page.get_by_role('button', name='Вернуть предыдущие веса', exact=True).is_disabled())
        self.assertIn('Веса — обученные настройки модели', self.page.locator('body').inner_text())
        self.assertFalse((Path(self.folder.name)/'text-ai.json').exists())

    def test_grounded_advertising_is_distinguished_from_model_text_with_measured_rates(self):
        def preview(route):
            request = route.request.post_data_json
            if request.get('action') == 'generate-preview':
                self.assertEqual(request['styleMode'], 'advertising')
                route.fulfill(status=200, content_type='application/json', body=json.dumps({
                    'status': 'review', 'backend': 'grounded-advertising', 'fallback': True,
                    'title': '已确认房源', 'body': '租金80000卢布。', 'tags': '#莫斯科租房',
                    'ai': {'generatedTokens': 25, 'tokensPerSecond': 12.5, 'elapsedSeconds': 2.4}}))
            else:
                route.continue_()
        self.page.route('**/api/learning', preview)
        self.page.get_by_role('button', name='Создать ИИ-черновик по карточке', exact=True).click()
        self.page.wait_for_function("document.body.textContent.includes('ответ модели не прошёл проверку')")
        text = self.page.locator('body').inner_text()
        self.assertIn('Модель: 25 токенов', text)
        self.assertIn('12.5 ток/с', text)
        self.assertIn('Время: 2.4 с', text)


if __name__ == '__main__':
    unittest.main()
