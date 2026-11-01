"""Paired phone copy/ZIP acceptance for a preserved legacy commission draft.

Uses only isolated temporary cards and generated pictures from the shared
browser fixture; it does not import a listing or touch the owner's cache.
"""
import copy
import io
import json
import unittest
import zipfile

import test_phone_browser as acceptance
import local_server


class ClientPublicationBrowserAcceptance(unittest.TestCase):
    setUpClass = classmethod(acceptance.PhoneBrowserAcceptance.setUpClass.__func__)
    tearDownClass = classmethod(acceptance.PhoneBrowserAcceptance.tearDownClass.__func__)
    setUp = acceptance.PhoneBrowserAcceptance.setUp
    tearDown = acceptance.PhoneBrowserAcceptance.tearDown

    def assert_fee_free(self, text):
        self.assertNotRegex(text, r'中介费|中介費|佣金|傭金|免佣|комисси|commission')

    def test_old_draft_phone_copy_and_zip_share_one_clean_projection_without_mutating_source(self):
        self.result['fields'].update({'price': '80000', 'deposit': '40000', 'commission': 50})
        self.result['body'] = '租金80000\n中介费50%\n押金40000'
        self.result['tags'] = '#莫斯科 #免佣'
        local_server.save_result(self.directory, self.result, self.card_id)
        raw_before = copy.deepcopy(local_server.read_result(self.directory, self.card_id))
        self.page.add_init_script("""
            window.__publicationCopies = [];
            Object.defineProperty(navigator, 'clipboard', {
                configurable: true,
                value: {writeText: async function(text) {
                    window.__publicationCopies.push(String(text));
                }}
            });
        """)

        acceptance.PhoneBrowserAcceptance.connect(self)
        self.page.locator('#phoneCards .listing-card').first.click()
        self.page.wait_for_function("document.querySelector('#phoneBody').textContent.includes('押金40000')")
        body = self.page.locator('#phoneBody').inner_text()
        tags = self.page.locator('#phoneTags').inner_text()
        self.assertEqual(body, '租金80000\n押金40000')
        self.assertEqual(tags, '#莫斯科')
        self.assert_fee_free(body + '\n' + tags)
        self.assertTrue(self.page.evaluate('window.isSecureContext'))

        self.page.locator('#phoneCopyBody').click()
        self.page.wait_for_function('window.__publicationCopies.length === 1')
        self.assertEqual(self.page.evaluate('window.__publicationCopies[0]'), body)
        self.assertIn('Скопировано', self.page.locator('#phoneToast').inner_text())
        self.page.locator('#phoneCopyAll').click()
        self.page.wait_for_function('window.__publicationCopies.length === 2')
        copied = self.page.evaluate('window.__publicationCopies[1]')
        self.assertIn(self.result['titles'][0], copied)
        self.assertIn(body, copied)
        self.assertIn('#莫斯科', copied)
        self.assert_fee_free(copied)

        detail_response = self.context.request.get(self.base + '/api/phone/cards/' + self.card_id)
        self.assertEqual(detail_response.status, 200)
        detail = detail_response.json()
        self.assertEqual(detail['fields']['price'], '80000')
        self.assertEqual(detail['fields']['deposit'], '40000')
        self.assertNotIn('commission', detail['fields'])
        self.assert_fee_free(json.dumps(detail, ensure_ascii=False))

        zip_path = self.page.locator('#phoneDownloadZip').get_attribute('href')
        self.assertTrue(zip_path and zip_path.startswith('/api/phone/'))
        archive_response = self.context.request.get(self.base + zip_path)
        self.assertEqual(archive_response.status, 200)
        with zipfile.ZipFile(io.BytesIO(archive_response.body())) as archive:
            self.assertIsNone(archive.testzip())
            post = archive.read('post.txt').decode('utf-8')
            manifest_text = archive.read('manifest.json').decode('utf-8')
            self.assertIn('租金80000', post)
            self.assertIn('押金40000', post)
            self.assertIn('#莫斯科', post)
            self.assert_fee_free(post + '\n' + manifest_text)
            self.assertEqual(len(json.loads(manifest_text)), len(self.assets))

        self.assertEqual(local_server.read_result(self.directory, self.card_id), raw_before)
        self.assertEqual(raw_before['body'], '租金80000\n中介费50%\n押金40000')
        self.assertEqual(raw_before['tags'], '#莫斯科 #免佣')
        self.assertEqual(raw_before['fields']['commission'], 50)
        acceptance.PhoneBrowserAcceptance.assert_mobile_fit(self)


if __name__ == '__main__':
    unittest.main()
