"""Desktop token/Origin boundaries against generated cache data only."""
import base64
import json
from pathlib import Path
import re
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.request
import urllib.error

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
import local_server
import workspace_backup


class WorkspaceBackupHTTP(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.directory=Path(self.temp.name)
        self.server=local_server.create_server(self.directory,0)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base=f'http://127.0.0.1:{self.server.server_port}'
        page=urllib.request.urlopen(self.base+'/').read().decode()
        self.token=self.server.desktop_access.token
        local_server.save_result(self.directory,{'fields':{'price':'80000','area':'40'},'titles':['合成测试'],'body':'仅供自动测试','tags':'#测试'})

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.temp.cleanup()

    def request(self,path,data=None,token=True,extra=None):
        request=urllib.request.Request(self.base+'/api/local/'+path,
            data=json.dumps(data).encode() if data is not None else None,
            headers={'Content-Type':'application/json',**({'X-Redcn-Token':self.token} if token else {}),**(extra or {})})
        return urllib.request.urlopen(request)

    def test_missing_desktop_token_or_wrong_origin_never_starts_private_export(self):
        with patch.object(workspace_backup,'export_workspace') as export:
            for token,extra in [(False,{}),(True,{'Origin':'https://other.example'})]:
                with self.assertRaises(urllib.error.HTTPError) as caught:self.request('workspace-backup.zip',token=token,extra=extra)
                self.assertEqual(caught.exception.code,403)
            export.assert_not_called()

    def test_authenticated_download_and_read_only_inspection_then_new_folder_restore(self):
        with self.request('workspace-backup.zip') as response:
            self.assertEqual(response.status,200);self.assertEqual(response.headers.get_content_type(),'application/zip')
            content=response.read()
        before=workspace_backup._cards(self.directory)
        with self.request('workspace-backup/inspect',{'data':base64.b64encode(content).decode()}) as response:
            receipt=json.loads(response.read());self.assertEqual(receipt['cards'],1)
        self.assertEqual(workspace_backup._cards(self.directory),before)
        destination=self.directory/'fresh-isolated-library'
        self.assertEqual(len(workspace_backup.restore_workspace(content,destination)['cards']),1)
        self.assertFalse((destination/'phone-access.json').exists())
        with self.assertRaises(urllib.error.HTTPError) as caught:self.request('workspace-backup/inspect',{'data':'!!!'})
        self.assertEqual(caught.exception.code,400)


if __name__=='__main__':unittest.main()
