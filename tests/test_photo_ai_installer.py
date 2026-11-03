import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import stat
import ssl
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import zipfile

INSTALLER = Path(__file__).resolve().parents[1] / "scripts" / "install-photo-ai-macos.py"
spec = importlib.util.spec_from_file_location("install_ollama", str(INSTALLER))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class SetupTests(unittest.TestCase):
    def test_native_architecture_check_uses_macos_lipo_input_before_command(self):
        app = Path('/private/example/Ollama.app')
        def verify_tool(args, *unused):
            if args[:3] == ['/usr/bin/codesign', '-d', '--verbose=4']:
                return SimpleNamespace(stdout='', stderr='Authority=Developer ID Application: Example\nTeamIdentifier=ABCDEFGHIJ\n')
            return SimpleNamespace(stdout='', stderr='accepted')
        with patch.object(m, 'run', side_effect=verify_tool) as tool, patch.object(m, 'emit'):
            m.verify(app)
        self.assertEqual(tool.call_args_list[-1].args[0],
            ['/usr/bin/lipo', str(app / 'Contents/Resources/ollama'), '-verify_arch', 'arm64'])

    def test_spawn_signal_is_deferred_until_child_handle_is_registered(self):
        observed = []
        def interrupted(number, frame):
            observed.append('delivered')
            raise KeyboardInterrupt()
        previous = signal.signal(signal.SIGTERM, interrupted)
        process = None
        try:
            with self.assertRaises(KeyboardInterrupt):
                with m.defer_start_signals():
                    process = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(30)'],
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        start_new_session=True)
                    signal.raise_signal(signal.SIGTERM)
                    self.assertEqual(observed, [])
                    observed.append('registered')
            self.assertEqual(observed, ['registered', 'delivered'])
        finally:
            signal.signal(signal.SIGTERM, previous)
            if process is not None:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=2)

    def test_installed_start_is_not_blocked_by_download_disk_requirement(self):
        with patch.object(m.sys, 'platform', 'darwin'), patch.object(m.platform, 'mac_ver', return_value=('14.0', '', '')), \
                patch.object(m, 'run', return_value=SimpleNamespace(stdout='arm64\n')), \
                patch.object(m.Path, 'is_file', return_value=True), patch.object(m.shutil, 'disk_usage', return_value=SimpleNamespace(free=0)) as disk:
            m.preflight(require_storage=False)
            disk.assert_not_called()
            with self.assertRaisesRegex(RuntimeError, '6 GiB'):
                m.preflight(require_storage=True)

    def installation(self, directory):
        base = Path(directory) / 'tools'
        app = base / m.VERSION / 'Ollama.app'
        cli = app / 'Contents/Resources/ollama'
        cli.parent.mkdir(parents=True)
        cli.touch()
        (base / m.VERSION / 'install.json').write_text(json.dumps({'version': m.VERSION, 'sha256': m.ASSET_SHA256}))
        return base, app, cli

    def test_existing_owned_daemon_is_reused_without_signature_or_key_changes(self):
        with patch.object(m, 'owned_state', return_value={'pid': 123}), \
                patch.object(m, 'local_json', return_value={'version': m.VERSION}), \
                patch.object(m, 'verify') as verify, patch.object(m.subprocess, 'Popen') as spawn, patch.object(m, 'emit'):
            m.start(False)
        spawn.assert_not_called()
        verify.assert_not_called()

    def test_first_run_key_creation_is_refused_without_explicit_flag(self):
        with tempfile.TemporaryDirectory() as td:
            base, app, cli = self.installation(td)
            with patch.object(m, 'BASE', base), patch.object(m, 'APP', app), patch.object(m, 'CLI', cli), \
                    patch.object(m, 'owned_state', return_value=None), patch.object(m, 'port_free'), \
                    patch.object(m, 'verify'), patch.object(m.Path, 'home', return_value=Path(td)), \
                    patch.object(m.subprocess, 'Popen') as spawn:
                with self.assertRaisesRegex(RuntimeError, 'allow-initial-keypair'):
                    m.start(False)
                self.assertFalse((Path(td) / '.ollama').exists())
                spawn.assert_not_called()

    def test_start_cancellation_cleans_and_reaps_its_real_owned_child(self):
        original_spawn = subprocess.Popen
        children = []
        def spawn(*args, **kwargs):
            child = original_spawn([sys.executable, '-c', 'import time; time.sleep(30)'],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True)
            children.append(child)
            return child
        with tempfile.TemporaryDirectory() as td:
            base, app, cli = self.installation(td)
            keys = Path(td) / '.ollama'
            keys.mkdir()
            (keys / 'id_ed25519').touch()
            state = base / 'daemon.json'
            with patch.object(m, 'BASE', base), patch.object(m, 'APP', app), patch.object(m, 'CLI', cli), \
                    patch.object(m, 'MODEL_DIR', base / 'models'), patch.object(m, 'STATE', state), \
                    patch.object(m, 'owned_state', return_value=None), patch.object(m, 'port_free'), \
                    patch.object(m, 'verify'), patch.object(m.Path, 'home', return_value=Path(td)), \
                    patch.object(m, 'fingerprint', return_value=(str(cli) + ' serve', 'started')), \
                    patch.object(m, 'local_json', side_effect=KeyboardInterrupt), \
                    patch.object(m.subprocess, 'Popen', side_effect=spawn):
                try:
                    with self.assertRaises(KeyboardInterrupt):
                        m.start(False)
                    self.assertEqual(len(children), 1)
                    self.assertIsNotNone(children[0].poll())
                    self.assertFalse(state.exists())
                finally:
                    for child in children:
                        if child.poll() is None:
                            os.killpg(child.pid, signal.SIGKILL)
                            child.wait(timeout=2)

    def test_download_tls_uses_certifi_roots_when_available(self):
        roots = SimpleNamespace(where=lambda: '/verified/project/cacert.pem')
        with patch.dict(sys.modules, {'certifi': roots}), patch.object(m.ssl, 'create_default_context') as create:
            m.download_tls_context()
        create.assert_called_once_with(cafile='/verified/project/cacert.pem')

    def test_download_tls_stdlib_fallback_still_verifies_peer(self):
        with patch.dict(sys.modules, {'certifi': None}):
            context = m.download_tls_context()
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)

    def test_release_urls_and_redirects(self):
        self.assertTrue(m.safe_https(m.ASSET_URL))
        for url in ("http://github.com/a", "https://github.com.evil/a", "https://github.com@127.0.0.1/a", "https://github.com:8443/a"):
            self.assertFalse(m.safe_https(url))

    def test_private_environment_preserves_home_and_clears_proxies(self):
        with patch.dict(os.environ, {"HOME": "/keep-home", "HTTP_PROXY": "http://unused", "OLLAMA_HOST": "0.0.0.0:11434"}):
            env = m.daemon_env()
        self.assertEqual(env["HOME"], "/keep-home")
        self.assertEqual(env["OLLAMA_HOST"], "127.0.0.1:11435")
        self.assertEqual(env["OLLAMA_NO_CLOUD"], "1")
        self.assertNotIn("HTTP_PROXY", env)

    def test_wall_limit_interrupts_work(self):
        with self.assertRaises(TimeoutError), m.wall_limit(.01):
            time.sleep(.05)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL)[0], 0)

    def test_download_checks_both_hash_and_size(self):
        class Response(io.BytesIO):
            headers = {"Content-Length": "3"}
        class Opener:
            def open(self, *args, **kwargs):
                return Response(b"abc")
        digest = m.hashlib.sha256(b"abc").hexdigest()
        with tempfile.TemporaryDirectory() as td, patch.object(m.urllib.request, "build_opener", return_value=Opener()), patch.object(m, "ASSET_BYTES", 3), patch.object(m, "emit"):
            with patch.object(m, "ASSET_SHA256", digest):
                m.download(Path(td) / "ok.zip")
            with patch.object(m, "ASSET_SHA256", "0" * 64), self.assertRaises(ValueError):
                m.download(Path(td) / "bad.zip")

    def test_zip_traversal_symlink_ancestor_and_escape(self):
        def link(archive, name, target):
            item = zipfile.ZipInfo(name)
            item.create_system = 3
            item.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(item, target)
        with tempfile.TemporaryDirectory() as td:
            for kind in ("good", "traversal", "ancestor", "escape"):
                path = Path(td) / (kind + ".zip")
                with zipfile.ZipFile(path, "w") as archive:
                    if kind == "good":
                        archive.writestr("Ollama.app/Contents/file", "ok")
                        link(archive, "Ollama.app/Contents/current", "file")
                    elif kind == "traversal":
                        archive.writestr("../escape", "bad")
                    elif kind == "ancestor":
                        link(archive, "Ollama.app/a", ".")
                        link(archive, "Ollama.app/a/b", "..")
                    else:
                        link(archive, "Ollama.app/out", "..")
                if kind == "good":
                    m.validate_zip(path)
                else:
                    with self.assertRaises(ValueError):
                        m.validate_zip(path)

    def test_dead_owned_pid_is_stale(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "daemon.json"
            state.write_text(json.dumps({"pid": 123, "pgid": 123, "processStart": "same"}))
            with patch.object(m, "STATE", state), patch.object(m, "fingerprint", return_value=(str(m.CLI) + " serve", "same")), patch.object(m.os, "getpgid", side_effect=ProcessLookupError):
                self.assertIsNone(m.owned_state())


if __name__ == "__main__":
    unittest.main(verbosity=2)
