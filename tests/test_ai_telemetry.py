"""Measured AI progress, bounded seeds and process cleanup without model loads."""
from contextlib import nullcontext
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'cli'))
import ai_telemetry
import job_progress
import local_text_model as runtime

CONFIG={'model_type':'qwen2','hidden_size':896,'num_hidden_layers':24,
    'num_attention_heads':14,'num_key_value_heads':2}
REQUEST={'messages':[{'role':'user','content':'Synthetic apartment facts only'}]}


class TelemetryTests(unittest.TestCase):
    def test_allowlist_rejects_private_content_paths_nonfinite_and_wrong_types(self):
        value=ai_telemetry.sanitize_ai({'backend':'mlx','model':'/Users/private/model',
            'operation':{},'phase':[],'tokensPerSecond':float('nan'),
            'generatedTokens':True,'photoIndex':-1,'elapsedSeconds':float('inf'),
            'prompt':'private prompt','images':['raw pixels'],'url':'https://private.invalid'})
        self.assertEqual(value,{'backend':'mlx'})
        self.assertEqual(ai_telemetry.sanitize_ai({'model':'https://private.invalid'}),{})
        self.assertEqual(ai_telemetry.sanitize_ai(None),{})
        self.assertEqual(ai_telemetry.sanitize_ai({'elapsedSeconds':10**1000,
            'tokensPerSecond':10**1000,'generatedTokens':10**1000}),{})

    def test_detector_and_cache_never_claim_current_token_generation(self):
        detector=ai_telemetry.sanitize_ai({'backend':'yolox-trained','model':'redcn-furniture-v1',
            'operation':'furniture','phase':'complete','tokenBased':False,
            'generatedTokens':123,'tokensPerSecond':99,'generationSeconds':2,
            'inferenceSeconds':.25,'imagesPerSecond':4})
        self.assertNotIn('generatedTokens',detector)
        self.assertNotIn('tokensPerSecond',detector)
        self.assertEqual(detector['imagesPerSecond'],4)
        cached=ai_telemetry.sanitize_ai({**detector,'backend':'ollama','tokenBased':True,
            'cached':True,'generatedTokens':123,'tokensPerSecond':99,'generationSeconds':2})
        self.assertEqual(cached['phase'],'cached')
        for field in ('generatedTokens','tokensPerSecond','generationSeconds','imagesPerSecond','inferenceSeconds'):
            self.assertNotIn(field,cached)

    def test_rules_fallback_keeps_measured_draft_rate_but_labels_text_source(self):
        value=ai_telemetry.sanitize_ai({'backend':'mlx','operation':'text','phase':'complete',
            'textSource':'rules','tokensPerSecond':7.5,'generatedTokens':30})
        self.assertEqual(value['textSource'],'rules');self.assertEqual(value['tokensPerSecond'],7.5)
        self.assertNotIn('textSource',ai_telemetry.sanitize_ai({'textSource':'<script>bad</script>'}))

    def test_sidecar_contains_only_allowlisted_metadata_and_is_private(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'progress.json'
            ai_telemetry.write_progress(path,{'backend':'mlx','phase':'generating',
                'generatedTokens':12,'tokensPerSecond':6,'prompt':'secret'})
            self.assertEqual(path.stat().st_mode&0o777,0o600)
            self.assertNotIn('secret',path.read_text())
            self.assertEqual(ai_telemetry.read_progress(path)['generatedTokens'],12)
            target=Path(directory)/'untouched';target.write_text('private')
            path.unlink();path.symlink_to(target)
            ai_telemetry.write_progress(path,{'phase':'error'})
            self.assertEqual(target.read_text(),'private')
            self.assertEqual(ai_telemetry.read_progress(path),{})

    def test_job_clock_advances_elapsed_but_never_invents_rate_or_history(self):
        with tempfile.TemporaryDirectory() as directory:
            clock=[0]
            tracker=job_progress.JobProgress(job_progress.DurationHistory(directory),'import:vision',
                clock=lambda:clock[0],wall_clock=lambda:1000+clock[0])
            tracker.update('ИИ',stage='vision',ai={'backend':'ollama','model':'fixture-vlm',
                'operation':'furniture','phase':'generating','elapsedSeconds':2,
                'generatedTokens':10,'tokensPerSecond':5,'photoIndex':1,'photoTotal':2,'prompt':'secret'})
            clock[0]=3
            current=tracker.snapshot()['ai']
            self.assertEqual(current['elapsedSeconds'],5)
            self.assertEqual(current['tokensPerSecond'],5)
            self.assertTrue(current['active'])
            self.assertNotIn('prompt',current)
            current['model']='mutated'
            self.assertEqual(tracker.snapshot()['ai']['model'],'fixture-vlm')
            tracker.update('Сохраняем',stage='save')
            clock[0]=20
            frozen=tracker.snapshot()['ai']
            self.assertFalse(frozen['active']);self.assertEqual(frozen['elapsedSeconds'],5)
            tracker.finish(True)
            history=(Path(directory)/'import-timings.json').read_text()
            self.assertNotIn('fixture-vlm',history)
            self.assertNotIn('tokens',history)

    def test_error_stops_ai_clock_and_keeps_measured_tokens(self):
        with tempfile.TemporaryDirectory() as directory:
            clock=[0];tracker=job_progress.JobProgress(job_progress.DurationHistory(directory),'import:cache',clock=lambda:clock[0])
            tracker.update('ИИ',stage='generate',ai={'backend':'mlx','phase':'generating','elapsedSeconds':1,'generatedTokens':4})
            clock[0]=5;tracker.finish(False);clock[0]=100
            ai=tracker.snapshot()['ai']
            self.assertEqual(ai['phase'],'error');self.assertFalse(ai['active'])
            self.assertEqual(ai['elapsedSeconds'],6);self.assertEqual(ai['generatedTokens'],4)


class TextStreamTests(unittest.TestCase):
    def test_uint32_seed_and_advertising_token_limit_remain_bounded(self):
        self.assertNotIn('seed',runtime.clean_request(REQUEST))
        self.assertEqual(runtime.clean_request({**REQUEST,'seed':0,'maxTokens':768})['maxTokens'],768)
        self.assertEqual(runtime.clean_request({**REQUEST,'seed':2**32-1})['seed'],2**32-1)
        for seed in (True,-1,2**32,1.2,'4',None):
            with self.assertRaises(ValueError):runtime.clean_request({**REQUEST,'seed':seed})
        with self.assertRaises(ValueError):runtime.clean_request({**REQUEST,'maxTokens':1025})

    def test_worker_uses_real_stream_counters_and_never_writes_generated_text_to_progress(self):
        seeds=[];records=[];samplers=[]
        mx=types.ModuleType('mlx.core');mx.set_memory_limit=lambda value:None;mx.set_cache_limit=lambda value:None
        mx.random=types.SimpleNamespace(seed=seeds.append)
        mlx=types.ModuleType('mlx');mlx.core=mx
        tokenizer=types.SimpleNamespace(apply_chat_template=lambda *args,**kwargs:'private fixture prompt',encode=lambda text:[1,2,3,4])
        module=types.ModuleType('mlx_lm');module.load=lambda *args,**kwargs:(object(),tokenizer)
        def stream(model,tokenizer,**kwargs):
            self.assertEqual(kwargs['max_tokens'],768)
            kwargs['prompt_progress_callback'](2,4)
            for index,text in enumerate(('你好','','世界'),1):
                yield types.SimpleNamespace(text=text,generation_tokens=index,generation_tps=6,
                    prompt_tokens=4,prompt_tps=20)
        module.stream_generate=stream
        sampling=types.ModuleType('mlx_lm.sample_utils');sampling.make_sampler=lambda **kwargs:samplers.append(kwargs)
        with tempfile.TemporaryDirectory() as directory:
            model=Path(directory);(model/'config.json').write_text(json.dumps(CONFIG))
            with patch.dict(sys.modules,{'mlx':mlx,'mlx.core':mx,'mlx_lm':module,'mlx_lm.sample_utils':sampling}), \
                    patch.object(runtime,'write_progress',side_effect=lambda path,value:records.append(ai_telemetry.sanitize_ai(value))):
                result=runtime.worker(model,'-',{**REQUEST,'maxTokens':768,'seed':99},progress_path=model/'progress.json')
        self.assertEqual(seeds,[99]);self.assertEqual(result['text'],'你好世界')
        self.assertEqual(result['ai']['generatedTokens'],3)
        self.assertEqual(result['ai']['tokensPerSecond'],6)
        self.assertEqual(result['ai']['generationSeconds'],.5)
        self.assertEqual(result['ai']['phase'],'complete')
        self.assertEqual(records[0]['phase'],'loading')
        self.assertNotIn('你好',json.dumps(records,ensure_ascii=False))
        self.assertNotIn('private fixture prompt',json.dumps(records))

    def _runtime(self, cancel_on_poll=False):
        cancel=threading.Event();records=[];instances=[]
        class Process:
            def __init__(self,command,**kwargs):
                self.path=Path(command[-1]);self.returncode=None;self.calls=[];self.terminated=False
                instances.append(self)
            def communicate(self,input=None,timeout=None):
                self.calls.append(input)
                if len(self.calls)==1:
                    ai_telemetry.write_progress(self.path,{'backend':'mlx','model':runtime.BASE_MODEL,
                        'operation':'text','phase':'generating','generatedTokens':8,'tokensPerSecond':4,'generationSeconds':2})
                    if cancel_on_poll:cancel.set()
                    raise subprocess.TimeoutExpired('fixture worker',timeout)
                self.returncode=0
                return json.dumps({'model':runtime.BASE_MODEL,'text':'synthetic result',
                    'ai':{'backend':'mlx','operation':'text','phase':'complete','generatedTokens':10,'tokensPerSecond':5,'generationSeconds':2}}).encode(),None
            def poll(self):return self.returncode
            def terminate(self):self.terminated=True;self.returncode=-15
            def wait(self,timeout=None):return self.returncode
            def kill(self):self.returncode=-9
        scheduler=types.SimpleNamespace(model_resource=lambda *args,**kwargs:nullcontext(),touch_foreground=lambda *args:None)
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);(base/'config.json').write_text(json.dumps(CONFIG));python=base/'python';python.write_text('fixture only')
            with patch.object(runtime,'settings',return_value={'enabled':True,'pythonPath':str(python),'modelPath':directory,'adapterPath':None,'maxSeconds':55}), \
                    patch.dict(sys.modules,{'learning_scheduler':scheduler}),patch.object(runtime.subprocess,'Popen',Process):
                result=runtime.generate(REQUEST,base,cancel=cancel,status=lambda message,**fields:records.append(fields))
        return result,records,instances[0]

    def test_runtime_forwards_real_live_metrics_and_cleans_sidecar(self):
        result,records,process=self._runtime()
        self.assertEqual(result['status'],'review')
        self.assertTrue(any(row['ai'].get('generatedTokens')==8 for row in records))
        self.assertEqual(result['ai']['generatedTokens'],10)
        self.assertEqual(result['ai']['tokensPerSecond'],5)
        self.assertIsNone(process.calls[1]);self.assertFalse(process.path.parent.exists())

    def test_cancel_stops_only_owned_worker_and_cleans_sidecar(self):
        result,records,process=self._runtime(cancel_on_poll=True)
        self.assertEqual(result['status'],'unavailable');self.assertEqual(result['ai']['phase'],'cancelled')
        self.assertTrue(process.terminated);self.assertFalse(process.path.parent.exists())
        self.assertEqual(result['ai']['generatedTokens'],8)


if __name__=='__main__':unittest.main()
