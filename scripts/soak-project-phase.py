#!/usr/bin/env python3
"""One real isolated operational phase; called by soak-project.py, not a benchmark."""
import argparse
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import sqlite3
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'cli'))
from project_soak import atomic


def import_phase(workspace, args):
    import redcn
    from local_server import save_result, read_result
    from node_runtime import resolve_node
    fixture=ROOT/'tests/fixtures/cian-user-observed-v26.html'
    listing=redcn.parse_html(fixture.read_text(),'https://www.cian.ru/rent/flat/123456789/')
    fields={**listing.get('fields',{}),'source':listing['text'],'photos':[]}
    result=redcn.generate(listing['text'],fields)
    assert result['body'] and result['titles'] and result['fields']['price']
    cache=workspace/'application';cache.mkdir(exist_ok=True,mode=0o700)
    identifier='a'*32
    save_result(cache,result,identifier)
    stored=read_result(cache,identifier)
    assert stored['body']==result['body'] and stored['fields']==result['fields']
    with redcn.open_cache(cache) as db:
        assert db.execute('SELECT COUNT(*) FROM results').fetchone()[0]==1
    return {'backend':'node-python','sourceFixtureSha256':hashlib.sha256(fixture.read_bytes()).hexdigest(),
            'savedCardCount':1,'reopenedSameRevision':True,'externalSourceRequested':False,
            'nodeExecutable':Path(resolve_node()).name}


def editor_phase(workspace,args):
    from playwright.sync_api import sync_playwright
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self,*a,**kw):super().__init__(*a,directory=str(ROOT/'docs'),**kw)
        def log_message(self,*_):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    errors=[]
    try:
        with sync_playwright() as runtime:
            browser=runtime.chromium.launch(channel='chrome',headless=True)
            try:
                context=browser.new_context(viewport={'width':1280,'height':900},service_workers='block')
                base='http://127.0.0.1:'+str(server.server_port)
                # This built static fixture is isolated from the production API.
                def route(request):
                    if request.request.url.startswith(base+'/'):request.continue_()
                    else:raise ValueError('Unexpected external editor request')
                context.route('**/*',route)
                page=context.new_page();page.set_default_timeout(12000)
                page.on('pageerror',lambda error:errors.append(type(error).__name__))
                page.goto(base,wait_until='domcontentloaded')
                page.wait_for_function("document.documentElement.dataset.editorReady==='true'")
                page.locator('#source').fill('2-комнатная квартира. Общая площадь 52 м². Цена 80000 руб. Есть холодильник.')
                page.locator('#extract').click();page.locator('#generate').click()
                assert '80,000' in page.locator('#output').input_value()
                page.locator('#output').fill('手工编辑保留。')
                page.locator('#output').dispatch_event('change')
                page.locator('#outputTitle').fill('手工标题')
                page.locator('#outputTitle').dispatch_event('change')
                page.reload(wait_until='domcontentloaded')
                page.wait_for_function("document.documentElement.dataset.editorReady==='true'")
                assert page.locator('#output').input_value()=='手工编辑保留。'
                assert page.locator('#outputTitle').input_value()=='手工标题'
                assert not errors
                context.close()
            finally:browser.close()
    finally:server.shutdown();server.server_close();thread.join(5)
    return {'backend':'chrome','manualEditSurvivedReload':True,'pageErrors':len(errors),'productionServerUsed':False}


def photo_phase(workspace,args):
    if os.environ.get('REDCN_SOAK_RESOURCE_HELD')!='1':raise ValueError('Shared resource lock is required')
    if not args.model_cache:raise ValueError('Explicit model cache required')
    from PIL import Image
    from trained_photo_detector import _infer,MODEL_SHA
    image=Image.new('RGB',(416,416),(210,215,220));stream=io.BytesIO();image.save(stream,format='PNG');image.close()
    content=stream.getvalue();result=_infer(Path(args.model_cache),content,'image/png')
    assert result['status']=='review' and result['provider']=='yolox-trained'
    return {'backend':'onnxruntime','modelSha256':MODEL_SHA,'imageSha256':hashlib.sha256(content).hexdigest(),
            'imageKind':'synthetic-runtime-fixture','accuracyEvaluated':False,
            'inferenceSeconds':result.get('ai',{}).get('inferenceSeconds'),
            'architecture':result.get('runtimeArchitecture')}


def training_phase(workspace,args):
    if os.environ.get('REDCN_SOAK_RESOURCE_HELD')!='1':raise ValueError('Shared resource lock is required')
    if not args.base_model:raise ValueError('Explicit downloaded model required')
    specification=importlib.util.spec_from_file_location('soak_training',ROOT/'scripts/train-local-text-model.py')
    module=importlib.util.module_from_spec(specification);specification.loader.exec_module(module)
    dataset=ROOT/'data/learning/factual-curriculum-v28.json'
    folder=workspace/'training';folder.mkdir(exist_ok=True,mode=0o700)
    with tempfile.TemporaryDirectory(prefix='bounded-',dir=folder) as output:
        recipe=SimpleNamespace(model=args.base_model,dataset=str(dataset),output=output,steps=2,
            max_seconds=45,max_seq_length=1024,eval_examples=2,learning_rate=.00002,
            seed=20261102,sampling='shuffled',cancel_file=None,resume_adapter=args.adapter)
        receipt=module.run(recipe)
        assert receipt['stepsCompleted']==2 and not receipt['stoppedEarly'] and receipt['changedParameterElements']>0
        # Preserve the small genuine training receipt; temporary unaccepted weights
        # are deleted only from the directory created by this phase itself.
        receipts=workspace/'training-receipts';receipts.mkdir(exist_ok=True,mode=0o700)
        atomic(receipts/(str(time.time_ns())+'.json'),receipt)
        return {'backend':'mlx','completedSteps':receipt['stepsCompleted'],
                'changedParameterElements':receipt['changedParameterElements'],
                'peakMlxBytes':receipt['peakMemoryBytes'],'adapterSha256':receipt['adapterSha256'],
                'datasetSha256':receipt['datasetSha256'],'autoPromoted':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kind',choices=('import','editor','photo','training'),required=True)
    parser.add_argument('--workspace',type=Path,required=True)
    parser.add_argument('--model-cache')
    parser.add_argument('--base-model')
    parser.add_argument('--adapter')
    args=parser.parse_args();workspace=args.workspace.resolve()
    if os.environ.get('REDCN_SOAK_WORKSPACE')!=str(workspace) or not (workspace/'protocol.json').is_file():
        parser.error('Use the isolated soak runner')
    os.umask(0o077)
    signal.signal(signal.SIGTERM,lambda *_:(_ for _ in ()).throw(KeyboardInterrupt()))
    value={'kind':args.kind,'ok':False,'isolated':True,'mocked':False};start=time.monotonic()
    try:
        function={'import':import_phase,'editor':editor_phase,'photo':photo_phase,'training':training_phase}[args.kind]
        value.update(function(workspace,args));value['ok']=True
    except BaseException as error:
        value['errorType']=type(error).__name__
        raise
    finally:
        value['seconds']=time.monotonic()-start
        atomic(workspace/(args.kind+'.json'),value)
    print(json.dumps(value,ensure_ascii=False))


if __name__=='__main__':main()
