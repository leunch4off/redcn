import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'cli'))
import acceptance_evidence as evidence
import access_roles
import test_card_api as fixture


def report(task='RCN-225'):
    checks = {key: 'not-run' for key in evidence.TASKS[task]['checks']}
    checks[next(iter(checks))] = 'pass'
    value = {'action': 'record', 'eventId': 'observed-000000001', 'taskId': task,
             'device': {'model': 'iPhone 14 Pro', 'browser': 'Safari', 'os': 'iOS', 'network': 'unknown'},
             'client': {'origin': 'https://fixture.tuna.free', 'userAgent': 'Synthetic test agent',
                        'standalone': False, 'secureContext': True, 'viewportWidth': 393, 'viewportHeight': 852,
                        'visualViewportHeight': 500, 'reducedViewportObserved': True,
                        'shareAvailable': True, 'fileShareAvailable': True},
             'checks': checks, 'notes': 'Synthetic fixture for storage semantics, not a physical-device acceptance.'}
    if task == 'RCN-262':
        value['reviewer'] = {'name': 'Test reviewer', 'relationship': 'unknown', 'nativeSpeakerReported': False}
    return value


class AcceptanceEvidenceTests(unittest.TestCase):
    def test_readonly_empty_overview_does_not_create_cache_or_mark_any_task_done(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)/'new'
            value = evidence.overview(directory)
            self.assertEqual(value['records'], [])
            self.assertFalse(value['roadmapChanged'])
            self.assertEqual(set(value['tasks']), {'RCN-170', 'RCN-224', 'RCN-225', 'RCN-247', 'RCN-262'})
            self.assertFalse(directory.exists())

    def test_append_idempotency_scoped_actor_and_conflicting_retry_preserve_first_record(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            first = evidence.record(directory, report(), actor='phone:first')
            again = evidence.record(directory, report(), actor='phone:first')
            self.assertEqual(first['records'], again['records'])
            changed = report(); changed['notes'] += ' Changed.'
            with self.assertRaises(evidence.EvidenceConflict):
                evidence.record(directory, changed, actor='phone:first')
            second = evidence.record(directory, report(), actor='phone:second')
            self.assertEqual(second['total'], 2)
            self.assertEqual(len({row['actorHash'] for row in second['records']}), 2)
            self.assertNotIn('phone:first', json.dumps(second))
            self.assertEqual((directory/'acceptance-evidence/records.sqlite3').stat().st_mode & 0o777, 0o600)

    def test_same_origin_after_report_does_not_certify_reboot_iphone_or_native_reviewer(self):
        with tempfile.TemporaryDirectory() as root:
            for task in evidence.TASKS:
                data = report(task)
                data['eventId'] = 'same-event-'+task
                data['checks'] = {key: 'pass' for key in data['checks']}
                if task == 'RCN-224':
                    data['device']['network'] = 'cellular'
                if task == 'RCN-262':
                    data['reviewer'].update(relationship='independent-person', nativeSpeakerReported=True)
                value = evidence.record(root, data)
                saved = value['records'][0]
                self.assertEqual(saved['source'], 'authenticated-operator-report')
                self.assertEqual(saved['verification'], 'operator-reported')
                self.assertFalse(saved['roadmapChanged'])
                self.assertNotIn('done', saved)
            self.assertEqual(evidence.overview(root)['total'], len(evidence.TASKS))

    def test_unknown_fields_sensitive_urls_all_unrun_and_wrong_scalar_types_are_rejected(self):
        mutations = [lambda v: v.update(status='done'), lambda v: v.update(taskId='RCN-334'),
                     lambda v: v['client'].update(origin='https://user:password@fixture.tuna.free'),
                     lambda v: v['client'].update(origin='https://fixture.tuna.free/phone/pair#secret'),
                     lambda v: v['client'].update(standalone='yes'),
                     lambda v: v['client'].update(viewportWidth=True),
                     lambda v: v['client'].update(visualViewportHeight=float('nan')),
                     lambda v: v['device'].update(network='5g-guessed'),
                     lambda v: v.update(checks={k: 'not-run' for k in v['checks']}),
                     lambda v: v['checks'].update(added='pass'),
                     lambda v: v.update(notes=''), lambda v: v.update(eventId='../escape')]
        for mutate in mutations:
            with self.subTest(mutation=mutate), tempfile.TemporaryDirectory() as root:
                value = report(); mutate(value)
                with self.assertRaises(ValueError):
                    evidence.record(root, value)
                self.assertEqual(list(Path(root).iterdir()), [])

    def test_language_review_requires_explicit_reviewer_context_but_does_not_infer_native_from_han(self):
        value = report('RCN-262')
        value.pop('reviewer')
        with self.assertRaises(ValueError):
            evidence.validate_report(value)
        value = report('RCN-262')
        value['reviewer']['name'] = '中文姓名'
        normalized = evidence.validate_report(value)
        self.assertFalse(normalized['reviewer']['nativeSpeakerReported'])
        value['reviewer']['nativeSpeakerReported'] = 'true'
        with self.assertRaises(ValueError):
            evidence.validate_report(value)
        value = report('RCN-262'); value['checks']['independent_reviewer'] = 'pass'
        with self.assertRaises(ValueError):
            evidence.validate_report(value)
        value = report('RCN-224'); value['checks']['cellular_access'] = 'pass'
        with self.assertRaises(ValueError):
            evidence.validate_report(value)

    def test_payload_tamper_is_detected_without_rewriting_the_database(self):
        with tempfile.TemporaryDirectory() as root:
            evidence.record(root, report())
            path = Path(root)/'acceptance-evidence/records.sqlite3'
            with sqlite3.connect(path) as db:
                db.execute("UPDATE reports SET payload='{}'")
            before = path.read_bytes()
            with self.assertRaisesRegex(ValueError, 'целостность'):
                evidence.overview(root)
            self.assertEqual(path.read_bytes(), before)

    def test_full_export_contains_history_beyond_screen_limit_and_is_not_new_device_proof(self):
        with tempfile.TemporaryDirectory() as root:
            for index in range(31):
                value = report(); value['eventId'] = 'observation-'+str(index).zfill(12)
                evidence.record(root, value)
            self.assertEqual(evidence.overview(root)['total'], 31)
            self.assertEqual(len(evidence.overview(root)['records']), 30)
            path = Path(root)/'acceptance-evidence/records.sqlite3'
            before = path.read_bytes()
            status, value, _, _ = evidence.dispatch(root, 'acceptance-evidence', {'action': 'export'})
            self.assertEqual(status, 200)
            self.assertEqual(len(value['records']), 31)
            self.assertTrue(value['historicalOnlyOnAnotherWorkspace'])
            self.assertFalse(value['roadmapChanged'])
            self.assertEqual(path.read_bytes(), before)

    def test_workspaces_are_separate_and_symlink_destinations_are_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            one, two = Path(root)/'one', Path(root)/'two'
            evidence.record(one, report())
            self.assertEqual(evidence.overview(two)['total'], 0)
            two.mkdir(); (two/'acceptance-evidence').symlink_to(one/'acceptance-evidence', target_is_directory=True)
            with self.assertRaises(ValueError):
                evidence.record(two, report())
            self.assertEqual(evidence.overview(one)['total'], 1)

    def test_route_contract_and_viewer_denial_do_not_expose_a_public_acceptance_switch(self):
        self.assertIsNone(evidence.dispatch(None, 'some-other-route'))
        for role in ('editor', 'owner'):
            for method in ('GET', 'POST'):
                self.assertTrue(access_roles.phone_route_allowed(role, method, 'acceptance-evidence'))
        for method in ('GET', 'POST'):
            self.assertFalse(access_roles.phone_route_allowed('viewer', method, 'acceptance-evidence'))


class AcceptanceEvidenceHTTP(unittest.TestCase):
    setUp = fixture.CardDocumentRoutes.setUp
    tearDown = fixture.CardDocumentRoutes.tearDown
    request = fixture.CardDocumentRoutes.request

    def test_records_require_session_csrf_and_preserve_cards(self):
        before = (self.directory/'listings.sqlite3').read_bytes()
        self.assertEqual(self.request('acceptance-evidence', auth=False)[0], 401)
        self.assertEqual(self.request('acceptance-evidence', report(), csrf=False)[0], 403)
        status, body, _ = self.request('acceptance-evidence', report())
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body)['total'], 1)
        self.assertFalse(json.loads(body)['roadmapChanged'])
        self.assertEqual((self.directory/'listings.sqlite3').read_bytes(), before)
        self.assertEqual(self.request('acceptance-evidence', {'action': 'complete-all'})[0], 400)
        saved = self.phone.pairing.list_sessions()
        self.phone.pairing.manage_session(saved['sessions'][0]['id'], role='viewer', expected_revision=saved['revision'])
        self.assertEqual(self.request('acceptance-evidence')[0], 403)


if __name__ == '__main__':
    unittest.main()
