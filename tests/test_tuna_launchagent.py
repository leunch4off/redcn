import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import plistlib
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

PATH = Path(__file__).resolve().parents[1]/'scripts/install-tuna-launchagent.py'
SPEC = importlib.util.spec_from_file_location('tuna_launchagent_installer', PATH)
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


class TunaLaunchAgentTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.home = Path(self.folder.name).resolve()
        self.binary = self.home/'tools with spaces/tuna'
        self.binary.parent.mkdir()
        self.binary.write_bytes(b'fixture executable; never run')
        self.binary.chmod(0o700)
        self.agent = m.prepare(self.home, binary=self.binary)

    def native_install(self):
        with patch.object(m.platform, 'system', return_value='Darwin'), \
                patch.object(m, 'native_lint', return_value=True), \
                patch.object(m, 'service_inventory', return_value=self.clear_inventory()):
            return m.install(self.agent)

    def clear_inventory(self):
        return {'queryState': 'available', 'decision': 'clear',
            'registeredServiceCount': 0, 'existingService': None}

    def registered(self, **changes):
        return {'Name': m.SERVICE_NAME, 'Scope': 1, 'State': 'running', 'PID': 1445,
            'Exe': str(self.binary), 'Program': 'tuna', 'User': '',
            'Args': ['http', m.UPSTREAM, '--domain='+m.DOMAIN, '--inspect=false',
                '--https-redirect', '--log', 'stdout', '--service', m.SERVICE_NAME],
            'File': 'SECRET_FILE_NOT_TO_PRINT', 'Log': 'SECRET_LOG_NOT_TO_PRINT', **changes}

    def registry_runner(self, rows=None, **changes):
        result = SimpleNamespace(returncode=0, stdout=json.dumps([] if rows is None else rows), **changes)
        return lambda args, **kwargs: result

    def test_plist_has_only_literal_phone_args_and_private_output_settings(self):
        document = plistlib.loads(self.agent['payload'])
        self.assertEqual(document, self.agent['document'])
        self.assertEqual(document['ProgramArguments'], [str(self.binary), 'http', '127.0.0.1:8767',
            '--domain=free-quail-5273.a.tuna.free', '--inspect=false', '--https-redirect', '--log=stdout'])
        self.assertTrue(document['RunAtLoad'])
        self.assertTrue(document['KeepAlive'])
        self.assertEqual(document['Umask'], 0o077)
        self.assertEqual(document['ThrottleInterval'], 30)
        for key in ('StandardInPath', 'StandardOutPath', 'StandardErrorPath'):
            self.assertEqual(document[key], '/dev/null')
        self.assertNotIn('EnvironmentVariables', document)
        self.assertNotIn('8765', str(document))
        self.assertEqual(document['WorkingDirectory'], str(self.home/'.cache/redcn/tuna-launchagent'))

    def test_other_origins_credentials_queries_shell_fragments_and_desktop_port_are_rejected(self):
        for origin in ('http://'+m.DOMAIN, 'https://user:password@'+m.DOMAIN,
                m.ORIGIN+'?token=secret', m.ORIGIN+'#pairing', 'https://different.tuna.am',
                m.ORIGIN+'/phone/', m.ORIGIN+';open -a Terminal'):
            with self.assertRaises(ValueError):
                m.prepare(self.home, binary=self.binary, origin=origin)
        for upstream in ('127.0.0.1:8765', '0.0.0.0:8767', 'localhost:8767', '8767', 'https://127.0.0.1:8767'):
            with self.assertRaises(ValueError):
                m.prepare(self.home, binary=self.binary, upstream=upstream)

    def test_default_dry_run_writes_nothing_and_never_starts_a_process(self):
        stream = io.StringIO()
        with patch.object(m.platform, 'system', return_value='Linux'), \
                patch.object(m.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='[]')) as run, \
                patch.object(m.subprocess, 'Popen') as spawn, \
                contextlib.redirect_stdout(stream):
            self.assertEqual(m.main(['--home', str(self.home), '--binary', str(self.binary)]), 0)
        result = json.loads(stream.getvalue())
        self.assertEqual(result['mode'], 'dry-run')
        self.assertFalse(result['started'])
        self.assertFalse(result['installed'])
        self.assertFalse(result['paidPlanRequired'])
        self.assertFalse((self.home/'Library').exists())
        self.assertFalse((self.home/'.cache').exists())
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0], [str(self.binary), 'service', 'list', '--json'])
        self.assertNotIn('shell', run.call_args.kwargs)
        spawn.assert_not_called()

    def test_registered_official_service_is_a_noop_and_creates_no_competing_plist(self):
        with patch.object(m.platform, 'system', return_value='Darwin'), \
                patch.object(m.subprocess, 'run', side_effect=self.registry_runner([self.registered()])), \
                patch.object(m.subprocess, 'Popen') as spawn, patch.object(m.os, 'kill') as kill:
            result = m.install(self.agent)
        self.assertEqual(result['reason'], 'already-managed')
        self.assertFalse(result['installed'])
        self.assertFalse(result['started'])
        self.assertFalse((self.home/'Library').exists())
        self.assertFalse((self.home/'.cache').exists())
        self.assertEqual(result['serviceInventory']['existingService']['name'], 'redcn-phone')
        self.assertNotIn('SECRET_', json.dumps(result))
        self.assertNotIn('Args', json.dumps(result))
        spawn.assert_not_called()
        kill.assert_not_called()

    def test_unavailable_malformed_or_unknown_registry_blocks_installation(self):
        cases = [self.registry_runner([self.registered(Name='unknown')]),
            self.registry_runner([self.registered(Exe='/usr/bin/other')]),
            self.registry_runner([self.registered(Args=['http', '127.0.0.1:8765', '--domain='+m.DOMAIN])]),
            self.registry_runner([self.registered(Args=['http', m.UPSTREAM, '--domain=other.example'])]),
            self.registry_runner([self.registered(Args=['http', m.UPSTREAM, '--domain='+m.DOMAIN, '--service', 'other'])]),
            self.registry_runner([self.registered(PID=0)]),
            self.registry_runner([self.registered(Scope=2)]),
            self.registry_runner([self.registered(State='unknown')]),
            self.registry_runner([self.registered(), self.registered()]),
            lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout='SECRET_STDOUT'),
            lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout='not-json SECRET_STDOUT'),
            lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout='{"Args":"SECRET_STDOUT"}')]
        for runner in cases:
            with self.subTest(runner=runner), patch.object(m.platform, 'system', return_value='Darwin'), \
                    patch.object(m.subprocess, 'run', side_effect=runner):
                with self.assertRaisesRegex(ValueError, 'конкурирующего'):
                    m.install(self.agent)
            self.assertFalse((self.home/'Library').exists())
            self.assertFalse((self.home/'.cache').exists())

    def test_empty_successful_cli_registry_allows_fresh_install(self):
        with patch.object(m.platform, 'system', return_value='Darwin'), \
                patch.object(m, 'native_lint', return_value=True), \
                patch.object(m.subprocess, 'run', side_effect=self.registry_runner([])) as run:
            result = m.install(self.agent)
        self.assertTrue(result['installed'])
        self.assertFalse(result['started'])
        self.assertEqual(run.call_args.args[0], [str(self.binary), 'service', 'list', '--json'])

    def test_internal_service_flag_requires_the_exact_verified_registration_and_pid(self):
        arguments = shlex_quote(str(self.binary))+' http '+m.UPSTREAM+' --domain='+m.DOMAIN+' --service redcn-phone'
        inventory = m.service_inventory(self.binary, runner=self.registry_runner([self.registered()]))
        self.assertEqual(inventory['decision'], 'already-managed')
        registered = inventory['existingService']
        self.assertTrue(m.process_match(1445, binary=self.binary, runner=self.metadata(arguments),
            registered_service=registered)['exactKnownTunnelMatch'])
        for proof in (None, {'name': 'unknown', 'pid': 1445}, {'name': 'redcn-phone', 'pid': 1234}):
            self.assertFalse(m.process_match(1445, binary=self.binary, runner=self.metadata(arguments),
                registered_service=proof)['exactKnownTunnelMatch'])

    def test_dry_run_managed_service_has_no_activation_commands_or_install_permission(self):
        stream = io.StringIO()
        with patch.object(m.platform, 'system', return_value='Linux'), \
                patch.object(m.subprocess, 'run', side_effect=self.registry_runner([self.registered()])), \
                contextlib.redirect_stdout(stream):
            self.assertEqual(m.main(['--home', str(self.home), '--binary', str(self.binary)]), 0)
        result = json.loads(stream.getvalue())
        self.assertFalse(result['canInstallWithoutOverwrite'])
        self.assertEqual(result['serviceInventory']['decision'], 'already-managed')
        self.assertEqual(result['laterCommands'], [])
        self.assertFalse((self.home/'Library').exists())

    def test_service_query_failure_never_leaks_stderr_or_exception_details(self):
        def unavailable(*_args, **_kwargs):
            raise OSError('SECRET_INTERNAL_ERROR')
        result = m.service_inventory(self.binary, runner=unavailable)
        self.assertEqual(result['decision'], 'blocked')
        self.assertEqual(result['queryState'], 'unavailable')
        self.assertNotIn('SECRET_', json.dumps(result))

    def test_install_only_creates_private_directories_and_new_known_plist(self):
        with patch.object(m.subprocess, 'Popen') as spawn, patch.object(m.os, 'kill') as kill:
            result = self.native_install()
        self.assertTrue(result['installed'])
        self.assertFalse(result['started'])
        self.assertEqual(plistlib.loads(self.agent['path'].read_bytes()), self.agent['document'])
        self.assertEqual(stat.S_IMODE(self.agent['path'].stat().st_mode), 0o600)
        for path in (self.home/'Library', self.home/'Library/LaunchAgents', self.home/'.cache',
                self.home/'.cache/redcn', self.agent['working']):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700)
        self.assertEqual(list(self.agent['path'].parent.iterdir()), [self.agent['path']])
        spawn.assert_not_called()
        kill.assert_not_called()

    def test_existing_shared_launchagents_permissions_are_not_rewritten(self):
        parent = self.agent['path'].parent
        parent.mkdir(parents=True)
        parent.chmod(0o755)
        self.native_install()
        self.assertEqual(stat.S_IMODE(parent.stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE(self.agent['working'].stat().st_mode), 0o700)

    def test_existing_plist_is_never_read_or_overwritten(self):
        self.agent['path'].parent.mkdir(parents=True)
        marker = b'fixture secret: must stay untouched'
        self.agent['path'].write_bytes(marker)
        with patch.object(Path, 'read_bytes', side_effect=AssertionError('Existing plist must not be read')):
            with self.assertRaisesRegex(ValueError, 'уже существует'):
                self.native_install()
        self.assertEqual(self.agent['path'].read_bytes(), marker)
        self.assertFalse(self.agent['working'].exists())

    def test_symlink_parent_and_existing_target_are_refused_without_following(self):
        outside = self.home/'outside'
        outside.mkdir()
        (self.home/'Library').mkdir()
        (self.home/'Library/LaunchAgents').symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'ссылок'):
            self.native_install()
        self.assertEqual(list(outside.iterdir()), [])

    def test_private_working_directory_with_public_permissions_is_refused(self):
        self.agent['working'].mkdir(parents=True)
        self.agent['working'].chmod(0o755)
        with self.assertRaisesRegex(ValueError, '0700'):
            self.native_install()
        self.assertFalse(self.agent['path'].exists())

    def test_creation_race_does_not_replace_target_and_cleans_only_own_temporary(self):
        def appeared(source, destination, **_kwargs):
            Path(destination).write_bytes(b'concurrent fixture file')
            raise FileExistsError('fixture race')
        with patch.object(m.os, 'link', side_effect=appeared):
            with self.assertRaises(FileExistsError):
                self.native_install()
        self.assertEqual(self.agent['path'].read_bytes(), b'concurrent fixture file')
        self.assertEqual(list(self.agent['path'].parent.iterdir()), [self.agent['path']])

    def test_native_lint_uses_stdin_and_no_shell_or_tuna_execution(self):
        with patch.object(m.platform, 'system', return_value='Darwin'), \
                patch.object(m.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
            self.assertTrue(m.native_lint(self.agent['payload']))
        self.assertEqual(run.call_args.args[0], ['/usr/bin/plutil', '-lint', '-'])
        self.assertEqual(run.call_args.kwargs['input'], self.agent['payload'])
        self.assertNotIn('shell', run.call_args.kwargs)

    def metadata(self, arguments, *, uid=None, executable=None):
        fields = {'uid=': str(os.getuid() if uid is None else uid),
            'comm=': executable or str(self.binary), 'args=': arguments}
        return lambda args, **kwargs: SimpleNamespace(returncode=0, stdout=fields[args[-1]])

    def test_readonly_process_summary_requires_exact_binary_owner_domain_and_explicit_upstream(self):
        arguments = shlex_quote(str(self.binary))+' http '+m.UPSTREAM+' --domain='+m.DOMAIN+' --inspect=true'
        result = m.process_match(1445, binary=self.binary, runner=self.metadata(arguments))
        self.assertTrue(result['exactKnownTunnelMatch'])
        planned = ' '.join(shlex_quote(value) for value in self.agent['document']['ProgramArguments'])
        self.assertTrue(m.process_match(1445, binary=self.binary, runner=self.metadata(planned))['exactKnownTunnelMatch'])
        self.assertEqual(set(result), {'pid', 'ownUser', 'executableMatches', 'domainMatches',
            'explicitPhoneUpstream', 'exactKnownTunnelMatch', 'reason', 'unrecognizedOptionNames', 'unrecognizedPositionalPresent'})
        for runner in (self.metadata(arguments, uid=os.getuid()+1),
                self.metadata(arguments, executable='/usr/bin/other'),
                self.metadata(arguments.replace(m.UPSTREAM, '127.0.0.1:8765'))):
            self.assertFalse(m.process_match(1445, binary=self.binary, runner=runner)['exactKnownTunnelMatch'])

    def test_bare_upstream_and_secret_or_unrecognized_arguments_never_authorize_handoff_or_get_printed(self):
        bare = shlex_quote(str(self.binary))+' http --domain='+m.DOMAIN
        result = m.process_match(1445, binary=self.binary, runner=self.metadata(bare))
        self.assertFalse(result['exactKnownTunnelMatch'])
        self.assertEqual(result['unrecognizedOptionNames'], [])
        self.assertFalse(result['explicitPhoneUpstream'])
        self.assertEqual(result['reason'], 'upstream-not-explicit')
        secret = bare+' '+m.UPSTREAM+' --token=DO_NOT_RETURN_THIS'
        result = m.process_match(1445, binary=self.binary, runner=self.metadata(secret))
        self.assertFalse(result['exactKnownTunnelMatch'])
        self.assertEqual(result['unrecognizedOptionNames'], ['--token'])
        self.assertNotIn('DO_NOT_RETURN_THIS', json.dumps(result))
        for pid in (0, 1, -1, True):
            with self.assertRaises(ValueError):
                m.process_match(pid, binary=self.binary)


def shlex_quote(value):
    import shlex
    return shlex.quote(value)


if __name__ == '__main__':
    unittest.main()
