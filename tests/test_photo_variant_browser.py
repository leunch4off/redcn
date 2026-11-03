"""Actual Chrome raster/publication acceptance for one observed source choice."""
import base64
import io
import json
import os
from pathlib import Path
import re
import threading
import unittest
from unittest.mock import patch
import zlib

import test_phone_browser as fixture
from test_photo_variant_pipeline import PRIMARY,LARGE,TINY,raster
from test_publication_backend import design_fixture
import card_assets
import local_server
import phone_relay
import photo_store
import redcn
from PIL import Image


class PhotoVariantBrowserAcceptance(unittest.TestCase):
    setUpClass=classmethod(fixture.PhoneBrowserAcceptance.setUpClass.__func__)
    tearDownClass=classmethod(fixture.PhoneBrowserAcceptance.tearDownClass.__func__)

    def setUp(self):
        fixture.PhoneBrowserAcceptance.setUp(self)
        self.desktop_thread=threading.Thread(target=self.controller.serve_forever,daemon=True);self.desktop_thread.start()
        self.desktop='http://127.0.0.1:'+str(self.controller.server_port)
        images={PRIMARY:raster((600,600),'#ff0000'),LARGE:raster((800,800),'#0000ff'),TINY:raster((120,120),'#00ff00')}
        source={'url':PRIMARY,'photoId':'native-variant-fixture','variants':{'fullUrl':PRIMARY,'thumbnailUrl':TINY},'observedVariants':[{'url':LARGE,'source':'srcset'}]}
        self.source=source
        self.result={'fields':{'price':'80000','area':'54','photos':[PRIMARY],'photoItems':[source]},'listing':{'images':[PRIMARY],'photoItems':[source]},'titles':['实拍房源'],'body':'已确认的房源照片资料。','tags':'#莫斯科租房'}
        with patch.object(redcn,'fetch_bytes',side_effect=lambda url,**kw:(images[url],'image/jpeg')),patch.object(photo_store,'auto_crop_photo',return_value={'autoStatus':'unchanged'}):phone_relay.cache_photos(self.directory,self.result)
        local_server.save_result(self.directory,self.result,self.card_id)
        self.choice=photo_store.accepted_photo_for_url(self.directory,PRIMARY,source=source)
        design=design_fixture();photo=next(node for node in design['surfaces']['intro']['nodes'] if node['type']=='image')
        hero=json.loads(json.dumps(photo));hero.update(id='native-accepted-photo-outro',x=0,y=0,w=1080,h=900,z=100,bind='photo1');design['surfaces']['outro']['nodes'].append(hero)
        card_assets.save_design(self.directory,design)
        self.page.route('https://images.cdn-cian.ru/**',lambda route:route.abort())
        self.page.add_init_script("if(window.top===window){localStorage.setItem('most-photo-analyze','false');localStorage.setItem('most-photo-autoload','true')}")

    def tearDown(self):
        self.controller.shutdown();self.desktop_thread.join();fixture.PhoneBrowserAcceptance.tearDown(self)

    def assert_blue(self,pixel):
        self.assertLess(pixel[0],35);self.assertLess(pixel[1],35);self.assertGreater(pixel[2],220)

    def browser_pixel(self,selector,x=.5,y=.15):
        return self.page.locator(selector).evaluate("(image,point)=>{const c=document.createElement('canvas');c.width=image.naturalWidth;c.height=image.naturalHeight;c.getContext('2d').drawImage(image,0,0);return Array.from(c.getContext('2d').getImageData(Math.floor(c.width*point.x),Math.floor(c.height*point.y),1,1).data)}",{'x':x,'y':y})

    def test_gallery_designer_intro_outro_html_and_native_pdf_share_selected_raster(self):
        self.page.set_viewport_size({'width':1440,'height':1100})
        self.page.goto(self.controller.desktop_access.launch_url(self.controller.server_port,self.card_id),wait_until='domcontentloaded')
        self.page.wait_for_function("document.getElementById('output').value.includes('已确认的房源照片资料')")
        self.page.locator('#photoCard').wait_for(state='visible')
        self.page.locator('#photoCard').scroll_into_view_if_needed()
        self.page.wait_for_function("document.querySelector('#photoGallery img')?.naturalWidth===800",timeout=20000)
        self.assert_blue(self.browser_pixel('#photoGallery img',.5,.5))
        self.page.locator('#photoGallery .photo-open').click()
        self.page.wait_for_function("document.getElementById('photoOriginalView').naturalWidth===600&&document.getElementById('photoCropView').naturalWidth===800")
        self.assert_blue(self.browser_pixel('#photoCropView',.5,.5))
        self.page.locator('#photoClose').click();self.page.locator('#openPresentation').click()
        self.page.wait_for_function("document.getElementById('presentationStatus').textContent.includes('1 фото')",timeout=30000)
        for tab in ('intro','outro'):
            self.page.locator('#presentationTab'+tab).click()
            self.page.wait_for_function("document.getElementById('presentationImagePreview').naturalWidth===1080",timeout=20000)
            self.assert_blue(self.browser_pixel('#presentationImagePreview'))
            with self.page.expect_download(timeout=45000) as download:self.page.locator('#presentationImageDownload').click()
            with Image.open(download.value.path()) as image:self.assert_blue(image.convert('RGB').getpixel((image.width//2,int(image.height*.15))))
        self.page.locator('#presentationTabpdf').click()
        with self.page.expect_download(timeout=45000) as download:self.page.locator('#presentationHtml').click()
        html=Path(download.value.path()).read_text()
        pictures=re.findall(r'<img[^>]+src="(data:image/(?:png|jpeg|webp);base64,[^\"]+)"',html)
        self.assertTrue(pictures)
        for data in pictures:
            with Image.open(io.BytesIO(base64.b64decode(data.split(',',1)[1]))) as image:self.assert_blue(image.convert('RGB').getpixel((image.width//2,image.height//2)))
        with self.page.expect_download(timeout=90000) as download:self.page.locator('#presentationPdf').click()
        pdf=Path(download.value.path()).read_bytes();self.assertTrue(pdf.startswith(b'%PDF-'));self.assertIn(b'/Subtype /Image',pdf)
        if os.environ.get('REDCN_PHOTO_VARIANT_QA_ARTIFACT'):
            artifact=Path(os.environ['REDCN_PHOTO_VARIANT_QA_ARTIFACT']);artifact.parent.mkdir(parents=True,exist_ok=True);artifact.write_bytes(pdf)
        # Inspect the actual immutable raster embedded by native Chrome. This
        # bounded fixture reader deliberately handles only its owned RGB image,
        # rather than pretending a PDF signature proves the selected content.
        selected_images=[];image_headers=[]
        for value in re.findall(rb'\d+ 0 obj\b(.*?)\nendobj',pdf,re.S):
            if b'/Subtype /Image' not in value:continue
            start=re.search(rb'\bstream\r?\n',value);length=re.search(rb'/Length\s+(\d+)\b',value)
            image_headers.append(value[:start.start() if start else 400].decode('latin1'))
            width=re.search(rb'/Width\s+(\d+)\b',value);height=re.search(rb'/Height\s+(\d+)\b',value)
            if not start or not length or not width or not height:continue
            width,height=int(width[1]),int(height[1])
            # Native print can store only the clipped visible region (this
            # fixture's800px source becomes656×800). Pixel identity, rather
            # than an unchanged square shape, proves which source was printed.
            if max(width,height)!=800 or min(width,height)<320:continue
            # Read the declared byte length instead of depending on whitespace
            # around the endstream delimiter.
            stream=value[start.end():start.end()+int(length[1])]
            if b'/DCTDecode' in value:
                with Image.open(io.BytesIO(stream)) as image:selected_images.append(image.convert('RGB').getpixel((width//2,height//2)))
            elif b'/FlateDecode' in value:
                raw=zlib.decompress(stream)
                if len(raw)==width*height*3:
                    with Image.frombytes('RGB',(width,height),raw) as image:selected_images.append(image.getpixel((width//2,height//2)))
        self.assertTrue(selected_images,'Native PDF must contain selected-source raster pixels; owned fixture image headers: '+repr(image_headers))
        for pixel in selected_images:self.assert_blue(pixel)
        self.assertEqual(photo_store.accepted_photo_for_url(self.directory,PRIMARY,source=self.source)['selected']['id'],self.choice['selected']['id'])

    def test_phone_prepared_photo_uses_selected_variant_and_original_tab_keeps_primary_bytes(self):
        fixture.PhoneBrowserAcceptance.connect(self)
        self.page.locator('.listing-card').first.click()
        self.page.wait_for_function("document.querySelector('#phonePhotos img')?.naturalWidth===800",timeout=15000)
        self.assert_blue(self.browser_pixel('#phonePhotos img',.5,.5))
        self.page.locator('#phoneOriginalVariant').click()
        self.page.wait_for_function("document.querySelector('#phonePhotos img')?.naturalWidth===600")
        original=self.browser_pixel('#phonePhotos img',.5,.5);self.assertGreater(original[0],220);self.assertLess(original[2],35)
        self.page.locator('#phoneCropVariant').click();self.page.wait_for_function("document.querySelector('#phonePhotos img')?.naturalWidth===800")
        self.assert_blue(self.browser_pixel('#phonePhotos img',.5,.5));fixture.PhoneBrowserAcceptance.assert_mobile_fit(self)


if __name__=='__main__':unittest.main()
