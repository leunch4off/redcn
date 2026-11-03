"""Live text progress, creator isolation and cancellation at the save boundary."""
import http.cookiejar
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'cli'))
import individual_copy
import language_service
import local_server
from text_generation_jobs import TextGenerationJobs, TextJobError, dispatch
import test_phone_relay as phone_fixture


PREVIEW = {'action': 'preview', 'fields': {'price': '85000', 'sourceDescription': 'PRIVATE SOURCE'},
    'output': {'title': '旧标题', 'body': '旧正文'}, 'part': 'body', 'requestId': 'request_text_001'}
DRAFT = {'status': 'review', 'modelAccepted': True, 'fallback': False,
    # Synthetic accepted-response contract. These tests exercise auth, CAS and
    # cancellation around the real save; they do not measure model quality.
    'generationMode': 'individual-model-prose', 'textSource': 'model',
    'modelContribution': 'full-publication-prose',
    'factualTextSource': 'checked-listing-and-accepted-photo-evidence',
    'title': '新标题', 'body': '新的中文房源正文', 'tags': '#莫斯科'}


def terminal(manager, job_id, principal):
    deadline = time.monotonic()+3
    while time.monotonic() < deadline:
        value = manager.status(job_id, principal)
        if value['state'] != 'running':
            return value
        threading.Event().wait(.005)
    raise AssertionError('Text worker did not finish')


class TextJobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.directory = Path(self.temp.name)
        self.manager = TextGenerationJobs(self.directory)

    def tearDown(self):
        self.manager.close(); self.temp.cleanup()

    def start(self, payload=PREVIEW, **kwargs):
        return self.manager.start(payload, principal='desktop:owner', authorize=lambda: None, **kwargs)['job_id']

    def card(self):
        return local_server.save_result(self.directory, {'fields': {'price': '85000', 'photos': []},
            'titles': ['手工标题'], 'body': '手工正文', 'tags': '#手工'})

    def test_running_status_measures_tokens_and_excludes_requests_or_callback_secrets(self):
        entered, release = threading.Event(), threading.Event()
        def preview(_directory, _data, *, status, cancel):
            status('PRIVATE CALLBACK https://private.test', stage='generate',
                ai={'backend': 'mlx', 'operation': 'text', 'phase': 'generating', 'tokenBased': True,
                    'generatedTokens': 37, 'tokensPerSecond': 12.5, 'prompt': 'PRIVATE PROMPT', 'path': '/private/model'})
            entered.set(); release.wait(2)
            return {**DRAFT, 'rawText': 'PRIVATE RAW', 'fields': {'secret': 'PRIVATE FIELDS'}}
        with patch.object(individual_copy, 'generate_preview', side_effect=preview):
            job_id = self.start(); self.assertTrue(entered.wait(1))
            value = self.manager.status(job_id, 'desktop:owner')
            self.assertEqual(value['state'], 'running'); self.assertTrue(value['canCancel'])
            self.assertEqual(value['progress']['ai']['generatedTokens'], 37)
            self.assertEqual(value['progress']['ai']['tokensPerSecond'], 12.5)
            self.assertEqual([stage['id'] for stage in value['progress']['stages']], ['generate'])
            self.assertNotIn('PRIVATE', json.dumps(value)); self.assertNotIn('principal', value)
            for other in ('phone:another', 'desktop:other'):
                with self.assertRaises(TextJobError) as caught: self.manager.status(job_id, other)
                self.assertEqual(caught.exception.status, 404)
                with self.assertRaises(TextJobError): self.manager.cancel(job_id, other)
            release.set(); result = terminal(self.manager, job_id, 'desktop:owner')
        self.assertEqual(result['resultType'], 'draft'); self.assertEqual(result['result']['body'], DRAFT['body'])
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.assertFalse(result['canCancel'])

    def test_cancel_sets_real_event_and_drops_draft(self):
        entered, observed = threading.Event(), threading.Event()
        def preview(_directory, _data, *, status, cancel):
            entered.set()
            if cancel.wait(2): observed.set()
            return DRAFT
        with patch.object(individual_copy, 'generate_preview', side_effect=preview):
            job_id = self.start(); self.assertTrue(entered.wait(1))
            cancelled = self.manager.cancel(job_id, 'desktop:owner')
            self.assertTrue(cancelled['cancelRequested']); self.assertFalse(cancelled['canCancel'])
            self.assertTrue(observed.wait(1)); value = terminal(self.manager, job_id, 'desktop:owner')
        self.assertEqual(value['state'], 'cancelled'); self.assertNotIn('result', value)

    def test_replay_conflicts_and_bounded_records_with_expiry(self):
        entered, release = threading.Event(), threading.Event()
        def preview(*_args, **_kwargs): entered.set(); release.wait(2); return DRAFT
        now = [100.0]
        self.manager.close(); self.manager = TextGenerationJobs(self.directory, limit=2, ttl=10, clock=lambda: now[0])
        with patch.object(individual_copy, 'generate_preview', side_effect=preview):
            job_id = self.start(); self.assertTrue(entered.wait(1))
            replay = self.manager.start(PREVIEW, principal='desktop:owner', authorize=lambda: None)
            self.assertEqual(replay['job_id'], job_id); self.assertTrue(replay['replayed'])
            with self.assertRaises(TextJobError) as caught: self.start({**PREVIEW, 'part': 'title'})
            self.assertEqual(caught.exception.code, 'TEXT_JOB_REQUEST_CONFLICT')
            with self.assertRaises(TextJobError) as caught: self.start({**PREVIEW, 'requestId': 'request_text_002'})
            self.assertEqual(caught.exception.code, 'TEXT_JOB_BUSY')
            release.set(); terminal(self.manager, job_id, 'desktop:owner')
        with patch.object(individual_copy, 'generate_preview', return_value=DRAFT):
            for number in range(2):
                newer = self.start({**PREVIEW, 'requestId': 'request_next_'+str(number)})
                terminal(self.manager, newer, 'desktop:owner'); now[0] += 1
        self.assertLessEqual(len(self.manager.jobs), 2)
        with self.assertRaises(TextJobError): self.manager.status(job_id, 'desktop:owner')
        now[0] += 11
        with self.assertRaises(TextJobError): self.manager.status(newer, 'desktop:owner')
        self.assertEqual(self.manager.jobs, {}); self.assertEqual(self.manager.requests, {})

    def test_cancel_before_actual_rewrite_save_preserves_card(self):
        card_id = self.card(); original = local_server.read_result(self.directory, card_id)
        entered = threading.Event()
        def preview(_directory, _data, *, status, cancel):
            entered.set(); cancel.wait(2); return DRAFT
        with patch.object(individual_copy, 'generate_preview', side_effect=preview):
            job_id = self.start({'action': 'rewrite', 'cardId': card_id, 'part': 'body'})
            self.assertTrue(entered.wait(1)); self.manager.cancel(job_id, 'desktop:owner')
            value = terminal(self.manager, job_id, 'desktop:owner')
        self.assertEqual(value['state'], 'cancelled')
        self.assertEqual(local_server.read_result(self.directory, card_id), original)

    def test_revoked_authorization_and_concurrent_edit_are_checked_before_save(self):
        for mode in ('revoke', 'edit'):
            with self.subTest(mode=mode):
                card_id = self.card(); entered, release = threading.Event(), threading.Event(); allowed = [True]
                def authorize():
                    if not allowed[0]: raise PermissionError('PRIVATE AUTH ERROR')
                def preview(*_args, **_kwargs): entered.set(); release.wait(2); return DRAFT
                with patch.object(individual_copy, 'generate_preview', side_effect=preview):
                    job_id = self.manager.start({'action': 'rewrite', 'cardId': card_id, 'part': 'body'},
                        principal='phone:owner', authorize=authorize)['job_id']
                    self.assertTrue(entered.wait(1))
                    expected = local_server.read_result(self.directory, card_id)
                    if mode == 'revoke': allowed[0] = False
                    else:
                        expected['body'] = '更新的手工正文'; local_server.save_result(self.directory, expected, card_id)
                    release.set(); value = terminal(self.manager, job_id, 'phone:owner')
                self.assertEqual(value['state'], 'error')
                self.assertEqual(value['code'], 'TEXT_JOB_FORBIDDEN' if mode == 'revoke' else 'TEXT_JOB_CARD_CHANGED')
                self.assertNotIn('PRIVATE', json.dumps(value))
                self.assertEqual(local_server.read_result(self.directory, card_id), expected)

    def test_cancel_after_commit_begins_never_claims_card_was_not_saved(self):
        card_id = self.card(); entered, release = threading.Event(), threading.Event()
        original_save = language_service._save
        def save(*args, **kwargs): entered.set(); release.wait(2); return original_save(*args, **kwargs)
        with patch.object(individual_copy, 'generate_preview', return_value=DRAFT), patch.object(language_service, '_save', side_effect=save):
            job_id = self.start({'action': 'rewrite', 'cardId': card_id, 'part': 'body'})
            self.assertTrue(entered.wait(1))
            value = self.manager.cancel(job_id, 'desktop:owner')
            self.assertFalse(value['canCancel']); self.assertFalse(value['cancelRequested'])
            release.set(); value = terminal(self.manager, job_id, 'desktop:owner')
        self.assertEqual(value['state'], 'done'); self.assertEqual(value['resultType'], 'card')
        self.assertEqual(local_server.read_result(self.directory, card_id)['body'], DRAFT['body'])

    def test_close_cancels_and_restart_has_no_false_recovery(self):
        entered = threading.Event()
        def preview(_directory, _data, *, status, cancel): entered.set(); cancel.wait(2); return DRAFT
        with patch.object(individual_copy, 'generate_preview', side_effect=preview):
            job_id = self.start(); self.assertTrue(entered.wait(1)); self.manager.close()
            self.assertEqual(self.manager.status(job_id, 'desktop:owner')['state'], 'cancelled')
        with self.assertRaises(TextJobError) as caught: self.start()
        self.assertEqual(caught.exception.code, 'TEXT_JOB_CLOSED')
        other = TextGenerationJobs(self.directory)
        try:
            status, payload = dispatch(other, 'text-jobs/'+job_id, principal='desktop:owner', authorize=lambda: None)
            self.assertEqual(status, 404); self.assertEqual(payload['code'], 'TEXT_JOB_UNAVAILABLE')
            self.assertNotIn('не изменена', payload['error'])
        finally: other.close()

    def test_invalid_requests_and_unexpected_errors_never_expose_payload(self):
        for payload in ({**PREVIEW, 'principal': 'owner'}, {**PREVIEW, 'part': 'tags'},
                        {**PREVIEW, 'requestId': 'x'}, {**PREVIEW, 'fields': {'x': float('nan')}}):
            with self.assertRaises(TextJobError): self.start(payload)
        with patch.object(individual_copy, 'generate_preview', side_effect=RuntimeError('PRIVATE INPUT /private/path')):
            value = terminal(self.manager, self.start(), 'desktop:owner')
        self.assertEqual(value['state'], 'error'); self.assertNotIn('PRIVATE', json.dumps(value))

    def test_rejected_model_explains_checked_fact_reason_without_saving(self):
        card_id = self.card()
        with patch.object(individual_copy, 'generate_preview', return_value={'status': 'blocked',
                'modelAccepted': False, 'warnings': ['Не сохранена сумма залога с правильным назначением']}):
            job_id = self.start({'action': 'rewrite', 'cardId': card_id, 'part': 'body'})
            value = terminal(self.manager, job_id, 'desktop:owner')
        self.assertEqual(value['code'], 'TEXT_JOB_REJECTED')
        self.assertIn('сумма залога', value['message'])
        self.assertEqual(local_server.read_result(self.directory, card_id)['body'], '手工正文')


class TextJobRoutes(unittest.TestCase):
    setUp = phone_fixture.PhoneRelayTests.setUp
    tearDown = phone_fixture.PhoneRelayTests.tearDown
    request = phone_fixture.PhoneRelayTests.request
    pair = phone_fixture.PhoneRelayTests.pair
    add_card = phone_fixture.PhoneRelayTests.add_card

    def start_response(self, payload, *, local=False):
        base = 'http://127.0.0.1:'+str(self.controller.server_port) if local else self.base
        fields = {'Origin': base, 'Content-Type': 'application/json'}
        fields['X-Redcn-Token' if local else 'X-Phone-CSRF'] = self.controller.desktop_access.token if local else self.csrf
        request = urllib.request.Request(base+('/api/local/' if local else '/api/phone/')+'text-jobs',
            json.dumps(payload).encode(), headers=fields)
        with self.client.open(request) as response:
            self.assertEqual(response.status, 202); return json.load(response)

    def test_phone_auth_csrf_role_and_creator_isolation(self):
        card_id = self.add_card(); payload = {'action': 'rewrite', 'cardId': card_id, 'part': 'body'}
        with self.assertRaises(urllib.error.HTTPError) as caught: self.request('/api/phone/text-jobs', payload)
        self.assertEqual(caught.exception.code, 401)
        self.pair()
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request('/api/phone/text-jobs', payload, headers={'X-Phone-CSRF': 'wrong'})
        self.assertEqual(caught.exception.code, 403)
        with self.assertRaises(urllib.error.HTTPError) as caught: self.request('/api/phone/text-jobs', PREVIEW)
        self.assertEqual(caught.exception.code, 400)
        entered, release = threading.Event(), threading.Event()
        def preview(*_args, **_kwargs): entered.set(); release.wait(2); return DRAFT
        with patch.object(individual_copy, 'generate_preview', side_effect=preview):
            started = self.start_response(payload); self.assertTrue(entered.wait(1)); job_id = started['job_id']
            own = self.request('/api/phone/text-jobs/'+job_id); self.assertEqual(own['state'], 'running')
            second = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
            code, _ = self.server.pairing.issue()
            paired = self.request('/api/phone/pair', {'code': code}, client=second)
            for path, body in [('/api/phone/text-jobs/'+job_id, None), ('/api/phone/text-jobs/'+job_id+'/cancel', {})]:
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    self.request(path, body, client=second, headers={'X-Phone-CSRF': paired['csrfToken']})
                self.assertEqual(caught.exception.code, 404)
            sessions = self.server.pairing.list_sessions()
            digest = self.server.pairing._digest(next(iter(self.jar)).value)
            self.server.pairing.manage_session(digest, role='viewer', expected_revision=sessions['revision'])
            for path, body in [('/api/phone/text-jobs/'+job_id, None), ('/api/phone/text-jobs', payload)]:
                with self.assertRaises(urllib.error.HTTPError) as caught: self.request(path, body)
                self.assertEqual(caught.exception.code, 403)
            release.set()
            value = terminal(self.controller.text_generation_jobs, job_id, 'phone:'+digest+':editor')
        self.assertEqual(value['code'], 'TEXT_JOB_FORBIDDEN')
        self.assertEqual(local_server.read_result(self.directory, card_id)['body'], '正文')

    def test_local_gate_health_and_saved_result(self):
        self.controller_thread = threading.Thread(target=self.controller.serve_forever, daemon=True)
        self.controller_thread.start(); base = 'http://127.0.0.1:'+str(self.controller.server_port)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(urllib.request.Request(base+'/api/local/text-jobs', json.dumps(PREVIEW).encode(),
                headers={'Content-Type': 'application/json', 'Origin': base}))
        self.assertEqual(caught.exception.code, 403)
        entered, release = threading.Event(), threading.Event()
        def preview(*_args, **_kwargs): entered.set(); release.wait(2); return DRAFT
        with patch.object(individual_copy, 'generate_preview', side_effect=preview):
            job_id = self.start_response(PREVIEW, local=True)['job_id']; self.assertTrue(entered.wait(1))
            with urllib.request.urlopen(base+'/api/local/health') as response: health = json.load(response)
            self.assertEqual(health['activeJobs'], 1); self.assertEqual(health['activeTextJobs'], 1)
            self.assertNotIn(job_id, json.dumps(health))
            fields = {'Origin': base, 'X-Redcn-Token': self.controller.desktop_access.token, 'Content-Type': 'application/json'}
            with urllib.request.urlopen(urllib.request.Request(base+'/api/local/text-jobs/'+job_id, headers=fields)) as response:
                self.assertEqual(json.load(response)['state'], 'running')
            with urllib.request.urlopen(urllib.request.Request(base+'/api/local/text-jobs/'+job_id+'/cancel', b'{}', headers=fields)) as response:
                self.assertTrue(json.load(response)['cancelRequested'])
            release.set()
            value = terminal(self.controller.text_generation_jobs, job_id, 'desktop:'+self.controller.workspace_owner.owner_id)
        self.assertEqual(value['state'], 'cancelled')
        with patch.object(individual_copy, 'generate_preview', return_value=DRAFT):
            job_id = self.start_response({**PREVIEW, 'requestId': 'request_text_002'}, local=True)['job_id']
            terminal(self.controller.text_generation_jobs, job_id, 'desktop:'+self.controller.workspace_owner.owner_id)
            with urllib.request.urlopen(urllib.request.Request(base+'/api/local/text-jobs/'+job_id, headers=fields)) as response:
                value = json.load(response)
            self.assertEqual(value['state'], 'done'); self.assertEqual(value['result']['body'], DRAFT['body'])
        with urllib.request.urlopen(base+'/api/local/health') as response: health = json.load(response)
        self.assertEqual(health['activeJobs'], 0); self.assertEqual(health['activeTextJobs'], 0)


if __name__ == '__main__':
    unittest.main()
