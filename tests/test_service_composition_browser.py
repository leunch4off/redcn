"""Authenticated server services survive actual phone copy and lazy designer.

Owned temporary cards and loopback servers only. No imports or model requests.
"""
from datetime import datetime,timezone,timedelta
import unittest

import test_phone_browser as fixture
import local_server
from service_claims import service_card_key


class ServiceCompositionBrowserAcceptance(unittest.TestCase):
    setUpClass=classmethod(fixture.PhoneBrowserAcceptance.setUpClass.__func__)
    tearDownClass=classmethod(fixture.PhoneBrowserAcceptance.tearDownClass.__func__)
    tearDown=fixture.PhoneBrowserAcceptance.tearDown

    def setUp(self):
        fixture.PhoneBrowserAcceptance.setUp(self)
        fields={**self.result['fields'],'editorIdentity':'phone-service-fixture','pickup':True}
        fields['serviceConfirmations']={'pickup':{
            'value':{'included':True,'luggage':False},'cardKey':service_card_key(fields),
            'actor':'PRIVATE_PHONE_OPERATOR','at':(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()}}
        self.result={**self.result,'fields':fields,'body':'已确认：免费接送到入住地点。'}
        local_server.save_result(self.directory,self.result,self.card_id)
        self.page.add_init_script("Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>{window.serviceCopied=text;}}})")

    def test_authenticated_copy_and_bundled_design_then_expiry_blocks_reuse(self):
        fixture.PhoneBrowserAcceptance.connect(self)
        self.page.locator('#phoneCards .listing-card').first.click()
        self.page.locator('#phoneDetail').wait_for(state='visible')
        response=self.context.request.get(self.base+'/api/phone/cards/'+self.card_id).json()
        self.assertNotIn('serviceConfirmations',response['fields'])
        self.assertNotIn('PRIVATE_PHONE_OPERATOR',str(response))
        expires=response['serviceAuthorization']['expiresAt']
        self.page.locator('#phoneCopyBody').click()
        self.page.wait_for_function("window.serviceCopied==='已确认：免费接送到入住地点。'")
        self.page.locator('#phoneDesignCard').click()
        self.page.locator('#designCanvasEditor').wait_for()
        self.page.wait_for_function("document.getElementById('presentationStatus').textContent.includes('2 фото')")
        self.page.locator('#presentationHtmlPreview > summary').click()
        self.page.wait_for_function("document.getElementById('presentationPreview').srcdoc.includes('免费接送到入住地点')")
        self.page.locator('#phoneTabs [data-phone-view="cards"]').click()
        self.page.locator('#phoneCards .listing-card').first.click()
        self.page.locator('#phoneDetail').wait_for(state='visible')
        self.page.evaluate("expires=>{window.serviceCopied='UNCHANGED';Date.now=()=>Date.parse(expires)+1;}",expires)
        self.page.locator('#phoneCopyBody').click()
        self.assertEqual(self.page.evaluate('window.serviceCopied'),'UNCHANGED')
        self.assertIn('подтверд',self.page.locator('#phoneToast').inner_text().lower())

    def test_legacy_unsupported_services_keep_card_photos_and_show_actionable_review(self):
        fields={**self.result['fields']};fields.pop('serviceConfirmations')
        legacy={**self.result,'fields':fields,'body':'租金80000卢布。\n免费接送到入住地点。'}
        local_server.save_result(self.directory,legacy,self.card_id)
        fixture.PhoneBrowserAcceptance.connect(self)
        self.page.locator('#phoneCards .listing-card').first.click()
        self.page.locator('#phoneDetail').wait_for(state='visible')
        self.assertEqual(self.page.locator('#phoneDetailStamp').get_attribute('data-publication-needs-review'),'true')
        self.assertIn('подтверд',self.page.locator('#phoneDetailStamp').inner_text().lower())
        self.assertIn('80000',self.page.locator('#phoneBody').inner_text())
        self.assertNotIn('免费',self.page.locator('#phoneBody').inner_text())
        self.page.wait_for_function("document.querySelector('#phonePhotos img')?.naturalWidth>0")


if __name__=='__main__':unittest.main()
