"""Background ownership, fixed-port waiting and reversible LaunchAgent files."""
import errno
import importlib.util
from pathlib import Path
import plistlib
import signal
import subprocess
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
    def result(self, code=0, stderr=''):
        return subprocess.CompletedProcess([],code,'',stderr)

    def test_runtime_arch_falls_back_when_native_dependencies_cannot_load(self):
        python = Path('/tmp/project with spaces/.venv/bin/python')
        responses = [self.result(1,'dlopen: incompatible architecture'),
                     subprocess.CompletedProcess([],0,'x86_64\n','')]
        with patch.object(macos_service.subprocess,'run',side_effect=responses) as run:
            self.assertEqual(macos_service.select_runtime_arch(python,preferred='arm64'),'x86_64')
        self.assertEqual([call.args[0][:3] for call in run.call_args_list],
                         [['/usr/bin/arch','-arm64',str(python)],['/usr/bin/arch','-x86_64',str(python)]])
        for call in run.call_args_list:
            probe = call.args[0][-1]
            self.assertIn('from PIL import Image, ImageOps',probe)
            self.assertIn('playwright.sync_api, certifi',probe)
            self.assertEqual(call.kwargs['timeout'],15)

    def test_runtime_arch_keeps_working_preference_and_rejects_no_compatible_environment(self):
        with patch.object(macos_service.platform,'machine',return_value='x86_64'), patch.object(macos_service.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'x86_64\n','')) as run:
            self.assertEqual(macos_service.select_runtime_arch('/tmp/python'),'x86_64')
            self.assertEqual(run.call_count,1)
        with patch.object(macos_service.subprocess,'run',side_effect=[subprocess.TimeoutExpired('probe',15),self.result(1)]) as run:
            with self.assertRaisesRegex(ValueError,'Зависимости не изменены'):
                macos_service.select_runtime_arch('/tmp/python',preferred='arm64')
            self.assertEqual(run.call_count,2)

    def test_owned_legacy_manifest_upgrades_to_arch_wrapped_runtime(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(); path = root/'agent.plist'
            macos_service.write_manifest(path,macos_service.manifest(root,root))
            macos_service.assert_owned_manifest(path,root)
            for architecture in ('x86_64','arm64'):
                payload = macos_service.manifest(root,root,runtime_arch=architecture)
                self.assertEqual(payload['ProgramArguments'][:5],
                                 ['/usr/bin/arch','-'+architecture,str(root/'.venv/bin/python'),'-u',str(root/'cli/background_server.py')])
                macos_service.write_manifest(path,payload)
                macos_service.assert_owned_manifest(path,root)
            payload['ProgramArguments'][4] = '/another-copy/cli/background_server.py'
            with path.open('wb') as stream: plistlib.dump(payload,stream)
            with self.assertRaisesRegex(ValueError,'другой копии'):
                macos_service.assert_owned_manifest(path,root)

    def test_reload_waits_for_async_unload_and_retries_only_absent_bootstrap_race(self):
        responses = [self.result(), self.result(), self.result(), self.result(113),
                     self.result(5,'Input/output error'), self.result(113), self.result()]
        with patch.object(macos_service,'assert_owned_manifest') as owned, patch.object(macos_service,'launchctl',side_effect=responses) as launch, patch.object(macos_service.time,'sleep') as sleep:
            macos_service.reload_agent('gui/501',Path('/tmp/agent.plist'))
        owned.assert_called_once_with(Path('/tmp/agent.plist'))
        self.assertEqual([call.args[0] for call in launch.call_args_list],
                         ['bootout','print','print','print','bootstrap','print','bootstrap'])
        self.assertEqual([call.args[0] for call in sleep.call_args_list],[.2,.2,.3])

    def test_reload_does_not_retry_permanent_error_or_loaded_label(self):
        scenarios = (
            [self.result(),self.result(113),self.result(9,'Bad file descriptor')],
            [self.result(),self.result(113),self.result(5,'Input/output error'),self.result()],
        )
        for responses in scenarios:
            with self.subTest(responses=responses), patch.object(macos_service,'assert_owned_manifest'), patch.object(macos_service,'launchctl',side_effect=responses) as launch, patch.object(macos_service.time,'sleep') as sleep:
                with self.assertRaisesRegex(ValueError,'scripts/macos-service.py status'):
                    macos_service.reload_agent('gui/501',Path('/tmp/agent.plist'))
                self.assertEqual(sum(call.args[0]=='bootstrap' for call in launch.call_args_list),1)
                self.assertEqual(sum(call.args[0]=='bootout' for call in launch.call_args_list),1)
                sleep.assert_not_called()

    def test_reload_attempts_and_unload_wait_are_bounded(self):
        responses = [self.result(),self.result(113),self.result(5),self.result(113),
                     self.result(5),self.result(113),self.result(5),self.result(113)]
        with patch.object(macos_service,'assert_owned_manifest'), patch.object(macos_service,'launchctl',side_effect=responses) as launch, patch.object(macos_service.time,'sleep'):
            with self.assertRaisesRegex(ValueError,'launchd не принял'):
                macos_service.reload_agent('gui/501',Path('/tmp/agent.plist'))
            self.assertEqual(sum(call.args[0]=='bootstrap' for call in launch.call_args_list),3)
        with patch.object(macos_service,'assert_owned_manifest'), patch.object(macos_service,'launchctl',return_value=self.result()) as launch, patch.object(macos_service.time,'monotonic',side_effect=[0,1,3]), patch.object(macos_service.time,'sleep'):
            with self.assertRaisesRegex(ValueError,'ещё завершает'):
                macos_service.reload_agent('gui/501',Path('/tmp/agent.plist'),timeout=2)
            self.assertFalse(any(call.args[0]=='bootstrap' for call in launch.call_args_list))

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
