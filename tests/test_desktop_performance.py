"""Cold editor readiness without constructing hidden publication/knowledge panels."""
import threading
import time
import unittest

import test_phone_browser as acceptance


class DesktopPerformanceAcceptance(unittest.TestCase):
    setUpClass=classmethod(acceptance.PhoneBrowserAcceptance.setUpClass.__func__)
    tearDownClass=classmethod(acceptance.PhoneBrowserAcceptance.tearDownClass.__func__)
    setUp=acceptance.PhoneBrowserAcceptance.setUp
    tearDown=acceptance.PhoneBrowserAcceptance.tearDown

    def test_editor_ready_within_ten_seconds_and_optional_panels_load_on_demand(self):
        thread=threading.Thread(target=self.controller.serve_forever,daemon=True);thread.start()
        requests=[]
        self.page.on('request',lambda request:requests.append(request.url))
        try:
            started=time.monotonic()
            self.page.goto('http://127.0.0.1:'+str(self.controller.server_port)+'/',wait_until='domcontentloaded',timeout=10000)
            self.page.wait_for_function("document.documentElement.dataset.editorReady==='true'",timeout=10000)
            elapsed=time.monotonic()-started
            self.assertLess(elapsed,10,'Editor readiness took '+str(elapsed)+' seconds')
            self.assertEqual(self.page.locator('#presentationMount').locator('*').count(),0)
            self.assertEqual(self.page.locator('#geoSummary').inner_text(),'')
            self.assertFalse(any(url.endswith('/api/local/design') or url.endswith('/research.json') for url in requests))
            # The initial screen is interactive before hidden panels are built.
            self.page.locator('#price').fill('80000')
            self.assertEqual(self.page.locator('#price').input_value(),'80000')
            self.page.locator('nav [data-tab="knowledge"]').click()
            self.page.wait_for_function("document.getElementById('geoSummary').textContent.includes('остановочных')")
            self.page.locator('nav [data-tab="presentation"]').click()
            self.page.locator('#presentationRefresh').wait_for(state='visible')
            self.assertGreater(self.page.locator('#presentationMount').locator('*').count(),20)
            print('DESKTOP_EDITOR_READY_SECONDS',round(elapsed,3),flush=True)
        finally:
            self.controller.shutdown();thread.join()


if __name__=='__main__':unittest.main()
