"""No models, live photographs, outbound requests or live caches required."""
import base64
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'cli'))
import local_photo_ai as ai
from PIL import Image

MODEL = 'qwen3-vl:2b-instruct-q4_K_M'
DIGEST = 'a' * 64


def item(feature='oven', **changes):
    return {'id': feature, 'visible': True, 'confidence': .96, 'count': 1,
        'countExact': True, 'evidence': 'Духовой шкаф со стеклянной дверцей и ручкой', **changes}


def inventory(*objects, unknown=()):
    return {'objects': list(objects), 'unknownIds': list(unknown)}


def photograph(size=(96, 64)):
    buffer = io.BytesIO()
    with Image.new('RGB', size, '#bcbcbc') as image:
        image.save(buffer, 'PNG')
    return buffer.getvalue()


@contextmanager
def server(chat=None, tags=None, show=None, delay=0, redirect=False, raw=None, trickle=False):
    seen = []
    entered = threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_GET(self):
            self.handle_call()
        def do_POST(self):
            self.handle_call()
        def handle_call(self):
            body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
            seen.append((self.path, json.loads(body) if body else None, dict(self.headers)))
            if self.path == '/api/tags':
                data = tags if tags is not None else {'models': [{'name': MODEL, 'size': 1_900_000_000}]}
            elif self.path == '/api/show':
                data = show if show is not None else {'capabilities': ['completion', 'vision']}
            else:
                entered.set()
                if delay:
                    time.sleep(delay)
                if redirect:
                    self.send_response(307)
                    self.send_header('Location', 'http://192.168.1.2:8080/stolen')
                    self.end_headers()
                    return
                data = chat if chat is not None else {'done': True, 'done_reason': 'stop', 'message': {'content': json.dumps(inventory(item()))}}
            encoded = raw if raw is not None and self.path == '/api/chat' else json.dumps(data).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(encoded)))
            self.end_headers()
            try:
                if trickle and self.path == '/api/chat':
                    for byte in encoded:
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        time.sleep(.02)
                else:
                    self.wfile.write(encoded)
            except (BrokenPipeError, ConnectionResetError):
                pass
    instance = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    instance.daemon_threads = True
    thread = threading.Thread(target=lambda: instance.serve_forever(poll_interval=.01), daemon=True)
    thread.start()
    try:
        yield 'http://127.0.0.1:' + str(instance.server_port), seen, entered
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join(timeout=1)


class InventoryValidationTests(unittest.TestCase):
    def normalize(self, raw):
        return ai.normalize_inventory(raw, DIGEST, MODEL)

    def test_positive_inventory_carries_per_photo_provenance_and_never_promotes_counts(self):
        result = self.normalize(inventory(item('chairs', count=2, evidence='Два стула с отдельными спинками у обеденного стола')))
        row = result['candidates'][0]
        self.assertTrue(row['autoEligible'])
        self.assertFalse(row['autoCountEligible'])
        self.assertEqual(row['status'], 'candidate')
        self.assertEqual(row['count'], 2)
        self.assertEqual(row['evidence'][0]['originalId'], DIGEST)
        self.assertEqual(row['evidence'][0]['model'], MODEL)
        self.assertIn('fridge', result['unknownIds'])
        self.assertNotIn('absent', str(result))

    def test_oven_never_establishes_other_kitchen_appliances(self):
        result = self.normalize(inventory(item()))
        self.assertEqual([row['id'] for row in result['candidates']], ['oven'])
        self.assertTrue({'stove', 'sink', 'fridge', 'microwave'} <= set(result['unknownIds']))

    def test_all_visible_feature_ids_match_frontend_and_permissions_are_excluded(self):
        source = (Path(__file__).resolve().parents[1] / 'web' / 'engine.mjs').read_text()
        block = source.split('export const FEATURES = [', 1)[1].split('\n];', 1)[0]
        import re
        ids = set(re.findall(r"\['([A-Za-z]+)'", block))
        self.assertEqual(set(ai.FEATURE_LABELS), ids - {'internet'})
        rows = self.normalize(inventory(item('internet'), item('pets')))
        self.assertEqual(rows['candidates'], [])

    def test_structural_features_are_review_only_and_bed_dimensions_are_never_claimed(self):
        rows = [item(feature, evidence='Предмет виден на фотографии') for feature in ai.REVIEW_ONLY]
        result = self.normalize(inventory(*rows, item('bed', evidence='Кровать с мягким изголовьем у стены')))
        for row in result['candidates']:
            if row['id'] == 'bedAny':
                self.assertTrue(row['autoEligible'])
                self.assertNotIn('двуспальная', row['label'].lower())
            else:
                self.assertFalse(row['autoEligible'])
        self.assertNotIn('bed', [row['id'] for row in result['candidates']])

    def test_low_confidence_and_generic_evidence_never_automatically_fill(self):
        for changes in ({'confidence': .89}, {'evidence': 'Предмет виден на фотографии'}):
            row = self.normalize(inventory(item(**changes)))['candidates'][0]
            self.assertFalse(row['autoEligible'])
        self.assertTrue(self.normalize(inventory(item(confidence=.9)))['candidates'][0]['autoEligible'])

    def test_new_hardware_ids_remain_distinct_and_do_not_imply_other_equipment(self):
        result = self.normalize(inventory(
            item('router', evidence='Роутер с двумя антеннами и сетевыми разъёмами'),
            item('monitor', evidence='Монитор на подставке с прямоугольным экраном'),
            item('mirror', evidence='Зеркало над умывальником в тонкой раме'),
            item('dryingRack', evidence='Складная сушилка для белья с металлическими прутьями')))
        rows = result['candidates']
        self.assertEqual({row['id'] for row in rows}, {'router', 'monitor', 'mirror', 'dryingRack'})
        self.assertTrue(all(row['autoEligible'] for row in rows))
        self.assertTrue({'computer', 'tv', 'dryer', 'washbasin'} <= set(result['unknownIds']))
        self.assertNotIn('internet', result['unknownIds'])

    def test_negative_speculative_and_hidden_evidence_in_three_languages_is_rejected(self):
        for evidence in ('Духовка не видна на фото', 'Холодильник отсутствует на фотографии',
                'Возможно, это духовой шкаф', 'Oven is not visible', 'Probably an oven behind the cabinet',
                'No visible oven in this photograph', '烤箱不可见，因为橱柜关闭', '可能有一个烤箱在这里'):
            with self.subTest(evidence=evidence):
                self.assertEqual(self.normalize(inventory(item(evidence=evidence)))['candidates'], [])

    def test_planned_or_future_objects_are_never_positive_visual_observations(self):
        for evidence in ('Планируется установить духовку', 'Пылесос будет куплен',
                'Купят холодильник на следующей неделе', 'Установят духовку рядом с мойкой',
                'Привезут кровать в спальню', 'Собираются добавить духовой шкаф',
                'An oven will be installed here', 'A planned oven beside the cabinet',
                'Going to buy a new oven for the kitchen', 'An oven is going to be installed',
                'Scheduled oven delivery tomorrow',
                '准备在这里安装烤箱', '厨房将配备烤箱和冰箱', '计划购买一台烤箱', '烤箱待安装在橱柜内'):
            with self.subTest(evidence=evidence):
                self.assertEqual(self.normalize(inventory(item(evidence=evidence)))['candidates'], [])
        # The future filter uses word boundaries, not the substring "will".
        positive = 'An oven with a glass door beside the window'
        self.assertTrue(self.normalize(inventory(item(evidence=positive)))['candidates'][0]['autoEligible'])

    def test_strict_scores_visibility_ids_and_schema(self):
        for changes in ({'confidence': True}, {'confidence': float('nan')}, {'confidence': 2},
                {'confidence': .64}, {'visible': 1}, {'visible': False}, {'id': ['oven']},
                {'evidence': []}, {'evidence': 'short'}, {'countExact': 'true'}, {'extra': 'data'}):
            with self.subTest(changes=changes):
                self.assertEqual(self.normalize(inventory(item(**changes)))['candidates'], [])
        for raw in ({}, {'objects': []}, {'objects': 'wrong', 'unknownIds': []},
                inventory(unknown=['fake']), {'objects': [], 'unknownIds': [], 'source': 'fake'}):
            with self.assertRaises(ValueError):
                self.normalize(raw)
        with self.assertRaises(ValueError):
            ai.normalize_inventory(inventory(), '../photo', MODEL)

    def test_uncertain_counts_and_duplicate_views_never_inflate_counts(self):
        for count in (True, 0, 13, 2.0, '2', None):
            row = self.normalize(inventory(item(count=count)))['candidates'][0]
            self.assertIsNone(row['count'])
        self.assertIsNone(self.normalize(inventory(item(count=2, countExact=False)))['candidates'][0]['count'])
        result = self.normalize(inventory(item(count=2), item(count=4, confidence=.88)))
        self.assertEqual(len(result['candidates']), 1)
        self.assertEqual(result['candidates'][0]['count'], 2)

    def test_contradictory_unknown_is_review_only(self):
        result = self.normalize(inventory(item(), unknown=['oven']))
        self.assertFalse(result['candidates'][0]['autoEligible'])
        self.assertIn('oven', result['unknownIds'])


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.content = photograph()

    def analyze(self, endpoint, **kwargs):
        return ai.analyze_photo(self.content, 'image/png', model=MODEL, endpoint=endpoint, **kwargs)

    def test_actual_loopback_request_uses_schema_safe_metadata_free_preview_and_no_proxy(self):
        with server() as (endpoint, seen, _), patch.dict(os.environ, {'HTTP_PROXY': 'http://invalid.invalid:3', 'OLLAMA_API_KEY': 'must-not-be-sent'}):
            result = self.analyze(endpoint)
        self.assertEqual(result['status'], 'ok', result)
        self.assertEqual(result['originalId'], hashlib.sha256(self.content).hexdigest())
        self.assertEqual([row[0] for row in seen], ['/api/tags', '/api/show', '/api/chat'])
        payload = seen[-1][1]
        self.assertEqual(payload['format'], ai.inventory_schema())
        self.assertFalse(payload['stream'])
        self.assertEqual(payload['options']['temperature'], 0)
        self.assertEqual(payload['options']['num_ctx'], 4096)
        self.assertEqual(payload['keep_alive'], 0)
        self.assertNotIn('Authorization', seen[-1][2])
        self.assertNotIn('tools', payload)
        preview = base64.b64decode(payload['messages'][0]['images'][0], validate=True)
        self.assertTrue(preview.startswith(b'\xff\xd8\xff'))
        with Image.open(io.BytesIO(preview)) as image:
            self.assertFalse(image.getexif())
            self.assertLessEqual(max(image.size), 1280)
        self.assertEqual(self.content, photograph())

    def test_no_configuration_and_pre_cancel_do_not_connect(self):
        cancel = threading.Event(); cancel.set()
        with patch.object(ai.http.client, 'HTTPConnection') as connection:
            self.assertEqual(ai.analyze_photo(self.content, 'image/png', model='')['status'], 'disabled')
            self.assertEqual(ai.analyze_photo(self.content, 'image/png', model=MODEL, cancel=cancel)['status'], 'cancelled')
            connection.assert_not_called()

    def test_endpoint_cannot_exfiltrate_photos(self):
        bad = ['https://127.0.0.1:11434', 'http://localhost:11434', 'http://192.168.0.1:11434',
            'http://127.0.0.1.example.org', 'http://user:pass@127.0.0.1:11434',
            'http://127.0.0.1:11434/api/chat', 'http://127.0.0.1:11434?external=1',
            'http://127.0.0.1:11434#fragment', 'http://127.0.0.1:0', 'http://127.0.0.1:65536',
            'file:///tmp/model', 'http://127.0.0.1\n:11434']
        with patch.object(ai.http.client, 'HTTPConnection') as connection:
            for endpoint in bad:
                with self.subTest(endpoint=endpoint):
                    self.assertEqual(self.analyze(endpoint)['status'], 'invalid')
            connection.assert_not_called()
        self.assertEqual(ai._origin('http://[::1]:11435'), ('::1', 11435))

    def test_cloud_names_aliases_missing_models_and_text_models_never_receive_image(self):
        with patch.object(ai.http.client, 'HTTPConnection') as connection:
            self.assertEqual(ai.analyze_photo(self.content, 'image/png', model='qwen3-vl:cloud')['status'], 'invalid')
            connection.assert_not_called()
        for options, status in (({'tags': {'models': []}}, 'unavailable'),
                ({'tags': {'models': [{'name': MODEL, 'size': 100, 'remote_host': 'https://ollama.com'}]}}, 'invalid'),
                ({'tags': {'models': [{'name': MODEL, 'size': 100, 'remote_model': 'qwen:cloud'}]}}, 'invalid'),
                ({'show': {'capabilities': ['completion']}}, 'unavailable')):
            with server(**options) as (endpoint, seen, _):
                result = self.analyze(endpoint)
            self.assertEqual(result['status'], status, result)
            self.assertNotIn('/api/chat', [row[0] for row in seen])

    def test_invalid_image_or_timeout_settings_never_connect(self):
        with patch.object(ai.http.client, 'HTTPConnection') as connection:
            for content, mime in ((b'not an image', 'image/png'), (self.content, 'image/jpeg'),
                    (self.content, 'text/plain'), (b'x' * (ai.MAX_IMAGE_BYTES + 1), 'image/png')):
                self.assertEqual(ai.analyze_photo(content, mime, model=MODEL)['status'], 'invalid')
            for timeout in (0, -1, 121, True, float('nan'), '60'):
                self.assertEqual(self.analyze(ai.DEFAULT_ENDPOINT, timeout=timeout)['status'], 'invalid')
            self.assertEqual(self.analyze(ai.DEFAULT_ENDPOINT, deadline=time.monotonic()-1)['status'], 'timeout')
            connection.assert_not_called()

    def test_redirects_malformed_json_truncation_and_excessive_output_are_failures(self):
        cases = [({'redirect': True}, 'unavailable'), ({'raw': b'not json'}, 'invalid'),
            ({'raw': b'{"done":true,"done":false}'}, 'invalid'),
            ({'raw': b'x' * (ai.MAX_RESPONSE_BYTES + 1)}, 'invalid'),
            ({'chat': {'done': False, 'message': {'content': '{}'}}}, 'invalid'),
            ({'chat': {'done': True, 'done_reason': 'length', 'message': {'content': json.dumps(inventory(item()))}}}, 'invalid'),
            ({'chat': {'done': True, 'message': {'content': '{"objects":[],"unknownIds":[],"x":NaN}'}}}, 'invalid')]
        for options, status in cases:
            with self.subTest(options=list(options)):
                with server(**options) as (endpoint, seen, _):
                    result = self.analyze(endpoint)
                self.assertEqual(result['status'], status, result)
                self.assertEqual([row[0] for row in seen].count('/api/chat'), 1)

    def test_absolute_deadline_bounds_even_slow_drip_response(self):
        with server(trickle=True) as (endpoint, _, _):
            started = time.monotonic()
            result = self.analyze(endpoint, timeout=2, deadline=started+.18)
            elapsed = time.monotonic() - started
        self.assertEqual(result['status'], 'timeout', result)
        self.assertLess(elapsed, .6)
        self.assertEqual(result['candidates'], [])

    def test_inflight_cancel_is_prompt_and_serial_slot_is_not_queued(self):
        cancel = threading.Event()
        result = {}
        with server(delay=.4) as (endpoint, _, entered):
            thread = threading.Thread(target=lambda: result.update(self.analyze(endpoint, cancel=cancel)), daemon=True)
            thread.start()
            self.assertTrue(entered.wait(1))
            self.assertEqual(self.analyze(endpoint)['status'], 'busy')
            started = time.monotonic(); cancel.set(); thread.join(timeout=.6)
            self.assertFalse(thread.is_alive())
            self.assertLess(time.monotonic()-started, .6)
            self.assertEqual(result['status'], 'cancelled')
        # The closed socket wakes the worker and releases the slot, rather than
        # leaving a permanent busy state after cancellation.
        released = False
        until = time.monotonic() + 1
        while time.monotonic() < until:
            if ai._SLOT.acquire(blocking=False):
                ai._SLOT.release(); released = True; break
            time.sleep(.01)
        self.assertTrue(released)


if __name__ == '__main__':
    unittest.main()
