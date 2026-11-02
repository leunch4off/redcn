from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
from import_job_journal import ImportJobJournal,ImportRequestConflict


class ImportJobJournalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.journal=ImportJobJournal(self.root)
    def tearDown(self):self.journal.close();self.temp.cleanup()
    def reserve(self,**kw):return self.journal.reserve({'url':'https://example.invalid/test'},actor='fixture owner',request_id='request-fixture-001',**kw)
    def save_card(self,card_id,body='manual edit'):
        with sqlite3.connect(self.root/'listings.sqlite3') as db:
            db.execute('CREATE TABLE IF NOT EXISTS results (id TEXT PRIMARY KEY,payload TEXT)')
            db.execute('INSERT OR REPLACE INTO results VALUES (?,?)',(card_id,json.dumps({'body':body})))

    def test_replay_concurrency_and_changed_request_never_allocate_second_card(self):
        with ThreadPoolExecutor(max_workers=5) as pool:rows=list(pool.map(lambda _:self.reserve(),range(12)))
        self.assertEqual(len({row['jobId'] for row in rows}),1);self.assertEqual(len({row['resultId'] for row in rows}),1)
        self.assertEqual(sum(not row['replayed'] for row in rows),1)
        with self.assertRaises(ImportRequestConflict):self.journal.reserve({'url':'different'},actor='fixture owner',request_id='request-fixture-001')
        self.save_card(rows[0]['resultId']);self.journal.finish(rows[0]['jobId'],'done')
        self.assertTrue(self.reserve()['replayed']);self.assertEqual(self.reserve()['state'],'done')

    def test_new_journal_preserves_live_owner_and_recovery_never_rewrites_manual_card(self):
        job=self.reserve();self.save_card(job['resultId'])
        other=ImportJobJournal(self.root)
        try:
            self.assertEqual(other.recover(),[])
            self.journal.progress(job['jobId'],'vision');self.journal.close()
            self.assertEqual(other.recover(),[job['jobId']]);status=other.status(job['jobId'])
            self.assertEqual(status['state'],'interrupted');self.assertEqual(status['recovery']['action'],'open-card-and-prepare-photos')
            self.assertFalse(status['recovery']['partialCheckpointIsComplete'])
            with sqlite3.connect(self.root/'listings.sqlite3') as db:self.assertEqual(json.loads(db.execute('SELECT payload FROM results').fetchone()[0])['body'],'manual edit')
            self.assertTrue(other.reserve({'url':'https://example.invalid/test'},actor='fixture owner',request_id='request-fixture-001')['replayed'])
        finally:other.close()

    def test_incomplete_job_cannot_finish_and_other_process_cannot_finish_it(self):
        job=self.reserve()
        with self.assertRaisesRegex(ValueError,'без сохранённой'):self.journal.finish(job['jobId'],'done')
        other=ImportJobJournal(self.root)
        try:
            with self.assertRaisesRegex(ValueError,'не владеет'):other.finish(job['jobId'],'done')
        finally:other.close()
        self.journal.finish(job['jobId'],'cancelled')
        self.assertEqual(self.journal.status(job['jobId'])['recovery']['action'],'repeat-import')

    def test_real_process_termination_releases_lease_without_resetting_persisted_result(self):
        code="""import json,sys,time
from pathlib import Path
from import_job_journal import ImportJobJournal
root=Path(sys.argv[1]);journal=ImportJobJournal(root)
row=journal.reserve({'url':'isolated'},actor='child fixture',request_id='request-subprocess-001')
journal.progress(row['jobId'],'vision');(root/'ready.json').write_text(json.dumps(row))
time.sleep(30)
"""
        env=dict(__import__('os').environ);env['PYTHONPATH']=str(Path(__file__).resolve().parents[1]/'cli')
        process=subprocess.Popen([sys.executable,'-c',code,str(self.root)],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        try:
            deadline=time.monotonic()+5
            while not (self.root/'ready.json').exists() and time.monotonic()<deadline and process.poll() is None:time.sleep(.01)
            self.assertTrue((self.root/'ready.json').exists(),process.stderr.read().decode() if process.poll() is not None else 'worker did not start')
            row=json.loads((self.root/'ready.json').read_text());self.save_card(row['resultId'])
            self.assertEqual(self.journal.recover(),[])
            process.terminate();process.wait(timeout=5)
            self.assertEqual(self.journal.recover(),[row['jobId']])
            status=self.journal.status(row['jobId']);self.assertEqual(status['state'],'interrupted');self.assertEqual(status['result_id'],row['resultId'])
        finally:
            if process.poll() is None:process.kill();process.wait(timeout=5)
            process.stderr.close()


if __name__=='__main__':unittest.main()
