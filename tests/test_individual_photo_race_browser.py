"""Delayed text jobs and real photo UI callbacks on disposable Chrome servers.

Text replies and vision observations are authored fixtures, not model-quality
evidence. The actual built editor, photo cache and photo-to-text queue execute.
No personal card or native model is used by these scenarios.
"""
import threading
import unittest
from unittest.mock import patch

import test_phone_browser as fixture
from browser_text_jobs_fixture import MockTextJobs, accepted_draft
from test_photo_inventory_browser import observation


LABELS = {'fridge': '冰箱', 'sofa': '沙发', 'bedAny': '床'}


def photo_aware_draft(request, ordinal):
    result = accepted_draft(request, ordinal)
    found = [LABELS[key] for key in request['fields'].get('features', []) if key in LABELS]
    result['body'] += '\n已确认的照片物品：' + ('、'.join(found) if found else '本次尚无照片物品') + '。'
    result['inventoryCount'] = len(found)
    return result


class IndividualPhotoRaceBrowserTests(unittest.TestCase):
    setUpClass = classmethod(fixture.PhoneBrowserAcceptance.setUpClass.__func__)
    tearDownClass = classmethod(fixture.PhoneBrowserAcceptance.tearDownClass.__func__)

    def setUp(self):
        fixture.PhoneBrowserAcceptance.setUp(self)
        self.desktop_thread = threading.Thread(target=self.controller.serve_forever, daemon=True)
        self.desktop_thread.start()
        self.page.set_viewport_size({'width': 1440, 'height': 1100})
        self.page.add_init_script("""if(window===window.top){
            localStorage.setItem('most-photo-analyze','false');
            localStorage.setItem('most-photo-autoload','false');
        }""")
        def cached_image(route):
            asset = next(item for item in self.assets if item['sourceUrl'] == route.request.url)
            content, mime, _ = fixture.photo_store.asset(self.directory, asset['id'])
            route.fulfill(status=200, content_type=mime, body=content)
        self.page.route('https://images.cdn-cian.ru/**', cached_image)
        self.jobs = MockTextJobs(self.page, 'local', photo_aware_draft, hold=True)
        self.page.goto(self.controller.desktop_access.launch_url(self.controller.server_port, self.card_id),
            wait_until='domcontentloaded')
        self.page.wait_for_function("document.documentElement.dataset.editorReady==='true'&&"
            "document.getElementById('importStatus').textContent.includes('Сохранённая карточка загружена')")

    def tearDown(self):
        self.controller.shutdown()
        self.desktop_thread.join()
        fixture.PhoneBrowserAcceptance.tearDown(self)

    def start_text(self):
        self.page.locator('#rewriteCopy').click()
        self.page.wait_for_function("document.querySelector('#individualCopyProgress').textContent.includes('24,5 токенов/с')")
        self.assertEqual(len(self.jobs.requests), 1)
        self.assertEqual(self.jobs.requests[0]['fields'].get('features'), [])

    def finish_photo_analysis(self, *, complete=True):
        first_digest = self.assets[0]['id']
        def analyze(_directory, digest, **_options):
            self.assertIn(digest, [asset['id'] for asset in self.assets])
            features = (['fridge', 'sofa'] if digest == first_digest else ['bedAny']) if complete else (
                ['fridge'] if digest == first_digest else [])
            return observation(digest, features)
        with patch.object(fixture.redcn, 'fetch_bytes', side_effect=AssertionError('Use cached fixture originals only')), \
                patch.object(fixture.photo_store, 'analyze_photo', side_effect=analyze) as model:
            self.page.locator('#photoAnalyze').click()
            expected = "&&document.getElementById('feature-sofa').checked&&document.getElementById('feature-bedAny').checked" if complete else ''
            self.page.wait_for_function("!document.getElementById('photoAnalyze').disabled&&document.getElementById('feature-fridge').checked"+expected)
            self.assertEqual(model.call_count, 2)
        self.assertEqual(len(self.jobs.requests), 1, 'New photo facts queue behind the active text job')

    def assert_finished_without_retry(self, expected_body):
        self.page.wait_for_function("!document.getElementById('rewriteCopy').disabled")
        self.assertEqual(len(self.jobs.requests), 1)
        self.assertEqual(self.page.locator('#output').input_value(), expected_body)
        self.assertFalse(self.page.get_by_role('button', name='Применить новую версию', exact=True).is_visible())

    def test_multiple_photo_completions_coalesce_into_one_retry_with_latest_evidence(self):
        self.start_text()
        self.finish_photo_analysis(complete=False)
        self.finish_photo_analysis(complete=True)
        self.assertEqual(self.page.locator('#output').input_value(), self.result['body'])
        self.jobs.release_all()
        apply = self.page.get_by_role('button', name='Применить новую версию', exact=True)
        apply.wait_for(state='visible')
        self.page.wait_for_function("!document.getElementById('rewriteCopy').disabled")
        self.assertEqual(len(self.jobs.requests), 2, 'The obsolete response must cause exactly one fresh request')
        self.assertEqual(self.jobs.requests[1]['part'], 'all')
        self.assertEqual(set(self.jobs.requests[1]['fields']['features']), set(LABELS))
        evidence = self.jobs.requests[1]['fields']['photoEvidence']
        self.assertEqual({item['id'] for item in evidence}, set(LABELS))
        self.assertTrue(all(item['sourceUrl'] in self.result['fields']['photos'] for item in evidence))
        self.assertNotEqual(self.jobs.requests[0]['requestId'], self.jobs.requests[1]['requestId'])
        self.assertEqual(self.page.locator('#output').input_value(), self.result['body'])
        preview = apply.locator('..').inner_text()
        for label in LABELS.values():
            self.assertIn(label, preview)
        self.assertNotIn('本次尚无照片物品', preview, 'The stale text must never be offered for acceptance')
        apply.click()
        self.page.wait_for_function("document.getElementById('output').value.includes('冰箱')&&"
            "document.getElementById('output').value.includes('沙发')&&document.getElementById('output').value.includes('床')")
        self.assertEqual(len(self.jobs.requests), 2)

    def test_manual_edit_after_photo_completion_suppresses_queued_retry(self):
        self.start_text()
        self.finish_photo_analysis()
        manual = '照片回来后，我修改了这段正文，请保留。'
        self.page.locator('#output').fill(manual)
        self.jobs.release_all()
        self.assert_finished_without_retry(manual)
        self.assertIn('карточка изменилась', self.page.locator('#review').inner_text())

    def test_manual_edit_before_photo_completion_does_not_queue_retry(self):
        self.start_text()
        manual = '生成期间先写下的手工正文，不接受自动替换。'
        self.page.locator('#output').fill(manual)
        self.finish_photo_analysis()
        self.jobs.release_all()
        self.assert_finished_without_retry(manual)

    def test_explicit_cancel_discards_the_pending_photo_retry(self):
        self.start_text()
        self.finish_photo_analysis()
        self.page.locator('#individualCopyCancel').click()
        self.jobs.release_all()
        self.assert_finished_without_retry(self.result['body'])
        self.assertEqual(len(self.jobs.cancellations), 1)
        self.assertIn('отменена', self.page.locator('#review').inner_text())

    def test_switching_cards_discards_pending_retry_and_does_not_write_into_new_card(self):
        self.start_text()
        self.finish_photo_analysis()
        self.page.locator('#newObject').click()
        self.assertEqual(self.page.locator('#output').input_value(), '')
        self.jobs.release_all()
        self.assert_finished_without_retry('')
        self.assertEqual(self.page.locator('#outputTitle').input_value(), '')
        self.assertEqual(self.page.locator('#tags').input_value(), '')
        self.assertEqual(fixture.local_server.read_result(self.directory, self.card_id)['body'], self.result['body'])


if __name__ == '__main__':
    unittest.main()
