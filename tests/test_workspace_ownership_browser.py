"""Optional real Chrome checks of the owner handoff on disposable workspaces."""
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from urllib.parse import urlsplit

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
sys.path.insert(0,str(Path(__file__).resolve().parent))
import local_server
import test_phone_browser as acceptance


class DesktopOwnerBrowser(unittest.TestCase):
    setUpClass=classmethod(acceptance.PhoneBrowserAcceptance.setUpClass.__func__)
    tearDownClass=classmethod(acceptance.PhoneBrowserAcceptance.tearDownClass.__func__)

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.directory=Path(self.temp.name)
        self.server=local_server.create_server(self.directory,0)
        self.start_server()
        self.context=self.browser.new_context(viewport={'width':1280,'height':900})
        self.page=self.context.new_page();self.errors=[];self.calls=[]
        self.page.on('pageerror',lambda error:self.errors.append(str(error)))
        self.page.on('request',lambda request:self.calls.append(urlsplit(request.url).path))

    def start_server(self):
        self.thread=threading.Thread(target=self.server.serve_forever,kwargs={'poll_interval':.01},daemon=True);self.thread.start()
        self.base='http://127.0.0.1:'+str(self.server.server_port)

    def tearDown(self):
        self.context.close();self.server.shutdown();self.server.server_close();self.thread.join();self.temp.cleanup()
        self.assertEqual(self.errors,[])

    def test_anonymous_loopback_page_is_not_an_owner_invitation(self):
        self.page.goto(self.base+'/')
        self.page.locator('#desktopOwnerGate').wait_for()
        self.assertEqual(self.page.locator('meta[name="redcn-token"]').get_attribute('content'),'')
        self.assertFalse(any(path.startswith('/api/local/') for path in self.calls))
        self.assertNotIn(self.server.desktop_access.token,self.page.content())

    def test_owner_fragment_exchange_disappears_and_plain_reload_remains_authenticated(self):
        self.page.goto(self.server.desktop_access.launch_url(self.server.server_port))
        self.page.wait_for_function("!!document.querySelector('meta[name=redcn-token]').content && !location.hash")
        self.assertIn('/api/local/desktop-access',self.calls)
        self.assertFalse(any('redcn-owner' in path for path in self.calls))
        cookie=next(row for row in self.context.cookies() if row['name']==self.server.desktop_access.cookie_name(self.server.server_port))
        self.assertTrue(cookie['httpOnly']);self.assertEqual(cookie['sameSite'],'Strict')
        self.page.reload();self.page.wait_for_function("!!document.querySelector('meta[name=redcn-token]').content")
        self.assertEqual(self.page.locator('#desktopOwnerGate').count(),0)

    def test_owner_cookie_survives_the_same_controller_restart(self):
        self.page.goto(self.server.desktop_access.launch_url(self.server.server_port))
        self.page.wait_for_function("!!document.querySelector('meta[name=redcn-token]').content && !location.hash")
        old=self.server.desktop_access.token;port=self.server.server_port
        self.server.shutdown();self.server.server_close();self.thread.join()
        self.server=local_server.create_server(self.directory,port);self.start_server()
        self.assertEqual(old,self.server.desktop_access.token)
        self.page.goto(self.base+'/')
        self.page.wait_for_function("!!document.querySelector('meta[name=redcn-token]').content")
        self.assertEqual(self.page.locator('#desktopOwnerGate').count(),0)


if __name__=='__main__':unittest.main()
