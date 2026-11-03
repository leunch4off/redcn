import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'cli'))
import learning_discovery as discovery
from learning_memory import LearningMemory


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def card(self, text):
        return {'fields': {'sourceDescription': text}}

    def test_import_is_deduplicated_and_never_approved_training(self):
        card = self.card('Кухня оборудована духовкой. Окна выходят во двор.')
        one = discovery.capture_import(self.root, card, 'card-one')
        same = discovery.capture_import(self.root, card, 'card-one')
        other = discovery.capture_import(self.root, card, 'card-two')
        self.assertEqual(one['newPhrases'], 2)
        self.assertEqual(same['status'], 'already-captured')
        self.assertEqual(same['newPhrases'], 0)
        self.assertEqual(other['duplicatePhrases'], 2)
        summary = discovery.status(self.root)
        self.assertEqual(summary['uniqueSourcePhrases'], 2)
        self.assertEqual(summary['indexedCards'], 2)
        self.assertEqual(LearningMemory(self.root).status()['approvedExamples'], 0)

    def test_changes_keep_provenance_and_known_exact_translation_is_separate(self):
        LearningMemory(self.root).record('translation', {'ru': 'Окна во двор.', 'zh': '窗户朝向院内。'}, status='approved')
        first = discovery.capture_import(self.root, self.card('Окна во двор.'), 'card')
        later = discovery.capture_import(self.root, self.card('Окна во двор. Есть кладовая.'), 'card')
        self.assertEqual(first['knownPhrases'], 1)
        self.assertEqual(later['newPhrases'], 1)
        self.assertNotEqual(first['sourceSha256'], later['sourceSha256'])

    def test_contact_redaction_and_decimal_preservation(self):
        discovery.capture_import(self.root, self.card('Площадь 38.5 м². Телефон: +79991234567. Сдаётся квартира.'), 'card')
        texts = [row['text'] for row in discovery.status(self.root)['candidates']]
        self.assertIn('Площадь 38.5 м².', texts)
        self.assertNotIn('79991234567', json.dumps(texts))

    def test_read_only_sweep_catches_saved_cards_once(self):
        path = self.root/'listings.sqlite3'
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE results(id TEXT PRIMARY KEY, updated REAL, payload TEXT)')
            for i in range(4):
                db.execute('INSERT INTO results VALUES(?,?,?)', (str(i), i, json.dumps(self.card('Квартира номер '+str(i)+'.'))))
        original = path.read_bytes()
        self.assertEqual(discovery.sweep_saved_cards(self.root, limit=2)['indexed'], 2)
        self.assertEqual(discovery.sweep_saved_cards(self.root, limit=2)['indexed'], 2)
        self.assertEqual(discovery.sweep_saved_cards(self.root, limit=2)['indexed'], 0)
        self.assertEqual(path.read_bytes(), original)

    def test_review_explicitly_adds_one_conservative_group(self):
        discovery.capture_import(self.root, self.card('Окна выходят во двор. Комнаты изолированы.'), 'card')
        for row in discovery.status(self.root)['candidates']:
            result = discovery.review_phrase(self.root, row['id'], '经人工确认的中文译文。')
            self.assertEqual(result['event']['status'], 'approved')
        summary = LearningMemory(self.root).status()
        self.assertEqual(summary['approvedExamples'], 2)
        self.assertEqual(summary['approvedGroups'], 1)
        self.assertFalse(summary['trainingEligible'])

    def test_secrets_and_invalid_review_are_rejected(self):
        with self.assertRaises(ValueError):
            discovery.capture_import(self.root, self.card('Пароль: secret123. Квартира в аренду.'), 'card')
        with self.assertRaises(ValueError):
            discovery.review_phrase(self.root, 'a'*64, 'unchecked Russian')


if __name__ == '__main__':
    unittest.main()
