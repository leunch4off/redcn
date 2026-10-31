"""Avito provider contract in real Chrome, with isolated source documents only."""
import base64
from pathlib import Path
import sys
import time
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
import browser_import
from browser_dom import source_scripts
from source_adapters import adapter_for
import redcn

URL='https://www.avito.ru/moskva/kvartiry/flat_7462093377'
PNG=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l1sAAAAASUVORK5CYII=')

class AvitoBrowserAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:from playwright.sync_api import sync_playwright
        except ImportError:raise unittest.SkipTest('Optional Playwright/Chrome acceptance')
        cls.runtime=sync_playwright().start()
        try:cls.browser=cls.runtime.chromium.launch(channel='chrome',headless=True)
        except Exception as error:cls.runtime.stop();raise unittest.SkipTest(str(error).splitlines()[0])

    @classmethod
    def tearDownClass(cls):cls.browser.close();cls.runtime.stop()

    def setUp(self):
        self.context=self.browser.new_context(viewport={'width':1200,'height':800})
        self.context.route('https://*.img.avito.st/**',lambda route:route.fulfill(status=200,content_type='image/png',body=PNG))
        self.page=self.context.new_page()
        self.snapshot,self.probe=source_scripts(adapter_for(URL))
        self.html=(Path(__file__).parent/'fixtures/avito-52.html').read_text()
        self.context.route(URL,lambda route:route.fulfill(status=200,content_type='text/html',body=self.html))

    def tearDown(self):self.context.close()

    def read(self):
        self.page.goto(URL)
        first=self.page.evaluate(self.snapshot)
        self.assertTrue(browser_import.listing_ready(first)[0])
        current,audit=browser_import.capture_sections(self.page,first,time.monotonic()+10,lambda value,**kwargs:value,snapshot_script=self.snapshot,section_probe=self.probe)
        return current,audit

    def test_ready_sections_include_all_params_location_and_main_gallery_without_share_or_other_flat(self):
        current,audit=self.read()
        self.assertTrue(audit['completed']);self.assertEqual(audit['strategy'],'sections')
        self.assertEqual(len(current['images']),12);self.assertFalse(any('wrong-' in image for image in current['images']))
        self.assertNotIn('PRIVATE_SELLER_PHONE',current['text']);self.assertNotIn('999999',current['text'])
        parsed=redcn.parse_html(current['metadata']+current['sourceHtml'],URL)
        self.assertEqual(parsed['fields']['deposit'],'75000');self.assertEqual(parsed['fields']['commission'],'0')
        self.assertEqual(parsed['fields']['childrenPolicy'],'no');self.assertEqual(parsed['fields']['petPolicy'],'no')
        self.assertEqual(parsed['fields']['buildingYear'],'2002');self.assertEqual(parsed['fields']['walk'],'16–20')
        self.assertEqual(len(parsed['images']),12)

    def test_empty_heading_does_not_hide_lazy_rules_loaded_by_scrolling(self):
        self.page.goto(URL)
        self.page.evaluate('''() => {
          const block=[...document.querySelectorAll('[data-marker="item-view/item-params"]')].find(e=>e.querySelector('h2').textContent==='Правила');
          const content=block.querySelector('ul');content.remove();block.style.marginTop='1500px';block.style.marginBottom='1500px';
          new IntersectionObserver((entries,observer)=>{if(entries.some(e=>e.isIntersecting)){block.append(content);observer.disconnect();}}).observe(block);
        }''')
        first=self.page.evaluate(self.snapshot)
        current,audit=browser_import.capture_sections(self.page,first,time.monotonic()+10,lambda value,**kwargs:value,snapshot_script=self.snapshot,section_probe=self.probe)
        self.assertTrue(audit['completed']);self.assertIn('Можно с питомцем',current['text'])
        self.assertGreaterEqual(next(r for r in audit['sections'] if r['section']=='characteristics')['blocksVisited'],2)

if __name__=='__main__':unittest.main()
