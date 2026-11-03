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
window.studio=setupLearningStudio({mount:document.getElementById('mount'),canManageSettings:()=>window.owner!==false,getGroup:()=>'listing-one',getFields:()=>fields,getOutput:()=>output,onApply:async candidate=>{applied.push(candidate);Object.assign(output,candidate);},api:async(path,data)=>{const response=await fetch('/api/'+path,{method:data?'POST':'GET',headers:{'Content-Type':'application/json'},...(data?{body:JSON.stringify(data)}:{})});const value=await response.json();if(!response.ok)throw Error(value.error);return value;}});window.ready=true;
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
        self.page.locator('summary').first.click()
        self.page.wait_for_function("document.body.textContent.includes('На проверке: 0')")

    def tearDown(self):
        self.context.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.folder.cleanup()
        self.assertEqual(self.errors, [])

    def test_photo_model_selection_saves_only_settings_and_keeps_world_review_only(self):
        config={'enabled':True,'model':'redcn-furniture-v1','autoApply':True,
                'endpoint':'http://127.0.0.1:11434','maxSeconds':600}
        writes=[]
        def respond(route):
            if route.request.method=='POST':
                values=route.request.post_data_json;writes.append(values);config.update(values)
            route.fulfill(json=config)
        self.page.route('**/api/photo-ai',respond)
        self.page.get_by_text('Модель для анализа фотографий',exact=True).click()
        self.page.wait_for_function("document.querySelector('.learning-photo-settings select').value==='redcn-furniture-v1'")
        self.page.get_by_label('Локальная модель фото',exact=True).select_option('redcn-furniture-world-v2')
        panel=self.page.locator('.learning-photo-settings')
        self.assertIn('0 категорий для автоподтверждения · 0 автоматических фактов',panel.inner_text())
        self.assertIn('требуют подтверждения перед включением в описание',panel.inner_text())
        self.page.get_by_role('button',name='Сохранить модель фото',exact=True).click()
        self.page.wait_for_function("document.querySelector('.learning-studio').getAttribute('aria-busy')==='false'")
        self.assertEqual(writes,[{'model':'redcn-furniture-world-v2','enabled':True,'autoApply':True}])
        self.assertEqual(self.page.evaluate('output.body'),'Старое описание')
        self.assertEqual(self.page.evaluate('applied'),[])
        self.assertEqual(config['endpoint'],'http://127.0.0.1:11434')
        self.assertEqual(self.errors,[])

    def test_photo_settings_are_read_only_after_owner_permission_is_removed(self):
        writes=[]
        def respond(route):
            if route.request.method=='POST':writes.append(route.request.post_data_json)
            route.fulfill(json={'enabled':True,'model':'redcn-furniture-world-v2','autoApply':True})
        self.page.route('**/api/photo-ai',respond)
        self.page.evaluate('window.owner=false;window.studio.setPermissions()')
        self.page.get_by_text('Модель для анализа фотографий',exact=True).click()
        self.page.wait_for_function("document.querySelector('.learning-photo-settings').textContent.includes('0 категорий для автоподтверждения')")
        self.assertFalse(self.page.get_by_label('Локальная модель фото',exact=True).is_visible())
        self.assertFalse(self.page.get_by_role('button',name='Сохранить модель фото',exact=True).is_visible())
        self.assertEqual(writes,[])
        self.assertEqual(self.errors,[])

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

    def test_new_source_phrase_is_visible_then_reviewed_without_claiming_weight_training(self):
        from learning_discovery import capture_import, status as discovery_status
        from learning_memory import LearningMemory
        directory=Path(self.folder.name)
        capture_import(directory,{'fields':{'sourceDescription':'В спальне установлен письменный стол.'}},'fixture-card')
        self.page.evaluate('studio.refresh()')
        self.assertIn('уникальных фрагментов 1',self.page.locator('body').inner_text())
        self.assertIn('не уже выученные моделью слова',self.page.locator('body').inner_text())
        self.assertEqual(LearningMemory(directory).status()['approvedExamples'],0)
        self.page.get_by_label('Перевод новой исходной фразы',exact=True).fill('卧室配有书桌。')
        with self.page.expect_response(lambda response:response.url.endswith('/api/learning') and response.request.method=='POST' and response.request.post_data_json.get('action')=='review-source-phrase'):
            self.page.get_by_role('button',name='Утвердить перевод для памяти',exact=True).click()
        self.page.wait_for_function("document.body.textContent.includes('ожидают перевода и проверки 0')")
        self.assertEqual(LearningMemory(directory).status()['approvedExamples'],1)
        self.assertEqual(discovery_status(directory)['pendingTranslation'],0)
        self.page.evaluate('studio.clear()')
        self.assertNotIn('В спальне установлен письменный стол.',self.page.locator('body').inner_text())

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
        from learning_scheduler import load_config
        toggle = self.page.get_by_role('checkbox', name='Принимать новые веса после проверки качества', exact=True)
        self.assertFalse(toggle.is_checked())
        with self.page.expect_response(lambda response: response.url.endswith('/api/learning')
                and response.request.method=='POST'
                and response.request.post_data_json.get('action')=='scheduler-config') as saved:
            toggle.check()
        self.assertEqual(saved.value.status,200)
        self.assertEqual(saved.value.request.post_data_json,
                         {'action':'scheduler-config','automaticPromotion':True})
        self.page.wait_for_function("document.querySelector('.learning-studio').getAttribute('aria-busy')==='false'")
        self.assertTrue(toggle.is_checked())
        self.assertTrue(load_config(Path(self.folder.name))['automaticPromotion'])
        self.assertFalse(load_config(Path(self.folder.name))['enabled'])
        text=self.page.locator('body').inner_text()
        self.assertIn('Автоприёмка включена: только после сравнения качества',text)
        self.assertIn('только после полного сравнения с текущей',text)
        self.assertIn('проверочные факты без ошибок, отсутствие ухудшений и разные варианты текста',text)
        self.assertTrue(self.page.get_by_role('button', name='Вернуть предыдущие веса', exact=True).is_disabled())
        self.assertIn('Веса — обученные настройки модели',text)
        self.assertFalse((Path(self.folder.name)/'text-ai.json').exists())

    def test_grounded_advertising_is_distinguished_from_model_text_with_measured_rates(self):
        def preview(route):
            request = route.request.post_data_json
            if request.get('action') == 'generate-preview':
                # The primary composer now requests full individual prose; legacy
                # grounded responses must still be identified honestly in the UI.
                self.assertEqual(request['styleMode'], 'individual')
                self.assertEqual(request['groupId'], 'listing-one')
                self.assertEqual(request['fields']['price'], 80000)
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
        self.assertEqual(self.page.evaluate('window.applied'), [])
        self.assertEqual(self.page.evaluate('window.output.body'), 'Старое описание')

    def test_withdrawal_removes_approval_actions_and_shows_lineage(self):
        def action_response(response,action):
            return (response.url.endswith('/api/learning') and response.request.method=='POST'
                and response.request.post_data_json.get('action')==action)
        idle="document.querySelector('.learning-studio').getAttribute('aria-busy')==='false'"
        self.page.get_by_label('Что запомнить',exact=True).select_option('translation')
        self.page.get_by_label('Русский текст',exact=True).fill('Есть вымышленный стол')
        self.page.get_by_label('Китайский текст',exact=True).fill('有虚构书桌')
        with self.page.expect_response(lambda response:action_response(response,'record')) as recorded:
            self.page.get_by_role('button',name='Запомнить',exact=True).click()
        self.assertEqual(recorded.value.status,200);event_id=recorded.value.json()['event']['id']
        self.page.wait_for_function(idle)
        with self.page.expect_response(lambda response:action_response(response,'provenance')) as original:
            self.page.get_by_role('button',name='Происхождение и версии корпуса',exact=True).click()
        self.assertEqual(original.value.json()['id'],event_id)
        self.page.wait_for_function("document.querySelector('.learning-provenance')?.textContent.includes('operator')")
        self.page.wait_for_function(idle)

        # The initial counter already says "отозвано: 0". A broad substring
        # wait cannot prove withdrawal completed and can click through busy
        # refreshes. Hold this actual action so the causal boundary is tested.
        self.page.evaluate('''() => {
            const original=window.fetch.bind(window);
            window.fetch=(url,options)=>{
                if(options?.body && JSON.parse(options.body).action==='withdraw')
                    return new Promise(resolve=>{window.releaseWithdrawal=()=>resolve(original(url,options));});
                return original(url,options);
            };
        }''')
        self.page.get_by_role('button',name='Отозвать из обучения',exact=True).click()
        self.page.wait_for_function("typeof window.releaseWithdrawal==='function'")
        self.assertEqual(self.page.locator('.learning-studio').get_attribute('aria-busy'),'true')
        self.assertIsNone(learning_service.LearningMemory(Path(self.folder.name)).provenance(event_id)['withdrawal'])
        with self.page.expect_response(lambda response:action_response(response,'withdraw')) as withdrawn:
            self.page.evaluate('releaseWithdrawal()')
        self.assertEqual(withdrawn.value.status,200)
        self.assertTrue(withdrawn.value.json()['withdrawn'])
        self.assertFalse(withdrawn.value.json()['existingWeightInfluenceRemoved'])
        self.page.wait_for_function(idle)
        with self.page.expect_response(lambda response:action_response(response,'list') and response.request.post_data_json.get('status')=='withdrawn') as filtered:
            self.page.locator('select').filter(has=self.page.locator('option[value="withdrawn"]')).select_option('withdrawn')
        self.assertEqual(filtered.value.status,200)
        events=filtered.value.json()['events']
        self.assertEqual([(row['id'],row['status']) for row in events],[(event_id,'withdrawn')])
        self.page.wait_for_function(idle)
        row=self.page.locator('.learning-studio article').filter(has_text='Есть вымышленный стол')
        row.get_by_text('Перевод · отозвано из обучения',exact=True).wait_for()
        with self.page.expect_response(lambda response:action_response(response,'provenance')) as lineage:
            row.get_by_role('button',name='Происхождение и версии корпуса',exact=True).click()
        self.assertEqual(lineage.value.status,200)
        self.assertEqual(lineage.value.json()['id'],event_id)
        self.assertIsNotNone(lineage.value.json()['withdrawal'])
        row.locator('.learning-provenance').get_by_text('Разрешение отозвано. Влияние на уже обученные веса не удалено; затронутым моделям нужно переобучение.',exact=True).wait_for()
        self.assertEqual(self.page.get_by_role('button',name='Утвердить',exact=True).count(),0)
        self.assertFalse(self.page.evaluate('document.documentElement.scrollWidth>innerWidth'))

    def test_structure_mode_discloses_model_role_and_preserves_manual_text_on_exhaustion(self):
        calls=[]
        def preview(route):
            request=route.request.post_data_json
            if request.get('action')!='generate-preview':return route.continue_()
            calls.append(request)
            self.assertEqual(request['styleMode'],'advertising-structure')
            result={'modelContribution':'selection-of-layout-only','factualTextSource':'verified-renderer',
                'status':'review','title':'租住笔记','body':'租金80000卢布。','tags':'#莫斯科租房'}
            if len(calls)>1:result.update(status='blocked',variantsExhausted=True,warnings=['Доступные схемы этой карточки уже использованы. Текущий текст сохранён.'])
            route.fulfill(status=200,content_type='application/json',body=json.dumps(result))
        self.page.route('**/api/learning',preview)
        self.page.get_by_label('Способ подготовки объявления',exact=True).select_option('advertising-structure')
        generate=self.page.get_by_role('button',name='Создать ИИ-черновик по карточке',exact=True)
        generate.click()
        self.page.wait_for_function("document.body.textContent.includes('Модель выбрала подачу; фактические блоки сохранены')")
        body=self.page.get_by_label('Описание · редактируемый ИИ-черновик',exact=True)
        body.fill('Моя ручная редакция')
        generate.click()
        self.page.wait_for_function("document.body.textContent.includes('Доступные схемы этой карточки уже использованы')")
        self.assertEqual(body.input_value(),'Моя ручная редакция')
        self.assertTrue(self.page.get_by_role('button',name='Применить проверенный черновик',exact=True).is_disabled())
        self.assertEqual(self.page.evaluate('applied.length'),0)
        self.assertFalse(self.page.evaluate('document.documentElement.scrollWidth>innerWidth'))

    def test_late_model_answer_preserves_edits_and_cannot_repopulate_cleared_card(self):
        self.page.evaluate('''() => {
          const original=window.fetch;
          window.fetch=(url,options)=>{
            if(options?.body && JSON.parse(options.body).action==='generate-preview')
              return new Promise(resolve=>{window.finishPreview=()=>resolve(new Response(JSON.stringify({status:'review',title:'新标题',body:'MODEL RESULT',tags:'#莫斯科租房'}),{status:200,headers:{'Content-Type':'application/json'}}));});
            return original(url,options);
          };
        }''')
        body=self.page.get_by_label('Описание · редактируемый ИИ-черновик',exact=True)
        generate=self.page.get_by_role('button',name='Создать ИИ-черновик по карточке',exact=True)
        generate.click()
        self.page.wait_for_function("typeof window.finishPreview==='function'")
        body.fill('Ручной текст во время запроса')
        self.page.evaluate('finishPreview()')
        self.page.wait_for_function("document.body.textContent.includes('Вы изменили черновик во время генерации')")
        self.assertEqual(body.input_value(),'Ручной текст во время запроса')
        self.assertTrue(self.page.get_by_role('button',name='Применить проверенный черновик',exact=True).is_disabled())
        self.page.evaluate('window.finishPreview=null')
        generate.click()
        self.page.wait_for_function("typeof window.finishPreview==='function'")
        self.page.evaluate('studio.clear();finishPreview()')
        self.page.wait_for_function("document.querySelector('.learning-studio').getAttribute('aria-busy')==='false'")
        self.assertEqual(body.input_value(),'')
        self.assertEqual(self.page.get_by_label('Название · редактируемый ИИ-черновик',exact=True).input_value(),'')
        self.assertEqual(self.page.evaluate('applied.length'),0)

    def test_model_history_binds_selected_version_and_clears_private_screen(self):
        def history(route):
            if route.request.post_data_json.get('action')!='model-history':return route.continue_()
            route.fulfill(status=200,content_type='application/json',body=json.dumps({'total':2,'note':'Fixture version history',
                'candidates':[{'id':'one','weightsSha256':'a'*64,'selected':True,'state':'trained-unaccepted','receiptMatchesWeights':True,
                    'stepsCompleted':5,'examples':{'train':10,'validation':4},'seconds':4,'peakMlxBytes':500,
                    'historicalEvaluation':None}, {'id':'two','weightsSha256':'b'*64,'selected':False,'state':'paused','receiptMatchesWeights':True}]}))
        self.page.route('**/api/learning',history)
        self.page.get_by_text('Версии локальной модели',exact=True).click()
        self.page.wait_for_function("document.body.textContent.includes('Выбрана сейчас · aaaaaaaaaaaa')")
        text=self.page.locator('.model-history').inner_text()
        self.assertIn('Кандидат · bbbbbbbbbbbb',text)
        self.assertIn('Обучение прервано',text)
        self.assertNotIn('успешно принят',text)
        self.page.evaluate('studio.clear()')
        self.assertEqual(self.page.locator('.model-history article').count(),0)


if __name__ == '__main__':
    unittest.main()
