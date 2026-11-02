import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'cli'))
from learning_memory import LearningMemory
import learning_service


class LearningConsentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cache = Path(self.temp.name)
        self.memory = LearningMemory(self.cache)

    def event(self, text='配有洗衣机', **kwargs):
        return self.memory.record('description', {'text':text, 'language':'zh'},
                                  status='approved', actor='editor-A', **kwargs)

    def dataset(self, name='receipt-snapshot'):
        snapshot = self.memory.snapshot(name)
        value = {'version':1, 'splits':{
            'train':[json.loads(x) for x in Path(snapshot['train']).read_text().splitlines()],
            'validation':[json.loads(x) for x in Path(snapshot['valid']).read_text().splitlines()]},
            'provenance':{'kind':'approved-local-memory', 'snapshot':snapshot['manifest']}}
        path = self.cache/(name+'.json')
        path.write_text(json.dumps(value, ensure_ascii=False))
        return path

    def test_withdrawal_cascades_and_marks_already_trained_weights_without_erasing_them(self):
        source = self.event(group_id='flat-a')
        derived = self.event('洗衣机已配置', group_id='flat-a', derived_from=[source['id']])
        dataset = self.dataset()
        weights = self.cache/'existing-weights.bin';weights.write_bytes(b'existing learned influence')
        digest = hashlib.sha256(weights.read_bytes()).hexdigest()
        self.memory.register_candidate(digest, dataset)
        result = self.memory.withdraw(source['id'], actor='editor-B', reason='Отозвано разрешение')
        self.assertEqual(result['affectedExamples'], 2)
        self.assertEqual(result['affectedCandidateHashes'], [digest])
        self.assertTrue(result['retrainingRequired'])
        self.assertFalse(result['existingWeightInfluenceRemoved'])
        self.assertEqual(hashlib.sha256(weights.read_bytes()).hexdigest(), digest)
        self.assertTrue(self.memory.candidate_consent(digest)['retrainingRequired'])
        self.assertEqual({r['id'] for r in self.memory.list_events('withdrawn')}, {source['id'], derived['id']})
        self.assertEqual(self.memory.retrieve('洗衣机'), [])
        with self.assertRaises(ValueError):self.memory.assert_dataset_consent(dataset)
        fresh = self.memory.snapshot('after-withdrawal')
        self.assertEqual(fresh['manifest']['trainExamples']+fresh['manifest']['validExamples'], 0)

    def test_reapproval_exact_copy_and_derivative_cannot_silently_restore_consent(self):
        source = self.event()
        self.memory.withdraw(source['id'])
        with self.assertRaises(ValueError):self.memory.review(source['id'], 'approved')
        with self.assertRaises(ValueError):self.event(group_id='new-group')
        with self.assertRaises(ValueError):self.event('有新措辞', derived_from=[source['id']])
        with self.assertRaises(ValueError):self.event('修正后的内容', supersedes=source['id'])
        self.assertEqual(self.memory.status()['approvedExamples'], 0)

    def test_original_review_actor_snapshot_and_dataset_are_visible(self):
        source = self.memory.record('translation', {'ru':'Стиральная машина', 'zh':'洗衣机'}, actor='editor-A')
        self.memory.review(source['id'], 'approved', actor='editor-B')
        dataset = self.dataset();digest = self.memory.register_dataset(dataset)
        provenance = self.memory.provenance(source['id'])
        self.assertEqual([r['actor'] for r in provenance['reviewHistory']], ['editor-A','editor-B'])
        self.assertEqual(provenance['snapshots'][0]['snapshotId'], 'receipt-snapshot')
        self.assertEqual(provenance['datasetHashes'], [digest])
        self.assertFalse(provenance['historicalActorUnknown'])

    def test_identical_payload_and_legacy_supersedes_are_withdrawn_together(self):
        first = self.event(group_id='one')
        duplicate = self.event(group_id='two')
        replacement = self.event('新描述', supersedes=first['id'])
        with self.memory._db() as db:db.execute('DELETE FROM learning_edges')
        result = self.memory.withdraw(first['id'])
        self.assertEqual(result['affectedExamples'], 3)
        self.assertEqual({r['id'] for r in self.memory.list_events('withdrawn')},
                         {first['id'], duplicate['id'], replacement['id']})

    def test_tampered_messages_under_approved_id_fail_consent_binding(self):
        self.event();path = self.dataset();value = json.loads(path.read_text())
        next(row for part in value['splits'].values() for row in part)['messages'][-1]['content'] = '免费登记'
        path.write_text(json.dumps(value))
        with self.assertRaises(ValueError):self.memory.register_dataset(path)

    def test_renaming_envelope_and_row_does_not_restore_withdrawn_payload(self):
        source=self.event();path=self.dataset();value=json.loads(path.read_text())
        self.memory.withdraw(source['id'])
        row=next(row for part in value['splits'].values() for row in part)
        value['provenance']={'kind':'project-authored'};row['id']='renamed-corpus-row'
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError,'отозван'):self.memory.assert_dataset_consent(path)
        row.pop('provenance');row['status']='project-authored'
        row['messages'][-1]['content']='  '+row['messages'][-1]['content']+'\n'
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError,'отозван'):self.memory.assert_dataset_consent(path)

    def test_unregistered_operator_row_cannot_be_laundered_through_authored_envelope(self):
        path=self.cache/'unknown-reviewed.json'
        value={'version':1,'provenance':{'kind':'project-authored'},'splits':{'train':[
            {'id':'unknown-operator-row','provenance':{'kind':'operator-reviewed'}}],'validation':[]}}
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError,'отсутствует'):self.memory.assert_dataset_consent(path)

    def test_unknown_local_ids_fail_but_authored_corpus_is_not_mislabelled_operator_data(self):
        path = self.cache/'authored.json'
        value = {'version':1, 'splits':{'train':[{'id':'fictional-row-1'}], 'validation':[]},
                 'provenance':{'kind':'project-authored'}}
        path.write_text(json.dumps(value));self.memory.assert_dataset_consent(path)
        value['provenance']['kind'] = 'approved-local-memory';path.write_text(json.dumps(value))
        with self.assertRaises(ValueError):self.memory.assert_dataset_consent(path)

    def test_withdrawal_during_snapshot_preparation_prevents_publishing_old_rows(self):
        source = self.event()
        original = tempfile.mkdtemp
        def withdraw_after_read(*args, **kwargs):
            value = original(*args, **kwargs)
            self.memory.withdraw(source['id'])
            return value
        with patch('learning_memory.tempfile.mkdtemp', side_effect=withdraw_after_read):
            with self.assertRaises(ValueError):self.memory.snapshot('race')
        self.assertFalse((self.cache/'learning/snapshots/race').exists())
        self.assertEqual(list((self.cache/'learning/snapshots').iterdir()), [])

    def test_api_uses_authenticated_actor_and_requests_running_training_to_stop(self):
        row = learning_service.dispatch(self.cache, 'learning', {'action':'record', 'kind':'description',
            'payload':{'text':'房源说明', 'language':'zh'}, 'status':'approved'}, actor='phone:authorized')[1]['event']
        value = learning_service.dispatch(self.cache, 'learning', {'action':'withdraw','id':row['id']}, actor='Mac owner')[1]
        self.assertTrue(value['withdrawn'])
        self.assertTrue((self.cache/'learning/training.cancel').is_file())
        history = self.memory.provenance(row['id'])
        self.assertEqual(history['reviewHistory'][0]['actor'], 'phone:authorized')
        self.assertEqual(history['withdrawal']['actor'], 'Mac owner')
        with self.assertRaises(ValueError):
            learning_service.dispatch(self.cache, 'learning', {'action':'withdraw','id':row['id'],'actor':'forged'})

    def test_old_event_actor_is_unknown_and_no_current_actor_is_invented(self):
        row = self.event()
        with self.memory._db() as db:db.execute('DELETE FROM learning_audit')
        self.assertTrue(self.memory.provenance(row['id'])['historicalActorUnknown'])
        self.assertEqual(self.memory.provenance(row['id'])['reviewHistory'], [])

    def test_revoked_generated_history_is_not_reused_in_prompts(self):
        row = self.memory.record('title', {'text':'示例标题', 'language':'zh'}, source='generated', group_id='flat')
        self.memory.withdraw(row['id'])
        self.assertEqual(self.memory.recent_generated('flat', kind='title'), [])


if __name__ == '__main__':unittest.main()
