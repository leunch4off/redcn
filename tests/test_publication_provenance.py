import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'cli'))
import language_service
import local_server
from publication_provenance import bind_generation, bind_manual, clean_metadata, part_origin, text_digest


DRAFT = {'status': 'review', 'modelAccepted': True, 'fallback': False,
    'generationMode': 'individual-model-prose', 'modelContribution': 'full-publication-prose',
    'textSource': 'local-model', 'title': '新标题', 'body': '新正文', 'tags': '#租房'}


class PublicationProvenanceTests(unittest.TestCase):
    def card(self):
        return {'fields': {'price': '80000', 'rooms': '2'}, 'titles': ['原标题'],
            'body': '原来的正文', 'tags': '#莫斯科租房'}

    def test_historical_title_metadata_cannot_certify_the_body(self):
        card = self.card()
        card['aiAdvertisement'] = {**DRAFT, 'part': 'title'}
        self.assertEqual(part_origin(card, 'title'), 'legacy-model')
        self.assertEqual(part_origin(card, 'body'), 'unknown')

    def test_separate_rewrites_keep_the_unchanged_part_attribution(self):
        card = bind_generation(self.card(), {**DRAFT, 'part': 'all'})
        changed = {**copy.deepcopy(card), 'titles': ['另一个标题']}
        result = bind_generation(changed, {**DRAFT, 'part': 'title'}, previous=card)
        self.assertEqual(result['body'], card['body'])
        self.assertEqual(result['aiAdvertisement']['modelContribution'], 'title-prose')
        self.assertEqual(part_origin(result, 'title'), 'individual')
        self.assertEqual(part_origin(result, 'body'), 'individual')
        self.assertEqual(part_origin(result, 'tags'), 'rules')

    def test_editing_a_model_body_invalidates_its_model_attribution(self):
        card = bind_generation(self.card(), {**DRAFT, 'part': 'all'})
        card['body'] += '\n人工更正'
        self.assertEqual(part_origin(card, 'body'), 'manual')
        self.assertEqual(part_origin(card, 'title'), 'individual')
        manual = bind_manual(card, 'tags', previous=card)
        self.assertEqual(part_origin(manual, 'tags'), 'manual')

    def test_editorial_review_is_not_reported_as_native_model_generation(self):
        result = bind_generation(self.card(), {'part': 'body',
            'generationMode': 'assistant-editorial', 'textSource': 'assistant-reviewed',
            'modelAccepted': False, 'modelContribution': 'none'})
        self.assertEqual(part_origin(result, 'body'), 'assistant-reviewed')
        self.assertEqual(part_origin(result, 'title'), 'unknown')
        result['body'] += ' 更正'
        self.assertEqual(part_origin(result, 'body'), 'manual')

    def test_nested_metadata_does_not_copy_private_drafts(self):
        result = clean_metadata({'parts': {'body': {**DRAFT, 'rawText': 'private',
            'messages': ['private'], 'textDigest': text_digest('新正文')}}})
        row = result['parts']['body']
        for key in ('rawText', 'messages', 'title', 'body', 'tags'):
            self.assertNotIn(key, row)

    def test_real_title_rewrite_preserves_old_body_and_its_manual_marker(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            original = self.card()
            original['templateInfo'] = {'descriptionId': 'description-old', 'manualParts': ['body']}
            card_id = local_server.save_result(directory, original)
            with patch('individual_copy.generate_preview', return_value={**DRAFT, 'part': 'title'}):
                language_service.rewrite(directory, card_id, {'mode': 'individual', 'part': 'title'})
            saved = local_server.read_result(directory, card_id)
            self.assertEqual(saved['body'], original['body'])
            self.assertEqual(saved['templateInfo']['manualParts'], ['body'])
            self.assertEqual(saved['templateInfo']['modelContribution'], 'title-prose')
            self.assertEqual(part_origin(saved, 'title'), 'individual')
            self.assertEqual(part_origin(saved, 'body'), 'manual')
            language_service.edit_text(directory, card_id, {'part': 'title', 'text': '手工标题'})
            self.assertEqual(part_origin(local_server.read_result(directory, card_id), 'title'), 'manual')


if __name__ == '__main__':
    unittest.main()
