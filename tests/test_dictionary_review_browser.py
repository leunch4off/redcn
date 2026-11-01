"""Owned HTTP/SQLite fixture for dictionary approvals; no import controller."""
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'cli'))
import dictionary_reviews as reviews

HARNESS='''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/web/dictionary-review.css"><style>*{box-sizing:border-box}body{margin:0;padding:12px;font:16px system-ui}.btn{padding:10px;border:1px solid #bcc9af;border-radius:8px;background:white}main{max-width:900px;margin:auto}</style><main id="mount"></main><script type="module">
import {setupDictionaryReview} from '/web/dictionary-review.mjs';
window.activeRules={'стол':'旧书桌'};window.usedRules=[];window.translationRequests=0;
window.dictionaryReview=setupDictionaryReview({mount:document.getElementById('mount'),getRules:()=>window.activeRules,onUseRule:(ru,zh)=>{window.activeRules[ru]=zh;window.usedRules.push({ru,zh});},translatePhrase:async text=>{window.translationRequests++;return {text:'书桌',backend:'fixture-provider',needsReview:true,warnings:['Проверьте черновик']};},api:async(path,data)=>{const response=await fetch('/api/'+path,{method:data?'POST':'GET',headers:{'Content-Type':'application/json'},...(data?{body:JSON.stringify(data)}:{})});const result=await response.json();if(!response.ok)throw Error(result.error);return result;}});
window.dictionaryReady=true;
</script></html>'''


class DictionaryReviewBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise unittest.SkipTest('Optional dictionary acceptance needs Playwright and Chrome')
        cls.runtime=sync_playwright().start()
        try:cls.browser=cls.runtime.chromium.launch(channel='chrome',headless=True)
        except Exception as error:
            cls.runtime.stop();raise unittest.SkipTest('Optional Chrome unavailable: '+str(error).splitlines()[0])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close();cls.runtime.stop()

    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();directory=Path(self.folder.name)
        self.directory=directory
        class Handler(SimpleHTTPRequestHandler):
            def __init__(handler,*args,**kwargs):super().__init__(*args,directory=str(ROOT),**kwargs)
            def log_message(handler,*args):pass
            def reply(handler,status,payload):
                raw=json.dumps(payload,ensure_ascii=False).encode();handler.send_response(status);handler.send_header('Content-Type','application/json;charset=utf-8');handler.send_header('Content-Length',str(len(raw)));handler.end_headers();handler.wfile.write(raw)
            def do_GET(handler):
                if handler.path=='/':
                    raw=HARNESS.encode();handler.send_response(200);handler.send_header('Content-Type','text/html;charset=utf-8');handler.send_header('Content-Length',str(len(raw)));handler.end_headers();handler.wfile.write(raw)
                elif handler.path=='/api/dictionary-reviews':handler.reply(200,reviews.snapshot(directory))
                else:super().do_GET()
            def do_POST(handler):
                if handler.path!='/api/dictionary-reviews':handler.reply(404,{'error':'fixture route'});return
                result=reviews.dispatch(directory,'dictionary-reviews',json.loads(handler.rfile.read(int(handler.headers['Content-Length']))),actor='fixture-reviewer');handler.reply(result[0],result[1])
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.context=self.browser.new_context(viewport={'width':393,'height':852});self.page=self.context.new_page();self.errors=[];self.page.on('pageerror',lambda error:self.errors.append(str(error)))
        self.page.goto('http://127.0.0.1:'+str(self.server.server_port),wait_until='domcontentloaded');self.page.wait_for_function('window.dictionaryReady===true')

    def tearDown(self):
        self.context.close();self.server.shutdown();self.server.server_close();self.thread.join();self.folder.cleanup();self.assertEqual(self.errors,[])

    def open_reviews(self):
        self.page.locator('.dictionary-review>summary').first.click();self.page.wait_for_function("document.querySelector('.dictionary-review [role=status]').textContent.includes('Версия общего словаря:')")

    def test_translation_proposal_approval_and_personal_adoption_are_four_separate_actions(self):
        self.open_reviews();self.assertEqual(self.page.evaluate('translationRequests'),0)
        self.page.get_by_label('Русская фраза исправления',exact=True).fill('стол')
        self.page.get_by_label('Источник и пример исправления',exact=True).fill('Исправление оператора: письменный стол на фото')
        self.page.get_by_role('button',name='Перевести выбранную фразу',exact=True).click()
        self.page.wait_for_function("document.querySelector('[aria-label=\"Китайская пара для проверки\"]').value==='书桌'")
        self.assertEqual(self.page.evaluate('translationRequests'),1);self.assertEqual(reviews.snapshot(self.directory)['rules'],{})
        self.page.get_by_role('button',name='Предложить пару для утверждения',exact=True).click();self.page.get_by_role('button',name='Утвердить пару',exact=True).wait_for()
        self.assertEqual(reviews.snapshot(self.directory)['rules'],{})
        self.page.get_by_role('button',name='Утвердить пару',exact=True).click();self.page.get_by_role('button',name='Применить эту пару',exact=True).wait_for()
        self.assertEqual(reviews.snapshot(self.directory)['rules'],{'стол':'书桌'});self.assertEqual(self.page.evaluate('activeRules["стол"]'),'旧书桌')
        self.page.get_by_role('button',name='Применить эту пару',exact=True).click();self.assertEqual(self.page.evaluate('usedRules.length'),0)
        self.page.get_by_label('Заменить моё действующее правило после сравнения',exact=True).check();self.page.get_by_role('button',name='Применить эту пару',exact=True).click()
        self.assertEqual(self.page.evaluate('activeRules["стол"]'),'书桌');self.assertEqual(self.page.evaluate('usedRules.length'),1)

    def test_knowledge_note_is_a_proposal_with_source_and_not_an_automatic_dictionary_edit(self):
        self.page.evaluate("dictionaryReview.proposeFromKnowledge({ru:'шкаф',zh:'衣柜',source:'База знаний · пример старой и новой формулировки'})")
        self.page.wait_for_function("document.querySelector('.dictionary-review [role=status]').textContent.includes('Версия общего словаря:')")
        self.assertEqual(self.page.get_by_label('Русская фраза исправления',exact=True).input_value(),'шкаф');self.assertEqual(reviews.snapshot(self.directory)['rules'],{})
        self.page.get_by_role('button',name='Предложить пару для утверждения',exact=True).click();self.page.get_by_role('button',name='Утвердить пару',exact=True).wait_for()
        entry=reviews.snapshot(self.directory)['entries'][0];self.assertEqual(entry['origin'],'knowledge');self.assertIn('База знаний',entry['source']);self.assertEqual(entry['state'],'proposed')

    def test_stale_version_from_another_device_requires_refresh_and_preserves_form_draft(self):
        self.open_reviews();reviews.mutate(self.directory,{'action':'propose','expectedRevision':0,'ru':'шкаф','zh':'衣柜','source':'Другое устройство'},actor='another-device')
        self.page.get_by_label('Русская фраза исправления',exact=True).fill('стол');self.page.get_by_label('Китайская пара для проверки',exact=True).fill('书桌');self.page.get_by_label('Источник и пример исправления',exact=True).fill('Мой черновик')
        self.page.get_by_role('button',name='Предложить пару для утверждения',exact=True).click()
        self.page.wait_for_function("document.querySelector('.dictionary-review [role=status]').textContent.includes('другом устройстве')")
        self.assertEqual(self.page.get_by_label('Русская фраза исправления',exact=True).input_value(),'стол');self.assertEqual(reviews.snapshot(self.directory)['revision'],1)

    def test_pronunciation_has_sourced_pinyin_source_links_and_honest_unknown_state_on_393px(self):
        self.page.locator('.pronunciation-review>summary').click();self.page.get_by_label('Поиск проверенного произношения',exact=True).fill('卧室')
        self.assertEqual(self.page.locator('.pronunciation-list article').count(),1);self.assertIn('wò shì',self.page.locator('.pronunciation-list').inner_text())
        self.assertIn('dict.concised.moe.edu.tw/dictView.jsp?ID=43162',self.page.locator('.pronunciation-list a').get_attribute('href'))
        self.page.get_by_label('Поиск проверенного произношения',exact=True).fill('неизвестное')
        self.assertIn('не вычисляется по догадке',self.page.locator('.pronunciation-list').inner_text());self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'),394)


if __name__=='__main__':unittest.main()
