"""Optional installed-app re-pair acceptance with a separate browser cookie jar."""
import threading
import unittest

import test_phone_browser as acceptance


class InstalledAppPairingAcceptance(unittest.TestCase):
    setUpClass=classmethod(acceptance.PhoneBrowserAcceptance.setUpClass.__func__)
    tearDownClass=classmethod(acceptance.PhoneBrowserAcceptance.tearDownClass.__func__)
    setUp=acceptance.PhoneBrowserAcceptance.setUp
    tearDown=acceptance.PhoneBrowserAcceptance.tearDown

    def test_mac_shows_live_code_and_fresh_installed_app_accepts_it(self):
        manager=self.controller.phone_relay;manager.enable(0)
        desktop_thread=threading.Thread(target=self.controller.serve_forever,daemon=True);desktop_thread.start()
        installed=None
        try:
            self.page.goto('http://127.0.0.1:'+str(self.controller.server_port)+'/')
            self.page.locator('#phoneConnectionShortcut').click()
            self.page.locator('#phonePairManualCode').wait_for(state='visible')
            code=self.page.locator('#phonePairManualCode').inner_text().replace(' ','')
            self.assertRegex(code,r'^[0-9]{8}$')
            self.page.reload();self.page.locator('#phoneConnectionShortcut').click()
            self.assertEqual(self.page.locator('#phonePairManualCode').inner_text().replace(' ',''),code)
            # A new standalone app context has no Safari session. It enters the
            # current visible eight-digit code, without needing the used QR URL.
            installed=self.browser.new_context(viewport={'width':393,'height':852},is_mobile=True,has_touch=True)
            installed.add_init_script("Object.defineProperty(navigator,'standalone',{value:true})")
            app=installed.new_page();base='http://127.0.0.1:'+str(manager.server.server_port)
            app.goto(base+'/phone/')
            app.locator('#phonePair').wait_for(state='visible')
            self.assertIn('8 цифр',app.locator('#phonePairForm').inner_text())
            app.locator('#phonePairCode').fill(code[:4]+' '+code[4:]);app.locator('#phonePairSubmit').click()
            app.locator('#phoneWorkspace').wait_for(state='visible')
            self.assertEqual(installed.request.get(base+'/api/phone/cards').status,200)
            self.page.wait_for_function("document.getElementById('phonePairManualCode').textContent.replaceAll(' ','')!=="+repr(code),timeout=15000)
            self.assertRegex(self.page.locator('#phonePairManualCode').inner_text().replace(' ',''),r'^[0-9]{8}$')
            app.reload();app.locator('#phoneWorkspace').wait_for(state='visible')
            self.assertTrue(app.locator('#phoneInstallTop [data-pwa-install]').is_hidden())
            self.assertLessEqual(app.evaluate('document.documentElement.scrollWidth'),393)
        finally:
            if installed:installed.close()
            self.controller.shutdown();desktop_thread.join()


if __name__=='__main__':unittest.main()
