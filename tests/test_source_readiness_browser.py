"""Synthetic completed imports retain honest source status in both interfaces."""
import json
import threading
import unittest

import test_phone_browser as acceptance
import local_server


class SourceReadinessBrowserAcceptance(unittest.TestCase):
    setUpClass = classmethod(acceptance.PhoneBrowserAcceptance.setUpClass.__func__)
    tearDownClass = classmethod(acceptance.PhoneBrowserAcceptance.tearDownClass.__func__)
    setUp = acceptance.PhoneBrowserAcceptance.setUp
    tearDown = acceptance.PhoneBrowserAcceptance.tearDown

    def readiness(self):
        return {'state': 'partial', 'partial': True, 'completed': False, 'sections': [
            {'name': 'gallery', 'reason': 'ready', 'ready': True, 'visited': True, 'found': True},
            {'name': 'description', 'reason': 'not_visited_budget', 'ready': False, 'visited': False, 'found': None},
            {'name': 'characteristics', 'reason': 'not_found', 'ready': False, 'visited': True, 'found': False}],
            'warnings': ['PRIVATE_CAPTURE_MUST_NOT_APPEAR']}

    def done(self, photo_partial=False):
        return {'state': 'done', 'partial': True, 'sourcePartial': True, 'photoPartial': photo_partial,
                'result_id': self.card_id, 'message': 'Карточка готова', 'sourceReadiness': self.readiness(),
                'progress': {'stage': 'done', 'elapsedSeconds': 7, 'estimateSource': 'none', 'stages': [
                    {'id': 'scroll', 'label': 'Разделы исходной страницы', 'state': 'partial',
                     'quality': 'partial', 'elapsedSeconds': 3}]}}

    def mock_phone_import(self, result):
        job_id = 'd' * 32
        self.page.route('**/api/phone/import', lambda route: route.fulfill(
            status=202, content_type='application/json', body=json.dumps({'job_id': job_id})))
        self.page.route('**/api/phone/jobs/' + job_id, lambda route: route.fulfill(
            status=200, content_type='application/json', body=json.dumps(result, ensure_ascii=False)))
        self.page.locator('#phoneTabs [data-phone-view="import"]').click()
        self.page.locator('#phoneImportUrl').fill('https://www.cian.ru/rent/flat/300691182/')
        self.page.locator('#phoneImportSubmit').click()
        self.page.locator('#phoneJobOpen').wait_for(state='visible')

    def test_phone_source_gap_is_complete_operation_with_warning_and_without_photo_retry(self):
        self.result['listing'] = {'sourceReadiness': self.readiness()}
        local_server.save_result(self.directory, self.result, self.card_id)
        acceptance.PhoneBrowserAcceptance.connect(self)
        self.mock_phone_import(self.done())
        self.assertIn('исходная страница собрана частично', self.page.locator('#phoneJobTitle').inner_text())
        message = self.page.locator('#phoneJobMessage').inner_text()
        self.assertIn('Сбор исходной страницы частичный', message)
        self.assertIn('Описание — проверка не выполнена: исчерпан лимит времени', message)
        self.assertNotIn('PRIVATE', message)
        self.assertTrue(self.page.locator('#phoneJobRestart').is_hidden())
        self.assertEqual(self.page.locator('#phoneJobSteps [data-state="partial"] .job-step-mark').inner_text(), '!')
        self.assertIn('сбор исходной страницы', self.page.locator('#phoneJobEstimate').inner_text())
        self.assertNotIn('подготовка фото', self.page.locator('#phoneJobEstimate').inner_text())
        self.page.locator('#phoneJobOpen').click()
        self.page.wait_for_function("document.querySelector('#phoneDetailStamp').textContent.includes('Сбор исходной страницы частичный')")
        self.assertNotIn('PRIVATE', self.page.locator('#phoneDetailStamp').inner_text())
        acceptance.PhoneBrowserAcceptance.assert_mobile_fit(self)

    def test_both_partial_causes_keep_independent_photo_retry(self):
        acceptance.PhoneBrowserAcceptance.connect(self)
        self.mock_phone_import(self.done(photo_partial=True))
        self.assertTrue(self.page.locator('#phoneJobRestart').is_visible())
        self.assertEqual(self.page.locator('#phoneJobRestart').inner_text(), 'Повторить подготовку фото')
        message = self.page.locator('#phoneJobMessage').inner_text()
        self.assertIn('Сбор исходной страницы частичный', message)
        self.assertIn('Фотографии подготовлены частично', message)
        acceptance.PhoneBrowserAcceptance.assert_mobile_fit(self)

    def test_desktop_done_uses_saved_readiness_if_job_has_no_summary(self):
        result = {**self.result, 'listing': {'sourceReadiness': self.readiness()}}
        job_id = 'e' * 32
        self.page.route('**/api/local/import', lambda route: route.fulfill(
            status=202, content_type='application/json', body=json.dumps({'job_id': job_id})))
        self.page.route('**/api/local/jobs/' + job_id, lambda route: route.fulfill(
            status=200, content_type='application/json', body=json.dumps({'state': 'done', 'partial': True,
                'photoPartial': False, 'result_id': self.card_id, 'message': 'Готово'})))
        self.page.route('**/api/local/results/' + self.card_id, lambda route: route.fulfill(
            status=200, content_type='application/json', body=json.dumps(result, ensure_ascii=False)))
        thread = threading.Thread(target=self.controller.serve_forever, daemon=True)
        thread.start()
        try:
            self.page.goto(self.controller.desktop_access.launch_url(self.controller.server_port))
            self.page.locator('#url').fill('https://www.cian.ru/rent/flat/300691182/')
            self.page.locator('#import').click()
            self.page.wait_for_function("document.querySelector('#importStatus').textContent.includes('Сбор исходной страницы частичный')")
            self.assertIn('Описание — проверка не выполнена', self.page.locator('#review').inner_text())
            self.assertNotIn('PRIVATE', self.page.locator('#importStatus').inner_text())
            self.assertTrue(self.page.locator('#import').is_enabled())
            acceptance.PhoneBrowserAcceptance.assert_mobile_fit(self)
        finally:
            self.controller.shutdown()
            thread.join()

    def test_desktop_manual_save_reload_keeps_source_quality_and_structured_metadata(self):
        metadata = {'sourceReadiness': self.readiness(),
                    'photoItems': [{'url': self.assets[0]['sourceUrl'], 'photoId': '123', 'order': 0,
                                    'isLayout': False, 'isCianLayout': False,
                                    'variants': {'fullUrl': self.assets[0]['sourceUrl']}, 'source': 'CIAN offer'}],
                    'layouts': [{'url': 'https://images.cdn-cian.ru/images/fixture-plan.jpg',
                                 'isLayout': True, 'isCianLayout': True}],
                    'sourceDates': {'created': '2021-04-26T00:00:00Z', 'published': 1619455532,
                                    'updated': None, 'timezone': 'Europe/Moscow'},
                    'transitDetails': [{'id': 9, 'name': 'Лианозово', 'line': 'МЦД', 'mode': 'walk', 'time': 7}]}
        self.result['listing'] = {**metadata, 'capture': {'rawHtml': 'PRIVATE_CAPTURE'}}
        local_server.save_result(self.directory, self.result, self.card_id)
        thread = threading.Thread(target=self.controller.serve_forever, daemon=True)
        thread.start()
        try:
            self.page.goto(self.controller.desktop_access.launch_url(self.controller.server_port,self.card_id))
            self.page.wait_for_function("document.querySelector('#importStatus').textContent.includes('Сохранённая карточка загружена')")
            self.assertIn('Сбор исходной страницы частичный', self.page.locator('#review').inner_text())
            self.page.locator('#area').fill('60')
            with self.page.expect_response(lambda response: response.url.endswith('/api/local/save') and response.request.method == 'POST'):
                self.page.locator('#saveLocal').click()
            self.page.wait_for_function("document.querySelector('#localSaveStatus').textContent.startsWith('Сохранено')")
            saved = local_server.read_result(self.directory, self.card_id)
            self.assertEqual(saved['fields']['area'], '60')
            for key in ('photoItems', 'layouts', 'sourceDates', 'transitDetails'):
                self.assertEqual(saved['fields'][key], metadata[key])
                self.assertEqual(saved['listing'][key], metadata[key])
            self.assertEqual(saved['listing']['sourceReadiness']['state'], 'partial')
            self.assertNotIn('capture', saved['listing'])
            self.assertNotIn('PRIVATE', json.dumps(saved['fields'], ensure_ascii=False))
            draft = self.page.evaluate("JSON.parse(localStorage.getItem('most-draft')).fields")
            self.assertEqual(draft['sourceReadiness']['state'], 'partial')
            self.assertEqual(draft['transitDetails'][0]['id'], 9)
            self.page.reload()
            self.page.wait_for_function("document.querySelector('#importStatus').textContent.includes('Сохранённая карточка загружена')")
            self.assertEqual(self.page.locator('#area').input_value(), '60')
            self.assertIn('Сбор исходной страницы частичный', self.page.locator('#review').inner_text())
            acceptance.PhoneBrowserAcceptance.assert_mobile_fit(self)
        finally:
            self.controller.shutdown()
            thread.join()


if __name__ == '__main__':
    unittest.main()
