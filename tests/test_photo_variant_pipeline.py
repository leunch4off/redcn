"""One accepted source/crop is shared by cached gallery, phone and ZIP."""
import base64
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
from PIL import Image
import local_server
import phone_relay
import photo_store
import photo_variants
import redcn

PRIMARY='https://images.cdn-cian.ru/images/variant-pipeline-primary.jpg'
LARGE='https://images.cdn-cian.ru/images/variant-pipeline-large.jpg'
TINY='https://images.cdn-cian.ru/images/variant-pipeline-tiny.jpg'


def raster(size,color):
    stream=io.BytesIO();Image.new('RGB',size,color).save(stream,'PNG');return stream.getvalue()


class PhotoVariantPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.directory=Path(self.temp.name)
        self.images={PRIMARY:raster((600,600),'#ff0000'),LARGE:raster((800,800),'#0000ff'),TINY:raster((120,120),'#00ff00')}
        self.source={'url':PRIMARY,'photoId':'fixture-photo-1','variants':{'fullUrl':PRIMARY,'thumbnailUrl':TINY},'observedVariants':[{'url':LARGE,'source':'srcset'}]}
        self.result={'fields':{'photos':[PRIMARY],'photoItems':[self.source]},'listing':{'images':[PRIMARY],'photoItems':[self.source]},'titles':['真实房源'],'body':'照片资料','tags':'#莫斯科租房'}

    def tearDown(self):self.temp.cleanup()

    def fetch(self,url,**kwargs):return self.images[url],'image/jpeg'

    def prepare(self):
        with patch.object(redcn,'fetch_bytes',side_effect=self.fetch),patch.object(photo_store,'auto_crop_photo',return_value={'autoStatus':'unchanged'}):
            phone_relay.cache_photos(self.directory,self.result)
        return local_server.save_result(self.directory,self.result)

    def test_selected_variant_is_identical_in_accepted_phone_cover_and_prepared_zip_and_survives_reimport(self):
        card_id=self.prepare();choice=photo_store.accepted_photo_for_url(self.directory,PRIMARY,source=self.source)
        primary=photo_store.load_photo(self.directory,PRIMARY);selected=choice['selected']
        self.assertNotEqual(primary['id'],selected['id']);self.assertEqual(choice['reason'],'observed-variant')
        self.assertFalse(choice['variantSelection']['cleanlinessConfirmed'])
        self.assertEqual(choice['variantSelection']['sourceOriginalId'],selected['id'])
        card=phone_relay.public_card(self.directory,card_id)
        self.assertEqual(card['media'][0]['id'],primary['id']);self.assertEqual(card['media'][0]['prepared']['id'],selected['id'])
        self.assertEqual(phone_relay.list_cards(self.directory)['cards'][0]['coverUrl'],'/api/phone/previews/'+selected['id'])
        for variant,expected in [('prepared',self.images[LARGE]),('original',self.images[PRIMARY])]:
            archive=zipfile.ZipFile(io.BytesIO(phone_relay.card_zip(self.directory,card_id,variant)))
            manifest=json.loads(archive.read('manifest.json'))
            self.assertEqual(archive.read(manifest[0]['name']),expected)
            self.assertEqual(manifest[0]['selectedId'],selected['id'] if variant=='prepared' else primary['id'])
        with patch.object(redcn,'fetch_bytes',side_effect=AssertionError('Reimport must reuse observed originals')),patch.object(photo_store,'auto_crop_photo',return_value={'autoStatus':'unchanged'}):
            phone_relay.cache_photos(self.directory,self.result)
            self.assertEqual(photo_store.accepted_photo_for_url(self.directory,PRIMARY,source=self.source)['selected']['id'],selected['id'])
        decision=photo_variants.variant_decision(self.directory,PRIMARY,self.source,verify_bytes=True)
        self.assertTrue(decision['cached'])
        self.assertEqual({row['url'] for row in decision['candidates']},{PRIMARY,LARGE,TINY})
        for row in decision['candidates']:self.assertEqual(photo_store.asset(self.directory,row['id'])[0],self.images[row['url']])

    def test_manual_crop_and_explicit_original_override_variant_everywhere(self):
        card_id=self.prepare();original=photo_store.load_photo(self.directory,PRIMARY)
        crop=photo_store.crop_photo(self.directory,original['id'],{'bottom':10})
        accepted=photo_store.accepted_photo_for_url(self.directory,PRIMARY)
        self.assertEqual(accepted['selected']['id'],crop['id']);self.assertEqual(accepted['reason'],'manual')
        self.assertNotIn('variantSelection',accepted)
        self.assertEqual(phone_relay.public_card(self.directory,card_id)['media'][0]['prepared']['id'],crop['id'])
        self.result['fields']['photoEdits']={'originals':[PRIMARY]};local_server.save_result(self.directory,self.result,card_id)
        self.assertEqual(phone_relay.public_card(self.directory,card_id)['media'][0]['prepared']['id'],original['id'])
        self.assertEqual(photo_store.accepted_photo_for_url(self.directory,PRIMARY,prefer_original=True)['selected']['id'],original['id'])

    def test_crop_of_selected_variant_keeps_both_content_identities(self):
        card_id=self.prepare();initial=photo_store.accepted_photo_for_url(self.directory,PRIMARY,source=self.source)
        variant_id=initial['selected']['id'];crop=photo_store.crop_photo(self.directory,variant_id,{'bottom':10})
        choice=photo_store.accepted_photo_for_url(self.directory,PRIMARY,source=self.source)
        self.assertEqual(choice['selected']['id'],crop['id']);self.assertEqual(choice['selected']['originalId'],variant_id)
        self.assertNotEqual(choice['original']['id'],variant_id)
        self.assertEqual(choice['variantSelection']['canonicalOriginalId'],choice['original']['id'])
        phone=phone_relay.public_card(self.directory,card_id)['media'][0]
        self.assertEqual(phone['crop']['id'],crop['id']);self.assertEqual(phone['prepared']['id'],crop['id'])

    def test_failed_primary_uses_only_eligible_observed_source_and_preserves_failure_evidence(self):
        def fetch(url,**kwargs):
            if url==PRIMARY:raise OSError('fixture primary unavailable')
            return self.fetch(url,**kwargs)
        with patch.object(redcn,'fetch_bytes',side_effect=fetch),patch.object(photo_store,'auto_crop_photo',return_value={'autoStatus':'unchanged'}):
            phone_relay.cache_photos(self.directory,self.result)
        self.assertEqual(self.result['mediaStatus'],'ready');self.assertEqual(self.result['media'][0]['actualSourceUrl'],LARGE)
        card_id=local_server.save_result(self.directory,self.result)
        with patch.object(redcn,'fetch_bytes',side_effect=AssertionError('API must use the cached observed fallback')):
            original=photo_store.load_photo(self.directory,PRIMARY,source=self.source)
            self.assertTrue(original['observedFallback']);self.assertEqual(original['sourceUrl'],LARGE)
            card=phone_relay.public_card(self.directory,card_id)
            self.assertEqual(card['media'][0]['id'],original['id']);self.assertEqual(card['media'][0]['prepared']['id'],original['id'])
        decision=photo_variants.variant_decision(self.directory,PRIMARY)
        self.assertEqual(next(row for row in decision['candidates'] if row['url']==PRIMARY)['status'],'unavailable')

    def test_tiny_preview_is_preserved_but_not_automatically_promoted_when_primary_fails(self):
        self.source['observedVariants']=[]
        def fetch(url,**kwargs):
            if url==PRIMARY:raise OSError('fixture primary unavailable')
            return self.fetch(url,**kwargs)
        with patch.object(redcn,'fetch_bytes',side_effect=fetch):phone_relay.cache_photos(self.directory,self.result)
        self.assertEqual(self.result['mediaStatus'],'partial');self.assertEqual(self.result['photoProcessing']['downloaded'],0)
        self.assertEqual(phone_relay.card_media(self.directory,self.result),[])
        decision=photo_variants.variant_decision(self.directory,PRIMARY)
        self.assertEqual(decision['status'],'review');self.assertEqual(photo_store.asset(self.directory,decision['selected']['id'])[0],self.images[TINY])

    def test_newly_attached_canonical_bytes_invalidate_old_variant_decision(self):
        self.prepare();fresh=raster((1000,800),'#ffff00')
        original=photo_store.attach_photo(self.directory,PRIMARY,'data:image/png;base64,'+base64.b64encode(fresh).decode(),source=self.source)
        self.assertEqual(photo_store.accepted_photo_for_url(self.directory,PRIMARY)['selected']['id'],original['id'])
        with patch.object(redcn,'fetch_bytes',side_effect=AssertionError('Already cached candidates')):
            photo_store.load_observed_photo(self.directory,PRIMARY,self.source)
        self.assertEqual(photo_store.accepted_photo_for_url(self.directory,PRIMARY,source=self.source)['selected']['id'],original['id'])

    def test_two_cards_keep_their_own_observed_group_after_the_other_card_reimports(self):
        card_a=self.prepare();blue=photo_store.accepted_photo_for_url(self.directory,PRIMARY,source=self.source)['selected']['id']
        second=json.loads(json.dumps(self.result));green_url=LARGE.replace('large','different-group')
        self.images[green_url]=raster((900,900),'#00ff00')
        source_b={**self.source,'photoId':'fixture-photo-2','observedVariants':[{'url':green_url,'source':'srcset'}]}
        second['fields']['photoItems']=[source_b];second['listing']['photoItems']=[source_b]
        with patch.object(redcn,'fetch_bytes',side_effect=self.fetch),patch.object(photo_store,'auto_crop_photo',return_value={'autoStatus':'unchanged'}):phone_relay.cache_photos(self.directory,second)
        card_b=local_server.save_result(self.directory,second)
        green=photo_store.accepted_photo_for_url(self.directory,PRIMARY,source=source_b)['selected']['id']
        self.assertNotEqual(blue,green)
        self.assertEqual(phone_relay.public_card(self.directory,card_a)['media'][0]['prepared']['id'],blue)
        self.assertEqual(phone_relay.public_card(self.directory,card_b)['media'][0]['prepared']['id'],green)
        self.assertEqual(photo_variants.export_decision(self.directory,PRIMARY,self.source)['selected']['id'],blue)
        self.assertEqual(photo_variants.export_decision(self.directory,PRIMARY,source_b)['selected']['id'],green)
        legacy={'fields':{'photos':[PRIMARY]},'titles':['旧卡片'],'body':'源图','tags':''}
        legacy_id=local_server.save_result(self.directory,legacy)
        primary=photo_store.load_photo(self.directory,PRIMARY)
        self.assertEqual(phone_relay.public_card(self.directory,legacy_id)['media'][0]['prepared']['id'],primary['id'])
        self.assertEqual(photo_store.accepted_photo_for_url(self.directory,PRIMARY)['selected']['id'],primary['id'])
        self.assertEqual(photo_store.accepted_photo_for_url(self.directory,PRIMARY,allow_unscoped_variants=True)['selected']['id'],green)

    def test_shared_bytes_do_not_replace_the_url_identity_of_another_cached_source(self):
        alias=PRIMARY.replace('primary','byte-identical-alias');content=self.images[PRIMARY]
        with patch.object(redcn,'fetch_bytes',return_value=(content,'image/png')):
            first=photo_store.load_photo(self.directory,PRIMARY)
            other=photo_store.load_photo(self.directory,alias)
        self.assertEqual(first['id'],other['id'])
        self.assertEqual(photo_store.load_photo(self.directory,PRIMARY)['sourceUrl'],PRIMARY)
        self.assertEqual(photo_store.accepted_photo_for_url(self.directory,PRIMARY)['original']['sourceUrl'],PRIMARY)
        self.assertEqual(photo_store.accepted_photo_for_url(self.directory,alias)['original']['sourceUrl'],alias)

    def test_cli_keeps_primary_original_and_exports_the_same_prepared_source_as_phone(self):
        card_id=self.prepare();choice=photo_store.accepted_photo_for_url(self.directory,PRIMARY,source=self.source)
        with patch.object(redcn,'fetch_bytes',side_effect=AssertionError('CLI reuses the same observed originals')):
            rows=redcn.download_photos([PRIMARY],self.directory/'cli-export',cache_dir=self.directory,photo_items=[self.source])
        row=rows[0];self.assertNotIn('error',row)
        self.assertEqual(Path(row['path']).read_bytes(),self.images[PRIMARY]);self.assertEqual(Path(row['prepared_path']).read_bytes(),self.images[LARGE])
        self.assertEqual(row['selectedId'],choice['selected']['id']);self.assertEqual(row['selectionReason'],'observed-variant')
        self.assertEqual(phone_relay.public_card(self.directory,card_id)['media'][0]['prepared']['id'],row['selectedId'])
        self.assertFalse(photo_store.asset(self.directory,row['originalId'])[2].get('attached',False))

    def test_cli_automatic_crop_targets_the_same_selected_original_and_manual_crop_has_priority(self):
        self.prepare();choice=photo_store.accepted_photo_for_url(self.directory,PRIMARY,source=self.source)
        with patch.object(redcn,'fetch_bytes',side_effect=AssertionError('Reuse originals')),patch.object(photo_store,'crop_photo',return_value={'status':'review','autoStatus':'unchanged'}) as crop:
            row=redcn.download_photos([PRIMARY],self.directory/'cli-auto',crop=True,cache_dir=self.directory,photo_items=[self.source])[0]
        self.assertEqual(crop.call_args.args[1],choice['selectedOriginal']['id']);self.assertEqual(row['selectedId'],choice['selected']['id'])
        with patch.object(redcn,'fetch_bytes',side_effect=AssertionError('Reuse originals')):
            row=redcn.download_photos([PRIMARY],self.directory/'cli-manual',crop_bottom=10,cache_dir=self.directory,photo_items=[self.source])[0]
        accepted=photo_store.accepted_photo_for_url(self.directory,PRIMARY,source=self.source)
        self.assertEqual(accepted['reason'],'manual');self.assertEqual(row['selectedId'],accepted['selected']['id'])
        self.assertEqual(Path(row['crop_path']).read_bytes(),photo_store.asset(self.directory,accepted['selected']['id'])[0])

    def test_cli_crop_failure_keeps_the_accepted_variant_exportable_without_losing_primary(self):
        self.prepare();choice=photo_store.accepted_photo_for_url(self.directory,PRIMARY,source=self.source)
        with patch.object(redcn,'fetch_bytes',side_effect=AssertionError('Reuse originals')),patch.object(photo_store,'crop_photo',side_effect=RuntimeError('fixture crop failed')):
            row=redcn.download_photos([PRIMARY],self.directory/'cli-crop-failed',crop=True,cache_dir=self.directory,photo_items=[self.source])[0]
        self.assertEqual(row['crop']['errorClass'],'RuntimeError');self.assertNotIn('error',row)
        self.assertEqual(row['selectedId'],choice['selected']['id']);self.assertEqual(Path(row['prepared_path']).read_bytes(),self.images[LARGE]);self.assertEqual(Path(row['path']).read_bytes(),self.images[PRIMARY])

    def test_replacing_explicit_source_identity_does_not_inherit_another_groups_variants(self):
        self.result['listing']['photoItems'][0]['isLayout']=True
        self.result['fields']['photoItems']=[{'url':PRIMARY,'photoId':'different-source-id'}]
        item=phone_relay.photo_source_items(self.result)[PRIMARY]
        self.assertEqual(item['photoId'],'different-source-id');self.assertNotIn('variants',item);self.assertNotIn('observedVariants',item)
        self.assertTrue(item['isLayout'],'A layout cannot silently become an apartment photo')
        self.result['listing']['photoItems'][0].pop('isLayout')
        fetched=[]
        with patch.object(redcn,'fetch_bytes',side_effect=lambda url,**kw:(fetched.append(url) or self.fetch(url,**kw))),patch.object(photo_store,'auto_crop_photo',return_value={'autoStatus':'unchanged'}):phone_relay.cache_photos(self.directory,self.result)
        self.assertEqual(fetched,[PRIMARY]);self.assertIsNone(photo_variants.variant_decision(self.directory,PRIMARY))
        self.result['fields']['photoItems']=[{'url':PRIMARY,'photoId':self.source['photoId'],'order':5}]
        merged=phone_relay.photo_source_items(self.result)[PRIMARY]
        self.assertEqual(merged['observedVariants'],self.source['observedVariants']);self.assertEqual(merged['order'],5)

    def test_cached_and_refetched_identical_bytes_do_not_relabel_old_witnesses_with_new_photo_id(self):
        with patch.object(redcn,'fetch_bytes',side_effect=self.fetch):first=photo_store.load_photo(self.directory,PRIMARY,source=self.source)
        replacement={'url':PRIMARY,'photoId':'replacement-without-old-witnesses'}
        with patch.object(redcn,'fetch_bytes',side_effect=AssertionError('Reuse exact cached bytes')):
            cached=photo_store.load_photo(self.directory,PRIMARY,source=replacement)
        self.assertEqual(cached['id'],first['id']);self.assertEqual(cached['photoId'],replacement['photoId']);self.assertNotIn('variants',cached);self.assertNotIn('observedVariants',cached)
        with patch.object(redcn,'fetch_bytes',side_effect=self.fetch):refetched=photo_store.load_photo(self.directory,PRIMARY,refresh=True,source={**replacement,'photoId':'third-source-group'})
        self.assertEqual(refetched['id'],first['id']);self.assertEqual(refetched['photoId'],'third-source-group');self.assertNotIn('observedVariants',refetched)
        alias=PRIMARY.replace('primary','unknown-alias')
        with patch.object(redcn,'fetch_bytes',return_value=self.fetch(PRIMARY)):
            unknown=photo_store.load_photo(self.directory,alias)
        self.assertEqual(unknown['id'],first['id']);self.assertNotIn('photoId',unknown);self.assertNotIn('observedVariants',unknown)

    def test_explicit_empty_editor_group_list_clears_old_source_choice_and_keeps_layout_exclusion(self):
        card_id=self.prepare();self.result['fields']['photoItems']=[]
        self.assertNotIn(PRIMARY,phone_relay.photo_source_items(self.result))
        local_server.save_result(self.directory,self.result,card_id)
        primary=photo_store.load_photo(self.directory,PRIMARY)
        self.assertEqual(phone_relay.public_card(self.directory,card_id)['media'][0]['prepared']['id'],primary['id'])
        layout={'url':TINY,'photoId':'known-layout','isLayout':True,'variants':{'fullUrl':TINY}}
        self.result['listing']['photoItems'].append(layout);self.result['listing']['images'].append(TINY)
        self.assertTrue(phone_relay.photo_source_items(self.result)[TINY]['isLayout']);self.assertNotIn(TINY,phone_relay.source_images(self.result))


if __name__=='__main__':unittest.main()
