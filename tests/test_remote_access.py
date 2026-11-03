"""Temporary HTTPS lifecycle without a real network, account, or binary download."""
import io
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'cli'))
import remote_access


FIRST = 'https://quiet-marble-otter.trycloudflare.com'
SECOND = 'https://bright-roof-bird.trycloudflare.com'


class FakeProcess:
    def __init__(self, output='', returncode=None):
        self.stderr = io.StringIO(output)
        self.returncode = returncode
        self.pid = 94001
        self.terminated = False

    def poll(self): return self.returncode
    def terminate(self): self.terminated = True; self.returncode = 0
    def kill(self): self.terminated = True; self.returncode = -9
    def wait(self, timeout=None): return self.returncode


def running_output(url=FIRST):
    return f'INF Your quick Tunnel has been created!\n| {url} |\nINF Registered tunnel connection connIndex=0 protocol=http2\n'


class QuickTunnelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.binary = self.folder / 'cloudflared'
        self.binary.write_text('not executed by tests')
        self.binary.chmod(0o700)
        self.managers = []

    def tearDown(self):
        for manager in self.managers: manager.stop()
        self.temp.cleanup()

    def manager(self, **kwargs):
        instance = remote_access.RemoteAccess(self.folder, executable=self.binary, ready_timeout=kwargs.pop('ready_timeout', 1), **kwargs)
        self.managers.append(instance)
        return instance

    def wait_state(self, manager, expected):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            status = manager.status()
            if status['state'] == expected: return status
            time.sleep(.01)
        self.fail(f'Expected {expected}, got {manager.status()}')

    def test_only_complete_https_quick_origin_is_trusted(self):
        for valid in (FIRST, '| ' + FIRST + ' |', '{"url":"' + FIRST + '"}', '\x1b[36m' + FIRST + '\x1b[0m'):
            self.assertEqual(remote_access.quick_tunnel_url(valid), FIRST)
        for invalid in ('http://quiet-marble-otter.trycloudflare.com', FIRST + '.evil.com', FIRST + ':443',
                        FIRST + '/api/local/', FIRST + '?token=x', FIRST + '#pair',
                        'https://user:password@quiet-marble-otter.trycloudflare.com',
                        'https://trycloudflare.com', 'https://-bad.trycloudflare.com', 'https://example.com'):
            self.assertIsNone(remote_access.quick_tunnel_url(invalid), invalid)

    def test_explicit_binary_must_exist_and_be_executable(self):
        self.assertEqual(remote_access.find_cloudflared(self.binary), str(self.binary.resolve()))
        self.binary.chmod(0o600)
        self.assertIsNone(remote_access.find_cloudflared(self.binary))
        manager = self.manager()
        with patch.object(remote_access.subprocess, 'Popen') as spawn:
            result = manager.start(8767)
        self.assertEqual(result['state'], 'error'); self.assertFalse(result['available']); spawn.assert_not_called()

    def test_local_port_boundary_rejects_desktop_and_invalid_values(self):
        manager = self.manager()
        for port in (True, 0, -1, 65536, 8765, '8767'):
            with self.subTest(port=port), self.assertRaises(ValueError): manager.start(port)

    def test_ready_registers_exact_origin_and_stop_removes_it(self):
        allowed = set()
        manager = self.manager(on_url=allowed.add, on_stop=allowed.discard)
        process = FakeProcess(running_output())
        with patch.object(remote_access.subprocess, 'Popen', return_value=process) as spawn, patch.dict(os.environ, {'TUNNEL_TOKEN':'not-inherited'}):
            self.assertIn(manager.start(8767)['state'], ('starting', 'ready'))
            status = self.wait_state(manager, 'ready')
            self.assertEqual(status['url'], FIRST); self.assertTrue(status['temporary'])
            command = spawn.call_args.args[0]
            self.assertEqual(command[command.index('--url') + 1], 'http://127.0.0.1:8767')
            self.assertIn('--no-autoupdate', command); self.assertEqual(command[command.index('--protocol') + 1], 'http2')
            configuration = Path(command[command.index('--config') + 1])
            self.assertEqual(configuration.read_text(), '{}\n')
            self.assertEqual(configuration.stat().st_mode & 0o777, 0o600)
            self.assertNotIn('TUNNEL_TOKEN', spawn.call_args.kwargs['env'])
            self.assertEqual(spawn.call_args.kwargs['env'].get('HOME'), os.environ.get('HOME'))
            manager.start(8767); self.assertEqual(spawn.call_count, 1)
            self.assertEqual(allowed, {FIRST})
            self.assertEqual(manager.stop()['state'], 'stopped')
        self.assertTrue(process.terminated); self.assertEqual(allowed, set())

    def test_missing_connection_deadline_stops_owned_process_and_revokes_announced_url(self):
        allowed = set()
        manager = self.manager(on_url=allowed.add, on_stop=allowed.discard, ready_timeout=.05)
        process = FakeProcess(FIRST + '\n')
        with patch.object(remote_access.subprocess, 'Popen', return_value=process):
            manager.start(8767)
            result = self.wait_state(manager, 'error')
            deadline = time.monotonic() + 1
            while not process.terminated and time.monotonic() < deadline: time.sleep(.01)
        self.assertIn('интернет', result['error']); self.assertIsNone(result['url'])
        self.assertTrue(process.terminated); self.assertEqual(allowed, set())

    def test_process_failure_and_allowlist_failure_do_not_leave_an_open_origin(self):
        manager = self.manager()
        with patch.object(remote_access.subprocess, 'Popen', return_value=FakeProcess(returncode=2)):
            manager.start(8767); self.wait_state(manager, 'error')
        removed = []
        manager = self.manager(on_url=lambda url: (_ for _ in ()).throw(RuntimeError('allowlist failed')), on_stop=removed.append)
        process = FakeProcess(running_output())
        with patch.object(remote_access.subprocess, 'Popen', return_value=process):
            manager.start(8767); self.wait_state(manager, 'error')
        self.assertEqual(removed, [FIRST])

    def test_old_callback_cannot_reopen_stopped_origin_or_remove_new_origin(self):
        allowed = set(); entered = threading.Event(); release = threading.Event()
        def register(url):
            if url == FIRST:
                entered.set(); release.wait(2)
            allowed.add(url)
        manager = self.manager(on_url=register, on_stop=allowed.discard)
        first, second = FakeProcess(running_output(FIRST)), FakeProcess(running_output(SECOND))
        with patch.object(remote_access.subprocess, 'Popen', side_effect=[first, second]):
            manager.start(8767); self.assertTrue(entered.wait(1))
            started = time.monotonic(); manager.stop(); self.assertLess(time.monotonic()-started, .5)
            manager.start(8767); self.wait_state(manager, 'ready')
            release.set()
            deadline = time.monotonic() + 1
            while FIRST in allowed and time.monotonic() < deadline: time.sleep(.01)
            # The released old callback may run just after the first check.
            time.sleep(.05)
            self.assertEqual(allowed, {SECOND}); self.assertEqual(manager.status()['url'], SECOND)


if __name__ == '__main__': unittest.main()
