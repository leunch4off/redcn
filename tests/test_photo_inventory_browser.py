"""Optional Chrome acceptance for positive photo facts and the phone inventory.

Photos, cards and servers are isolated temporary fixtures. The local model and
phone preparation job are mocked; these tests never import a listing or run AI.
"""
import copy
import re
import threading
import unittest
from unittest.mock import patch

import test_phone_browser as phone_fixture


MODEL='qwen3-vl:2b-instruct-q4_K_M'
OBJECTS={
    'fridge':'Холодильник с отдельными дверцами и ручками у кухонной стены',
    'sofa':'Диван с подлокотниками и мягкими подушками у стены',
    'bedAny':'Кровать с мягким изголовьем и матрасом у стены',
}


def observation(digest,features):
    from local_photo_ai import normalize_inventory
    normalized=normalize_inventory({'objects':[{'id':feature,'visible':True,
        'confidence':.98,'count':None,'countExact':False,'evidence':OBJECTS[feature]}
        for feature in features],'unknownIds':[]},digest,MODEL)
    return {'status':'review','provider':'ollama','model':MODEL,
        'engine':'Ollama · fixture · локально','source':'local-photo',
        'originalId':digest,'autoApply':True,**normalized}


class PhotoInventoryBrowserAcceptance(unittest.TestCase):
    # Module references avoid discovering the shared TestCase a second time.
    setUpClass=classmethod(phone_fixture.PhoneBrowserAcceptance.setUpClass.__func__)
    tearDownClass=classmethod(phone_fixture.PhoneBrowserAcceptance.tearDownClass.__func__)

    def setUp(self):
        phone_fixture.PhoneBrowserAcceptance.setUp(self)
        self.desktop_thread=threading.Thread(target=self.controller.serve_forever,daemon=True)
        self.desktop_thread.start();self.desktop='http://127.0.0.1:'+str(self.controller.server_port)
        self.page.add_init_script("if(window===window.top){localStorage.setItem('most-photo-analyze','false');localStorage.setItem('most-photo-autoload','false')}")
        def cached_image(route):
            asset=next(item for item in self.assets if item['sourceUrl']==route.request.url)
            content,mime,_=phone_fixture.photo_store.asset(self.directory,asset['id'])
            route.fulfill(status=200,content_type=mime,body=content)
        self.page.route('https://images.cdn-cian.ru/**',cached_image)

    def tearDown(self):
        self.controller.shutdown();self.desktop_thread.join()
        phone_fixture.PhoneBrowserAcceptance.tearDown(self)

    def read_card(self):return phone_fixture.local_server.read_result(self.directory,self.card_id)

    def save_fixture(self):phone_fixture.local_server.save_result(self.directory,self.result,self.card_id)

    def open_desktop(self):
        self.page.set_viewport_size({'width':1440,'height':1100})
        self.page.goto(self.desktop+'/listing/'+self.card_id,wait_until='domcontentloaded')
        self.page.wait_for_function("document.documentElement.dataset.editorReady==='true'&&document.getElementById('importStatus').textContent.includes('Сохранённая карточка загружена')")

    def fridge_observation(self):
        return self.page.locator('#photoObservations label').filter(has_text='Холодильник').locator('input[type="checkbox"]')

    def assert_positive_body(self,fridge=True):
        body=self.page.locator('#output').input_value()
        self.assertIn('沙发',body);self.assertIn('床',body)
        self.assertEqual('冰箱' in body,fridge)
        self.assertNotRegex(body,re.compile(r'未配备|没有冰箱|无冰箱|нет холодильника|без холодильника',re.I))
        self.assertNotIn('双人床',body,'A generic visible bed does not establish its width')

    def test_desktop_local_observations_override_source_flag_and_rejection_survives_save(self):
        self.result['fields'].update({'features':[],'style':'compact','source':'Квартира 54 м². Аренда 80 000 руб.',
            'sourceAudit':{'fields':[],'warnings':[],'stats':{},'furniture':[
                {'id':'fridge','label':'Холодильник','status':'absent',
                    'matches':[{'source':'JSON · hasFridge','text':'hasFridge=false'}]}]}})
        self.result['listing']={'offer':{'hasFridge':False},'images':self.result['fields']['photos']}
        self.save_fixture();self.open_desktop()
        self.assertFalse(self.page.locator('#feature-fridge').is_checked())
        first_digest=self.assets[0]['id']
        def analyze(_directory,digest,**_options):
            self.assertIn(digest,[asset['id'] for asset in self.assets])
            return observation(digest,['fridge','sofa'] if digest==first_digest else ['bedAny'])
        # A failed cache lookup would fail the test instead of fetching externally.
        with patch.object(phone_fixture.redcn,'fetch_bytes',side_effect=AssertionError('Only cached originals are permitted')), \
                patch.object(phone_fixture.photo_store,'analyze_photo',side_effect=analyze) as model:
            self.page.locator('#photoAnalyze').click()
            self.page.wait_for_function("!document.getElementById('photoAnalyze').disabled&&document.getElementById('feature-fridge').checked&&document.getElementById('feature-sofa').checked&&document.getElementById('feature-bedAny').checked")
            self.assertEqual(model.call_count,2)
        self.assertTrue(self.fridge_observation().is_checked());self.assertFalse(self.page.locator('#feature-bed').is_checked())
        self.assert_positive_body()
        # A human can correct, restore and reject the same observation even
        # while the source's unchecked Cian flag remains in the audit.
        self.fridge_observation().uncheck()
        self.page.wait_for_function("!document.getElementById('feature-fridge').checked&&!document.getElementById('output').value.includes('冰箱')")
        self.fridge_observation().check()
        self.page.wait_for_function("document.getElementById('feature-fridge').checked&&document.getElementById('output').value.includes('冰箱')")
        self.fridge_observation().uncheck()
        self.page.wait_for_function("!document.getElementById('feature-fridge').checked&&!document.getElementById('output').value.includes('冰箱')")
        self.page.locator('#generate').click();self.assert_positive_body(fridge=False)
        with self.page.expect_response(lambda response:response.url.endswith('/api/local/save') and response.request.method=='POST'):
            self.page.locator('#saveLocal').click()
        self.page.wait_for_function("document.getElementById('localSaveStatus').textContent.includes('Сохранено')")
        saved=copy.deepcopy(self.read_card());url=self.assets[0]['sourceUrl']
        self.assertTrue(saved['fields']['photoEdits']['observations'][url]['rejected']['fridge'])
        self.assertNotIn('fridge',saved['fields']['features']);self.assertIn('sofa',saved['fields']['features'])
        self.assertEqual(saved['fields']['sourceAudit']['furniture'][0]['status'],'absent')
        self.page.reload();self.page.wait_for_function("document.documentElement.dataset.editorReady==='true'&&document.querySelectorAll('#photoObservations input[type=checkbox]').length===3")
        self.assertFalse(self.fridge_observation().is_checked());self.assertFalse(self.page.locator('#feature-fridge').is_checked())
        self.page.locator('#generate').click();self.assert_positive_body(fridge=False)

    def test_phone_inventory_is_readonly_fits_and_analyzes_only_on_explicit_request(self):
        first=observation(self.assets[0]['id'],['fridge'])
        self.result['photoInventory']={'version':1,'state':'complete','checked':2,'total':2,
            'totalAvailable':2,'coverageLimit':30,'failed':0,'skipped':0,'truncated':False,
            'photos':[{'sourceUrl':self.assets[0]['sourceUrl'],'originalId':self.assets[0]['id'],
                'status':'review','candidates':first['candidates'],'unknownIds':['washer']},
                {'sourceUrl':self.assets[1]['sourceUrl'],'originalId':self.assets[1]['id'],
                'status':'review','candidates':[],'unknownIds':['sofa','washer']}],
            'features':['fridge'],'photoEvidence':[]}
        self.save_fixture();job_id='a'*32
        self.page.route('**/api/phone/jobs/'+job_id,lambda route:route.fulfill(status=200,
            json={'state':'running','kind':'prepare','cardId':self.card_id,'message':'Проверяем мебель и технику на Mac…'}))
        with patch.object(self.controller,'prepare_card',return_value=job_id) as prepare:
            phone_fixture.PhoneBrowserAcceptance.connect(self)
            self.page.locator('#phoneCards .listing-card').first.click()
            inventory=self.page.locator('#phonePhotoInventory');inventory.locator('summary').click()
            self.assertIn('проверено 2 / 2 фото',inventory.inner_text())
            self.assertIn('Холодильник · видно на фото',inventory.inner_text())
            self.assertIn('это не означает отсутствие мебели',inventory.inner_text())
            self.assertEqual(inventory.locator('input, textarea, select').count(),0)
            self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'),393)
            inventory.get_by_role('button',name='Фото 2',exact=True).click()
            self.page.wait_for_function("document.getElementById('phonePhotoPosition').textContent.startsWith('2 /')")
            prepare.assert_not_called()
            with self.page.expect_response(lambda response:response.url.endswith('/cards/'+self.card_id+'/analyze') and response.request.method=='POST') as response:
                self.page.locator('#phoneAnalyzeFurniture').click()
            self.assertEqual(response.value.status,202);prepare.assert_called_once_with(self.card_id,analyze=True)
            self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'),393)
            self.assertEqual(self.read_card()['body'],self.result['body'])


if __name__=='__main__':unittest.main()
