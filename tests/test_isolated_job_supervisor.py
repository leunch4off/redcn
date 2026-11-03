import os
from pathlib import Path
import signal
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
from isolated_job_supervisor import IsolatedJobInterrupted,IsolatedJobSupervisor


class IsolatedJobSupervisorTests(unittest.TestCase):
    def test_foreground_cancels_owned_child_while_caller_is_blocked(self):
        busy=threading.Event()
        with self.assertRaises(IsolatedJobInterrupted):
            with IsolatedJobSupervisor(busy=busy.is_set,seconds=10,grace_seconds=.2) as owner:
                process=owner.spawn([sys.executable,'-c','import time;time.sleep(30)'])
                busy.set()
                # No owner.wait/check loop: the independent watcher still
                # interrupts the owned process during a blocking phase.
                process.wait(timeout=3)
                self.assertNotEqual(process.returncode,0)
                self.assertEqual(owner.reason,'foreground-request')

    def test_actual_deadline_and_exception_cleanup_do_not_report_success(self):
        start=time.monotonic()
        with self.assertRaises(IsolatedJobInterrupted):
            with IsolatedJobSupervisor(seconds=.2,grace_seconds=.1) as owner:
                process=owner.spawn([sys.executable,'-c','import time;time.sleep(30)'])
                owner.wait(process)
        self.assertIsNotNone(process.poll());self.assertLess(time.monotonic()-start,3)
        self.assertTrue(owner.receipt()['interrupted'])
        with self.assertRaisesRegex(ValueError,'test-error'):
            with IsolatedJobSupervisor(seconds=10,grace_seconds=.1) as cleanup:
                child=cleanup.spawn([sys.executable,'-c','import time;time.sleep(30)'])
                raise ValueError('test-error')
        self.assertIsNotNone(child.poll())

    def test_success_and_owned_signal_restore_do_not_mutate_unrelated_process(self):
        import subprocess
        other=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])
        previous=signal.getsignal(signal.SIGTERM)
        try:
            with IsolatedJobSupervisor(seconds=5) as owner:
                child=owner.spawn([sys.executable,'-c','pass'])
                self.assertEqual(owner.wait(child),0)
            self.assertFalse(owner.receipt()['interrupted'])
            self.assertIsNone(other.poll());self.assertEqual(signal.getsignal(signal.SIGTERM),previous)
        finally:other.terminate();other.wait(timeout=3)

    def test_signal_sets_cancel_and_reaps_only_the_owned_group(self):
        with self.assertRaises(IsolatedJobInterrupted):
            with IsolatedJobSupervisor(seconds=5,grace_seconds=.1) as owner:
                child=owner.spawn([sys.executable,'-c','import time;time.sleep(30)'])
                os.kill(os.getpid(),signal.SIGTERM)
                owner.wait(child)
        self.assertEqual(owner.reason,'signal-'+str(signal.SIGTERM));self.assertIsNotNone(child.poll())


if __name__=='__main__':unittest.main()
