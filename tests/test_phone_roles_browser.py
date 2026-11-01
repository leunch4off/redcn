"""Role UI regressions on disposable companion servers only."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parent))
import test_phone_browser as fixture


class PhoneRoleBrowserAcceptance(unittest.TestCase):
    setUpClass=classmethod(fixture.PhoneBrowserAcceptance.setUpClass.__func__)
    tearDownClass=classmethod(fixture.PhoneBrowserAcceptance.tearDownClass.__func__)
    setUp=fixture.PhoneBrowserAcceptance.setUp
    tearDown=fixture.PhoneBrowserAcceptance.tearDown
    connect=fixture.PhoneBrowserAcceptance.connect
    assert_mobile_fit=fixture.PhoneBrowserAcceptance.assert_mobile_fit

    def grant(self,role):
        state=self.phone.pairing.list_sessions();self.phone.pairing.manage_session(state['sessions'][0]['id'],role=role,expected_revision=state['revision'])

    def test_dictionary_initializer_exists_after_reconnect_without_reloading_document(self):
        self.connect();self.page.locator('#phoneTabs [data-phone-view="setup"]').click();self.page.locator('#phoneDictionaryMount .dictionary-review').first.wait_for(state='visible')
        self.page.wait_for_function("document.getElementById('phoneDictionaryMount').textContent.includes('Версия общего словаря:')")
        self.phone.pairing.revoke();self.page.locator('#phoneTabs [data-phone-view="cards"]').click();self.page.locator('#phonePair').wait_for(state='visible')
        _,manual,_=self.phone.pairing.current();self.page.locator('#phonePairCode').fill(manual);self.page.locator('#phonePairSubmit').click();self.page.locator('#phoneWorkspace').wait_for(state='visible')
        self.page.locator('#phoneTabs [data-phone-view="setup"]').click();self.page.wait_for_function("document.getElementById('phoneDictionaryMount').textContent.includes('Версия общего словаря:')");self.assert_mobile_fit()

    def test_role_change_arrives_with_normal_feed_and_viewer_photos_do_not_unhide_edit_controls(self):
        self.connect();self.grant('viewer');self.page.locator('#phoneRefresh').click();self.page.wait_for_function("document.getElementById('phoneRoleStatus').textContent.includes('читатель')")
        self.assertTrue(self.page.locator('#phoneTabs [data-phone-view="import"]').is_hidden());self.assertTrue(self.page.locator('#phoneTabs [data-phone-view="design"]').is_hidden())
        self.page.locator('#phoneCards .listing-card').first.click();self.page.wait_for_function("document.querySelector('#phonePhotos img')?.naturalWidth>0")
        for selector in ['#phoneAnalyzeFurniture','#phonePreparePhotos','#phoneArchiveMount','#phonePublicationReviewMount','#phoneRoutesMount']:
            self.assertTrue(self.page.locator(selector).is_hidden(),selector)
        self.assertEqual(self.page.locator('#phoneArchiveMount').inner_text(),'','private archive is not initialized for readers')
        self.assertTrue(self.page.locator('#phoneRewriteAll').is_disabled());self.assertTrue(self.page.locator('#phoneDownloadZip').is_visible());self.assert_mobile_fit()


if __name__=='__main__':unittest.main()
