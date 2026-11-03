import base64
import io
import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
import vision_photo
import photo_store
from PIL import Image

class VisionTests(unittest.TestCase):
    def test_classification_never_infers_other_kitchen_appliances_or_counts(self):
        rows=[{'label':'oven','confidence':.93,'region':[0,0,1,1]},
              {'label':'oven','confidence':.82,'region':[.35,.35,.65,.65]},
              {'label':'furniture','confidence':.99,'region':[0,0,1,1]},
              {'label':'kitchen','confidence':.99,'region':[0,0,1,1]}]
        result=vision_photo.classifier_candidates(rows)
        self.assertEqual([r['id'] for r in result],['oven'])
        self.assertIsNone(result[0]['count']);self.assertEqual(result[0]['status'],'candidate')
        self.assertEqual(len(result[0]['evidence']),2)

    def test_classification_rejects_bad_scores_regions_and_unknown_labels(self):
        rows=[{'label':'fridge','confidence':.2,'region':[0,0,1,1]},
              {'label':'fridge','confidence':float('nan'),'region':[0,0,1,1]},
              {'label':'fridge','confidence':True,'region':[0,0,1,1]},
              {'label':'fridge','confidence':.99,'region':[.8,0,.8,1]},
              {'label':'room','confidence':.99,'region':[0,0,1,1]}]
        self.assertEqual(vision_photo.classifier_candidates(rows),[])

    def test_model_response_only_accepts_visible_supported_evidence(self):
        result=vision_photo.object_candidates({'objects':[
            {'id':'chairs','visible':True,'confidence':.94,'count':2,'evidence':'Две отдельные спинки стульев у стола'},
            {'id':'fridge','visible':False,'confidence':1,'count':1,'evidence':'Предполагается за шкафом'},
            {'id':'wifi','visible':True,'confidence':1,'count':1,'evidence':'Фото не доказывает наличие услуги'},
            {'id':'oven','visible':True,'confidence':.9,'count':True,'evidence':'Стеклянная дверца духовки'}]})
        self.assertEqual({r['id'] for r in result},{'chairs','oven'})
        self.assertEqual(result[0]['count'],2);self.assertIsNone(result[1]['count'])

    def test_native_timeout_becomes_actionable_validation_error(self):
        import subprocess
        with patch.object(vision_photo,'_native_classify',side_effect=subprocess.TimeoutExpired('vision',60)):
            with self.assertRaisesRegex(ValueError,'вовремя'):vision_photo.native_classify(None,'unused')

    def test_photo_analysis_caches_original_bound_result_without_promoting_features(self):
        with tempfile.TemporaryDirectory() as directory:
            buffer=io.BytesIO();Image.new('RGB',(400,600),'white').save(buffer,'PNG')
            original=photo_store.attach_photo(directory,'https://images.cdn-cian.ru/images/123.jpg','data:image/png;base64,'+base64.b64encode(buffer.getvalue()).decode())
            analysis={'version':1,'status':'review','engine':'test','candidates':[{'id':'oven','count':None,'status':'candidate'}]}
            with patch.object(vision_photo,'analyze_image',return_value=analysis) as model:
                first=photo_store.analyze_photo(directory,original['id']);second=photo_store.analyze_photo(directory,original['id'])
            self.assertEqual(model.call_count,1);self.assertFalse(first['cached']);self.assertTrue(second['cached'])
            self.assertEqual(first['originalId'],original['id']);self.assertNotIn('features',first)
            crop=photo_store.crop_photo(directory,original['id'],{'bottom':10})
            with self.assertRaisesRegex(ValueError,'оригиналу'):photo_store.analyze_photo(directory,crop['id'])

    def test_split_brand_tokens_are_joined_with_measured_union(self):
        rows=[{'text':'CI','confidence':.9,'x':.75,'y':.85,'width':.04,'height':.03},
              {'text':'AN','confidence':.91,'x':.80,'y':.85,'width':.04,'height':.03}]
        result=photo_store.watermark_boxes(rows)
        self.assertTrue(result);self.assertAlmostEqual(result[0]['width'],.09)
        far=[rows[0],{**rows[1],'x':.95}]
        self.assertEqual(photo_store.watermark_boxes(far),[])

    def test_footer_crop_scales_to_portrait_landscape_and_square(self):
        row={'text':'ID 300691182','confidence':.99,'x':.76,'y':.89,'width':.1,'height':.0225}
        boxes=photo_store.watermark_boxes([row],True)
        for width,height in [(720,960),(1280,978),(1000,1000)]:
            proposal=photo_store.propose_crop(width,height,boxes)
            self.assertEqual(proposal['status'],'detected');self.assertGreater(proposal['retainedArea'],.75)
            self.assertGreater(proposal['margins']['bottom'],17)
            self.assertLess(proposal['margins']['bottom'],22)
            self.assertLess(proposal['rect'][3],height*boxes[0]['y'])

if __name__=='__main__':unittest.main()
