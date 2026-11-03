"""Portable choices retain all observed available bytes; generated fixtures only."""
import base64
import copy
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
from PIL import Image,ImageDraw
import card_assets
import dossier_backup as backup
import local_server
import photo_store
import photo_variants as variants
import phone_relay
import workspace_backup

FULL='https://images.cdn-cian.ru/images/portable-variant-full.jpg'
THUMB='https://images.cdn-cian.ru/images/portable-variant-thumbnail.jpg'
ALT='https://images.cdn-cian.ru/images/portable-variant-srcset.webp'


def picture(size,color):
    image=Image.new('RGB',size,color);draw=ImageDraw.Draw(image);draw.rectangle((10,10,size[0]//2,size[1]//2),outline='white',width=3)
    stream=io.BytesIO();image.save(stream,'PNG');image.close();return stream.getvalue()


class DossierVariantsBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.parent=Path(self.temp.name)
        self.source=self.parent/'source';self.source.mkdir();self.destination=self.parent/'destination'
        self.source_info={'url':FULL,'photoId':'portable-photo-123','variants':{'fullUrl':FULL,'thumbnailUrl':THUMB},'observedVariants':[{'url':ALT,'source':'srcset'}]}
        self.pictures={FULL:picture((320,240),'#8899aa'),THUMB:picture((160,120),'#776655'),ALT:picture((1200,900),'#557799')}
        self.card=local_server.save_result(self.source,{'fields':{'photos':[FULL],'photoItems':[self.source_info],'price':'80000'},'listing':{'images':[FULL],'photoItems':[self.source_info]},'titles':['合成测试'],'body':'仅供隔离测试','tags':'#测试'})

    def tearDown(self):self.temp.cleanup()

    def probe(self,*,missing=()):
        def load(directory,url,**options):
            if url in missing:raise OSError('synthetic source unavailable')
            return photo_store.attach_photo(directory,url,'data:image/png;base64,'+base64.b64encode(self.pictures[url]).decode(),source=self.source_info,manual=False)
        with patch.object(photo_store,'load_photo',side_effect=load):
            return variants.probe_variants(self.source,FULL,self.source_info,refresh=True)

    def assert_no_network(self):
        return patch.object(photo_store,'load_photo',side_effect=AssertionError('Backup must not download or run a probe'))

    def archive(self):
        with self.assert_no_network():return backup.export_private(self.source,self.card)

    def mutated(self,content,change):
        manifest,files=backup._read_zip(content);change(manifest,files)
        return backup._make_zip(manifest,files)

    def test_round_trip_keeps_selected_reason_rejected_originals_manual_priority_and_workspace_counter(self):
        decision=self.probe();self.assertEqual(decision['selected']['url'],ALT)
        crop=photo_store.crop_photo(self.source,decision['selected']['id'],{'left':5})
        content=self.archive();manifest,files=backup._read_zip(content)
        self.assertEqual(backup.photo_count(manifest),1);self.assertEqual(len(manifest['mediaUrls']),3)
        self.assertEqual(len(manifest['photoVariantDecisions']),1)
        with self.assert_no_network():receipt=backup.restore_private(self.destination,content)
        self.assertEqual(receipt['photos'],1);self.assertEqual(receipt['photoVariantDecisions'],1)
        restored=variants.export_decision(self.destination,FULL,source=self.source_info)
        self.assertEqual(restored['reason'],'observed-resolution-eligible');self.assertEqual(restored['selected']['url'],ALT)
        for row in restored['candidates']:
            self.assertEqual(photo_store.asset(self.destination,row['id'])[0],self.pictures[row['url']])
        selected=photo_store.accepted_photo_for_url(self.destination,FULL,source=self.source_info)
        self.assertEqual(selected['selected']['id'],crop['id']);self.assertEqual(selected['selected']['verification'],'manual')
        with self.assert_no_network():
            workspace=workspace_backup.export_workspace(self.source)
            inspected=workspace_backup.inspect_workspace(workspace)
            self.assertEqual(inspected['photos'],1);self.assertEqual(inspected['photoVariantDecisions'],1)
            destination=self.parent/'workspace-copy';workspace_backup.restore_workspace(workspace,destination)
        self.assertEqual(variants.export_decision(destination,FULL,source=self.source_info)['selected']['url'],ALT)
        self.assertEqual(photo_store.accepted_photo_for_url(destination,FULL,source=self.source_info)['selected']['id'],crop['id'])

    def test_manual_crop_of_canonical_source_keeps_priority_over_an_eligible_variant(self):
        decision=self.probe();canonical=next(row for row in decision['candidates'] if row['url']==FULL)
        crop=photo_store.crop_photo(self.source,canonical['id'],{'left':5})
        with self.assert_no_network():backup.restore_private(self.destination,self.archive())
        self.assertEqual(variants.export_decision(self.destination,FULL,source=self.source_info)['selected']['url'],ALT)
        selected=photo_store.accepted_photo_for_url(self.destination,FULL,source=self.source_info)
        self.assertEqual(selected['selected']['id'],crop['id']);self.assertEqual(selected['reason'],'manual')

    def test_explicit_manual_original_keeps_priority_and_passport_bytes_still_survive(self):
        decision=self.probe()
        manual=photo_store.attach_photo(self.source,FULL,'data:image/png;base64,'+base64.b64encode(self.pictures[FULL]).decode(),source=self.source_info)
        with self.assert_no_network():backup.restore_private(self.destination,self.archive())
        self.assertEqual(variants.export_decision(self.destination,FULL,source=self.source_info)['selected']['id'],decision['selected']['id'])
        selected=photo_store.accepted_photo_for_url(self.destination,FULL,source=self.source_info)
        self.assertEqual(selected['selected']['id'],manual['id']);self.assertEqual(selected['reason'],'original')

    def test_unavailable_urls_are_not_required_but_available_alternative_is_retained(self):
        decision=self.probe(missing=(FULL,));self.assertEqual(decision['selected']['url'],ALT)
        content=self.archive();manifest,_=backup._read_zip(content)
        self.assertNotIn(FULL,[row['url'] for row in manifest['mediaUrls']])
        with self.assert_no_network():backup.restore_private(self.destination,content)
        self.assertEqual(photo_store.accepted_photo_for_url(self.destination,FULL,source=self.source_info)['selected']['id'],decision['selected']['id'])
        self.assertEqual(variants.export_decision(self.destination,FULL,source=self.source_info)['candidates'][0]['status'],'unavailable')

    def test_missing_available_alternative_fails_export_instead_of_dropping_passport(self):
        decision=self.probe();(self.source/'media'/decision['selected']['id']).unlink()
        with self.assert_no_network(),self.assertRaises(ValueError):backup.export_private(self.source,self.card)

    def test_tampered_group_selection_quality_identity_and_missing_candidate_reject_before_rows(self):
        self.probe();content=self.archive()
        changes=[lambda m,f:m['photoVariantDecisions'][0].update(groupSha='0'*64),
            lambda m,f:m['photoVariantDecisions'][0].update(selected=m['photoVariantDecisions'][0]['candidates'][1]),
            lambda m,f:m['photoVariantDecisions'][0]['candidates'][2]['quality'].update(pixels=999999999),
            lambda m,f:m['photoVariantDecisions'][0]['candidates'][2].update(photoId='foreign-photo'),
            lambda m,f:m['photoVariantDecisions'][0].update(canonicalUrl='https://images.cdn-cian.ru/images/foreign.jpg'),
            lambda m,f:m['mediaUrls'].pop()]
        for index,change in enumerate(changes):
            destination=self.parent/('bad-'+str(index))
            with self.subTest(case=index),self.assert_no_network(),self.assertRaises(ValueError):backup.restore_private(destination,self.mutated(content,change))
            self.assertFalse(destination.exists())

    def test_variant_write_rolls_back_with_card_media_and_dossier_on_late_failure(self):
        self.probe();content=self.archive();self.destination.mkdir()
        with card_assets._db(self.destination) as db:
            db.execute("CREATE TRIGGER fail_fixture BEFORE INSERT ON restored_manifests BEGIN SELECT RAISE(ABORT,'fixture failure after variant insert'); END")
        with self.assert_no_network(),self.assertRaisesRegex(sqlite3.IntegrityError,'fixture failure'):
            backup.restore_private(self.destination,content)
        for relative,table in [('listings.sqlite3','results'),('media.sqlite3','photo_urls'),('photo-variant-decisions.sqlite3','decisions')]:
            with sqlite3.connect(self.destination/relative) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0],0)

    def test_existing_unrelated_group_coexists_and_same_group_conflict_is_preserved(self):
        self.probe();content=self.archive();backup.restore_private(self.destination,content)
        old=variants.export_decision(self.destination,FULL,source=self.source_info)
        # A different explicit observed group for the same canonical source.
        newer={**old,'observed':[{'url':row['url'],'provenance':row['provenance']} for row in old['candidates'][:2]],'candidates':old['candidates'][:2],
            'observedCount':2,'truncated':False,'selected':old['candidates'][0],'status':'review','reason':'preview-requires-review'}
        group={'policy':newer['policy'],'photoId':newer['photoId'],'canonicalUrl':FULL,'observed':newer['observed']}
        newer['groupSha']=hashlib.sha256(json.dumps(group,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        assets={row['id']:{'content':photo_store.asset(self.destination,row['id'])[0],'mime':row['mime'],'metadata':photo_store.asset(self.destination,row['id'])[2]} for row in newer['candidates']}
        newer=variants.validate_portable_decision(FULL,newer,assets=assets);variants._persist(self.destination,newer)
        with self.assert_no_network():backup.restore_private(self.destination,content)
        self.assertEqual(variants.export_decision(self.destination,FULL,group_sha=newer['groupSha'])['groupSha'],newer['groupSha'])
        self.assertEqual(variants.export_decision(self.destination,FULL,source=self.source_info)['groupSha'],old['groupSha'])
        # A different decision for the SAME observed group is not overwritten.
        conflict=copy.deepcopy(old);conflict['candidates'][1]={'url':THUMB,'sourceUrl':THUMB,'photoId':old['photoId'],
            'provenance':old['candidates'][1]['provenance'],'status':'unavailable','reason':'load-or-decode-failed','errorClass':'OSError','message':'newer source failure'}
        assets={row['id']:{'content':photo_store.asset(self.destination,row['id'])[0],'mime':row['mime'],'metadata':photo_store.asset(self.destination,row['id'])[2]} for row in conflict['candidates'] if row['status']=='available'}
        conflict=variants.validate_portable_decision(FULL,conflict,assets=assets);variants._persist(self.destination,conflict)
        with self.assert_no_network(),self.assertRaisesRegex(ValueError,'другой выбор'):backup.restore_private(self.destination,content)
        self.assertEqual(variants.export_decision(self.destination,FULL,source=self.source_info)['candidates'][1]['status'],'unavailable')

    def test_workspace_preserves_two_historical_source_groups_for_one_canonical_url(self):
        source_a=copy.deepcopy(self.source_info);decision_a=self.probe()
        second_url='https://images.cdn-cian.ru/images/portable-variant-second-group.webp'
        source_b={**copy.deepcopy(source_a),'observedVariants':[{'url':second_url,'source':'srcset'}]}
        self.pictures[second_url]=picture((1500,1100),'#114477')
        self.source_info=source_b
        try:decision_b=self.probe()
        finally:self.source_info=source_a
        second=local_server.save_result(self.source,{'fields':{'photos':[FULL],'photoItems':[source_b],'price':'85000'},
            'listing':{'images':[FULL],'photoItems':[source_b]},'titles':['第二套合成测试'],'body':'仅供隔离测试','tags':'#测试'})
        self.assertNotEqual(decision_a['groupSha'],decision_b['groupSha'])
        with self.assert_no_network():
            first_bundle=backup.export_private(self.source,self.card);second_bundle=backup.export_private(self.source,second)
            content=workspace_backup.export_workspace(self.source)
            inspected=workspace_backup.inspect_workspace(content)
            self.assertEqual((inspected['cards'],inspected['photoVariantDecisions']),(2,2))
            receipt=workspace_backup.restore_workspace(content,self.destination)
        self.assertEqual(len(receipt['cards']),2)
        with sqlite3.connect(self.destination/'listings.sqlite3') as db:cards=[json.loads(row[0]) for row in db.execute('SELECT payload FROM results')]
        seen=set()
        for card in cards:
            source=phone_relay.photo_source_items(card)[FULL]
            expected=decision_a if source['observedVariants'][0]['url']==ALT else decision_b
            passport=variants.export_decision(self.destination,FULL,source=source)
            self.assertEqual(passport['groupSha'],expected['groupSha']);seen.add(passport['groupSha'])
            self.assertEqual(phone_relay.card_media(self.destination,card)[0]['prepared']['id'],expected['selected']['id'])
            self.assertEqual(photo_store.asset(self.destination,expected['selected']['id'])[0],self.pictures[expected['selected']['url']])
        self.assertEqual(seen,{decision_a['groupSha'],decision_b['groupSha']})
        # A valid foreign passport cannot be swapped into another card merely
        # because the primary URL is shared.
        foreign,_=backup._read_zip(second_bundle)
        bad=self.mutated(first_bundle,lambda m,f:m.update(photoVariantDecisions=foreign['photoVariantDecisions']))
        rejected=self.parent/'foreign-group'
        with self.assertRaisesRegex(ValueError,'исходной группой'):backup.restore_private(rejected,bad)
        self.assertFalse(rejected.exists())
        real=backup.restore_private;calls=[]
        def fail_second(*args,**kwargs):
            calls.append(1)
            if len(calls)==2:raise RuntimeError('synthetic second source-group failure')
            return real(*args,**kwargs)
        failed=self.parent/'failed-workspace'
        with patch.object(backup,'restore_private',side_effect=fail_second),self.assertRaisesRegex(RuntimeError,'second source-group'):
            workspace_backup.restore_workspace(content,failed)
        self.assertFalse(failed.exists());self.assertEqual(list(self.parent.glob('.redcn-restore-*')),[])

    def test_legacy_card_does_not_copy_or_inherit_an_unrelated_group_passport(self):
        decision=self.probe();scoped=backup.restore_private(self.destination,self.archive())
        legacy=local_server.save_result(self.source,{'fields':{'photos':[FULL],'price':'80000'},'listing':{'images':[FULL]},
            'titles':['旧版合成测试'],'body':'仅供隔离测试','tags':'#测试'})
        with self.assert_no_network():content=backup.export_private(self.source,legacy)
        manifest,_=backup._read_zip(content)
        self.assertEqual(manifest['photoVariantDecisions'],[]);self.assertEqual(len(manifest['mediaUrls']),1)
        restored=backup.restore_private(self.destination,content)
        card=local_server.read_result(self.destination,restored['result_id'])
        canonical=next(row for row in decision['candidates'] if row['url']==FULL)
        media=phone_relay.card_media(self.destination,card)[0]
        self.assertEqual(media['prepared']['id'],canonical['id']);self.assertNotIn('variantSelection',media)
        scoped_card=local_server.read_result(self.destination,scoped['result_id'])
        self.assertEqual(phone_relay.card_media(self.destination,scoped_card)[0]['prepared']['id'],decision['selected']['id'])

    def test_platform_photo_identity_is_not_remapped_as_a_coincidentally_equal_storage_id(self):
        self.source_info['photoId']=self.card
        card=local_server.read_result(self.source,self.card)
        for holder in (card['fields'],card['listing']):holder['photoItems']=[copy.deepcopy(self.source_info)]
        local_server.save_result(self.source,card,self.card)
        decision=self.probe()
        with self.assert_no_network():
            content=workspace_backup.export_workspace(self.source)
            receipt=workspace_backup.restore_workspace(content,self.destination)
        identifier=receipt['cards'][0]['result_id'];self.assertNotEqual(identifier,self.card)
        restored=local_server.read_result(self.destination,identifier)
        self.assertEqual(phone_relay.photo_source_items(restored)[FULL]['photoId'],self.card)
        self.assertEqual(phone_relay.card_media(self.destination,restored)[0]['prepared']['id'],decision['selected']['id'])


if __name__=='__main__':unittest.main()
