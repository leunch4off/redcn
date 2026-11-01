"""Versioned correction approval in owned temporary workspaces only."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'cli'))
import dictionary_reviews as reviews


class DictionaryReviewTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.directory=Path(self.folder.name)

    def tearDown(self):
        self.folder.cleanup()

    def proposal(self, ru='рабочий стол', zh='书桌', **extra):
        revision=reviews.snapshot(self.directory)['revision']
        return reviews.mutate(self.directory,{'action':'propose','expectedRevision':revision,'ru':ru,'zh':zh,'source':'Замечание оператора: точная бытовая формулировка','origin':'knowledge',**extra},actor='editor-one')

    def approve(self, state, **extra):
        return reviews.mutate(self.directory,{'action':'approve','expectedRevision':state['revision'],'id':state['entries'][0]['id'],**extra},actor='reviewer-two')

    def test_draft_is_not_effective_until_separate_approval_and_keeps_provenance(self):
        state=self.proposal()
        self.assertEqual(state['revision'],1);self.assertEqual(state['rules'],{})
        entry=state['entries'][0];self.assertEqual(entry['origin'],'knowledge');self.assertEqual(entry['proposed_by'],'editor-one')
        approved=self.approve(state)
        self.assertEqual(approved['revision'],2);self.assertEqual(approved['rules'],{'рабочий стол':'书桌'})
        self.assertEqual(approved['entries'][0]['reviewed_by'],'reviewer-two')
        event=approved['events'][0];self.assertIsNone(event['before_zh']);self.assertEqual(event['after_zh'],'书桌');self.assertEqual(event['action'],'approve')

    def test_conflicting_pair_requires_explicit_actual_before_value_and_keeps_previous_version(self):
        self.approve(self.proposal())
        proposed=self.proposal(ru=' Рабочий   стол ',zh='写字台')
        self.assertEqual(proposed['entries'][0]['previous_zh'],'书桌')
        for extra in ({},{'replaceApproved':True,'expectedPrevious':'STALE'},{'replaceApproved':False,'expectedPrevious':'书桌'}):
            with self.subTest(extra=extra),self.assertRaises(reviews.DictionaryConflict):self.approve(proposed,**extra)
        self.assertEqual(reviews.snapshot(self.directory)['revision'],3)
        approved=self.approve(proposed,replaceApproved=True,expectedPrevious='书桌')
        self.assertEqual(approved['revision'],4);self.assertEqual(list(approved['rules'].values()),['写字台'])
        self.assertEqual({row['state'] for row in approved['entries']},{'approved','superseded'})
        self.assertEqual(approved['events'][0]['before_zh'],'书桌');self.assertEqual(approved['events'][0]['after_zh'],'写字台')

    def test_stale_approval_cannot_overwrite_another_device_and_both_see_versions(self):
        first=self.proposal()
        self.proposal('шкаф','衣柜')
        with self.assertRaises(reviews.DictionaryConflict):self.approve(first)
        latest=reviews.snapshot(self.directory)
        self.assertEqual(latest['revision'],2);self.assertEqual(len(latest['entries']),2);self.assertEqual(latest['rules'],{})

    def test_rejected_machine_draft_never_becomes_a_verified_rule(self):
        state=self.proposal(origin='machine')
        rejected=reviews.mutate(self.directory,{'action':'reject','id':state['entries'][0]['id'],'expectedRevision':1,'reason':'Черновик подменяет назначение комнаты'},actor='reviewer')
        self.assertEqual(rejected['rules'],{});self.assertEqual(rejected['entries'][0]['state'],'rejected')
        self.assertEqual(rejected['events'][0]['reason'],'Черновик подменяет назначение комнаты')
        with self.assertRaises(reviews.DictionaryConflict):self.approve(rejected)

    def test_personal_profile_and_other_workspace_are_untouched(self):
        profile=self.directory/'phone-profile.json';profile.write_text('{"rules":{"личное":"自己的"}}',encoding='utf8');before=profile.read_bytes()
        self.approve(self.proposal())
        self.assertEqual(profile.read_bytes(),before)
        with tempfile.TemporaryDirectory() as another:self.assertEqual(reviews.snapshot(another)['rules'],{})
        self.assertEqual((self.directory/'dictionary-reviews.sqlite3').stat().st_mode&0o777,0o600)

    def test_invalid_actions_payloads_actors_and_untranslated_text_cannot_be_approved(self):
        for extra in ({'expectedRevision':True},{'zh':'полностью русский текст'},{'origin':'approved'},{'source':''},{'ru':[]},{'actor':'spoofed-owner'}):
            with self.subTest(extra=extra),self.assertRaises(ValueError):self.proposal(**extra)
        self.assertEqual(reviews.snapshot(self.directory)['rules'],{})
        self.assertEqual(reviews.dispatch(self.directory,'another-route'),None)
        self.assertEqual(reviews.dispatch(self.directory,'dictionary-reviews',{'action':'clear','expectedRevision':0})[0],400)

    def test_parallel_proposals_use_compare_and_swap_instead_of_lost_updates(self):
        reviews.snapshot(self.directory)
        def propose(index):
            try:return reviews.mutate(self.directory,{'action':'propose','expectedRevision':0,'ru':'слово '+str(index),'zh':'测试','source':'test'})['revision']
            except reviews.DictionaryConflict:return 'conflict'
        with ThreadPoolExecutor(max_workers=2) as pool:result=list(pool.map(propose,[1,2]))
        self.assertCountEqual(result,[1,'conflict']);self.assertEqual(reviews.snapshot(self.directory)['revision'],1)

    def test_dispatch_returns_conflict_status_without_changing_current_snapshot(self):
        state=self.proposal()
        response=reviews.dispatch(self.directory,'dictionary-reviews',{'action':'approve','id':state['entries'][0]['id'],'expectedRevision':0})
        self.assertEqual(response[0],409);self.assertEqual(reviews.snapshot(self.directory)['revision'],1)


if __name__=='__main__':
    unittest.main()
