"""No Mac, model, user caches or network required for runtime coverage."""
import ast
import importlib.util
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

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'cli'))
import photo_ai_runtime as runtime

CONFIG = {'enabled': True, 'endpoint': runtime.PRIVATE_ENDPOINT}


class ImmediateProcess:
    returncode = 0
    pid = 12345
    def poll(self):
        return self.returncode


class RuntimeTests(unittest.TestCase):
    def test_ineligible_cases_never_touch_install_paths_or_spawn(self):
        for platform, config in [('linux', CONFIG), ('darwin', {'enabled': False, 'endpoint': runtime.PRIVATE_ENDPOINT}),
                ('darwin', {'enabled': 1, 'endpoint': runtime.PRIVATE_ENDPOINT}),
                ('darwin', {'enabled': True, 'endpoint': 'http://127.0.0.1:11434'}),
                ('darwin', {'enabled': True, 'endpoint': 'http://localhost:11435'}),
                ('darwin', {'enabled': True, 'endpoint': 'http://127.0.0.1:11435/'})]:
            with self.subTest(platform=platform, config=config), patch.object(runtime.sys, 'platform', platform), \
                    patch.object(runtime, '_installed') as installed, patch.object(runtime.subprocess, 'Popen') as spawn:
                self.assertFalse(runtime.ensure_started('/unused', config))
                installed.assert_not_called()
                spawn.assert_not_called()

    def test_missing_app_or_keys_never_spawn(self):
        with tempfile.TemporaryDirectory() as td, patch.object(runtime.sys, 'platform', 'darwin'), \
                patch.object(runtime.Path, 'home', return_value=Path(td)), patch.object(runtime.subprocess, 'Popen') as spawn:
            self.assertFalse(runtime.ensure_started(td, CONFIG))
            self.assertEqual(list(Path(td).iterdir()), [])
            spawn.assert_not_called()

    def test_exact_private_paths_match_installer_and_existing_keys_are_required(self):
        installer = runtime.INSTALLER
        tree = ast.parse(installer.read_text())
        version = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                       and any(isinstance(target, ast.Name) and target.id == 'VERSION' for target in node.targets))
        self.assertEqual(version, runtime.OLLAMA_VERSION)
        with tempfile.TemporaryDirectory() as td, patch.object(runtime.Path, 'home', return_value=Path(td)):
            base = Path(td) / '.cache/redcn/tools/ollama' / version
            cli = base / 'Ollama.app/Contents/Resources/ollama'
            cli.parent.mkdir(parents=True)
            cli.touch()
            (base / 'install.json').write_text('{}')
            keys = Path(td) / '.ollama'
            keys.mkdir()
            (keys / 'id_ed25519').touch()
            self.assertFalse(runtime._installed())
            (keys / 'id_ed25519.pub').touch()
            self.assertTrue(runtime._installed())

    def test_cancelled_or_exhausted_budget_never_spawn(self):
        cancel = threading.Event()
        cancel.set()
        with patch.object(runtime.sys, 'platform', 'darwin'), patch.object(runtime, '_installed', return_value=True), \
                patch.object(runtime.subprocess, 'Popen') as spawn:
            self.assertFalse(runtime.ensure_started('/unused', CONFIG, cancel=cancel))
            for deadline in (time.monotonic() - 1, time.monotonic() + .1, float('nan'), True):
                self.assertFalse(runtime.ensure_started('/unused', CONFIG, deadline=deadline))
            spawn.assert_not_called()

    def test_installed_service_calls_only_start_and_keeps_environment(self):
        with patch.object(runtime.sys, 'platform', 'darwin'), patch.object(runtime, '_installed', return_value=True), \
                patch.object(runtime.subprocess, 'Popen', return_value=ImmediateProcess()) as spawn:
            self.assertTrue(runtime.ensure_started('/unused', CONFIG))
        args, kwargs = spawn.call_args
        self.assertEqual(args[0], [sys.executable, str(runtime.INSTALLER), '--stage', 'start', '--apply'])
        self.assertNotIn('--allow-initial-keypair', args[0])
        self.assertNotIn('env', kwargs)
        self.assertTrue(kwargs['start_new_session'])
        self.assertIs(kwargs['stdout'], subprocess.DEVNULL)
        self.assertIs(kwargs['stderr'], subprocess.DEVNULL)

    def test_start_failures_are_optional_and_not_logged(self):
        with patch.object(runtime.sys, 'platform', 'darwin'), patch.object(runtime, '_installed', return_value=True), \
                patch.object(runtime.subprocess, 'Popen', side_effect=OSError('sensitive details')):
            self.assertFalse(runtime.ensure_started('/unused', CONFIG))

    def test_waiting_for_another_start_is_cancellable(self):
        cancel = threading.Event()
        runtime._START_LOCK.acquire()
        timer = threading.Timer(.04, cancel.set)
        timer.start()
        try:
            with patch.object(runtime.sys, 'platform', 'darwin'), patch.object(runtime, '_installed', return_value=True), \
                    patch.object(runtime.subprocess, 'Popen') as spawn:
                before = time.monotonic()
                self.assertFalse(runtime.ensure_started('/unused', CONFIG, cancel=cancel))
                self.assertLess(time.monotonic() - before, .3)
                spawn.assert_not_called()
        finally:
            runtime._START_LOCK.release()
            timer.join()

    def test_cancellation_stops_and_reaps_only_our_real_starting_helper(self):
        with tempfile.TemporaryDirectory() as td:
            helper = Path(td) / 'scripts' / 'helper.py'
            helper.parent.mkdir()
            marker = Path(td) / 'pid'
            helper.write_text('import os,time\nfrom pathlib import Path\nPath(%r).write_text(str(os.getpid()))\ntime.sleep(30)\n' % str(marker))
            cancel = threading.Event()
            result = []
            with patch.object(runtime.sys, 'platform', 'darwin'), patch.object(runtime, '_installed', return_value=True), \
                    patch.object(runtime, 'INSTALLER', helper):
                thread = threading.Thread(target=lambda: result.append(runtime.ensure_started(td, CONFIG, cancel=cancel)))
                thread.start()
                deadline = time.monotonic() + 2
                while not marker.exists() and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertTrue(marker.exists())
                pid = int(marker.read_text())
                cancel.set()
                thread.join(2)
            self.assertFalse(thread.is_alive())
            self.assertEqual(result, [False])
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)

    def test_one_background_thread_returns_immediately_and_can_retry_new_settings(self):
        entered, release = threading.Event(), threading.Event()
        def startup(*args, **kwargs):
            entered.set()
            release.wait(1)
            return False
        with patch.object(runtime.sys, 'platform', 'darwin'), patch.object(runtime, '_BACKGROUND_THREAD', None), \
                patch('photo_ai_settings.load_config', return_value=CONFIG), patch.object(runtime, 'ensure_started', side_effect=startup) as ensure:
            before = time.monotonic()
            first = runtime.start_background('/unused')
            self.assertLess(time.monotonic() - before, .1)
            self.assertTrue(first.daemon)
            self.assertTrue(entered.wait(1))
            self.assertIs(runtime.start_background('/unused'), first)
            self.assertEqual(ensure.call_count, 1)
            release.set()
            first.join(1)
            second = runtime.start_background('/unused')
            second.join(1)
            self.assertIsNot(first, second)
            self.assertEqual(ensure.call_count, 2)

    def test_linux_background_does_not_create_thread(self):
        with patch.object(runtime.sys, 'platform', 'linux'), patch.object(runtime.threading, 'Thread') as thread:
            self.assertIsNone(runtime.start_background('/unused'))
            thread.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
