"""Only isolated generated images/contracts; no user dossiers are read."""
import base64
import copy
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
import card_assets
import card_api
import dossier_backup
import photo_store
from node_runtime import resolve_node

ROOT=Path(__file__).resolve().parents[1]
HTML='<!doctype html><html><head><meta charset="utf-8"></head><body><p>租金80,000₽</p></body></html>'


def image_bytes(color='#778899'):
    from PIL import Image
    output=io.BytesIO();image=Image.new('RGB',(640,480),color);image.save(output,'PNG');image.close()
    return output.getvalue()


def add_card(directory,card_id,photos=None):
    result={'fields':{'price':'80000','deposit':'40000','photos':photos or [],'photoEdits':{'originals':[]}},'titles':['房源'],'body':'租金80,000₽\n中介费50%','tags':'#莫斯科租房'}
    with sqlite3.connect(Path(directory)/'listings.sqlite3') as db:
        db.execute('CREATE TABLE IF NOT EXISTS results(id TEXT PRIMARY KEY,updated REAL,payload TEXT)')
        db.execute('INSERT INTO results VALUES(?,?,?)',(card_id,1,json.dumps(result,ensure_ascii=False)))
    return result


class DossierBackupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        script="import {defaultPublicationDesign} from './web/publication-model.mjs';process.stdout.write(JSON.stringify(defaultPublicationDesign()))"
        cls.template=json.loads(subprocess.check_output([resolve_node(),'--input-type=module','-e',script],cwd=ROOT,text=True))

    def setUp(self):
        self.source=tempfile.TemporaryDirectory();self.target=tempfile.TemporaryDirectory()
        self.a=Path(self.source.name);self.b=Path(self.target.name);self.card='a'*32
        self.url='https://images.cdn-cian.ru/images/isolated-fixture.jpg'
        self.original=photo_store.attach_photo(self.a,self.url,'data:image/png;base64,'+base64.b64encode(image_bytes()).decode())
        self.crop=photo_store.crop_photo(self.a,self.original['id'],{'left':5})
        self.raw=add_card(self.a,self.card,[self.url])
        card_assets.update_archive(self.a,self.card,{'status':'rented','notes':'PRIVATE NOTE FOR TEST'})
        self.contract=card_assets.add_attachment(self.a,self.card,{'name':'contract.pdf','category':'contract','data':base64.b64encode(b'%PDF-1.4\nPRIVATE-CONTRACT\n%%EOF').decode()})['attachments'][0]
        self.plan=card_assets.add_attachment(self.a,self.card,{'name':'floorplan.obj','category':'floorplan','data':base64.b64encode(b'v 0 0 0\nv 1 0 0\n').decode()})['attachments'][-1]
        # The source's private body is deliberately not currently exportable.
        self.export=card_assets.create_export(self.a,self.card,HTML,'html',template=self.template)
        card_assets.save_design(self.a,self.template)

    def tearDown(self):self.source.cleanup();self.target.cleanup()

    def stored(self,directory,card_id):
        with sqlite3.connect(Path(directory)/'listings.sqlite3') as db:return json.loads(db.execute('SELECT payload FROM results WHERE id=?',(card_id,)).fetchone()[0])

    def count(self,directory):
        path=Path(directory)/'listings.sqlite3'
        if not path.exists():return 0
        with sqlite3.connect(path) as db:
            try:return db.execute('SELECT COUNT(*) FROM results').fetchone()[0]
            except sqlite3.OperationalError:return 0

    def rebuild(self,bundle,mutate=None,extra=None):
        with zipfile.ZipFile(io.BytesIO(bundle)) as source:
            values={name:source.read(name) for name in source.namelist()}
        manifest=json.loads(values['manifest.json'])
        if mutate:mutate(manifest)
        values['manifest.json']=json.dumps(manifest,ensure_ascii=False).encode()
        if extra:values.update(extra)
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_DEFLATED) as target:
            for name,value in values.items():target.writestr(name,value)
        return out.getvalue()

    def test_full_private_transfer_retains_card_attachments_photos_crop_and_design_without_overwrite(self):
        old_global={'version':1,'preset':'minimal','accent':'#123456'};card_assets.save_design(self.b,old_global)
        existing=add_card(self.b,'b'*32)
        bundle=dossier_backup.export_private(self.a,self.card)
        result=dossier_backup.restore_private(self.b,bundle)
        self.assertNotEqual(result['result_id'],self.card);self.assertEqual(self.count(self.b),2)
        restored=self.stored(self.b,result['result_id']);self.assertEqual(restored['body'],self.raw['body'])
        self.assertEqual(self.stored(self.b,'b'*32),existing)
        self.assertEqual(card_assets.load_design(self.b)['template'],old_global)
        self.assertEqual(dossier_backup.get_restored_design(self.b,result['result_id'])['template'],self.template)
        archive=card_assets.archive(self.b,result['result_id']);self.assertEqual(archive['notes'],'PRIVATE NOTE FOR TEST');self.assertEqual(archive['status'],'rented')
        self.assertNotEqual(archive['attachments'][0]['id'],self.contract['id'])
        self.assertEqual({row['name'] for row in archive['attachments']},{'contract.pdf','floorplan.obj'})
        self.assertEqual(photo_store.asset(self.a,self.original['id'])[0],photo_store.asset(self.b,self.original['id'])[0])
        self.assertEqual(photo_store.accepted_photo_for_url(self.b,self.url)['selected']['id'],self.crop['id'])
        exports=card_assets.list_exports(self.b,result['result_id']);self.assertEqual(len(exports),1)
        self.assertTrue(exports[0]['requiresRegeneration'],'import does not certify arbitrary historical export bytes')
        with card_assets._db(self.b) as db:
            self.assertEqual(json.loads(db.execute('SELECT payload FROM export_design_snapshots WHERE export_id=?',(exports[0]['id'],)).fetchone()[0]),self.template)
        with self.assertRaises(ValueError):card_assets.export_asset(self.b,exports[0]['id'])
        again=dossier_backup.restore_private(self.b,bundle)
        self.assertNotEqual(result['result_id'],again['result_id']);self.assertEqual(self.count(self.b),3)

    def test_private_backup_does_not_include_other_cards_files_or_auth_databases(self):
        add_card(self.a,'c'*32)
        card_assets.add_attachment(self.a,'c'*32,{'name':'other.txt','category':'other','data':base64.b64encode(b'UNRELATED-PRIVATE').decode()})
        bundle=dossier_backup.export_private(self.a,self.card)
        with zipfile.ZipFile(io.BytesIO(bundle)) as zip:
            all_bytes=b''.join(zip.read(name) for name in zip.namelist())
            self.assertNotIn(b'UNRELATED-PRIVATE',all_bytes)
            self.assertTrue(all(name=='manifest.json' or name.startswith('blobs/') for name in zip.namelist()))

    def test_brand_image_ids_are_remapped_and_original_bytes_are_retained(self):
        logo=card_assets.add_design_image(self.a,{'role':'logo','name':'logo.png','data':'data:image/png;base64,'+base64.b64encode(image_bytes('#aa2288')).decode()})
        design=copy.deepcopy(self.template);design['brand']['logoAssetId']=logo['id'];card_assets.save_design(self.a,design)
        result=dossier_backup.restore_private(self.b,dossier_backup.export_private(self.a,self.card))
        saved=dossier_backup.get_restored_design(self.b,result['result_id'])['template'];new_logo=saved['brand']['logoAssetId']
        self.assertNotEqual(new_logo,logo['id']);self.assertEqual(card_assets.design_image(self.a,logo['id'])['dataUrl'],card_assets.design_image(self.b,new_logo)['dataUrl'])

    def test_source_photo_url_collision_rejects_without_overwriting_existing_asset(self):
        other=photo_store.attach_photo(self.b,self.url,'data:image/png;base64,'+base64.b64encode(image_bytes('#001122')).decode())
        with self.assertRaisesRegex(ValueError,'другая версия'):dossier_backup.restore_private(self.b,dossier_backup.export_private(self.a,self.card))
        self.assertEqual(self.count(self.b),0);self.assertEqual(photo_store.accepted_photo_for_url(self.b,self.url)['original']['id'],other['id'])

    def test_separate_restores_preserve_crop_links_when_crop_bytes_are_another_cards_original(self):
        from PIL import Image
        source=Image.new('RGB',(700,480),'#888888');source.paste('#aa1111',(0,0,70,480))
        source_bytes=io.BytesIO();source.save(source_bytes,'PNG');source.close()
        other=Image.new('RGB',(630,480),'#888888');other_bytes=io.BytesIO();other.save(other_bytes,'JPEG',quality=95,subsampling=0);other.close()
        source_url='https://images.cdn-cian.ru/images/transfer-crop-source.jpg'
        other_url='https://images.cdn-cian.ru/images/transfer-other-original.jpg'
        original=photo_store.attach_photo(self.a,source_url,'data:image/png;base64,'+base64.b64encode(source_bytes.getvalue()).decode())
        other_original=photo_store.attach_photo(self.a,other_url,'data:image/jpeg;base64,'+base64.b64encode(other_bytes.getvalue()).decode())
        cropped=photo_store.crop_photo(self.a,original['id'],{'left':10})
        self.assertEqual(cropped['id'],other_original['id'])
        add_card(self.a,'d'*32,[source_url]);add_card(self.a,'e'*32,[other_url])
        dossier_backup.restore_private(self.b,dossier_backup.export_private(self.a,'d'*32))
        dossier_backup.restore_private(self.b,dossier_backup.export_private(self.a,'e'*32))
        self.assertEqual(photo_store.asset(self.b,other_original['id'])[2]['kind'],'original')
        source_selection=photo_store.accepted_photo_for_url(self.b,source_url)
        self.assertEqual(source_selection['reason'],'manual');self.assertEqual(source_selection['selected']['id'],other_original['id'])
        self.assertEqual(photo_store.accepted_photo_for_url(self.b,other_url)['reason'],'original')

    def test_missing_original_prevents_claiming_a_complete_backup(self):
        (self.a/'media'/self.original['id']).unlink()
        with self.assertRaises((OSError,ValueError)):dossier_backup.export_private(self.a,self.card)

    def test_tampered_hash_extra_file_traversal_and_duplicate_paths_reject_before_rows(self):
        bundle=dossier_backup.export_private(self.a,self.card)
        corruptions=[self.rebuild(bundle,lambda m:m['files'][0].update(sha256='0'*64)),
                     self.rebuild(bundle,extra={'../escape.txt':b'NO'}),self.rebuild(bundle,extra={'unlisted.txt':b'NO'}),
                     self.rebuild(bundle,lambda m:m.update(type='redcn-client-dossier')),
                     self.rebuild(bundle,lambda m:m['attachments'][0].update(name='../contract.pdf'))]
        for raw in corruptions:
            with self.subTest(size=len(raw)),self.assertRaises(ValueError):dossier_backup.restore_private(self.b,raw)
            self.assertEqual(self.count(self.b),0)
        with patch.object(dossier_backup,'MAX_UNPACKED',10),self.assertRaises(ValueError):dossier_backup.restore_private(self.b,bundle)

    def test_client_copy_defaults_to_post_only_and_never_contains_notes_contract_or_raw_fees(self):
        bundle=dossier_backup.export_client(self.a,self.card,{'attachmentIds':[],'exportIds':[]})
        with zipfile.ZipFile(io.BytesIO(bundle)) as zip:
            self.assertEqual(set(zip.namelist()),{'manifest.json','post.txt'})
            content=b''.join(zip.read(name) for name in zip.namelist())
            self.assertNotIn(b'PRIVATE',content);self.assertNotIn('中介费'.encode(),content)
            self.assertIn('80,000'.encode(),content)

    def test_client_copy_requires_explicit_own_noncontract_selection_and_current_export_policy(self):
        bundle=dossier_backup.export_client(self.a,self.card,{'attachmentIds':[self.plan['id']],'exportIds':[self.export['id']]})
        with zipfile.ZipFile(io.BytesIO(bundle)) as zip:
            self.assertEqual(len(zip.namelist()),4)
            data=b''.join(zip.read(name) for name in zip.namelist());self.assertNotIn(b'PRIVATE-CONTRACT',data);self.assertIn(b'v 0 0 0',data)
        for selection in ({'attachmentIds':[self.contract['id']],'exportIds':[]},{'attachmentIds':['f'*32],'exportIds':[]},{'attachmentIds':[{}],'exportIds':[]}):
            with self.subTest(selection=selection),self.assertRaises(ValueError):dossier_backup.export_client(self.a,self.card,selection)

    def test_dispatch_requires_explicit_routes_and_produces_a_new_card(self):
        status,bundle,mime,headers=card_api.dispatch(self.a,'dossier-backup/'+self.card)
        self.assertEqual((status,mime),(200,'application/zip'));self.assertIn('attachment;',headers['Content-Disposition'])
        response=card_api.dispatch(self.b,'dossier-import',{'data':base64.b64encode(bundle).decode()})
        self.assertEqual(response[0],200);self.assertNotEqual(response[1]['result_id'],self.card)
        self.assertEqual(card_api.request_limit('dossier-import'),179_000_000)
        self.assertFalse(card_api.dispatch(self.a,'dossier-options/'+self.card)[1]['contractsIncluded'])

    def test_fact_history_is_preserved_as_imported_evidence_and_new_owner_snapshot(self):
        from fact_ledger import record_fact_revision,list_fact_revisions
        with sqlite3.connect(self.a/'listings.sqlite3') as db:
            record_fact_revision(db,self.card,{'price':'70000'},actor='operator-one',kind='manual')
            record_fact_revision(db,self.card,self.raw['fields'],actor='operator-two',kind='manual')
        result=dossier_backup.restore_private(self.b,dossier_backup.export_private(self.a,self.card))
        versions=list_fact_revisions(self.b,result['result_id'])
        self.assertEqual(len(versions),2)
        self.assertTrue(all(row['importedFromBackup'] for row in versions))
        self.assertTrue(all(row['resultId']==result['result_id'] for row in versions))
        self.assertEqual({row['sourceActor'] for row in versions},{'operator-one','operator-two'})

    def test_invalid_schema_or_transaction_failure_never_creates_half_a_card(self):
        bundle=dossier_backup.export_private(self.a,self.card)
        malformed=self.rebuild(bundle,lambda manifest:manifest['card']['fields'].update(price=[]))
        with self.assertRaises(ValueError):dossier_backup.restore_private(self.b,malformed)
        self.assertEqual(self.count(self.b),0)
        with patch('fact_ledger.record_fact_revision',side_effect=RuntimeError('synthetic transaction failure')),self.assertRaises(RuntimeError):
            dossier_backup.restore_private(self.b,bundle)
        self.assertEqual(self.count(self.b),0)
        with card_assets._db(self.b) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM attachments').fetchone()[0],0)


class ExactPhotoAndSelectionTests(unittest.TestCase):
    def setUp(self):self.temp=tempfile.TemporaryDirectory();self.directory=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()

    def test_cross_listing_duplicate_uses_actual_sha_and_retains_every_original(self):
        one='https://images.cdn-cian.ru/images/one.jpg';two='https://00.img.avito.st/image/other.jpg'
        data='data:image/png;base64,'+base64.b64encode(image_bytes()).decode()
        a=photo_store.attach_photo(self.directory,one,data);b=photo_store.attach_photo(self.directory,two,data)
        self.assertEqual(a['id'],b['id']);add_card(self.directory,'a'*32,[one,one]);add_card(self.directory,'b'*32,[two])
        groups=photo_store.exact_photo_duplicates(self.directory)['groups'];self.assertEqual(len(groups),1)
        self.assertEqual(groups[0]['sha256'],a['id']);self.assertEqual(len(groups[0]['references']),2)
        self.assertTrue((self.directory/'media'/a['id']).exists())
        self.assertTrue(photo_store.exact_photo_duplicates(self.directory,maximum_cards=1)['truncated'])
        (self.directory/'media'/a['id']).write_bytes(b'not the same bytes')
        self.assertEqual(photo_store.exact_photo_duplicates(self.directory)['groups'],[])

    def test_accepted_copy_prefers_manual_then_valid_ocr_and_never_runs_detector(self):
        url='https://images.cdn-cian.ru/images/selection.jpg';data='data:image/png;base64,'+base64.b64encode(image_bytes()).decode()
        original=photo_store.attach_photo(self.directory,url,data)
        checked=photo_store.crop_photo(self.directory,original['id'],{'left':8})
        with photo_store._connection(self.directory) as db:
            meta=dict(checked,verification='ocr-checked',verificationEvidence={'knownRegionsExcluded':True,'postOcr':'not-detected'})
            db.execute('UPDATE assets SET metadata=? WHERE id=?',(json.dumps(meta),checked['id']))
            db.execute('DELETE FROM crop_links WHERE asset_id=?',(checked['id'],))
        with patch.object(photo_store,'recognize',side_effect=AssertionError('No OCR')):
            self.assertEqual(photo_store.accepted_photo_for_url(self.directory,url)['selected']['id'],checked['id'])
            manual=photo_store.crop_photo(self.directory,original['id'],{'left':5})
            self.assertEqual(photo_store.accepted_photo_for_url(self.directory,url)['reason'],'manual')
            (self.directory/'media'/manual['id']).write_bytes(b'corrupt')
            self.assertEqual(photo_store.accepted_photo_for_url(self.directory,url)['selected']['id'],checked['id'])

    def test_unverified_or_incomplete_ocr_copy_does_not_replace_original(self):
        url='https://images.cdn-cian.ru/images/uncertain.jpg';original=photo_store.attach_photo(self.directory,url,'data:image/png;base64,'+base64.b64encode(image_bytes()).decode())
        crop=photo_store.crop_photo(self.directory,original['id'],{'left':5})
        with photo_store._connection(self.directory) as db:
            db.execute('UPDATE assets SET metadata=? WHERE id=?',(json.dumps(dict(crop,verification='ocr-checked',verificationEvidence={'knownRegionsExcluded':False,'postOcr':'not-detected'})),crop['id']))
            db.execute('DELETE FROM crop_links WHERE asset_id=?',(crop['id'],))
        self.assertEqual(photo_store.accepted_photo_for_url(self.directory,url)['selected']['id'],original['id'])

    def test_zero_crop_never_changes_the_original_kind_or_bytes(self):
        from PIL import Image
        image=Image.new('RGB',(640,480),'#888888');out=io.BytesIO();image.save(out,'JPEG',quality=95,subsampling=0);image.close()
        raw=out.getvalue();url='https://images.cdn-cian.ru/images/same-jpeg.jpg'
        original=photo_store.attach_photo(self.directory,url,'data:image/jpeg;base64,'+base64.b64encode(raw).decode())
        value=photo_store.crop_photo(self.directory,original['id'],{})
        self.assertEqual(value['status'],'unchanged');self.assertEqual(photo_store.asset(self.directory,original['id'])[2]['kind'],'original')
        self.assertEqual(photo_store.asset(self.directory,original['id'])[0],raw)

    def test_a_crop_equal_to_another_original_keeps_both_identity_roles(self):
        from PIL import Image
        source=Image.new('RGB',(700,480),'#888888');source.paste('#aa1111',(0,0,70,480));out=io.BytesIO();source.save(out,'PNG');source.close()
        base=Image.new('RGB',(630,480),'#888888');base_out=io.BytesIO();base.save(base_out,'JPEG',quality=95,subsampling=0);base.close()
        other_url='https://images.cdn-cian.ru/images/other-original.jpg';source_url='https://images.cdn-cian.ru/images/crop-source.jpg'
        other=photo_store.attach_photo(self.directory,other_url,'data:image/jpeg;base64,'+base64.b64encode(base_out.getvalue()).decode())
        original=photo_store.attach_photo(self.directory,source_url,'data:image/png;base64,'+base64.b64encode(out.getvalue()).decode())
        crop=photo_store.crop_photo(self.directory,original['id'],{'left':10})
        self.assertEqual(crop['id'],other['id'])
        self.assertEqual(photo_store.asset(self.directory,other['id'])[2]['kind'],'original')
        selected=photo_store.accepted_photo_for_url(self.directory,source_url)
        self.assertEqual(selected['selected']['kind'],'crop');self.assertEqual(selected['reason'],'manual')
        self.assertEqual(photo_store.accepted_photo_for_url(self.directory,other_url)['reason'],'original')

    def test_saved_per_card_margins_and_explicit_original_override_global_crop_without_ocr_or_hash_polling(self):
        import phone_relay
        url='https://images.cdn-cian.ru/images/per-card.jpg'
        original=photo_store.attach_photo(self.directory,url,'data:image/png;base64,'+base64.b64encode(image_bytes()).decode())
        first=photo_store.crop_photo(self.directory,original['id'],{'left':5})
        second=photo_store.crop_photo(self.directory,original['id'],{'bottom':10})
        card={'fields':{'photos':[url],'photoEdits':{'crops':{url:{'left':5}}}}}
        with patch.object(photo_store,'asset',side_effect=AssertionError('Polling must not hash originals')),patch.object(photo_store,'recognize',side_effect=AssertionError('Polling must not OCR')):
            selected=phone_relay.card_media(self.directory,card)[0]
            self.assertEqual(selected['crop']['id'],first['id'])
            card['fields']['photoEdits']['crops'][url]={'left':20}
            self.assertNotIn('crop',phone_relay.card_media(self.directory,card)[0])
            card['fields']['photoEdits']={'originals':[url]}
            self.assertNotIn('crop',phone_relay.card_media(self.directory,card)[0])
        self.assertNotEqual(first['id'],second['id'])


if __name__=='__main__':unittest.main()
