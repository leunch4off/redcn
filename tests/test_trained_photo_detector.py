import io
import json
import math
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'cli'))
import trained_photo_detector as detector
import vision_photo


def rows():
    return [[0.]*26 for _ in range(detector.ROWS)]


def item(raw, index, feature, score=.9):
    raw[index][:5] = [10-index, 10, math.log(10), math.log(10), 1.]
    raw[index][5+detector.CLASS_IDS.index(feature)] = score


class TrainedDetectorTests(unittest.TestCase):
    def test_new_class_has_box_but_never_publishes_or_counts(self):
        raw = rows(); item(raw, 0, 'washer'); item(raw, 1, 'washer', .8)
        result = detector.decode(raw, 832, 416, 'a'*64)
        self.assertEqual([row['id'] for row in result], ['washer'])
        self.assertEqual(len(result[0]['evidence']), 1)
        self.assertFalse(result[0]['autoEligible']); self.assertIsNone(result[0]['count'])
        self.assertEqual(result[0]['evidence'][0]['photoId'], 'a'*64)
        self.assertAlmostEqual(result[0]['evidence'][0]['region'][0], 80/832)

    def test_ambiguous_dataset_classes_are_not_mapped_into_features(self):
        for feature in ('gasStove', 'cabinetry', 'cupboard'):
            raw = rows(); item(raw, 0, feature)
            self.assertEqual(detector.decode(raw, 416, 416, 'a'*64), [])

    def test_oven_never_creates_fridge_and_bad_scores_are_rejected(self):
        raw = rows(); item(raw, 0, 'oven')
        self.assertEqual([row['id'] for row in detector.decode(raw, 416, 416, 'a'*64)], ['oven'])
        for bad in (float('nan'), True, 2.):
            raw = rows(); raw[0][4] = bad
            with self.assertRaises(ValueError): detector.decode(raw, 416, 416, 'a'*64)

    def test_wrong_model_hash_fails_before_runtime_import(self):
        with tempfile.TemporaryDirectory() as root:
            folder=Path(root)/detector.ARTIFACT; folder.mkdir(parents=True)
            receipt={'status':'trained-unaccepted','onnx':{'sha256':detector.MODEL_SHA,
                'path':'model.onnx','inputShape':[1,3,416,416],
                'outputEncoding':'raw-yolox','classIds':detector.CLASS_IDS}}
            (folder/'trainreceipt.json').write_text(json.dumps(receipt))
            (folder/'model.onnx').write_bytes(b'wrong weights')
            with self.assertRaisesRegex(ValueError,'Контрольная'): detector.load_artifact(root)
            self.assertEqual(detector.cache_identity(root),'unavailable')

    def test_cancel_and_expired_deadline_never_start_worker(self):
        cancel=threading.Event(); cancel.set()
        with patch.object(detector.subprocess,'Popen') as start:
            with self.assertRaises(ValueError): detector.analyze(b'photo','image/jpeg','/unused',cancel=cancel)
            with self.assertRaises(ValueError): detector.analyze(b'photo','image/jpeg','/unused',deadline=time.monotonic()-1)
            start.assert_not_called()

    def test_selected_provider_bypasses_ollama_and_apple(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as folder, Image.new('RGB',(32,32)) as image, \
                patch('photo_ai_settings.load_config', return_value={'enabled':True,'model':detector.MODEL}), \
                patch.object(detector,'analyze',return_value={'provider':'yolox-trained','autoApply':False}) as analyze, \
                patch('photo_ai_runtime.ensure_started',side_effect=AssertionError('Ollama must not start')), \
                patch.object(vision_photo,'native_classify',side_effect=AssertionError('Apple fallback must not run')):
            result=vision_photo.analyze_image(image,folder,content=b'fixture',mime='image/png')
        self.assertEqual(result['provider'],'yolox-trained'); self.assertFalse(result['autoApply'])
        self.assertEqual(analyze.call_args.args[:3],(b'fixture','image/png',folder))

    def test_parent_forces_review_policy_even_if_worker_sets_old_auto_flags(self):
        class Process:
            returncode=0
            def communicate(self, **kwargs):
                return json.dumps({'provider':'yolox-trained','model':detector.MODEL,
                    'autoApply':True,'candidates':[{'id':'sofa','autoEligible':True,'count':4}]}).encode(),b''
            def poll(self):return 0
        with patch.object(detector.subprocess,'Popen',return_value=Process()):
            result=detector.analyze(b'fixture','image/jpeg','/unused')
        self.assertFalse(result['autoApply']); self.assertFalse(result['candidates'][0]['autoEligible'])
        self.assertIsNone(result['candidates'][0]['count'])

    def test_detector_reports_image_timing_and_never_fake_tokens(self):
        class Process:
            returncode=0
            def communicate(self, **kwargs):
                return json.dumps({'status':'review','provider':'yolox-trained','model':detector.MODEL,
                    'autoApply':False,'candidates':[], 'ai':{'inferenceSeconds':.05,'imagesPerSecond':20,
                    'processedImages':1,'tokensPerSecond':999,'generatedTokens':99}}).encode(),b''
            def poll(self):return 0
        events=[]
        with patch.object(detector.subprocess,'Popen',return_value=Process()):
            result=detector.analyze(b'fixture','image/jpeg','/unused',status=lambda message,**fields:events.append(fields))
        self.assertEqual(result['ai']['inferenceSeconds'],.05)
        self.assertEqual(result['ai']['imagesPerSecond'],20)
        self.assertFalse(result['ai']['tokenBased'])
        self.assertNotIn('tokensPerSecond',result['ai'])
        self.assertNotIn('generatedTokens',result['ai'])
        self.assertEqual([row['ai']['phase'] for row in events],['loading','analyzing','complete'])

    def test_expired_worker_is_terminated_and_reaped(self):
        class Process:
            terminated=False
            reaped=False
            def communicate(self, **kwargs):
                raise detector.subprocess.TimeoutExpired(['worker'], .1)
            def poll(self):return None
            def terminate(self):self.terminated=True
            def wait(self, timeout):self.reaped=True
        process=Process()
        clock=iter([0,0])
        with patch.object(detector.subprocess,'Popen',return_value=process), \
                patch.object(detector.time,'monotonic',side_effect=lambda:next(clock,21)):
            result=detector.analyze(b'fixture','image/jpeg','/unused')
        self.assertEqual(result['status'],'unavailable')
        self.assertTrue(process.terminated); self.assertTrue(process.reaped)


if __name__=='__main__':unittest.main()
