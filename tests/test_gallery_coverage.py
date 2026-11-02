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
from gallery_coverage import gallery_coverage,normalized_coverage,observed_images,merge_gallery_items,cian_photo_url_identity
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
        self.assertTrue(audit['sectionsCompleted'],'Section visibility and album completeness are distinct')
        self.assertFalse(audit['completed'],'A visible ready slide cannot prove the complete album')
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

    def test_main_and_thumbnail_urls_share_only_the_literal_cian_family(self):
        mains=['https://images.cdn-cian.ru/images/fixture-apartment-'+str(9300000000+n)+'-1.jpg' for n in range(13)]
        thumbs=[url.replace('-1.jpg','-2.jpg') for url in mains]
        raw=[{'photoId':'dom-'+str(index),'identitySource':'observed-dom-node','url':url,'observedVariants':[{'url':url,'source':'src'}]} for index,url in enumerate(mains[:3]+thumbs)]
        merged=merge_gallery_items([],raw,redcn.allowed_url,'https://www.cian.ru/')
        self.assertEqual(len(merged),13);self.assertEqual([row['url'] for row in merged],mains[:3]+thumbs[3:])
        self.assertTrue(all(row['identitySource']=='observed-cian-photo-url' for row in merged))
        observed=observed_images(mains[:3]+thumbs,redcn.allowed_url,'https://www.cian.ru/',merged)
        coverage=gallery_coverage([],observed,structured_count=13,photo_items=merged)
        self.assertEqual((coverage['observed'],coverage['mainLinksObserved'],coverage['previewOnly']),(13,3,10))
        self.assertEqual(coverage['missing'],0);self.assertEqual(coverage['state'],'partial');self.assertFalse(coverage['completenessVerified'])
        merged=merge_gallery_items(merged,[{'photoId':'dom-later-'+str(index),'url':url,'observedVariants':[{'url':url,'source':'src'}]} for index,url in enumerate(mains[3:])],redcn.allowed_url,'https://www.cian.ru/')
        self.assertEqual([row['url'] for row in merged],mains)
        self.assertTrue(all({item['url'] for item in row['observedVariants']}=={main,thumb} for row,main,thumb in zip(merged,mains,thumbs)))
        full=gallery_coverage([],mains+thumbs,structured_count=13,photo_items=merged)
        self.assertEqual(full['state'],'complete');self.assertEqual(full['mainLinksObserved'],13);self.assertEqual(full['previewOnly'],0)
        self.assertEqual(normalized_coverage(full),full)
        self.assertEqual(__import__('photo_store')._source_metadata(merged[0])['identitySource'],'observed-cian-photo-url')

    def test_cdn_identity_rejects_lookalike_hosts_and_does_not_merge_reused_ids(self):
        url='https://images.cdn-cian.ru/images/apartment-one-9300000123-2.jpg'
        identity=cian_photo_url_identity(url)
        self.assertEqual(identity['key'],'https://images.cdn-cian.ru/images/apartment-one-9300000123')
        self.assertEqual(cian_photo_url_identity(url.replace('.jpg','.webp')+'?observed=1')['key'],identity['key'])
        for unsafe in [url.replace('https:','http:'),url.replace('images.cdn-cian.ru','images.cdn-cian.ru.evil.example'),url.replace('images.cdn-cian.ru','user@images.cdn-cian.ru'),url.replace('images.cdn-cian.ru','images.cdn-cian.ru:8443')]:
            self.assertIsNone(cian_photo_url_identity(unsafe))
        other=url.replace('apartment-one','apartment-two')
        rows=[{'url':value,'photoId':'9300000123','identitySource':'platform-photo-id','observedVariants':[{'url':value,'source':'src'}]} for value in [url,other]]
        self.assertEqual(len(merge_gallery_items([],rows,redcn.allowed_url,'https://www.cian.ru/')),2)
        self.assertEqual(observed_images([url,other],redcn.allowed_url,'https://www.cian.ru/'),[url,other])

    def test_observed_thumbnail_order_and_explicit_photo_list_keep_source_cover(self):
        mains=['https://images.cdn-cian.ru/images/fixture-order-'+str(9300000200+n)+'-1.jpg' for n in range(3)]
        rows=[{'url':url,'photoId':'dom-'+str(index),'observedVariants':[{'url':url,'source':'src'}],
               'galleryIndex':index,'galleryIndexSource':'thumbnail-rail'} for index,url in enumerate(mains)]
        merged=merge_gallery_items([],list(reversed(rows)),redcn.allowed_url,'https://www.cian.ru/')
        self.assertEqual([row['url'] for row in merged],mains)
        self.assertEqual(observed_images(list(reversed(mains)),redcn.allowed_url,'https://www.cian.ru/',merged),mains)
        source=[{'url':url,'photoId':str(index),'order':index,'source':'CIAN offer','variants':{'fullUrl':url}} for index,url in enumerate(mains)]
        contradictory=[{**row,'galleryIndex':2-index} for index,row in enumerate(rows)]
        merged=merge_gallery_items(source,contradictory,redcn.allowed_url,'https://www.cian.ru/')
        self.assertEqual([row['url'] for row in merged],mains,'Source photos[] order takes precedence over DOM ordinal conflicts')
        self.assertFalse(any('_captureOrder' in row or 'galleryIndexSource' in row for row in merged))

    def test_observed_fullurl_wins_unknown_renditions_stay_unknown_and_layout_family_is_excluded(self):
        main='https://images.cdn-cian.ru/images/04/104/204/9300000123-1.jpg'
        thumb=main.replace('-1.jpg','-2.jpg');unknown=main.replace('-1.jpg','-0.jpg')
        self.assertEqual(observed_images([thumb],redcn.allowed_url,'https://www.cian.ru/'),[thumb],'Never fabricate -1 from a thumbnail')
        self.assertEqual(observed_images([unknown,main],redcn.allowed_url,'https://www.cian.ru/'),[unknown],'Unknown rendition0 may be larger')
        self.assertEqual(gallery_coverage([],[unknown],structured_count=1)['state'],'unknown')
        self.assertEqual(gallery_coverage([],[unknown,main],structured_count=1)['mainLinksObserved'],1,'URL evidence considers all observed family witnesses, not only the chosen canonical')
        self.assertEqual(gallery_coverage([],[thumb,unknown],structured_count=1)['state'],'unknown','An unclassified observed rendition cannot be called thumbnail-only')
        rows=[{'url':unknown,'photoId':'9300000123','variants':{'fullUrl':unknown,'thumbnailUrl':thumb},'observedVariants':[{'url':main,'source':'src'}]}]
        self.assertEqual(observed_images([thumb,main],redcn.allowed_url,'https://www.cian.ru/',rows),[unknown])
        self.assertEqual(gallery_coverage([],[unknown],structured_count=1,photo_items=rows)['mainLinksObserved'],1)
        layout=[{'url':main,'photoId':'9300000123','isLayout':True}]
        self.assertEqual(observed_images([thumb],redcn.allowed_url,'https://www.cian.ru/',layouts=layout),[])
        self.assertEqual(merge_gallery_items([],[{'url':thumb,'photoId':'dom-plan','observedVariants':[{'url':thumb,'source':'src'}]}],redcn.allowed_url,'https://www.cian.ru/',layout),[])

    def test_role_counts_survive_public_roundtrip_and_invalid_roles_fail_closed(self):
        value={'version':2,'observed':13,'expected':13,'mainLinksObserved':3,'previewOnly':10,'state':'complete'}
        clean=normalized_coverage(value);self.assertEqual(clean['state'],'partial')
        ready=source_readiness({'capture':{'completed':True,'sections':[{'section':name,'ready':True,'reads':1} for name in ('gallery','description','characteristics')],'galleryCoverage':clean}})
        self.assertFalse(ready['completed']);self.assertTrue(ready['partial']);self.assertEqual(ready['gallery']['previewOnly'],10)
        for invalid in [{**value,'previewOnly':True},{**value,'mainLinksObserved':14},{'version':2,'observed':13,'expected':13}]:
            normalized=normalized_coverage(invalid)
            self.assertEqual(normalized['state'],'unknown');self.assertFalse(normalized['completenessVerified'])
            self.assertEqual(normalized_coverage(normalized)['state'],'unknown')
            state=source_readiness({'capture':{'completed':True,'sections':[{'section':name,'ready':True,'reads':1} for name in ('gallery','description','characteristics')],'galleryCoverage':normalized}})
            self.assertEqual(state['state'],'unknown');self.assertFalse(state['completed']);self.assertFalse(state['partial'])

    def test_sweep_precedes_slow_probe_and_zero_remaining_budget_cannot_be_complete(self):
        mains=['https://images.cdn-cian.ru/images/fixture-budget-'+str(9400000000+n)+'-1.jpg' for n in range(13)]
        thumbs=[url.replace('-1.jpg','-2.jpg') for url in mains]
        for budget in (0,6):
            with self.subTest(budget=budget):
                clock=Clock();page=SectionPage(clock);original=page.evaluate
                album={'countEvidence':[{'count':13,'source':'data-photo-count'}],'scrollable':True}
                first={**snapshot(images=mains[:3]+thumbs),'galleryState':album,'sectionStates':{name:{'found':True,'ready':True,**({'album':album} if name=='gallery' else {})} for name in ('gallery','description','characteristics')}}
                def evaluate(expression,argument=None):
                    if expression==browser_import.GALLERY_SWEEP:return {'images':mains,'galleryPhotos':[],'galleryStates':[album],'moves':12,'bounded':False,'restored':True}
                    if expression==browser_import.SECTION_PROBE and argument.get('section')=='gallery':clock.value+=7
                    return original(expression,argument)
                page.evaluate=evaluate
                with patch.object(browser_import.time,'monotonic',clock.now):
                    _,audit=browser_import.capture_sections(page,first,clock.now()+20,lambda *_args,**_kwargs:None,budget=budget)
                self.assertTrue(audit['sectionsCompleted']);self.assertEqual(audit['galleryCoverage']['observed'],13)
                if budget:
                    self.assertEqual(audit['browserCalls'][0]['kind'],'gallery-sweep');self.assertEqual(audit['galleryMoves'],12)
                    self.assertTrue(audit['completed']);self.assertEqual(audit['galleryCoverage']['mainLinksObserved'],13)
                else:
                    self.assertEqual(audit['galleryMoves'],0);self.assertFalse(audit['completed']);self.assertTrue(audit['bounded'])
                    self.assertEqual(audit['galleryCoverage']['previewOnly'],10)


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
