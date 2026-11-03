import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'cli'))
import photo_calibration as calibration


def corpus(count=75):
    splits={}
    for part in ('train','calibration','test'):
        rows=[]
        for n in range(count+20):
            sha=hashlib.sha256((part+str(n)).encode()).hexdigest()
            rows.append({'imageId':sha,'sha256':sha,'groupId':part+'-apartment-'+str(n),
                'sourceSplit':part,'domain':'rental-apartment','individualLicenseVerified':True,
                'verifiedClassIndices':[0],'negativeClassIndices':[0] if n>=count else [],
                'boxes':[] if n>=count else [{'classIndex':0,'xyxy':[.1,.1,.5,.5]}]})
        splits[part]=rows
    return {'classes':[{'id':'washer'}],'splits':splits}


def predictions(rows,fp=False):
    return {row['imageId']:[{'category':'washer','score':.9,'xyxy':[.1,.1,.5,.5]}]
            if row['boxes'] else ([{'category':'washer','score':.55,'xyxy':[.1,.1,.5,.5]}] if fp else []) for row in rows}


class PhotoCalibrationTests(unittest.TestCase):
    def test_calibration_uses_only_calibration_and_keeps_existing_gate(self):
        data=corpus();pred=predictions(data['splits']['calibration'],fp=True)
        frozen=calibration.freeze(data,pred,model_sha='a'*64)
        self.assertIsNotNone(frozen['classes']['washer']['threshold'])
        result=calibration.evaluate_test(data,predictions(data['splits']['test']),frozen,model_sha='a'*64)
        self.assertEqual(result['classes']['washer']['tp'],75)
        self.assertEqual(result['classes']['washer']['verifiedNegativeImages'],20)
        self.assertEqual(result['statisticallyPassingClasses'],['washer'])
        self.assertFalse(result['automaticAcceptance'])

    def test_small_dataset_does_not_pass_even_if_perfect(self):
        data=corpus(30);frozen=calibration.freeze(data,predictions(data['splits']['calibration']),model_sha='a'*64)
        self.assertIsNone(frozen['classes']['washer']['threshold'])
        self.assertLess(calibration.wilson(30,0),.95)

    def test_train_test_or_duplicate_groups_are_rejected(self):
        data=corpus();data['splits']['test'][0]['groupId']=data['splits']['train'][0]['groupId']
        with self.assertRaises(ValueError):calibration.validate_corpus(data)
        data=corpus();row=data['splits']['test'][0];row['sha256']=row['imageId']=data['splits']['train'][0]['sha256']
        with self.assertRaises(ValueError):calibration.validate_corpus(data)

    def test_predictions_must_explicitly_cover_exact_split(self):
        data=corpus();pred=predictions(data['splits']['test'])
        with self.assertRaises(ValueError):calibration.freeze(data,pred,model_sha='a'*64)
        pred=predictions(data['splits']['calibration']);pred.pop(next(iter(pred)))
        with self.assertRaises(ValueError):calibration.freeze(data,pred,model_sha='a'*64)

    def test_unknown_not_negative_and_duplicate_boxes_count_false_positive(self):
        data=corpus();rows=data['splits']['test'];rows[0]['verifiedClassIndices']=[]
        pred=predictions(rows);first=rows[1]['imageId'];pred[first].append(copy.deepcopy(pred[first][0]))
        result=calibration.metrics(rows,pred,['washer'],'washer',.85)
        self.assertEqual((result['tp'],result['fp'],result['unknownImages']),(74,1,1))

    def test_labels_and_frozen_policy_model_are_bound(self):
        data=corpus();frozen=calibration.freeze(data,predictions(data['splits']['calibration']),model_sha='a'*64)
        with self.assertRaises(ValueError):calibration.evaluate_test(data,predictions(data['splits']['test']),frozen,model_sha='b'*64)
        frozen['classes']['washer']['threshold']=.5
        with self.assertRaises(ValueError):calibration.evaluate_test(data,predictions(data['splits']['test']),frozen,model_sha='a'*64)

    def test_invalid_scores_labels_and_implicit_negative_are_rejected(self):
        data=corpus();data['splits']['test'][-1]['negativeClassIndices']=[]
        with self.assertRaises(ValueError):calibration.validate_corpus(data)
        data=corpus();pred=predictions(data['splits']['calibration']);pred[next(iter(pred))][0]['score']=float('nan')
        with self.assertRaises(ValueError):calibration.freeze(data,pred,model_sha='a'*64)

    def test_cli_test_reuse_requires_explicit_diagnostic(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder);data=corpus();pred=predictions(data['splits']['test'])
            frozen=calibration.freeze(data,predictions(data['splits']['calibration']),model_sha='a'*64)
            for name,value in [('data.json',data),('pred.json',pred),('frozen.json',frozen)]:
                (p/name).write_text(json.dumps(value))
            cmd=[sys.executable,str(ROOT/'scripts/photo-corpus.py'),'evaluate','--manifest',str(p/'data.json'),'--predictions',str(p/'pred.json'),'--model-sha','a'*64,'--frozen',str(p/'frozen.json')]
            first=subprocess.run(cmd+['--output',str(p/'one.json')],capture_output=True,text=True,timeout=15)
            self.assertEqual(first.returncode,0,first.stdout+first.stderr)
            second=subprocess.run(cmd+['--output',str(p/'two.json')],capture_output=True,text=True,timeout=15)
            self.assertNotEqual(second.returncode,0)
            third=subprocess.run(cmd+['--output',str(p/'three.json'),'--diagnostic'],capture_output=True,text=True,timeout=15)
            self.assertEqual(third.returncode,0,third.stdout+third.stderr)
            self.assertTrue(json.loads((p/'three.json').read_text())['diagnosticOnly'])


if __name__=='__main__':unittest.main()
