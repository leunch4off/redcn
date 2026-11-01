import hashlib
import json
from pathlib import Path
import sqlite3
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'cli'))
from learning_memory import LearningMemory


class LearningMemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.memory = LearningMemory(self.directory)

    def tearDown(self):
        self.temp.cleanup()

    def test_persistent_review_and_generated_text_cannot_approve_itself(self):
        row = self.memory.record('title', {'text':'阳光明亮的一居室', 'language':'zh'},
                                 source='generated', status='approved', group_id='flat-1')
        self.assertEqual(row['status'], 'pending')
        self.assertEqual(self.memory.retrieve('阳光'), [])
        self.memory.review(row['id'], 'approved')
        other = LearningMemory(self.directory)
        self.assertEqual(other.retrieve('阳光')[0]['id'], row['id'])
        self.assertEqual(other.status()['counts']['approved'], 1)
        self.assertFalse(other.status()['weightsUpdated'])
        self.assertFalse(other.status()['automaticPromotion'])

    def test_correction_supersedes_in_one_transaction_and_preserves_audit(self):
        first = self.memory.record('dictionary', {'ru':'варочная панель','zh':'烤箱'},
                                   status='approved', group_id='vocabulary')
        second = self.memory.record('dictionary', {'ru':'варочная панель','zh':'灶台'},
                                    status='approved', supersedes=first['id'])
        self.assertEqual(second['groupId'], first['groupId'])
        self.assertEqual(self.memory.status()['counts']['superseded'], 1)
        self.assertEqual(self.memory.retrieve('варочная')[0]['payload']['zh'], '灶台')
        with self.assertRaises(ValueError): self.memory.review(first['id'], 'approved')
        with self.assertRaises(ValueError):
            self.memory.record('dictionary', {'ru':'варочная панель','zh':'电磁炉'},
                               status='approved', supersedes=first['id'])
        with sqlite3.connect(self.memory.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM review_history').fetchone()[0], 3)

    def test_invalid_correction_keeps_previous_approved(self):
        first = self.memory.record('translation', {'ru':'кровать','zh':'床'}, status='approved', group_id='a')
        with self.assertRaises(ValueError):
            self.memory.record('translation', {'ru':'кровать','zh':'大床'}, status='approved',
                               group_id='different', supersedes=first['id'])
        self.assertEqual(self.memory.status()['counts']['approved'], 1)

    def test_accepted_pair_correction_automatically_retires_conflicts_in_same_group(self):
        old = self.memory.record('dictionary', {'ru':'  Варочная   панель ', 'zh':'烤箱'}, status='approved', group_id='vocabulary')
        unrelated = self.memory.record('translation', {'ru':'Варочная панель', 'zh':'炉灶'}, status='approved', group_id='different')
        new = self.memory.record('translation', {'ru':'варочная панель', 'zh':'灶台'}, status='approved', group_id='vocabulary')
        self.assertEqual(self.memory.status()['counts']['superseded'], 1)
        accepted = {row['id'] for row in self.memory.list_events('approved')}
        self.assertEqual(accepted, {new['id'],unrelated['id']})
        with sqlite3.connect(self.memory.path) as db:
            history = db.execute('SELECT previous,current FROM review_history WHERE event_id=? ORDER BY sequence', (old['id'],)).fetchall()
        self.assertEqual(history, [(None,'approved'),('approved','superseded')])

    def test_pending_pair_does_not_override_until_explicit_review(self):
        pending = self.memory.record('translation', {'ru':'духовой шкаф','zh':'烤箱'}, source='generated', group_id='dictionary')
        accepted = self.memory.record('dictionary', {'ru':'Духовой шкаф','zh':'烤炉'}, status='approved', group_id='dictionary')
        self.assertEqual(self.memory.approved_glossary('Духовой шкаф')[0]['zh'], '烤炉')
        self.memory.review(pending['id'], 'approved')
        self.assertEqual(self.memory.approved_glossary('Духовой шкаф')[0]['zh'], '烤箱')
        self.assertEqual(self.memory.list_events('superseded')[0]['id'], accepted['id'])

    def test_explicit_pending_revision_retains_accepted_data_and_checks_stale_conflict(self):
        old = self.memory.record('description', {'text':'Светлая квартира','language':'ru'}, status='approved')
        pending = self.memory.record('description', {'text':'Светлая квартира с отдельной спальней','language':'ru'},
                                     source='generated', status='approved', supersedes=old['id'])
        self.assertEqual(self.memory.status()['counts']['approved'],1)
        self.assertEqual(self.memory.status()['counts']['superseded'],0)
        self.memory.review(pending['id'], 'approved')
        self.assertEqual(self.memory.status()['counts']['superseded'],1)

    def test_approved_glossary_phrase_boundaries_reverse_and_latest_acceptance(self):
        self.memory.record('dictionary', {'ru':'стол','zh':'桌子'}, status='approved', group_id='a')
        self.memory.record('dictionary', {'ru':'рабочий стол','zh':'书桌'}, status='approved', group_id='a')
        self.memory.record('dictionary', {'ru':'тёмный','zh':'深色'}, status='approved', group_id='a')
        pending = self.memory.record('dictionary', {'ru':'стол','zh':'桌'}, group_id='b')
        self.memory.record('dictionary', {'ru':'СТОЛ','zh':'桌台'}, status='approved', group_id='c')
        self.memory.review(pending['id'], 'approved')
        matches = self.memory.approved_glossary('Темный рабочий стол, столешница и столик')
        self.assertEqual([row['ru'] for row in matches], ['рабочий стол','тёмный','стол'])
        self.assertEqual(matches[-1]['zh'],'桌')
        self.assertEqual(self.memory.approved_glossary('столик столешница'),[])
        reverse = self.memory.approved_glossary('配有书桌和桌子',reverse=True)
        self.assertEqual({row['ru'] for row in reverse}, {'рабочий стол','стол'})
        self.assertEqual(len(self.memory.approved_glossary('рабочий стол', limit=1)),1)
        with self.assertRaises(ValueError): self.memory.approved_glossary('стол',reverse=1)

    def test_only_unchecked_furniture_is_unknown_and_never_a_claim_of_absence(self):
        event = self.memory.record('furniture', {'items':[], 'excluded':['fridge','fridge','sofa']}, status='approved')
        self.assertEqual(event['payload'], {'items':[], 'excluded':['fridge','sofa']})
        self.assertEqual(self.memory.retrieve('fridge')[0]['id'],event['id'])
        snapshot = self.memory.snapshot('unchecked-furniture')
        exported = [json.loads(line) for name in ('train','valid') for line in Path(snapshot[name]).read_text().splitlines()]
        message = exported[0]['messages'][-1]['content']
        self.assertEqual(json.loads(message), {'items':[], 'excluded':['fridge','sofa'], 'excludedMeaning':'unconfirmed-not-absence'})
        self.assertIn('не доказывает отсутствие',exported[0]['messages'][0]['content'])
        with self.assertRaises(ValueError): self.memory.record('furniture', {'items':['desk'],'excluded':['desk']})
        with self.assertRaises(ValueError): self.memory.record('furniture', {'items':[],'excluded':[]})

    def test_record_is_idempotent_but_cannot_replace_other_content(self):
        values = {'text':'一居室', 'language':'zh'}
        first = self.memory.record('title', values, event_id='stable-event-1', status='approved')
        second = self.memory.record('title', values, event_id='stable-event-1', status='approved')
        self.assertEqual(first, second)
        self.assertEqual(self.memory.status()['counts']['approved'], 1)
        with self.assertRaises(ValueError):
            self.memory.record('title', {'text':'两居室', 'language':'zh'}, event_id='stable-event-1')

    def test_secrets_html_and_extra_private_fields_rejected_contacts_removed(self):
        for text in ['password: secret', 'пароль=private', '密码：secret', 'паспорт номер: 1234567890', '<html>source</html>',
                     'authorization: bearer abc', 'sk-abcdefghijklmnopqrstuvwxyz']:
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.memory.record('description', {'text':text, 'language':'ru'})
        with self.assertRaises(ValueError):
            self.memory.record('translation', {'ru':'стол','zh':'桌子','cookie':'private'})
        result = self.memory.record('description', {'text':'Кровать, 80000 ₽. user@example.com +7 (999) 123-45-67 https://cian.ru/x', 'language':'ru'},
                                    group_id='https://cian.ru/rent/flat/private-url')
        encoded = json.dumps(result, ensure_ascii=False)
        self.assertNotIn('user@example.com', encoded)
        self.assertNotIn('999', encoded)
        self.assertNotIn('cian.ru', encoded)
        self.assertIn('80000', encoded)
        self.assertEqual(len(result['groupId']), 64)

    def test_private_permissions_and_symlinks_rejected(self):
        self.assertEqual(stat.S_IMODE(self.memory.path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.memory.directory.stat().st_mode), 0o700)
        self.memory.path.rename(self.memory.directory/'actual.sqlite3')
        self.memory.path.symlink_to(self.memory.directory/'actual.sqlite3')
        with self.assertRaises(ValueError): self.memory.status()

    def test_validated_manual_furniture_has_no_invented_features(self):
        row = self.memory.record('furniture', {'items':['fridge','desk','fridge']}, status='approved')
        self.assertEqual(row['payload']['items'], ['fridge','desk'])
        self.assertEqual(self.memory.retrieve('desk')[0]['id'], row['id'])
        for payload in ({'items':[]}, {'items':[None]}, {'items':['desk'], 'confidence':.99}):
            with self.assertRaises(ValueError): self.memory.record('furniture', payload)

    def test_tag_ranking_uses_only_relevant_approved_observations(self):
        self.memory.record('tag_stat', {'tag':'莫斯科租房','views':2000,'observedAt':'2026-10-31T10:00:00Z'}, status='approved')
        self.memory.record('tag_stat', {'tag':'莫斯科租房','views':1000,'uses':2,'observedAt':'2026-10-30T10:00:00Z'}, status='approved')
        self.memory.record('tag_stat', {'tag':'不相关','views':10_000_000}, status='approved')
        self.memory.record('tag_stat', {'tag':'未知','views':999999}, status='pending')
        ranked = self.memory.rank_tags(['莫斯科租房','#未知'])
        self.assertEqual([row['tag'] for row in ranked], ['#莫斯科租房','#未知'])
        self.assertEqual(ranked[0]['views'], 1500)
        self.assertEqual(ranked[0]['observations'], 2)
        self.assertEqual(ranked[0]['uses'], 3)
        self.assertEqual(ranked[0]['confidence'], 'limited')
        self.assertFalse(ranked[0]['causalEffectKnown'])
        self.assertIsNone(ranked[1]['views'])
        self.assertEqual(ranked[1]['confidence'], 'unmeasured')

    def test_tag_invalid_measurements_and_dates_rejected(self):
        for update in ({'views':True},{'views':-1},{'views':1.5},{'uses':0},
                       {'observedAt':'2050-01-01T00:00:00Z'},{'observedAt':'2026-01-01'}, {'tag':'bad tag'}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                self.memory.record('tag_stat', {'tag':'租房', **update})

    def test_snapshot_group_split_dedup_review_only_and_hash_receipts(self):
        # Find listing groups for both persistent partitions deterministically.
        groups = {'train':[], 'valid':[]}
        for index in range(100):
            group = f'listing-{index}'
            digest = hashlib.sha256(group.encode()).hexdigest()
            part = 'valid' if int(digest[:8],16)/2**32 < .2 else 'train'
            if len(groups[part])<3: groups[part].append(group)
        for part in groups:
            for index, group in enumerate(groups[part]):
                self.memory.record('title', {'text':f'{part}title{index}','language':'zh'}, group_id=group, status='approved')
                self.memory.record('description', {'text':f'{part}description{index}','language':'zh'}, group_id=group, status='approved')
        for group in (groups['train'][0], groups['valid'][0]):
            self.memory.record('translation', {'ru':'Дублируется','zh':'重复'}, status='approved', group_id=group)
        self.memory.record('title', {'text':'PENDING-TEXT','language':'zh'})
        self.memory.record('tag_stat', {'tag':'租房','views':100}, status='approved')
        snapshot = self.memory.snapshot('test-snapshot')
        train = [json.loads(line) for line in Path(snapshot['train']).read_text().splitlines()]
        valid = [json.loads(line) for line in Path(snapshot['valid']).read_text().splitlines()]
        self.assertFalse({row['groupId'] for row in train} & {row['groupId'] for row in valid})
        self.assertEqual(sum(row['kind']=='translation' for row in train+valid), 1)
        self.assertTrue(any(row['kind']=='translation' for row in valid))
        self.assertNotIn('PENDING-TEXT', Path(snapshot['train']).read_text()+Path(snapshot['valid']).read_text())
        self.assertFalse(any(row['kind']=='tag_stat' for row in train+valid))
        for name, receipt in snapshot['manifest']['files'].items():
            content = (Path(snapshot['directory'])/name).read_bytes()
            self.assertEqual(hashlib.sha256(content).hexdigest(), receipt['sha256'])
        second = self.memory.snapshot('second', eval_fraction=.5)
        self.assertEqual(Path(snapshot['train']).read_bytes(), Path(second['train']).read_bytes())
        with self.assertRaises(ValueError): self.memory.snapshot('test-snapshot')
        with self.assertRaises(ValueError): self.memory.snapshot('../escape')

    def test_checkpoint_resume_receipt_and_eligibility(self):
        self.assertFalse(self.memory.status()['trainingEligible'])
        for index in range(20):
            self.memory.record('translation', {'ru':f'Мебель {index}','zh':f'家具{index}'}, status='approved', group_id=f'group{index%5}')
        self.assertTrue(self.memory.status()['trainingEligible'])
        self.memory.save_checkpoint('run-1', {'state':'paused','step':3,'model':'local-model','metrics':{'loss':.7}})
        self.memory.save_checkpoint('run-1', {'state':'complete','step':7,'model':'local-model','metrics':{'loss':.5}})
        resumed = LearningMemory(self.directory)
        self.assertEqual(resumed.checkpoints()[0]['step'], 7)
        self.assertEqual(resumed.status()['lastCheckpoint']['state'], 'complete')
        with self.assertRaises(ValueError): self.memory.save_checkpoint('bad', {'state':'complete','metrics':{'loss':float('nan')}})
        with self.assertRaises(ValueError): self.memory.save_checkpoint('bad', {'state':'promoted'})

    def test_list_events_filters_and_bad_queries_rejected(self):
        self.memory.record('title', {'text':'标题','language':'zh'})
        self.memory.record('title', {'text':'Другой','language':'ru'}, status='approved')
        self.assertEqual(len(self.memory.list_events('pending')),1)
        self.assertEqual(len(self.memory.list_events()),2)
        with self.assertRaises(ValueError): self.memory.list_events('bad')
        with self.assertRaises(ValueError): self.memory.retrieve('x',0)
        with self.assertRaises(ValueError): self.memory.list_events(limit=10000)


if __name__ == '__main__':
    unittest.main()
