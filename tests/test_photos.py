import base64
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
import redcn
import photo_store
import local_server
from PIL import Image

URL='https://images.cdn-cian.ru/images/123.jpg'

def picture(size=(800,600),kind='PNG'):
    buffer=io.BytesIO();Image.new('RGB',size,'#c3c8a9').save(buffer,kind);return buffer.getvalue()


class PhotoTests(unittest.TestCase):
    def setUp(self):self.temp=tempfile.TemporaryDirectory();self.directory=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()
    def attach(self,content=None,mime='image/png'):
        return photo_store.attach_photo(self.directory,URL,'data:'+mime+';base64,'+base64.b64encode(content or picture()).decode())

    def test_original_bytes_and_dimensions_survive_manual_derivatives(self):
        for size in [(1280,978),(720,960),(1000,1000)]:
            original=picture(size);meta=self.attach(original);crop=photo_store.crop_photo(self.directory,meta['id'],{'bottom':12})
            self.assertEqual(photo_store.asset(self.directory,meta['id'])[0],original)
            with Image.open(io.BytesIO(photo_store.asset(self.directory,crop['id'])[0])) as image:
                self.assertEqual(image.size,(size[0],__import__('math').ceil(size[1]*.88)))
            self.assertEqual(crop['verification'],'manual');self.assertNotEqual(meta['id'],crop['id'])

    def test_cached_original_never_refetches_and_corrupt_cache_is_detected(self):
        meta=self.attach()
        with patch.object(redcn,'fetch_bytes') as fetch:
            self.assertEqual(photo_store.load_photo(self.directory,URL)['id'],meta['id']);fetch.assert_not_called()
        (self.directory/'media'/meta['id']).write_bytes(b'bad')
        with self.assertRaisesRegex(ValueError,'повреждён'):photo_store.asset(self.directory,meta['id'])

    def test_photo_network_retry_is_bounded_to_timeout(self):
        opener=__import__('unittest.mock',fromlist=['MagicMock']).MagicMock()
        response=opener.open.return_value.__enter__.return_value;response.headers.get_content_type.return_value='image/png';response.read.return_value=picture()
        good=opener.open.return_value;opener.open.side_effect=[TimeoutError('TLS timeout'),good]
        with patch.object(redcn,'allowed_url',return_value=URL),patch.object(redcn.urllib.request,'build_opener',return_value=opener):
            self.assertEqual(redcn.fetch_bytes(URL,images=True)[1],'image/png')
        self.assertEqual(opener.open.call_count,2)

    def test_webp_response_is_recognized_even_if_url_ends_jpg(self):
        body=picture(kind='WEBP')
        with patch.object(redcn,'fetch_bytes',return_value=(body,'image/webp')):
            meta=photo_store.load_photo(self.directory,URL)
        self.assertEqual(meta['mime'],'image/webp');self.assertEqual(meta['width'],800)

    def test_exif_orientation_is_applied_before_crop(self):
        image=Image.new('RGB',(400,800));exif=Image.Exif();exif[274]=6;buffer=io.BytesIO();image.save(buffer,'JPEG',exif=exif)
        meta=self.attach(buffer.getvalue(),'image/jpeg');self.assertEqual((meta['width'],meta['height']),(800,400))
        cropped=photo_store.crop_photo(self.directory,meta['id'],{'bottom':10});self.assertEqual((cropped['width'],cropped['height']),(800,360))

    def test_auto_crop_checks_the_result_and_keeps_original(self):
        original=self.attach();box={'text':'ЦИАН','confidence':.99,'x':.75,'y':.84,'width':.16,'height':.05}
        with patch.object(photo_store,'recognize',side_effect=[([box],'test OCR'),([],'test OCR')]):
            result=photo_store.crop_photo(self.directory,original['id'])
        self.assertEqual(result['status'],'cropped');self.assertEqual(result['verification'],'ocr-checked');self.assertLess(result['height'],600)
        self.assertEqual(photo_store.asset(self.directory,original['id'])[0],picture())

    def test_uncertain_absent_and_center_marks_never_auto_crop(self):
        original=self.attach()
        for rows in [[],[{'text':'CIAN','confidence':.2,'x':.8,'y':.9,'width':.1,'height':.03}],[{'text':'CIAN','confidence':.99,'x':.45,'y':.45,'width':.1,'height':.04}]]:
            with patch.object(photo_store,'recognize',return_value=(rows,'test OCR')):result=photo_store.crop_photo(self.directory,original['id'])
            self.assertEqual(result['status'],'review');self.assertNotIn('kind',result)

    def test_id_footer_only_applies_to_cian_with_strong_edge_evidence(self):
        row={'text':'ID 300691182','confidence':1,'x':.76,'y':.885,'width':.08,'height':.0225}
        self.assertEqual(photo_store.watermark_boxes([row]),[])
        boxes=photo_store.watermark_boxes([row],True);self.assertEqual(boxes[0]['method'],'cian-id-footer')
        proposed=photo_store.propose_crop(1280,978,boxes);self.assertEqual(proposed['status'],'detected')
        self.assertLess(proposed['rect'][3],790)
        self.assertEqual(photo_store.watermark_boxes([{**row,'y':.3}],True),[])

    def test_remaining_mark_is_flagged_after_crop(self):
        original=self.attach();box={'text':'CIAN','confidence':.99,'x':.8,'y':.9,'width':.15,'height':.03}
        with patch.object(photo_store,'recognize',return_value=([box],'test OCR')):result=photo_store.crop_photo(self.directory,original['id'])
        self.assertEqual(result['status'],'review');self.assertEqual(result['verification'],'mark-remains')

    def test_invalid_images_margins_ids_and_host_rejected(self):
        with self.assertRaises(Exception):self.attach(b'<svg/>')
        with self.assertRaises(ValueError):self.attach(picture((20,20)))
        for value in [{'bottom':41},{'bottom':float('nan')},{'bottom':True},{'top':40,'bottom':40}]:
            with self.assertRaises(ValueError):photo_store.crop_rect(800,600,value)
        with self.assertRaises(ValueError):photo_store.asset(self.directory,'../../etc/passwd')
        with self.assertRaises(ValueError):photo_store.load_photo(self.directory,'https://127.0.0.1/x')

    def test_authenticated_api_serves_bytes_and_rejects_foreign_origin(self):
        original=self.attach();server=local_server.create_server(self.directory,0);thread=threading.Thread(target=server.serve_forever);thread.start()
        try:
            import re
            base=f'http://127.0.0.1:{server.server_port}';page=urllib.request.urlopen(base).read().decode();token=re.search('name="redcn-token" content="([^"]+)"',page)[1]
            for headers in [{},{'X-Redcn-Token':token,'Origin':'https://evil.test'}]:
                with self.assertRaises(urllib.error.HTTPError):urllib.request.urlopen(urllib.request.Request(base+'/api/local/media/'+original['id'],headers=headers))
            with urllib.request.urlopen(urllib.request.Request(base+'/api/local/media/'+original['id'],headers={'X-Redcn-Token':token})) as response:self.assertEqual(response.read(),picture())
            request=urllib.request.Request(base+'/api/local/photo',data=json.dumps({'action':'crop','id':original['id'],'margins':{'bottom':10}}).encode(),headers={'X-Redcn-Token':token,'Content-Type':'application/json'})
            with urllib.request.urlopen(request) as response:self.assertEqual(json.load(response)['height'],540)
        finally:server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()
