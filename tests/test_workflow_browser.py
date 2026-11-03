"""Owned static fixture for workflow interaction; no import controller or live data."""
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
HARNESS = '''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/web/workflow.css"><style>*{box-sizing:border-box}body{margin:0;padding:12px;font:16px system-ui;color:#243b2d}main{max-width:1400px;margin:auto;min-width:0}.btn{padding:9px;border:1px solid #d7dfd5;border-radius:7px;background:white}input,select,textarea{padding:8px;font:inherit;border:1px solid #d7dfd5;border-radius:6px}.muted{color:#687466}</style><main><div id="mount"></div></main><script type="module">
import {setupWorkflow} from '/web/workflow.mjs';
const seed=[{id:'a',title:'房源 A',url:'https://www.cian.ru/rent/flat/1001/',address:'Москва, Лесная улица,12',rooms:'2',area:'52',price:'80000',metro:'Сокол',pickup:false,arrival:false,support:true,luggage:false,audience:'general',registration:'assist',output:'KEEP_A',tags:'#莫斯科租房 #莫斯科生活',privateNote:'PRIVATE_KEEP'},{id:'b',title:'房源 B',url:'https://www.avito.ru/moskva/kvartiry/test_1002',address:'Москва, Лесная улица,12',rooms:'2',area:'52',price:'80000',metro:'Сокол',pickup:false,arrival:false,support:true,luggage:false,audience:'general',registration:'assist',output:'KEEP_B',tags:'#莫斯科租房',workflow:{status:'ready'}}];
let cards=JSON.parse(localStorage.getItem('workflow-fixture')||'null')||seed;
window.workflowCards=cards;window.workflowMessages=[];
window.workflowUI=setupWorkflow({mount:document.getElementById('mount'),getObjects:()=>cards,saveObjects:next=>{cards=next;window.workflowCards=cards;localStorage.setItem('workflow-fixture',JSON.stringify(cards));return true;},onOpen:id=>window.workflowOpened=id,toast:text=>window.workflowMessages.push(text)});
window.workflowReady=true;
</script></html>'''


class WorkflowBrowserAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise unittest.SkipTest('Optional workflow acceptance needs Playwright and Chrome')
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
                    body = HARNESS.encode()
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/html;charset=utf-8')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
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
        self.page.wait_for_function('window.workflowReady===true')

    def tearDown(self):
        self.context.close()
        self.assertEqual(self.errors, [])

    def open_a(self):
        self.page.locator('.workflow-card').filter(has_text='房源 A').get_by_role('button', name='Рабочие данные', exact=True).click()

    def select_both(self):
        self.page.get_by_label('Выбрать 房源 A', exact=True).check()
        self.page.get_by_label('Выбрать 房源 B', exact=True).check()
        self.page.get_by_text('Массовые изменения и сверка дублей', exact=True).click()

    def test_status_tasks_assignment_and_deadline_survive_mobile_reload(self):
        self.assertEqual(self.page.locator('.workflow-column').count(), 5)
        self.open_a()
        self.page.get_by_label('Рабочий статус', exact=True).select_option('verified')
        self.page.get_by_label('Ответственный', exact=True).fill('Анна')
        self.page.get_by_label('Срок карточки · Москва', exact=True).fill('2026-11-04')
        self.page.get_by_role('button', name='Сохранить рабочие данные', exact=True).click()
        self.page.get_by_label('Новая задача', exact=True).fill('Уточнить этаж')
        self.page.get_by_label('Ответственный задачи', exact=True).fill('Пётр')
        self.page.get_by_label('Срок задачи · Москва', exact=True).fill('2026-11-03')
        self.page.get_by_role('button', name='Добавить задачу', exact=True).click()
        self.page.locator('.workflow-task input[type=checkbox]').check()
        self.page.reload(wait_until='domcontentloaded')
        self.page.wait_for_function('window.workflowReady===true')
        self.assertEqual(self.page.locator('.workflow-column[data-state=verified] .workflow-card').count(), 1)
        self.open_a()
        self.assertEqual(self.page.get_by_label('Ответственный', exact=True).input_value(), 'Анна')
        self.assertTrue(self.page.locator('.workflow-task input[type=checkbox]').is_checked())
        self.assertEqual(self.page.evaluate('workflowCards[0].privateNote'), 'PRIVATE_KEEP')
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'), 394)

    def test_bulk_changes_require_preview_and_leave_manual_posts_untouched(self):
        self.select_both()
        self.page.get_by_label('Встреча / трансфер', exact=True).select_option('yes')
        self.page.get_by_label('Профиль тегов', exact=True).select_option('student')
        self.page.get_by_role('button', name='Предпросмотр массового изменения', exact=True).click()
        self.assertEqual(self.page.evaluate('workflowCards.map(row=>row.pickup)'), [False, False])
        self.assertIn('Готов', self.page.locator('.workflow-preview').first.inner_text())
        self.assertIn('Проверен', self.page.locator('.workflow-preview').first.inner_text())
        self.page.get_by_role('button', name='Применить показанные изменения', exact=True).click()
        self.assertEqual(self.page.evaluate('workflowCards.map(row=>row.pickup)'), [True, True])
        self.assertEqual(self.page.evaluate('workflowCards.map(row=>row.audience)'), ['student','student'])
        self.assertEqual(self.page.evaluate('workflowCards.map(row=>row.output)'), ['KEEP_A','KEEP_B'])
        self.assertTrue(self.page.evaluate('workflowCards[1].workflow.needsRegeneration'))

    def test_duplicate_links_require_explicit_confirmation_and_are_reciprocal(self):
        self.select_both()
        self.page.get_by_role('button', name='Сравнить выбранные источники', exact=True).click()
        self.page.get_by_role('button', name='Подтвердить связь без объединения данных', exact=True).click()
        self.assertIn('Подтвердите', self.page.locator('.workflow-status').inner_text())
        self.page.get_by_label('Я сравнил объявления и подтверждаю, что это одна квартира').check()
        self.page.get_by_role('button', name='Подтвердить связь без объединения данных', exact=True).click()
        self.assertEqual(self.page.evaluate('workflowCards[0].workflow.links[0].id'), 'b')
        self.assertEqual(self.page.evaluate('workflowCards[1].workflow.links[0].id'), 'a')
        self.open_a()
        self.page.get_by_role('button', name='Убрать связь с b', exact=True).click()
        self.assertEqual(self.page.evaluate('workflowCards.map(row=>row.workflow.links.length)'), [0,0])

    def test_tag_statistics_show_entered_post_counts_source_and_date(self):
        self.open_a()
        self.page.get_by_text('Добавить реальные показатели опубликованного поста', exact=True).click()
        self.page.get_by_label('ID поста', exact=True).fill('post-1')
        self.page.get_by_label('Источник статистики · ссылка', exact=True).fill('https://www.xiaohongshu.com/explore/post-1')
        self.page.get_by_label('Дата статистики', exact=True).fill('2026-11-01')
        self.page.get_by_label('Просмотры · пусто если неизвестно', exact=True).fill('100')
        self.page.get_by_label('Обращения · пусто если неизвестно', exact=True).fill('3')
        self.page.get_by_role('button', name='Сохранить показатели', exact=True).click()
        self.assertIn('2026-11-01', self.page.locator('.workflow-editor').inner_text())
        self.assertIn('xiaohongshu.com', self.page.locator('.workflow-editor').inner_text())
        self.page.get_by_text('Теги, просмотры и обращения', exact=True).click()
        self.assertIn('#莫斯科租房', self.page.locator('.workflow>details').last.inner_text())
        self.assertIn('100', self.page.locator('.workflow>details').last.inner_text())
        self.assertEqual(self.page.evaluate('workflowCards[0].workflow.metrics[0].leads'), 3)

    def test_stale_bulk_preview_cannot_overwrite_background_changes(self):
        self.select_both()
        self.page.get_by_label('Встреча / трансфер', exact=True).select_option('yes')
        self.page.get_by_role('button', name='Предпросмотр массового изменения', exact=True).click()
        self.page.evaluate("workflowCards[0].price='90000'")
        self.page.get_by_role('button', name='Применить показанные изменения', exact=True).click()
        self.assertIn('изменились',self.page.locator('.workflow-status').inner_text())
        self.assertEqual(self.page.evaluate('workflowCards[0].price'), '90000')
        self.assertEqual(self.page.evaluate('workflowCards[0].pickup'), False)


if __name__ == '__main__':
    unittest.main()
