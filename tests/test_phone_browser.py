"""Optional real-browser acceptance for the paired iPhone-sized companion."""
import base64
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
import local_server
import phone_relay
import photo_store
import redcn


class PhoneBrowserAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise unittest.SkipTest('Optional Playwright/Chrome acceptance')
        cls.runtime=sync_playwright().start()
        try:cls.browser=cls.runtime.chromium.launch(channel='chrome',headless=True)
        except Exception as error:
            cls.runtime.stop();raise unittest.SkipTest(str(error).splitlines()[0])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close();cls.runtime.stop()

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.directory=Path(self.temp.name)
        self.controller=local_server.create_server(self.directory,0)
        self.phone=phone_relay.create_phone_server(self.directory,self.controller,0,'127.0.0.1',hosts={'127.0.0.1'})
        self.thread=threading.Thread(target=self.phone.serve_forever,daemon=True);self.thread.start()
        self.base='http://127.0.0.1:'+str(self.phone.server_port)
        self.context=self.browser.new_context(viewport={'width':393,'height':852},device_scale_factor=3,is_mobile=True,has_touch=True,user_agent='Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Version/17.0 Mobile/15E148 Safari/604.1')
        self.page=self.context.new_page();self.errors=[];self.page.on('pageerror',lambda error:self.errors.append(str(error)))
        from PIL import Image
        self.assets=[]
        for number,color in enumerate(['#d5bba4','#8baca9'],1):
            stream=io.BytesIO();Image.new('RGB',(640,420),color).save(stream,'PNG')
            url=f'https://images.cdn-cian.ru/images/phone-browser-{number}.jpg'
            self.assets.append(photo_store.attach_photo(self.directory,url,'data:image/png;base64,'+base64.b64encode(stream.getvalue()).decode()))
        self.result={'fields':{'price':'80000','area':'54','metro':'Юго-Западная','photos':[a['sourceUrl'] for a in self.assets]},'titles':['莫斯科温馨两居室'],'body':'两间独立房间，家具与家电按照片确认。','tags':'#莫斯科租房','mediaStatus':'ready'}
        self.card_id=local_server.save_result(self.directory,self.result)

    def tearDown(self):
        self.context.close();self.phone.shutdown();self.phone.server_close();self.thread.join();self.controller.server_close();self.temp.cleanup()
        self.assertEqual(self.errors,[])

    def connect(self):
        code,_=self.phone.pairing.issue();self.page.goto(self.base+'/phone/pair#'+code)
        self.page.locator('#phoneWorkspace').wait_for(state='visible')
        self.page.locator('#phoneCards .listing-card').first.wait_for()
        self.assertNotIn('#',self.page.url)

    def assert_mobile_fit(self):
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'),393)

    def test_cards_photos_live_update_downloads_and_install_visibility(self):
        self.connect();self.assert_mobile_fit()
        install=self.page.locator('#phoneInstallTop [data-pwa-install]');self.assertTrue(install.is_visible())
        install.click();dialog=self.page.locator('.pwa-install-dialog[open]');dialog.wait_for()
        self.assertIn('Safari',dialog.inner_text());dialog.get_by_role('button',name='Понятно').click()
        self.assertTrue(install.is_visible(),'Opening instructions is not installation')
        self.page.locator('#phoneCards .listing-card').first.click()
        self.page.wait_for_function("document.querySelector('#phonePhotos img')?.naturalWidth>0")
        self.assertIn('两间独立',self.page.locator('#phoneBody').inner_text());self.assert_mobile_fit()
        self.page.locator('#phonePhotoNext').click()
        self.page.wait_for_function("document.getElementById('phonePhotoPosition').textContent.startsWith('2 /')")
        self.result['body']='Mac 已更新正文，照片顺序保持不变。'
        local_server.save_result(self.directory,self.result,self.card_id)
        self.page.wait_for_function("document.getElementById('phoneBody').textContent.includes('Mac 已更新')",timeout=15000)
        self.assertTrue(self.page.locator('#phonePhotoPosition').inner_text().startswith('2 /'))
        self.page.wait_for_function("document.getElementById('phonePhotos').scrollLeft>=document.getElementById('phonePhotos').clientWidth*.8")
        asset=self.page.locator('#phoneDownloadPhoto').get_attribute('href')
        response=self.context.request.get(self.base+asset)
        self.assertEqual(response.status,200);self.assertIn('attachment',response.headers['content-disposition'])
        self.assertEqual(response.body(),photo_store.asset(self.directory,self.assets[1]['id'])[0])
        archive=self.context.request.get(self.base+self.page.locator('#phoneDownloadZip').get_attribute('href'))
        zipped=zipfile.ZipFile(io.BytesIO(archive.body()));self.assertEqual(zipped.testzip(),None)
        self.assertIn('Mac 已更新',zipped.read('post.txt').decode());self.assertEqual(len([n for n in zipped.namelist() if n.endswith('.png')]),2)
        self.page.evaluate("window.dispatchEvent(new Event('appinstalled'))")
        self.assertTrue(install.is_hidden())

    def test_standalone_hides_install_and_phone_link_uses_mac_import_queue(self):
        self.page.add_init_script("Object.defineProperty(navigator,'standalone',{value:true})")
        self.connect();self.assertTrue(self.page.locator('#phoneInstallTop [data-pwa-install]').is_hidden())
        self.page.locator('#phoneTabs [data-phone-view="setup"]').click()
        self.assertTrue(self.page.locator('#phoneInstall [data-pwa-install]').is_hidden());self.assert_mobile_fit()
        source='https://www.cian.ru/rent/flat/300691182/'
        prepared={**self.result,'fields':{**self.result['fields'],'photos':[self.assets[0]['sourceUrl']]},'titles':['通过 Mac 准备的新房源']}
        with patch.object(redcn,'load_listing',return_value={'url':source,'text':'Квартира 54 м². Аренда 80 000 руб.','images':[self.assets[0]['sourceUrl']]}),patch.object(redcn,'generate',return_value=prepared),patch.object(photo_store,'load_photo',return_value=self.assets[0]) as load:
            self.page.locator('#phoneTabs [data-phone-view="import"]').click();self.assert_mobile_fit()
            self.page.locator('#phoneImportUrl').fill(source);self.page.locator('#phoneImportSubmit').click()
            self.page.locator('#phoneJobOpen').wait_for(state='visible',timeout=15000)
            load.assert_called_once();self.page.locator('#phoneJobOpen').click()
            self.page.wait_for_function("document.getElementById('phoneDetailTitle').textContent.includes('通过 Mac')")
            self.assert_mobile_fit()

    def test_main_editor_navigation_and_inputs_fit_iphone_width(self):
        thread=threading.Thread(target=self.controller.serve_forever,daemon=True);thread.start()
        try:
            self.page.goto('http://127.0.0.1:'+str(self.controller.server_port))
            for tab in ['editor','rules','library','settings','history','analytics','knowledge','roadmap']:
                self.page.locator('aside nav [data-tab="'+tab+'"]').click()
                self.assert_mobile_fit()
            self.page.locator('aside nav [data-tab="editor"]').click()
            self.page.locator('#metro').fill('Юго-Западная')
            self.assertGreaterEqual(self.page.locator('#metro').evaluate('el=>parseFloat(getComputedStyle(el).fontSize)'),16)
            self.assertGreaterEqual(self.page.locator('#generate').bounding_box()['height'],44)
            self.assert_mobile_fit()
        finally:
            self.controller.shutdown();thread.join()


if __name__=='__main__':unittest.main()
