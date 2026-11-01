"""Observed variant choices use actual decoded bytes, not guessed clean URLs."""
import base64
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
from PIL import Image, ImageDraw
import photo_store
import photo_variants as variants

FULL = 'https://images.cdn-cian.ru/images/observed-full.jpg'
THUMB = 'https://images.cdn-cian.ru/images/observed-small.jpg'
SRCSET = 'https://images.cdn-cian.ru/images/observed-srcset.jpg'


def picture(size=(800,600),kind='PNG',color='#456789'):
    image=Image.new('RGB',size,color);draw=ImageDraw.Draw(image)
    draw.rectangle((size[0]//8,size[1]//8,size[0]//2,size[1]//2),outline='white',width=3)
    stream=io.BytesIO();image.save(stream,kind);image.close();return stream.getvalue()


class PhotoVariantTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.directory=Path(self.temp.name)
        self.source={'photoId':'123','variants':{'fullUrl':FULL,'thumbnailUrl':THUMB}}
        self.pictures={FULL:picture(),THUMB:picture((160,120)),SRCSET:picture((1200,900),'WEBP')}
        self.calls=[]

    def tearDown(self):self.temp.cleanup()

    def attach(self,url):
        content=self.pictures[url]
        if isinstance(content,Exception):raise content
        return photo_store.attach_photo(self.directory,url,'data:image/jpeg;base64,'+base64.b64encode(content).decode(),source=self.source)

    def load(self,directory,url,**options):
        self.calls.append((url,options));return self.attach(url)

    def probe(self,source=None,**kwargs):
        with patch.object(photo_store,'load_photo',side_effect=self.load):
            return variants.probe_variants(self.directory,FULL,source or self.source,**kwargs)

    def test_full_original_wins_even_when_thumbnail_appears_clean(self):
        decision=self.probe()
        self.assertEqual(decision['selected']['url'],FULL);self.assertEqual(decision['reason'],'canonical-resolution-eligible')
        self.assertEqual(decision['status'],'selected');self.assertEqual(len(decision['candidates']),2)
        self.assertEqual(decision['candidates'][1]['quality']['status'],'small-preview')
        self.assertFalse(decision['watermarkAffectsSelection']);self.assertFalse(decision['selected']['cleanlinessConfirmed'])
        self.assertTrue(all(0<options['network_timeout']<=8 for _,options in self.calls))

    def test_actual_webp_mime_dimensions_decoding_and_statistics_are_saved(self):
        self.pictures[FULL]=self.pictures[SRCSET]
        decision=self.probe()
        selected=decision['selected']
        self.assertEqual(selected['mime'],'image/webp');self.assertEqual(selected['mimeEvidence'],'magic-and-decoder')
        self.assertEqual((selected['width'],selected['height']),(1200,900))
        self.assertGreater(selected['quality']['edgeRms'],0);self.assertGreater(selected['quality']['entropyBits'],0)
        self.assertEqual(selected['quality']['statisticsMeaning'],'descriptive-not-calibrated')
        self.assertEqual(photo_store.asset(self.directory,selected['id'])[0],self.pictures[FULL])

    def test_unavailable_full_can_choose_an_explicit_eligible_srcset_without_inventing_urls(self):
        self.pictures[FULL]=OSError('fixture full unavailable')
        source={**self.source,'observedVariants':[{'url':SRCSET,'source':'srcset'},
            {'url':'https://evil.test/clean.jpg','source':'srcset'},
            {'url':'https://images.cdn-cian.ru/not-observed.jpg','source':'invented-clean'}]}
        decision=self.probe(source)
        self.assertEqual(decision['selected']['url'],SRCSET);self.assertEqual(decision['status'],'selected')
        self.assertEqual(decision['candidates'][0]['status'],'unavailable')
        self.assertEqual([url for url,_ in self.calls],[FULL,THUMB,SRCSET])
        self.assertTrue(decision['retryable']);self.assertIn('observed.srcset',decision['selected']['provenance'])

    def test_only_small_preview_stays_review_and_is_never_auto_eligible(self):
        self.pictures[FULL]=OSError('fixture missing full')
        decision=self.probe()
        self.assertEqual(decision['selected']['url'],THUMB);self.assertEqual(decision['status'],'review')
        self.assertEqual(decision['reason'],'preview-requires-review')
        self.assertFalse(decision['selected']['quality']['resolutionEligible'])

    def test_extreme_aspect_preview_is_decoded_without_inventing_a_quality_failure(self):
        self.pictures[FULL]=picture((8000,32))
        decision=self.probe()
        self.assertEqual(decision['candidates'][0]['status'],'available')
        self.assertEqual(decision['status'],'review');self.assertEqual(decision['selected']['quality']['status'],'small-preview')

    def test_cache_reuses_same_observed_group_without_network_and_new_group_reprobes(self):
        first=self.probe();self.calls.clear()
        second=self.probe()
        self.assertTrue(second['cached']);self.assertEqual(first['decisionSha'],second['decisionSha']);self.assertEqual(self.calls,[])
        updated={**self.source,'observedVariants':[{'url':SRCSET,'source':'srcset'}]}
        self.assertIsNone(variants.variant_decision(self.directory,FULL,updated))
        third=self.probe(updated)
        self.assertNotEqual(third['groupSha'],first['groupSha']);self.assertIn(SRCSET,[url for url,_ in self.calls])
        self.assertEqual(variants.variant_decision(self.directory,FULL)['groupSha'],third['groupSha'])

    def test_original_argument_avoids_refetch_but_its_bytes_are_verified(self):
        original=self.attach(FULL);self.calls.clear()
        decision=self.probe(original=original)
        self.assertEqual([url for url,_ in self.calls],[THUMB]);self.assertEqual(decision['selected']['id'],original['id'])
        (self.directory/'media'/original['id']).write_bytes(b'fixture changed immutable bytes')
        self.calls.clear();failed=self.probe(original=original)
        self.assertEqual(failed['candidates'][0]['status'],'unavailable');self.assertEqual(failed['status'],'review')

    def test_changed_canonical_content_invalidates_old_cached_choice(self):
        first=self.probe()
        self.pictures[FULL]=picture((1024,768),color='#aabbcc');replacement=self.attach(FULL)
        second=self.probe(original=replacement)
        self.assertFalse(second['cached']);self.assertNotEqual(first['selected']['id'],second['selected']['id'])

    def test_bounded_candidate_count_and_time_budget_record_unchecked_variants(self):
        observed=[]
        for index in range(10):
            url='https://images.cdn-cian.ru/images/extra-'+str(index)+'.jpg';self.pictures[url]=picture((640,640),color='#aabbcc');observed.append({'url':url,'source':'srcset'})
        decision=self.probe({**self.source,'observedVariants':observed})
        self.assertEqual(len(self.calls),6);self.assertEqual(decision['probeLimit'],6);self.assertTrue(decision['truncated'])
        self.assertEqual(decision['observedCount'],12)
        self.calls.clear()
        clock=iter((0,0,2))
        with patch.object(variants.time,'monotonic',side_effect=lambda:next(clock)):
            timed=self.probe(refresh=True,max_seconds=1)
        self.assertEqual(len(self.calls),1);self.assertEqual(timed['candidates'][1]['reason'],'time-budget');self.assertTrue(timed['retryable'])

    def test_cancellation_and_invalid_identity_do_not_probe_or_create_database(self):
        event=threading.Event();event.set()
        with self.assertRaisesRegex(ValueError,'отменена'):self.probe(cancel=event)
        with self.assertRaisesRegex(ValueError,'photoId'):self.probe({'variants':{'fullUrl':FULL}})
        self.assertEqual(self.calls,[]);self.assertFalse((self.directory/'photo-variant-decisions.sqlite3').exists())

    def test_preserves_rejected_sources_and_corrupt_decision_is_not_trusted(self):
        decision=self.probe()
        for candidate in decision['candidates']:
            self.assertEqual(photo_store.asset(self.directory,candidate['id'])[0],self.pictures[candidate['url']])
        path=self.directory/'photo-variant-decisions.sqlite3'
        self.assertEqual(path.stat().st_mode&0o777,0o600)
        with sqlite3.connect(path) as db:
            original=db.execute('SELECT payload FROM decisions ORDER BY id DESC LIMIT 1').fetchone()[0]
            self.assertNotIn('data:image',original)
            db.execute('UPDATE decisions SET payload=?',('{"privateAlteration":true}',))
        with self.assertRaisesRegex(ValueError,'сумма'):variants.variant_decision(self.directory,FULL)

    def test_read_before_probe_is_nonmutating_and_never_downloads(self):
        with patch.object(photo_store,'load_photo',side_effect=AssertionError('Read must not download')):
            self.assertIsNone(variants.variant_decision(self.directory,FULL,self.source))
        self.assertEqual(list(self.directory.iterdir()),[])


if __name__=='__main__':unittest.main()
