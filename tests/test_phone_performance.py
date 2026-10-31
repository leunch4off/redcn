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
        self.assertEqual(self.page.locator('#phoneLoading').get_attribute('data-result'),'ready')
        self.assertEqual(self.page.locator('#phoneLoadCards').get_attribute('data-state'),'done')
        self.assertIn('100',self.page.locator('#phoneLoadCardsNote').text_content())
        self.assertFalse(self.page.locator('#phoneLoadDetails').evaluate('node=>node.open'))
        self.assertEqual(self.page.locator('#phoneLoadHost').text_content(),'Локальный адрес: '+self.base.removeprefix('http://'))
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
            self.assertEqual(self.page.locator('#phoneLoadConnection').get_attribute('data-state'),'active')
            self.assertEqual(self.page.locator('#phoneLoadSession').get_attribute('data-state'),'waiting')
            self.assertEqual(self.page.locator('#phoneLoadCards').get_attribute('data-state'),'waiting')
            self.page.locator('#phoneReconnect').wait_for(state='visible',timeout=10500)
            waited=time.perf_counter()-started
            self.assertLess(waited,10,'An unreachable Mac must not leave a 25-second startup spinner')
            self.assertIn('секунд',self.page.locator('#phonePairStatus').inner_text())
            self.assertEqual(self.page.locator('#phoneLoadConnection').get_attribute('data-state'),'error')
            self.assertEqual(self.page.locator('#phoneLoadCards').get_attribute('data-state'),'waiting')
            self.assertTrue(self.page.locator('#phoneLoadRetry').is_visible())
            self.assertTrue(self.page.locator('#phoneLoadDetails').evaluate('node=>node.open'))
            with self.page.expect_response(lambda response:response.url.endswith('/api/phone/state') and response.status==401):
                self.page.locator('#phoneLoadRetry').click()
            self.page.wait_for_function("document.getElementById('phoneConnection').textContent==='Нужно подключение'")
            self.assertTrue(self.page.locator('#phonePairSubmit').is_enabled())
            self.assertTrue(self.page.locator('#phoneReconnect').is_hidden())
            self.assertEqual(self.page.locator('#phoneLoading').get_attribute('data-result'),'code')
            self.assertEqual(self.page.locator('#phoneLoadConnection').get_attribute('data-state'),'done')
            self.assertEqual(self.page.locator('#phoneLoadSession').get_attribute('data-state'),'action')
            self.assertTrue(self.page.locator('#phoneLoadRetry').is_hidden())
        print(f'PHONE_UNREACHABLE_MS={waited*1000:.0f}',flush=True)

    def test_card_loading_error_preserves_completed_connection_and_can_retry(self):
        original=self.phone.RequestHandlerClass.do_GET;calls=[]
        def delayed(handler):
            if handler.path.startswith('/api/phone/cards') and not calls:
                calls.append(True);time.sleep(12)
            original(handler)
        with patch.object(self.phone.RequestHandlerClass,'do_GET',delayed):
            code,_=self.phone.pairing.issue();self.page.goto(self.base+'/phone/pair#'+code)
            self.page.wait_for_function("document.getElementById('phoneLoadCards').dataset.state==='active'")
            self.assertEqual(self.page.locator('#phoneLoadConnection').get_attribute('data-state'),'done')
            self.assertEqual(self.page.locator('#phoneLoadSession').get_attribute('data-state'),'done')
            self.assertNotIn(code,self.page.locator('#phoneLoading').inner_text())
            self.page.locator('#phoneLoadRetry').wait_for(state='visible',timeout=10500)
            self.assertEqual(self.page.locator('#phoneLoadCards').get_attribute('data-state'),'error')
            self.assertEqual(self.page.locator('#phoneLoadConnection').get_attribute('data-state'),'done')
            self.assertTrue(self.page.locator('#phoneWorkspace').is_visible())
            self.page.locator('#phoneLoadRetry').click()
            self.page.locator('#phoneCards .listing-card').first.wait_for()
            self.assertEqual(self.page.locator('#phoneLoading').get_attribute('data-result'),'ready')
            self.assertEqual(self.page.locator('#phoneLoadCards').get_attribute('data-state'),'done')
            self.assertTrue(self.page.locator('#phoneLoadRetry').is_hidden())
            self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'),393)

    def test_html_gateway_error_keeps_http_status_and_html_401_still_requires_login(self):
        private_html='<h1>PRIVATE_PROXY_DETAILS</h1><script>window.untrustedProxyExecuted=true</script>'
        self.page.route('**/api/phone/state',lambda route:route.fulfill(status=502,content_type='text/html',body=private_html))
        self.page.goto(self.base+'/phone/')
        self.page.locator('#phoneLoadRetry').wait_for(state='visible')
        self.assertIn('HTTP 502',self.page.locator('#phoneLoadConnectionNote').inner_text())
        self.assertEqual(self.page.locator('#phoneLoadConnection').get_attribute('data-state'),'error')
        self.assertEqual(self.page.locator('#phoneLoadSession').get_attribute('data-state'),'waiting')
        self.assertNotIn('PRIVATE_PROXY_DETAILS',self.page.locator('body').inner_text())
        self.assertFalse(self.page.evaluate('window.untrustedProxyExecuted===true'))
        self.page.unroute('**/api/phone/state')
        self.page.locator('#phoneLoadRetry').click()
        self.page.wait_for_function("document.getElementById('phoneLoading').dataset.result==='code'")
        acceptance.PhoneBrowserAcceptance.connect(self)
        self.page.route('**/api/phone/state',lambda route:route.fulfill(status=401,content_type='text/html',body=private_html))
        self.page.reload()
        self.page.wait_for_function("document.getElementById('phoneLoading').dataset.result==='code'")
        self.assertTrue(self.page.locator('#phonePair').is_visible())
        self.assertTrue(self.page.locator('#phoneWorkspace').is_hidden())
        self.assertEqual(self.page.locator('#phoneCards .listing-card').count(),0)
        self.assertNotIn('PRIVATE_PROXY_DETAILS',self.page.locator('body').inner_text())

    def test_late_initial_401_cannot_replace_new_pairing_loading_result(self):
        pending=[]
        self.page.route('**/api/phone/state',lambda route:pending.append(route))
        self.page.goto(self.base+'/phone/')
        self.page.wait_for_function("document.getElementById('phoneLoadConnection').dataset.state==='active'")
        code,_=self.phone.pairing.issue()
        self.page.locator('#phonePairCode').fill(code);self.page.locator('#phonePairSubmit').click()
        self.page.wait_for_function("document.getElementById('phoneLoading').dataset.result==='ready'")
        self.assertEqual(len(pending),1)
        with self.page.expect_response(lambda response:response.url.endswith('/api/phone/state') and response.status==401):
            pending[0].fulfill(status=401,content_type='application/json',body='{"error":"old request"}')
        self.page.wait_for_timeout(100)
        self.assertEqual(self.page.locator('#phoneLoading').get_attribute('data-result'),'ready')
        self.assertEqual(self.page.locator('#phoneLoadCards').get_attribute('data-state'),'done')
        self.assertTrue(self.page.locator('#phoneWorkspace').is_visible())
        self.assertEqual(self.page.locator('#phoneCards .listing-card').count(),1)


if __name__=='__main__':unittest.main()
