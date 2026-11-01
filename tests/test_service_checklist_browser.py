"""Service160 in the actual built editor on an owned static 393px fixture.

No controller/imports/models are constructed. All edits use visible controls;
private receipts are checked against a real client HTML download, not a mock.
"""
from datetime import datetime,timezone
from pathlib import Path
import unittest

import test_twenty_editor_browser as editor_fixture


class ServiceChecklistBuiltEditor(unittest.TestCase):
    # Bind fixture methods, rather than inheriting its TestCase and rerunning
    # every unrelated editor test under this class.
    setUpClass=classmethod(editor_fixture.TwentyEditorBrowserAcceptance.setUpClass.__func__)
    tearDownClass=classmethod(editor_fixture.TwentyEditorBrowserAcceptance.tearDownClass.__func__)
    tearDown=editor_fixture.TwentyEditorBrowserAcceptance.tearDown
    fields=editor_fixture.TwentyEditorBrowserAcceptance.fields
    seed=editor_fixture.TwentyEditorBrowserAcceptance.seed

    def setUp(self):
        self.context=self.browser.new_context(viewport={'width':393,'height':852},accept_downloads=True)
        self.context.route('**/*',lambda route:route.continue_() if route.request.url.startswith(self.url+'/') else route.abort())
        self.page=self.context.new_page();self.errors=[]
        self.page.on('pageerror',lambda error:self.errors.append(str(error)))
        self.page.goto(self.url,wait_until='domcontentloaded')
        self.page.wait_for_function("document.documentElement.dataset.editorReady==='true'")
        self.page.locator('nav button[data-tab="history"]').click()
        self.page.locator('#captureSnapshots').uncheck()
        self.page.locator('nav button[data-tab="editor"]').click()
        self.seed()
        self.page.locator('#support').uncheck()
        self.page.locator('#registrationMonths').fill('3')
        self.page.locator('#registrationPeople').fill('2')
        self.open_ledger()
        self.page.get_by_label('Автор изменения',exact=True).fill('PRIVATE_SERVICE_AUTHOR')
        self.page.get_by_label('Автор изменения',exact=True).press('Tab')

    def open_ledger(self):
        panel=self.page.locator('#factLedgerMount>details')
        if not panel.evaluate('(element)=>element.open'):panel.locator('summary').first.click()

    def service(self,label='Содействие регистрации по прибытии'):
        return self.page.get_by_label('Подтвердить услугу: '+label,exact=True)

    def confirm_registration(self):
        self.service().check()
        self.page.wait_for_function("JSON.parse(localStorage.getItem('most-draft'))?.fields?.serviceConfirmations?.registration?.actor==='PRIVATE_SERVICE_AUTHOR'")
        receipt=self.fields()['serviceConfirmations']['registration']
        self.assertEqual(receipt['value'],{'service':'arrival-registration-assistance','months':'3','people':'2'})
        self.assertEqual(receipt['actor'],'PRIVATE_SERVICE_AUTHOR')
        stamp=datetime.fromisoformat(receipt['at'].replace('Z','+00:00'))
        self.assertLess(abs((datetime.now(timezone.utc)-stamp).total_seconds()),120)
        self.assertTrue(self.service().is_checked())
        self.assertIn('PRIVATE_SERVICE_AUTHOR',self.service().locator('..').inner_text())
        return receipt

    def reload_editor(self):
        self.page.reload(wait_until='domcontentloaded')
        self.page.wait_for_function("document.documentElement.dataset.editorReady==='true'")
        self.open_ledger()

    def test_registration_receipt_saved_reloaded_and_removed_from_real_client_html(self):
        receipt=self.confirm_registration()
        self.page.locator('#save').click()
        self.page.wait_for_function("JSON.parse(localStorage.getItem('most-objects'))?.[0]?.serviceConfirmations?.registration?.actor==='PRIVATE_SERVICE_AUTHOR'")
        saved=self.page.evaluate("JSON.parse(localStorage.getItem('most-objects'))[0]")
        self.assertEqual(saved['serviceConfirmations']['registration'],receipt)
        self.reload_editor()
        self.assertEqual(self.fields()['serviceConfirmations']['registration'],receipt)
        self.assertTrue(self.service().is_checked())
        self.assertIn('PRIVATE_SERVICE_AUTHOR',self.service().locator('..').inner_text())
        self.page.locator('nav button[data-tab="history"]').click()
        self.page.locator('#historyFilter').select_option('save')
        self.page.get_by_role('button',name='Посмотреть / восстановить',exact=True).first.click()
        with self.page.expect_download() as downloaded:self.page.locator('#historyHtml').click()
        html=Path(downloaded.value.path()).read_text(encoding='utf-8')
        self.assertIn('80000',html)
        self.assertNotIn('serviceConfirmations',html)
        self.assertNotIn('PRIVATE_SERVICE_AUTHOR',html)
        self.assertNotIn('arrival-registration-assistance',html)
        self.assertEqual(self.fields()['serviceConfirmations']['registration'],receipt)
        self.assertEqual(self.page.evaluate("JSON.parse(localStorage.getItem('most-objects'))[0].serviceConfirmations.registration"),receipt)
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'),394)

    def test_people_and_month_changes_immediately_invalidate_checked_confirmation(self):
        self.confirm_registration()
        self.page.locator('#registrationPeople').fill('3')
        self.assertFalse(self.service().is_checked())
        self.assertIn('Объём услуги изменился',self.service().locator('..').inner_text())
        self.service().check()
        self.assertTrue(self.service().is_checked())
        self.assertEqual(self.fields()['serviceConfirmations']['registration']['value']['people'],'3')
        self.page.locator('#registrationMonths').fill('4')
        self.assertFalse(self.service().is_checked())
        self.assertIn('Объём услуги изменился',self.service().locator('..').inner_text())
        self.reload_editor()
        self.assertFalse(self.service().is_checked())

    def test_new_card_does_not_reuse_previous_card_service_confirmation(self):
        receipt=self.confirm_registration()
        previous=self.fields()['editorIdentity']
        self.page.locator('#save').click()
        self.page.wait_for_function("JSON.parse(localStorage.getItem('most-objects'))?.length===1")
        self.page.locator('#newObject').click()
        self.assertNotEqual(self.fields()['editorIdentity'],previous)
        self.assertEqual(self.fields()['serviceConfirmations'],{})
        self.open_ledger();self.assertFalse(self.service().is_checked())
        self.assertNotIn('PRIVATE_SERVICE_AUTHOR',self.service().locator('..').inner_text())
        self.assertEqual(self.page.evaluate("JSON.parse(localStorage.getItem('most-objects'))[0].serviceConfirmations.registration"),receipt)
        self.reload_editor();self.assertFalse(self.service().is_checked())
        self.assertEqual(self.fields()['serviceConfirmations'],{})

    def test_saved_benefits_change_invalidates_support_confirmation(self):
        self.page.locator('#support').check()
        support=self.service('Поддержка и преимущества студии')
        support.check();self.assertTrue(support.is_checked())
        before=self.fields()['serviceConfirmations']['support']
        self.page.locator('nav button[data-tab="settings"]').click()
        self.page.locator('#benefits').fill('新的中文支持服务内容')
        self.page.locator('#saveSettings').click()
        self.page.locator('nav button[data-tab="editor"]').click()
        self.open_ledger()
        self.assertFalse(support.is_checked())
        self.assertIn('Объём услуги изменился',support.locator('..').inner_text())
        self.assertEqual(self.fields()['serviceConfirmations']['support'],before)
        self.reload_editor();self.assertFalse(support.is_checked())


if __name__=='__main__':unittest.main()
