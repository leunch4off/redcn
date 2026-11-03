"""Protocol checks for the isolated experiment; these are not model-quality tests."""
import importlib.util
from pathlib import Path
import socket
import sys
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import json

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('open_vocabulary_pilot',ROOT/'scripts/pilot-open-vocabulary-furniture.py')
pilot=importlib.util.module_from_spec(spec);spec.loader.exec_module(pilot)


class Array:
    def __init__(self,rows,shape=None):self.rows=rows;self.shape=shape or (1,25,8400)
    def __getitem__(self,key):return self
    @property
    def T(self):return self
    def tolist(self):return self.rows


def detection(cx,cy,width,height,score=.8,cls=4):
    scores=[0.]*21;scores[cls]=score
    return [cx,cy,width,height,*scores]


class OpenVocabularyPilotProtocolTests(unittest.TestCase):
    def test_centered_letterbox_predictions_rescale_to_original_pixels(self):
        output=Array([detection(192.,320.,256.,256.)])
        result=pilot.decode_world(output,1000,500,.64,(0,160),pilot.metrics_module())
        self.assertEqual(result[0]['classIndex'],4)
        for actual,expected in zip(result[0]['xyxy'],[.1,.1,.5,.9]):self.assertAlmostEqual(actual,expected)

    def test_bad_shape_classes_or_nonfinite_scores_fail_before_any_fact(self):
        for rows,shape in [([detection(1.,1.,1.,1.,1.1)],None),
                           ([detection(1.,1.,1.,1.,float('nan'))],None),
                           ([[0.,0.,1.,1.,.7,.5]],None),
                           ([],(1,84,8400))]:
            with self.subTest(rows=rows,shape=shape),self.assertRaises(ValueError):
                pilot.decode_world(Array(rows,shape),640,640,1,(0,0),pilot.metrics_module())

    def test_low_score_or_zero_area_stays_without_a_detection(self):
        rows=[detection(20.,20.,40.,40.,.01),detection(10.,10.,0.,40.,.8)]
        self.assertEqual(pilot.decode_world(Array(rows),640,640,1,(0,0),pilot.metrics_module()),[])

    def test_unknown_annotation_classes_do_not_become_verified_negatives(self):
        metrics=pilot.metrics_module()
        totals=metrics.detection_metrics([{'boxes':[{'classIndex':0,'xyxy':[0,0,1,1]}],
            'verifiedClassIndices':[0],'predictions':[{'classIndex':4,'score':.99,'xyxy':[0,0,1,1]}]}],21,.35)
        self.assertEqual(totals[0]['fn'],1)
        self.assertEqual(totals[4]['fp'],0)
        self.assertEqual(totals[4]['verifiedImages'],0)

    def test_offline_guard_blocks_network_without_contacting_a_host(self):
        with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as sock:
            with pilot.offline_network() as attempts:
                with self.assertRaisesRegex(OSError,'offline'):sock.connect(('example.invalid',443))
            self.assertEqual(attempts,['example.invalid'])

    def test_same_named_clip_fork_is_rejected_before_weights_are_loaded(self):
        data={'url':'https://github.com/ultralytics/CLIP.git','vcs_info':{'commit_id':pilot.CLIP_COMMIT}}
        with patch.object(pilot.importlib.metadata,'distribution',return_value=SimpleNamespace(read_text=lambda name:json.dumps(data))):
            with self.assertRaisesRegex(ValueError,'same-named fork'):pilot.verify_clip_package()
        data['url']='https://github.com/openai/CLIP.git'
        with patch.object(pilot.importlib.metadata,'distribution',return_value=SimpleNamespace(read_text=lambda name:json.dumps(data))):
            self.assertEqual(pilot.verify_clip_package(),data)


if __name__=='__main__':unittest.main()
