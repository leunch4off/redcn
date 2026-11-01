from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'cli'))
from watermark_quality import crop_measurements, minimal_edge_crop, presence_counts


class WatermarkQualityTests(unittest.TestCase):
    def test_original_geometry_oracle_selects_smallest_edge_loss(self):
        # Pure geometry fixture, not a real-image accuracy measurement.
        value = minimal_edge_crop(1000, 1000, [[.8, .88, .1, .04]])
        self.assertEqual(value['rect'], [0, 0, 1000, 878])
        self.assertAlmostEqual(value['retainedArea'], .878)

    def test_important_object_loss_and_remaining_mark_are_distinct(self):
        value = crop_measurements(1000, 1000, [0, 0, 1000, 850], [[.8, .88, .1, .04]],
                                  [{'label': 'visible oven', 'region': [.2, .6, .3, .4]}])
        self.assertEqual(value['annotatedMarkRegionsRemaining'], 0)
        self.assertAlmostEqual(value['importantVisibleRegions'][0]['retainedFraction'], .625)
        self.assertTrue(value['importantRegionCut'])
        self.assertAlmostEqual(value['extraAreaLossVsAnnotatedOracle'], .028)
        self.assertFalse(value['cleanlinessConfirmed'])

    def test_central_mark_has_no_permitted_edge_crop_and_must_be_reviewed(self):
        self.assertIsNone(minimal_edge_crop(1000, 1000, [[.45, .45, .1, .1]]))

    def test_errors_and_unknowns_do_not_silently_become_false_negatives(self):
        rows = [{'watermarkPresent': True, 'detected': True},
                {'watermarkPresent': True, 'detected': False},
                {'watermarkPresent': False, 'detected': True},
                {'watermarkPresent': False, 'detected': False},
                {'watermarkPresent': None, 'detected': False},
                {'watermarkPresent': True, 'detectorError': 'timeout'}]
        value = presence_counts(rows)
        self.assertEqual([value[key] for key in ('tp', 'fp', 'fn', 'tn')], [1, 1, 1, 1])
        self.assertEqual(value['detectorErrors'], 1)
        self.assertEqual(value['unknownTruthExcluded'], 1)
        self.assertFalse(value['noDetectionProvesClean'])


if __name__ == '__main__':
    unittest.main()
