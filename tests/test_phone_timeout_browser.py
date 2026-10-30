"""Phone API/body regression when older Safari lacks AbortSignal static helpers.

All cards, imports and diagnostic attempts are isolated fixtures, never the live
owner's cache or the campaign armed for their next ten manual imports.
"""
import base64
import io
from pathlib import Path
import unittest
from unittest.mock import patch
import zipfile

import test_phone_browser as phone_fixture


class PhoneTimeoutCompatibilityAcceptance(unittest.TestCase):
    setUpClass=classmethod(phone_fixture.PhoneBrowserAcceptance.setUpClass.__func__)
    tearDownClass=classmethod(phone_fixture.PhoneBrowserAcceptance.tearDownClass.__func__)
    tearDown=phone_fixture.PhoneBrowserAcceptance.tearDown

    def setUp(self):
        phone_fixture.PhoneBrowserAcceptance.setUp(self)
        self.page.add_init_script("""for(const name of ['timeout','any'])Object.defineProperty(AbortSignal,name,{value:undefined,configurable:true});Object.defineProperty(navigator,'share',{value:async()=>{},configurable:true});Object.defineProperty(navigator,'canShare',{value:()=>true,configurable:true});""")

    def connect(self):phone_fixture.PhoneBrowserAcceptance.connect(self)

    def assert_no_static_helpers(self):
        self.assertEqual(self.page.evaluate("typeof AbortSignal.timeout+' / '+typeof AbortSignal.any"),'undefined / undefined')

    def test_pair_feed_photo_share_preparation_and_export_download_without_static_timeout(self):
        import card_assets
        content=phone_fixture.photo_store.asset(self.directory,self.assets[0]['id'])[0]
        card_assets.create_image_export(self.directory,self.card_id,base64.b64encode(content).decode(),'intro')
        self.connect();self.assert_no_static_helpers()
        self.page.locator('#phoneCards .listing-card').first.click()
        self.page.wait_for_function("document.getElementById('phoneSharePhoto').textContent==='Поделиться фото'&&!document.getElementById('phoneSharePhoto').disabled")
        self.assertIn('两间独立',self.page.locator('#phoneBody').inner_text())
        button=self.page.locator('#phoneFilesMount').get_by_role('button',name='Intro · PNG',exact=True);button.wait_for()
        with self.page.expect_download(timeout=15000) as download:button.click()
        self.assertEqual(Path(download.value.path()).read_bytes(),content)
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'),393)

    def test_manual_import_and_automatic_tg_zip_download_without_static_timeout(self):
        audit=self.controller.import_diagnostics;audit.arm()
        for index in range(9):
            token=audit.begin(format(index+1,'032x'),'https://www.cian.ru/rent/flat/'+str(80000000+index)+'/')
            audit.record(token,'fixture',stage='scroll',section='photos',completed=1,total=1)
            audit.complete(token,'success')
        self.connect();source='https://www.cian.ru/rent/flat/300691182/'
        prepared={**self.result,'fields':{**self.result['fields'],'photos':[self.assets[0]['sourceUrl']]},'titles':['兼容旧版Safari的房源']}
        with patch.object(phone_fixture.redcn,'load_listing',return_value={'url':source,'text':'Квартира 54 м². Аренда 80 000 руб.','images':[self.assets[0]['sourceUrl']]}),patch.object(phone_fixture.redcn,'generate',return_value=prepared),patch.object(phone_fixture.photo_store,'load_photo',return_value=self.assets[0]),patch.object(phone_fixture.photo_store,'auto_crop_photo',return_value={'autoStatus':'unchanged'}):
            self.page.locator('#phoneTabs [data-phone-view="import"]').click()
            self.page.locator('#phoneImportUrl').fill(source);self.page.locator('#phoneImportSubmit').click()
            self.page.locator('#phoneJobOpen').wait_for(state='visible',timeout=20000)
        status=audit.status();self.assertEqual(status['completed'],10);self.assertEqual(status['state'],'complete');self.assertTrue(status['bundleReady'])
        panel=self.page.locator('#phoneImportDiagnostics');panel.locator('.import-audit > summary').click()
        button=panel.get_by_role('button',name='Скачать tg.zip',exact=True);button.wait_for(state='visible',timeout=15000)
        with self.page.expect_download(timeout=15000) as download:button.click()
        content=Path(download.value.path()).read_bytes();bundle=zipfile.ZipFile(io.BytesIO(content))
        self.assertIsNone(bundle.testzip());self.assertIn('manifest.json',bundle.namelist());self.assert_no_static_helpers()


if __name__=='__main__':unittest.main()
