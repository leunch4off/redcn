"""Ten actual manual requests, bounded telemetry and credential-free archives."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'cli'))
import import_diagnostics as diagnostics


URL = 'https://www.cian.ru/rent/flat/333692113/'


class DiagnosticCampaignTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.clock = [0.0]
        self.wall = [2000000000.0]
        self.manager = self.make_manager()

    def tearDown(self):
        self.manager.close()
        self.temporary.cleanup()

    def make_manager(self, **kwargs):
        return diagnostics.ImportDiagnostics(self.directory,
            clock=lambda: self.clock[0], wall_clock=lambda: self.wall[0],
            environment=lambda: {'python': '3.11.9', 'nodeFound': True, 'nodeVersion': 'v24.4.0'}, **kwargs)

    def advance(self, seconds):
        self.clock[0] += seconds
        self.wall[0] += seconds

    def complete_ten(self, *, failure_at=None):
        self.manager.arm()
        for index in range(10):
            token = self.manager.begin('manual-' + str(index), URL)
            self.assertIsNotNone(token)
            self.manager.record(token, 'Читаем страницу', stage='navigate')
            self.advance(index + 1)
            self.manager.complete(token, 'error' if failure_at == index else 'success',
                error=TimeoutError('Chrome timed out') if failure_at == index else None)
        return self.read_bundle()

    def read_bundle(self):
        content, mime, name = self.manager.bundle_asset()
        self.assertEqual((mime, name), ('application/zip', 'tg.zip'))
        with zipfile.ZipFile(io.BytesIO(content)) as bundle:
            files = {name: bundle.read(name).decode('utf-8') for name in bundle.namelist()}
        return content, files, json.loads(files['manifest.json']), [json.loads(line) for line in files['checks.jsonl'].splitlines()]

    def test_disabled_until_explicitly_armed_and_exact_target_only(self):
        self.assertEqual(self.manager.status()['state'], 'idle')
        self.assertFalse(self.manager.status()['enabled'])
        self.assertIsNone(self.manager.begin('job-before-arm', URL))
        for invalid in (0, 1, 9, 11, True, '10', None):
            with self.assertRaises(ValueError):
                self.manager.arm(invalid)
        state = self.manager.arm()
        self.assertEqual((state['target'], state['reserved'], state['remaining']), (10, 0, 10))
        self.assertTrue(state['enabled'])
        self.assertFalse((self.directory / 'import-diagnostics/tg.zip').exists())

    def test_only_manual_cian_offer_links_reserve_a_check(self):
        self.manager.arm()
        for url in ('https://www.avito.ru/moskva/kvartiry/test',
                    'https://cian.ru.evil.test/rent/flat/123/',
                    'https://evil-cian.ru/rent/flat/123/',
                    'https://user:password@cian.ru/rent/flat/123/',
                    'http://cian.ru/rent/flat/123/',
                    'https://cian.ru:8765/rent/flat/123/',
                    'https://cian.ru/cat.php?deal_type=rent',
                    'https://cian.ru/rent/flat/123/%2Fprivate',
                    'https://cian.ru/rent/flat/' + '1' * 21 + '/',
                    None):
            self.assertIsNone(self.manager.begin('not-eligible', url), url)
        self.assertIsNone(self.manager.begin('hourly-watcher', URL, manual=False))
        self.assertIsNone(self.manager.begin('truthy-not-manual', URL, manual=1))
        self.assertEqual(self.manager.status()['reserved'], 0)
        token = self.manager.begin('actual-manual', 'https://moskva.cian.ru:443/rent/flat/333692113/?token=not-kept#fragment')
        self.assertIsNotNone(token)
        self.assertEqual(self.manager.status()['checks'][0]['url'], URL)

    def test_concurrent_requests_can_reserve_exactly_ten_including_failures_and_cancellation(self):
        self.manager.arm()
        with ThreadPoolExecutor(max_workers=20) as executor:
            tokens = list(executor.map(lambda index: self.manager.begin('job-' + str(index), URL), range(40)))
        tokens = [token for token in tokens if token]
        self.assertEqual(len(tokens), 10)
        state = self.manager.status()
        self.assertEqual((state['state'], state['reserved'], state['inFlight']), ('finishing', 10, 10))
        self.assertFalse(state['enabled'])
        self.assertIsNone(self.manager.begin('eleventh', URL))
        with ThreadPoolExecutor(max_workers=10) as executor:
            list(executor.map(lambda row: self.manager.complete(row[1], ('success', 'error', 'cancelled')[row[0] % 3]), enumerate(tokens)))
        state = self.manager.status()
        self.assertEqual((state['state'], state['completed'], state['inFlight'], state['remaining']), ('complete', 10, 0, 0))
        self.assertTrue(state['bundleReady'])
        _, _, manifest, checks = self.read_bundle()
        self.assertEqual(sum(manifest['outcomes'].values()), 10)
        self.assertEqual(len(checks), 10)
        self.assertFalse(manifest['captureEnabled'])

    def test_begin_and_complete_are_idempotent_and_never_rewrite_finished_bundle(self):
        self.manager.arm()
        token = self.manager.begin('same-job', URL)
        self.assertEqual(self.manager.begin('same-job', URL), token)
        self.manager.complete(token)
        self.manager.complete(token, 'error', error='late duplicate completion')
        self.assertEqual(self.manager.status()['completed'], 1)
        self.assertFalse(self.manager.record(token, 'late event', stage='navigate'))
        for index in range(1, 10):
            self.manager.complete(self.manager.begin('job-' + str(index), URL))
        content = self.manager.bundle_asset()[0]
        path = self.directory / 'import-diagnostics/tg.zip'
        stamp = path.stat().st_mtime_ns
        self.manager.complete(token, 'cancelled')
        self.assertEqual(path.stat().st_mtime_ns, stamp)
        self.assertEqual(self.manager.bundle_asset()[0], content)
        self.assertIsNone(self.manager.begin('job-11', URL))

    def test_sqlite_reservation_limit_is_shared_between_controller_instances(self):
        self.manager.arm()
        other = self.make_manager()
        def reserve(index):
            manager = self.manager if index % 2 else other
            return manager.begin('shared-' + str(index), URL)
        with ThreadPoolExecutor(max_workers=12) as executor:
            tokens = [token for token in executor.map(reserve, range(30)) if token]
        self.assertEqual(len(tokens), 10)
        self.assertEqual(other.status()['reserved'], 10)
        for token in tokens:
            other.complete(token)
        self.assertEqual(self.manager.status()['completed'], 10)
        self.assertTrue(self.manager.status()['bundleReady'])

    def test_tenth_completion_creates_archive_without_stopping_regular_imports(self):
        self.manager.arm()
        for index in range(9):
            self.manager.complete(self.manager.begin('job-' + str(index), URL))
        self.assertFalse(self.manager.status()['bundleReady'])
        with self.assertRaises(ValueError):
            self.manager.bundle_asset()
        token = self.manager.begin('job-9', URL)
        self.assertFalse(self.manager.status()['enabled'])
        self.assertEqual(self.manager.status()['completed'], 9)
        self.manager.complete(token, 'cancelled')
        self.assertTrue(self.manager.status()['bundleReady'])
        # The next ordinary import simply receives no capture token.
        self.assertIsNone(self.manager.begin('normal-import-after-ten', URL))

    def test_restart_recovery_counts_reserved_attempts_once_then_accepts_remaining_checks(self):
        self.manager.arm()
        tokens = [self.manager.begin('before-restart-' + str(index), URL) for index in range(4)]
        self.manager.complete(tokens[0])
        self.advance(12)
        restarted = self.make_manager()
        self.assertEqual(restarted.status()['completed'], 1, 'Construction alone must not interrupt another live controller')
        state = restarted.recover_interrupted()
        self.assertEqual((state['reserved'], state['completed'], state['inFlight']), (4, 4, 0))
        self.assertEqual([row['outcome'] for row in state['checks']], ['success', 'interrupted', 'interrupted', 'interrupted'])
        self.assertEqual(restarted.recover_interrupted()['completed'], 4)
        for index in range(4, 10):
            restarted.complete(restarted.begin('after-restart-' + str(index), URL))
        self.assertTrue(restarted.status()['bundleReady'])
        self.assertEqual(restarted.status()['completed'], 10)

    def test_recovery_of_ten_inflight_attempts_automatically_finalizes_archive(self):
        self.manager.arm()
        for index in range(10):
            self.manager.begin('pending-' + str(index), URL)
        self.advance(8)
        restarted = self.make_manager()
        state = restarted.recover_interrupted()
        self.assertEqual(state['state'], 'complete')
        self.assertTrue(state['bundleReady'])
        content, _, _ = restarted.bundle_asset()
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            manifest = json.loads(archive.read('manifest.json'))
        self.assertEqual(manifest['outcomes'], {'interrupted': 10})

    def test_pause_and_arm_preserve_collected_checks_and_old_archive(self):
        self.manager.arm()
        campaign = self.manager.status()['campaignId']
        token = self.manager.begin('first', URL)
        self.manager.pause()
        self.assertIsNone(self.manager.begin('while-paused', URL))
        self.manager.complete(token)
        self.assertEqual(self.manager.arm()['campaignId'], campaign)
        self.assertEqual(self.manager.status()['completed'], 1)
        self.assertTrue(self.manager.status()['enabled'])
        for index in range(1, 10):
            self.manager.complete(self.manager.begin('job-' + str(index), URL))
        content = self.manager.bundle_asset()[0]
        next_state = self.manager.arm()
        self.assertNotEqual(next_state['campaignId'], campaign)
        self.assertEqual(next_state['completed'], 0)
        self.assertEqual(self.manager.bundle_asset()[0], content)
        self.assertTrue((self.directory / 'import-diagnostics/archives' / (campaign + '.zip')).is_file())

    def test_events_and_final_error_are_bounded_independently(self):
        self.manager.arm()
        token = self.manager.begin('busy', URL)
        for index in range(300):
            self.manager.record(token, 'Прокрутка ' + str(index), stage='scroll', completed=index)
        self.advance(40)
        self.manager.complete(token, 'error', error=TimeoutError('Final timeout despite full event budget'))
        for index in range(1, 10):
            self.manager.complete(self.manager.begin('other-' + str(index), URL))
        _, files, _, checks = self.read_bundle()
        self.assertEqual(checks[0]['eventsStored'], 200)
        self.assertEqual(checks[0]['eventsDropped'], 101)
        self.assertEqual(checks[0]['errorClass'], 'TimeoutError')
        self.assertIn('Final timeout', checks[0]['errorMessage'])
        events = [json.loads(row) for row in files['events.ndjson'].splitlines()]
        self.assertEqual(sum(row['check'] == 1 for row in events), 200)
        self.assertEqual(checks[1]['errorClass'], '')

    def test_stage_section_and_manual_wait_durations_are_observed_not_invented(self):
        self.manager.arm()
        token = self.manager.begin('measured', URL)
        self.advance(2); self.manager.record(token, stage='navigate')
        self.advance(5); self.manager.record(token, stage='scroll', section='gallery')
        self.advance(3); self.manager.record(token, stage='challenge', awaitingUser=True)
        self.advance(8); self.manager.record(token, stage='scroll', section='gallery', awaitingUser=False)
        self.advance(4); self.manager.record(token, stage='scroll', section='description')
        self.advance(2); self.manager.record(token, stage='scroll', section='characteristics')
        self.advance(1); self.manager.record(token, stage='extract')
        self.advance(2); self.manager.complete(token)
        for index in range(1, 10):
            self.manager.complete(self.manager.begin('quick-' + str(index), URL))
        _, _, manifest, checks = self.read_bundle()
        first = checks[0]
        self.assertEqual(first['elapsedSeconds'], 27)
        self.assertEqual(first['stagesSeconds'], {'preflight': 2, 'navigate': 5, 'scroll': 10, 'challenge': 8, 'extract': 2})
        self.assertEqual(first['sectionsSeconds'], {'gallery': 15, 'description': 2, 'characteristics': 1})
        self.assertEqual(manifest['meanSeconds'], 2.7)

    def test_authoritative_progress_and_capture_keep_only_safe_observed_fields(self):
        self.manager.arm()
        token = self.manager.begin('captured', URL)
        self.advance(50)
        self.manager.complete(token, progress={'profile': 'import:cache', 'elapsedSeconds': 50,
            'message': 'COOKIE-DO-NOT-STORE', 'rawHtml': '<html>PRIVATE</html>', 'stages': [
                {'id': 'navigate', 'elapsedSeconds': 30, 'label': 'PRIVATE LABEL', 'state': 'done'},
                {'id': 'scroll', 'elapsedSeconds': 15, 'completed': 3, 'total': 3},
                {'id': 'invented', 'elapsedSeconds': 123}]}, capture={
                'version': 2, 'strategy': 'sections', 'scrolls': 4, 'seconds': 15,
                'activeSeconds': 11, 'completed': True, 'sourceHtml': 'PRIVATE HTML',
                'sections': [{'section': 'gallery', 'seconds': 8, 'reads': 2, 'ready': True,
                              'selector': '#PRIVATE', 'photos': ['PRIVATE PHOTO']},
                             {'section': 'description', 'seconds': 3, 'reads': 1},
                             {'section': 'characteristics', 'seconds': 4, 'reads': 1}]})
        for index in range(1, 10):
            self.manager.complete(self.manager.begin('other-' + str(index), URL))
        _, files, _, checks = self.read_bundle()
        merged = '\n'.join(files.values())
        for forbidden in ('COOKIE-DO-NOT-STORE', 'PRIVATE LABEL', 'PRIVATE HTML', '#PRIVATE', 'PRIVATE PHOTO'):
            self.assertNotIn(forbidden, merged)
        self.assertEqual(checks[0]['progress']['profile'], 'import:cache')
        self.assertEqual(checks[0]['stagesSeconds'], {'navigate': 30, 'scroll': 15})
        self.assertEqual(checks[0]['sectionsSeconds'], {'gallery': 8, 'description': 3, 'characteristics': 4})

    def test_credentials_contacts_and_private_paths_do_not_survive_archive_or_database(self):
        self.manager.arm()
        token = self.manager.begin('privacy', URL + '?access_token=URLTOKEN123')
        messages = [
            'Authorization: Bearer HEADERSECRET123',
            'Cookie: redcn_phone=COOKIESECRET123; csrf=SECONDSECRET',
            '"Cookie": "JSONCOOKIESECRET"',
            'redcn_phone=SHORT_COOKIE_VALUE',
            '"redcn_phone": "JSON_SHORT_COOKIE"',
            'csrf_token=SHORT_CSRF_VALUE session_token="SHORT_SESSION_VALUE"',
            'token=SHORTTOKEN123 password="private multi word password"',
            '"access_token": "JSONSHORTSECRET"',
            'https://user:password@example.test/private/URLSECRET?q=QUERYSECRET',
            'weixin://dl/login?secret=APPSECRET',
            '/Users/private-user/Downloads/private-folder/file.py',
            'C:\\Users\\windows-user\\private-file.py',
            'contact person@example.test +7 (999) 123-45-67 +86 13800138000',
            'opaque abcdefghijklmnopqrstuvwxyz0123456789ZYXWVUT',
            'HTML failure <html><body>PRIVATE FULL PAGE CONTENT</body></html>',
            '"description": "PRIVATE DESCRIPTION CONTENT"',
        ]
        for message in messages:
            self.manager.record(token, message, stage='navigate', rawHtml='SECRET_HTML', headers={'Cookie': 'RAW_HEADER'})
        self.manager.complete(token, 'error', error=ValueError('session=ERRORSESSION url=https://example.test/ERRORTOKEN'))
        for index in range(1, 10):
            self.manager.complete(self.manager.begin('other-' + str(index), URL))
        _, files, _, _ = self.read_bundle()
        all_text = '\n'.join(files.values())
        with sqlite3.connect(self.manager.path) as db:
            all_text += '\n'.join(row[0] for row in db.execute('SELECT payload FROM events'))
        for forbidden in ('HEADERSECRET123', 'COOKIESECRET123', 'SECONDSECRET', 'JSONCOOKIESECRET',
                          'SHORTTOKEN123', 'private multi word password', 'JSONSHORTSECRET',
                          'SHORT_COOKIE_VALUE', 'JSON_SHORT_COOKIE', 'SHORT_CSRF_VALUE', 'SHORT_SESSION_VALUE',
                          'URLSECRET', 'QUERYSECRET', 'APPSECRET', 'private-user', 'windows-user',
                          'person@example.test', '999', '13800138000',
                          'abcdefghijklmnopqrstuvwxyz0123456789ZYXWVUT', 'ERRORSESSION', 'ERRORTOKEN',
                          'SECRET_HTML', 'RAW_HEADER', 'URLTOKEN123', 'PRIVATE FULL PAGE CONTENT', 'PRIVATE DESCRIPTION CONTENT'):
            self.assertNotIn(forbidden, all_text, forbidden)
        self.assertIn(URL, all_text, 'Canonical public source link is deliberately retained')

    def test_partial_section_ends_before_a_long_whole_page_fallback(self):
        self.manager.arm()
        token = self.manager.begin('fallback', URL)
        self.advance(2); self.manager.record(token, stage='scroll', section='characteristics')
        self.advance(2); self.manager.record(token, stage='scroll', section='characteristics', sectionReady=False)
        self.manager.record(token, 'Ограниченная прокрутка всей страницы', stage='scroll')
        self.advance(14); self.manager.record(token, stage='extract')
        self.advance(2); self.manager.complete(token, capture={'strategy': 'sections', 'sectionFallback': True})
        for index in range(1, 10):
            self.manager.complete(self.manager.begin('other-' + str(index), URL))
        _, _, _, checks = self.read_bundle()
        self.assertEqual(checks[0]['sectionsSeconds'], {'characteristics': 2})
        self.assertTrue(checks[0]['capture']['sectionFallback'])
        self.assertEqual(checks[0]['stagesSeconds']['scroll'], 16)

    def test_invalid_unicode_error_is_representable_without_losing_the_attempt(self):
        self.manager.arm()
        token = self.manager.begin('invalid-unicode', URL)
        self.manager.record(token, 'Проверка \ud800 unicode', stage='navigate')
        self.manager.complete(token, 'error', error=ValueError('Ошибка \udfff invalid Unicode'))
        self.assertEqual(self.manager.status()['completed'], 1)
        self.assertEqual(self.manager.status()['checks'][0]['outcome'], 'error')

    def test_invalid_numbers_unknown_fields_and_huge_messages_are_discarded(self):
        self.manager.arm()
        token = self.manager.begin('bounds', URL)
        self.manager.record(token, 'A' * 10000, stage='unknown', completed=True,
            total=-1, seconds=float('nan'), maxTotalSeconds=float('inf'), private='DO_NOT_STORE')
        self.manager.complete(token, progress={'elapsedSeconds': float('nan'), 'stages': [
            {'id': 'navigate', 'elapsedSeconds': -50}, {'id': 'scroll', 'elapsedSeconds': float('inf')}]})
        for index in range(1, 10):
            self.manager.complete(self.manager.begin('other-' + str(index), URL))
        _, files, _, checks = self.read_bundle()
        self.assertNotIn('DO_NOT_STORE', '\n'.join(files.values()))
        self.assertNotIn('NaN', '\n'.join(files.values()))
        first_event = [json.loads(row) for row in files['events.ndjson'].splitlines()][1]
        self.assertLessEqual(len(first_event['message']), 320)
        self.assertNotIn('stage', first_event)
        self.assertNotIn('completed', first_event)
        self.assertNotIn('seconds', first_event)
        self.assertNotIn('elapsedSeconds', checks[0]['progress'])

    def test_bundle_write_failure_does_not_break_completion_and_can_be_retried(self):
        self.manager.arm()
        for index in range(9):
            self.manager.complete(self.manager.begin('job-' + str(index), URL))
        token = self.manager.begin('job-9', URL)
        with patch.object(diagnostics, '_atomic_write', side_effect=OSError('PRIVATE INTERNAL PATH')):
            state = self.manager.complete(token)
        self.assertEqual(state['state'], 'complete')
        self.assertEqual(state['completed'], 10)
        self.assertEqual(state['bundleError'], 'OSError')
        self.assertFalse(state['bundleReady'])
        self.assertTrue(self.manager.bundle_asset()[0].startswith(b'PK'))
        self.assertIsNone(self.manager.status()['bundleError'])

    def test_bundle_integrity_and_symlinks_are_checked_before_download(self):
        content, _, _, _ = self.complete_ten()
        campaign = self.manager.status()['campaignId']
        path = self.directory / 'import-diagnostics/archives' / (campaign + '.zip')
        path.write_bytes(content + b'tampered')
        with self.assertRaisesRegex(ValueError, 'повреждён'):
            self.manager.bundle_asset()
        path.unlink()
        target = self.directory / 'unrelated.txt'; target.write_text('KEEP-ME')
        path.symlink_to(target)
        with self.assertRaisesRegex(ValueError, 'Недопустимый'):
            self.manager.bundle_asset()
        self.assertEqual(target.read_text(), 'KEEP-ME')

    def test_database_and_directory_symlinks_never_redirect_diagnostic_writes(self):
        target = self.directory / 'unrelated'; target.mkdir()
        for kind in ('database', 'directory'):
            with tempfile.TemporaryDirectory() as cache:
                root = Path(cache); folder = root / 'import-diagnostics'
                if kind == 'directory':
                    folder.symlink_to(target, target_is_directory=True)
                    with self.assertRaises(ValueError):
                        diagnostics.ImportDiagnostics(root, environment=lambda: {})
                else:
                    manager = diagnostics.ImportDiagnostics(root, environment=lambda: {})
                    manager.path.unlink(); manager.path.symlink_to(target / 'never-created.sqlite3')
                    with self.assertRaises(ValueError):
                        manager.arm()
        self.assertEqual(list(target.iterdir()), [])

    def test_storage_remains_private_and_zip_names_are_fixed(self):
        content, files, _, _ = self.complete_ten()
        self.assertLess(len(content), diagnostics.MAX_BUNDLE_BYTES)
        self.assertEqual(set(files), {'manifest.json', 'checks.jsonl', 'events.ndjson', 'environment.json', 'summary.txt'})
        for name in files:
            self.assertNotIn('/', name)
            self.assertNotIn('..', name)
        for folder in (self.manager.folder, self.manager.archives):
            self.assertEqual(folder.stat().st_mode & 0o777, 0o700)
        for path in (self.manager.path, self.manager.folder / 'tg.zip'):
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(hashlib.sha256(content).hexdigest(), hashlib.sha256((self.manager.folder / 'tg.zip').read_bytes()).hexdigest())

    def test_explicit_rearm_preserves_previous_bundle_and_has_bounded_history(self):
        self.complete_ten()
        with patch.object(diagnostics, 'MAX_CAMPAIGNS', 1):
            with self.assertRaisesRegex(ValueError, '20 диагностических'):
                self.manager.arm()
        self.assertTrue(self.manager.status()['bundleReady'])

    def test_close_stops_new_capture_without_interrupting_running_worker(self):
        self.manager.arm()
        token = self.manager.begin('running', URL)
        self.manager.close()
        self.assertFalse(self.manager.status()['enabled'])
        self.assertIsNone(self.manager.begin('new', URL))
        self.assertTrue(self.manager.record(token, 'Завершаем', stage='save'))
        self.manager.complete(token)
        self.assertEqual(self.manager.status()['completed'], 1)


if __name__ == '__main__':
    unittest.main()
