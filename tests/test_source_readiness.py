import unittest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'cli'))
from source_readiness import source_readiness


class SourceReadinessTests(unittest.TestCase):
    def test_old_zero_read_false_is_unobserved_not_missing(self):
        result = source_readiness({'capture': {'sections': [
            {'section': 'gallery', 'reads': 2, 'found': True, 'ready': True},
            {'section': 'description', 'reads': 0, 'found': False, 'ready': False},
            {'section': 'characteristics', 'reads': 1, 'found': False, 'ready': False}]}})
        self.assertEqual([r['reason'] for r in result['sections']], ['ready', 'not_visited_budget', 'not_found'])
        self.assertIsNone(result['sections'][1]['found'])
        self.assertTrue(result['partial'])

    def test_completed_sections_keep_only_allowlisted_status_and_measurements(self):
        capture = {'sections': [{'section': name, 'reads': 1, 'ready': True, 'found': True,
                                 'reason': 'ready', 'stabilityKey': 'private text'}
                                for name in ('gallery', 'description', 'characteristics')],
                   'seconds': 2.5, 'budgetSeconds': 6, 'budgetOverrunSeconds': float('nan'),
                   'source': 'private text', 'browserCalls': [{'rawHtml': 'private'}]}
        result = source_readiness({'capture': capture})
        self.assertEqual(result['state'], 'ready'); self.assertFalse(result['partial'])
        self.assertEqual(result['seconds'], 2.5); self.assertNotIn('budgetOverrunSeconds', result)
        self.assertNotIn('private', str(result))
        self.assertEqual(source_readiness({'sourceReadiness': result}), result)

    def test_missing_capture_is_unknown_and_does_not_claim_verification(self):
        result = source_readiness({})
        self.assertEqual(result['state'], 'unknown'); self.assertFalse(result['completed'])

    def test_ready_probes_cannot_override_explicitly_incomplete_full_capture(self):
        rows=[{'section':name,'reads':1,'found':True,'ready':True,'reason':'ready'}
              for name in ('gallery','description','characteristics')]
        result=source_readiness({'capture':{'sections':rows,'completed':False}})
        self.assertEqual(result['state'],'partial');self.assertFalse(result['completed'])
        self.assertTrue(result['warnings'])


if __name__ == '__main__':
    unittest.main()
