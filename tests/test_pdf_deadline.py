"""A stuck browser, print call or cleanup cannot hold the PDF queue forever."""
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
import card_assets


HTML='<!doctype html><html><body>Private publication 中文</body></html>'


class PdfDeadlineTests(unittest.TestCase):
    def test_queue_wait_uses_total_deadline_and_never_launches_after_expiry(self):
        card_assets._pdf_lock.acquire()
        started=time.monotonic()
        try:
            with patch.object(card_assets.subprocess,'Popen') as spawn, self.assertRaisesRegex(TimeoutError,'Очередь PDF'):
                card_assets._render_pdf(HTML,'chrome',timeout=.08)
            spawn.assert_not_called()
            self.assertLess(time.monotonic()-started,.5)
        finally:card_assets._pdf_lock.release()

    def test_html_transport_is_stdin_and_worker_output_remains_exact_pdf(self):
        expected=hashlib.sha256(HTML.encode()).hexdigest()
        script='import sys,hashlib;content=sys.stdin.buffer.read();assert hashlib.sha256(content).hexdigest()=='+repr(expected)+';sys.stdout.buffer.write(b"%PDF-1.7\\nverified-stdin\\n%%EOF")'
        command=[sys.executable,'-c',script]
        with patch.object(card_assets,'_pdf_worker_command',return_value=command),patch.dict(os.environ,{'DEBUG':'pw:api','PWDEBUG':'1'}):
            real=subprocess.Popen;spawned=[]
            def spawn(*args,**kwargs):
                spawned.append((args,kwargs));return real(*args,**kwargs)
            with patch.object(card_assets.subprocess,'Popen',side_effect=spawn):
                content=card_assets._render_pdf(HTML,'chrome',timeout=3)
        self.assertEqual(content,b'%PDF-1.7\nverified-stdin\n%%EOF')
        worker_args,worker_options=next(item for item in spawned if item[0][0]==command)
        self.assertNotIn(HTML,repr(worker_args))
        self.assertNotIn('DEBUG',worker_options['env']);self.assertNotIn('PWDEBUG',worker_options['env'])
        self.assertTrue(card_assets._pdf_lock.acquire(False))
        card_assets._pdf_lock.release()

    def test_hung_worker_is_reaped_without_stopping_an_unrelated_process(self):
        unrelated=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])
        started=time.monotonic();owned=[];real=subprocess.Popen
        def spawn(*args,**kwargs):
            process=real(*args,**kwargs)
            if args[0][0]==sys.executable:owned.append(process)
            return process
        try:
            with patch.object(card_assets,'_pdf_worker_command',return_value=[sys.executable,'-c','import time;time.sleep(30)']),patch.object(card_assets.subprocess,'Popen',side_effect=spawn):
                with self.assertRaisesRegex(TimeoutError,'Создание PDF превысило'):
                    card_assets._render_pdf(HTML,'chrome',timeout=.6)
            self.assertLess(time.monotonic()-started,1.5)
            self.assertEqual(len(owned),1);self.assertIsNotNone(owned[0].poll())
            self.assertIsNone(unrelated.poll())
            self.assertTrue(card_assets._pdf_lock.acquire(False));card_assets._pdf_lock.release()
        finally:
            unrelated.terminate();unrelated.wait(timeout=2)

    def test_pdf_bytes_do_not_bypass_deadline_when_worker_cleanup_hangs(self):
        command=[sys.executable,'-c','import sys,time;sys.stdin.buffer.read();sys.stdout.buffer.write(b"%PDF-1.7\\nprinted-but-cleanup-stuck");sys.stdout.buffer.flush();time.sleep(30)']
        started=time.monotonic()
        with patch.object(card_assets,'_pdf_worker_command',return_value=command):
            with self.assertRaises(TimeoutError):card_assets._render_pdf(HTML,'chrome',timeout=.6)
        self.assertLess(time.monotonic()-started,1.5)
        self.assertTrue(card_assets._pdf_lock.acquire(False));card_assets._pdf_lock.release()

    @unittest.skipUnless(os.name=='posix','Chrome ownership on macOS/Linux uses process identity')
    def test_detached_browser_ignoring_term_is_stopped_even_after_owner_exits(self):
        with tempfile.TemporaryDirectory() as folder:
            ready=Path(folder)/'ready'
            child='import os,signal,time;from pathlib import Path;signal.signal(signal.SIGTERM,signal.SIG_IGN);Path('+repr(str(ready))+').write_text(str(os.getpid()));time.sleep(30)'
            owner='import subprocess,sys,time;subprocess.Popen([sys.executable,"-c",'+repr(child)+'],start_new_session=True);time.sleep(.35)'
            try:
                with patch.object(card_assets,'_pdf_worker_command',return_value=[sys.executable,'-c',owner]):
                    with self.assertRaises(TimeoutError):card_assets._render_pdf(HTML,'chrome',timeout=1.6)
                self.assertTrue(ready.exists())
                pid=int(ready.read_text())
                # An orphan may briefly remain as a reaped-by-init zombie, but
                # no owned browser is allowed to continue executing after return.
                row=subprocess.run(['ps','-p',str(pid),'-o','stat='],capture_output=True,text=True,timeout=1)
                self.assertTrue(not row.stdout.strip() or row.stdout.strip().startswith('Z'),row.stdout)
            finally:
                if ready.exists():
                    try:os.kill(int(ready.read_text()),signal.SIGKILL)
                    except ProcessLookupError:pass

    def test_worker_errors_are_fixed_codes_not_driver_page_content(self):
        secret='PRIVATE-FULL-HTML-MUST-NOT-APPEAR'
        command=[sys.executable,'-c','import sys;sys.stdin.buffer.read();sys.stderr.write('+repr(secret)+');sys.exit(2)']
        with patch.object(card_assets,'_pdf_worker_command',return_value=command):
            with self.assertRaises(ValueError) as failure:card_assets._render_pdf(HTML,'chrome',timeout=3)
        self.assertNotIn(secret,str(failure.exception))
        self.assertNotIn(HTML,str(failure.exception))

    def test_real_worker_rejects_active_html_before_loading_browser(self):
        result=subprocess.run(card_assets._pdf_worker_command('chrome'),input=b'<html><body><script>PRIVATE_SOURCE</script></body></html>',capture_output=True,timeout=5)
        self.assertEqual(result.returncode,2)
        self.assertEqual(result.stdout,b'')
        self.assertEqual(json.loads(result.stderr),{'error':'validation'})

    def test_worker_keeps_generic_policy_without_rechecking_private_service_receipts(self):
        import pdf_render_worker
        approved='<html><body><p>免费接机服务</p></body></html>'
        # The actual card/receipt gate runs in create_export. The worker must not
        # infer a missing receipt from an intentionally absent private card.
        pdf_render_worker.validate_document(approved)
        with self.assertRaises(ValueError):
            pdf_render_worker.validate_document('<html><body>Комиссия 50%</body></html>')


if __name__=='__main__':unittest.main()
