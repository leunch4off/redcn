"""Desktop and phone AI panels with synthetic jobs; no listing or model calls."""
import json
import hashlib
import threading
import unittest
from unittest.mock import patch

import test_phone_browser as acceptance


class AiProgressBrowserTests(unittest.TestCase):
    setUpClass=classmethod(acceptance.PhoneBrowserAcceptance.setUpClass.__func__)
    tearDownClass=classmethod(acceptance.PhoneBrowserAcceptance.tearDownClass.__func__)
    setUp=acceptance.PhoneBrowserAcceptance.setUp
    tearDown=acceptance.PhoneBrowserAcceptance.tearDown

    def payload(self, ai, *, done=False):
        return {'state':'done' if done else 'running','message':'Синтетическая проверка ИИ',
            **({'result_id':self.card_id} if done else {}),
            'progress':{'stage':'done' if done else 'vision','label':'Локальный ИИ',
                'elapsedSeconds':12,'stageElapsedSeconds':4,'ai':ai,'stages':[]}}

    def test_phone_switches_from_tokens_to_detector_to_cache_without_fake_speed(self):
        acceptance.PhoneBrowserAcceptance.connect(self)
        job_id='b'*32
        rows=[self.payload({'backend':'mlx','model':'fixture-text','operation':'text',
            'phase':'generating','active':True,'elapsedSeconds':8,'generatedTokens':40,
            'tokensPerSecond':10,'generationSeconds':4}),
            self.payload({'backend':'yolox-trained','model':'redcn-furniture-v1','operation':'furniture',
                'phase':'analyzing','active':True,'elapsedSeconds':2,'photoIndex':2,'photoTotal':5,
                'inferenceSeconds':1,'imagesPerSecond':1,'generatedTokens':777,'tokensPerSecond':777}),
            self.payload({'backend':'ollama','model':'fixture-vlm','operation':'furniture',
                'phase':'cached','active':False,'cached':True,'elapsedSeconds':.01,
                'tokensPerSecond':888,'generatedTokens':888},done=True)]
        phase=[0]
        self.page.route('**/api/phone/import',lambda route:route.fulfill(status=202,
            content_type='application/json',body=json.dumps({'job_id':job_id})))
        self.page.route('**/api/phone/jobs/'+job_id,lambda route:route.fulfill(status=200,
            content_type='application/json',body=json.dumps(rows[phase[0]],ensure_ascii=False)))
        self.page.locator('#phoneTabs [data-phone-view="import"]').click()
        self.page.locator('#phoneImportUrl').fill('https://www.cian.ru/rent/flat/300691182/')
        self.page.locator('#phoneImportSubmit').click()
        panel=self.page.locator('#phoneJobAiProgress');panel.wait_for(state='visible')
        self.assertIn('10 токенов/с',panel.inner_text())
        self.assertIn('40 токенов',panel.inner_text())
        acceptance.PhoneBrowserAcceptance.assert_mobile_fit(self)
        phase[0]=1
        self.page.wait_for_function("document.querySelector('#phoneJobAiProgress').textContent.includes('Фото 2 из 5')",timeout=6000)
        self.assertIn('1 фото/с',panel.inner_text());self.assertNotIn('777',panel.inner_text())
        self.assertIn('не генерирует токены',panel.inner_text())
        phase[0]=2
        self.page.locator('#phoneJobOpen').wait_for(state='visible',timeout=6000)
        self.assertIn('модель повторно не запускалась',panel.inner_text())
        self.assertNotIn('888',panel.inner_text())
        acceptance.PhoneBrowserAcceptance.assert_mobile_fit(self)

    def test_desktop_import_displays_live_model_counters_and_stops_on_completion(self):
        job_id='c'*32;done=[False]
        ai={'backend':'mlx','model':'fixture-text','operation':'text','phase':'generating',
            'active':True,'elapsedSeconds':6,'generatedTokens':24,'tokensPerSecond':12,'generationSeconds':2}
        self.page.route('**/api/local/import',lambda route:route.fulfill(status=202,
            content_type='application/json',body=json.dumps({'job_id':job_id})))
        self.page.route('**/api/local/jobs/'+job_id,lambda route:route.fulfill(status=200,
            content_type='application/json',body=json.dumps(self.payload(
                {**ai,**({'phase':'complete','active':False} if done[0] else {})},done=done[0]),ensure_ascii=False)))
        self.page.route('https://images.cdn-cian.ru/**',lambda route:route.fulfill(status=200,
            content_type='image/png',body=acceptance.photo_store.asset(self.directory,self.assets[0]['id'])[0]))
        self.page.add_init_script("localStorage.setItem('most-photo-analyze','false');localStorage.setItem('most-photo-autoload','false')")
        self.page.set_viewport_size({'width':1440,'height':1000})
        thread=threading.Thread(target=self.controller.serve_forever,daemon=True);thread.start()
        try:
            self.page.goto(self.controller.desktop_access.launch_url(self.controller.server_port),wait_until='domcontentloaded')
            self.page.wait_for_function("document.documentElement.dataset.editorReady==='true'")
            self.page.locator('#url').fill('https://www.cian.ru/rent/flat/300691182/')
            self.page.locator('#import').click()
            panel=self.page.locator('#importAiProgress');panel.wait_for(state='visible')
            self.assertIn('12 токенов/с',panel.inner_text());self.assertIn('24 токенов',panel.inner_text())
            done[0]=True
            self.page.wait_for_function("!document.querySelector('#import').disabled",timeout=6000)
            self.assertIn('Обработка завершена',panel.inner_text())
            self.assertIn('Затрачено 6 с',panel.inner_text())
        finally:
            self.controller.shutdown();thread.join()

    def test_direct_photo_analysis_saves_real_review_queue_without_confirming_facts_or_html(self):
        self.result['fields']['features']=[]
        acceptance.local_server.save_result(self.directory,self.result,self.card_id)
        self.page.add_init_script("localStorage.setItem('most-photo-analyze','false');localStorage.setItem('most-photo-autoload','false')")
        self.page.route('https://images.cdn-cian.ru/**',lambda route:route.fulfill(status=200,
            content_type='image/png',body=acceptance.photo_store.asset(self.directory,self.assets[0]['id'])[0]))
        self.page.set_viewport_size({'width':1440,'height':1000})
        thread=threading.Thread(target=self.controller.serve_forever,daemon=True);thread.start()
        def analyze(directory,digest,**options):
            score=.95
            return {'version':2,'status':'review','provider':'yolox-trained','model':'redcn-furniture-v1',
                'engine':'Synthetic detector','originalId':digest,'autoApply':True,'autoApplyMode':'suggestions-only',
                'unknownIds':[],'candidates':[{'id':'fridge',
                    'label':'Холодильник <img src=x onerror="globalThis.__photoAiXss=true">',
                    'confidence':score,'autoEligible':False,'automaticSuggestion':True,'requiresConfirmation':True,
                    'reviewId':hashlib.sha256((digest+'|fridge').encode()).hexdigest(),
                    'evidence':[{'kind':'object-detection','provider':'yolox-trained','model':'redcn-furniture-v1',
                        'source':'local-photo','originalId':digest,'photoId':digest,'confidence':score,'region':[.1,.1,.3,.4]}]}],
                'ai':{'backend':'yolox-trained','model':'redcn-furniture-v1','operation':'furniture',
                    'phase':'complete','tokenBased':False,'elapsedSeconds':1,'inferenceSeconds':.5,'imagesPerSecond':2}}
        try:
            self.page.goto(self.controller.desktop_access.launch_url(self.controller.server_port,self.card_id),wait_until='domcontentloaded')
            self.page.wait_for_function("document.documentElement.dataset.editorReady==='true'&&document.querySelector('#importStatus').textContent.includes('Сохранённая карточка загружена')")
            with patch.object(acceptance.photo_store,'analyze_photo',side_effect=analyze) as model, \
                    patch.object(acceptance.redcn,'fetch_bytes',side_effect=AssertionError('Only cached synthetic originals')):
                self.page.locator('#photoAnalyze').click()
                self.page.wait_for_function("!document.querySelector('#photoAnalyze').disabled&&document.querySelectorAll('#photoObservations input[type=checkbox]').length===2")
                self.assertEqual(model.call_count,2)
            self.assertIn('автодобавлено в журнал',self.page.locator('#photoObservations').inner_text())
            self.assertEqual(self.page.locator('#photoObservations img').count(),0)
            self.assertIsNone(self.page.evaluate('globalThis.__photoAiXss'))
            self.assertFalse(self.page.locator('#feature-fridge').is_checked())
            self.assertEqual(self.page.locator('#output').input_value(),self.result['body'])
            with self.page.expect_response(lambda response:response.url.endswith('/api/local/save') and response.request.method=='POST'):
                self.page.locator('#saveLocal').click()
            saved=acceptance.local_server.read_result(self.directory,self.card_id)
            self.assertEqual(saved['fields']['photoEdits']['reviewJournal']['pending'],2)
            self.assertEqual(saved['fields']['photoEdits']['reviewJournal']['confirmed'],0)
            self.assertNotIn('fridge',saved['fields']['features'])
            self.page.locator('#photoObservations input[type=checkbox]').first.check()
            self.page.wait_for_function("document.querySelector('#feature-fridge').checked")
            with self.page.expect_response(lambda response:response.url.endswith('/api/local/save') and response.request.method=='POST'):
                self.page.locator('#saveLocal').click()
            saved=acceptance.local_server.read_result(self.directory,self.card_id)
            self.assertEqual(saved['fields']['photoEdits']['reviewJournal']['confirmed'],1)
            self.assertEqual(saved['fields']['photoEdits']['reviewJournal']['pending'],1)
        finally:
            self.controller.shutdown();thread.join()


if __name__=='__main__':unittest.main()
