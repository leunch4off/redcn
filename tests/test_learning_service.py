import json
import hashlib
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'cli'))
import learning_service


class LearningServiceTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name)

    def request(self, data=None):
        code, result, mime, headers = learning_service.dispatch(self.path, 'learning', data)
        self.assertEqual(code, 200)
        self.assertEqual(mime, 'application/json; charset=utf-8')
        json.dumps(result)
        return result

    def test_unrelated_route_does_not_create_memory(self):
        self.assertIsNone(learning_service.dispatch(self.path, 'other'))
        self.assertFalse((self.path/'learning').exists())

    def test_pending_edit_can_be_reviewed_and_retrieved(self):
        event = self.request({'action': 'record', 'kind': 'dictionary',
            'payload': {'ru': 'рабочий стол', 'zh': '书桌'}, 'groupId': 'one'})['event']
        self.assertEqual(event['status'], 'pending')
        self.assertEqual(self.request()['status']['counts']['pending'], 1)
        self.request({'action': 'review', 'id': event['id'], 'status': 'approved'})
        found = self.request({'action': 'retrieve', 'query': 'рабочий стол'})
        self.assertEqual(found['matches'][0]['payload']['zh'], '书桌')
        self.assertEqual(len(self.request({'action': 'list', 'status': 'approved'})['events']), 1)

    def test_generated_example_cannot_bypass_review(self):
        event = self.request({'action': 'record', 'kind': 'title', 'source': 'generated',
            'status': 'approved', 'payload': {'text': '莫斯科租房', 'language': 'zh'}})['event']
        self.assertEqual(event['status'], 'pending')

    def test_snapshot_response_does_not_expose_dataset_paths(self):
        self.request({'action': 'record', 'kind': 'translation', 'status': 'approved',
            'payload': {'ru': 'есть холодильник', 'zh': '配有冰箱'}})
        result = self.request({'action': 'snapshot'})
        self.assertEqual(set(result), {'snapshot', 'status'})
        self.assertNotIn(str(self.path), json.dumps(result))

    def test_previews_use_content_service_without_changing_memory(self):
        fake = SimpleNamespace(generate_preview=lambda directory, data: {'status': 'review', 'title': '好房'},
            translate_preview=lambda directory, data: {'status': 'review', 'text': '书桌'})
        with patch.dict(sys.modules, {'local_content': fake}):
            self.assertEqual(self.request({'action': 'generate-preview', 'fields': {}})['title'], '好房')
            self.assertEqual(self.request({'action': 'translate-preview', 'text': 'стол', 'direction': 'ru-zh'})['text'], '书桌')
        self.assertEqual(self.request()['status']['counts']['approved'], 0)

    def test_invalid_action_and_payload_are_rejected(self):
        for data in ({'action': 'execute'}, {'action': 'record', 'kind': 'dictionary', 'payload': {'ru': 'стол'}},
                {'action': 'record', 'kind': 'tag_stat', 'payload': {'tag': '莫斯科', 'views': -1}}):
            with self.assertRaises(ValueError):
                self.request(data)

    def test_scheduler_change_requires_boolean_and_returns_only_public_state(self):
        changes = []
        fake = SimpleNamespace(status=lambda directory: {'enabled': True, 'automaticPromotion': False},
            save_config=lambda directory, value: changes.append(value))
        with patch.dict(sys.modules, {'learning_scheduler': fake}):
            result = self.request({'action': 'scheduler-config', 'enabled': True})
            self.assertEqual(changes, [{'enabled': True}])
            self.assertFalse(result['scheduler']['automaticPromotion'])
            with self.assertRaises(ValueError):
                self.request({'action': 'scheduler-config', 'enabled': 'true'})

    def test_auto_promotion_toggle_is_strict_and_does_not_accept_a_candidate(self):
        value = self.request({'action': 'scheduler-config', 'automaticPromotion': True})
        self.assertTrue(value['scheduler']['automaticPromotion'])
        self.assertEqual(value['scheduler']['promotion']['state'], 'not-evaluated')
        self.assertFalse((self.path/'text-ai.json').exists())
        for payload in ({'automaticPromotion': 1}, {'automaticPromotion': 'true'},
                        {'automaticPromotion': True, 'report': '/private/report.json'}, {}):
            with self.assertRaises(ValueError):
                self.request({'action': 'scheduler-config', **payload})

    def test_rollback_rejects_caller_supplied_paths(self):
        with self.assertRaises(ValueError):
            self.request({'action': 'rollback-model', 'path': '/different/weights'})

    def test_trained_parameter_status_requires_completed_receipt_and_matching_weights(self):
        import local_text_model
        from text_model_contract import EXPECTED_TRAINABLE
        folder = self.path/'tools/text-ai/runs/first'
        folder.mkdir(parents=True)
        weights = folder/'adapters.safetensors'
        weights.write_bytes(b'fixture adapter')
        receipt = {'status': 'training', 'trainableParameters': EXPECTED_TRAINABLE,
            'stepsCompleted': 2, 'adapterSha256': hashlib.sha256(weights.read_bytes()).hexdigest()}
        receipt_path = folder/'trainreceipt.json'
        receipt_path.write_text(json.dumps(receipt))
        config = {'modelPath': str(self.path/'model'), 'pythonPath': str(self.path/'runtime'),
            'enabled': False, 'adapterPath': None}
        with patch.object(local_text_model, 'settings', return_value=config):
            self.assertIsNone(learning_service._text_ai(self.path, {})['trainedParameters'])
            receipt['status'] = 'trained-unaccepted'
            receipt_path.write_text(json.dumps(receipt))
            value = learning_service._text_ai(self.path, {})
            self.assertEqual(value['trainedParameters'], EXPECTED_TRAINABLE)
            self.assertEqual(value['stepsCompleted'], 2)
            self.assertNotIn(str(self.path), json.dumps(value))
            weights.write_bytes(b'changed artifact')
            self.assertIsNone(learning_service._text_ai(self.path, {})['trainedParameters'])


if __name__ == '__main__':
    unittest.main()
