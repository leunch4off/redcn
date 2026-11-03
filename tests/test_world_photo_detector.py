"""Contract tests for the pinned, review-only WORLD provider; no model downloads."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'cli'))
import world_photo_detector as world
import trained_photo_detector as bounded
import photo_auto_policy as policy
import vision_photo
import photo_native_runtime as native

DIGEST='a'*64


def rows():
    return [[0.]*25 for _ in range(world.ROWS)]


def detection(raw,index,feature,score=.9):
    raw[index][:4]=[320.,320.,160.,320.]
    raw[index][4+world.CLASS_IDS.index(feature)]=score


class WorldPhotoTests(unittest.TestCase):
    def test_centered_letterbox_returns_original_coordinates_without_counts(self):
        raw=rows();detection(raw,0,'washer')
        result=world.decode(raw,240,480,4/3,(160,0),DIGEST)
        self.assertEqual([row['id'] for row in result],['washer'])
        candidate=result[0];evidence=candidate['evidence'][0]
        for actual,expected in zip(evidence['region'],[.25,.25,.5,.5]):self.assertAlmostEqual(actual,expected)
        self.assertEqual(evidence['originalId'],DIGEST)
        self.assertEqual((evidence['provider'],evidence['model']),('onnx-world',world.MODEL))
        self.assertIsNone(candidate['count']);self.assertFalse(candidate['autoEligible'])
        self.assertFalse(candidate['autoCountEligible'])

    def test_canonical_cabinet_suppresses_overlapping_ambiguous_heads(self):
        raw=rows();detection(raw,0,'cabinetry');detection(raw,1,'cupboard',.8)
        result=world.decode(raw,640,640,1,(0,0),DIGEST)
        self.assertEqual([row['id'] for row in result],['cabinet'])
        self.assertEqual(len(result[0]['evidence']),1)
        self.assertEqual(result[0]['evidence'][0]['rawClassId'],'cabinetry')
        self.assertNotIn('kitchenFurniture',[row['id'] for row in result])

    def test_raw_gas_stove_becomes_only_broad_surface_and_low_scores_are_ignored(self):
        raw=rows();detection(raw,0,'gasStove');detection(raw,1,'fridge',.349)
        result=world.decode(raw,640,640,1,(0,0),DIGEST)
        self.assertEqual([row['id'] for row in result],['stove'])
        self.assertEqual(result[0]['evidence'][0]['rawClassId'],'gasStove')

    def test_wrong_shape_nonfinite_and_boolean_scores_fail_closed(self):
        for invalid in (float('nan'),float('inf'),True,-.1,1.01):
            raw=rows();raw[0][4]=invalid
            with self.subTest(invalid=invalid),self.assertRaises(ValueError):world.decode(raw,640,640,1,(0,0),DIGEST)
        with self.assertRaises(ValueError):world.decode([],640,640,1,(0,0),DIGEST)

    def test_measured_pilot_200_box_limit_does_not_hide_category_after_box_100(self):
        raw=rows()
        for index in range(101):
            detection(raw,index,'chairs',.9)
            raw[index][:4]=[float(2+index*5),20.,2.,2.]
        detection(raw,101,'fridge',.8)
        result=world.decode(raw,640,640,1,(0,0),DIGEST)
        self.assertEqual({row['id'] for row in result},{'chairs','fridge'})
        self.assertTrue(all(row['count'] is None for row in result))

    def test_artifact_is_fixed_by_hash_classes_and_prompts_not_receipt_path(self):
        with tempfile.TemporaryDirectory() as td:
            folder=Path(td)/world.ARTIFACT;folder.mkdir(parents=True)
            path=Path(td)/world.MODEL_PATH;path.parent.mkdir();content=b'local pinned world fixture';path.write_bytes(content)
            sha=hashlib.sha256(content).hexdigest()
            receipt={'sourceCheckpointSha256':world.SOURCE_CHECKPOINT_SHA,'onnxSha256':sha,
                'classIds':world.CLASS_IDS,'prompts':world.PROMPTS,'status':'exported-unaccepted',
                'onnxFile':'https://invalid.test/no-fetch'}
            receipt_path=folder/'export-receipt.json';receipt_path.write_text(json.dumps(receipt))
            with patch.object(world,'MODEL_SHA',sha):
                self.assertEqual(world.load_artifact(td)[0],content)
                first=world.cache_identity(td)
                path.write_bytes(b'changed')
                with self.assertRaisesRegex(ValueError,'Контрольная сумма'):world.load_artifact(td)
                self.assertNotEqual(first,world.cache_identity(td))
                path.write_bytes(content);receipt['prompts']=['changed'];receipt_path.write_text(json.dumps(receipt))
                with self.assertRaisesRegex(ValueError,'Паспорт'):world.load_artifact(td)

    def test_world_routes_to_bounded_worker_without_ollama_or_apple(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as td,Image.new('RGB',(32,32)) as image, \
                patch('photo_ai_settings.load_config',return_value={'enabled':True,'model':world.MODEL,'autoApply':True}), \
                patch.object(bounded,'analyze',return_value={'status':'review','provider':world.PROVIDER,'model':world.MODEL,
                    'originalId':DIGEST,'candidates':[]}) as analyze, \
                patch('photo_ai_runtime.ensure_started',side_effect=AssertionError('Must not start Ollama')), \
                patch.object(vision_photo,'native_classify',side_effect=AssertionError('Must not fall back to Apple')):
            result=vision_photo.analyze_image(image,td,content=b'fixture',mime='image/webp')
        self.assertEqual(analyze.call_args.kwargs['model'],world.MODEL)
        self.assertEqual(analyze.call_args.args[1],'image/webp')
        self.assertEqual(result['autoApplyMode'],'suggestions-only')
        self.assertEqual(result['autoApplySummary']['automaticFacts'],0)
        self.assertEqual(result['autoApplySummary']['acceptedClasses'],0)

    def test_world_review_journal_preserves_identity_without_promotion(self):
        raw=rows();detection(raw,0,'sofa',.96)
        result={'status':'review','provider':world.PROVIDER,'model':world.MODEL,'originalId':DIGEST,
            'sourceUrl':'https://images.cdn-cian.ru/images/world-test.jpg',
            'candidates':world.decode(raw,640,640,1,(0,0),DIGEST)}
        reviewed=policy.trained_policy(result,requested=True)
        journal=policy.review_journal({},[reviewed],requested=True)
        self.assertEqual(journal['pending'],1)
        self.assertEqual(journal['entries'][0]['provider'],world.PROVIDER)
        self.assertEqual(journal['entries'][0]['model'],world.MODEL)
        self.assertIsNone(journal['entries'][0]['count'])
        self.assertEqual(reviewed['autoApplySummary']['automaticFacts'],0)
        result['provider']='yolox-trained'
        self.assertEqual(policy.review_journal({},[result],requested=True)['entries'],[])

    def test_world_parent_cannot_accept_mismatching_provider(self):
        class Process:
            returncode=0
            def communicate(self,**kwargs):
                return json.dumps({'status':'review','provider':'yolox-trained','model':world.MODEL,'candidates':[]}).encode(),b''
            def poll(self):return 0
        with patch('photo_native_runtime.runtime_command',return_value=(['python'],{'selection':'legacy'})), \
                patch.object(bounded.subprocess,'Popen',return_value=Process()) as launch:
            result=world.analyze(b'fixture','image/webp','/unused')
        self.assertIn('world_photo_detector.py',launch.call_args.args[0][1])
        self.assertEqual(result['status'],'unavailable')
        self.assertEqual(result['candidates'],[])

    def installer(self):
        spec=importlib.util.spec_from_file_location('install_photo_native_test',ROOT/'scripts/install-photo-native-runtime.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        return module

    def test_world_extension_install_pins_wheel_and_never_changes_provider_settings(self):
        installer=self.installer()
        with tempfile.TemporaryDirectory() as td:
            cache=Path(td);folder=cache/native.FOLDER;folder.mkdir(parents=True)
            def run(command,**kwargs):
                Path(command[command.index('--report')+1]).write_text('{}')
            with patch.object(world,'load_artifact',return_value=(b'fixture','receipt')), \
                    patch.object(native,'runtime_command',return_value=(['python'],{'selection':'native-arm','status':'verified'})) as runtime, \
                    patch.object(installer.subprocess,'run',side_effect=run) as pip:
                installer.install_world_extension(cache)
            command=pip.call_args.args[0]
            for flag in ('-arm64','-I','--isolated','--no-deps','--require-hashes','--only-binary=:all:'):self.assertIn(flag,command)
            self.assertEqual((folder/'pinned-world-requirements.txt').read_text(),
                'opencv-python=='+native.OPENCV_VERSION+' --hash=sha256:'+native.OPENCV_WHEEL_SHA+'\n')
            self.assertEqual(runtime.call_args.kwargs['model'],world.MODEL)
            self.assertFalse((cache/'photo-ai.json').exists())

    def test_existing_changed_world_extension_is_not_silently_overwritten(self):
        installer=self.installer()
        with tempfile.TemporaryDirectory() as td:
            cache=Path(td);(cache/native.FOLDER/'venv/lib/python3.11/site-packages/cv2').mkdir(parents=True)
            with patch.object(world,'load_artifact',return_value=(b'fixture','receipt')), \
                    patch.object(native,'runtime_command',return_value=(['python'],{'selection':'native-arm','status':'verified'})), \
                    patch.object(native,'_world_preprocessing',side_effect=native.NativeRuntimeError('OpenCV changed')), \
                    patch.object(installer.subprocess,'run') as pip:
                with self.assertRaises(native.NativeRuntimeError):installer.install_world_extension(cache)
            pip.assert_not_called()


if __name__=='__main__':unittest.main()
