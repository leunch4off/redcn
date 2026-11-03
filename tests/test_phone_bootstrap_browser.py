"""Startup faults must leave a finite, useful phone connection report.

Every server, cookie and card here belongs to a temporary fixture. These tests
never import a listing or touch the owner's ten-import diagnostic campaign.
"""
import re
import time
import unittest

import test_phone_browser as phone_fixture


class PhoneBootstrapAcceptance(unittest.TestCase):
    setUpClass=classmethod(phone_fixture.PhoneBrowserAcceptance.setUpClass.__func__)
    tearDownClass=classmethod(phone_fixture.PhoneBrowserAcceptance.tearDownClass.__func__)
    tearDown=phone_fixture.PhoneBrowserAcceptance.tearDown

    def setUp(self):
        phone_fixture.PhoneBrowserAcceptance.setUp(self)

    def pair_without_page(self):
        code,_=self.phone.pairing.issue()
        response=self.context.request.post(self.base+'/api/phone/pair',data={'code':code},headers={'Origin':self.base})
        self.assertEqual(response.status,200)

    def wait_for_error(self,started):
        self.page.wait_for_function("document.getElementById('phoneLoading').dataset.result==='error'",timeout=10500)
        self.assertLess(time.monotonic()-started,10.5)
        self.assertTrue(self.page.locator('#phoneLoadRetry').is_visible())
        self.assertFalse(self.page.locator('#phoneLoadRetry').is_disabled())
        self.assertTrue(self.page.locator('#phoneDiagnostics').is_visible())

    def report(self):
        return self.page.locator('#phoneDiagnosticText').inner_text()

    def test_module_syntax_failure_still_reports_error_and_independent_server_probe(self):
        secret='PRIVATE_FRAGMENT_NOT_FOR_DIAGNOSTICS'
        def broken_module(route):
            response=route.fetch()
            body,count=re.subn(r'<script type="module">.*?</script>','<script type="module">const syntaxFailure = ;</script>',response.text(),flags=re.S)
            self.assertEqual(count,1)
            route.fulfill(response=response,body=body)
        self.page.route('**/phone/',broken_module)
        started=time.monotonic()
        self.page.goto(self.base+'/phone/#'+secret)
        self.wait_for_error(started)
        self.page.wait_for_function("document.getElementById('phoneDiagnosticText').textContent.includes('401')",timeout=6000)
        report=self.report()
        self.assertRegex(report,r'SCRIPT_ERROR|BOOT_NOT_STARTED')
        self.assertNotIn(secret,report)
        self.assertNotIn(self.card_id,report)
        self.assertTrue(any('SyntaxError' in error or 'Unexpected token' in error for error in self.errors))
        # A parse fault is deliberately injected; any unrelated fault still fails.
        self.errors[:]=[error for error in self.errors if not ('SyntaxError' in error or 'Unexpected token' in error)]

    def test_silent_state_request_has_a_deadline_and_visible_reason(self):
        pending=[]
        self.page.route('**/api/phone/state',lambda route:pending.append(route))
        started=time.monotonic()
        self.page.goto(self.base+'/phone/')
        self.wait_for_error(started)
        self.assertIn('REQUEST_TIMEOUT',self.report())
        self.assertIn('state',self.report())
        self.assertTrue(pending)
        # Release the deliberately stalled route before fixture/context teardown.
        for route in pending:route.abort()
        self.page.wait_for_timeout(50)

    def test_html_from_external_gateway_is_not_mistaken_for_app_response(self):
        self.page.route('**/api/phone/state',lambda route:route.fulfill(status=200,content_type='text/html',body='<html><h1>Gateway welcome</h1></html>'))
        started=time.monotonic()
        self.page.goto(self.base+'/phone/')
        self.wait_for_error(started)
        self.assertIn('RESPONSE_NOT_JSON',self.report())
        self.assertIn('200',self.report())
        self.assertNotIn('Gateway welcome',self.report())

    def test_card_failure_identifies_card_stage_after_successful_session(self):
        self.pair_without_page()
        self.page.route('**/api/phone/cards*',lambda route:route.fulfill(status=503,content_type='application/json',body='{"error":"Fixture unavailable"}'))
        started=time.monotonic()
        self.page.goto(self.base+'/phone/')
        self.wait_for_error(started)
        self.assertEqual(self.page.locator('#phoneLoadSession').get_attribute('data-state'),'done')
        self.assertEqual(self.page.locator('#phoneLoadCards').get_attribute('data-state'),'error')
        self.assertIn('cards',self.report())
        self.assertIn('503',self.report())

    def test_old_safari_api_and_regexp_limitations_do_not_block_startup(self):
        self.page.add_init_script("""const NativeRegExp=RegExp;
          function LegacyRegExp(pattern,flags){
            if(typeof pattern==='string'&&(/\\(\\?<[:=!]/.test(pattern)))throw new SyntaxError('Unsupported legacy lookbehind');
            return new NativeRegExp(pattern,flags);
          }
          LegacyRegExp.prototype=NativeRegExp.prototype;Object.setPrototypeOf(LegacyRegExp,NativeRegExp);window.RegExp=LegacyRegExp;
          for(const name of ['timeout','any'])Object.defineProperty(AbortSignal,name,{value:undefined,configurable:true});
          Object.defineProperty(performance,'getEntriesByType',{value:undefined,configurable:true});
          Object.defineProperty(Object,'hasOwn',{value:undefined,configurable:true});
          Object.defineProperty(String.prototype,'replaceAll',{value:undefined,configurable:true});""")
        phone_fixture.PhoneBrowserAcceptance.connect(self)
        self.assertEqual(self.page.locator('#phoneLoading').get_attribute('data-result'),'ready')
        self.assertEqual(self.page.locator('#phoneCards .listing-card').count(),1)
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'),393)


if __name__=='__main__':unittest.main()
