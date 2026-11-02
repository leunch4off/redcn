"""Observed gallery DOM and explicitly synthetic delayed-slide regression.

The archive-derived fixtures contain no source photos. Tiny local GIF responses
test DOM scheduling only and never establish source resolution or image quality.
"""
import base64
import json
from pathlib import Path
import sys
import time
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
sys.path.insert(0,str(Path(__file__).resolve().parent))
import browser_import
import redcn
from gallery_coverage import gallery_coverage,merge_gallery_items,observed_images
import test_section_scroll as fixture

FIXTURES=Path(__file__).parent/'fixtures'
PIXEL=base64.b64decode('R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7')


class CianFullsizeBrowserTests(unittest.TestCase):
    setUpClass=classmethod(fixture.SectionBrowserAcceptance.setUpClass.__func__)
    tearDownClass=classmethod(fixture.SectionBrowserAcceptance.tearDownClass.__func__)
    setUp=fixture.SectionBrowserAcceptance.setUp
    tearDown=fixture.SectionBrowserAcceptance.tearDown

    def open_fixture(self,name):
        self.page.route('https://images.cdn-cian.ru/**',lambda route:route.fulfill(content_type='image/gif',body=PIXEL))
        self.page.set_content((FIXTURES/name).read_text())

    def collect(self,value):
        items=merge_gallery_items([],value['galleryPhotos'],redcn.allowed_url,'https://www.cian.ru/')
        images=observed_images(value['images'],redcn.allowed_url,'https://www.cian.ru/',items)
        return items,images

    def test_archive_dom_deduplicates_main_and_thumbnails_without_recommendations_or_plan(self):
        for name,count in [('cian-gallery-13-v29-fullsize.html',13),('cian-gallery-10-layout-v29-fullsize.html',10)]:
            with self.subTest(fixture=name):
                self.open_fixture(name)
                value=self.page.evaluate(browser_import.SNAPSHOT)
                items,images=self.collect(value)
                self.assertEqual(len(images),count);self.assertEqual(len(items),count)
                self.assertTrue(all(url.endswith('-1.jpg') for url in images))
                self.assertTrue(all(row['identitySource']=='observed-cian-photo-url' for row in items))
                self.assertTrue(all(len(row['observedVariants'])==2 for row in items))
                self.assertNotIn('recommendation',json.dumps(items).lower())
                self.assertNotIn('9500000100',json.dumps(items),'The separate floor plan is not an apartment photograph')
                coverage=gallery_coverage([value['galleryState']],images,structured_count=count,photo_items=items)
                self.assertEqual(coverage['state'],'complete');self.assertEqual(coverage['mainLinksObserved'],count)
                self.assertEqual(coverage['previewOnly'],0)
                self.page.unroute('https://images.cdn-cian.ru/**')

    def test_synthetic_delayed_main_ul_is_swept_before_thumbnail_rail_and_restored(self):
        self.open_fixture('cian-gallery-13-v29-fullsize.html')
        # The saved source archive already has all URLs. This controlled model
        # intentionally withholds ten main URLs until their slide is visited;
        # it is a regression of the capture algorithm, not a claim about source
        # JavaScript or a replay of a live CIAN request.
        self.page.evaluate('''() => {
          const root=document.querySelector('[data-name="OfferGallery"]');
          const main=root.querySelector('[data-name="Scroller"] > ul');
          const thumbs=root.querySelector('[data-name="PaginationThumbsComponent"]');
          const imgs=[...main.querySelectorAll('img')],urls=imgs.map(img=>img.getAttribute('src'));
          root.setAttribute('data-photo-count',String(imgs.length));
          imgs.forEach((img,index)=>{if(index>=3)img.removeAttribute('src');});
          // The thumbnail rail has a greater width ratio, so an old generic
          // largest-scrollWidth/clientWidth picker would visit the wrong rail.
          thumbs.style.width='40px';window.fixtureMainVisits=[];window.fixtureThumbMoves=0;
          main.addEventListener('scroll',()=>{
            const index=Math.round(main.scrollLeft/main.clientWidth);
            window.fixtureMainVisits.push(index);
            for(let n=Math.max(0,index-1);n<=Math.min(imgs.length-1,index+1);n++)imgs[n].setAttribute('src',urls[n]);
          });
          thumbs.addEventListener('scroll',()=>window.fixtureThumbMoves++);
        }''')
        self.page.wait_for_function('''() => [...document.querySelector('[data-name="Scroller"] > ul').querySelectorAll('img')].slice(3).every(img=>!img.currentSrc)''')
        first=self.page.evaluate(browser_import.SNAPSHOT)
        items,images=self.collect(first)
        initial=gallery_coverage([first['galleryState']],images,photo_items=items)
        self.assertEqual(initial['observed'],13);self.assertEqual(initial['mainLinksObserved'],3)
        self.assertEqual(initial['previewOnly'],10);self.assertEqual(initial['state'],'partial')
        sweep=self.page.evaluate(browser_import.GALLERY_SWEEP,{'budgetMs':1800,'maxMoves':40})
        result_items,result_images=self.collect(sweep)
        final=gallery_coverage(sweep['galleryStates'],result_images,photo_items=result_items)
        self.assertEqual(final['observed'],13);self.assertEqual(final['mainLinksObserved'],13)
        self.assertEqual(final['previewOnly'],0);self.assertEqual(final['state'],'complete')
        self.assertTrue(all(url.endswith('-1.jpg') for url in result_images))
        self.assertTrue(sweep['restored']);self.assertFalse(sweep['bounded'])
        self.assertGreater(sweep['moves'],0);self.assertLessEqual(sweep['moves'],40)
        self.assertEqual(self.page.evaluate('document.querySelector("[data-name=Scroller] > ul").scrollLeft'),0)
        self.assertEqual(self.page.evaluate('document.querySelector("[data-name=PaginationThumbsComponent]").scrollLeft'),0)
        self.assertEqual(self.page.evaluate('window.fixtureThumbMoves'),0)
        self.assertGreaterEqual(max(self.page.evaluate('window.fixtureMainVisits')),11)

    def test_largest_declared_srcset_precedes_lazy_preview_but_explicit_original_wins(self):
        self.page.route('https://images.cdn-cian.ru/**',lambda route:route.fulfill(content_type='image/gif',body=PIXEL))
        base='https://images.cdn-cian.ru/images/'
        self.page.set_content('<div data-name="OfferGallery"><img data-src="'+base+'preview.jpg" data-srcset="'+base+'full.jpg 1600w, '+base+'medium.jpg 400w"></div>')
        first=self.page.evaluate(browser_import.SNAPSHOT)
        self.assertEqual(first['images'],[base+'full.jpg'])
        self.assertEqual(first['galleryPhotos'][0]['url'],base+'full.jpg')
        self.assertEqual({row['url'] for row in first['galleryPhotos'][0]['observedVariants']},{base+'preview.jpg',base+'full.jpg',base+'medium.jpg'})
        self.page.locator('img').evaluate('(img,url)=>img.setAttribute("data-original",url)',base+'original.jpg')
        second=self.page.evaluate(browser_import.SNAPSHOT)
        self.assertEqual(second['images'],[base+'original.jpg'])
        self.assertEqual(second['galleryPhotos'][0]['url'],base+'original.jpg')

    def test_sweep_from_last_slide_collects_first_to_last_and_restores_original_position(self):
        self.page.set_content((FIXTURES/'cian-virtual-gallery.synthetic.html').read_text())
        self.page.evaluate('fixtureViewport.scrollLeft=fixtureViewport.scrollWidth-fixtureViewport.clientWidth')
        self.page.wait_for_function('fixtureSlide.getAttribute("aria-posinset")==="19"')
        before=self.page.evaluate('fixtureViewport.scrollLeft')
        first=self.page.evaluate(browser_import.SNAPSHOT)
        sweep=self.page.evaluate(browser_import.GALLERY_SWEEP,{'budgetMs':1800,'maxMoves':40})
        # Final import merges earlier observations too; the initial last slide
        # may not become a cover ahead of source aria-posinset order.
        items=merge_gallery_items(first['galleryPhotos'],first['galleryPhotos']+sweep['galleryPhotos'],redcn.allowed_url,'https://www.cian.ru/')
        images=observed_images(first['images']+sweep['images'],redcn.allowed_url,'https://www.cian.ru/',items)
        expected=['https://images.cdn-cian.ru/images/synthetic-virtual-'+str(index)+'.jpg' for index in range(19)]
        self.assertEqual(images,expected);self.assertEqual([row['url'] for row in items],expected)
        self.assertFalse(any('_captureOrder' in row or 'galleryIndex' in row for row in items))
        self.assertEqual(gallery_coverage(sweep['galleryStates'],images,photo_items=items)['state'],'complete')
        self.assertEqual(self.page.evaluate('fixtureViewport.scrollLeft'),before)
        self.assertGreater(sweep['moves'],0);self.assertFalse(sweep['bounded']);self.assertTrue(sweep['restored'])

    def test_early_sweep_reveals_offscreen_gallery_before_visibility_gated_lazy_callbacks(self):
        markup=(FIXTURES/'cian-virtual-gallery.synthetic.html').read_text()
        markup=markup.replace("const index=Math.min", "const box=fixtureViewport.getBoundingClientRect();if(box.top>=innerHeight||box.bottom<=0)return;const index=Math.min")
        markup=markup.replace("fixtureSlide.style.left=", "window.fixtureVisibleVisits.push(index);fixtureSlide.style.left=")
        markup=markup.replace("// Authored test mechanics", "window.fixtureVisibleVisits=[];// Authored test mechanics")
        self.page.set_content(markup)
        self.page.evaluate('document.querySelector("[data-name=OfferGallery]").style.marginTop="2400px"')
        self.assertGreater(self.page.evaluate('fixtureViewport.getBoundingClientRect().top'),700)
        first=self.page.evaluate(browser_import.SNAPSHOT)
        result,audit=browser_import.capture_sections(self.page,first,time.monotonic()+10,lambda *_args,**_kwargs:None,budget=6,section_wait=2)
        items,images=self.collect(result)
        self.assertEqual(len(images),19);self.assertEqual(audit['galleryCoverage']['missing'],0)
        self.assertEqual(audit['galleryCoverage']['state'],'complete');self.assertTrue(audit['completed'])
        self.assertGreaterEqual(max(self.page.evaluate('window.fixtureVisibleVisits')),18)
        self.assertEqual(self.page.evaluate('fixtureViewport.scrollLeft'),0);self.assertEqual(self.page.evaluate('scrollY'),0)


if __name__=='__main__':unittest.main()
