"""Exercise the actual batch importer against disposable cards and originals."""
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'cli'))
from photo_teacher_review import make_review

spec = importlib.util.spec_from_file_location('apply_assistant_reviews', ROOT / 'scripts/apply-assistant-photo-reviews.py')
importer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(importer)


class BatchReviewImportTests(unittest.TestCase):
    def test_backup_text_and_library_order_survive_real_apply(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            (cache / 'media').mkdir()
            content = b'explicit synthetic original bytes'
            digest = hashlib.sha256(content).hexdigest()
            (cache / 'media' / digest).write_bytes(content)
            url = 'https://images.cdn-cian.ru/images/synthetic-1.jpg'
            card = {'fields': {'features': ['tv'], 'photos': [url], 'featureCounts': {'tv': 1}},
                    'body': 'Operator wording', 'titles': ['Operator title'], 'tags': '#manual',
                    'media': [{'id': digest, 'kind': 'original', 'sourceUrl': url}]}
            before = json.dumps(card)
            with sqlite3.connect(cache / 'listings.sqlite3') as db:
                db.execute('CREATE TABLE results(id TEXT PRIMARY KEY, updated REAL, payload TEXT)')
                db.executemany('INSERT INTO results VALUES(?,?,?)', [('1'*32, 10., before), ('2'*32, 20., before)])
            review = make_review(digest, ['washer'], dataset_id='a'*64, reviewed_at='2026-11-03T00:00:00Z')
            dataset = cache / 'reviews.json'
            dataset.write_text(json.dumps({'provenance': 'assistant-visual-review', 'humanVerified': False,
                'exhaustive': False, 'cards': [{'cardId': '1'*32, 'photos': [{'sourceUrl': url, 'review': review}]}]}))
            receipt = cache / 'applied.json'
            with patch.object(sys, 'argv', ['apply', '--cache', str(cache), '--dataset', str(dataset),
                                           '--receipt', str(receipt), '--apply']), contextlib.redirect_stdout(io.StringIO()):
                importer.main()
            with sqlite3.connect(cache / 'listings.sqlite3') as db:
                rows = db.execute('SELECT id,updated,payload FROM results ORDER BY updated DESC').fetchall()
            self.assertEqual([(r[0], r[1]) for r in rows], [('2'*32, 20.), ('1'*32, 10.)])
            saved = json.loads(rows[1][2])
            self.assertIn('washer', saved['fields']['features'])
            self.assertEqual(saved['fields']['featureCounts'], {'tv': 1})
            for key in ('body', 'titles', 'tags'):
                self.assertEqual(saved[key], card[key])
            result = json.loads(receipt.read_text())
            self.assertTrue(result['sourceOrderPreserved'])
            self.assertFalse(result['weightsChanged'])
            with sqlite3.connect(result['backup']) as db:
                self.assertEqual(db.execute('SELECT payload FROM results WHERE id=?', ('1'*32,)).fetchone()[0], before)
            self.assertEqual(saved['fields']['photoEvidence'][0]['status'], 'assistant-reviewed')
            self.assertFalse(saved['fields']['photoEvidence'][0]['humanVerified'])


if __name__ == '__main__':
    unittest.main()
