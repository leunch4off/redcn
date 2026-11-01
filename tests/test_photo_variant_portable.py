"""Portable choices validate actual source bytes before any atomic restore."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
sys.path.insert(0,str(Path(__file__).resolve().parent))
import photo_store
import photo_variants
import redcn
from test_photo_variant_pipeline import PRIMARY,LARGE,TINY,raster


class PortablePhotoVariantTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.directory=Path(self.temp.name)
        self.source={'photoId':'portable-17','variants':{'fullUrl':PRIMARY,'thumbnailUrl':TINY},'observedVariants':[{'url':LARGE,'source':'srcset'}]}
        self.images={PRIMARY:raster((600,600),'#f00'),LARGE:raster((800,800),'#00f'),TINY:raster((120,120),'#0f0')}
        with patch.object(redcn,'fetch_bytes',side_effect=lambda url,**kw:(self.images[url],'image/jpeg')):
            self.decision=photo_variants.probe_variants(self.directory,PRIMARY,self.source)
        self.assets={}
        for row in self.decision['candidates']:
            content,mime,metadata=photo_store.asset(self.directory,row['id']);self.assets[row['id']]={'content':content,'mime':mime,'metadata':metadata}

    def tearDown(self):self.temp.cleanup()

    def test_export_and_pure_validation_preserve_the_selected_source_and_record_without_io(self):
        exported=photo_variants.export_decision(self.directory,PRIMARY)
        with patch.object(redcn,'fetch_bytes',side_effect=AssertionError('Portable validation must be offline')),patch.object(photo_store,'_connection',side_effect=AssertionError('Portable validation must be pure')),patch.object(photo_store,'recognize',side_effect=AssertionError('Portable validation must not run OCR')):
            validated=photo_variants.validate_portable_decision(PRIMARY,exported,assets=self.assets)
            canonical,group,payload,digest=photo_variants.portable_record(validated)
        self.assertEqual(canonical,PRIMARY);self.assertEqual(group,self.decision['groupSha'])
        self.assertEqual(json.loads(payload)['selected']['id'],self.decision['selected']['id']);self.assertEqual(len(digest),64)
        self.assertFalse(validated['selected']['cleanlinessConfirmed']);self.assertEqual(validated['observed'],self.decision['observed'])

    def test_tampered_group_selected_sha_dimensions_mime_quality_and_clean_claims_are_rejected(self):
        exported=photo_variants.export_decision(self.directory,PRIMARY)
        changes=[lambda v:v.update(canonicalUrl=TINY),lambda v:v.update(groupSha='f'*64),
            lambda v:v['selected'].update(id='f'*64),lambda v:v['candidates'][0].update(width=1000),
            lambda v:v['candidates'][0].update(mime='image/webp'),lambda v:v['candidates'][1]['quality'].update(resolutionEligible=True),
            lambda v:v['candidates'][0].update(cleanlinessConfirmed=True),lambda v:v['observed'][0]['provenance'].append('invented-clean-url')]
        for change in changes:
            invalid=copy.deepcopy(exported);change(invalid)
            with self.subTest(change=repr(change)),self.assertRaises(ValueError):photo_variants.validate_portable_decision(PRIMARY,invalid,assets=self.assets)
        malformed=copy.deepcopy(exported);malformed.pop('observed');malformed['candidates']=None
        with self.assertRaises(ValueError):photo_variants.validate_portable_decision(PRIMARY,malformed,assets=self.assets)

    def test_missing_or_corrupt_declined_original_fails_instead_of_silently_dropping_passport(self):
        tiny=next(row for row in self.decision['candidates'] if row['url']==TINY)
        target=self.directory/'media'/tiny['id'];target.unlink()
        with self.assertRaisesRegex(ValueError,'отсутствует'):photo_variants.export_decision(self.directory,PRIMARY)
        target.write_bytes(b'fixture corrupt file')
        with self.assertRaisesRegex(ValueError,'повреждён'):photo_variants.export_decision(self.directory,PRIMARY)

    def test_changed_candidate_url_mapping_fails_export(self):
        fresh=raster((900,900),'#fff')
        with patch.object(redcn,'fetch_bytes',return_value=(fresh,'image/png')):photo_store.load_photo(self.directory,LARGE,refresh=True)
        with self.assertRaisesRegex(ValueError,'изменился'):photo_variants.export_decision(self.directory,PRIMARY)

    def test_unavailable_candidates_do_not_require_unfetched_bytes_and_legacy_truncated_is_explicit(self):
        with patch.object(redcn,'fetch_bytes',side_effect=lambda url,**kw:(_ for _ in ()).throw(OSError('fixture unavailable')) if url==PRIMARY else (self.images[url],'image/png')):
            value=photo_variants.probe_variants(self.directory,PRIMARY,self.source,refresh=True)
        assets={row['id']:self.assets[row['id']] for row in value['candidates'] if row.get('status')=='available'}
        clean=photo_variants.validate_portable_decision(PRIMARY,value,assets=assets)
        self.assertEqual(clean['candidates'][0]['status'],'unavailable');self.assertEqual(clean['selected']['url'],LARGE)
        legacy=copy.deepcopy(value);legacy.pop('observed');legacy['truncated']=True
        with self.assertRaisesRegex(ValueError,'сокращённый'):photo_variants.validate_portable_decision(PRIMARY,legacy,assets=assets)

    def test_exact_group_lookup_retains_both_historical_passports_without_cross_applying_latest(self):
        first=photo_variants.export_decision(self.directory,PRIMARY,self.source)
        source_b={**self.source,'photoId':'different-group-18','observedVariants':[{'url':LARGE,'source':'data-srcset'}]}
        with patch.object(redcn,'fetch_bytes',side_effect=AssertionError('Existing source bytes remain cached')):
            second=photo_variants.probe_variants(self.directory,PRIMARY,source_b)
        self.assertNotEqual(first['groupSha'],second['groupSha'])
        self.assertEqual(photo_variants.export_decision(self.directory,PRIMARY,group_sha=first['groupSha'])['photoId'],self.source['photoId'])
        self.assertEqual(photo_variants.export_decision(self.directory,PRIMARY,group_sha=second['groupSha'])['photoId'],source_b['photoId'])
        self.assertIsNone(photo_variants.export_decision(self.directory,PRIMARY,group_sha='f'*64))
        with self.assertRaises(ValueError):photo_variants.export_decision(self.directory,PRIMARY,self.source,group_sha=second['groupSha'])
        with self.assertRaises(ValueError):photo_variants.export_decision(self.directory,PRIMARY,group_sha='not-a-group')


if __name__=='__main__':unittest.main()
