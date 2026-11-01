"""Synthetic saved-search contracts; no live source, browser profile or import."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
import listing_watch as watch
import activity_log
from search_adapters import avito, cian

AVITO='https://www.avito.ru/moskva/kvartiry?f=ASgBAgICAkSSA8gQ8AeQUg&priceMax=95000'


def card(identity=7462093377,*,date='Сегодня, 12:00',price='85 000 ₽ в месяц',district='район Ясенево',extra=''):
    return f'''<div data-marker="item"><a href="/moskva/kvartiry/2-k._kvartira_52_m_1417_et._{identity}?context=discard"><h3 data-marker="item-title">2-к. квартира, 52 м², 14/17 эт.</h3></a>
    <p data-marker="item-price">{price}</p><p data-marker="item-address">Москва, тестовая улица</p><span data-marker="item-address/district">{district}</span><time data-marker="item-date">{date}</time>{extra}</div>'''


def candidate(identity=1,**extra):
    return {'id':f'avito:{identity}','url':f'https://www.avito.ru/moskva/kvartiry/test_{identity}','platform':'avito','title':'2-к. квартира','price':85000,'currency':'RUB','pricePeriod':'month','area':52,'rooms':2,'district':'Ясенево','publicationStatus':'active',**extra}


class SeparateAdapterContracts(unittest.TestCase):
    def test_avito_search_preserves_semantic_filters_but_starts_newest_from_one(self):
        value=watch.normalize_search_url(AVITO+'&s=1&p=6&utm_source=private#x')
        query=parse_qs(urlsplit(value).query)
        self.assertEqual(query['f'],['ASgBAgICAkSSA8gQ8AeQUg']);self.assertEqual(query['priceMax'],['95000'])
        self.assertEqual(query['p'],['1']);self.assertEqual(query['s'],['104']);self.assertNotIn('utm_source',query)
        for value in ['https://www.avito.ru/profile','https://www.avito.ru/moskva/kvartiry/test_1','https://avito.ru.evil.test/moskva/kvartiry','https://u:p@avito.ru/moskva/kvartiry','https://avito.ru:8443/moskva/kvartiry','http://avito.ru/moskva/kvartiry']:
            with self.subTest(value=value),self.assertRaises(ValueError):watch.normalize_search_url(value)

    def test_avito_own_html_cards_extract_only_observed_fields_and_relative_date_stays_label(self):
        rows=watch.parse_search(card(),AVITO)
        self.assertEqual(len(rows),1);row=rows[0]
        self.assertEqual(row['id'],'avito:7462093377');self.assertNotIn('context',row['url'])
        self.assertEqual((row['price'],row['currency'],row['pricePeriod'],row['area'],row['rooms']),(85000,'RUB','month',52,2))
        self.assertEqual(row['district'],'район Ясенево');self.assertIsNone(row['publishedAt']);self.assertEqual(row['publishedLabel'],'Сегодня, 12:00')
        self.assertNotIn('photos',row)

    def test_avito_absolute_date_meta_and_recommendations_are_separate(self):
        markup=card(date='<meta itemprop="datePosted" content="2026-10-30T12:00:00+03:00">')
        markup+='<div data-marker="recommendations">'+card(123)+'</div>'
        markup+='<script>captcha="not visible";</script><style>captcha</style>'
        rows=watch.parse_search(markup,AVITO)
        self.assertEqual([r['id'] for r in rows],['avito:7462093377']);self.assertEqual(rows[0]['publishedAt'],'2026-10-30T09:00:00Z')
        self.assertFalse(rows.confirmed_empty)

    def test_avito_unknown_currency_period_and_archived_are_not_inferred(self):
        row=watch.parse_search(card(price='1000 USD',extra='<p>Объявление снято</p>'),AVITO)[0]
        self.assertIsNone(row['price']);self.assertIsNone(row['currency']);self.assertEqual(row['pricePeriod'],'unknown');self.assertEqual(row['publicationStatus'],'removed')
        daily=watch.parse_search(card(price='5000 ₽ за сутки'),AVITO)[0];self.assertEqual(daily['pricePeriod'],'day')

    def test_unknown_and_challenge_never_become_empty_baselines(self):
        self.assertFalse(watch.parse_search('<main>Пустая страница</main>',AVITO).confirmed_empty)
        self.assertTrue(watch.parse_search('<main data-marker="search/no-results">Ничего не найдено</main>',AVITO).confirmed_empty)
        with self.assertRaises(watch.NeedsAttention):watch.parse_search('<h1>Доступ ограничен</h1>',AVITO)
        with self.assertRaises(ValueError):watch.parse_search('{}',AVITO,'application/json')

    def test_provider_modules_do_not_import_the_scheduler_or_each_other(self):
        root=Path(__file__).resolve().parents[1]/'cli/search_adapters'
        self.assertNotIn('import listing_watch',(root/'cian.py').read_text())
        self.assertNotIn('from listing_watch',(root/'avito.py').read_text())
        self.assertEqual(cian.parse_search('<div data-name="CardComponent"><a href="/rent/flat/2/">2-к. 52 м² 85\u00a0000 ₽/мес</a></div>',watch.DEFAULT_SEARCH_URL)[0]['price'],85000)

    def test_avito_browser_navigation_cannot_change_filters_or_host(self):
        source=watch.normalize_search_url(AVITO)
        self.assertIn('p=4',watch.allowed_search_navigation(watch._page_url(source,4),source,resolve=lambda _:['1.1.1.1']))
        for value in ['https://www.avito.ru/moskva/kvartiry?priceMax=999999','https://www.avito.ru/profile','https://example.test/moskva/kvartiry']:
            with self.subTest(value=value),self.assertRaises(ValueError):watch.allowed_search_navigation(value,source,resolve=lambda _:['1.1.1.1'])


class FilterContracts(unittest.TestCase):
    filters={'minRent':50000,'maxRent':90000,'minArea':40,'maxArea':60,'rooms':[2],'districts':['Ясенево']}
    def test_accept_reject_and_pending_reasons_are_distinct(self):
        self.assertEqual(watch.candidate_decision(candidate(),self.filters)['state'],'accepted')
        rejected=watch.candidate_decision(candidate(price=95000),self.filters);self.assertEqual(rejected['state'],'rejected');self.assertTrue(any('выше' in reason for reason in rejected['reasons']))
        pending=watch.candidate_decision(candidate(district=''),self.filters);self.assertEqual(pending['state'],'pending');self.assertIn('Район источником не подтверждён',pending['reasons'])

    def test_unknown_fields_do_not_satisfy_explicit_filters(self):
        for values in [{'pricePeriod':'unknown'},{'currency':'USD'},{'price':None},{'rooms':None},{'area':None},{'district':''}]:
            with self.subTest(values=values):self.assertEqual(watch.candidate_decision(candidate(**values),self.filters)['state'],'pending')
        self.assertEqual(watch.candidate_decision(candidate(pricePeriod='day'),self.filters)['state'],'pending')

    def test_inactive_rejected_even_without_filters_and_no_geography_guess(self):
        self.assertEqual(watch.candidate_decision(candidate(publicationStatus='removed'))['state'],'rejected')
        self.assertEqual(watch.candidate_decision(candidate(district='',address='Москва, Ясенево'),self.filters)['state'],'pending')
        self.assertEqual(watch.candidate_decision(candidate(district='район Ясенево'),self.filters)['state'],'accepted')

    def test_filter_validation_does_not_accept_script_options_or_ambiguous_ranges(self):
        for values in [{'minRent':True},{'maxArea':float('nan')},{'rooms':[True]},{'rooms':['2']},{'districts':['']},{'minRent':90000,'maxRent':50000},{'unsafe':'x'}]:
            with self.subTest(values=values),self.assertRaises(ValueError):watch.normalized_filters(values)


class ExpandedCoverageContracts(unittest.TestCase):
    def network(self,calls,*,date='2026-10-30T09:00:00Z',repeat=False):
        def fetch(url,data=None,**kwargs):
            calls.append((url,data,kwargs));identity=1 if repeat else len(calls)
            return json.dumps({'data':{'offersSerialized':[{'cianId':identity,'creationDate':date}]}}).encode(),'application/json'
        return fetch

    def test_explicit_four_pages_preserve_query_and_report_actual_partial_coverage(self):
        calls=[];config={'searchUrl':watch.normalize_search_url(watch.DEFAULT_SEARCH_URL),'adapter':'html','maxPages':4}
        with patch.object(watch,'_http_page',self.network(calls)):result=watch.fetch_search(config,Path('/unused'),threading.Event())
        self.assertEqual(len(calls),4);self.assertEqual(len(result['items']),4)
        self.assertEqual([parse_qs(urlsplit(call[0]).query)['p'][0] for call in calls],['1','2','3','4'])
        coverage=result['coverageInfo'];self.assertEqual(coverage['visitedPages'],4);self.assertEqual(coverage['stopReason'],'page-limit');self.assertTrue(coverage['partial']);self.assertIsNone(coverage['gapCount'])

    def test_two_known_absolute_date_pages_stop_at_control_boundary(self):
        calls=[];cutoff=datetime(2026,10,31,tzinfo=timezone.utc).timestamp()
        config={'searchUrl':watch.DEFAULT_SEARCH_URL,'adapter':'html','maxPages':8,'knownIds':['cian:1'],'stopBeforePublished':cutoff}
        with patch.object(watch,'_http_page',self.network(calls,repeat=True)):result=watch.fetch_search(config,Path('/unused'),threading.Event())
        self.assertEqual(len(calls),2);self.assertEqual(result['coverageInfo']['stopReason'],'known-date-boundary');self.assertEqual(result['coverageInfo']['duplicateCount'],1)
        self.assertEqual(len(result['items']),1)

    def test_relative_dates_never_trigger_premature_publication_boundary(self):
        calls=[];config={'searchUrl':watch.DEFAULT_SEARCH_URL,'adapter':'html','maxPages':4,'knownIds':['cian:1'],'stopBeforePublished':1793404800}
        with patch.object(watch,'_http_page',self.network(calls,date='Сегодня',repeat=True)):result=watch.fetch_search(config,Path('/unused'),threading.Event())
        self.assertEqual(len(calls),4);self.assertEqual(result['coverageInfo']['unknownPublicationDates'],1)

    def test_time_budget_returns_measured_partial_batch_instead_of_opening_more_pages(self):
        calls=[];clock=[0.0];network=self.network(calls)
        def slow(*args,**kwargs):
            value=network(*args,**kwargs);clock[0]+=10;return value
        with patch.object(watch.time,'monotonic',lambda:clock[0]),patch.object(watch,'_http_page',slow):result=watch.fetch_search({'searchUrl':watch.DEFAULT_SEARCH_URL,'adapter':'html','maxPages':8},Path('/unused'),threading.Event())
        self.assertEqual(len(calls),3);self.assertEqual(result['pages'],3);self.assertEqual(result['coverageInfo']['stopReason'],'time-budget')
        self.assertEqual([entry[2]['budget'] for entry in calls],[30,20,10])

    def test_avito_uses_only_saved_search_pages_and_rejects_private_api_mode(self):
        calls=[]
        def network(url,data=None,**kwargs):calls.append(url);return card(len(calls)).encode(),'text/html'
        config={'searchUrl':watch.normalize_search_url(AVITO),'adapter':'html','maxPages':3}
        with patch.object(watch,'_http_page',network):result=watch.fetch_search(config,Path('/unused'),threading.Event())
        self.assertEqual(len(calls),3);self.assertEqual(result['platform'],'avito');self.assertTrue(all(urlsplit(url).path=='/moskva/kvartiry' for url in calls))
        with self.assertRaises(ValueError):watch.fetch_search({**config,'adapter':'api'},Path('/unused'),threading.Event())


class PersistentOperatorWorkflow(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.clock=[1793404800.0];self.batch=[candidate(1)]
        self.manager=watch.ListingWatch(self.root,fetch=lambda *_:{'items':list(self.batch),'pages':1,'coverageInfo':{'stopReason':'page-limit','pageItems':[len(self.batch)],'duplicateCount':0}},now=lambda:self.clock[0],start=False)
    def tearDown(self):self.manager.close();self.temp.cleanup()
    def configure(self,**extra):return self.manager.configure({'enabled':True,'searchUrl':AVITO,**extra})

    def test_feed_triage_with_first_seen_dates_and_only_opt_in_internal_notifications(self):
        self.configure(filters={'maxRent':90000,'districts':['Ясенево']});self.manager.tick()
        self.clock[0]+=3601;self.batch=[candidate(2),candidate(3,price=100000),candidate(4,district='')];self.manager.tick()
        self.assertEqual([r['id'] for r in self.manager.list_new()['items']],['avito:2']);self.assertEqual(self.manager.notifications()['items'],[])
        self.assertEqual(self.manager.list_items('rejected')['items'][0]['id'],'avito:3');self.assertEqual(self.manager.list_items('pending')['items'][0]['id'],'avito:4')
        row=self.manager.list_new()['items'][0];self.assertEqual(row['firstSeen'],self.clock[0]);self.assertIsNone(row['publishedAt']);self.assertTrue(row['decisionReasons'])
        self.manager.configure({'notifications':{'enabled':True,'channel':'in-app'}})
        self.clock[0]+=3601;self.batch=[candidate(2),candidate(5)];self.manager.tick()
        self.assertEqual([r['id'] for r in self.manager.notifications()['items']],['avito:5'])
        self.clock[0]+=3601;self.manager.tick();self.assertEqual(len(self.manager.notifications()['items']),1)
        self.manager.mark_read(['avito:5']);self.assertTrue(self.manager.notifications()['items'][0]['read'])

    def test_notification_disable_restart_and_source_isolation(self):
        self.configure(notifications={'enabled':True,'channel':'in-app'});self.manager.tick();self.clock[0]+=3601;self.batch=[candidate(2)];self.manager.tick()
        self.manager.configure({'notifications':{'enabled':False,'channel':'in-app'}});self.clock[0]+=3601;self.batch=[candidate(3)];self.manager.tick()
        self.assertEqual(len(self.manager.notifications()['items']),1)
        restarted=watch.ListingWatch(self.root,start=False)
        try:self.assertEqual(len(restarted.notifications()['items']),1);self.assertFalse(restarted.status()['config']['notifications']['enabled'])
        finally:restarted.close()
        self.manager.configure({'searchUrl':AVITO+'&q=other'});self.assertEqual(self.manager.notifications()['items'],[])

    def test_no_external_channel_tokens_or_implicit_api_fallback(self):
        for config in [{'notifications':{'enabled':True,'channel':'telegram','token':'SECRET'}},{'notifications':{'enabled':1,'channel':'in-app'}},{'adapter':'api','searchUrl':AVITO},{'maxPages':9},{'maxPages':True}]:
            with self.subTest(config=config),self.assertRaises(ValueError):self.manager.configure(config)
        self.assertFalse(self.manager.status()['config']['enabled'])

    def test_filters_can_be_changed_without_losing_discovery_or_read_state(self):
        self.configure(filters={'maxRent':80000});self.manager.tick();row=self.manager.list_items('rejected')['items'][0]
        self.manager.mark_read([row['id']]);self.manager.configure({'filters':{'maxRent':90000}})
        changed=self.manager.list_items('accepted')['items'][0];self.assertEqual(changed['firstSeen'],row['firstSeen']);self.assertTrue(changed['read']);self.assertFalse(changed['isNew'])

    def test_safe_persistent_receipts_and_auth_dispatch_do_not_store_raw_exceptions(self):
        self.configure();self.manager.tick();self.clock[0]+=3601
        self.manager.fetch=lambda *_:(_ for _ in ()).throw(TimeoutError('TOKEN_COOKIE Authorization: secret <html>private</html>'))
        result=self.manager.tick();self.assertEqual(result['nextRun'],self.clock[0]+7200);self.assertNotIn('secret',result['error'])
        rows=self.manager.dispatch('activity-log',query={'kind':['watch']})[1]['items'];self.assertEqual(len(rows),2);self.assertEqual(rows[0]['error_code'],'timeout');self.assertTrue(rows[0]['stages']['fetch']>=0)
        self.assertNotIn(b'TOKEN_COOKIE',(self.root/'activity-log.sqlite3').read_bytes());self.assertNotIn(b'Authorization',(self.root/'activity-log.sqlite3').read_bytes())
        self.assertEqual(self.manager.dispatch('listing-watch/notifications')[1]['channel'],'in-app')


class ActivityPrivacyContracts(unittest.TestCase):
    def test_manual_recorder_whitelist_cursor_and_private_fields(self):
        with tempfile.TemporaryDirectory() as root:
            activity_log.record_import_attempt(root,source_url='https://www.cian.ru/rent/flat/1/?token=PRIVATE',elapsed=1.2,stages={'scroll':.3,'photos':.5,'Cookie':'SECRET','html':'<html>PRIVATE</html>'},outcome='error',error=ValueError('PRIVATE auth cookie'))
            activity_log.record_import_attempt(root,source_url='https://www.avito.ru/moskva/kvartiry/test_1',elapsed=2,outcome='done')
            first=activity_log.list_attempts(root,kind='import',limit=1);self.assertTrue(first['hasMore'])
            second=activity_log.list_attempts(root,kind='import',limit=1,before=first['nextBefore']);self.assertEqual(second['items'][0]['stages'],{'scroll':.3,'photos':.5})
            self.assertEqual(second['items'][0]['source'],'cian');self.assertEqual(second['items'][0]['error_code'],'invalid_response')
            data=(Path(root)/'activity-log.sqlite3').read_bytes();self.assertNotIn(b'PRIVATE',data);self.assertNotIn(b'SECRET',data);self.assertEqual((Path(root)/'activity-log.sqlite3').stat().st_mode&0o777,0o600)

    def test_invalid_timing_and_unknown_kind_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            for kwargs in [{'kind':'private'},{'kind':'watch','elapsed':float('nan')},{'kind':'watch','elapsed':True},{'kind':'watch','outcome':'raw'}]:
                with self.subTest(kwargs=kwargs),self.assertRaises(ValueError):activity_log.record_attempt(root,**kwargs)


if __name__=='__main__':unittest.main()
