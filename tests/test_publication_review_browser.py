"""Disposable 393px browser acceptance for colleague publication review.

Transport fixtures exercise the production UI. They do not replace the
independent server actor, revision and publication-hash tests.
"""
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import unittest

ROOT=Path(__file__).resolve().parents[1]
HARNESS='''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/web/publication-review.css"><style>*{box-sizing:border-box}body{margin:0;padding:12px;font:16px system-ui}main{max-width:900px;margin:auto}.btn{min-height:44px;padding:8px;white-space:normal}textarea{font:inherit}.muted{color:#657367;font-size:13px}</style><main id="review"></main><script type="module">
import {setupPublicationReview} from '/web/publication-review.mjs';
const a='a'.repeat(32),b='b'.repeat(32),copy=value=>JSON.parse(JSON.stringify(value));
const base=id=>({version:1,cardId:id,revision:0,savedUpdated:10,currentHash:'c'.repeat(64),state:'not-submitted',review:null,history:[]});
window.reviewCards={[a]:base(a),[b]:{...base(b),revision:7,state:'approved',review:{submitted_by:'phone:other',comment:'Только карточка Б'}}};
window.reviewCardId=a;window.reviewActor='phone:submitter';window.reviewCalls=[];window.reviewMessages=[];window.holdNextGet=false;window.conflictNextPost=false;
const view=id=>{const value=copy(reviewCards[id]);value.canReview=value.state==='pending'&&value.review?.submitted_by!==reviewActor;return value;};
window.reviewApi=async(path,data)=>{
 const id=path.split('/').at(-1);reviewCalls.push({path,data:data?copy(data):null,actor:reviewActor});
 if(!data){const value=view(id);if(holdNextGet){holdNextGet=false;return new Promise(resolve=>{window.releaseReviewGet=()=>resolve(value);});}return value;}
 const card=reviewCards[id];
 if(conflictNextPost){conflictNextPost=false;card.revision+=4;card.savedUpdated+=1;throw Error('Конфликт: сохранённый пост изменился');}
 if(data.expectedRevision!==card.revision||data.expectedUpdated!==card.savedUpdated)throw Error('Конфликт версии проверки');
 if(data.action==='submit'){card.state='pending';card.review={submitted_by:reviewActor,comment:data.comment};}
 else if(card.state==='pending'&&card.review?.submitted_by!==reviewActor){card.state=data.action==='approve'?'approved':'rejected';card.review.comment=data.comment;}
 else throw Error('Нужна другая сессия коллеги');
 card.revision+=1;card.history.unshift({revision:card.revision,at:'2026-11-01T12:00:00Z',actor:reviewActor,action:data.action,comment:data.comment});return view(id);
};
window.reviewUI=setupPublicationReview({mount:document.getElementById('review'),api:reviewApi,getCardId:()=>reviewCardId,toast:value=>reviewMessages.push(value)});
await reviewUI.refresh();document.querySelector('.publication-review').open=true;window.reviewReady=true;
</script></html>'''


class PublicationReviewBrowserAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise unittest.SkipTest('Optional publication-review acceptance needs Playwright/Chrome')
        cls.runtime=sync_playwright().start()
        try:
            cls.browser=cls.runtime.chromium.launch(channel='chrome',headless=True)
        except Exception as error:
            cls.runtime.stop()
            raise unittest.SkipTest(str(error).splitlines()[0])
        class Handler(SimpleHTTPRequestHandler):
            def __init__(self,*args,**kwargs):
                super().__init__(*args,directory=str(ROOT),**kwargs)
            def log_message(self,*args):
                pass
            def do_GET(self):
                if self.path=='/':
                    body=HARNESS.encode()
                    self.send_response(200)
                    self.send_header('Content-Type','text/html; charset=utf-8')
                    self.send_header('Content-Length',str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    super().do_GET()
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()
        cls.url='http://127.0.0.1:'+str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.runtime.stop()
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.context=self.browser.new_context(viewport={'width':393,'height':852})
        self.page=self.context.new_page()
        self.errors=[]
        self.page.on('pageerror',lambda error:self.errors.append(str(error)))
        self.page.goto(self.url,wait_until='domcontentloaded')
        self.page.wait_for_function('window.reviewReady===true')

    def tearDown(self):
        self.context.close()
        self.assertEqual(self.errors,[])

    def button(self,name):
        return self.page.get_by_role('button',name=name,exact=True)

    def writes(self):
        return self.page.evaluate('reviewCalls.filter(call=>call.data)')

    def test_submit_cannot_self_approve_then_another_actor_can_approve(self):
        self.button('Отправить сохранённый пост').click()
        self.page.wait_for_function("document.querySelector('[role=status]').textContent.includes('Ожидает другого участника')")
        self.assertTrue(self.button('Одобрить пост').is_disabled())
        self.assertTrue(self.button('Вернуть на доработку').is_disabled())
        self.assertIn('Собственную отправку',self.page.get_by_role('status').inner_text())
        self.assertEqual(self.writes()[0]['data'],{'action':'submit','expectedRevision':0,'expectedUpdated':10,'comment':''})
        self.page.evaluate("reviewActor='phone:reviewer';reviewUI.refresh()")
        self.page.wait_for_function("[...document.querySelectorAll('button')].find(b=>b.textContent==='Одобрить пост').disabled===false")
        self.button('Одобрить пост').click()
        self.page.wait_for_function("document.querySelector('[role=status]').textContent.includes('Одобрен коллегой')")
        self.assertEqual(self.writes()[-1]['actor'],'phone:reviewer')
        self.assertEqual(self.writes()[-1]['data']['action'],'approve')
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'),394)

    def test_changed_saved_version_requires_new_submission(self):
        self.button('Отправить сохранённый пост').click()
        self.page.wait_for_function("reviewCards[reviewCardId].state==='pending'")
        self.page.evaluate("reviewCards[reviewCardId].savedUpdated=11;reviewCards[reviewCardId].state='stale';reviewActor='phone:reviewer';reviewUI.refresh()")
        self.assertIn('отправьте текущую сохранённую версию повторно',self.page.get_by_role('status').inner_text())
        self.assertTrue(self.button('Одобрить пост').is_disabled())
        self.assertFalse(self.button('Отправить сохранённый пост').is_disabled())
        self.button('Отправить сохранённый пост').click()
        self.page.wait_for_function("reviewCards[reviewCardId].state==='pending'")
        self.assertEqual(self.writes()[-1]['data']['expectedUpdated'],11)
        self.assertTrue(self.button('Одобрить пост').is_disabled())

    def test_switching_card_discards_delayed_previous_response(self):
        self.page.evaluate("() => {holdNextGet=true;window.delayedReview=reviewUI.refresh();return true;}")
        self.page.wait_for_function("typeof releaseReviewGet==='function'")
        self.page.evaluate("reviewCardId='b'.repeat(32);reviewUI.clear();reviewUI.refresh()")
        self.page.wait_for_function("document.querySelector('.publication-review-history').textContent.includes('Только карточка Б')")
        self.assertFalse(self.page.evaluate('async()=>{releaseReviewGet();return await delayedReview;}'))
        self.assertIn('Только карточка Б',self.page.locator('.publication-review-history').inner_text())
        self.assertIn('версия проверки 7',self.page.get_by_role('status').inner_text())
        self.assertEqual(self.writes(),[])

    def test_conflict_clears_action_state_and_never_retries_write_automatically(self):
        self.page.evaluate('conflictNextPost=true')
        self.button('Отправить сохранённый пост').click()
        self.page.wait_for_function("document.querySelector('[role=status]').textContent.includes('Обновите проверку перед повторным действием')")
        self.assertEqual(len(self.writes()),1)
        self.assertTrue(self.button('Отправить сохранённый пост').is_disabled())
        self.assertTrue(self.button('Одобрить пост').is_disabled())
        self.page.evaluate("[...document.querySelectorAll('button')].find(b=>b.textContent==='Отправить сохранённый пост').click()")
        self.assertEqual(len(self.writes()),1)
        self.button('Обновить проверку').click()
        self.page.wait_for_function("[...document.querySelectorAll('button')].find(b=>b.textContent==='Отправить сохранённый пост').disabled===false")
        self.button('Отправить сохранённый пост').click()
        self.page.wait_for_function("reviewCards[reviewCardId].state==='pending'")
        self.assertEqual(len(self.writes()),2)
        self.assertEqual(self.writes()[-1]['data']['expectedRevision'],4)
        self.assertEqual(self.writes()[-1]['data']['expectedUpdated'],11)

    def test_return_to_editor_requires_a_reason_and_keeps_comment_as_text(self):
        self.button('Отправить сохранённый пост').click()
        self.page.wait_for_function("reviewCards[reviewCardId].state==='pending'")
        self.page.evaluate("reviewActor='phone:reviewer';reviewUI.refresh()")
        self.page.wait_for_function("[...document.querySelectorAll('button')].find(b=>b.textContent==='Вернуть на доработку').disabled===false")
        self.button('Вернуть на доработку').click()
        self.assertIn('Укажите причину',self.page.get_by_role('status').inner_text())
        self.assertEqual(len(self.writes()),1)
        self.page.get_by_label('Комментарий к проверке поста',exact=True).fill('Проверьте площадь <img src=x onerror=alert(1)>')
        self.button('Вернуть на доработку').click()
        self.page.wait_for_function("document.querySelector('[role=status]').textContent.includes('Возвращён на доработку')")
        self.assertIn('<img src=x onerror=alert(1)>',self.page.locator('.publication-review-history').inner_text())
        self.assertEqual(self.page.locator('.publication-review-history img').count(),0)
        self.assertEqual(self.writes()[-1]['data']['action'],'reject')


if __name__=='__main__':unittest.main()
