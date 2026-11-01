"""Page scroll completeness must never stand in for album completeness."""
import json
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
sys.path.insert(0,str(Path(__file__).resolve().parent))
import browser_import
from gallery_coverage import gallery_coverage,normalized_coverage,observed_images,merge_gallery_items
import redcn
from source_readiness import source_readiness
import test_section_scroll as section_fixture
Clock=section_fixture.Clock
SectionPage=section_fixture.SectionPage
snapshot=section_fixture.snapshot

URLS=['https://images.cdn-cian.ru/images/virtual-'+str(index)+'.jpg' for index in range(19)]


class VirtualGalleryPage(SectionPage):
    def __init__(self,clock,*,scrollable=True,total=19):
        super().__init__(clock);self.index=0;self.scrollable=scrollable;self.total=total

    def album(self):return {'countEvidence':[{'count':self.total,'source':'aria-setsize'}],'scrollable':self.scrollable}

    def evaluate(self,expression,argument=None):
        if expression==browser_import.GALLERY_SWEEP:
            self.clock.value+=.016*18;return {'images':URLS,'galleryPhotos':[],'galleryStates':[self.album()],'moves':18,'bounded':False,'restored':True}
        if expression==browser_import.GALLERY_SCROLL:
            before=self.index*300
            if argument.get('restore') is not None:self.index=0;return {'moved':True,'before':before,'after':0}
            moved=self.index<18
            if moved:self.index+=1
            return {'moved':moved,'before':before,'after':self.index*300,'atEnd':self.index==18}
        if expression==browser_import.SNAPSHOT:
            result=snapshot(images=[URLS[self.index]])
            result.update(galleryState=self.album(),sectionStates={kind:{'found':True,'ready':True,'album':self.album()} if kind=='gallery' else {'found':True,'ready':True} for kind in ('gallery','description','characteristics')})
            return result
        result=super().evaluate(expression,argument)
        if expression==browser_import.SECTION_PROBE:
            if argument.get('section')=='all':result['sections']['gallery']['album']=self.album()
            elif argument.get('section')=='gallery':result['album']=self.album()
        return result


class GalleryCoverageTests(unittest.TestCase):
    def test_virtualized_slides_accumulate_without_clicks_and_restore_gallery(self):
        clock=Clock();page=VirtualGalleryPage(clock)
        initial=page.evaluate(browser_import.SNAPSHOT)
        with patch.object(browser_import.time,'monotonic',clock.now):
            result,audit=browser_import.capture_sections(page,initial,clock.now()+20,lambda *_args,**_kwargs:None)
        self.assertEqual(result['images'],URLS);self.assertEqual(audit['galleryMoves'],18)
        self.assertEqual(audit['galleryCoverage']['state'],'complete');self.assertEqual(audit['galleryCoverage']['missing'],0)
        self.assertEqual(page.index,0);self.assertTrue(page.restored)

    def test_ready_visible_gallery_reports_eighteen_missing_unmounted_slides(self):
        clock=Clock();page=VirtualGalleryPage(clock,scrollable=False)
        initial=page.evaluate(browser_import.SNAPSHOT)
        with patch.object(browser_import.time,'monotonic',clock.now):
            _,audit=browser_import.capture_sections(page,initial,clock.now()+20,lambda *_args,**_kwargs:None)
        self.assertTrue(audit['completed'],'Section visibility and album completeness are distinct')
        result=source_readiness({'capture':audit})
        self.assertTrue(result['partial']);self.assertEqual(result['gallery']['observed'],1)
        self.assertEqual(result['gallery']['expected'],19);self.assertEqual(result['gallery']['missing'],18)
        self.assertEqual(source_readiness({'sourceReadiness':result}),result)

    def test_total_unknown_conflicts_and_retention_limit_are_not_complete_claims(self):
        unknown=gallery_coverage([],URLS);self.assertEqual(unknown['state'],'unknown');self.assertIsNone(unknown['missing'])
        conflict=gallery_coverage([{'countEvidence':[{'count':18,'source':'gallery-text'},{'count':19,'source':'aria-setsize'}]}],URLS)
        self.assertEqual(conflict['state'],'unknown');self.assertTrue(conflict['countConflict'])
        large=gallery_coverage([],['image-'+str(n) for n in range(40)],structured_count=40)
        ready=source_readiness({'capture':{'completed':True,'sections':[{'section':kind,'reads':1,'found':True,'ready':True} for kind in ('gallery','description','characteristics')],'galleryCoverage':large}})
        self.assertTrue(ready['partial']);self.assertEqual(ready['gallery']['overLimit'],10)

    def test_canonicalization_uses_only_explicit_same_photo_id_variants_and_excludes_layout(self):
        thumb=URLS[0].replace('.jpg','-thumb.jpg');unrelated=URLS[0].replace('.jpg','-unobserved.jpg')
        observed=[thumb,URLS[0],URLS[1],URLS[2],unrelated]
        rows=[{'url':URLS[0],'photoId':'one','variants':{'fullUrl':URLS[0],'thumbnailUrl':thumb}}]
        layouts=[{'url':URLS[2],'photoId':'layout','isLayout':True}]
        result=observed_images(observed,redcn.allowed_url,'https://www.cian.ru/',rows,layouts)
        self.assertEqual(result,[URLS[0],URLS[1],unrelated])
        self.assertEqual(observed_images([thumb,URLS[0]],redcn.allowed_url,'https://www.cian.ru/',[{'url':URLS[0],'variants':{'thumbnailUrl':thumb}}]),[thumb,URLS[0]])

    def test_public_counts_drop_unknown_metadata_and_recompute_instead_of_trusting_state(self):
        raw={'state':'complete','observed':1,'expected':19,'missing':0,'rawHtml':'PRIVATE','cookies':'PRIVATE','countEvidence':[{'count':19,'source':'aria-setsize','html':'PRIVATE'},{'count':3,'source':'PRIVATE'}]}
        clean=normalized_coverage(raw);self.assertEqual(clean['state'],'partial');self.assertEqual(clean['missing'],18);self.assertNotIn('PRIVATE',json.dumps(clean))
        self.assertIsNone(normalized_coverage({'observed':True,'expected':19}))

    def test_literal_photo_id_attaches_disjoint_dom_url_but_local_hash_cannot_claim_identity(self):
        existing=[{'url':URLS[0],'photoId':'123','source':'CIAN offer','variants':{'fullUrl':URLS[0]}}]
        observed={'url':URLS[1],'photoId':'123','identitySource':'platform-photo-id','observedVariants':[{'url':URLS[1],'source':'srcset'}]}
        output=merge_gallery_items(existing,[observed],redcn.allowed_url,'https://www.cian.ru/')
        self.assertEqual(len(output),1);self.assertEqual(output[0]['observedVariants'][0]['url'],URLS[1])
        observed['identitySource']='observed-dom-node'
        self.assertEqual(len(merge_gallery_items(existing,[observed],redcn.allowed_url,'https://www.cian.ru/')),2)

    def test_fixture_manifest_explicitly_does_not_close_the_real_source_corpus_gate(self):
        manifest=json.loads((Path(__file__).parent/'fixtures/cian-source-contracts.manifest.json').read_text())
        self.assertFalse(manifest['isRealCianCorpus']);self.assertFalse(manifest['fulfillsRCN020'])
        self.assertTrue(all((Path(__file__).parent/'fixtures'/row['path']).is_file() for row in manifest['fixtures']))

    def test_more_than_six_lazy_variants_remain_in_the_source_witness_passport(self):
        variants=[{'url':url,'source':'data-srcset'} for url in URLS[:12]]
        observed={'url':URLS[0],'photoId':'twelve-variants','identitySource':'platform-photo-id','observedVariants':variants}
        output=merge_gallery_items([],[observed],redcn.allowed_url,'https://www.cian.ru/')
        self.assertEqual(output[0]['observedVariants'],variants)
        source=__import__('photo_store')._source_metadata(output[0])
        self.assertEqual(source['observedVariants'],variants)


class VirtualGalleryBrowserAcceptance(unittest.TestCase):
    setUpClass=classmethod(section_fixture.SectionBrowserAcceptance.setUpClass.__func__)
    tearDownClass=classmethod(section_fixture.SectionBrowserAcceptance.tearDownClass.__func__)
    setUp=section_fixture.SectionBrowserAcceptance.setUp
    tearDown=section_fixture.SectionBrowserAcceptance.tearDown
    def test_synthetic_virtual_gallery_reports_all_observed_slides(self):
        self.page.set_content((Path(__file__).parent/'fixtures/cian-virtual-gallery.synthetic.html').read_text())
        self.page.wait_for_function('document.getElementById("fixtureSlide").complete')
        first=self.page.evaluate(browser_import.SNAPSHOT)
        self.assertEqual(len(first['images']),1)
        result,audit=browser_import.capture_sections(self.page,first,time.monotonic()+10,lambda *_args,**_kwargs:None,section_wait=2,budget=6)
        self.assertEqual(len(result['images']),19);self.assertEqual(audit['galleryCoverage']['state'],'complete')
        self.assertEqual(self.page.evaluate('fixtureViewport.scrollLeft'),0)

    def test_transform_style_virtual_gallery_keeps_explicit_missing_count(self):
        self.page.set_content((Path(__file__).parent/'fixtures/cian-virtual-gallery.synthetic.html').read_text())
        self.page.evaluate('fixtureViewport.style.overflowX="hidden"')
        first=self.page.evaluate(browser_import.SNAPSHOT)
        _,audit=browser_import.capture_sections(self.page,first,time.monotonic()+10,lambda *_args,**_kwargs:None)
        self.assertEqual(audit['galleryCoverage']['missing'],18)
        self.assertTrue(source_readiness({'capture':audit})['partial'])

    def test_picture_variants_and_nested_recommendations_do_not_inflate_count(self):
        self.page.route('https://images.cdn-cian.ru/**',lambda route:route.abort())
        self.page.set_content('<main><h1 data-name="OfferTitle">Квартира 54 м²</h1><div data-name="PriceInfo">80000 ₽ в месяц</div><div data-name="OfferGallery" data-photo-count="1"><picture data-photo-id="literal-17"><source type="image/x-fixture-unsupported" srcset="https://images.cdn-cian.ru/images/frame.webp 1200w"><img data-original="https://images.cdn-cian.ru/images/frame.jpg" src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"></picture><div data-name="Recommendations" aria-label="1 of 30"><img src="https://images.cdn-cian.ru/unrelated.jpg">30 фото</div></div><div data-name="OfferDescription">Объявление для проверки принадлежности галереи. Мебель, кухня, шкаф и рабочий стол.</div></main>')
        result=self.page.evaluate(browser_import.SNAPSHOT)
        self.assertEqual(len(result['images']),1);self.assertEqual(len(result['galleryPhotos']),1)
        self.assertEqual(result['galleryPhotos'][0]['photoId'],'literal-17')
        self.assertEqual(len(result['galleryPhotos'][0]['observedVariants']),2)
        self.assertEqual({row['count'] for row in result['galleryState']['countEvidence']},{1})
