from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'cli'))
from photo_quality_dataset import category_truth, human_labels, presence_metrics, target_mids


class HumanLabelDatasetTests(unittest.TestCase):
    def test_missing_and_machine_labels_are_never_negative_truth(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'labels.csv'
            path.write_text('ImageID,Source,LabelName,Confidence\n'
                            'aaaaaaaaaaaaaaaa,machine,/m/chair,1\n'
                            'bbbbbbbbbbbbbbbb,verification,/m/chair,0\n'
                            'cccccccccccccccc,crowdsource-verification,/m/chair,1\n')
            labels, conflicts, ignored = human_labels(path, {'/m/chair'})
            self.assertIsNone(category_truth(labels, 'aaaaaaaaaaaaaaaa', ['/m/chair']))
            self.assertIs(category_truth(labels, 'bbbbbbbbbbbbbbbb', ['/m/chair']), False)
            self.assertIs(category_truth(labels, 'cccccccccccccccc', ['/m/chair']), True)
            self.assertEqual((len(conflicts), ignored), (0, 1))

    def test_conflicting_human_labels_are_removed_not_resolved_for_the_model(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'labels.csv'
            path.write_text('ImageID,Source,LabelName,Confidence\n'
                            'aaaaaaaaaaaaaaaa,verification,/m/chair,0\n'
                            'aaaaaaaaaaaaaaaa,crowdsource-verification,/m/chair,1\n')
            labels, conflicts, _ = human_labels(path, {'/m/chair'})
            self.assertEqual(labels, {})
            self.assertEqual(len(conflicts), 1)

    def test_multiple_source_classes_need_all_verified_negatives(self):
        labels = {('a', 'computer'): False}
        self.assertIsNone(category_truth(labels, 'a', ['computer', 'personal-computer']))
        labels['a', 'personal-computer'] = False
        self.assertIs(category_truth(labels, 'a', ['computer', 'personal-computer']), False)
        labels['a', 'computer'] = True
        self.assertIs(category_truth(labels, 'a', ['computer', 'personal-computer']), True)

    def test_presence_fp_fn_and_unknown_denominators_are_separate(self):
        rows = [
            {'truth': {'chairs': True, 'hood': True}, 'predictions': [{'classId': 'chairs', 'score': .8}]},
            {'truth': {'chairs': False}, 'predictions': [{'classId': 'chairs', 'score': .6}]},
            {'truth': {'chairs': None}, 'predictions': [{'classId': 'chairs', 'score': .9}]},
        ]
        result = presence_metrics(rows, {'chairs': (), 'hood': ()}, ['chairs'])
        self.assertEqual((result['chairs']['tp'], result['chairs']['fp'], result['chairs']['unknownImages']), (1, 1, 1))
        self.assertEqual(result['hood']['fn'], 1)
        self.assertFalse(result['hood']['supportedByActiveVocabulary'])
        self.assertEqual(result['hood']['evaluationMode'], 'unrepresentable-output-category')
        self.assertIsNone(result['hood']['precision'])

    def test_generic_clothing_hood_is_not_mapped_to_a_kitchen_extractor(self):
        mapping = target_mids([], {'Hood': '/clothing', 'Range hood': '/extractor', 'Computer': '/computer'})
        self.assertEqual(mapping['hood'], ('/extractor',))
        self.assertNotIn('/clothing', str(mapping))

    def test_unknown_model_class_cannot_claim_a_success_for_unsupported_category(self):
        with self.assertRaises(ValueError):
            presence_metrics([{'truth': {'hood': True}, 'predictions': [{'classId': 'hood', 'score': .9}]}], {'hood': ()}, ['chairs'])


if __name__ == '__main__':
    unittest.main()
