import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
import card_assets
import client_bundle_consistency as consistency
import dossier_backup
from local_server import save_result,read_result

HTML='<!doctype html><html><head><meta charset="utf-8"></head><body><h1>公寓</h1><p>月租80,000₽</p></body></html>'


class ClientBundleConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.card=save_result(self.root,{'fields':{'price':'80000','commission':'50','internal':'private'},'titles':['公寓'],'body':'月租80,000₽','tags':'#莫斯科租房'})
    def tearDown(self):self.temp.cleanup()
    def edit(self):
        data=read_result(self.root,self.card);data['fields']['price']='81000';data['body']='月租81,000₽';save_result(self.root,data,self.card)

    def test_old_material_is_preserved_but_cannot_enter_current_client_zip(self):
        exported=card_assets.create_export(self.root,self.card,HTML,'html')
        self.assertEqual(exported['revisionStatus'],'current')
        with card_assets._db(self.root) as db:digest=db.execute('SELECT digest FROM exports WHERE id=?',(exported['id'],)).fetchone()[0]
        self.edit()
        status=card_assets.list_exports(self.root,self.card)[0]
        self.assertEqual(status['revisionStatus'],'stale');self.assertTrue(status['requiresRegeneration'])
        self.assertTrue((self.root/'card-assets'/digest).is_file())
        with self.assertRaises(ValueError):dossier_backup.export_client(self.root,self.card,{'attachmentIds':[],'exportIds':[exported['id']]})

    def test_edit_during_pdf_generation_cannot_certify_mixed_revision(self):
        def render(*args,**kwargs):self.edit();return b'%PDF-1.4\n%%EOF'
        with patch.object(card_assets,'_render_pdf',side_effect=render):
            with self.assertRaises(consistency.ClientRevisionConflict):card_assets.create_export(self.root,self.card,HTML,'pdf')
        self.assertEqual(card_assets.list_exports(self.root,self.card),[])

    def test_zip_checks_revision_after_assembly_and_current_post_excludes_private_fields(self):
        exported=card_assets.create_export(self.root,self.card,HTML,'html')
        selection={'attachmentIds':[],'exportIds':[exported['id']]}
        archive=dossier_backup.export_client(self.root,self.card,selection)
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            manifest=json.loads(bundle.read('manifest.json'));post=bundle.read('post.txt').decode()
            self.assertEqual(manifest['cardRevisionSha256'],exported['revisionSha256'])
            self.assertNotIn('private',post);self.assertNotIn('commission',post)
        original=dossier_backup._make_zip
        def late(*args,**kwargs):out=original(*args,**kwargs);self.edit();return out
        with patch.object(dossier_backup,'_make_zip',side_effect=late):
            with self.assertRaises(consistency.ClientRevisionConflict):dossier_backup.export_client(self.root,self.card,selection)

    def test_old_and_tampered_revision_records_are_not_implicitly_trusted(self):
        exported=card_assets.create_export(self.root,self.card,HTML,'html')
        with card_assets._db(self.root) as db:db.execute('UPDATE export_card_revisions SET artifact_sha=? WHERE export_id=?',('0'*64,exported['id']))
        self.assertEqual(card_assets.list_exports(self.root,self.card)[0]['revisionStatus'],'unverified')
        with self.assertRaises(ValueError):card_assets.export_asset(self.root,exported['id'])

    def test_http_dispatch_rejects_missing_and_stale_request_revision(self):
        from card_api import dispatch
        from phone_relay import public_card
        request={'result_id':self.card,'kind':'html','html':HTML}
        self.assertEqual(dispatch(self.root,'card-export',request)[0],409)
        loaded=public_card(self.root,self.card)['presentationReceipt']
        self.edit()
        self.assertEqual(dispatch(self.root,'card-export',{**request,'expectedRevision':loaded['revisionSha256'],'sourceReceipt':loaded})[0],409)
        self.assertEqual(card_assets.list_exports(self.root,self.card),[])
        fresh=public_card(self.root,self.card)['presentationReceipt']
        result=dispatch(self.root,'card-export',{**request,'html':HTML.replace('80,000','81,000'),'expectedRevision':fresh['revisionSha256'],'sourceReceipt':fresh})
        self.assertEqual(result[0],200);self.assertEqual(result[1]['revisionSha256'],fresh['revisionSha256'])

    def test_stale_phone_card_cannot_borrow_new_revision_from_export_list(self):
        from card_api import dispatch
        from phone_relay import public_card
        loaded=public_card(self.root,self.card)
        self.assertEqual(loaded['body'],'月租80,000₽')
        old=loaded['presentationReceipt']
        self.edit()
        latest=dispatch(self.root,'card-exports/'+self.card)[1]['revisionSha256']
        request={'result_id':self.card,'kind':'html','html':HTML,'expectedRevision':latest}
        # Exact reported reproducer: latest list token + already rendered HTML.
        self.assertEqual(dispatch(self.root,'card-export',request)[0],409)
        # Copying the old source receipt onto that newer token also fails.
        self.assertEqual(dispatch(self.root,'card-export',{**request,'sourceReceipt':old})[0],409)
        self.assertEqual(card_assets.list_exports(self.root,self.card),[])
        current=public_card(self.root,self.card)
        fresh=current['presentationReceipt']
        result=dispatch(self.root,'card-export',{**request,'html':HTML.replace('80,000','81,000'),'sourceReceipt':fresh})
        self.assertEqual(result[0],200,result[1])
        content=dossier_backup.export_client(self.root,self.card,{'attachmentIds':[],'exportIds':[result[1]['id']]})
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            for name in ['post.txt',next(name for name in archive.namelist() if name.startswith('materials/'))]:
                text=archive.read(name).decode();self.assertIn('81,000',text);self.assertNotIn('80,000',text)

    def test_loaded_receipt_is_not_issued_for_a_payload_changed_during_media_read(self):
        import phone_relay
        real=phone_relay.card_media
        def interrupted(*args,**kwargs):
            media=real(*args,**kwargs);self.edit();return media
        with patch.object(phone_relay,'card_media',side_effect=interrupted):
            with self.assertRaises(consistency.ClientRevisionConflict):phone_relay.public_card(self.root,self.card)

    def test_saved_payload_and_card_identity_are_part_of_the_receipt_boundary(self):
        old=read_result(self.root,self.card);revision=consistency.snapshot(self.root,self.card)
        receipt=consistency.source_receipt(self.root,self.card,revision)
        other=save_result(self.root,old)
        with self.assertRaises(consistency.ClientRevisionConflict):consistency.verify_source_receipt(self.root,other,consistency.snapshot(self.root,other),receipt)
        self.edit()
        with self.assertRaises(consistency.ClientRevisionConflict):consistency.snapshot(self.root,self.card,expected_card=old)

    def test_http_export_rechecks_loaded_source_after_actual_render_boundary(self):
        from card_api import dispatch
        from phone_relay import public_card
        receipt=public_card(self.root,self.card)['presentationReceipt']
        def render(*args,**kwargs):self.edit();return b'%PDF-1.4\n%%EOF'
        with patch.object(card_assets,'_render_pdf',side_effect=render):
            result=dispatch(self.root,'card-export',{'result_id':self.card,'kind':'pdf','html':HTML,'expectedRevision':receipt['revisionSha256'],'sourceReceipt':receipt})
        self.assertEqual(result[0],409);self.assertEqual(result[1]['code'],'CLIENT_REVISION_CHANGED')
        self.assertEqual(card_assets.list_exports(self.root,self.card),[])


if __name__=='__main__':unittest.main()
