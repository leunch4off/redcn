"""Saved-post review receipts against isolated SQLite and HTTP fixtures."""
import http.cookiejar
import json
from pathlib import Path
import sys
import tempfile
import unittest
import urllib.error
import urllib.request

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
sys.path.insert(0,str(Path(__file__).resolve().parent))
import local_server
import publication_review
import test_phone_relay as fixture


class PublicationReviews(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.directory=Path(self.temp.name)
        self.cid=local_server.save_result(self.directory,{'fields':{'price':'80000','area':'50'},'titles':['中文房源'],'body':'真实房源说明','tags':'#莫斯科'})

    def tearDown(self):self.temp.cleanup()

    def state(self,actor='phone:author'):return publication_review.snapshot(self.directory,self.cid,actor=actor)

    def change(self,action,actor='phone:author',state=None,**patch):
        state=state or self.state(actor)
        return publication_review.mutate(self.directory,self.cid,{'action':action,'expectedRevision':state['revision'],'expectedUpdated':state['savedUpdated'],**patch},actor=actor)

    def test_saved_post_can_only_be_reviewed_by_different_verified_actor(self):
        initial=self.state();self.assertEqual(initial['state'],'not-submitted');self.assertEqual(initial['revision'],0)
        submitted=self.change('submit',comment='Проверьте текущую версию');self.assertEqual(submitted['state'],'pending');self.assertFalse(submitted['canReview'])
        with self.assertRaisesRegex(ValueError,'другой'):self.change('approve')
        other=self.state('Mac owner');self.assertTrue(other['canReview'])
        approved=self.change('approve',actor='Mac owner',state=other,comment='Поля проверены')
        self.assertEqual(approved['state'],'approved');self.assertEqual(approved['review']['submitted_by'],'phone:author');self.assertEqual(approved['review']['reviewed_by'],'Mac owner')
        self.assertEqual([row['action'] for row in approved['history']],['approve','submit'])
        self.assertEqual(local_server.read_result(self.directory,self.cid)['body'],'真实房源说明','review never rewrites or posts saved content')

    def test_changed_client_content_is_stale_and_cannot_be_approved_with_fresh_revision(self):
        submitted=self.change('submit');card=local_server.read_result(self.directory,self.cid);card['body']='新的真实说明';local_server.save_result(self.directory,card,self.cid)
        changed=self.state('Mac owner');self.assertEqual(changed['state'],'stale');self.assertFalse(changed['canReview']);self.assertNotEqual(changed['currentHash'],submitted['currentHash'])
        with self.assertRaises(publication_review.PublicationReviewConflict):self.change('approve',actor='Mac owner',state=changed)
        resubmitted=self.change('submit',state=changed);self.assertEqual(resubmitted['state'],'pending');self.assertEqual(resubmitted['review']['post_hash'],changed['currentHash'])
        self.assertEqual(self.change('approve',actor='Mac owner')['state'],'approved')

    def test_compare_and_swap_prevents_old_review_and_saved_card_versions(self):
        old=self.state();self.change('submit',state=old)
        with self.assertRaises(publication_review.PublicationReviewConflict):self.change('submit',state=old)
        state=self.state('Mac owner');card=local_server.read_result(self.directory,self.cid);card['fields']['source']='Private operator source edit';local_server.save_result(self.directory,card,self.cid)
        # Private-only edits preserve the client content hash, but a stale editor
        # must still explicitly refresh its saved-card receipt before reviewing.
        current=self.state('Mac owner');self.assertEqual(current['currentHash'],state['currentHash']);self.assertEqual(current['state'],'pending')
        with self.assertRaises(publication_review.PublicationReviewConflict):self.change('approve',actor='Mac owner',state=state)
        self.assertEqual(self.change('approve',actor='Mac owner',state=current)['state'],'approved')

    def test_reject_needs_reason_and_a_new_submission_before_approval(self):
        self.change('submit')
        with self.assertRaisesRegex(ValueError,'причину'):self.change('reject',actor='phone:reviewer')
        rejected=self.change('reject',actor='phone:reviewer',comment='Проверьте стоимость');self.assertEqual(rejected['state'],'rejected');self.assertFalse(rejected['canReview'])
        with self.assertRaises(publication_review.PublicationReviewConflict):self.change('approve',actor='Mac owner')
        resubmitted=self.change('submit');self.assertEqual(resubmitted['revision'],3);self.assertIsNone(resubmitted['review']['reviewed_by'])

    def test_actor_and_versions_are_not_accepted_from_untrusted_payload(self):
        state=self.state();valid={'action':'submit','expectedRevision':state['revision'],'expectedUpdated':state['savedUpdated']}
        for patch in ({'actor':'Mac owner'},{'expectedRevision':True},{'expectedUpdated':float('nan')},{'comment':'x'*1001},{'action':'publish'}):
            with self.assertRaises(ValueError):publication_review.mutate(self.directory,self.cid,{**valid,**patch},actor='phone:author')
        for actor in (None,'','bad\nactor'):
            with self.assertRaises(ValueError):publication_review.snapshot(self.directory,self.cid,actor=actor)
        self.assertEqual(self.state()['revision'],0)

    def test_dispatch_returns_conflict_and_missing_or_demo_cards_are_not_reviewed(self):
        path='publication-review/'+self.cid;state=self.state();payload={'action':'submit','expectedRevision':state['revision'],'expectedUpdated':state['savedUpdated']}
        self.assertEqual(publication_review.dispatch(self.directory,path,payload,actor='author')[0],200)
        self.assertEqual(publication_review.dispatch(self.directory,path,payload,actor='author')[0],409)
        self.assertIsNone(publication_review.dispatch(self.directory,'unrelated',actor='author'))
        demo=local_server.save_result(self.directory,{'fields':{'isDemo':True},'titles':[],'body':'','tags':''})
        self.assertEqual(publication_review.dispatch(self.directory,'publication-review/'+demo,actor='author')[0],400)
        self.assertEqual(publication_review.dispatch(self.directory,'publication-review/'+'f'*32,actor='author')[0],400)


class PublicationReviewHTTP(unittest.TestCase):
    setUp=fixture.PhoneRelayTests.setUp
    tearDown=fixture.PhoneRelayTests.tearDown
    request=fixture.PhoneRelayTests.request
    pair=fixture.PhoneRelayTests.pair
    add_card=fixture.PhoneRelayTests.add_card

    def test_phone_server_supplies_actor_and_separate_session_reviews_then_viewer_is_denied(self):
        self.pair();cid=self.add_card();route='publication-review/'+cid
        # The real boundary supplies actor; the dispatcher never inspects a
        # request's claimed identity, role, or nickname.
        self.controller.workspace_api=lambda path,data=None,namespace='phone',query=None,actor=None:publication_review.dispatch(self.directory,path,data,actor=actor)
        author=self.request('/api/phone/'+route)
        payload={'action':'submit','expectedRevision':author['revision'],'expectedUpdated':author['savedUpdated']}
        submitted=self.request('/api/phone/'+route,payload)
        with self.assertRaises(urllib.error.HTTPError) as caught:self.request('/api/phone/'+route,{**payload,'actor':'Mac owner'})
        self.assertEqual(caught.exception.code,400)
        with self.assertRaises(urllib.error.HTTPError) as caught:self.request('/api/phone/'+route,{'action':'approve','expectedRevision':submitted['revision'],'expectedUpdated':submitted['savedUpdated']})
        self.assertEqual(caught.exception.code,400)
        jar=http.cookiejar.CookieJar();colleague=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar));code,_=self.server.pairing.issue();paired=self.request('/api/phone/pair',{'code':code},client=colleague);csrf=paired['csrfToken']
        reviewer=self.request('/api/phone/'+route,client=colleague);self.assertTrue(reviewer['canReview'])
        approved=self.request('/api/phone/'+route,{'action':'approve','expectedRevision':reviewer['revision'],'expectedUpdated':reviewer['savedUpdated']},headers={'X-Phone-CSRF':csrf},client=colleague)
        self.assertEqual(approved['state'],'approved');self.assertNotEqual(approved['review']['submitted_by'],approved['review']['reviewed_by'])
        import hashlib
        sid=hashlib.sha256(next(iter(jar)).value.encode()).hexdigest();state=self.server.pairing.list_sessions();self.server.pairing.manage_session(sid,role='viewer',expected_revision=state['revision'])
        for data in (None,{'action':'submit','expectedRevision':approved['revision'],'expectedUpdated':approved['savedUpdated']}):
            with self.assertRaises(urllib.error.HTTPError) as caught:self.request('/api/phone/'+route,data,headers={'X-Phone-CSRF':csrf},client=colleague)
            self.assertEqual(caught.exception.code,403)


if __name__=='__main__':unittest.main()
