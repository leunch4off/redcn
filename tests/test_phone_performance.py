"""Real-browser budget and incremental feed checks with private fixture data."""
import time
import unittest
from unittest.mock import patch

import test_phone_browser as acceptance
import local_server


class PhonePerformanceAcceptance(unittest.TestCase):
    setUpClass=classmethod(acceptance.PhoneBrowserAcceptance.setUpClass.__func__)
    tearDownClass=classmethod(acceptance.PhoneBrowserAcceptance.tearDownClass.__func__)
    setUp=acceptance.PhoneBrowserAcceptance.setUp
    tearDown=acceptance.PhoneBrowserAcceptance.tearDown

    def test_hundred_cards_start_without_designer_and_unchanged_refresh_keeps_covers(self):
        for number in range(99):
            local_server.save_result(self.directory,{**self.result,'titles':[f'房源 {number:03d}']})
        api_requests=[];image_requests=[]
        self.page.on('request',lambda request:api_requests.append(request.url) if '/api/phone/' in request.url else None)
        self.page.on('request',lambda request:image_requests.append(request.url) if any(segment in request.url for segment in ('/media/','/previews/')) else None)
        started=time.perf_counter();acceptance.PhoneBrowserAcceptance.connect(self)
        self.page.wait_for_function("document.querySelectorAll('#phoneCards .listing-card').length===100")
        ready_ms=(time.perf_counter()-started)*1000
        self.assertLess(ready_ms,10000,'The local fixture feed should be usable within 10 seconds')
        self.assertFalse(any(url.endswith('/state') for url in api_requests),'Pairing already returns the CSRF token')
        self.assertFalse(any(url.endswith('/design') for url in api_requests),'Design is not needed for the initial feed')
        self.assertEqual(self.page.locator('#phoneDesignMount').evaluate('node=>node.childElementCount'),0)
        self.assertEqual(self.page.locator('#phoneArchiveMount').evaluate('node=>node.childElementCount'),0)
        self.assertGreater(self.page.locator('#phoneCards img[loading="lazy"]').count(),90)
        self.page.wait_for_function("document.querySelector('#phoneCards img').naturalWidth>0")
        self.page.wait_for_load_state('networkidle')
        self.page.evaluate("window.__performanceCover=document.querySelector('#phoneCards img')")
        selected=self.page.locator('#phoneCards .listing-card').first.get_attribute('data-card-id')
        before=len(image_requests)
        with self.page.expect_response(lambda response:response.url.split('?')[0].endswith('/api/phone/cards')):
            self.page.locator('#phoneRefresh').click()
        self.assertTrue(self.page.evaluate("document.querySelector('#phoneCards img')===window.__performanceCover"))
        self.assertEqual(len(image_requests),before,'An unchanged feed must not restart image downloads')
        result=local_server.read_result(self.directory,selected);result['titles']=['更新后的房源标题']
        local_server.save_result(self.directory,result,selected)
        with self.page.expect_response(lambda response:response.url.split('?')[0].endswith('/api/phone/cards')):
            self.page.locator('#phoneRefresh').click()
        self.page.locator('#phoneCards').get_by_text('更新后的房源标题',exact=True).wait_for()
        self.assertTrue(self.page.locator('[data-card-id="'+selected+'"] img').evaluate('image=>image===window.__performanceCover'))
        self.page.locator('#phoneTabs [data-phone-view="design"]').click()
        self.page.locator('#presentationRefresh').wait_for()
        self.assertTrue(any(url.endswith('/design') for url in api_requests))
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'),393)
        print(f'PHONE_READY_100_CARDS_MS={ready_ms:.0f}',flush=True)

    def test_slow_connection_stops_waiting_before_ten_seconds_and_can_retry(self):
        original=self.phone.RequestHandlerClass.do_GET;calls=[]
        def delayed(handler):
            if handler.path=='/api/phone/state' and not calls:
                calls.append(True);time.sleep(12)
            original(handler)
        with patch.object(self.phone.RequestHandlerClass,'do_GET',delayed):
            started=time.perf_counter();self.page.goto(self.base+'/phone/')
            self.page.locator('#phoneReconnect').wait_for(state='visible',timeout=10500)
            waited=time.perf_counter()-started
            self.assertLess(waited,10,'An unreachable Mac must not leave a 25-second startup spinner')
            self.assertIn('секунд',self.page.locator('#phonePairStatus').inner_text())
            with self.page.expect_response(lambda response:response.url.endswith('/api/phone/state') and response.status==401):
                self.page.locator('#phoneReconnect').click()
            self.page.wait_for_function("document.getElementById('phoneConnection').textContent==='Нужно подключение'")
            self.assertTrue(self.page.locator('#phonePairSubmit').is_enabled())
            self.assertTrue(self.page.locator('#phoneReconnect').is_hidden())
        print(f'PHONE_UNREACHABLE_MS={waited*1000:.0f}',flush=True)


if __name__=='__main__':unittest.main()
