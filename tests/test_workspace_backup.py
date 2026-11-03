"""Synthetic dossiers in separate folders; no real library/auth/photo is read."""
import base64
import copy
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
import access_roles
import dossier_backup
import workspace_backup as backup
import photo_store
import photo_instances
import redcn
from learning_memory import LearningMemory
from phone_relay import save_profile,load_profile
from node_runtime import resolve_node

ROOT=Path(__file__).resolve().parents[1]


def picture(color):
    from PIL import Image
    image=Image.new('RGB',(64,64),color);out=io.BytesIO();image.save(out,'PNG');image.close();return out.getvalue()


def add_card(root,identifier,urls=(),related=()):
    payload={'fields':{'price':'80000','area':'40','photos':list(urls),'relatedOffers':list(related)},
        'titles':['莫斯科40㎡'],'body':'月租80000卢布','tags':'#莫斯科租房'}
    with redcn.open_cache(root) as db:
        db.execute('CREATE TABLE IF NOT EXISTS results(id TEXT PRIMARY KEY,updated REAL,payload TEXT)')
        db.execute('INSERT INTO results VALUES(?,?,?)',(identifier,1,json.dumps(payload,ensure_ascii=False)))
    return payload


class WorkspaceBackupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template=json.loads(subprocess.check_output([resolve_node(),'--input-type=module','-e',
            'import {defaultPublicationDesign} from "./web/publication-model.mjs";process.stdout.write(JSON.stringify(defaultPublicationDesign()));'],cwd=ROOT,text=True))

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.parent=Path(self.temp.name)
        self.source=self.parent/'source';self.source.mkdir();self.destination=self.parent/'restored'
        self.one='a'*32;self.two='b'*32
        self.url='https://images.cdn-cian.ru/images/generated-workspace-fixture.png'
        photo=photo_store.attach_photo(self.source,self.url,'data:image/png;base64,'+base64.b64encode(picture('#8899aa')).decode())
        self.original=photo['id'];self.crop=photo_store.crop_photo(self.source,self.original,{'left':5})
        add_card(self.source,self.one,[self.url],[{'resultId':self.two}]);add_card(self.source,self.two,(),[{'resultId':self.one}])
        card_assets.update_archive(self.source,self.one,{'status':'rented','notes':'PRIVATE SYNTHETIC NOTE'})
        self.contract=card_assets.add_attachment(self.source,self.one,{'name':'fixture-contract.pdf','category':'contract',
            'data':base64.b64encode(b'%PDF-1.4\nSYNTHETIC CONTRACT\n%%EOF').decode()})
        card_assets.save_design(self.source,self.template)
        for index in range(2):
            template=copy.deepcopy(self.template);template['theme']['pdf']['accent']='#bb8833' if index else '#aa9922'
            card_assets.create_export(self.source,self.one,'<!doctype html><html><body>版本'+str(index)+'</body></html>','html',template=template)
        save_profile(self.source,{'rules':{'письменный стол':'书桌'},'prefs':{'brandTags':'#莫斯科租房'},'data':{'style':'compact'}})
        memory=LearningMemory(self.source)
        self.accepted=memory.record('translation',{'ru':'есть стол','zh':'有书桌'},status='approved',group_id='fixture-one')
        self.pending=memory.record('translation',{'ru':'нужна проверка','zh':'需要核实'},group_id='fixture-two')
        memory.snapshot('fixture-portable')
        with sqlite3.connect(self.source/'listings.sqlite3') as db:
            db.execute('CREATE TABLE history(id TEXT PRIMARY KEY,stamp REAL,payload TEXT,png BLOB)')
            db.execute('INSERT INTO history VALUES(?,?,?,?)',('history-fixture',1,json.dumps({'fields':{'resultId':self.one},'title':'旧版'}),picture('#445566')))
        for name in ('phone-auth.json','cookies.json','photo-ai.json'):(self.source/name).write_text('DO-NOT-TRANSFER-'+name)
        (self.source/'import-diagnostics').mkdir();(self.source/'import-diagnostics'/'campaigns.sqlite3').write_bytes(b'PRIVATE DIAGNOSTICS')

    def tearDown(self):self.temp.cleanup()

    def bundle(self):return backup.export_workspace(self.source)

    def rewrite(self,data,edit):
        with zipfile.ZipFile(io.BytesIO(data)) as source:files={name:source.read(name) for name in source.namelist()}
        manifest=json.loads(files['manifest.json']);edit(manifest,files)
        files['manifest.json']=backup._json(manifest)
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_DEFLATED) as target:
            for name,value in files.items():target.writestr(name,value)
        return out.getvalue()

    def test_portable_library_dictionary_learning_history_and_all_versions_restore_together(self):
        data=self.bundle();preview=backup.inspect_workspace(data)
        self.assertEqual((preview['cards'],preview['attachments'],preview['exports'],preview['photos']),(2,1,2,1))
        result=backup.restore_workspace(data,self.destination)
        self.assertEqual(len(result['cards']),2);self.assertTrue(result['devicesMustPairAgain'])
        one,two=[row['result_id'] for row in result['cards']]
        with sqlite3.connect(self.destination/'listings.sqlite3') as db:
            rows={key:json.loads(value) for key,value in db.execute('SELECT id,payload FROM results')}
            history=db.execute('SELECT payload,png FROM history').fetchone()
        self.assertEqual(rows[one]['fields']['relatedOffers'][0]['resultId'],two)
        self.assertEqual(rows[two]['fields']['relatedOffers'][0]['resultId'],one)
        self.assertEqual(json.loads(history[0])['fields']['resultId'],one);self.assertTrue(history[1].startswith(b'\x89PNG'))
        self.assertEqual(load_profile(self.destination),load_profile(self.source))
        source_memory=LearningMemory(self.source);restored_memory=LearningMemory(self.destination)
        self.assertEqual(restored_memory.status()['counts'],source_memory.status()['counts'])
        self.assertEqual(restored_memory.retrieve('есть стол')[0]['id'],self.accepted['id'])
        self.assertEqual(restored_memory.list_events(status='pending')[0]['id'],self.pending['id'])
        self.assertEqual(card_assets.archive(self.destination,one)['notes'],'PRIVATE SYNTHETIC NOTE')
        exports=card_assets.list_exports(self.destination,one)
        self.assertEqual(len(exports),2);self.assertTrue(all(row['requiresRegeneration'] for row in exports))
        self.assertEqual(photo_store.accepted_photo_for_url(self.destination,self.url)['selected']['id'],self.crop['id'])
        self.assertEqual(card_assets.load_design(self.destination)['template'],self.template)
        self.assertEqual(backup.inspect_workspace(backup.export_workspace(self.destination))['cards'],2)

    def test_auth_diagnostics_models_and_raw_cache_are_never_in_workspace_bundle(self):
        with redcn.open_cache(self.source) as db:db.execute('INSERT INTO cache VALUES(?,?,?)',('https://www.cian.ru/rent/flat/123/',1,'PRIVATE SOURCE HTML CACHE'))
        data=self.bundle()
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            combined=b''.join(z.read(name) for name in z.namelist())
            self.assertNotIn(b'DO-NOT-TRANSFER',combined);self.assertNotIn(b'PRIVATE DIAGNOSTICS',combined)
            self.assertNotIn(b'PRIVATE SOURCE HTML CACHE',combined)
        backup.restore_workspace(data,self.destination)
        for name in ('phone-auth.json','cookies.json','photo-ai.json','import-diagnostics'):
            self.assertFalse((self.destination/name).exists())

    def test_existing_destination_and_competing_creation_are_not_overwritten(self):
        data=self.bundle();self.destination.mkdir();sentinel=self.destination/'keep';sentinel.write_text('USER DATA')
        with self.assertRaises(FileExistsError):backup.restore_workspace(data,self.destination)
        self.assertEqual(sentinel.read_text(),'USER DATA')
        another=self.parent/'competing';real=backup._publish
        def race(stage,destination):
            destination.mkdir();(destination/'keep').write_text('OTHER DATA');real(stage,destination)
        with patch.object(backup,'_publish',side_effect=race),self.assertRaises(FileExistsError):backup.restore_workspace(data,another)
        self.assertEqual((another/'keep').read_text(),'OTHER DATA')
        self.assertEqual(list(self.parent.glob('.redcn-restore-*')),[])

    def test_second_card_failure_never_publishes_half_workspace(self):
        data=self.bundle();real=dossier_backup.restore_private;calls=[]
        def fail_second(*args,**kwargs):
            calls.append(1)
            if len(calls)==2:raise RuntimeError('fixture disk failure')
            return real(*args,**kwargs)
        with patch.object(dossier_backup,'restore_private',side_effect=fail_second),self.assertRaisesRegex(RuntimeError,'fixture'):
            backup.restore_workspace(data,self.destination)
        self.assertFalse(self.destination.exists());self.assertEqual(list(self.parent.glob('.redcn-restore-*')),[])

    def test_bad_nested_schema_hash_count_and_unlisted_file_reject(self):
        data=self.bundle()
        corruptions=[self.rewrite(data,lambda m,f:m['files'][0].update(sha256='0'*64)),
            self.rewrite(data,lambda m,f:m['cards'][0].update(photos=500)),
            self.rewrite(data,lambda m,f:f.update({'../escape':b'NO'}))]
        for bad in corruptions:
            with self.subTest(bytes=len(bad)),self.assertRaises(ValueError):backup.restore_workspace(bad,self.destination)
            self.assertFalse(self.destination.exists())

    def test_inspection_rejects_incompatible_columns_types_and_primary_keys_without_publishing(self):
        content=self.bundle();manifest,files=backup._read(content)
        original=json.loads(files['metadata.json'])
        cases=[]
        columns=copy.deepcopy(original);columns['tables']['learning/examples']={'columns':['not_a_column'],'rows':[[1]]};cases.append(columns)
        types=copy.deepcopy(original);table=types['tables']['learning/examples'];table['rows'][0][table['columns'].index('created')]=123;cases.append(types)
        keys=copy.deepcopy(original);table=keys['tables']['learning/examples'];table['rows'].append(copy.deepcopy(table['rows'][0]));cases.append(keys)
        history=copy.deepcopy(original);table=history['tables']['listings/history'];table['rows'][0][table['columns'].index('payload')]='{invalid';cases.append(history)
        for index,metadata in enumerate(cases):
            replacement={**files,'metadata.json':backup._json(metadata)}
            candidate=backup._pack({key:value for key,value in manifest.items() if key!='files'},replacement)
            with self.subTest(case=index):
                with self.assertRaises(ValueError):backup.inspect_workspace(candidate)
                with self.assertRaises(ValueError):backup.restore_workspace(candidate,self.destination)
                self.assertFalse(self.destination.exists());self.assertEqual(list(self.parent.glob('.redcn-restore-*')),[])

    def test_change_to_dictionary_or_dossier_during_export_requires_retry(self):
        real=backup._pack
        def change(manifest,files):
            save_profile(self.source,{'rules':{'кровать':'床'}})
            return real(manifest,files)
        with patch.object(backup,'_pack',side_effect=change),self.assertRaisesRegex(ValueError,'изменились'):self.bundle()

    def test_private_card_backup_merges_nineteen_sources_and_rejects_concurrent_notes_edit(self):
        urls=[]
        for index in range(19):
            url='https://images.cdn-cian.ru/images/portable-'+str(index)+'.png';urls.append(url)
            photo_store.attach_photo(self.source,url,'data:image/png;base64,'+base64.b64encode(picture((index*10,55,99))).decode())
        with sqlite3.connect(self.source/'listings.sqlite3') as db:
            value=json.loads(db.execute('SELECT payload FROM results WHERE id=?',(self.one,)).fetchone()[0])
            value['fields']['photos']=urls[:1];value['listing']={'images':urls}
            db.execute('UPDATE results SET payload=? WHERE id=?',(json.dumps(value),self.one))
        content=dossier_backup.export_private(self.source,self.one)
        manifest,_=dossier_backup._read_zip(content);self.assertEqual(len(manifest['mediaUrls']),19)
        real=dossier_backup._make_zip
        def change(manifest,files):
            card_assets.update_archive(self.source,self.one,{'notes':'Changed while exporting'})
            return real(manifest,files)
        with patch.object(dossier_backup,'_make_zip',side_effect=change),self.assertRaisesRegex(ValueError,'изменились'):
            dossier_backup.export_private(self.source,self.one)

    def test_platform_publish_does_not_replace_an_empty_existing_directory(self):
        stage=self.parent/'stage';stage.mkdir();(stage/'content').write_text('NEW')
        self.destination.mkdir()
        with self.assertRaises(FileExistsError):backup._publish(stage,self.destination)
        self.assertTrue((stage/'content').exists());self.assertTrue(self.destination.exists())

    def test_zero_card_workspace_retains_global_brand_originals_and_design(self):
        empty=self.parent/'empty';empty.mkdir()
        with redcn.open_cache(empty) as db:db.execute('CREATE TABLE results(id TEXT PRIMARY KEY,updated REAL,payload TEXT)')
        template=copy.deepcopy(self.template)
        for role,key,color in [('logo','logoAssetId','#ff3300'),('avatar','avatarAssetId','#0033ff')]:
            original=picture(color)
            image=card_assets.add_design_image(empty,{'name':role+'.png','role':role,'data':'data:image/png;base64,'+base64.b64encode(original).decode()})
            template['brand'][key]=image['id']
        card_assets.save_design(empty,template)
        content=backup.export_workspace(empty);self.assertEqual(backup.inspect_workspace(content)['cards'],0)
        backup.restore_workspace(content,self.destination)
        self.assertEqual(card_assets.load_design(self.destination)['template'],template)
        for key in ('logoAssetId','avatarAssetId'):
            source=card_assets.design_image(empty,template['brand'][key])
            restored=card_assets.design_image(self.destination,template['brand'][key])
            self.assertEqual(source,restored)
        self.assertEqual(backup.inspect_workspace(backup.export_workspace(self.destination))['cards'],0)

    def test_manual_photo_instance_review_keeps_its_evidence_under_new_card_id(self):
        photo_instances.save_review(self.source,self.one,{'version':1,'listingId':self.one,'observations':[
            {'id':'fixture-observation','photoId':self.original,'feature':'chairs','region':[0.1,0.1,0.4,0.4],
             'status':'confirmed','instanceId':'fixture-chair','linkConfirmed':True}], 'countConfirmations':{'chairs':1}},
            photo_ids=[self.original],expected_revision=0)
        receipt=backup.restore_workspace(self.bundle(),self.destination);identifier=receipt['cards'][0]['result_id']
        review=photo_instances.load_review(self.destination,identifier,photo_ids=[self.original])
        self.assertEqual(review['revision'],1);self.assertEqual(review['document']['listingId'],identifier)
        self.assertEqual(review['summary']['confirmedVisibleCounts'],{'chairs':1})

    def test_desktop_dispatch_only_and_phone_roles_cannot_export_or_inspect(self):
        for role in ('owner','editor','viewer'):
            for method,path in [('GET','workspace-backup.zip'),('POST','workspace-backup/inspect')]:
                self.assertFalse(access_roles.phone_route_allowed(role,method,path))
        with patch.object(backup,'export_workspace') as export,patch.object(backup,'inspect_workspace') as inspect:
            for path,data in [('workspace-backup.zip',None),('workspace-backup/inspect',{'data':'eA=='})]:
                self.assertEqual(backup.dispatch(self.source,path,data,namespace='phone')[0],403)
            export.assert_not_called();inspect.assert_not_called()
        content=self.bundle()
        result=backup.dispatch(self.source,'workspace-backup/inspect',{'data':base64.b64encode(content).decode()})
        self.assertEqual(result[0],200);self.assertEqual(result[1]['cards'],2)
        self.assertEqual(backup.dispatch(self.source,'workspace-backup.zip',{})[0],405)
        for data in ({'data':'!!!!'},{'data':'eA==','destination':str(self.destination)}):
            with self.assertRaises(ValueError):backup.dispatch(self.source,'workspace-backup/inspect',data)
        self.assertFalse(self.destination.exists())


if __name__=='__main__':unittest.main()
