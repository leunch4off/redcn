"""Owned Chrome acceptance of the lightweight monitor widget; no Cian requests."""
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import unittest


ROOT=Path(__file__).resolve().parents[1]
HARNESS='''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/web/listing-watch.css"><style>*{box-sizing:border-box}body{margin:0;font:16px system-ui;background:#f7f8f6;color:#243b2a}#mount{max-width:1200px;padding:10px;margin:auto}.btn{font:inherit;padding:10px;border:1px solid #cad5cd;border-radius:8px;background:white;color:#284b34}.btn.primary{background:#2e5a40;color:white}.muted{color:#617166}.eyebrow{font-size:11px;letter-spacing:.1em}</style><div id="mount"></div><script type="module">
import {setupListingWatch} from '/web/listing-watch.mjs';
window.calls=[];window.scanUrls=[];window.delayNextStatus=false;window.releaseStatus=null;
window.state={config:{enabled:true,searchUrl:'https://www.cian.ru/cat.php?deal_type=rent&region=1',intervalMinutes:60,adapter:'html'},state:'idle',baselineComplete:true,lastRun:1793424600,lastSuccess:1793424600,nextRun:1793428200,coverage:'first-2-pages',error:''};
window.rows=[{id:'cian:100',url:'https://www.cian.ru/rent/flat/100/',title:'Найденная квартира',price:68000,area:41,metro:'Рассказовка',publishedAt:'2026-10-31T05:30:00Z',firstSeen:1793424900,read:false,isNew:true},{id:'cian:101',url:'javascript:alert(1)',title:'<img id="injected" src="bad">',publishedAt:null,publishedLabel:'',firstSeen:1793425000,read:false,isNew:true},{id:'cian:102',url:'https://www.cian.ru/rent/flat/102/',title:'Из начального снимка',publishedAt:null,firstSeen:1793424500,read:true,isNew:false}];
const copy=value=>JSON.parse(JSON.stringify(value));
async function api(path,data){
 window.calls.push({path,data:copy(data??null)});
 if(path==='listing-watch'&&data){window.state.config=copy(data);return copy({...window.state,unreadCount:window.rows.filter(row=>!row.read).length});}
 if(path==='listing-watch'){
  const captured=copy({...window.state,unreadCount:window.rows.filter(row=>!row.read).length});
  if(window.delayNextStatus){window.delayNextStatus=false;await new Promise(resolve=>window.releaseStatus=resolve);}
  return captured;
 }
 if(path.startsWith('listing-watch/items?')){const params=new URL('/'+path,location.href).searchParams;return {items:copy(window.rows.filter(row=>params.get('category')==='all'||!row.read)),hasMore:false,nextBefore:null};}
 if(path==='listing-watch/read'){for(const row of window.rows)if(data.ids.includes(row.id))row.read=true;return copy(window.state);}
 if(path==='listing-watch/check')return copy({...window.state,state:'checking'});
 throw Error('Unexpected widget request');
}
window.widget=setupListingWatch({mount:document.getElementById('mount'),api,onScan:async url=>window.scanUrls.push(url)});await window.widget.refresh();window.ready=true;
</script></html>'''


class ListingWatchBrowserAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise unittest.SkipTest('Optional monitor acceptance needs Playwright and Chrome')
        cls.runtime=sync_playwright().start()
        try:
            cls.browser=cls.runtime.chromium.launch(channel='chrome',headless=True)
        except Exception as error:
            cls.runtime.stop();raise unittest.SkipTest(str(error).splitlines()[0])
        class Handler(SimpleHTTPRequestHandler):
            def __init__(self,*args,**kwargs):super().__init__(*args,directory=str(ROOT),**kwargs)
            def log_message(self,*args):pass
            def do_GET(self):
                if self.path=='/':
                    content=HARNESS.encode();self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(content)));self.end_headers();self.wfile.write(content)
                else:super().do_GET()
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start();cls.url='http://127.0.0.1:'+str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close();cls.runtime.stop();cls.server.shutdown();cls.server.server_close();cls.thread.join()

    def setUp(self):
        self.context=self.browser.new_context(viewport={'width':393,'height':852},has_touch=True)
        self.page=self.context.new_page();self.errors=[];self.page.on('pageerror',lambda error:self.errors.append(str(error)))
        self.page.goto(self.url,wait_until='domcontentloaded');self.page.wait_for_function('window.ready===true')

    def tearDown(self):
        self.context.close();self.assertEqual(self.errors,[])

    def test_mobile_dates_safe_links_and_manual_scan_only(self):
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'),393)
        self.assertEqual(self.page.locator('.listing-watch-item').count(),2)
        first=self.page.locator('[data-watch-id="cian:100"]');second=self.page.locator('[data-watch-id="cian:101"]')
        self.assertIn('Размещено в ЦИАН: 31.10.2026',first.inner_text())
        self.assertIn('Дата размещения ЦИАН не указана',second.inner_text())
        self.assertIn('Впервые найдено нами:',second.inner_text());self.assertEqual(self.page.locator('#injected').count(),0)
        self.assertEqual(self.page.evaluate('window.scanUrls'),[])
        self.assertTrue(second.get_by_role('button',name='Сканировать объявление',exact=True).is_disabled())
        first.get_by_role('button',name='Сканировать объявление',exact=True).click()
        self.page.wait_for_function('window.scanUrls.length===1')
        self.assertEqual(self.page.evaluate('window.scanUrls'),['https://www.cian.ru/rent/flat/100/'])
        self.assertFalse(self.page.evaluate("window.calls.some(call=>call.path==='listing-watch/read')"))
        first.get_by_role('button',name='Прочитано',exact=True).click()
        self.page.wait_for_function("!document.querySelector('[data-watch-id=\"cian:100\"]')")
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'),393)

    def test_poll_does_not_overwrite_unsaved_config_and_save_keeps_hour_interval(self):
        self.page.locator('.listing-watch-settings > summary').click()
        self.page.evaluate('window.delayNextStatus=true;window.delayedRefresh=window.widget.refresh();void 0')
        self.page.wait_for_function("typeof window.releaseStatus==='function'")
        target='https://www.cian.ru/cat.php?deal_type=rent&region=1&minprice=50000'
        self.page.get_by_label('Ссылка на поиск ЦИАН',exact=True).fill(target)
        self.page.evaluate('window.releaseStatus();void 0')
        self.page.wait_for_function("document.querySelector('.listing-watch-status').textContent.includes('ожидает')")
        self.assertEqual(self.page.get_by_label('Ссылка на поиск ЦИАН',exact=True).input_value(),target)
        self.page.get_by_role('button',name='Сохранить настройки',exact=True).click()
        self.page.wait_for_function("window.calls.some(call=>call.path==='listing-watch'&&call.data)")
        saved=self.page.evaluate("window.calls.findLast(call=>call.path==='listing-watch'&&call.data).data")
        self.assertEqual(saved,{'enabled':True,'searchUrl':target,'intervalMinutes':60,'adapter':'html'})

    def test_late_old_category_cannot_replace_new_category_and_destroy_is_final(self):
        self.page.evaluate('window.delayNextStatus=true;window.pendingRefresh=window.widget.refresh();void 0')
        self.page.wait_for_function("typeof window.releaseStatus==='function'")
        self.page.get_by_label('Категория найденных объявлений',exact=True).select_option('all')
        self.page.locator('[data-watch-id="cian:102"]').wait_for()
        self.page.evaluate('window.releaseStatus();void 0')
        self.page.wait_for_function("document.querySelectorAll('.listing-watch-item').length===3")
        self.page.evaluate('window.delayNextStatus=true;window.pendingRefresh=window.widget.refresh();void 0')
        self.page.wait_for_function("typeof window.releaseStatus==='function'")
        self.page.evaluate('window.widget.destroy();window.releaseStatus();void 0')
        self.page.evaluate('async()=>await window.pendingRefresh')
        self.assertEqual(self.page.locator('#mount').inner_html(),'')


if __name__=='__main__':unittest.main()
