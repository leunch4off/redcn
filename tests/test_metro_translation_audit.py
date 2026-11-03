import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('metro_translation_audit', ROOT / 'scripts/audit-metro-translations.py')
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


class MetroTranslationAuditTest(unittest.TestCase):
    def test_real_catalogue_unresolved_inventory_preserves_source_and_line_scope(self):
        path = ROOT / 'data/moscow-geo.json'
        before = path.read_bytes()
        catalogue = json.loads(before)
        original = json.dumps(catalogue, ensure_ascii=False, sort_keys=True)
        result = audit.audit(catalogue)
        self.assertEqual(result['missingMcdRows'], 81)
        self.assertEqual(result['missingByLine'], {'МЦД-1': 0, 'МЦД-2': 25, 'МЦД-3': 31, 'МЦД-4': 25})
        self.assertFalse(result['currentOfficialNetworkCompletenessVerified'])
        self.assertEqual(result['unmatchedPublishedFacts'][0]['lineIds'], ['131'])
        self.assertEqual(result['unmatchedPublishedFacts'][0]['ru'], 'Петровско-Разумовская')
        self.assertEqual(json.dumps(catalogue, ensure_ascii=False, sort_keys=True), original)
        self.assertEqual(path.read_bytes(), before)

    def test_missing_line_schema_does_not_produce_false_complete_report(self):
        with self.assertRaisesRegex(ValueError, 'No MCD lines'):
            audit.audit({'lines': [], 'records': []})


if __name__ == '__main__':
    unittest.main()
