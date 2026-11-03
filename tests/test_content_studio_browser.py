"""Owned isolated Chrome acceptance for content tools; no source imports/controller."""
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import unittest


ROOT = Path(__file__).resolve().parents[1]
HARNESS = '''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/web/content-studio.css"><style>*{box-sizing:border-box}body{margin:0;padding:12px;font:16px system-ui;color:#243b2d}main{max-width:850px;margin:auto}.btn{padding:10px;min-height:44px;border:1px solid #d7dfd5;border-radius:8px;background:white}textarea,input,select{padding:8px;font:inherit;border:1px solid #d7dfd5;border-radius:6px}main>label{display:block}main>label>input,main>label>textarea{display:block;width:100%}.muted{color:#687466;font-size:13px}</style><main><label>Текущий заголовок<input id="title"></label><label>Текущее описание<textarea id="body"></textarea></label><label>Текущие теги<input id="tags"></label><button id="regen" type="button">Перегенерировать всё</button><button id="titleRegen" type="button">Новые заголовки</button><div id="mount"></div></main><script type="module">
import {setupContentStudio,renderTitleFormula} from '/web/content-studio.mjs';
const read=(key,fallback)=>JSON.parse(localStorage.getItem(key)||'null')??fallback,write=(key,value)=>{localStorage.setItem(key,JSON.stringify(value));return true;};
const stored=read('content-test-state',null);
let state=stored||{fields:{url:'https://www.cian.ru/rent/flat/123/',editorIdentity:'acceptance-editor-a'},output:{title:'原来标题',body:'原来正文。',tags:'#莫斯科租房'}};
const save=()=>write('content-test-state',state),render=()=>{for(const key of ['title','body','tags'])document.getElementById(key).value=state.output[key];};
window.contentState=state;window.contentMessages=[];window.contentCommits=0;
const commit=value=>{state.output={...state.output,...value};window.contentCommits++;render();save();};
window.contentStudio=setupContentStudio({mount:document.getElementById('mount'),read,write,getFields:()=>state.fields,getOutput:()=>({...state.output}),setOutput:commit,toast:message=>window.contentMessages.push(message),onProfileChange:value=>{Object.assign(state.fields,value);save();},previewTitle:formula=>renderTitleFormula(formula,{city:'莫斯科',layout:'两居室',price:'8W/月',area:'52㎡'})});
window.contentStudio.applySettings(state.fields);window.contentStudio.markCommitted();if(stored)for(const key of ['title','body','tags'])if(state.output[key]?.trim())window.contentStudio.markManual(key);render();
for(const key of ['title','body','tags'])document.getElementById(key).addEventListener('input',event=>{state.output[key]=event.target.value;window.contentStudio.markManual(key);save();});
document.getElementById('regen').onclick=()=>window.contentStudio.requestRegeneration({title:'新标题',body:'新版正文。',tags:'#莫斯科 #租房'},commit);
document.getElementById('titleRegen').onclick=()=>window.contentStudio.requestRegeneration({title:'轮换标题'},commit);
window.contentReady=true;
</script></html>'''


class ContentStudioBrowserAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise unittest.SkipTest('Optional content acceptance needs Playwright and Chrome')
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
                if self.path == '/':
                    value = HARNESS.encode()
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/html; charset=utf-8')
                    self.send_header('Content-Length', str(len(value)))
                    self.end_headers()
                    self.wfile.write(value)
                else:
                    super().do_GET()

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
        self.context = self.browser.new_context(viewport={'width':393, 'height':852})
        self.page = self.context.new_page()
        self.errors = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.goto(self.url, wait_until='domcontentloaded')
        self.page.wait_for_function('window.contentReady===true')
        self.page.locator('summary').click()

    def tearDown(self):
        self.context.close()
        self.assertEqual(self.errors, [])

    def test_profile_formula_and_unicode_history_persist_on_mobile_reload(self):
        self.page.get_by_label('Название своего стиля').fill('Строго без эмодзи')
        self.page.get_by_label('Пунктуация').select_option('fullwidth')
        self.page.get_by_label('Эмодзи', exact=True).select_option('none')
        self.page.get_by_label('Обращение').select_option('polite')
        self.page.get_by_role('button', name='Сохранить стиль', exact=True).click()
        self.page.get_by_label('Название формулы').fill('Площадь и цена')
        self.page.get_by_label('Шаблон с проверяемыми полями').fill('{city}｜{layout}｜{area}｜{price}')
        self.page.get_by_role('button', name='Предпросмотр', exact=True).click()
        self.assertEqual(self.page.locator('.content-studio-preview').inner_text(), '莫斯科｜两居室｜52㎡｜8W/月')
        self.page.get_by_role('button', name='Сохранить формулу', exact=True).click()
        self.page.get_by_role('button', name='Запомнить текущий заголовок').click()
        self.page.reload()
        self.page.wait_for_function('window.contentReady===true')
        self.page.locator('summary').click()
        self.assertEqual(self.page.get_by_label('Пунктуация').input_value(), 'fullwidth')
        self.assertEqual(self.page.get_by_label('Эмодзи', exact=True).input_value(), 'none')
        self.assertEqual(self.page.get_by_label('Обращение').input_value(), 'polite')
        self.assertIn('{price}', self.page.get_by_label('Шаблон с проверяемыми полями').input_value())
        self.assertIn('原来标题', self.page.get_by_label('Предыдущая версия').inner_text())
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'), 393)
        self.assertGreaterEqual(self.page.get_by_label('Шаблон с проверяемыми полями').evaluate('el=>parseFloat(getComputedStyle(el).fontSize)'), 16)

    def test_manual_body_survives_title_rotation_and_requires_diff_before_replacement(self):
        self.page.locator('#body').fill('手动编辑保留冰箱。')
        self.page.locator('#titleRegen').click()
        self.assertEqual(self.page.locator('#title').input_value(), '轮换标题')
        self.assertEqual(self.page.locator('#body').input_value(), '手动编辑保留冰箱。')
        self.page.locator('#regen').click()
        self.assertEqual(self.page.locator('#body').input_value(), '手动编辑保留冰箱。')
        self.assertIn('手动编辑保留冰箱', '\n'.join(self.page.locator('.content-studio-diff del').all_inner_texts()))
        self.assertIn('新版正文', '\n'.join(self.page.locator('.content-studio-diff ins').all_inner_texts()))
        self.page.get_by_role('button', name='Оставить текущий текст').click()
        self.assertEqual(self.page.locator('#body').input_value(), '手动编辑保留冰箱。')
        self.page.locator('#regen').click()
        self.page.get_by_role('button', name='Применить новую версию').click()
        self.assertEqual(self.page.locator('#body').input_value(), '新版正文。')

    def test_title_restore_preserves_body_tags_and_ignores_other_listing(self):
        self.page.evaluate("contentStudio.recordTitle('已选标题 A','selected');contentStudio.recordTitle('已选标题 B','selected')")
        self.page.get_by_label('Предыдущая версия').select_option(index=1)
        self.page.get_by_role('button', name='Восстановить выбранный заголовок').click()
        self.assertEqual(self.page.locator('#title').input_value(), '已选标题 A')
        self.assertEqual(self.page.locator('#body').input_value(), '原来正文。')
        self.assertEqual(self.page.locator('#tags').input_value(), '#莫斯科租房')
        self.page.evaluate("contentState.fields.url='https://www.cian.ru/rent/flat/124/';contentStudio.refresh()")
        self.assertEqual(self.page.get_by_label('Предыдущая версия').locator('option').count(), 0)

    def test_stale_preview_does_not_overwrite_edits_and_formula_text_never_executes(self):
        self.page.locator('#body').fill('作者版本。')
        self.page.locator('#regen').click()
        self.page.locator('#body').fill('较新的作者版本。')
        self.page.get_by_role('button', name='Применить новую версию').click()
        self.assertEqual(self.page.locator('#body').input_value(), '较新的作者版本。')
        self.assertIn('изменились после предпросмотра', self.page.evaluate('contentMessages.at(-1)'))
        self.page.get_by_label('Название формулы').fill('Без исполнения')
        self.page.get_by_label('Шаблон с проверяемыми полями').fill('<img src=x onerror="window.injected=true"> {city}')
        self.page.get_by_role('button', name='Предпросмотр', exact=True).click()
        self.assertIn('<img', self.page.locator('.content-studio-preview').inner_text())
        self.assertEqual(self.page.locator('#mount img').count(), 0)
        self.assertIsNone(self.page.evaluate('window.injected'))
        self.page.get_by_label('Шаблон с проверяемыми полями').fill('{commission}')
        self.page.get_by_role('button', name='Сохранить формулу', exact=True).click()
        self.assertIn('переменная', self.page.evaluate('contentMessages.at(-1)'))

    def test_manual_post_stays_protected_after_reloading_saved_editor(self):
        self.page.locator('#body').fill('作者手动保存的正文。')
        self.page.reload()
        self.page.wait_for_function('window.contentReady===true')
        self.page.locator('summary').click()
        self.page.locator('#regen').click()
        self.assertEqual(self.page.locator('#body').input_value(), '作者手动保存的正文。')
        self.assertTrue(self.page.get_by_role('button', name='Применить новую версию').is_visible())
        self.assertIn('作者手动保存的正文', '\n'.join(self.page.locator('.content-studio-diff del').all_inner_texts()))
        self.page.get_by_role('button', name='Оставить текущий текст').click()
        self.assertEqual(self.page.locator('#body').input_value(), '作者手动保存的正文。')


if __name__ == '__main__':
    unittest.main()
