"""Section-driven import checks: immediate ready paths and bounded missing data."""
import sys
import tempfile
import threading
import time
import unittest
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'cli'))
import browser_import

URL = 'https://www.cian.ru/rent/flat/300691182/'
TEXT = '2-комнатная квартира, 54 м². Аренда 80 000 ₽. Изолированные комнаты. Кухня, шкаф и рабочий стол.'
IMAGE = 'https://images.cdn-cian.ru/images/section-room.jpg'


def snapshot(text=TEXT, images=()):
    return {'text':text, 'fallback':text, 'body':text, 'title':'Квартира',
            'images':list(images), 'metadata':''}


class Clock:
    def __init__(self): self.value = 10
    def now(self): return self.value


class SectionPage:
    def __init__(self, clock, states=None):
        self.clock = clock
        self.url = URL
        self.states = states or {}
        self.section = None
        self.probes = []
        self.waits = []
        self.frames = 0
        self.restored = False

    def is_closed(self): return False

    def evaluate(self, expression, argument=None):
        if expression == browser_import.SECTION_PROBE:
            self.section = argument['section']
            self.probes.append(dict(argument))
            rows = self.states.get(self.section)
            if rows:
                return rows.pop(0) if len(rows)>1 else rows[0]
            return {'found':True, 'ready':True, 'recognized':True,
                    'loadedVisibleImages':1 if self.section=='gallery' else 0}
        if expression == browser_import.SECTION_FRAME:
            self.frames += 1
            self.clock.value += .016
            return None
        if expression == browser_import.SCROLL_TOP:
            self.restored = True
            return None
        if expression == browser_import.SNAPSHOT:
            return snapshot(TEXT+(' Есть пианино.' if self.section=='characteristics' else ''), [IMAGE])
        raise AssertionError('Unexpected browser operation')

    def wait_for_timeout(self, milliseconds):
        self.waits.append(milliseconds)
        self.clock.value += milliseconds/1000


class SectionCaptureTests(unittest.TestCase):
    def run_capture(self, page, clock, **kwargs):
        with patch.object(browser_import.time, 'monotonic', clock.now):
            return browser_import.capture_sections(page, snapshot(), clock.now()+20,
                lambda *_args, **_kwargs: None, **kwargs)

    def test_loaded_sections_advance_immediately_and_keep_visible_photo_candidates(self):
        clock = Clock();page = SectionPage(clock);reports = []
        result, audit = self.run_capture(page, clock,
            status=lambda message, **fields: reports.append((message, fields)))
        visits = [row['section'] for row in page.probes if row.get('scroll')]
        self.assertEqual(visits, ['gallery','description','characteristics'])
        self.assertEqual(page.waits, [], 'A ready block must not pay a loading delay')
        self.assertEqual(page.frames, 0, 'Already loaded sections must not wait for a rendering frame')
        self.assertIn('пианино', result['text']);self.assertEqual(result['images'], [IMAGE])
        self.assertTrue(audit['completed']);self.assertFalse(audit['bounded'])
        self.assertFalse(audit['pageBottomReached'])
        self.assertEqual([row['reads'] for row in audit['sections']], [1,1,1])
        self.assertLess(audit['seconds'], .1);self.assertTrue(page.restored)
        self.assertEqual([fields['section'] for _,fields in reports if fields.get('sectionReady')], visits)

    def test_pending_image_polls_only_until_ready_then_goes_to_description(self):
        clock = Clock();page = SectionPage(clock, {'gallery':[
            {'found':True,'ready':False}, {'found':True,'ready':False},
            {'found':True,'ready':False}, {'found':True,'ready':True}]})
        _, audit = self.run_capture(page, clock)
        self.assertTrue(audit['completed'])
        self.assertEqual(page.waits, [80,80])
        self.assertEqual(audit['sections'][0]['reads'], 3)
        self.assertEqual(audit['sections'][1]['reads'], 1)

    def test_missing_layout_is_bounded_and_reports_incomplete_sections(self):
        clock = Clock();page = SectionPage(clock, {kind:[{'found':False,'ready':False}]
            for kind in ('gallery','description','characteristics')})
        _, audit = self.run_capture(page, clock, budget=.6, section_wait=.2)
        self.assertFalse(audit['completed']);self.assertTrue(audit['bounded'])
        self.assertEqual(audit['scrolls'], 0)
        self.assertLessEqual(audit['seconds'], .602)
        self.assertTrue(page.restored)

    def test_manual_challenge_does_not_consume_short_capture_budget(self):
        clock=Clock();page=SectionPage(clock);original=page.evaluate;challenge_pending=[True]
        def evaluate(expression, argument=None):
            if expression==browser_import.SNAPSHOT and challenge_pending:
                challenge_pending.pop();return snapshot('Подтвердите, что вы не робот')
            return original(expression, argument)
        page.evaluate=evaluate
        result,audit=self.run_capture(page,clock)
        self.assertTrue(audit['completed']);self.assertIn('пианино',result['text'])
        self.assertEqual(page.waits,[500]);self.assertGreaterEqual(audit['seconds'],.5)
        self.assertLess(audit['activeSeconds'],.1)

    def test_cancel_and_untrusted_redirect_are_checked_before_scroll(self):
        clock = Clock();page = SectionPage(clock);event = threading.Event();event.set()
        with self.assertRaisesRegex(ValueError, 'отменён'):
            self.run_capture(page, clock, cancel=event)
        self.assertEqual(page.probes, []);self.assertTrue(page.restored)
        page = SectionPage(clock)
        def reject(*_args, **_kwargs): raise ValueError('Неверный сайт')
        with self.assertRaisesRegex(ValueError, 'Неверный сайт'):
            browser_import.capture_sections(page, snapshot(), time.monotonic()+20, reject)
        self.assertEqual(page.probes, []);self.assertTrue(page.restored)

    def test_browser_ready_sections_skip_three_text_reads_and_page_walk(self):
        for initial_error in (None,'Execution context was destroyed','Cannot find context with specified id','Unexpected DOM script failure'):
            with self.subTest(initial_error=initial_error):
                api,pw,context,page=[MagicMock() for _ in range(4)]
                page.url=URL;page.is_closed.return_value=False;pending=[initial_error] if initial_error else []
                api.Error=type('FakeBrowserError',(Exception,),{})
                api.TimeoutError=type('FakeBrowserTimeout',(api.Error,),{})
                def evaluate(expression, argument=None):
                    if expression==browser_import.SECTION_PROBE:
                        return {'recognized':True,'found':True,'ready':True}
                    if expression==browser_import.SCROLL_TOP:return None
                    if expression==browser_import.SNAPSHOT:
                        if pending:raise api.Error(pending.pop())
                        return snapshot(images=[IMAGE])
                    raise AssertionError('The ready path must not run a page walk or wait for frames')
                page.evaluate.side_effect=evaluate;context.new_page.return_value=page
                pw.chromium.launch_persistent_context.return_value=context
                api.sync_playwright.return_value.__enter__.return_value=pw
                with tempfile.TemporaryDirectory() as folder,patch.dict(sys.modules,{'playwright':MagicMock(),'playwright.sync_api':api}):
                    if initial_error=='Unexpected DOM script failure':
                        with self.assertRaisesRegex(ValueError,'Unexpected DOM script failure'):
                            browser_import.load_browser(URL,Path(folder),lambda markup,url:{'images':[]},lambda url,**kwargs:url)
                    else:
                        result=browser_import.load_browser(URL,Path(folder),lambda markup,url:{'images':[]},lambda url,**kwargs:url)
                        self.assertEqual(result['capture']['strategy'],'sections')
                        self.assertTrue(result['capture']['completed'])
                if initial_error and initial_error!='Unexpected DOM script failure':page.wait_for_timeout.assert_called_once_with(100)
                else:page.wait_for_timeout.assert_not_called()
                context.close.assert_called_once()


class SectionBrowserAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise unittest.SkipTest('Optional browser acceptance needs Playwright and Chrome')
        cls.runtime = sync_playwright().start()
        try:
            cls.browser = cls.runtime.chromium.launch(channel='chrome', headless=True)
        except Exception as error:
            cls.runtime.stop()
            raise unittest.SkipTest('Optional Chrome unavailable: '+str(error).splitlines()[0])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close();cls.runtime.stop()

    def setUp(self):
        self.context = self.browser.new_context(viewport={'width':1200,'height':700})
        self.page = self.context.new_page()

    def tearDown(self): self.context.close()

    def metrics(self, case, audit):
        if os.environ.get('REDCN_CAPTURE_METRICS'):
            print('SECTION_METRIC '+json.dumps({'case':case, 'seconds':audit.get('seconds'),
                'reads':[row['reads'] for row in audit.get('sections',[])],
                'blocksVisited':[row['blocksVisited'] for row in audit.get('sections',[])]}),flush=True)

    def html(self, delayed=False):
        # Decoded inline image avoids network timing in this structural test.
        image='data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7'
        return '''<main><h1>Аренда 2-комнатной квартиры 54 м²</h1>
          <div data-name="PriceInfo">80 000 ₽ в месяц</div><div style="height:1100px"></div>
          <div data-name="OfferGallery"><img width="500" height="300" src="'''+image+'''" data-original="'''+IMAGE+'''" ></div>
          <div style="height:1300px"></div><div data-name="OfferDescription">'''+('' if delayed else TEXT)+'''</div>
          <div style="height:1300px"></div><div data-name="GeneralInfoSection">'''+('' if delayed else 'Мебель: шкаф, рабочий стол. Техника: духовой шкаф, пианино.')+'''</div>
          <div style="height:1200px"></div><div data-name="FeaturesItem">'''+('' if delayed else 'Холодильник и микроволновая печь.')+'''</div>
          <div style="height:1000px"></div><div data-name="Recommendations"><img data-original="https://images.cdn-cian.ru/unrelated.jpg"></div>
          </main><script>window.visits=[];const nativeScroll=Element.prototype.scrollIntoView;
          Element.prototype.scrollIntoView=function(...args){const id={OfferGallery:'gallery',OfferDescription:'description',GeneralInfoSection:'characteristics',FeaturesItem:'equipment'}[this.dataset.name];if(id&&!visits.includes(id))visits.push(id);return nativeScroll.apply(this,args);};
          let pending=new Set();addEventListener('scroll',()=>{
            for(const [name,id,text] of [['OfferGallery','gallery',''],['OfferDescription','description',`'''+TEXT+'''`],['GeneralInfoSection','characteristics','Мебель: шкаф, рабочий стол. Техника: духовой шкаф, пианино.'],['FeaturesItem','equipment','Холодильник и микроволновая печь.']]){
              const el=document.querySelector('[data-name="'+name+'"]'),box=el.getBoundingClientRect();
              if(box.top<innerHeight&&box.bottom>0){if(!visits.includes(id))visits.push(id);
                if(!el.textContent.trim()&&text&&!pending.has(name)){pending.add(name);setTimeout(()=>el.textContent=text,120);}}
            }
          });</script>'''

    def test_loaded_gallery_advances_to_description_and_characteristics_without_polling(self):
        self.page.set_content(self.html());self.page.wait_for_function('document.querySelector("img").complete')
        self.page.evaluate('document.querySelector("[data-name=OfferGallery]").style.height="2400px"')
        first=self.page.evaluate(browser_import.SNAPSHOT)
        result,audit=browser_import.capture_sections(self.page,first,time.monotonic()+10,lambda *_args,**_kwargs:None)
        self.assertTrue(audit['completed']);self.assertEqual(self.page.evaluate('window.visits'),['gallery','description','characteristics'])
        self.assertTrue(all(row['reads']==1 for row in audit['sections']))
        self.assertIn('пианино',result['text']);self.assertIn(IMAGE,result['images'])
        self.assertNotIn('https://images.cdn-cian.ru/unrelated.jpg',result['images'])
        self.assertEqual(self.page.evaluate('scrollY'),0)
        self.metrics('loaded-tall-gallery',audit)

    def test_delayed_description_and_furniture_load_after_their_direct_scroll(self):
        self.page.set_content(self.html(delayed=True));first=self.page.evaluate(browser_import.SNAPSHOT)
        result,audit=browser_import.capture_sections(self.page,first,time.monotonic()+10,lambda *_args,**_kwargs:None)
        self.assertTrue(audit['completed']);self.assertIn('пианино',result['text'])
        self.assertIn('микроволновая печь',result['text'])
        self.assertIn('equipment',self.page.evaluate('window.visits'))
        self.assertEqual(audit['sections'][2]['blocksVisited'],2)
        self.assertEqual([row['section'] for row in audit['sections']],['gallery','description','characteristics'])
        self.assertTrue(all(row['reads']>1 for row in audit['sections'][1:]))
        self.assertLess(audit['seconds'],2)
        self.metrics('delayed-description-and-equipment',audit)

    def test_background_tab_with_suspended_animation_frames_still_completes(self):
        self.page.set_content(self.html(delayed=True))
        self.page.evaluate('window.requestAnimationFrame=()=>0')
        first=self.page.evaluate(browser_import.SNAPSHOT)
        result,audit=browser_import.capture_sections(self.page,first,time.monotonic()+10,lambda *_args,**_kwargs:None)
        self.assertTrue(audit['completed']);self.assertIn('пианино',result['text'])
        self.assertLess(audit['seconds'],2.5)
        self.metrics('suspended-animation-frames',audit)

    def test_committed_listing_is_read_before_slow_deferred_script_finishes(self):
        import playwright.sync_api as api
        self.page.evaluate('document.body.remove()')
        empty=self.page.evaluate(browser_import.SNAPSHOT)
        self.assertEqual(empty['text'],'');self.assertEqual(empty['images'],[])
        release=threading.Event();requested=threading.Event();dom_ready=threading.Event()
        markup='<head><script defer src="/slow.js"></script></head>'+self.html()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*_args):pass
            def do_GET(self):
                if self.path=='/slow.js':
                    requested.set();release.wait(10);body=b'window.deferredFinished=true'
                    mime='application/javascript'
                else:body=markup.encode();mime='text/html; charset=utf-8'
                try:
                    self.send_response(200);self.send_header('Content-Type',mime)
                    self.send_header('Content-Length',str(len(body)));self.end_headers()
                    self.wfile.write(body);self.wfile.flush()
                except (BrokenPipeError,ConnectionResetError):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.daemon_threads=True
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        origin='http://127.0.0.1:'+str(server.server_port)
        self.context.on('page',lambda page:page.on('domcontentloaded',
            lambda:dom_ready.set() if page.url.startswith(origin) else None))
        playwright=SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=lambda *_args,**_kwargs:self.context))
        def parse(text,url):
            self.assertTrue(requested.is_set())
            self.assertFalse(dom_ready.is_set(),'Reading must not wait for DOMContentLoaded')
            self.assertFalse(release.is_set());self.assertIn('пианино',text)
            return {'images':[]}
        started=time.monotonic()
        try:
            with tempfile.TemporaryDirectory() as folder,patch.object(api,'sync_playwright') as mocked,patch.object(browser_import,'adapter_for',return_value=browser_import.adapter_for(URL)):
                mocked.return_value.__enter__.return_value=playwright
                result=browser_import.load_browser(origin+'/',Path(folder),parse,
                    lambda url,**_kwargs:url,timeout=10)
            self.assertTrue(result['capture']['completed']);self.assertEqual(result['capture']['strategy'],'sections')
            self.assertLess(time.monotonic()-started,3)
            self.metrics('committed-before-domcontentloaded',result['capture'])
        finally:
            release.set();server.shutdown();server.server_close();thread.join()


if __name__=='__main__': unittest.main()
