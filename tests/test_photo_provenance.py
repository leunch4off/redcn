"""Real format evidence and conservative, reversible photo policy boundaries."""
import base64
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
from PIL import Image
import photo_store

URL='https://images.cdn-cian.ru/images/example-1080967826-1.jpg'

def picture(kind='WEBP',size=(720,960)):
    image=Image.new('RGB',size,'#7296a3');stream=io.BytesIO();image.save(stream,kind);return stream.getvalue()

class PhotoProvenanceTests(unittest.TestCase):
    def setUp(self):self.temp=tempfile.TemporaryDirectory();self.directory=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()
    def attach(self,body=None,**kwargs):
        return photo_store.attach_photo(self.directory,URL,'data:image/jpeg;base64,'+base64.b64encode(body or picture()).decode(),**kwargs)

    def test_actual_webp_bytes_override_jpg_url_and_browser_mime_without_reencoding(self):
        body=picture();row=self.attach(body)
        self.assertEqual(row['mime'],'image/webp');self.assertEqual(row['reportedMime'],'image/jpeg')
        self.assertEqual(row['mimeEvidence'],'magic-and-decoder');self.assertEqual(photo_store.asset(self.directory,row['id'])[0],body)
        with patch.object(photo_store.redcn,'fetch_bytes',return_value=(body,'image/jpeg')):
            fetched=photo_store.load_photo(self.directory,URL,refresh=True)
        self.assertEqual(fetched['mime'],'image/webp')

    def test_layout_metadata_is_allowlisted_and_prevents_automatic_crop_or_furniture_analysis(self):
        row=self.attach(source={'photoId':1083817631,'order':10,'isLayout':True,'isCianLayout':True,
            'variants':{'fullUrl':URL,'miniUrl':'https://evil.test/x.jpg'},'auth':'SECRET'})
        self.assertEqual(row['photoId'],'1083817631');self.assertEqual(row['variants'],{'fullUrl':URL});self.assertNotIn('SECRET',str(row))
        with patch.object(photo_store,'recognize',side_effect=AssertionError('Layout must not use OCR')):
            decision=photo_store.auto_crop_photo(self.directory,row['id'])
            self.assertEqual(decision['reason'],'layout');self.assertEqual(decision['autoStatus'],'skipped')
            with self.assertRaisesRegex(ValueError,'Планировка'):photo_store.analyze_photo(self.directory,row['id'])
        manual=photo_store.crop_photo(self.directory,row['id'],{'bottom':2})
        self.assertEqual(manual['verification'],'manual');self.assertEqual(manual['photoId'],'1083817631');self.assertEqual(photo_store.asset(self.directory,row['id'])[0],picture())
        with patch.object(photo_store.redcn,'fetch_bytes',return_value=(picture(),'image/webp')):
            refreshed=photo_store.load_photo(self.directory,URL,refresh=True)
        self.assertTrue(refreshed['isLayout']);self.assertEqual(refreshed['photoId'],'1083817631')

    def test_empty_ocr_is_not_confirmed_cleanliness_and_parameters_are_part_of_cache(self):
        row=self.attach()
        with patch.object(photo_store,'recognize',return_value=([],'fixture OCR')) as ocr:
            first=photo_store.auto_crop_photo(self.directory,row['id']);again=photo_store.auto_crop_photo(self.directory,row['id'])
            self.assertEqual(ocr.call_count,3)
            with patch.object(photo_store,'AUTO_CROP_MAX_AREA_LOSS',.20):fresh=photo_store.auto_crop_photo(self.directory,row['id'])
            self.assertEqual(ocr.call_count,6)
        self.assertEqual(first['autoStatus'],'unchanged');self.assertEqual(first['markState'],'not-detected')
        self.assertFalse(first['cleanlinessConfirmed']);self.assertTrue(again['cached']);self.assertFalse(fresh['cached']);self.assertNotEqual(first['policy'],fresh['policy'])

    def test_small_or_multiple_distant_edge_regions_need_review(self):
        box={'x':.8,'y':.9,'width':.1,'height':.03,'confidence':.99,'text':'CIAN'}
        self.assertEqual(photo_store.propose_crop(120,200,[box])['reason'],'small-image')
        small=self.attach(picture(size=(120,200)))
        with patch.object(photo_store,'recognize',side_effect=AssertionError('Small preview must not use automatic OCR')):
            self.assertEqual(photo_store.auto_crop_photo(self.directory,small['id'])['reason'],'small-image')
        repeated=[{**box,'y':.08},{**box,'y':.85}]
        self.assertEqual(photo_store.propose_crop(720,960,repeated)['reason'],'area-loss')

    def test_cached_source_annotation_preserves_pixels_and_photo_identity(self):
        original=self.attach()
        with patch.object(photo_store.redcn,'fetch_bytes',side_effect=AssertionError('Cached source must not download')):
            updated=photo_store.load_photo(self.directory,URL,source={'photoId':'1080967826','order':4,'isLayout':False})
        self.assertTrue(updated['cached']);self.assertEqual(updated['id'],original['id']);self.assertEqual(updated['photoId'],'1080967826')

    def test_malformed_footer_ocr_is_retryable_not_confirmed_clean(self):
        row=self.attach()
        with patch.object(photo_store,'recognize',side_effect=[([],'fixture OCR'),({'bad':'shape'},'fixture OCR')]):
            result=photo_store.auto_crop_photo(self.directory,row['id'])
        self.assertEqual(result['autoStatus'],'unavailable');self.assertFalse(result['cleanlinessConfirmed'])
        self.assertEqual(result['markState'],'not-checked')

    def test_perceptual_candidate_keeps_both_sha_distinct_originals_and_skips_layout(self):
        from PIL import ImageDraw
        image=Image.new('RGB',(720,960),'#eee');draw=ImageDraw.Draw(image);draw.rectangle((100,100,550,650),fill='#345');draw.line((100,600,500,150),fill='white',width=12)
        a=io.BytesIO();image.save(a,'WEBP',quality=95);draw.text((560,900),'CIAN',fill='#bbb');b=io.BytesIO();image.save(b,'WEBP',quality=80)
        left=self.attach(a.getvalue());right=self.attach(b.getvalue());layout=self.attach(picture('PNG'),source={'isLayout':True})
        pairs=photo_store.near_duplicate_candidates(self.directory,[left['id'],right['id'],layout['id']])
        self.assertEqual(len(pairs),1);self.assertEqual(pairs[0]['action'],'retain-both');self.assertTrue(pairs[0]['requiresReview'])
        self.assertNotEqual(left['id'],right['id']);self.assertEqual(photo_store.asset(self.directory,left['id'])[0],a.getvalue());self.assertEqual(photo_store.asset(self.directory,right['id'])[0],b.getvalue())

if __name__=='__main__':unittest.main()
