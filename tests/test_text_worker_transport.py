"""Real child-process input regression; no MLX/model dependency is involved."""
from contextlib import nullcontext
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'cli'))
import local_text_model as runtime


CONFIG = {'model_type': 'qwen2', 'hidden_size': 896, 'num_hidden_layers': 24,
          'num_attention_heads': 14, 'num_key_value_heads': 2}
CHILD = '''import sys,time,json,hashlib
time.sleep(.3)
raw=sys.stdin.buffer.read()
value=json.loads(raw)
print(json.dumps({'model':sys.argv[1], 'text':hashlib.sha256(raw).hexdigest()}))
'''


class TextWorkerTransportTests(unittest.TestCase):
    def invoke(self, *, cancel=False, hang=False, deadline=None):
        request = {'messages': [{'role': 'user', 'content': '家具资料甲乙丙丁' * 1400}],
                   'maxTokens': 350, 'temperature': .5}
        expected = hashlib.sha256(json.dumps(runtime.clean_request(request), ensure_ascii=False).encode()).hexdigest()
        original = subprocess.Popen
        children, files, records = [], [], []
        cancellation = threading.Event()

        def spawn(command, **kwargs):
            files.append(kwargs['stdin'])
            self.assertNotEqual(kwargs['stdin'], subprocess.PIPE)
            code = 'import time;time.sleep(30)' if hang or cancel else CHILD
            child = original([sys.executable, '-c', code, runtime.BASE_MODEL], **kwargs)
            children.append(child)
            if cancel:
                cancellation.set()
            return child

        scheduler = types.SimpleNamespace(model_resource=lambda *a, **kw: nullcontext(),
                                         touch_foreground=lambda *a: None)
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            (base/'config.json').write_text(json.dumps(CONFIG))
            config = {'enabled': True, 'pythonPath': sys.executable, 'modelPath': folder,
                      'adapterPath': None, 'maxSeconds': .65 if hang else 3}
            with patch.object(runtime, 'settings', return_value=config), \
                    patch.dict(sys.modules, {'learning_scheduler': scheduler}), \
                    patch.object(runtime.subprocess, 'Popen', side_effect=spawn):
                result = runtime.generate(request, base, cancel=cancellation, deadline=deadline,
                                          status=lambda message, **fields: records.append(fields))
        return result, expected, children, files, records

    def test_slow_reader_receives_entire_request_and_eof(self):
        result, expected, children, files, _ = self.invoke()
        self.assertEqual(result['status'], 'review', result)
        self.assertEqual(result['text'], expected)
        self.assertEqual(children[0].returncode, 0)
        self.assertTrue(files[0].closed)
        self.assertTrue(children[0].stdout.closed)

    def test_cancel_reaps_only_owned_worker_and_closes_request(self):
        result, _, children, files, _ = self.invoke(cancel=True)
        self.assertEqual(result['ai']['phase'], 'cancelled')
        self.assertIsNotNone(children[0].returncode)
        self.assertTrue(files[0].closed)
        self.assertTrue(children[0].stdout.closed)

    def test_timeout_still_reaps_worker_with_complete_input(self):
        result, _, children, files, _ = self.invoke(hang=True)
        self.assertEqual(result['ai']['phase'], 'timeout')
        self.assertIsNotNone(children[0].returncode)
        self.assertTrue(files[0].closed)

    def test_expired_external_deadline_does_not_start_worker(self):
        result, _, children, files, _ = self.invoke(deadline=0)
        self.assertEqual(result['ai']['phase'], 'timeout')
        self.assertFalse(children)
        self.assertFalse(files)


if __name__ == '__main__':
    unittest.main()
