"""Data audit tests need no model runtime and never infer absent labels."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/audit-photo-training-readiness.py'
spec = importlib.util.spec_from_file_location('photo_training_readiness', SCRIPT)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class PhotoTrainingReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.data = {'classes': [{'id': 'washer'}, {'id': 'dishwasher'}], 'splits': {
            'train': [self.row('train-a', 'train', [0])],
            'validation': [self.row('val-a', 'validation', [0]), self.row('val-b', 'validation', [])],
        }}
        self.manifest = self.base / 'dataset-ready.json'

    def tearDown(self):
        self.temporary.cleanup()

    def row(self, identifier, split, positives, verified=None, **extra):
        contents = ('synthetic-file-' + identifier).encode()
        path = self.base / (identifier + '.jpg')
        path.write_bytes(contents)
        return {'imageId': identifier, 'path': path.name, 'sha256': hashlib.sha256(contents).hexdigest(),
                'sourceSplit': split, 'status': 'downloaded',
                'boxes': [{'classIndex': index, 'xyxy': [.1, .1, .8, .8]} for index in positives],
                'verifiedClassIndices': list(positives if verified is None else verified), **extra}

    def report(self, data=None, **options):
        self.manifest.write_text(json.dumps(self.data if data is None else data))
        return audit.audit_manifest(self.manifest, **options)

    def test_missing_class_and_missing_verification_remain_unknown(self):
        self.data['splits']['validation'][1]['boxes'] = [{'classIndex': 0, 'xyxy': [.1, .1, .8, .8]}]
        report = self.report()
        washer, dishwasher = [item['splits']['validation'] for item in report['classes']]
        self.assertEqual(washer['annotatedPositiveImages'], 2)
        self.assertEqual(washer['verifiedPositiveImages'], 1)
        self.assertEqual(washer['verifiedNegativeImages'], 0)
        self.assertEqual(washer['unknownImages'], 1)
        self.assertEqual(dishwasher['unknownImages'], 2)
        self.assertEqual(dishwasher['verifiedNegativeImages'], 0)

    def test_only_explicit_verified_absence_counts_as_negative(self):
        self.data['splits']['validation'][1]['verifiedClassIndices'] = [0]
        report = self.report()
        self.assertEqual(report['classes'][0]['splits']['validation']['verifiedNegativeImages'], 1)
        self.assertEqual(report['classes'][1]['splits']['validation']['verifiedNegativeImages'], 0)
        self.assertEqual(report['classesWithPotentiallySufficientValidationSupport'], 0)
        self.assertFalse(report['automaticAcceptance'])

    def test_overlapping_ids_hashes_groups_and_source_split_are_reported(self):
        self.data['splits']['train'][0]['groupId'] = 'same-flat'
        duplicate = copy.deepcopy(self.data['splits']['train'][0])
        self.data['splits']['validation'].append(duplicate)
        report = self.report()
        self.assertEqual(report['integrityIssues'], {
            'duplicateImageHashes': 1, 'duplicateImageIds': 1, 'groupsAcrossSplits': 1, 'sourceSplitMismatch': 1})
        self.assertIn('manifest-or-image-integrity-issues', report['blockers'])

    def test_changed_missing_symlink_and_unread_images_are_not_verified(self):
        (self.base / 'train-a.jpg').write_bytes(b'changed')
        (self.base / 'val-a.jpg').unlink()
        (self.base / 'val-b.jpg').unlink()
        (self.base / 'val-b.jpg').symlink_to(self.base / 'train-a.jpg')
        report = self.report()
        self.assertEqual(report['integrityIssues']['imageHashMismatch'], 1)
        self.assertEqual(report['integrityIssues']['missingOrUnsafeImage'], 2)
        self.assertEqual(sum(item['verifiedImageHashes'] for item in report['splits'].values()), 0)
        metadata = audit.audit_manifest(self.manifest, verify_images=False)
        self.assertFalse(metadata['imageHashesChecked'])
        self.assertIn('image-hashes-not-checked', metadata['blockers'])

    def test_nonexhaustive_and_contradictory_negative_labels_are_blocked(self):
        row = self.data['splits']['validation'][0]
        row['nonExhaustiveClassIndices'] = [0]
        row['negativeClassIndices'] = [0, 1]
        report = self.report()
        self.assertEqual(report['integrityIssues'], {'negativeClassHasPositiveBox': 1,
            'negativeClassNotInVerifiedClasses': 1, 'nonExhaustiveClassMarkedVerified': 1})
        self.assertEqual(report['classes'][0]['splits']['validation']['verifiedPositiveBoxes'], 0)

    def test_perfect_prediction_support_does_not_imply_model_acceptance(self):
        self.data['classes'] = [{'id': 'washer'}]
        self.data['splits']['validation'] = [self.row('positive-' + str(i), 'validation', [0]) for i in range(75)]
        self.data['splits']['validation'] += [self.row('negative-' + str(i), 'validation', [], [0]) for i in range(20)]
        report = self.report()
        self.assertTrue(report['classes'][0]['validationSupport']['couldMeetExistingStatisticalGate'])
        self.assertGreaterEqual(report['classes'][0]['validationSupport'][
            'precisionWilsonLower95IfAllVerifiedBoxesDetectedWithoutFalsePositives'], .95)
        self.assertFalse(report['automaticAcceptance'])
        self.assertFalse(report['independentTest']['independenceVerified'])
        self.assertIn('independent-test-split-absent', report['blockers'])

    def test_declared_groups_test_and_rights_do_not_certify_independence(self):
        self.data['splits']['test'] = [self.row('test-a', 'test', [0], groupId='test-flat',
                                              domain='rental-apartment', individualLicenseVerified=True)]
        for split, rows in self.data['splits'].items():
            for index, row in enumerate(rows):
                row.setdefault('groupId', split + '-flat-' + str(index))
                row['individualLicenseVerified'] = True
        report = self.report()
        self.assertTrue(report['groups']['coverageComplete'])
        self.assertTrue(report['independentTest']['present'])
        self.assertTrue(report['independentTest']['rentalDomainDeclared'])
        self.assertFalse(report['independentTest']['independenceVerified'])
        self.assertFalse(report['automaticAcceptance'])

    def test_cli_preserves_source_bytes_and_does_not_expose_private_identifiers(self):
        self.report()
        before = {path.name: path.read_bytes() for path in self.base.iterdir()}
        result = subprocess.run([sys.executable, str(SCRIPT), '--manifest', str(self.manifest)],
                                text=True, capture_output=True, timeout=10, check=True)
        self.assertEqual(before, {path.name: path.read_bytes() for path in self.base.iterdir()})
        report = json.loads(result.stdout)
        self.assertTrue(report['imageHashesChecked'])
        self.assertNotIn(str(self.base), result.stdout)
        self.assertNotIn('train-a', result.stdout)
        self.assertNotIn('val-a', result.stdout)
        self.assertNotIn('sourceUrl', result.stdout)
        self.assertEqual(result.stderr, '')

    def test_invalid_class_box_and_manifest_symlink_are_rejected(self):
        for changed in ({'classIndex': True, 'xyxy': [.1, .1, .8, .8]},
                        {'classIndex': 0, 'xyxy': [.8, .1, .1, .8]},
                        {'classIndex': 0, 'xyxy': [float('nan'), .1, .8, .8]}):
            data = copy.deepcopy(self.data)
            data['splits']['train'][0]['boxes'] = [changed]
            with self.assertRaises(ValueError):
                self.report(data)
        self.report()
        symlink = self.base / 'linked-manifest.json'
        symlink.symlink_to(self.manifest)
        with self.assertRaises(ValueError):
            audit.audit_manifest(symlink)


if __name__ == '__main__':
    unittest.main()
