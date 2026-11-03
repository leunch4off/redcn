"""Public dataset gate and statistics contracts, not runtime performance data."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('photo_native_benchmark',ROOT/'scripts/benchmark-photo-native.py')
benchmark=importlib.util.module_from_spec(spec);spec.loader.exec_module(benchmark)


class NativePhotoBenchmarkContractTests(unittest.TestCase):
    def test_nearest_rank_percentiles_have_explicit_small_sample_meaning(self):
        values=list(range(1,21));result=benchmark.stats(values)
        self.assertEqual(result['p50'],10);self.assertEqual(result['p95'],19)
        self.assertEqual(result['percentileMethod'],'nearest-rank');self.assertEqual(benchmark.stats([]),{'n':0})

    def fixture(self,folder):
        root=folder/'tools/photo-training/20261101-first-pass';(root/'run-01').mkdir(parents=True)
        source=b'public test manifest';model=b'public test model'
        (root/'dataset-source.json').write_bytes(source);(root/'run-01/model.onnx').write_bytes(model)
        rows=[]
        for index in range(80):
            identity=format(index,'016x')
            rows.append({'imageId':identity,'sourceSplit':'validation','sourceUrl':'https://open-images-dataset.s3.amazonaws.com/validation/'+identity+'.jpg',
                'attribution':{'License':'https://creativecommons.org/licenses/by/2.0/','OriginalLandingURL':'https://www.flickr.com/photos/public/'+identity,'Author':'Public test'},
                'status':'downloaded','path':'images/validation/'+identity+'.jpg','sha256':'a'*64,'bytes':1})
        ready={'source':'Open Images V7 boxes','sourceReadyManifestSha256':benchmark.sha(source),'explicitDatasetLicenseAllowance':True,'splits':{'train':[],'validation':rows}}
        (root/'dataset-ready.json').write_text(json.dumps(ready))
        return root,ready,benchmark.sha(source),benchmark.sha(model)

    def test_manifest_origin_is_checked_before_any_photo_is_opened(self):
        with tempfile.TemporaryDirectory() as directory:
            cache=Path(directory);root,ready,source_sha,model_sha=self.fixture(cache)
            with patch.object(benchmark,'EXPECTED_MANIFEST',source_sha),patch.object(benchmark,'EXPECTED_MODEL',model_sha):
                self.assertEqual(len(benchmark.dataset(cache)[1]),80)
                ready['splits']['validation'][0]['sourceUrl']='file:///private/user-photo.jpg'
                (root/'dataset-ready.json').write_text(json.dumps(ready))
                with self.assertRaisesRegex(ValueError,'public origin'):benchmark.dataset(cache)

    def test_private_path_and_unattributed_row_are_rejected_by_origin_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            cache=Path(directory);root,ready,source_sha,model_sha=self.fixture(cache)
            with patch.object(benchmark,'EXPECTED_MANIFEST',source_sha),patch.object(benchmark,'EXPECTED_MODEL',model_sha):
                for field,value in [('path','../../private/user.jpg'),('attribution',{})]:
                    previous=ready['splits']['validation'][0][field]
                    ready['splits']['validation'][0][field]=value
                    (root/'dataset-ready.json').write_text(json.dumps(ready))
                    with self.assertRaisesRegex(ValueError,'public origin'):benchmark.dataset(cache)
                    ready['splits']['validation'][0][field]=previous


if __name__=='__main__':unittest.main()
