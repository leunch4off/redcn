"""Real desktop/paired-phone integration of routes, incoming links and diagnostics.

Every server and card belongs to the temporary fixture. No Cian import or search
is started, so these checks never consume the owner's ten manual diagnostics.
"""
import copy
import threading
import unittest

import test_phone_browser as phone_fixture


class WorkspaceBrowserAcceptance(unittest.TestCase):
    # Refer through the module: importing the TestCase itself under another
    # global name makes unittest discover and rerun the original suite.
    setUpClass=classmethod(phone_fixture.PhoneBrowserAcceptance.setUpClass.__func__)
    tearDownClass=classmethod(phone_fixture.PhoneBrowserAcceptance.tearDownClass.__func__)

    def setUp(self):
        phone_fixture.PhoneBrowserAcceptance.setUp(self)
        self.result['fields'].update({
            'latitude':'55.7558','longitude':'37.6173','address':'Москва, тестовый адрес',
            'universityRoutes':[{'name':'МГУ','campus':'Учебный корпус','campusId':'',
                'address':'Москва, Ленинские горы, 1','latitude':55.702,'longitude':37.530,
                'time':'35–45','mode':'bus','transport':'Автобус 261','routeDistanceKm':'',
                'stops':[{'name':'Университет','latitude':55.692,'longitude':37.534}]}]})
        phone_fixture.local_server.save_result(self.directory,self.result,self.card_id)
        self.desktop_thread=threading.Thread(target=self.controller.serve_forever,daemon=True)
        self.desktop_thread.start();self.desktop='http://127.0.0.1:'+str(self.controller.server_port)
        self.page.add_init_script("localStorage.setItem('most-photo-analyze','false');localStorage.setItem('most-photo-autoload','false')")
        self.route_requests=[]
        self.page.on('request',lambda request:self.route_requests.append(request.url) if request.url.split('?',1)[0].endswith('/phone/routes.mjs') else None)
        # Opening the design tab must not request public map tiles in this test.
        self.page.route('**/api/phone/listing-map',lambda route:route.fulfill(status=400,json={'error':'Карта в этой проверке не загружается'}))

    def tearDown(self):
        self.controller.shutdown();self.desktop_thread.join()
        phone_fixture.PhoneBrowserAcceptance.tearDown(self)

    def read_card(self):return phone_fixture.local_server.read_result(self.directory,self.card_id)

    def open_desktop(self):
        self.page.set_viewport_size({'width':1440,'height':1100})
        self.page.goto(self.desktop+'/listing/'+self.card_id,wait_until='domcontentloaded')
        self.page.wait_for_function("document.documentElement.dataset.editorReady==='true'&&document.getElementById('output').value.includes('两间独立')")

    def pair_phone(self):
        self.page.set_viewport_size({'width':393,'height':852})
        phone_fixture.PhoneBrowserAcceptance.connect(self)

    def assert_fit(self):self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'),393)

    def test_desktop_structured_routes_save_and_workspace_mounts(self):
        self.open_desktop();before=copy.deepcopy(self.read_card())
        route_mount=self.page.locator('#universityRoutesMount')
        self.page.locator('details').filter(has=route_mount).locator('summary').first.click()
        route_mount.locator('.university-route-entry > summary').first.click()
        route_mount.get_by_label('Автобусы / пересадки · маршрут 1',exact=True).fill('Автобус 119 → метро Университет')
        route_mount.get_by_label('Подтверждённое время, мин · маршрут 1',exact=True).fill('45–60')
        route_mount.get_by_label('Проверенная длина маршрута, км · маршрут 1',exact=True).fill('8.5')
        self.assertTrue(route_mount.locator('a[href^="https://www.google.com/maps/dir/"]').count()>0)
        self.page.locator('#saveLocal').click()
        self.page.wait_for_function("document.getElementById('localSaveStatus').textContent.includes('Сохранено')")
        after=self.read_card();row=after['fields']['universityRoutes'][0]
        self.assertEqual(row['transport'],'Автобус 119 → метро Университет');self.assertEqual(row['time'],'45–60');self.assertEqual(float(row['routeDistanceKm']),8.5)
        self.assertEqual((after['body'],after['tags'],after['titles'][0]),(before['body'],before['tags'],before['titles'][0]))
        self.assertEqual(after['fields']['photos'],before['fields']['photos'])
        self.page.reload();self.page.wait_for_function("document.getElementById('output').value.includes('两间独立')")
        self.page.locator('details').filter(has=self.page.locator('#universityRoutesMount')).locator('summary').first.click()
        self.page.locator('#universityRoutesMount .university-route-entry > summary').first.click()
        self.assertEqual(self.page.locator('#universityRoutesMount').get_by_label('Автобусы / пересадки · маршрут 1',exact=True).input_value(),'Автобус 119 → метро Университет')
        self.page.locator('aside nav [data-tab="watch"]').click()
        self.page.locator('#listingWatchMount .listing-watch').wait_for()
        self.page.wait_for_function("document.querySelector('#listingWatchMount .listing-watch-status').textContent.includes('выключена')")
        self.page.locator('aside nav [data-tab="editor"]').click()
        self.page.locator('#importDiagnosticsMount .import-audit').wait_for()
        self.assertEqual(self.controller.import_diagnostics.status()['completed'],0)

    def test_phone_lazy_routes_save_and_all_five_tabs_fit(self):
        before=copy.deepcopy(self.read_card());self.pair_phone();self.assert_fit()
        self.assertEqual(self.page.locator('#phoneTabs [data-phone-view]').count(),5);self.assertEqual(self.route_requests,[])
        self.page.locator('#phoneCards .listing-card').first.click()
        self.page.locator('#phoneRoutesMount .phone-route-controller > summary').wait_for()
        self.assertEqual(self.route_requests,[],'The routes catalog must not load with the initial feed or card')
        self.page.locator('#phoneRoutesMount .phone-route-controller > summary').click()
        self.page.locator('#phoneRoutesMount .university-route-editor').wait_for()
        self.assertEqual(len(self.route_requests),1)
        self.page.locator('#phoneRoutesMount .university-route-entry > summary').first.click()
        self.page.locator('#phoneRoutesMount').get_by_label('Автобусы / пересадки · маршрут 1',exact=True).fill('Автобус 119, пересадка на метро')
        self.page.locator('#phoneRoutesMount').get_by_role('button',name='Сохранить маршруты',exact=True).click()
        self.page.wait_for_function("document.querySelector('#phoneRoutesMount .phone-route-controller > p').textContent.includes('Маршруты сохранены')")
        after=self.read_card();expected=copy.deepcopy(before);expected['fields']['universityRoutes']=after['fields']['universityRoutes'];self.assertEqual(after,expected)
        self.assertEqual(after['fields']['universityRoutes'][0]['transport'],'Автобус 119, пересадка на метро');self.assert_fit()
        for view in ['watch','import','design','setup','cards']:
            self.page.locator('#phoneTabs [data-phone-view="'+view+'"]').click()
            if view=='watch':
                self.page.locator('#phoneWatchMount .listing-watch').wait_for()
                self.page.wait_for_function("document.querySelector('#phoneWatchMount .listing-watch-status').textContent.includes('выключена')")
            elif view=='import':self.page.locator('#phoneImportDiagnostics .import-audit').wait_for()
            elif view=='design':self.page.locator('#phoneDesignMount #designCanvasEditor').wait_for(timeout=30000)
            self.assert_fit()
        for button in self.page.locator('#phoneTabs [data-phone-view]').all():
            box=button.bounding_box();self.assertIsNotNone(box);self.assertGreaterEqual(box['x'],-1);self.assertLessEqual(box['x']+box['width'],394);self.assertGreaterEqual(box['height'],44)
        self.assertEqual(self.controller.import_diagnostics.status()['completed'],0)

    def test_diagnostic_series_is_shared_between_desktop_and_phone_without_imports(self):
        self.open_desktop();audit=self.page.locator('#importDiagnosticsMount')
        audit.locator('.import-audit > summary').click()
        audit.get_by_role('button',name='Начать 10 проверок',exact=True).click()
        self.page.wait_for_function("document.querySelector('#importDiagnosticsMount .import-audit-status').textContent.includes('Следующие ручные')")
        state=self.controller.import_diagnostics.status();self.assertEqual(state['state'],'armed');self.assertEqual(state['target'],10);self.assertEqual(state['completed'],0)
        self.pair_phone();self.page.locator('#phoneTabs [data-phone-view="import"]').click()
        phone_audit=self.page.locator('#phoneImportDiagnostics');phone_audit.locator('.import-audit > summary').click()
        self.page.wait_for_function("document.querySelector('#phoneImportDiagnostics .import-audit-status').textContent.includes('Следующие ручные')")
        self.assertTrue(phone_audit.get_by_role('button',name='Начать 10 проверок',exact=True).is_disabled())
        self.assertTrue(phone_audit.get_by_role('button',name='Скачать tg.zip',exact=True).is_hidden());self.assert_fit()
        self.assertEqual(self.controller.import_diagnostics.status()['completed'],0)


if __name__=='__main__':unittest.main()
