import math
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'cli'))
import tiny_photo_detector as detector


def rows():
    return [[0.] * 85 for _ in range(detector.ROWS)]


def item(raw, index, class_id, *, x=10, y=10, width=10, height=10, score=.9):
    # Tests place observations in stride-8 cells in the first grid row.
    raw[index][:5] = [x-index, y, math.log(width), math.log(height), 1.]
    raw[index][class_id+5] = score


class TinyDetectorTests(unittest.TestCase):
    def test_variant_size_and_hash_validation_without_loading_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'model.onnx'
            path.write_bytes(b'fixture model')
            with self.assertRaisesRegex(ValueError, 'SHA-256'):
                detector._model_bytes(path, 'nano')
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            with patch.dict(detector.VARIANTS, {'tiny': {**detector.VARIANTS['tiny'], 'sha256': digest}}):
                content, name, verified = detector._model_bytes(path, 'tiny', digest)
                self.assertTrue(detector._model_bytes(path, 'tiny')[2])
            self.assertEqual(name, 'yolox-tiny-0.1.1rc0')
            self.assertEqual(content, b'fixture model')
            self.assertTrue(verified)
            with self.assertRaisesRegex(ValueError, 'официальному'):
                detector._model_bytes(path, 'tiny', digest)
            with self.assertRaises(ValueError): detector._model_bytes(path, 'small')
            with self.assertRaises(ValueError): detector._model_bytes(path, 'tiny', 'bad')

    def test_rectangular_photo_boxes_scale_and_clip_padding(self):
        raw = rows()
        item(raw, 0, 72, x=10, y=10, width=10, height=10)
        value = detector.decode(raw, 832, 416)[0]
        self.assertEqual(value['id'], 'fridge')
        for actual, expected in zip(value['evidence'][0]['region'], [80/832, 80/416, 160/832, 160/416]):
            self.assertAlmostEqual(actual, expected)
        # A detection fully in the bottom padding cannot become a visible object.
        item(raw, 1, 57, x=10, y=45, width=4, height=4)
        self.assertEqual(len(detector.decode(raw, 832, 416)), 1)

    def test_nms_same_class_but_not_different_objects_or_classes(self):
        raw = rows()
        item(raw, 0, 56)
        item(raw, 1, 56, score=.8)
        item(raw, 2, 56, x=30, score=.7)
        item(raw, 3, 57, score=.8)
        values = {row['id']: row for row in detector.decode(raw, 416, 416)}
        self.assertEqual(len(values['chairs']['evidence']), 2)
        self.assertEqual(len(values['sofa']['evidence']), 1)
        self.assertIsNone(values['chairs']['count'])
        self.assertFalse(values['chairs']['autoEligible'])

    def test_oven_never_infers_fridge_hob_or_other_appliances(self):
        raw = rows()
        item(raw, 0, 69)
        self.assertEqual([row['id'] for row in detector.decode(raw, 416, 416)], ['oven'])

    def test_laptop_label_remains_specific_monitor_not_inferred(self):
        raw = rows()
        item(raw, 0, 63)
        result = detector.decode(raw, 416, 416)
        self.assertEqual(result[0]['label'], 'Ноутбук')
        self.assertFalse(result[0]['autoEligible'])
        self.assertIsNone(result[0]['count'])

    def test_global_winner_must_be_supported_and_unknown_rows_are_not_absence(self):
        raw = rows()
        item(raw, 0, 72, score=.5)
        raw[0][5] = .9  # person wins over fridge.
        self.assertEqual(detector.decode(raw, 416, 416), [])

    def test_bad_shape_nan_and_scores_rejected(self):
        with self.assertRaises(ValueError): detector.decode([], 416, 416)
        for value in (float('nan'), float('inf'), True):
            raw = rows(); raw[0][0] = value
            with self.assertRaises(ValueError): detector.decode(raw, 416, 416)
        raw = rows(); raw[0][4] = 2.
        with self.assertRaises(ValueError): detector.decode(raw, 416, 416)

    def test_threshold_rejects_low_confidence_without_claiming_absence(self):
        raw = rows(); item(raw, 0, 59, score=.2)
        self.assertEqual(detector.decode(raw, 416, 416), [])
        for bad in (True, float('nan'), 0, 2):
            with self.assertRaises(ValueError): detector.decode(raw, 416, 416, threshold=bad)


if __name__ == '__main__':
    unittest.main()
