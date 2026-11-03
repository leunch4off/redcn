import csv
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

SPEC = importlib.util.spec_from_file_location('prepare_photo_training_data',
    Path(__file__).resolve().parents[1] / 'scripts' / 'prepare-photo-training-data.py')
data = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(data)


def row(image='0000000000000001', label='/m/a', **updates):
    return {'ImageID': image, 'LabelName': label, 'Confidence': '1',
        'XMin': '.1', 'YMin': '.2', 'XMax': '.7', 'YMax': '.8',
        'IsGroupOf': '0', 'IsDepiction': '0', 'IsInside': '0', **updates}


class PhotoTrainingDataTests(unittest.TestCase):
    def test_archive_paths_portable_hashes_verified_and_exact_source_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            source_dir = Path(folder) / 'source'
            source_dir.mkdir()
            photo = source_dir / 'public.jpg'
            photo.write_bytes(b'public image fixture bytes')
            row = {'imageId': '0000000000000001', 'status': 'downloaded',
                'path': str(photo), 'sha256': hashlib.sha256(photo.read_bytes()).hexdigest(), 'boxes': []}
            parent = source_dir / 'dataset-ready.json'
            source = {'classes': [], 'splits': {'train': [row]}, 'parentManifestSha256': 'b' * 64,
                'individualLicensesVerified': False, 'sources': {'official': 'public-url'}}
            parent_bytes = json.dumps(source, indent=3).encode() + b'\n'
            parent.write_bytes(parent_bytes)
            destination = Path(folder) / 'archive'
            result = data.archive_ready_manifest(parent, destination)
            portable = json.loads((destination / 'dataset-ready.json').read_text())
            archived_path = portable['splits']['train'][0]['path']
            self.assertFalse(Path(archived_path).is_absolute())
            self.assertEqual((destination / archived_path).read_bytes(), photo.read_bytes())
            self.assertEqual((destination / 'dataset-source.json').read_bytes(), parent_bytes)
            self.assertEqual(parent.read_bytes(), parent_bytes)
            self.assertEqual(portable['parentManifestSha256'], hashlib.sha256(parent_bytes).hexdigest())
            self.assertEqual(portable['upstreamParentManifestSha256'], 'b' * 64)
            self.assertEqual(portable['sources'], source['sources'])
            self.assertFalse(portable['individualLicensesVerified'])
            self.assertEqual(result['images'], 1)
            with self.assertRaises(ValueError):
                data.archive_ready_manifest(parent, destination)
            photo.write_bytes(b'changed after manifest')
            with self.assertRaises(ValueError):
                data.archive_ready_manifest(parent, Path(folder) / 'wrong-hash')
            self.assertFalse((Path(folder) / 'wrong-hash').exists())

    def test_ready_manifest_excludes_failed_and_unknown_status_explicitly(self):
        downloaded = {'imageId': '0000000000000001', 'status': 'downloaded',
            'path': '/public/one.jpg', 'sha256': 'a' * 64,
            'boxes': [{'classIndex': 0, 'xyxy': [.1, .2, .7, .8]}]}
        manifest = {'classes': [{'id': 'chair'}], 'sources': {'url': 'public'},
            'splits': {'train': [downloaded, {'imageId': '0000000000000002',
                'status': 'failed', 'error': 'HTTP 404'},
                {'imageId': '0000000000000003', 'status': 'unexpected'}]},
            'selectedImageCountsByClass': {'train': [3]}}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'dataset.json'
            path.write_text(json.dumps(manifest))
            data.finalize_ready_manifest(path)
            ready = json.loads(path.with_name('dataset-ready.json').read_text())
        self.assertEqual(ready['splits']['train'], [downloaded])
        self.assertEqual(ready['exclusions']['count'], 2)
        self.assertEqual(ready['exclusions']['rows'][0]['reason'], 'HTTP 404')
        self.assertIn('unexpected', ready['exclusions']['rows'][1]['reason'])
        self.assertEqual(ready['selectedImageCountsByClass'], {'train': [3]})
        self.assertEqual(ready['readyImageCountsByClass'], {'train': [1]})
        self.assertEqual(ready['sources'], manifest['sources'])

    def test_ready_manifest_hashes_exact_parent_and_preserves_it(self):
        parent = b'{ "classes": [], "splits": {"train": []} }\n'
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'dataset.json'
            path.write_bytes(parent)
            result = data.finalize_ready_manifest(path)
            ready = json.loads(path.with_name('dataset-ready.json').read_text())
            self.assertEqual(path.read_bytes(), parent)
            self.assertEqual(result['parentManifestSha256'], hashlib.sha256(parent).hexdigest())
            self.assertEqual(ready['selectedDataSplitSha256']['train'], hashlib.sha256(b'[]').hexdigest())

    def test_selected_image_keeps_all_target_boxes_in_complete_group(self):
        classes = [{'openImagesId': '/m/a'}, {'openImagesId': '/m/b'}]
        rows = [row(), row(label='/m/b'), row(image='0000000000000002')]
        selected, counts = data.choose_groups(rows, classes, 1, 1)
        self.assertEqual(len(selected), 1)
        self.assertEqual(len(selected[0]['boxes']), 2)
        self.assertEqual(selected[0]['verifiedClassIndices'], [0, 1])
        self.assertEqual(counts, [1, 1])

    def test_missing_classes_remain_unknown_not_negative(self):
        selected, counts = data.choose_groups([row()],
            [{'openImagesId': '/m/a'}, {'openImagesId': '/m/b'}], 1, 5)
        self.assertEqual(selected[0]['verifiedClassIndices'], [0])
        self.assertEqual(counts, [1, 0])
        self.assertNotIn('negativeClassIndices', selected[0])

    def test_invalid_group_depiction_and_nan_do_not_become_boxes(self):
        for value in (row(IsGroupOf='1'), row(IsDepiction='1'),
                      row(XMin='nan'), row(XMax='2'), row(Confidence='0')):
            self.assertIsNone(data.parse_box(value, {'/m/a': 0}))

    def test_ignored_group_removes_class_exhaustiveness_without_losing_visible_box(self):
        selected, _ = data.choose_groups([row(), row(IsGroupOf='1')],
            [{'openImagesId': '/m/a'}], 1, 5)
        self.assertEqual(len(selected[0]['boxes']), 1)
        self.assertEqual(selected[0]['verifiedClassIndices'], [])
        self.assertEqual(selected[0]['annotatedPositiveClassIndices'], [0])
        self.assertEqual(selected[0]['nonExhaustiveClassIndices'], [0])

    def test_curl_stream_stops_and_reaps_only_its_own_process_after_prefix(self):
        process = Mock(stdout=io.BytesIO(b'prefix'))
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired('curl', 3), 0]
        with patch.object(data.subprocess, 'Popen', return_value=process) as spawn:
            with data.public_stream('https://example.invalid/public.csv', curl=True) as (stream, owner):
                self.assertEqual(stream.read(3), b'pre')
                self.assertIs(owner, process)
        process.terminate.assert_called_once_with()
        process.kill.assert_called_once_with()
        self.assertEqual(process.wait.call_count, 2)
        command = spawn.call_args.args[0]
        self.assertIsInstance(command, list)
        self.assertIn('--max-time', command)
        self.assertFalse(spawn.call_args.kwargs.get('shell', False))

    def test_unsorted_csv_rejected(self):
        with self.assertRaises(ValueError):
            data.choose_groups([row(image='0000000000000002'), row()],
                [{'openImagesId': '/m/a'}], 2, 10)

    def test_byte_cutoff_drops_partial_last_image_group(self):
        lines = [b'ImageID,LabelName\n', b'0000000000000001,/m/a\n',
                 b'0000000000000001,/m/b\n', b'0000000000000002,/m/a\n',
                 b'0000000000000002,/m/b\n']
        raw = b''.join(lines)
        bounded = data.BoundedCSV(io.BytesIO(raw), len(raw)-5)
        result = list(csv.DictReader(iter(bounded)))
        self.assertEqual([row['ImageID'] for row in result],
            ['0000000000000001', '0000000000000001'])
        self.assertFalse(bounded.complete)
        self.assertLessEqual(bounded.bytes, len(raw))

    def test_complete_small_csv_retains_final_group(self):
        bounded = data.BoundedCSV(io.BytesIO(b'ImageID,LabelName\n0000000000000001,/m/a\n'), 1024)
        self.assertEqual(len(list(csv.DictReader(iter(bounded)))), 1)
        self.assertTrue(bounded.complete)

    def test_no_implicit_download_for_unverified_dataset_license(self):
        result = data.download_image('/unused', 'train', {'imageId': '0000000000000001'}, {}, False)
        self.assertEqual(result['status'], 'excluded')
        self.assertFalse(result['individualLicenseVerified'])
        self.assertEqual(result['rightsStatus'], 'dataset-declaration-only')


if __name__ == '__main__':
    unittest.main()
