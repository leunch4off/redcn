import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('tiny_training', Path(__file__).resolve().parents[1]/'scripts'/'train-tiny-photo-detector.py')
training = importlib.util.module_from_spec(spec)
spec.loader.exec_module(training)


class TrainingMetricsTests(unittest.TestCase):
    def test_baseline_deltas_keep_degradation_and_undefined_precision(self):
        baseline = {'0.5': [{'tp': 3, 'fp': 0, 'fn': 1, 'precision': 1., 'recall': .75}, {'tp': 0, 'fp': 0, 'fn': 1, 'precision': None, 'recall': 0.}]}
        final = {'0.5': [{'tp': 4, 'fp': 1, 'fn': 0, 'precision': .8, 'recall': 1.}, {'tp': 1, 'fp': 0, 'fn': 0, 'precision': 1., 'recall': 1.}]}
        rows = training.comparison_metrics(baseline, final, [{'id': 'sofa'}, {'id': 'washer'}])['0.5']
        self.assertAlmostEqual(rows[0]['precisionDelta'], -.2)
        self.assertFalse(rows[0]['bothMetricsNondecreasing'])
        self.assertIsNone(rows[1]['precisionDelta'])

    def test_each_target_can_only_match_once(self):
        records = [{'verifiedClassIndices': [0], 'boxes': [{'classIndex': 0, 'xyxy': [0, 0, 1, 1]}], 'predictions': [
            {'classIndex': 0, 'score': .9, 'xyxy': [0, 0, 1, 1]},
            {'classIndex': 0, 'score': .8, 'xyxy': [0, 0, 1, 1]}]}]
        result = training.detection_metrics(records, 1, .5)[0]
        self.assertEqual((result['tp'], result['fp'], result['fn']), (1, 1, 0))
        self.assertEqual(result['precision'], .5)

    def test_unknown_classes_do_not_become_false_positive_negatives(self):
        records = [{'verifiedClassIndices': [0], 'boxes': [], 'predictions': [{'classIndex': 1, 'score': .99, 'xyxy': [0, 0, 1, 1]}]}]
        result = training.detection_metrics(records, 2, .5)
        self.assertEqual(result[1]['verifiedImages'], 0)
        self.assertEqual(result[1]['fp'], 0)
        self.assertEqual(result[0]['verifiedNegativeImages'], 1)

    def test_small_perfect_positive_sample_never_passes_gate(self):
        row = {'verifiedClassIndices': [0], 'boxes': [{'classIndex': 0, 'xyxy': [0, 0, 1, 1]}],
            'predictions': [{'classIndex': 0, 'score': .99, 'xyxy': [0, 0, 1, 1]}]}
        result = training.detection_metrics([row]*5, 1, .5)[0]
        self.assertEqual(result['precision'], 1)
        self.assertFalse(result['statisticalGatePassed'])
        self.assertLess(result['precisionWilsonLower95'], .95)

    def test_positive_only_even_large_sample_never_passes_gate(self):
        row = {'verifiedClassIndices': [0], 'boxes': [{'classIndex': 0, 'xyxy': [0, 0, 1, 1]}],
            'predictions': [{'classIndex': 0, 'score': .99, 'xyxy': [0, 0, 1, 1]}]}
        self.assertFalse(training.detection_metrics([row]*100, 1, .5)[0]['statisticalGatePassed'])

    def test_classwise_nms_does_not_merge_different_classes(self):
        output = [[160, 160, 80, 80, 1., .9, .1], [160, 160, 80, 80, 1., .8, .1], [160, 160, 80, 80, 1., .1, .9]]
        rows = training.predictions_from_output(output, 640, 640, 320)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['xyxy'], [.375, .375, .625, .625])


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        (self.base/'a.jpg').write_bytes(b'image a')
        (self.base/'b.jpg').write_bytes(b'image b')
        self.data = {'classes': [{'id': 'bedAny', 'cocoIndex': 59}], 'splits': {
            'train': [{'imageId': 'a', 'path': 'a.jpg', 'sourceSplit': 'train', 'boxes': [{'classIndex': 0, 'xyxy': [.1, .1, .9, .9]}]}],
            'validation': [{'imageId': 'b', 'path': 'b.jpg', 'sourceSplit': 'validation', 'boxes': []}]}}

    def tearDown(self):
        self.temp.cleanup()

    def test_valid_dataset_hashes_files_and_keeps_roles(self):
        result = training.validate_manifest(self.data, self.base)
        self.assertEqual(result['splits']['train'][0]['verifiedClassIndices'], [0])
        self.assertEqual(len(result['splits']['train'][0]['sha256']), 64)

    def test_duplicate_pixels_and_source_split_swaps_rejected(self):
        data = copy.deepcopy(self.data)
        data['splits']['validation'][0]['path'] = 'a.jpg'
        with self.assertRaisesRegex(ValueError, 'Duplicate image content'):
            training.validate_manifest(data, self.base)
        data = copy.deepcopy(self.data)
        data['splits']['train'][0]['sourceSplit'] = 'validation'
        with self.assertRaisesRegex(ValueError, 'Source split'):
            training.validate_manifest(data, self.base)

    def test_unknown_label_and_invalid_box_are_rejected(self):
        for box in ({'classIndex': 1, 'xyxy': [.1, .1, .9, .9]}, {'classIndex': 0, 'xyxy': [.9, .1, .1, .9]}):
            data = copy.deepcopy(self.data); data['splits']['train'][0]['boxes'] = [box]
            with self.assertRaises(ValueError):
                training.validate_manifest(data, self.base)


if __name__ == '__main__':
    unittest.main()
