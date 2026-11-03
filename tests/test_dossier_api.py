"""Authenticated dossier transfer against disposable fixture servers only."""
import base64
import io
import json
from pathlib import Path
import sys
import unittest
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
import card_assets
import local_server
import test_card_api as fixture


class DossierTransferRoutes(unittest.TestCase):
    setUp=fixture.CardDocumentRoutes.setUp
    tearDown=fixture.CardDocumentRoutes.tearDown
    request=fixture.CardDocumentRoutes.request

    def grant(self,role):
        state=self.phone.pairing.list_sessions()
        self.phone.pairing.manage_session(state['sessions'][0]['id'],role=role,expected_revision=state['revision'])

    def test_private_backup_import_and_selected_copy_keep_pairing_csrf_and_new_identity(self):
        self.grant('owner')
        archive='card-archive/'+self.card
        private=b'%PDF-1.4\nSYNTHETIC PRIVATE CONTRACT\n%%EOF'
        self.request(archive,{'action':'update','notes':'SYNTHETIC PRIVATE NOTE'})
        self.request(archive,{'action':'attach','name':'contract.pdf','category':'contract','data':base64.b64encode(private).decode()})
        path='dossier-backup/'+self.card
        self.assertEqual(self.request(path,auth=False)[0],401)
        self.assertEqual(self.request('dossier-options/'+self.card,auth=False)[0],401)
        self.grant('editor')
        self.assertEqual(self.request(path)[0],403,'paired editor cannot export the complete private workspace dossier')
        self.grant('owner')
        status,backup,headers=self.request(path)
        self.assertEqual(status,200);self.assertIn('attachment;',headers['Content-Disposition'])
        with zipfile.ZipFile(io.BytesIO(backup)) as zip:
            manifest=json.loads(zip.read('manifest.json'))
            self.assertEqual(manifest['archive']['notes'],'SYNTHETIC PRIVATE NOTE')
            self.assertTrue(manifest['attachments'])
        encoded={'data':base64.b64encode(backup).decode()}
        self.assertEqual(self.request('dossier-import',encoded,auth=False)[0],401)
        self.assertEqual(self.request('dossier-import',encoded,csrf=False)[0],403)
        status,body,_=self.request('dossier-import',encoded)
        self.assertEqual(status,200,body);new_id=json.loads(body)['result_id'];self.assertNotEqual(new_id,self.card)
        self.assertEqual(card_assets.archive(self.directory,new_id)['notes'],'SYNTHETIC PRIVATE NOTE')
        self.assertEqual(local_server.read_result(self.directory,self.card)['body'],'Description')
        selection={'attachmentIds':[],'exportIds':[]}
        self.assertEqual(self.request('dossier-client/'+self.card,selection,csrf=False)[0],403)
        status,body,_=self.request('dossier-client/'+self.card,selection)
        self.assertEqual(status,200)
        with zipfile.ZipFile(io.BytesIO(body)) as zip:
            self.assertEqual(set(zip.namelist()),{'manifest.json','post.txt'})
            self.assertNotIn(b'PRIVATE',b''.join(zip.read(name) for name in zip.namelist()))


if __name__=='__main__':unittest.main()
