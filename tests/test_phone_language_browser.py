"""Phone writing controls: independent changes, draft review and stale responses."""
import json
import unittest

import test_phone_browser as acceptance
import local_server
import phone_relay


class PhoneLanguageBrowserAcceptance(unittest.TestCase):
    setUpClass=classmethod(acceptance.PhoneBrowserAcceptance.setUpClass.__func__)
    tearDownClass=classmethod(acceptance.PhoneBrowserAcceptance.tearDownClass.__func__)
    setUp=acceptance.PhoneBrowserAcceptance.setUp
    tearDown=acceptance.PhoneBrowserAcceptance.tearDown

    def open_card(self,card_id=None):
        self.page.locator('[data-card-id="'+(card_id or self.card_id)+'"]').click()
        self.page.locator('#phoneDetail').wait_for(state='visible')
        self.page.wait_for_function("document.querySelector('#phonePhotos img')?.naturalWidth>0")

    def card_payload(self):
        return phone_relay.public_card(self.directory,self.card_id)

    def test_independent_rewrites_preserve_other_text_gallery_and_scroll(self):
        self.result['body']='这段是手动修改后保留的描述。'
        local_server.save_result(self.directory,self.result,self.card_id)
        capabilities=[]
        self.page.on('request',lambda request:capabilities.append(request.url) if request.url.endswith('/api/phone/language') else None)
        acceptance.PhoneBrowserAcceptance.connect(self);self.open_card()
        self.page.locator('#phonePhotoNext').click()
        self.page.wait_for_function("document.querySelector('#phonePhotoPosition').textContent.startsWith('2 /')")
        photo=self.page.locator('#phoneDownloadPhoto').get_attribute('href')
        self.page.evaluate("window.savedGalleryImage=document.querySelector('#phonePhotos img')")
        requests=[]
        def rewrite(route):
            part=route.request.post_data_json['part'];requests.append(part)
            if part in ('title','all'):self.result['titles']=['莫斯科两居出租｜54㎡']
            if part in ('body','all'):self.result['body']='两间独立房间，房屋信息以已确认的条件为准。'
            if part in ('tags','all'):self.result['tags']='#莫斯科租房 #莫斯科房源'
            local_server.save_result(self.directory,self.result,self.card_id)
            route.fulfill(status=200,content_type='application/json',body=json.dumps(self.card_payload(),ensure_ascii=False))
        self.page.route('**/api/phone/cards/'+self.card_id+'/rewrite',rewrite)
        self.page.locator('#phoneRewriteTitle').scroll_into_view_if_needed();scroll=self.page.evaluate('window.scrollY')
        self.page.locator('#phoneRewriteTitle').click()
        self.page.wait_for_function("document.querySelector('#phoneTitleCopy').textContent==='莫斯科两居出租｜54㎡'")
        self.assertEqual(self.page.locator('#phoneBody').inner_text(),'这段是手动修改后保留的描述。')
        self.assertEqual(self.page.locator('#phoneTags').inner_text(),'#莫斯科租房')
        self.assertLess(abs(self.page.evaluate('window.scrollY')-scroll),5)
        self.assertTrue(self.page.evaluate("window.savedGalleryImage===document.querySelector('#phonePhotos img')"))
        self.assertTrue(self.page.locator('#phonePhotoPosition').inner_text().startswith('2 /'))
        self.assertEqual(self.page.locator('#phoneDownloadPhoto').get_attribute('href'),photo)
        self.page.locator('#phoneRewriteBody').click()
        self.page.wait_for_function("document.querySelector('#phoneBody').textContent.includes('已确认')")
        self.assertEqual(self.page.locator('#phoneTitleCopy').inner_text(),'莫斯科两居出租｜54㎡')
        self.assertEqual(self.page.locator('#phoneTags').inner_text(),'#莫斯科租房')
        self.page.locator('#phoneRewriteTags').click()
        self.page.wait_for_function("document.querySelector('#phoneTags').textContent.includes('莫斯科房源')")
        self.page.locator('#phoneRewriteAll').click()
        self.page.wait_for_function("document.querySelector('#phoneWritingStatus').textContent==='Новая подача сохранена в карточке.'")
        self.assertEqual(requests,['title','body','tags','all'])
        self.assertEqual(capabilities,[],'Language resources must stay lazy on the normal card path')
        acceptance.PhoneBrowserAcceptance.assert_mobile_fit(self)

    def test_russian_translation_is_reviewed_then_saved_to_selected_part(self):
        acceptance.PhoneBrowserAcceptance.connect(self);self.open_card()
        self.page.route('**/api/phone/language',lambda route:route.fulfill(status=200,content_type='application/json',body=json.dumps({
            'dictionary':{'entries':1200},'templates':{'title':{'count':200001},'body':{'count':200002},'tags':{'count':200003}}})))
        translations=[];edits=[]
        def translate(route):
            translations.append(route.request.post_data_json)
            route.fulfill(status=200,content_type='application/json',body=json.dumps({'text':'配有冰箱，неслово','unknown':['неслово'],'coverage':0.7,'warnings':['Уточните неизвестную фразу.']},ensure_ascii=False))
        def edit(route):
            payload=route.request.post_data_json;edits.append(payload)
            if payload['part']=='title':self.result['titles']=[payload['text']]
            elif payload['part']=='body':self.result['body']=payload['text']
            else:self.result['tags']=payload['text']
            local_server.save_result(self.directory,self.result,self.card_id)
            route.fulfill(status=200,content_type='application/json',body=json.dumps(self.card_payload(),ensure_ascii=False))
        self.page.route('**/api/phone/translate',translate)
        self.page.route('**/api/phone/cards/'+self.card_id+'/edit-text',edit)
        self.page.locator('#phoneLanguageStudio summary').click()
        self.page.wait_for_function("document.querySelector('#phoneLanguageStats').textContent.includes('200')")
        self.assertIn('считаются отдельно',self.page.locator('#phoneLanguageStats').inner_text())
        self.page.locator('#phoneRussianDraft').fill('Есть холодильник и неслово.')
        self.page.locator('#phoneTranslateDraft').click()
        self.page.wait_for_function("document.querySelector('#phoneChineseDraft').value.includes('冰箱')")
        self.assertIn('неслово',self.page.locator('#phoneTranslationReview').inner_text())
        self.assertEqual(edits,[],'Preview must not silently save the translation')
        self.page.locator('#phoneChineseDraft').fill('配有冰箱和书桌。')
        self.page.locator('#phoneApplyDraft').click()
        self.page.wait_for_function("document.querySelector('#phoneBody').textContent==='配有冰箱和书桌。'")
        self.assertEqual(edits[0]['part'],'body');self.assertEqual(edits[0]['sourceRu'],'Есть холодильник и неслово.')
        self.assertEqual(self.page.locator('#phoneTitleCopy').inner_text(),'莫斯科温馨两居室')
        self.assertEqual(self.page.locator('#phoneTags').inner_text(),'#莫斯科租房')
        self.page.locator('#phoneLanguagePart').select_option('tags')
        self.page.locator('#phoneChineseDraft').fill('#莫斯科租房 #房源信息')
        self.page.locator('#phoneApplyDraft').click()
        self.page.wait_for_function("document.querySelector('#phoneTags').textContent.includes('房源信息')")
        self.assertEqual(edits[1]['part'],'tags');self.assertEqual(len(translations),1)
        acceptance.PhoneBrowserAcceptance.assert_mobile_fit(self)

    def test_late_translation_cannot_fill_another_card_and_logout_clears_drafts(self):
        other=local_server.save_result(self.directory,{**self.result,'titles':['另一套莫斯科公寓']})
        acceptance.PhoneBrowserAcceptance.connect(self);self.open_card()
        self.page.route('**/api/phone/language',lambda route:route.fulfill(status=200,content_type='application/json',body='{}'))
        held=[]
        self.page.route('**/api/phone/translate',lambda route:held.append(route))
        self.page.locator('#phoneLanguageStudio summary').click()
        self.page.locator('#phoneRussianDraft').fill('Текст первой квартиры')
        self.page.locator('#phoneTranslateDraft').click()
        self.page.wait_for_function("document.querySelector('#phoneWritingStatus').textContent.includes('Переводим')")
        self.page.locator('#phoneBack').click();self.open_card(other)
        self.assertTrue(held)
        with self.page.expect_response(lambda response:response.url.endswith('/api/phone/translate')):
            held[0].fulfill(status=200,content_type='application/json',body=json.dumps({'text':'第一套房的内容','unknown':[]},ensure_ascii=False))
        self.page.wait_for_load_state('networkidle')
        self.assertEqual(self.page.locator('#phoneDetailTitle').inner_text(),'另一套莫斯科公寓')
        self.assertEqual(self.page.locator('#phoneChineseDraft').input_value(),'')
        self.assertEqual(self.page.locator('#phoneRussianDraft').input_value(),'')
        self.page.unroute('**/api/phone/translate')
        self.page.route('**/api/phone/translate',lambda route:route.fulfill(status=401,content_type='application/json',body='{"paired":false}'))
        self.page.locator('#phoneLanguageStudio summary').click()
        self.page.locator('#phoneRussianDraft').fill('Личный черновик второй квартиры')
        self.page.locator('#phoneChineseDraft').fill('第二套的草稿')
        self.page.locator('#phoneTranslateDraft').click()
        self.page.locator('#phonePair').wait_for(state='visible')
        self.assertEqual(self.page.locator('#phoneChineseDraft').input_value(),'')
        self.assertEqual(self.page.locator('#phoneRussianDraft').input_value(),'')
        self.assertTrue(self.page.locator('#phoneLanguageStudio').evaluate('node=>!node.open'))


if __name__=='__main__':unittest.main()
