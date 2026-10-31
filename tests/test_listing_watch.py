"""Bounded discovery inbox, source dates, auth-ready dispatch and restart state."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
import listing_watch as watch

URL=watch.DEFAULT_SEARCH_URL


def offer(identity,**extra):
    return {'id':'cian:'+str(identity),'url':'https://www.cian.ru/rent/flat/'+str(identity)+'/', 'title':'Квартира '+str(identity),'price':65000,'currency':'RUB','area':41,'metro':'Рассказовка','publishedAt':None,'publishedLabel':'','publicationDateSource':None,**extra}


class ParserChecks(unittest.TestCase):
    def test_public_search_normalizes_tracking_and_orders_newest_from_page_one(self):
        value=watch.normalize_search_url('https://www.cian.ru/cat.php?region=1&room1=1&sort=price&p=5&utm_source=private')
        self.assertIn('sort=creation_date_desc',value);self.assertIn('p=1',value)
        self.assertNotIn('utm_',value);self.assertNotIn('p=5',value)
        for value in ['https://cian.ru.evil.test/cat.php','https://avito.ru/cat.php','http://www.cian.ru/cat.php','https://user:secret@www.cian.ru/cat.php','https://www.cian.ru:8080/cat.php','https://www.cian.ru/rent/flat/123/','https://www.cian.ru/api/account']:
            with self.subTest(value=value),self.assertRaises(ValueError):watch.normalize_search_url(value)

    def test_api_maps_known_filters_and_refuses_to_broaden_unknown_filters(self):
        source=watch.normalize_search_url('https://www.cian.ru/cat.php?deal_type=rent&region=1&room1=1&room9=1&minprice=50000&maxprice=90000&for_day=0')
        q=watch.api_query(source,2)['jsonQuery']
        self.assertEqual(q['_type'],'flatrent');self.assertEqual(q['page'],{'type':'term','value':2})
        self.assertEqual(q['sort'],{'type':'term','value':'creation_date_desc'})
        self.assertEqual(q['room']['value'],[1,9]);self.assertEqual(q['price']['value'],{'gte':50000,'lte':90000})
        with self.assertRaisesRegex(ValueError,'фильтры'):watch.api_query(source+'&metro%5B0%5D=42')
        with self.assertRaises(ValueError):watch.api_query(source+'&currency=1')
        with self.assertRaises(ValueError):watch.api_query(source+'&region=bad')

    def test_official_or_search_json_dates_are_separate_from_update_times_and_unknown_labels(self):
        payload={'data':{'offersSerialized':[
            {'cianId':123,'fullUrl':'https://spb.cian.ru/rent/flat/123/?tracking=1','title':'Студия','totalArea':38,'bargainTerms':{'priceRur':70000},'creationDate':'2026-10-30T12:30:00+03:00'},
            {'cianId':124,'fullUrl':'/rent/flat/124/','updatedAt':'2026-10-31T00:00:00Z','bargainTerms':{'price':1000,'currency':'usd'}},
            {'cianId':125,'fullUrl':'https://www.cian.ru/rent/flat/125/','creationDate':'Сегодня, 12:00'},
            {'cianId':123,'fullUrl':'https://www.cian.ru/rent/flat/123/'},
        ],'suggestOffersSerializedList':[{'offersSerialized':[{'cianId':999,'fullUrl':'https://www.cian.ru/rent/flat/999/'}]}]}}
        rows=watch.parse_search(json.dumps(payload),URL,'application/json')
        self.assertEqual({r['id'] for r in rows},{'cian:123','cian:124','cian:125'})
        # Repeated source IDs remain a single item, even across host/query forms.
        row=watch.parse_search(json.dumps({'data':{'offersSerialized':[payload['data']['offersSerialized'][0]]}}),URL,'application/json')[0]
        self.assertEqual(row['url'],'https://www.cian.ru/rent/flat/123/')
        self.assertEqual(row['publishedAt'],'2026-10-30T09:30:00Z');self.assertEqual(row['currency'],'RUB')
        second=next(r for r in rows if r['id']=='cian:124');self.assertIsNone(second['publishedAt']);self.assertIsNone(second['price']);self.assertIsNone(second['currency'])
        third=next(r for r in rows if r['id']=='cian:125');self.assertIsNone(third['publishedAt']);self.assertEqual(third['publishedLabel'],'Сегодня, 12:00')

    def test_json_config_assignment_and_recognized_html_cards_exclude_recommendations(self):
        payload={'search':{'offersSerialized':[{'cianId':123,'creationDate':'2026-10-30','title':'Один'}]}}
        markup='<script>window._cianConfig["frontend-serp"] = '+json.dumps(payload)+';</script>'
        rows=watch.parse_search(markup,URL);self.assertEqual(rows[0]['id'],'cian:123');self.assertEqual(rows[0]['publishedAt'],'2026-10-30')
        markup='''<main><div data-name="CardComponent"><a href="/rent/flat/321/">Квартира 41 м²</a><time itemprop="datePublished" datetime="2026-10-30T09:00:00Z"></time></div>
        <div data-name="Recommendations"><div data-name="CardComponent"><a href="/rent/flat/999/">Чужая</a></div></div></main>'''
        rows=watch.parse_search(markup,URL);self.assertEqual([r['id'] for r in rows],['cian:321']);self.assertEqual(rows[0]['publicationDateSource'],'datePublished')

    def test_script_captcha_filename_is_not_a_challenge_but_visible_check_is(self):
        markup='<script src="captcha.js"></script><div data-name="CardComponent"><a href="/rent/flat/123/">Квартира</a></div>'
        self.assertEqual(len(watch.parse_search(markup,URL)),1)
        with self.assertRaises(watch.NeedsAttention):watch.parse_search('<h1>Подтвердите, что вы не робот</h1>',URL)
        with self.assertRaises(ValueError):watch.parse_search('not JSON',URL,'application/json')

    def test_absolute_and_epoch_dates_do_not_turn_naive_or_relative_dates_into_timestamps(self):
        self.assertEqual(watch._publication(1761868800000)[0],'2025-10-31T00:00:00Z')
        self.assertEqual(watch._publication('2026-10-31')[0],'2026-10-31')
        for value in ['2026-10-31 10:30:00','вчера',True,-1,'2026-02-30','2026-10-31T25:00:00Z']:
            with self.subTest(value=value):self.assertIsNone(watch._publication(value)[0])

    def test_private_dns_never_reaches_https_request(self):
        with patch.object(watch,'_dns_slot',None),patch.object(watch.socket,'getaddrinfo',return_value=[(None,None,None,None,('127.0.0.1',443))]),patch.object(watch.urllib.request,'build_opener') as network:
            with self.assertRaisesRegex(ValueError,'Непубличный'):watch._http_page(URL)
            network.assert_not_called()

    def test_http_adapter_reads_at_most_two_search_pages_and_never_offer_pages(self):
        calls=[]
        def network(url,data=None):
            calls.append((url,data));return json.dumps({'data':{'offersSerialized':[{'cianId':100+len(calls)}]}}).encode(),'application/json'
        config={'searchUrl':watch.normalize_search_url(URL),'adapter':'api'}
        with patch.object(watch,'_http_page',network):result=watch.fetch_search(config,Path('/tmp/unused-watch'),threading.Event())
        self.assertEqual(len(calls),2);self.assertEqual(result['pages'],2)
        self.assertTrue(all(url==watch.API_URL for url,_ in calls));self.assertEqual([data['jsonQuery']['page']['value'] for _,data in calls],[1,2])

    def test_browser_observes_only_ordinary_matching_search_page_response(self):
        raw=json.dumps({'data':{'offersSerialized':[{'cianId':555,'creationDate':'2026-10-31','bargainTerms':{'priceRur':68000}}]}}).encode()
        response=SimpleNamespace(url=watch.API_URL,status=200,request=SimpleNamespace(post_data_json=watch.api_query(watch.normalize_search_url(URL),1)),headers={'content-length':str(len(raw))},body=lambda:raw)
        rows=watch.browser_response_items(response,1,URL);self.assertEqual(rows[0]['publishedAt'],'2026-10-31');self.assertEqual(rows[0]['price'],68000)
        self.assertEqual(watch.browser_response_items(response,2,URL),[])
        response.url='https://api.cian.ru/account/messages/';self.assertEqual(watch.browser_response_items(response,1,URL),[])
        response.url=watch.API_URL;response.status=403;self.assertEqual(watch.browser_response_items(response,1,URL),[])

    def test_browser_navigation_is_guarded_before_foreign_or_local_request(self):
        calls=[]
        class Route:
            def __init__(self,url,navigation=True):self.request=SimpleNamespace(url=url,is_navigation_request=lambda:navigation,frame=SimpleNamespace(parent_frame=None))
            def continue_(self):calls.append('continued')
            def abort(self):calls.append('aborted')
        blocked=[];resolved=[]
        with patch.object(watch,'_addresses',lambda host:resolved.append(host) or ['1.1.1.1']):
            watch.guard_search_navigation(Route(watch._page_url(watch.normalize_search_url(URL),2)),URL,blocked.append)
            self.assertEqual(calls,['continued']);self.assertEqual(resolved,['www.cian.ru'])
            for url in ['http://127.0.0.1:8765/','https://evil.test/cat.php','https://www.cian.ru/api/account','https://user:password@www.cian.ru/cat.php','https://www.cian.ru/cat.php?region=2']:
                watch.guard_search_navigation(Route(url),URL,blocked.append)
            self.assertEqual(calls[1:],['aborted']*5);self.assertEqual(resolved,['www.cian.ru']);self.assertEqual(blocked,[True]*5)
            # Non-navigation resources remain normal browser resources; this
            # guard is not a fingerprint/header/CAPTCHA modification.
            watch.guard_search_navigation(Route('https://cdn.example/image.jpg',False),URL)
            self.assertEqual(calls[-1],'continued')
        with patch.object(watch,'_addresses',side_effect=ValueError('Непубличный адрес')):
            watch.guard_search_navigation(Route(URL),URL,blocked.append)
        self.assertEqual(calls[-1],'aborted')

    def test_only_explicit_successful_api_zero_count_is_a_confirmed_empty_result(self):
        good={'data':{'offersSerialized':[],'offerCount':0}}
        rows=watch.parse_search(json.dumps(good),URL,'application/json');self.assertTrue(rows.confirmed_empty)
        for payload in [{'data':{'offersSerialized':[]}}, {'data':{'offersSerialized':[],'offerCount':'0'}}, {'data':{'offersSerialized':[],'offersCount':0,'errors':['captcha_required']}}, {'data':{'offersSerialized':[],'offerCount':0},'error':'access denied'}, {'data':{'offersSerialized':[],'offerCount':0},'success':False}]:
            with self.subTest(payload=payload):self.assertFalse(watch.parse_search(json.dumps(payload),URL,'application/json').confirmed_empty)
        self.assertFalse(watch.parse_search('<script>'+json.dumps(good)+'</script>',URL).confirmed_empty,'Unknown HTML must never become a successful empty baseline')


class StorageChecks(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.clock=[1761868800.0];self.calls=[];self.batch=[offer(1),offer(2)]
        def fetch(config,cancel):self.calls.append(config);return {'items':list(self.batch),'pages':2}
        self.manager=watch.ListingWatch(self.root,fetch=fetch,now=lambda:self.clock[0],start=False)

    def tearDown(self):self.manager.close();self.temp.cleanup()

    def configured(self):return self.manager.configure({'enabled':True,'searchUrl':URL})

    def test_disabled_default_never_requests_network_or_creates_results_database(self):
        self.assertEqual(self.manager.tick()['state'],'disabled');self.assertEqual(self.calls,[])
        self.assertFalse((self.root/'listings.sqlite3').exists())
        self.assertEqual((self.root/'listing-watch.sqlite3').stat().st_mode & 0o777,0o600)

    def test_baseline_then_new_links_are_deduplicated_and_publication_is_not_first_seen(self):
        self.configured();a=self.manager.tick();self.assertTrue(a['baselineComplete']);self.assertEqual(a['newCount'],0)
        self.clock[0]+=3601;self.batch=[offer(2),offer(3),offer(3)]
        a=self.manager.tick();self.assertEqual(a['unreadCount'],1);self.assertEqual(a['totalSeen'],3)
        row=self.manager.list_new()['items'][0];self.assertEqual(row['id'],'cian:3');self.assertIsNone(row['publishedAt']);self.assertEqual(row['firstSeen'],self.clock[0])
        self.manager.tick();self.assertEqual(len(self.calls),2,'Hourly checks must not turn into a polling loop')

    def test_known_old_listing_returning_to_first_pages_is_not_a_new_publication(self):
        self.configured();self.manager.tick();self.clock[0]+=3601
        self.batch=[offer(8,publishedAt='2025-01-01T00:00:00Z'),offer(9,publishedAt='2025-10-31T01:00:00Z')]
        self.manager.tick();self.assertEqual([r['id'] for r in self.manager.list_new()['items']],['cian:9'])
        self.assertIn('cian:8',[r['id'] for r in self.manager.list_items('all')['items']])

    def test_read_state_restart_and_source_specific_baselines_persist(self):
        self.configured();self.manager.tick();self.clock[0]+=3601;self.batch=[offer(3)];self.manager.tick()
        self.manager.mark_read(['cian:3']);self.assertEqual(self.manager.status()['unreadCount'],0)
        restarted=watch.ListingWatch(self.root,fetch=self.manager.fetch,now=lambda:self.clock[0],start=False)
        try:
            self.assertTrue(restarted.status()['baselineComplete']);self.assertTrue(restarted.list_new()['items'][0]['read'])
            restarted.configure({'searchUrl':URL+'&room1=1'});self.assertFalse(restarted.status()['baselineComplete'])
            restarted.tick();self.assertEqual(restarted.status()['newCount'],0)
        finally:restarted.close()

    def test_error_backoff_is_persistent_and_never_manufactures_a_baseline(self):
        self.configured()
        def fail(*_):raise watch.NeedsAttention('HTTP 403: нужна ручная проверка')
        self.manager.fetch=fail;a=self.manager.tick();self.assertEqual(a['state'],'needs_attention');self.assertFalse(a['baselineComplete']);self.assertEqual(a['runCount'],1)
        self.assertEqual(a['nextRun'],self.clock[0]+7200)
        restarted=watch.ListingWatch(self.root,fetch=fail,now=lambda:self.clock[0],start=False)
        try:self.assertEqual(restarted.status()['consecutiveFailures'],1);self.assertEqual(restarted.status()['nextRun'],a['nextRun'])
        finally:restarted.close()

    def test_unrecognized_empty_page_is_attention_not_successful_empty_monitor(self):
        self.configured();self.manager.fetch=lambda *_:{'items':[]};a=self.manager.tick();self.assertFalse(a['baselineComplete']);self.assertEqual(a['state'],'needs_attention')

    def test_confirmed_empty_api_baseline_makes_the_first_later_offer_new(self):
        self.configured();self.manager.fetch=lambda *_:{'items':[],'confirmedEmpty':True}
        a=self.manager.tick();self.assertTrue(a['baselineComplete']);self.assertEqual(a['totalSeen'],0);self.assertEqual(a['state'],'idle')
        self.clock[0]+=3601;self.manager.fetch=lambda *_:{'items':[offer(5)]};a=self.manager.tick()
        self.assertEqual(a['unreadCount'],1);self.assertEqual(self.manager.list_new()['items'][0]['id'],'cian:5')

    def test_only_explicit_reads_change_seen_state_and_legacy_results_are_untouched(self):
        legacy=self.root/'listings.sqlite3';legacy.write_bytes(b'USER-DATA-UNTOUCHED');before=hashlib.sha256(legacy.read_bytes()).hexdigest()
        self.configured();self.manager.tick();self.clock[0]+=3601;self.batch=[offer(3)];self.manager.tick()
        self.manager.list_new();self.assertEqual(self.manager.status()['unreadCount'],1)
        with self.assertRaises(ValueError):self.manager.mark_read(['cian:3 OR 1=1'])
        self.manager.mark_read(['cian:3']);self.assertEqual(hashlib.sha256(legacy.read_bytes()).hexdigest(),before)

    def test_cursor_paginates_same_timestamp_without_loss_or_duplicate(self):
        self.configured();self.manager.tick();self.clock[0]+=3601;self.batch=[offer(i) for i in range(10,25)];self.manager.tick()
        ids=[];cursor=None
        while True:
            page=self.manager.list_new(limit=4,before=cursor);ids.extend(r['id'] for r in page['items'])
            if not page['hasMore']:break
            cursor=page['nextBefore']
        self.assertEqual(len(ids),15);self.assertEqual(len(set(ids)),15)
        for cursor in ['nan:cian:2','0:evil','10:cian:2 OR 1=1']:
            with self.assertRaises(ValueError):self.manager.list_new(before=cursor)

    def test_retention_cap_counts_new_rows_only_and_never_deletes_known_ids(self):
        self.configured();self.manager.tick()
        with patch.object(watch,'MAX_SEEN',3):
            self.clock[0]+=3601;state=self.manager.tick();self.assertEqual(state['consecutiveFailures'],0);self.assertEqual(state['totalSeen'],2)
            self.clock[0]+=3601;self.batch=[offer(2),offer(3)];self.assertEqual(self.manager.tick()['totalSeen'],3)
            self.clock[0]+=3601;self.batch=[offer(4)];state=self.manager.tick();self.assertEqual(state['totalSeen'],3);self.assertIn('лимита',state['error'])
            self.assertEqual({r['id'] for r in self.manager.list_items('all')['items']},{'cian:1','cian:2','cian:3'})

    def test_config_validation_rejects_frequency_below_an_hour_and_unknown_options(self):
        for values in [{'enabled':True},{'searchUrl':URL,'intervalMinutes':1},{'intervalMinutes':True},{'adapter':'stealth'},{'cookies':'private'}]:
            with self.subTest(values=values),self.assertRaises(ValueError):self.manager.configure(values)

    def test_background_worker_resumes_a_persisted_due_source_and_stops(self):
        self.configured();self.manager.start();end=time.monotonic()+2
        while time.monotonic()<end and not self.manager.status()['baselineComplete']:time.sleep(.01)
        self.assertTrue(self.manager.status()['baselineComplete']);self.manager.close();self.assertFalse(self.manager._thread.is_alive())

    def test_single_inflight_request_prevents_parallel_chrome_and_late_close_writes(self):
        self.configured();entered=threading.Event();release=threading.Event()
        def slow(*_):entered.set();release.wait(2);return {'items':[offer(9)]}
        self.manager.fetch=slow;t=threading.Thread(target=self.manager.tick);t.start();self.assertTrue(entered.wait(1))
        self.assertEqual(self.manager.tick(force=True)['state'],'checking');self.manager.close();release.set();t.join(2)
        self.assertFalse(self.manager.status()['baselineComplete']);self.assertEqual(self.manager.status()['totalSeen'],0)

    def test_dispatch_preserves_query_and_only_returns_own_routes(self):
        self.assertIsNone(self.manager.dispatch('other-route'))
        status,value,mime,headers=self.manager.dispatch('listing-watch',{'enabled':False,'searchUrl':URL})
        self.assertEqual(status,200);self.assertEqual(value['config']['intervalMinutes'],60)
        with self.assertRaises(ValueError):self.manager.dispatch('listing-watch/items',query={'category':['private']})
        self.assertEqual(self.manager.dispatch('listing-watch/items',query={'category':['all'],'limit':['2']})[1]['items'],[])

    def test_interrupted_attempt_resumes_after_restart_without_resetting_error_backoff(self):
        self.configured();entered=threading.Event()
        def waiting(config,cancel):entered.set();cancel.wait(2);return {'items':[offer(9)]}
        self.manager.fetch=waiting;self.manager.start();self.assertTrue(entered.wait(1));self.manager.close()
        resumed=watch.ListingWatch(self.root,fetch=lambda *_:{'items':[offer(10)]},now=lambda:self.clock[0],start=False)
        try:
            self.assertEqual(resumed.status()['nextRun'],self.clock[0]);self.assertTrue(resumed.tick()['baselineComplete'])
        finally:resumed.close()


class OwnedProcessChecks(unittest.TestCase):
    def test_hard_deadline_stops_only_owned_worker_process(self):
        processes=[];real=subprocess.Popen
        def spawn(*args,**kwargs):
            p=real(*args,**kwargs);processes.append(p);return p
        with tempfile.TemporaryDirectory() as folder,patch.object(watch,'_source_command',return_value=[sys.executable,'-c','import time;time.sleep(120)']),patch.object(watch.subprocess,'Popen',spawn):
            with self.assertRaises(TimeoutError):watch.fetch_search_bounded({'adapter':'html','searchUrl':URL},Path(folder),threading.Event(),timeout=.15)
        self.assertEqual(len(processes),1);self.assertIsNotNone(processes[0].poll())

    def test_manual_import_browser_lock_is_released_after_watch_timeout(self):
        from browser_import import _browser_lock
        with tempfile.TemporaryDirectory() as folder,patch.object(watch,'_source_command',return_value=[sys.executable,'-c','import time;time.sleep(120)']):
            with self.assertRaises(TimeoutError):watch.fetch_search_bounded({'adapter':'chrome','searchUrl':URL},Path(folder),threading.Event(),timeout=.1)
        acquired=_browser_lock.acquire(False)
        try:self.assertTrue(acquired,'A timed out monitor must not block the next manual scan')
        finally:
            if acquired:_browser_lock.release()

    def test_close_waits_for_cancelled_default_worker_and_no_late_baseline(self):
        entered=threading.Event();processes=[];real=subprocess.Popen
        def spawn(*args,**kwargs):
            p=real(*args,**kwargs);processes.append(p);entered.set();return p
        with tempfile.TemporaryDirectory() as folder,patch.object(watch,'_source_command',return_value=[sys.executable,'-c','import time;time.sleep(120)']),patch.object(watch.subprocess,'Popen',spawn):
            manager=watch.ListingWatch(folder,start=False)
            try:
                manager.configure({'searchUrl':URL,'enabled':True});manager.start();self.assertTrue(entered.wait(1));manager.close()
                self.assertFalse(manager._thread.is_alive());self.assertIsNotNone(processes[0].poll());self.assertFalse(manager.status()['baselineComplete'])
            finally:manager.close()

    @unittest.skipUnless(watch.os.name=='posix','Owned process groups are the Mac/Linux mechanism')
    def test_cancellation_reaches_owned_descendant_not_just_python_owner(self):
        with tempfile.TemporaryDirectory() as folder:
            ready=Path(folder)/'ready';stopped=Path(folder)/'stopped'
            child="import signal,time;from pathlib import Path;signal.signal(signal.SIGTERM,lambda *a:(Path("+repr(str(stopped))+").write_text('terminated'),exit(0)));Path("+repr(str(ready))+").write_text('ready');time.sleep(120)"
            owner='import subprocess,time,sys;subprocess.Popen([sys.executable,"-c",'+repr(child)+']);time.sleep(120)'
            cancel=threading.Event();outcome=[]
            def run():
                try:watch.fetch_search_bounded({'adapter':'html','searchUrl':URL},Path(folder),cancel)
                except ValueError:outcome.append('cancelled')
            with patch.object(watch,'_source_command',return_value=[sys.executable,'-c',owner]):
                worker=threading.Thread(target=run);worker.start();end=time.monotonic()+2
                while not ready.exists() and time.monotonic()<end:time.sleep(.01)
                self.assertTrue(ready.exists());cancel.set();worker.join(4)
                self.assertFalse(worker.is_alive());self.assertTrue(stopped.exists());self.assertEqual(outcome,['cancelled'])


if __name__=='__main__':unittest.main()
