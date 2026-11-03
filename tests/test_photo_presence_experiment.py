import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'cli'))
from photo_presence_experiment import read_manifest,presence_summary


class PresenceExperimentTests(unittest.TestCase):
    def manifest(self,rows,**updates):
        return {'humanVerified':False,'exhaustive':False,'photos':rows,**updates}

    def read(self,value):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'manifest.json';path.write_text(json.dumps(value))
            return read_manifest(path)

    def row(self,sha,group,**labels):
        return {'originalId':sha*64,'listingGroup':group,'positiveIds':['sofa'],**labels}

    def test_shared_photo_joins_apartment_groups_before_any_split(self):
        rows,_=self.read(self.manifest([self.row('a','A'),self.row('a','B'),self.row('b','B'),self.row('c','C'),self.row('b','C')]))
        self.assertEqual(len(rows),3)
        self.assertEqual(len({row['listingGroup'] for row in rows}),1)
        self.assertEqual(len({row['split'] for row in rows}),1)

    def test_unknown_classes_never_become_verified_negatives(self):
        result=presence_summary([{'split':'test','positiveIds':['sofa'],'scores':{'sofa':.9,'washer':.99}}])['0.35']['totals']
        self.assertEqual(result['positiveWitnesses'],1)
        self.assertEqual(result['recoveredPositiveWitnesses'],1)
        self.assertEqual(result['explicitNegativeWitnesses'],0)
        self.assertEqual(result['negativeWitnessViolations'],0)
        self.assertEqual(result['unlabelledPredictions'],1)
        self.assertNotIn('precision',result)

    def test_only_explicit_absence_counts_and_contradictions_fail(self):
        result=presence_summary([{'split':'validation','positiveIds':['sofa'],'absentIds':['washer'],'scores':{'washer':.9}}])['0.35']['totals']
        self.assertEqual(result['explicitNegativeWitnesses'],1)
        self.assertEqual(result['negativeWitnessViolations'],1)
        with self.assertRaises(ValueError):self.read(self.manifest([self.row('a','A',absentIds=['sofa'])]))
        with self.assertRaises(ValueError):self.read(self.manifest([self.row('a','A'),self.row('a','B',positiveIds=[],absentIds=['sofa'])]))

    def test_assistant_labels_cannot_be_claimed_human_or_exhaustive(self):
        for update in ({'humanVerified':True},{'exhaustive':True}):
            with self.assertRaises(ValueError):self.read(self.manifest([self.row('a','A')],**update))

    def test_broad_stove_and_cabinet_cannot_train_narrow_legacy_classes(self):
        spec=importlib.util.spec_from_file_location('train_presence_test',ROOT/'scripts/train-photo-presence-v30.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        positive,negative=module.positive_indices({'positiveIds':['stove','cabinet','fridge'],'absentIds':['wardrobe']})
        from trained_photo_detector import CLASS_IDS
        self.assertEqual([CLASS_IDS[i] for i in positive],['fridge'])
        self.assertEqual([CLASS_IDS[i] for i in negative],['wardrobe'])


if __name__=='__main__':unittest.main()
