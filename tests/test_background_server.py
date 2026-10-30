"""Background ownership, fixed-port waiting and reversible LaunchAgent files."""
import errno
import importlib.util
from pathlib import Path
import plistlib
import signal
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'cli'))
import background_server

spec = importlib.util.spec_from_file_location('macos_service', Path(__file__).resolve().parents[1]/'scripts'/'macos-service.py')
macos_service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(macos_service)


class CycleStop:
    def __init__(self, cycles=1): self.cycles = cycles; self.done = False; self.waits = []
    def is_set(self): return self.done
    def set(self): self.done = True
    def wait(self, seconds):
        self.waits.append(seconds)
        self.cycles -= 1
        if self.cycles <= 0: self.done = True
        return self.done


class BackgroundServerTests(unittest.TestCase):
    def backend(self):
        backend = Mock()
        backend.create_server.side_effect = OSError(errno.EADDRINUSE, 'occupied')
        backend.matching_server.return_value = False
        return backend

    def test_busy_foreign_port_is_never_bypassed_or_terminated(self):
        backend = self.backend(); stop = CycleStop(2)
        with patch.object(background_server, 'log'):
            background_server.run_service('/tmp/redcn-test', backend=backend, stop_event=stop)
        self.assertEqual(backend.create_server.call_count, 2)
        for call in backend.create_server.call_args_list:
            self.assertEqual(call.args[1], 8765)
        backend.enable_existing_phone.assert_not_called()
        self.assertEqual(stop.waits, [5,5])

    def test_matching_normal_launcher_is_reused_without_new_pair_each_poll(self):
        backend = self.backend(); stop = CycleStop(3)
        backend.matching_server.return_value = True
        with patch.object(background_server, 'log') as logger:
            background_server.run_service('/tmp/redcn-test', backend=backend, stop_event=stop)
        backend.enable_existing_phone.assert_called_once_with(8765,8767)
        self.assertEqual(logger.call_count, 2)  # state transition and stop, no polling flood

    def test_owned_listener_and_tunnel_close_when_phone_cannot_bind(self):
        backend = self.backend(); stop = CycleStop()
        server = Mock(); backend.create_server.side_effect = None
        backend.create_server.return_value = server
        server.phone_relay.enable.side_effect = OSError(errno.EADDRINUSE,'phone occupied')
        with patch.object(background_server,'log'):
            background_server.run_service('/tmp/redcn-test', backend=backend, stop_event=stop)
        server.phone_relay.enable.assert_called_once_with(8767)
        server.serve_forever.assert_not_called()
        server.server_close.assert_called_once()

    def test_sigterm_shuts_down_owned_server_and_restores_handler(self):
        backend = self.backend(); stop = threading.Event(); down = threading.Event()
        server = Mock(); backend.create_server.side_effect = None
        backend.create_server.return_value = server
        server.shutdown.side_effect = down.set
        server.serve_forever.side_effect = lambda **_: signal.raise_signal(signal.SIGTERM)
        previous = signal.getsignal(signal.SIGTERM)
        with patch.object(background_server,'log'):
            background_server.run_service('/tmp/redcn-test',backend=backend,stop_event=stop)
        self.assertTrue(down.wait(1)); self.assertTrue(stop.is_set())
        server.server_close.assert_called_once()
        self.assertIs(signal.getsignal(signal.SIGTERM),previous)

    def test_invalid_ports_cannot_create_any_listener(self):
        backend = self.backend()
        for ports in ((8765,8765),(0,8767),(8765,65536),(True,8767)):
            with self.subTest(ports=ports), self.assertRaises(ValueError):
                background_server.run_service('/tmp/test',*ports,backend=backend)
        backend.create_server.assert_not_called()


class LaunchAgentTests(unittest.TestCase):
    def test_manifest_contains_literal_project_python_and_no_shell_or_browser(self):
        root = Path('/tmp/project with spaces').resolve(); home = Path('/tmp/user')
        manifest = macos_service.manifest(root,home)
        self.assertEqual(manifest['ProgramArguments'][:3], [str(root/'.venv/bin/python'),'-u',str(root/'cli/background_server.py')])
        self.assertTrue(manifest['RunAtLoad']); self.assertTrue(manifest['KeepAlive'])
        self.assertEqual(manifest['ThrottleInterval'],10)
        self.assertEqual(manifest['Umask'],0o077)
        self.assertEqual(manifest['WorkingDirectory'],str(root))
        self.assertNotIn('token',str(manifest).lower())

    def test_plist_is_private_atomic_and_does_not_follow_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary); path = folder/'agent.plist'
            payload = macos_service.manifest(folder,folder)
            macos_service.write_manifest(path,payload)
            with path.open('rb') as stream: self.assertEqual(plistlib.load(stream),payload)
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
            self.assertEqual([p.name for p in folder.iterdir()],['agent.plist'])
            original = path.read_bytes(); link = folder/'link.plist'; link.symlink_to(path)
            with self.assertRaises(ValueError): macos_service.write_manifest(link,payload)
            self.assertEqual(path.read_bytes(),original)

    def test_install_cannot_overwrite_or_unload_another_project_copy(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary); root = folder/'current-copy'
            path = folder/'Library'/'LaunchAgents'/(macos_service.LABEL+'.plist')
            previous = macos_service.manifest(folder/'another-copy',folder)
            macos_service.write_manifest(path,previous)
            original = path.read_bytes()
            with patch.object(macos_service,'ROOT',root), patch.object(macos_service.Path,'home',return_value=folder), patch.object(macos_service.sys,'platform','darwin'), patch.object(macos_service,'launchctl') as launch:
                for action in ('install','remove'):
                    with self.subTest(action=action), self.assertRaisesRegex(ValueError,'другой копии'):
                        macos_service.main([action])
                launch.assert_not_called()
            self.assertEqual(path.read_bytes(),original)
            macos_service.assert_owned_manifest(path,folder/'another-copy')


if __name__ == '__main__': unittest.main()
