"""Designer transport, session races and separate readiness on disposable servers."""
import time
import unittest

import test_phone_browser as fixture


class PhoneDesignerLoadingBrowserTests(unittest.TestCase):
    setUpClass = classmethod(fixture.PhoneBrowserAcceptance.setUpClass.__func__)
    tearDownClass = classmethod(fixture.PhoneBrowserAcceptance.tearDownClass.__func__)
    setUp = fixture.PhoneBrowserAcceptance.setUp
    tearDown = fixture.PhoneBrowserAcceptance.tearDown

    def open_card(self):
        self.page.locator('#phoneCards .listing-card').first.click()
        self.page.locator('#phoneDetail').wait_for(state='visible')

    def design_button(self):
        return self.page.locator('#phoneTabs [data-phone-view="design"]')

    def wait_editor_ready(self):
        try:
            self.page.wait_for_function("document.getElementById('phoneDesignMount').dataset.editorReady==='true'")
        except Exception:
            print('DESIGNER_FAILURE_DIAGNOSTICS='+str(self.page.evaluate('''() => ({
                dataset:{...document.getElementById('phoneDesignMount').dataset},
                mountText:document.getElementById('phoneDesignMount').textContent.slice(0,1500),
                presentationStatus:document.getElementById('presentationStatus')?.textContent,
                connection:document.getElementById('phoneConnection')?.textContent,
                marks:performance.getEntriesByType('mark').filter(row=>row.name.startsWith('redcn-designer')).map(row=>({name:row.name,start:row.startTime})),
                resources:performance.getEntriesByType('resource').filter(row=>/designer\\.mjs|\\/api\\/phone\\/design$/.test(row.name)).map(row=>({path:new URL(row.name).pathname,start:row.startTime,duration:row.duration,bytes:row.transferSize}))
            })''')), flush=True)
            raise

    def test_selected_card_prewarms_only_public_module_and_open_reads_template_in_parallel(self):
        modules, designs, photos = [], [], []
        self.page.route('**/phone/designer.mjs', lambda route: modules.append(route))
        self.page.route('**/api/phone/design', lambda route: designs.append(route))
        # The card's <img> previews may load normally. Hold only the designer's
        # authenticated fetches so first-canvas and photo redraw readiness can
        # be proved independently, without racing a fast local image response.
        def hold_designer_photo(route):
            if route.request.resource_type == 'fetch':
                photos.append(route)
            else:
                route.continue_()
        self.page.route('**/api/phone/media/*', hold_designer_photo)
        fixture.PhoneBrowserAcceptance.connect(self)
        self.assertEqual(modules, [], 'The initial feed does not need the designer')
        self.open_card()
        self.page.wait_for_function("document.getElementById('phoneDesignMount').dataset.moduleStage==='warming'")
        self.assertEqual(len(modules), 1)
        self.assertEqual(designs, [], 'Prewarming must never cache the private shared template')
        self.design_button().click()
        self.page.wait_for_function("document.getElementById('phoneDesignMount').dataset.editorStage==='loading'")
        self.assertEqual(len(designs), 1, 'Shared-template GET starts before the held module is released')
        self.assertEqual(len(modules), 1, 'The explicit open reuses the in-flight public module')
        self.assertEqual(self.page.locator('#phoneDesignMount').get_attribute('data-editor-ready'), 'false')
        self.assertEqual(self.page.locator('#phoneDesignMount').get_attribute('data-photos-ready'), 'false')
        # Answer the private GET now. Holding it until a cold 4MB module has
        # loaded would manufacture an unrelated eight-second request timeout.
        designs[0].fulfill(json={'template': None, 'updated': None, 'rules': {}})
        self.page.wait_for_function("document.getElementById('phoneDesignMount').dataset.templateStage==='ready'")
        started = time.perf_counter()
        modules[0].continue_()
        self.page.locator('#designCanvasEditor').wait_for()
        self.wait_editor_ready()
        print('COLD_DESIGNER_FIXTURE_EDITOR_MS='+str(round((time.perf_counter()-started)*1000, 1)), flush=True)
        print('DESIGNER_PHASES='+str(self.page.evaluate("({mount:{...document.getElementById('phoneDesignMount').dataset},canvas:{...document.getElementById('designCanvasEditor').dataset}})")), flush=True)
        self.assertEqual(self.page.locator('#designCanvasEditor').get_attribute('data-preview-ready'), 'true')
        first_draws = self.page.locator('#designCanvasEditor').evaluate('node=>Number(node.dataset.drawCount)')
        self.assertGreaterEqual(first_draws, 1, 'Editor-ready requires a genuinely painted canvas')
        self.assertEqual(self.page.locator('#phoneDesignMount').get_attribute('data-photos-ready'), 'false')
        self.page.unroute('**/api/phone/media/*', hold_designer_photo)
        for route in photos:
            route.continue_()
        # Decoded photos invalidate the first preview. Wait for the subsequent
        # real draw, not just the once-set editor-ready milestone.
        self.page.wait_for_function("draws=>document.getElementById('phoneDesignMount').dataset.photosReady==='true'&&document.getElementById('designCanvasEditor').dataset.previewReady==='true'&&Number(document.getElementById('designCanvasEditor').dataset.drawCount)>draws", arg=first_draws)
        self.assertEqual(len(designs), 1, 'The common studio consumes the prefetched read, not a second GET')
        self.assertGreaterEqual(self.page.locator('#phoneDesignMount').evaluate('node=>Number(node.dataset.editorMs)'), 0)
        self.assertTrue(self.page.evaluate("performance.getEntriesByName('redcn-designer-editor-ready').length>0&&performance.getEntriesByName('redcn-designer-photos-ready').length>0"))
        fixture.PhoneBrowserAcceptance.assert_mobile_fit(self)

    def test_warmed_module_does_not_mark_editor_ready_before_shared_template_response(self):
        designs = []
        self.page.route('**/api/phone/design', lambda route: designs.append(route))
        fixture.PhoneBrowserAcceptance.connect(self)
        self.open_card()
        self.page.wait_for_function("document.getElementById('phoneDesignMount').dataset.moduleStage==='ready'")
        self.design_button().click()
        self.page.wait_for_function("document.getElementById('phoneDesignMount').dataset.editorStage==='loading'")
        self.assertEqual(len(designs), 1)
        self.assertEqual(self.page.locator('#phoneDesignMount').get_attribute('data-template-stage'), 'loading')
        self.assertEqual(self.page.locator('#phoneDesignMount').get_attribute('data-editor-ready'), 'false')
        designs[0].fulfill(json={'template': None, 'updated': None, 'rules': {}})
        self.wait_editor_ready()
        self.page.locator('#designCanvasEditor').wait_for()
        self.page.wait_for_function("document.getElementById('phoneDesignMount').dataset.photosReady==='true'&&document.getElementById('designCanvasEditor').dataset.previewReady==='true'")
        self.assertEqual(self.page.locator('#designCanvasEditor').get_attribute('data-preview-ready'), 'true')

    def test_save_data_disables_only_the_automatic_warmup(self):
        self.page.add_init_script("Object.defineProperty(navigator,'connection',{configurable:true,value:{saveData:true}})")
        requests = []
        self.page.on('request', lambda request: requests.append(request.url))
        fixture.PhoneBrowserAcceptance.connect(self)
        self.open_card()
        self.page.wait_for_function("document.querySelector('#phonePhotos img')?.naturalWidth>0")
        self.assertFalse(any(url.endswith('/phone/designer.mjs') for url in requests))
        self.design_button().click()
        self.wait_editor_ready()
        self.assertEqual(sum(url.endswith('/phone/designer.mjs') for url in requests), 1)

    def test_late_template_response_after_logout_is_discarded_and_reconnect_reads_fresh(self):
        held = []
        self.page.route('**/api/phone/design', lambda route: held.append(route))
        fixture.PhoneBrowserAcceptance.connect(self)
        self.open_card()
        self.page.wait_for_function("document.getElementById('phoneDesignMount').dataset.moduleStage==='ready'")
        self.design_button().click()
        self.page.locator('#designCanvasEditor').wait_for()
        self.assertEqual(len(held), 1)
        self.phone.pairing.revoke()
        self.page.locator('#phonePair').wait_for(state='visible', timeout=15000)
        held[0].fulfill(json={'template': None, 'updated': 'old-private-revision', 'rules': {'старое': '旧'}})
        self.page.wait_for_load_state('networkidle')
        self.assertEqual(self.page.locator('#phoneDesignMount').get_attribute('data-editor-ready'), 'false')
        self.assertEqual(self.page.locator('#designCanvasEditor').count(), 0)
        self.page.unroute('**/api/phone/design')
        fresh = []
        def current_design(route):
            fresh.append(route.request.url)
            route.fulfill(json={'template': None, 'updated': 'fresh-revision', 'rules': {}})
        self.page.route('**/api/phone/design', current_design)
        _, code, _ = self.phone.pairing.current()
        self.page.locator('#phonePairCode').fill(code)
        self.page.locator('#phonePairSubmit').click()
        self.page.locator('#phoneCards .listing-card').first.wait_for(state='visible')
        self.open_card()
        self.design_button().click()
        self.wait_editor_ready()
        self.assertEqual(len(fresh), 1)


if __name__ == '__main__':
    unittest.main()
