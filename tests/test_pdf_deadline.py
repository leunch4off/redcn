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


def process_state(pid):
    """Inspect the same PID namespace as kill/getpid, not a host /proc alias."""
    try:os.kill(pid,0)
    except ProcessLookupError:return None
    if sys.platform.startswith('linux'):
        namespace=os.readlink('/proc/self/ns/pid')
        for entry in Path('/proc').iterdir():
            if not entry.name.isdigit():continue
            try:
                if os.readlink(entry/'ns/pid')!=namespace:continue
                fields={line.split(':',1)[0]:line.split(':',1)[1].split() for line in (entry/'status').read_text().splitlines() if ':' in line}
                if int(fields['NSpid'][-1])==pid:return fields['State'][0]
            except (OSError,KeyError,ValueError):continue
        return None
    result=subprocess.run(['ps','-p',str(pid),'-o','stat='],capture_output=True,text=True,timeout=1)
    return result.stdout.strip() or None


def proc_fixture(root,host,pid,parent,group,namespace,start):
    entry=root/str(host);(entry/'ns').mkdir(parents=True)
    (entry/'ns/pid').symlink_to('pid:['+str(namespace)+']')
    (entry/'status').write_text('Pid:\t'+str(host)+'\nPPid:\t'+str(parent)+'\nNSpid:\t'+str(host)+' '+str(pid)+'\nNSpgid:\t'+str(host)+' '+str(group)+'\nState:\tS (sleeping)\n')
    values=['S',str(parent),str(host)]+['0']*16+[str(start)]
    (entry/'stat').write_text(str(host)+' (not-a-command-line) '+' '.join(values))


def observed_leaf_tracker(ready,ack,events):
    """Fixture gate: an observed leaf, rather than a presumed sleep interval."""
    original=card_assets._PdfOwnedProcesses
    class ObservedLeaf(original):
        cleaning=False
        def refresh(self,deadline,**kwargs):
            result=super().refresh(deadline,**kwargs)
            if not self.cleaning and ready.exists() and int(ready.read_text()) in self.identities and not ack.exists():
                ack.write_text('observed')
                events.append('observed-before-cleanup')
            return result
        def stop(self,deadline):
            self.cleaning=True
            events.append('owner-exited' if self.process.poll() is not None else 'owner-running')
            return super().stop(deadline)
    return ObservedLeaf


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
        script='import sys,hashlib,json;content=sys.stdin.buffer.read();assert hashlib.sha256(content).hexdigest()=='+repr(expected)+';pdf=b"%PDF-1.7\\nverified-stdin\\n%%EOF";sys.stdout.buffer.write(pdf);sys.stderr.write(json.dumps({"protocol":"redcn-pdf-v1","complete":True,"bytes":len(pdf),"sha256":hashlib.sha256(pdf).hexdigest()}))'
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
            ready=Path(folder)/'ready';ack=Path(folder)/'observed';events=[]
            child='import os,signal,time;from pathlib import Path;signal.signal(signal.SIGTERM,signal.SIG_IGN);Path('+repr(str(ready))+').write_text(str(os.getpid()));time.sleep(30)'
            owner='import subprocess,sys,time;from pathlib import Path;subprocess.Popen([sys.executable,"-c",'+repr(child)+'],start_new_session=True);\nwhile not Path('+repr(str(ack))+').exists():time.sleep(.005)'
            try:
                with patch.object(card_assets,'_pdf_worker_command',return_value=[sys.executable,'-c',owner]),patch.object(card_assets,'_PdfOwnedProcesses',observed_leaf_tracker(ready,ack,events)):
                    with self.assertRaises(TimeoutError):card_assets._render_pdf(HTML,'chrome',timeout=1.6)
                self.assertTrue(ready.exists())
                self.assertEqual(events,['observed-before-cleanup','owner-exited'])
                pid=int(ready.read_text())
                # An orphan may briefly remain as a reaped-by-init zombie, but
                # no owned browser is allowed to continue executing after return.
                state=process_state(pid)
                self.assertTrue(not state or state.startswith('Z'),state)
            finally:
                if ready.exists():
                    try:os.kill(int(ready.read_text()),signal.SIGKILL)
                    except ProcessLookupError:pass

    @unittest.skipUnless(sys.platform.startswith('linux'),'Linux PID namespace translation')
    def test_host_proc_pid_collision_never_overwrites_owned_namespace_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            proc_fixture(root,1000,5,0,5,111,100)
            proc_fixture(root,1001,6,1000,6,111,200)
            # A foreign process exposes the same inner PID 6, but belongs to a
            # sibling namespace and cannot become a signal target.
            proc_fixture(root,2001,6,0,6,222,900)
            (root/'self').symlink_to(root/'1000',target_is_directory=True)
            rows=card_assets._pdf_linux_snapshot(time.monotonic()+1,proc_root=root)
            self.assertEqual(rows[6],(5,6,'linux:200','S'))
            self.assertNotIn('linux:900',[row[2] for row in rows.values()])

    @unittest.skipUnless(sys.platform.startswith('linux'),'Linux owned-process metadata')
    def test_known_process_metadata_denial_is_unknown_not_confirmed_gone(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            proc_fixture(root,1000,5,0,5,111,100)
            proc_fixture(root,1001,6,1000,6,111,200)
            (root/'self').symlink_to(root/'1000',target_is_directory=True)
            read_text=Path.read_text;readlink=os.readlink
            def denied_stat(path,*args,**kwargs):
                if path==root/'1001/stat':raise PermissionError('Denied process metadata')
                return read_text(path,*args,**kwargs)
            with patch.object(Path,'read_text',denied_stat):
                self.assertIsNone(card_assets._pdf_linux_snapshot(time.monotonic()+1,{6},proc_root=root))
            def denied_namespace(path,*args,**kwargs):
                if Path(path)==root/'1001/ns/pid':raise PermissionError('Denied namespace metadata')
                return readlink(path,*args,**kwargs)
            with patch.object(card_assets.os,'readlink',side_effect=denied_namespace):
                self.assertIsNone(card_assets._pdf_linux_snapshot(time.monotonic()+1,{6},proc_root=root))

    def test_darwin_metadata_failure_or_malformed_rows_are_not_an_empty_snapshot(self):
        for result in (subprocess.CompletedProcess([],0,'malformed metadata\n',''),subprocess.CompletedProcess([],1,'','ps: metadata not permitted')):
            with self.subTest(result=result),patch.object(card_assets.sys,'platform','darwin'),patch.object(card_assets.subprocess,'run',return_value=result):
                self.assertIsNone(card_assets._pdf_process_snapshot(.1,{123}))
        with patch.object(card_assets.sys,'platform','darwin'),patch.object(card_assets.subprocess,'run',return_value=subprocess.CompletedProcess([],1,'','')):
            self.assertEqual(card_assets._pdf_process_snapshot(.1,{123}),{})

    @unittest.skipUnless(os.name=='posix','Owned POSIX descendants')
    def test_kill_delivery_is_confirmed_for_detached_leaf_before_return(self):
        with tempfile.TemporaryDirectory() as folder:
            ready=Path(folder)/'ready';ack=Path(folder)/'observed';events=[];delayed=[];real_killpg=os.killpg
            child='import os,signal,time;from pathlib import Path;signal.signal(signal.SIGTERM,signal.SIG_IGN);Path('+repr(str(ready))+').write_text(str(os.getpid()));time.sleep(30)'
            owner='import subprocess,sys,time;from pathlib import Path;subprocess.Popen([sys.executable,"-c",'+repr(child)+'],start_new_session=True);\nwhile not Path('+repr(str(ack))+').exists():time.sleep(.005)'
            def delayed_kill(group,signum):
                if signum==signal.SIGKILL and ready.exists() and group==int(ready.read_text()):
                    def deliver():
                        try:real_killpg(group,signum)
                        except ProcessLookupError:pass
                    timer=threading.Timer(.06,deliver);timer.start();delayed.append(timer)
                else:real_killpg(group,signum)
            try:
                with patch.object(card_assets,'_pdf_worker_command',return_value=[sys.executable,'-c',owner]),patch.object(card_assets.os,'killpg',side_effect=delayed_kill),patch.object(card_assets,'_PdfOwnedProcesses',observed_leaf_tracker(ready,ack,events)):
                    with self.assertRaises(TimeoutError):card_assets._render_pdf(HTML,'chrome',timeout=1.6)
                self.assertEqual(events,['observed-before-cleanup','owner-exited'])
                self.assertTrue(delayed,'The detached leaf must have been tracked and signalled')
                state=process_state(int(ready.read_text()))
                self.assertTrue(not state or state.startswith('Z'),state)
            finally:
                for timer in delayed:timer.join(1)
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

    @unittest.skipUnless(os.name=='posix','Unsupported detached-worker fixture')
    def test_raw_pdf_from_fast_unregistered_worker_is_not_false_success(self):
        with tempfile.TemporaryDirectory() as folder:
            ready=Path(folder)/'ready'
            child='import os,time;from pathlib import Path;Path('+repr(str(ready))+').write_text(str(os.getpid()));time.sleep(30)'
            owner='import subprocess,sys,time;from pathlib import Path;subprocess.Popen([sys.executable,"-c",'+repr(child)+'],start_new_session=True,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);\nwhile not Path('+repr(str(ready))+').exists():time.sleep(.005)\nsys.stdout.buffer.write(b"%PDF-1.7\\nunregistered")'
            try:
                with patch.object(card_assets,'_pdf_worker_command',return_value=[sys.executable,'-c',owner]):
                    with self.assertRaisesRegex(ValueError,'не подтвердил завершение') as failure:
                        card_assets._render_pdf(HTML,'chrome',timeout=2)
                self.assertIn('может быть неполной',str(failure.exception))
                self.assertNotIn('процессы Chrome остановлены',str(failure.exception))
            finally:
                # This arbitrary payload intentionally bypasses the supported
                # renderer protocol. Never imply its unobserved orphan was
                # reaped; clean only the fixture's own recorded child here.
                if ready.exists():
                    try:os.kill(int(ready.read_text()),signal.SIGKILL)
                    except ProcessLookupError:pass

    def test_unavailable_process_metadata_does_not_claim_cleanup_was_confirmed(self):
        with patch.object(card_assets,'_pdf_worker_command',return_value=[sys.executable,'-c','import time;time.sleep(30)']),patch.object(card_assets,'_pdf_process_snapshot',return_value=None):
            with self.assertRaisesRegex(ValueError,'остановка собственного Chrome не подтверждена'):
                card_assets._render_pdf(HTML,'chrome',timeout=.4)
        self.assertTrue(card_assets._pdf_lock.acquire(False));card_assets._pdf_lock.release()

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
