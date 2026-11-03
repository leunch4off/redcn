"""Exercise built shells without ES2022 hasOwn/at APIs or global polyfills."""
import json
import threading
import unittest

import test_phone_browser as fixture
import local_server


class OwnPropertyBrowserTests(unittest.TestCase):
    setUpClass = classmethod(fixture.PhoneBrowserAcceptance.setUpClass.__func__)
    tearDownClass = classmethod(fixture.PhoneBrowserAcceptance.tearDownClass.__func__)
    tearDown = fixture.PhoneBrowserAcceptance.tearDown

    def setUp(self):
        fixture.PhoneBrowserAcceptance.setUp(self)
        self.context.add_init_script("""for (const [target,key] of [
            [Object,'hasOwn'],[Array.prototype,'at'],[String.prototype,'at']])
            Object.defineProperty(target,key,{configurable:true,writable:true,value:undefined});""")
        self.result['fields'].update({'layout': 'two', 'rooms': '2', 'universityRoutes': [
            {'name': 'МГУ', 'mode': 'bus', 'time': '30', 'transport': '261'}]})
        schema = {
            'version': 1, 'state': 'partial', 'platform': 'cian',
            'reasons': ['unknown-schema', 'field-conflict', 'constructor', 'PRIVATE_REASON'],
            'missingCritical': [], 'criticalCoverage': {'price': True, 'area': True, 'layout': True, 'description': True},
            'privateReviewFragment': 'PRIVATE_CAPTURE'}
        self.result['listing'] = {'sourceSchemaHealth': schema, 'sourceReadiness': {
            'state': 'partial', 'partial': True, 'completed': False, 'sections': [], 'schema': schema}}
        local_server.save_result(self.directory, self.result, self.card_id)

    def assert_missing_native(self):
        self.assertEqual(self.page.evaluate("[typeof Object.hasOwn,typeof Array.prototype.at,typeof String.prototype.at]"),
                         ['undefined', 'undefined', 'undefined'])

    def test_desktop_saved_card_schema_warning_survives_edit_and_reload_without_es2022_apis(self):
        thread = threading.Thread(target=self.controller.serve_forever, daemon=True)
        thread.start()
        try:
            self.page.goto(self.controller.desktop_access.launch_url(self.controller.server_port, self.card_id))
            self.page.wait_for_function("document.querySelector('#importStatus').textContent.includes('Сохранённая карточка загружена')")
            self.assert_missing_native()
            self.assertIn('Форма страницы источника изменилась', self.page.locator('#review').inner_text())
            self.assertNotIn('PRIVATE', self.page.locator('#review').inner_text())
            self.page.locator('#area').fill('61')
            with self.page.expect_response(lambda response: response.url.endswith('/api/local/save') and response.request.method == 'POST'):
                self.page.locator('#saveLocal').click()
            self.page.wait_for_function("document.querySelector('#localSaveStatus').textContent.startsWith('Сохранено')")
            saved = local_server.read_result(self.directory, self.card_id)
            self.assertEqual(saved['fields']['area'], '61')
            self.assertEqual(saved['fields']['sourceSchemaHealth']['reasons'], ['unknown-schema', 'field-conflict'])
            self.assertNotIn('PRIVATE', json.dumps(saved['fields']))
            self.page.reload()
            self.page.wait_for_function("document.querySelector('#importStatus').textContent.includes('Сохранённая карточка загружена')")
            self.assertEqual(self.page.locator('#area').input_value(), '61')
            self.assertIn('противоречивые сведения', self.page.locator('#review').inner_text())
            self.assert_missing_native()
        finally:
            self.controller.shutdown()
            thread.join()

    def test_phone_schema_card_and_lazy_routes_and_designer_work_without_es2022_apis(self):
        fixture.PhoneBrowserAcceptance.connect(self)
        self.assert_missing_native()
        self.page.locator('#phoneCards .listing-card').first.click()
        self.page.locator('#phoneDetail').wait_for(state='visible')
        self.page.wait_for_function("document.getElementById('phoneDetailStamp').textContent.includes('Форма страницы источника изменилась')")
        self.assertNotIn('PRIVATE', self.page.locator('#phoneDetailStamp').inner_text())
        routes = self.page.locator('.phone-route-controller > summary')
        routes.click()
        self.page.locator('.phone-route-controller .university-route-entry').first.wait_for(state='visible')
        self.page.locator('#phoneTabs [data-phone-view="design"]').click()
        self.page.wait_for_function("document.getElementById('phoneDesignMount').dataset.editorReady==='true'")
        self.assertEqual(self.page.locator('#designCanvasEditor').get_attribute('data-preview-ready'), 'true')
        self.assert_missing_native()


if __name__ == '__main__':
    unittest.main()
