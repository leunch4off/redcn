"""Mobile import stages, honest estimates, and complete prepared photo sets."""
import io
import json
import unittest
from unittest.mock import patch
import zipfile

import test_phone_browser as acceptance
import photo_store


class PhoneJobBrowserAcceptance(unittest.TestCase):
    setUpClass=classmethod(acceptance.PhoneBrowserAcceptance.setUpClass.__func__)
    tearDownClass=classmethod(acceptance.PhoneBrowserAcceptance.tearDownClass.__func__)
    setUp=acceptance.PhoneBrowserAcceptance.setUp
    tearDown=acceptance.PhoneBrowserAcceptance.tearDown

    def running_progress(self):
        return {'state':'running','message':'Проверяем фото 2 из 5', 'progress':{
            'stage':'crop','label':'Автоматическое кадрирование','profile':'import:chrome',
            'elapsedSeconds':42,'stageElapsedSeconds':8,'estimateSource':'history',
            'estimateSeconds':90,'sampleCount':4,'historyRangeSeconds':[65,115],
            'completed':2,'total':5,'awaitingUser':False,'stages':[
                {'id':'browser','label':'Запуск Chrome','state':'done','elapsedSeconds':3},
                {'id':'crop','label':'Автоматическое кадрирование','state':'running',
                 'elapsedSeconds':8,'completed':2,'total':5}]}}

    def test_import_shows_live_stages_sample_average_manual_wait_and_completion(self):
        acceptance.PhoneBrowserAcceptance.connect(self)
        job_id='e'*32;phase=[0]
        running=self.running_progress()
        manual={'state':'running','message':'Пройдите проверку в Chrome на Mac', 'progress':{
            **running['progress'],'stage':'challenge','label':'Проверка в браузере',
            'elapsedSeconds':48,'stageElapsedSeconds':2,'completed':None,'total':None,
            'awaitingUser':True,'stages':[
                *[{**row,'state':'done'} for row in running['progress']['stages']],
                {'id':'challenge','label':'Проверка в браузере','state':'waitingManual',
                 'elapsedSeconds':2}]}}
        done={'state':'done','message':'Карточка и фотографии готовы','result_id':self.card_id,
            'progress':{**running['progress'],'stage':'done','label':'Готово','elapsedSeconds':67,
                'stageElapsedSeconds':0,'completed':5,'total':5,
                'stages':[{**row,'state':'done'} for row in running['progress']['stages']]}}
        responses=[running,manual,done]
        self.page.route('**/api/phone/import',lambda route:route.fulfill(
            status=202,content_type='application/json',body=json.dumps({'job_id':job_id})))
        self.page.route('**/api/phone/jobs/'+job_id,lambda route:route.fulfill(
            status=200,content_type='application/json',body=json.dumps(responses[phase[0]],ensure_ascii=False)))
        self.page.locator('#phoneTabs [data-phone-view="import"]').click()
        self.page.locator('#phoneImportUrl').fill('https://www.cian.ru/rent/flat/300691182/')
        self.page.locator('#phoneImportSubmit').click()
        self.page.wait_for_function("document.querySelector('#phoneJobCount').value===2")
        self.assertEqual(self.page.locator('#phoneJobSteps li').count(),2)
        self.assertEqual(self.page.locator('#phoneJobSteps [data-state="running"]').count(),1)
        self.assertIn('2 из 5',self.page.locator('#phoneJobSteps').inner_text())
        self.assertIn('4 завершённым операциям',self.page.locator('#phoneJobEstimate').inner_text())
        self.assertIn('1 мин 30 с',self.page.locator('#phoneJobEstimate').inner_text())
        self.assertEqual(self.page.locator('#phoneJobCount').get_attribute('max'),'5')
        elapsed=self.page.locator('#phoneJobElapsed').inner_text()
        self.page.wait_for_function("document.querySelector('#phoneJobElapsed').textContent!=="+json.dumps(elapsed),timeout=2500)
        self.assertTrue(self.page.locator('#phoneImportSubmit').is_disabled())
        acceptance.PhoneBrowserAcceptance.assert_mobile_fit(self)

        phase[0]=1
        self.page.wait_for_function("document.querySelector('#phoneJobTitle').textContent.includes('Нужна проверка')",timeout=6000)
        estimate=self.page.locator('#phoneJobEstimate').inner_text()
        self.assertIn('Время ручного подтверждения предсказать нельзя',estimate)
        self.assertNotIn('Ориентировочно осталось',estimate)
        self.assertTrue(self.page.locator('#phoneJobCount').is_hidden())
        self.assertEqual(self.page.locator('#phoneJobSteps [data-state="waitingManual"]').count(),1)

        phase[0]=2
        self.page.locator('#phoneJobOpen').wait_for(state='visible',timeout=6000)
        self.assertEqual(self.page.locator('#phoneJobTitle').inner_text(),'Готово на Mac')
        self.assertTrue(self.page.locator('#phoneImportSubmit').is_enabled())
        self.assertEqual(self.page.locator('#phoneJobSteps [data-state="running"]').count(),0)
        self.assertIn('Затрачено 1 мин 7 с',self.page.locator('#phoneJobElapsed').inner_text())
        self.page.locator('#phoneJobOpen').click()
        self.page.wait_for_function("document.querySelector('#phoneDetailTitle').textContent==='莫斯科温馨两居室'")
        acceptance.PhoneBrowserAcceptance.assert_mobile_fit(self)

    def test_prepared_gallery_and_zip_keep_untrimmed_photo_beside_safe_crop(self):
        # Explicit fixture crop is independent of Apple Vision and real OCR.
        with patch.object(photo_store,'recognize',side_effect=AssertionError('Manual crop must not run OCR')):
            cropped=photo_store.crop_photo(self.directory,self.assets[0]['id'],{'bottom':10})
        self.assertEqual(cropped['verification'],'manual')
        acceptance.PhoneBrowserAcceptance.connect(self)
        self.page.locator('#phoneCards .listing-card').first.click()
        self.page.wait_for_function("document.querySelector('#phonePhotos img')?.naturalWidth>0")
        expected_crop='/api/phone/media/'+cropped['id']
        expected_second='/api/phone/media/'+self.assets[1]['id']
        self.assertEqual(self.page.locator('#phoneCropVariant').get_attribute('aria-pressed'),'true')
        self.assertEqual(self.page.locator('#phonePhotos img').count(),2)
        self.assertEqual(self.page.locator('#phonePhotos img').nth(0).get_attribute('src'),expected_crop)
        self.assertEqual(self.page.locator('#phonePhotos img').nth(1).get_attribute('src'),expected_second)
        self.assertIn('1 обрезано',self.page.locator('#phoneMediaStatus').inner_text())
        prepared=self.page.locator('#phoneDownloadZip').get_attribute('href')
        self.assertTrue(prepared.endswith('?variant=prepared'))
        response=self.context.request.get(self.base+prepared)
        self.assertEqual(response.status,200)
        archive=zipfile.ZipFile(io.BytesIO(response.body()))
        self.assertEqual(archive.testzip(),None)
        self.assertEqual(archive.read('01.jpg'),photo_store.asset(self.directory,cropped['id'])[0])
        self.assertEqual(archive.read('02.png'),photo_store.asset(self.directory,self.assets[1]['id'])[0])
        manifest=json.loads(archive.read('manifest.json'))
        self.assertEqual([row['kind'] for row in manifest],['crop','original'])
        self.page.locator('#phoneOriginalVariant').click()
        self.assertEqual(self.page.locator('#phonePhotos img').nth(0).get_attribute('src'),'/api/phone/media/'+self.assets[0]['id'])
        self.assertEqual(self.page.locator('#phonePhotos img').count(),2)
        self.assertTrue(self.page.locator('#phoneDownloadZip').get_attribute('href').endswith('?variant=original'))
        acceptance.PhoneBrowserAcceptance.assert_mobile_fit(self)

    def test_reload_resumes_active_mac_job_without_sending_another_import(self):
        acceptance.PhoneBrowserAcceptance.connect(self)
        state=self.context.request.get(self.base+'/api/phone/state').json()
        job_id='d'*32;source='https://www.cian.ru/rent/flat/300691182/'
        state['activeJob']={'id':job_id,'kind':'import','state':'running','sourceUrl':source}
        self.page.route('**/api/phone/state',lambda route:route.fulfill(
            status=200,content_type='application/json',body=json.dumps(state)))
        self.page.route('**/api/phone/jobs/'+job_id,lambda route:route.fulfill(
            status=200,content_type='application/json',body=json.dumps(self.running_progress(),ensure_ascii=False)))
        imports=[]
        self.page.on('request',lambda request:imports.append(request.url) if request.url.endswith('/api/phone/import') else None)
        self.page.reload()
        self.page.wait_for_function("document.querySelector('#phoneJobCount').value===2")
        self.assertTrue(self.page.locator('#phoneImportView').is_visible())
        self.assertEqual(self.page.locator('#phoneImportUrl').input_value(),source)
        self.assertEqual(imports,[])
        self.assertIn('4 завершённым операциям',self.page.locator('#phoneJobEstimate').inner_text())
        acceptance.PhoneBrowserAcceptance.assert_mobile_fit(self)
        self.page.unroute('**/api/phone/jobs/'+job_id)
        self.page.route('**/api/phone/jobs/'+job_id,lambda route:route.fulfill(
            status=401,content_type='application/json',body=json.dumps({'paired':False})))
        self.page.locator('#phonePair').wait_for(state='visible',timeout=6000)
        self.assertTrue(self.page.locator('#phoneJob').is_hidden())
        self.assertEqual(self.page.locator('#phoneJobSteps').text_content(),'')
        self.assertEqual(self.page.locator('#phoneJobMessage').text_content(),'')
        self.assertEqual(self.page.locator('#phoneImportUrl').input_value(),'')

    def test_partial_photos_are_not_averaged_and_retry_prepares_existing_card(self):
        acceptance.PhoneBrowserAcceptance.connect(self)
        first='a'*32;second='b'*32;imports=[];prepares=[]
        partial={'state':'done','partial':True,'message':'Текст готов, фотографии подготовлены частично.',
            'result_id':self.card_id,'progress':{'stage':'done','elapsedSeconds':5,
                'stageElapsedSeconds':0,'historyEligible':False,'estimateSource':'none','sampleCount':0}}
        complete={'state':'done','partial':False,'message':'Фотографии готовы','result_id':self.card_id,
            'progress':{'stage':'done','elapsedSeconds':12,'stageElapsedSeconds':0,
                'estimateSource':'history','sampleCount':1,'estimateSeconds':12}}
        def start_import(route):
            imports.append(True);route.fulfill(status=202,content_type='application/json',body=json.dumps({'job_id':first}))
        def start_prepare(route):
            prepares.append(True);route.fulfill(status=202,content_type='application/json',body=json.dumps({'job_id':second}))
        self.page.route('**/api/phone/import',start_import)
        self.page.route('**/api/phone/cards/'+self.card_id+'/prepare',start_prepare)
        self.page.route('**/api/phone/jobs/'+first,lambda route:route.fulfill(status=200,content_type='application/json',body=json.dumps(partial,ensure_ascii=False)))
        self.page.route('**/api/phone/jobs/'+second,lambda route:route.fulfill(status=200,content_type='application/json',body=json.dumps(complete,ensure_ascii=False)))
        self.page.locator('#phoneTabs [data-phone-view="import"]').click()
        self.page.locator('#phoneImportUrl').fill('https://www.cian.ru/rent/flat/300691182/')
        self.page.locator('#phoneImportSubmit').click()
        self.page.locator('#phoneJobRestart').wait_for(state='visible')
        self.assertIn('частично',self.page.locator('#phoneJobTitle').inner_text())
        self.assertIn('не включается',self.page.locator('#phoneJobEstimate').inner_text())
        self.page.locator('#phoneJobRestart').click()
        self.page.wait_for_function("document.querySelector('#phoneJobTitle').textContent==='Готово на Mac'")
        self.assertEqual(len(imports),1);self.assertEqual(len(prepares),1)
        self.assertTrue(self.page.locator('#phoneJobRestart').is_hidden())


if __name__=='__main__':unittest.main()
