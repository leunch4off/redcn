"""Measured waiting, failure-before-browser and distinct cached import timings."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'cli'))
import job_progress
import local_server
import node_runtime
import phone_relay
import photo_store
import redcn
import browser_import

URL = 'https://www.cian.ru/rent/flat/321415127/'


class MeasuredProgressTests(unittest.TestCase):
    def test_emit_supports_old_callback_without_hiding_callback_errors(self):
        old = []
        job_progress.emit(old.append, 'Загрузка', stage='navigate')
        self.assertEqual(old, ['Загрузка'])
        richer = []
        job_progress.emit(lambda message, **fields: richer.append(fields), 'Фото', stage='autoCrop', completed=2)
        self.assertEqual(richer, [{'stage':'crop', 'completed':2}])
        def broken(message, **fields): raise TypeError('callback defect')
        with self.assertRaisesRegex(TypeError, 'callback defect'):
            job_progress.emit(broken, 'Загрузка', stage='navigate')

    def test_clock_is_observed_and_manual_check_has_no_fake_countdown(self):
        with tempfile.TemporaryDirectory() as folder:
            history = job_progress.DurationHistory(folder)
            history.record('import:chrome', 60, {'browser':10, 'navigate':50})
            clock = [0]
            tracker = job_progress.JobProgress(history, 'import:chrome', clock=lambda:clock[0], wall_clock=lambda:1000+clock[0])
            clock[0] = 5
            tracker.update('Запуск', stage='browser', maxTotalSeconds=90)
            clock[0] = 15
            report = tracker.snapshot()
            self.assertEqual(report['elapsedSeconds'],15)
            self.assertEqual(report['stageElapsedSeconds'],10)
            self.assertEqual(report['serverTime'],1015)
            self.assertEqual(report['remainingSeconds'],45)
            self.assertEqual(report['sampleCount'],1)
            tracker.update('Нужна проверка',stage='challenge',awaitingUser=True)
            clock[0] = 20
            manual = tracker.snapshot()
            self.assertTrue(manual['awaitingUser'])
            self.assertIsNone(manual['remainingSeconds'])
            self.assertEqual(manual['stages'][-1]['state'],'waitingManual')
            tracker.update('Проверка завершена',stage='navigate',awaitingUser=False)
            clock[0] = 75
            exceeded = tracker.snapshot()
            self.assertTrue(exceeded['estimateExceeded'])
            self.assertIsNone(exceeded['remainingSeconds'])
            self.assertNotIn('percent',exceeded)
            tracker.finish(False)
            self.assertEqual(tracker.snapshot()['stages'][-1]['state'],'error')
            self.assertEqual(history.estimate('import:chrome')['sampleCount'],1)

    def test_history_is_bounded_private_and_keeps_cached_mean_separate(self):
        with tempfile.TemporaryDirectory() as folder:
            history=job_progress.DurationHistory(folder,limit=3)
            self.assertIsNone(history.estimate('import:chrome')['estimateSeconds'])
            for seconds in (10,20,30,40):history.record('import:chrome',seconds,{'navigate':seconds})
            history.record('import:cache',2,{'generate':2})
            self.assertEqual(history.estimate('import:chrome'),{
                'estimateSeconds':30.0,'estimateSource':'history','sampleCount':3,'historyRangeSeconds':[20,40]})
            self.assertEqual(history.estimate('import:cache')['estimateSeconds'],2)
            self.assertEqual(history.path.stat().st_mode & 0o777,0o600)
            self.assertNotIn('url',history.path.read_text().lower())
            self.assertEqual([p.name for p in Path(folder).iterdir()],['import-timings.json'])
            history.path.write_text('{broken')
            self.assertEqual(history.estimate('prepare')['sampleCount'],0)
            history.record('prepare',8,{'photos':8})
            self.assertEqual(history.estimate('prepare')['estimateSeconds'],8)

    def test_cached_job_switches_profile_before_training_mean(self):
        with tempfile.TemporaryDirectory() as folder:
            clock=[0]
            history=job_progress.DurationHistory(folder)
            history.record('import:chrome',100,{'navigate':100})
            tracker=job_progress.JobProgress(history,'import:chrome',clock=lambda:clock[0])
            tracker.set_profile('import:cache')
            self.assertIsNone(tracker.snapshot()['estimateSeconds'])
            clock[0]=3
            tracker.finish(True)
            self.assertEqual(tracker.snapshot()['sampleCount'],1)
            self.assertEqual(tracker.snapshot()['estimateSeconds'],3)
            self.assertEqual(history.estimate('import:chrome')['sampleCount'],1)
            self.assertEqual(history.estimate('import:cache')['estimateSeconds'],3)

    def test_scroll_reports_observed_steps_without_claiming_all_limit_steps_needed(self):
        from test_browser_capture import ScrollPage, snapshot
        page=ScrollPage([snapshot()], [{'height':1000,'top':300,'bottom':True}])
        events=[]
        _,audit=browser_import.capture_scrolled(page,snapshot(),time.monotonic()+10,
            lambda *_args,**_kwargs:None,status=lambda message,**fields:events.append((message,fields)))
        updates=[fields for _,fields in events if 'completed' in fields]
        self.assertEqual(len(updates),audit['scrolls'])
        self.assertTrue(all(fields['stage']=='scroll' for fields in updates))
        self.assertEqual(updates[-1]['completed'],audit['scrolls'])
        self.assertTrue(all('total' not in fields for fields in updates))


class ImportJobProgressTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.directory=Path(self.temp.name)
        self.server=local_server.create_server(self.directory,0)

    def tearDown(self):
        self.server.server_close()
        self.temp.cleanup()

    def wait_job(self, job_id):
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            value=self.server.job_status(job_id)
            if value['state']!='running':return value
            time.sleep(.01)
        self.fail('Fixture job did not finish')

    def test_runtime_failure_is_reported_before_browser_is_called_and_not_averaged(self):
        with patch.object(node_runtime,'resolve_node',side_effect=ValueError('Node.js отсутствует')),patch.object(redcn,'load_listing') as load:
            job_id=self.server.start_import({'url':URL,'loader':'chrome'})
            job=self.wait_job(job_id)
        load.assert_not_called()
        self.assertEqual(job['state'],'error')
        self.assertEqual(job['progress']['stage'],'preflight')
        self.assertEqual(job['progress']['sampleCount'],0)
        self.assertIsNone(job['progress']['estimateSeconds'])
        self.assertEqual(self.server.latest_job()['id'],job_id)
        self.assertFalse((self.directory/'import-timings.json').exists())

    def test_photo_runtime_failure_is_reported_before_browser_is_called(self):
        with patch.object(node_runtime,'resolve_node',return_value='/fixture/node'),patch.object(photo_store,'pillow',side_effect=ValueError('Несовместимая архитектура Pillow')),patch.object(redcn,'load_listing') as load:
            job_id=self.server.start_import({'url':URL,'loader':'chrome'})
            job=self.wait_job(job_id)
        load.assert_not_called()
        self.assertEqual(job['state'],'error')
        self.assertEqual(job['progress']['stage'],'preflight')
        self.assertIn('Pillow',job['message'])
        self.assertFalse(job['progress']['historyEligible'])
        self.assertFalse((self.directory/'import-timings.json').exists())

    def test_twenty_failed_photos_keep_text_but_never_train_fast_success_average(self):
        photos=['https://images.cdn-cian.ru/images/partial-'+str(i)+'.jpg' for i in range(20)]
        def partial(directory,result,report=None,cancel=None):
            result.update(media=[{'sourceUrl':url,'error':'Не удалось загрузить фото'} for url in photos],
                mediaStatus='partial',photoProcessing={'total':20,'checked':0,'cropped':0,
                    'review':0,'failed':0,'skipped':20,'downloadFailed':20})
        with patch.object(node_runtime,'resolve_node',return_value='/fixture/node'),patch.object(redcn,'load_listing',return_value={'url':URL,'text':'Квартира 54 м². 80 000 руб.','images':photos,'cached':True}),patch.object(redcn,'generate',return_value={'fields':{'photos':photos},'titles':['标题'],'body':'正文','tags':'#租房'}),patch.object(phone_relay,'cache_photos',side_effect=partial):
            job_id=self.server.start_import({'url':URL,'loader':'chrome'})
            job=self.wait_job(job_id)
        self.assertEqual(job['state'],'done')
        self.assertTrue(job['partial'])
        self.assertEqual(job['mediaStatus'],'partial')
        self.assertTrue(job['warnings'])
        self.assertIn('не полностью',job['message'])
        self.assertFalse(job['progress']['historyEligible'])
        self.assertEqual(job['progress']['sampleCount'],0)
        self.assertIsNone(job['progress']['estimateSeconds'])
        self.assertFalse((self.directory/'import-timings.json').exists())
        stored=local_server.read_result(self.directory,job['result_id'])
        self.assertEqual(stored['body'],'正文')
        self.assertEqual(stored['photoProcessing']['downloadFailed'],20)

    def test_running_snapshot_advances_and_cached_success_has_separate_history(self):
        entered,release=threading.Event(),threading.Event()
        def load(*args,**kwargs):
            args[5]('Читаем блоки',stage='navigate')
            entered.set();release.wait(2)
            return {'url':URL,'text':'Квартира 54 м². 80 000 руб.','images':[],'cached':True}
        def photos(directory,result,report=None,cancel=None):
            report('Фото не указаны',stage='photos',completed=0,total=0)
            result.update(media=[],mediaStatus='ready')
        with patch.object(node_runtime,'resolve_node',return_value='/fixture/node'),patch.object(redcn,'load_listing',side_effect=load),patch.object(redcn,'generate',return_value={'fields':{'photos':[]},'titles':['标题'],'body':'正文','tags':'#租房'}),patch.object(phone_relay,'cache_photos',side_effect=photos):
            job_id=self.server.start_import({'url':URL,'loader':'chrome'})
            self.assertTrue(entered.wait(1))
            first=self.server.latest_job()
            time.sleep(.12)
            second=self.server.latest_job()
            self.assertEqual(first['progress']['stage'],'navigate')
            self.assertGreater(second['progress']['elapsedSeconds'],first['progress']['elapsedSeconds'])
            self.assertNotIn('tracker',second)
            json.dumps(second)
            release.set();done=self.wait_job(job_id)
        self.assertEqual(done['state'],'done')
        self.assertEqual(done['progress']['stage'],'done')
        self.assertEqual(done['progress']['profile'],'import:cache')
        self.assertEqual(job_progress.DurationHistory(self.directory).estimate('import:chrome')['sampleCount'],0)
        self.assertEqual(job_progress.DurationHistory(self.directory).estimate('import:cache')['sampleCount'],1)
        self.assertEqual(done['progress']['stages'][-1]['id'],'save')


if __name__=='__main__':unittest.main()
