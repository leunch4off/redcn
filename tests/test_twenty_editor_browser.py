"""Full built editor acceptance on an isolated static fixture, never a controller."""
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]


class TwentyEditorBrowserAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise unittest.SkipTest('Optional editor acceptance needs Playwright and Chrome')
        cls.runtime = sync_playwright().start()
        try:
            cls.browser = cls.runtime.chromium.launch(channel='chrome', headless=True)
        except Exception as error:
            cls.runtime.stop()
            raise unittest.SkipTest('Optional Chrome unavailable: ' + str(error).splitlines()[0])

        class Handler(SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=str(ROOT / 'docs'), **kwargs)

            def log_message(self, *args):
                pass

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
        self.page.wait_for_function("document.documentElement.dataset.editorReady==='true'")

    def tearDown(self):
        self.context.close()
        self.assertEqual(self.errors, [])

    def fields(self):
        return self.page.evaluate("JSON.parse(localStorage.getItem('most-draft')).fields")

    def seed(self):
        self.page.locator('#source').fill('2-комнатная квартира. Общая площадь 52 м². Одна спальня, одна гостиная. Цена 80000 руб. Есть рабочий стол и холодильник.')
        self.page.locator('#extract').click()
        self.page.locator('#deposit').fill('40000')
        self.page.locator('#commission').fill('50')
        self.page.locator('#generate').click()
        self.assertIn('80,000', self.page.locator('#output').input_value())

    def test_saved_formulas_styles_history_and_manual_diff_in_real_built_editor(self):
        self.seed()
        self.assertEqual(self.fields()['bedrooms'], '1')
        self.assertEqual(self.fields()['livingRooms'], '1')
        self.page.locator('details:has(#initialPayment)>summary').click()
        self.assertIn('160', self.page.locator('#initialPayment').inner_text())
        self.assertNotIn('佣金', self.page.locator('#output').input_value())
        studio = self.page.locator('#contentStudio')
        studio.locator('summary').click()
        studio.get_by_label('Название своего стиля').fill('Спокойный')
        studio.get_by_label('Эмодзи', exact=True).select_option('none')
        studio.get_by_label('Пунктуация', exact=True).select_option('compact')
        studio.get_by_label('Обращение', exact=True).select_option('polite')
        studio.get_by_role('button', name='Сохранить стиль', exact=True).click()
        studio.get_by_label('Название формулы', exact=True).fill('Моя формула')
        studio.get_by_label('Шаблон с проверяемыми полями').fill('{city}｜{layout}｜{area}｜{price}')
        studio.get_by_role('button', name='Сохранить формулу', exact=True).click()
        self.page.locator('#audience').select_option('family')
        self.page.locator('#generate').click()
        self.assertRegex(self.page.locator('#output').input_value(), '家人|家庭')
        self.assertNotIn('🏠', self.page.locator('#output').input_value())
        self.assertIn('｜', self.page.locator('#outputTitle').input_value())
        self.assertEqual(self.fields()['studioProfile']['name'], 'Спокойный')
        manual = '手工保留的正文。'
        self.page.locator('#output').fill(manual)
        self.page.locator('#rotateTitles').click()
        self.assertEqual(self.page.locator('#output').input_value(), manual)
        self.page.locator('#rewriteCopy').click()
        self.assertEqual(self.page.locator('#output').input_value(), manual)
        studio.get_by_role('button', name='Оставить текущий текст', exact=True).click()
        self.assertEqual(self.page.locator('#output').input_value(), manual)
        self.page.locator('#rewriteCopy').click()
        studio.get_by_role('button', name='Применить новую версию', exact=True).click()
        self.assertNotEqual(self.page.locator('#output').input_value(), manual)
        self.page.locator('#outputTitle').fill('手工标题')
        self.page.locator('#output').focus()
        self.page.reload(wait_until='domcontentloaded')
        self.page.wait_for_function("document.documentElement.dataset.editorReady==='true'")
        self.assertEqual(self.page.locator('#outputTitle').input_value(), '手工标题')
        self.assertEqual(self.fields()['titleFormula'], '{city}｜{layout}｜{area}｜{price}')
        self.assertEqual(self.fields()['bedrooms'], '1')
        studio = self.page.locator('#contentStudio')
        studio.locator('summary').click()
        self.assertIn('手工标题', studio.get_by_label('Предыдущая версия', exact=True).inner_text())
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'), 394)

    def test_imported_rate_and_private_fee_metadata_survive_reload_then_block_wrong_monthly_generation(self):
        payload = {'fields':{'url':'https://www.avito.ru/moskva/kvartiry/test_123','source':'fixture','price':'3500','pricePeriod':'day','priceCurrency':'RUB','deposit':'1000','commission':'','commissionAmount':'500','commissionSource':'item-params','oneTimeFees':[{'label':'Уборка','amount':'250','currency':'RUB','source':'item-params'}],'bedrooms':'1','livingRooms':'1','publicationStatus':'removed','eligibleForNew':False},'titles':['原来标题'],'body':'原来正文','tags':'#莫斯科租房'}
        self.page.locator('#cliFile').set_input_files({'name':'fixture.json','mimeType':'application/json','buffer':json.dumps(payload).encode()})
        self.page.wait_for_function("JSON.parse(localStorage.getItem('most-draft'))?.fields?.url==='https://www.avito.ru/moskva/kvartiry/test_123'")
        before = self.fields()
        for key in ['pricePeriod','priceCurrency','commissionAmount','commissionSource','oneTimeFees','bedrooms','livingRooms','publicationStatus','eligibleForNew']:
            self.assertEqual(before[key],payload['fields'][key])
        self.page.reload(wait_until='domcontentloaded')
        self.page.wait_for_function("document.documentElement.dataset.editorReady==='true'")
        self.page.locator('#generate').click()
        self.assertRegex(self.page.locator('#review').inner_text().lower(), 'период|не за месяц')
        self.assertEqual(self.page.locator('#output').input_value(),'原来正文')
        self.page.locator('details:has(#initialPayment)>summary').click()
        self.assertIn('не месячную',self.page.locator('#initialPayment').inner_text())

    def test_studio_conflict_visible_and_foreign_currency_cannot_replace_post(self):
        self.seed()
        self.page.locator('#layout').select_option('studio')
        self.page.locator('#generate').click()
        self.assertIn('Studio', self.page.locator('#review').inner_text())
        self.assertNotIn('卧室数量', self.page.locator('#output').input_value())
        before = self.page.locator('#output').input_value()
        self.page.locator('#priceCurrency').select_option('USD')
        self.page.locator('#generate').click()
        self.assertIn('валют',self.page.locator('#review').inner_text().lower())
        self.assertEqual(self.page.locator('#output').input_value(),before)

    def test_loading_a_different_source_cannot_pair_old_manual_text_with_new_facts(self):
        self.seed()
        self.page.locator('#output').fill('OLD_MANUAL_PROPERTY')
        html='<h1 data-name="OfferTitle">1-комнатная квартира 36 м²</h1><div data-name="PriceInfo">60 000 ₽ в месяц</div><div data-name="Description">Есть холодильник.</div>'
        self.page.locator('#htmlFile').set_input_files({'name':'different.html','mimeType':'text/html','buffer':html.encode()})
        self.page.wait_for_function("document.getElementById('importStatus').textContent.includes('HTML разобран локально')")
        self.assertNotIn('OLD_MANUAL_PROPERTY',self.page.locator('#output').input_value())
        self.assertEqual(self.page.locator('#price').input_value(),'60000')
        self.assertEqual(self.page.locator('#area').input_value(),'36')
        self.assertIn('60,000',self.page.locator('#output').input_value())


if __name__ == '__main__':
    unittest.main()
