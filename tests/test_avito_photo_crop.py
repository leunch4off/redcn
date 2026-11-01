"""Conservative CIAN/Avito OCR policy; fixtures do not prove OCR accuracy."""
import base64
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
import photo_store
from PIL import Image


def box(text='Avito', **updates):
    return {'text':text,'confidence':.99,'x':.76,'y':.89,'width':.15,'height':.035,**updates}


class AvitoCropTests(unittest.TestCase):
    def test_both_brands_and_lookalikes_are_supported_as_bounded_tokens(self):
        for text,brand in [('Avito','avito'),('Авито','avito'),('Аvitо.ru','avito'),
                           ('CIAN','cian'),('ЦИАН','cian'),('C1AN.ru','cian')]:
            with self.subTest(text=text):
                rows=photo_store.watermark_boxes([box(text)])
                self.assertEqual(len(rows),1)
                self.assertEqual(rows[0]['brand'],brand)
        for text in ['musician','politician','special','Avitometer','notavito','cianna','Яavito','avitoя']:
            with self.subTest(text=text):self.assertEqual(photo_store.watermark_boxes([box(text)]),[])

    def test_adjacent_avito_fragments_keep_measured_union_but_far_fragments_do_not_join(self):
        first=box('Avi',x=.75,width=.045,height=.03)
        second=box('to',x=.805,width=.035,height=.03)
        result=photo_store.watermark_boxes([first,second])
        self.assertEqual(len(result),1);self.assertEqual(result[0]['brand'],'avito')
        self.assertAlmostEqual(result[0]['x'],.75);self.assertAlmostEqual(result[0]['width'],.09)
        self.assertEqual(photo_store.watermark_boxes([first,{**second,'x':.95}]),[])

    def test_invalid_tokens_cannot_form_a_valid_joined_brand(self):
        for bad in [box(confidence=2),box(confidence=True),box(width=-.05),
                    box(x=-.01),box(y=.99,height=.05),box(width=float('nan'))]:
            with self.subTest(bad=bad):self.assertEqual(photo_store.watermark_boxes([bad]),[])
        self.assertEqual(photo_store.watermark_boxes([box('Avi',width=-.04),box('to')]),[])

    def test_avito_crop_scales_but_center_and_excessive_loss_require_review(self):
        marks=photo_store.watermark_boxes([box()])
        for size in [(800,600),(720,960),(1000,1000),(1920,1080)]:
            with self.subTest(size=size):
                result=photo_store.propose_crop(*size,marks)
                self.assertEqual(result['status'],'detected')
                self.assertGreaterEqual(result['retainedArea'],.75)
                self.assertLess(result['rect'][3],size[1]*marks[0]['y'])
        central=photo_store.watermark_boxes([box(x=.45,y=.45)])
        self.assertEqual(photo_store.propose_crop(800,600,central)['status'],'review')
        self.assertEqual(photo_store.propose_crop(300,900,marks)['reason'],'small-image')

    def test_avito_id_alone_does_not_use_cian_footer_heuristic(self):
        self.assertEqual(photo_store.watermark_boxes([box('ID 12345678')]),[])

    def test_verified_avito_derivative_preserves_original_and_remaining_mark_blocks_auto(self):
        with tempfile.TemporaryDirectory() as directory:
            buffer=io.BytesIO();Image.new('RGB',(800,600),'#aa9988').save(buffer,'PNG')
            content=buffer.getvalue()
            # Explicit local fixture on an existing allowed host; no network.
            original=photo_store.attach_photo(directory,'https://20.img.avito.st/image/avito-policy-fixture.jpg',
                'data:image/png;base64,'+base64.b64encode(content).decode())
            with patch.object(photo_store,'recognize',side_effect=[([box()],'fixture OCR'),([],'fixture OCR')]):
                result=photo_store.auto_crop_photo(directory,original['id'])
            self.assertEqual(result['autoStatus'],'cropped')
            self.assertEqual(result['verification'],'ocr-checked')
            self.assertFalse(result['cleanlinessConfirmed'])
            self.assertEqual(photo_store.asset(directory,original['id'])[0],content)
            with patch.object(photo_store,'AUTO_CROP_POLICY','remaining-avito-fixture'), \
                    patch.object(photo_store,'recognize',return_value=([box()],'fixture OCR')):
                result=photo_store.auto_crop_photo(directory,original['id'])
            self.assertEqual(result['autoStatus'],'review')
            self.assertEqual(result['verification'],'mark-remains')
            self.assertEqual(photo_store.asset(directory,original['id'])[0],content)


if __name__=='__main__':unittest.main()
