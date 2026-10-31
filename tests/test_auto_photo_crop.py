"""Automatic prepared photos keep originals and never select an unsure crop."""
import base64
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
import local_server
import phone_relay
import photo_store
from PIL import Image


URL='https://images.cdn-cian.ru/images/auto-verified.jpg'
BOX={'text':'CIAN','confidence':.99,'x':.76,'y':.89,'width':.15,'height':.035}


class AutomaticCropTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.directory=Path(self.temp.name)
        stream=io.BytesIO();Image.new('RGB',(800,600),'#aabbcc').save(stream,'PNG')
        self.bytes=stream.getvalue()
        self.original=photo_store.attach_photo(self.directory,URL,'data:image/png;base64,'+base64.b64encode(self.bytes).decode())

    def tearDown(self):self.temp.cleanup()

    def test_verified_crop_is_automatic_cached_and_original_is_unchanged(self):
        with patch.object(photo_store,'recognize',side_effect=[([BOX],'fixture OCR'),([],'fixture OCR')]) as ocr:
            decision=photo_store.auto_crop_photo(self.directory,self.original['id'])
            again=photo_store.auto_crop_photo(self.directory,self.original['id'])
        self.assertEqual(decision['autoStatus'],'cropped');self.assertEqual(decision['verification'],'ocr-checked')
        self.assertTrue(again['cached']);self.assertEqual(again['id'],decision['id']);self.assertEqual(ocr.call_count,2)
        self.assertEqual(photo_store.asset(self.directory,self.original['id'])[0],self.bytes)
        media=phone_relay.card_media(self.directory,{'fields':{'photos':[URL]}})
        self.assertEqual(media[0]['crop']['id'],decision['id'])

    def test_absent_mark_decision_is_cached_but_policy_change_checks_again(self):
        with patch.object(photo_store,'recognize',return_value=([],'fixture OCR')) as ocr:
            result=photo_store.auto_crop_photo(self.directory,self.original['id'])
            again=photo_store.auto_crop_photo(self.directory,self.original['id'])
            self.assertEqual(ocr.call_count,3)
            with patch.object(photo_store,'AUTO_CROP_POLICY','next-reviewed-policy'):
                fresh=photo_store.auto_crop_photo(self.directory,self.original['id'])
            self.assertEqual(ocr.call_count,6)
        self.assertEqual(result['autoStatus'],'unchanged');self.assertTrue(again['cached']);self.assertFalse(fresh['cached'])
        self.assertNotIn('crop',phone_relay.card_media(self.directory,{'fields':{'photos':[URL]}})[0])

    def test_weak_center_and_remaining_marks_never_become_prepared_crops(self):
        cases=[({**BOX,'confidence':.6},'unchanged'),({**BOX,'x':.45,'y':.45},'review'),(BOX,'review')]
        for number,(box,expected) in enumerate(cases):
            with self.subTest(case=number),patch.object(photo_store,'AUTO_CROP_POLICY','uncertain-'+str(number)),patch.object(photo_store,'recognize',return_value=([box],'fixture OCR')):
                result=photo_store.auto_crop_photo(self.directory,self.original['id'])
            self.assertEqual(result['autoStatus'],expected)
            self.assertNotIn('crop',phone_relay.card_media(self.directory,{'fields':{'photos':[URL]}})[0])
        self.assertEqual(photo_store.asset(self.directory,self.original['id'])[0],self.bytes)

    def test_unavailable_ocr_is_retryable_instead_of_cached_as_no_mark(self):
        with patch.object(photo_store,'recognize',side_effect=ValueError('OCR is temporarily busy')):
            failed=photo_store.auto_crop_photo(self.directory,self.original['id'])
        self.assertEqual(failed['autoStatus'],'unavailable')
        with patch.object(photo_store,'recognize',side_effect=[([BOX],'fixture OCR'),([],'fixture OCR')]) as ocr:
            result=photo_store.auto_crop_photo(self.directory,self.original['id'])
        self.assertEqual(result['autoStatus'],'cropped');self.assertFalse(result['cached']);self.assertEqual(ocr.call_count,2)

    def test_explicit_manual_crop_has_priority_and_avoids_new_ocr(self):
        manual=photo_store.crop_photo(self.directory,self.original['id'],{'bottom':10})
        with patch.object(photo_store,'recognize',side_effect=AssertionError('Manual crop must stay selected')):
            result=photo_store.auto_crop_photo(self.directory,self.original['id'])
        self.assertEqual(result['autoStatus'],'manual');self.assertEqual(result['id'],manual['id'])
        media=phone_relay.card_media(self.directory,{'fields':{'photos':[URL]}})
        self.assertEqual(media[0]['crop']['id'],manual['id'])

    def test_cancel_and_expired_budget_do_not_start_ocr(self):
        cancel=threading.Event();cancel.set()
        with patch.object(photo_store,'recognize') as ocr:
            with self.assertRaisesRegex(ValueError,'отменена'):photo_store.auto_crop_photo(self.directory,self.original['id'],cancel=cancel)
            skipped=photo_store.auto_crop_photo(self.directory,self.original['id'],deadline=time.monotonic()-1)
            ocr.assert_not_called()
        self.assertEqual(skipped['autoStatus'],'skipped')

    def test_cached_success_is_rechecked_if_derivative_disappears(self):
        with patch.object(photo_store,'recognize',side_effect=[([BOX],'fixture OCR'),([],'fixture OCR')]):
            first=photo_store.auto_crop_photo(self.directory,self.original['id'])
        (self.directory/'media'/first['id']).unlink()
        with patch.object(photo_store,'recognize',side_effect=[([BOX],'fixture OCR'),([],'fixture OCR')]) as ocr:
            repaired=photo_store.auto_crop_photo(self.directory,self.original['id'])
        self.assertFalse(repaired['cached']);self.assertEqual(ocr.call_count,2)
        self.assertTrue((self.directory/'media'/repaired['id']).is_file())

    def test_interrupted_native_compile_never_installs_partial_binary(self):
        exists=Path.exists
        def installed(path):return True if str(path)=='/usr/bin/swiftc' else exists(path)
        def interrupt(command,**kwargs):
            Path(command[command.index('-o')+1]).write_bytes(b'partial compiler output')
            raise photo_store.subprocess.TimeoutExpired(command,kwargs['timeout'])
        image=photo_store.open_photo(self.bytes)
        try:
            with patch.object(photo_store.sys,'platform','darwin'),patch.object(Path,'exists',installed),patch.object(photo_store.subprocess,'run',side_effect=interrupt):
                with self.assertRaises(photo_store.subprocess.TimeoutExpired):
                    photo_store.recognize(image,self.directory,deadline=time.monotonic()+1)
        finally:image.close()
        self.assertEqual(list((self.directory/'ocr').iterdir()),[])

    def test_import_prepares_photos_and_reports_counts_with_old_callback(self):
        result={'fields':{'photos':[URL]}};messages=[]
        with patch.object(photo_store,'recognize',side_effect=[([BOX],'fixture OCR'),([],'fixture OCR')]):
            phone_relay.cache_photos(self.directory,result,messages.append)
        self.assertEqual(result['mediaStatus'],'ready');self.assertEqual(result['photoProcessing']['cropped'],1)
        self.assertEqual(result['photoProcessing']['checked'],1);self.assertEqual(result['media'][0]['autoCrop']['autoStatus'],'cropped')
        self.assertTrue(any('повторный OCR' in value for value in messages))
        self.assertTrue(any('безопасно обрезано 1' in value for value in messages))

    def test_cancelled_import_does_not_download_or_run_ocr(self):
        cancel=threading.Event();cancel.set()
        with patch.object(photo_store,'load_photo') as load,patch.object(photo_store,'auto_crop_photo') as crop:
            with self.assertRaisesRegex(ValueError,'отменена'):
                phone_relay.cache_photos(self.directory,{'fields':{'photos':[URL]}},cancel=cancel)
        load.assert_not_called();crop.assert_not_called()

    def test_prepared_zip_keeps_uncropped_photo_and_original_zip_stays_original(self):
        other=URL.replace('auto-verified','auto-other');stream=io.BytesIO();Image.new('RGB',(300,500),'#ddaa77').save(stream,'PNG')
        second=photo_store.attach_photo(self.directory,other,'data:image/png;base64,'+base64.b64encode(stream.getvalue()).decode())
        with patch.object(photo_store,'recognize',side_effect=[([BOX],'fixture OCR'),([],'fixture OCR')]):
            crop=photo_store.auto_crop_photo(self.directory,self.original['id'])
        identifier=local_server.save_result(self.directory,{'fields':{'photos':[URL,other]},'titles':['房源'],'body':'测试'})
        prepared=zipfile.ZipFile(io.BytesIO(phone_relay.card_zip(self.directory,identifier,'prepared')))
        manifest=json.loads(prepared.read('manifest.json'))
        self.assertEqual([item['kind'] for item in manifest],['crop','original'])
        self.assertEqual(prepared.read(manifest[0]['name']),photo_store.asset(self.directory,crop['id'])[0])
        self.assertEqual(prepared.read(manifest[1]['name']),photo_store.asset(self.directory,second['id'])[0])
        originals=zipfile.ZipFile(io.BytesIO(phone_relay.card_zip(self.directory,identifier,'original')))
        original_manifest=json.loads(originals.read('manifest.json'))
        self.assertEqual([item['kind'] for item in original_manifest],['original','original'])
        self.assertEqual(originals.read(original_manifest[0]['name']),self.bytes)


if __name__=='__main__':unittest.main()
