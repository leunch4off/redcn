#!/usr/bin/env python3
"""Explicit public-Open-Images runtime benchmark; never an import controller.

Reads only the first-pass public manifest/artifacts and validation photographs
whose public origin, licence metadata and SHA are present in that manifest.
The selected detector remains review-only; no active configuration is changed.
"""
import argparse
from contextlib import contextmanager
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import resource
import select
import sqlite3
import subprocess
import sys
import time

EXPECTED_MANIFEST = '33b741b0f9162379ae58c9cd23dd03ee73c9fc9c5a875abf22f7dcfaf0416e0c'
EXPECTED_MODEL = '650c6d96cd8b9e0c0422174467590b6ec377bc7fefa6c6ff01b0546db5392de5'


def sha(content):
    return hashlib.sha256(content).hexdigest()


def dataset(cache):
    root = cache/'tools/photo-training/20261101-first-pass'
    source = root/'dataset-source.json'
    ready = root/'dataset-ready.json'
    if any(path.is_symlink() for path in (root, source, ready)):
        raise ValueError('Public dataset must not use symlinks')
    if sha(source.read_bytes()) != EXPECTED_MANIFEST:
        raise ValueError('Public source manifest SHA changed')
    document = json.loads(ready.read_text())
    if document.get('sourceReadyManifestSha256') != EXPECTED_MANIFEST:
        raise ValueError('Portable manifest is not linked to the trained source manifest')
    if document.get('source') != 'Open Images V7 boxes' or document.get('explicitDatasetLicenseAllowance') is not True:
        raise ValueError('Explicit public-dataset allowance is missing')
    model = root/'run-01/model.onnx'
    if model.is_symlink() or sha(model.read_bytes()) != EXPECTED_MODEL:
        raise ValueError('Chosen first-pass detector identity changed')
    rows = document.get('splits', {}).get('validation', [])
    train_ids = {row['imageId'] for row in document['splits']['train']}
    train_shas = {row.get('sha256') for row in document['splits']['train']}
    if len(rows) != 80 or len({row.get('imageId') for row in rows}) != 80:
        raise ValueError('Expected the existing 80-frame held-out public split')
    for row in rows:
        identity = row.get('imageId', '')
        attribution = row.get('attribution', {})
        if (len(identity) != 16 or any(character not in '0123456789abcdef' for character in identity)
                or identity in train_ids or row.get('sha256') in train_shas or row.get('sourceSplit') != 'validation'
                or row.get('sourceUrl') != 'https://open-images-dataset.s3.amazonaws.com/validation/'+identity+'.jpg'
                or attribution.get('License') != 'https://creativecommons.org/licenses/by/2.0/'
                or not str(attribution.get('OriginalLandingURL', '')).startswith('https://www.flickr.com/photos/')
                or not attribution.get('Author') or row.get('status') != 'downloaded'
                or row.get('path') != 'images/validation/'+identity+'.jpg'
                or len(row.get('sha256', '')) != 64):
            raise ValueError('A held-out frame lacks verifiable public origin metadata')
    return root, rows, sha(ready.read_bytes())


def public_photo(root, row):
    path = root/row['path']
    if any(parent.is_symlink() for parent in (path, path.parent, path.parent.parent)):
        raise ValueError('Public image path must not be a symlink')
    if not path.is_file() or not 0 < path.stat().st_size <= 12_000_000:
        raise ValueError('Public image is absent or outside the byte limit')
    content = path.read_bytes()
    if sha(content) != row['sha256'] or len(content) != row['bytes']:
        raise ValueError('Public image SHA/bytes mismatch')
    return content


def active(cache):
    path = cache/'import-diagnostics/campaigns.sqlite3'
    if path.is_symlink():
        raise ValueError('Diagnostic database must not be a link')
    with sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=.3) as db:
        return db.execute('SELECT count(*) FROM attempts WHERE finished IS NULL').fetchone()[0]


@contextmanager
def model_gate(cache):
    import fcntl
    path = cache/'learning/model-resource.lock'
    if not path.is_file() or path.is_symlink():
        raise ValueError('Existing shared model-resource lock required')
    descriptor = os.open(path, os.O_RDWR|getattr(os, 'O_NOFOLLOW', 0))
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX|fcntl.LOCK_NB)
        if active(cache) != 0:
            raise ValueError('An import is active; benchmark did not start')
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def stats(values):
    ordered = sorted(values)
    if not ordered:
        return {'n': 0}
    return {'n': len(ordered), 'p50': ordered[math.ceil(len(ordered)*.5)-1],
            'p95': ordered[math.ceil(len(ordered)*.95)-1], 'min': ordered[0], 'max': ordered[-1],
            'mean': sum(ordered)/len(ordered), 'percentileMethod': 'nearest-rank'}


def signature(result):
    vector, kinds = [], []
    for row in sorted(result.get('candidates', []), key=lambda row: row['id']):
        kinds.append(row['id']); vector.append(row['confidence'])
        for evidence in row.get('evidence', []):
            vector.append(evidence.get('confidence', 0)); vector.extend(evidence.get('region', []))
    fingerprint = sha(json.dumps({'ids': kinds, 'values': [round(value, 6) for value in vector]}, sort_keys=True).encode())
    return {'ids': kinds, 'values': vector, 'sha256': fingerprint}


def worker(args):
    root, rows, _ = dataset(args.cache)
    sys.path.insert(0, str(args.source_root/'cli'))
    import trained_photo_detector as detector
    if detector.MODEL_SHA != EXPECTED_MODEL:
        raise ValueError('Runtime detector does not pin the selected model')
    outputs = []
    indices = list(range(len(rows))) if args.worker == 'warm' else [args.index]
    for index in indices:
        content = public_photo(root, rows[index])
        began = time.perf_counter()
        result = detector._infer(args.cache, content, 'image/jpeg')
        elapsed = time.perf_counter()-began
        if result.get('status') != 'review' or result.get('autoApply') is not False or any(candidate.get('autoEligible') for candidate in result.get('candidates', [])):
            raise ValueError('Review-only model policy changed')
        outputs.append({'publicImageId': rows[index]['imageId'], 'seconds': elapsed,
                        'peakRssBytes': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                        'signature': signature(result)})
    import onnxruntime
    binary = next((Path(onnxruntime.__file__).parent/'capi').glob('onnxruntime_pybind11_state*.so'))
    result = {'machine': platform.machine(), 'python': platform.python_version(),
              'executable': sys.executable, 'baseExecutable': sys._base_executable,
              'packages': {name: importlib.metadata.version(name) for name in ('numpy', 'Pillow', 'onnxruntime')},
              'ortBinary': str(binary), 'ortBinarySha256': sha(binary.read_bytes()),
              'peakRssBytes': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
              'outputs': outputs}
    print(json.dumps(result), flush=True)


def stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait(timeout=1)


def launch(command, timeout=12):
    started = time.perf_counter()
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
        if process.returncode:
            raise ValueError('Benchmark worker failed: '+stderr[-700:])
        return json.loads(stdout), time.perf_counter()-started
    finally:
        stop(process)


def checked_command(command):
    result = subprocess.run(command, capture_output=True, text=True, timeout=15)
    return {'exitCode': result.returncode, 'output': (result.stdout+result.stderr).strip().replace(str(Path.home()),'<user-home>')[:1500]}


def runtime_receipt(first):
    return {'machine': first['machine'], 'python': first['python'], 'packages': first['packages'],
            'pythonCodeSignature': checked_command(['/usr/bin/codesign', '--verify', '--strict', '--verbose=2', first['baseExecutable']]),
            'pythonArchitectures': checked_command(['/usr/bin/file', '-L', first['baseExecutable']]),
            'ortCodeSignature': checked_command(['/usr/bin/codesign', '--verify', '--strict', '--verbose=2', first['ortBinary']]),
            'ortArchitectures': checked_command(['/usr/bin/file', '-L', first['ortBinary']]),
            'ortBinarySha256': first['ortBinarySha256']}


def browser_worker():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='chrome', headless=True)
        try:
            page = browser.new_page(viewport={'width':1200,'height':800})
            page.set_content('<style>body{background:#08172b;color:#dac080;display:grid;grid-template-columns:repeat(4,1fr);gap:20px}article{height:130px;background:#173354;animation:move .8s infinite alternate}@keyframes move{to{transform:translateY(12px)}}</style>'+''.join('<article>PUBLIC BENCHMARK '+str(index)+'</article>' for index in range(16))+'<canvas width="500" height="120"></canvas><script>window.gaps=[];let last=performance.now();function frame(now){gaps.push(now-last);last=now;const c=document.querySelector("canvas").getContext("2d");c.fillStyle=`hsl(${now%360} 60% 50%)`;c.fillRect(0,0,500,120);requestAnimationFrame(frame);}requestAnimationFrame(frame);</script>')
            print(json.dumps({'ready': True, 'browserVersion': browser.version, 'method': 'isolated-headless-Chrome synthetic CSS/canvas page; no user browser profile or network'}), flush=True)
            began = time.monotonic()
            while time.monotonic()-began < 90:
                page.wait_for_timeout(100)
                if select.select([sys.stdin], [], [], 0)[0]:
                    break
            result = page.evaluate('({frames:gaps.length,gaps:gaps.slice(1)})')
            print(json.dumps({'frames': result['frames'], 'frameGapMs': stats(result['gaps'])}), flush=True)
        finally:
            browser.close()


def controller(args):
    if sys.platform != 'darwin' or platform.machine() != 'arm64':
        raise ValueError('Controller must run natively on the actual Mac')
    root, rows, ready_sha = dataset(args.cache)
    def command(architecture, python, mode='one', index=0):
        return ['/usr/bin/arch', '-'+architecture, str(python), str(Path(__file__).resolve()),
                '--cache', str(args.cache), '--source-root', str(args.source_root), '--worker', mode, '--index', str(index)]
    receipt = {'kind':'actual-public-photo-runtime-benchmark', 'version':1, 'performedAtUtc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),
               'model':'redcn-furniture-v1', 'modelSha256':EXPECTED_MODEL, 'sourceManifestSha256':EXPECTED_MANIFEST,
               'portableManifestSha256':ready_sha, 'runtimeSourceSha256':sha((args.source_root/'cli/trained_photo_detector.py').read_bytes()),
               'benchmarkScriptSha256':sha(Path(__file__).read_bytes()),
               'publicDataset':{'source':'Open Images validation','frames':len(rows),'origin':'manifest S3 URL + Flickr landing + CC-BY-2.0 declaration','individualOriginalLicencesIndependentlyVerified':False,'privatePhotosRead':False},
               'hardware':{'model':checked_command(['/usr/sbin/sysctl','-n','hw.model']), 'cpu':checked_command(['/usr/sbin/sysctl','-n','machdep.cpu.brand_string']), 'memoryBytes':checked_command(['/usr/sbin/sysctl','-n','hw.memsize']), 'os':platform.mac_ver()[0]},
               'method':{'processCold':'new explicit-architecture Python worker per frame, actual trained_photo_detector._infer; OS filesystem cache not flushed','inProcessWarm':'actual _infer repeatedly; creates new ONNX session per frame but imports remain warm; diagnostic only, production worker exits per request','threads':{'intraOp':2,'interOp':1},'provider':'CPUExecutionProvider','rssMethod':'Darwin resource.getrusage(RUSAGE_SELF).ru_maxrss bytes','accuracyAcceptance':False},
               'phases':{},'activityGuards':[]}
    all_outputs = {}
    with model_gate(args.cache):
        for architecture, python in (('arm64',args.arm_python),('x86_64',args.x86_python)):
            if active(args.cache) != 0:
                raise ValueError('Import became active; stopping benchmark')
            receipt['activityGuards'].append({'phase':architecture,'activeImports':0,'sharedModelResource':'exclusive-held'})
            print('PHASE '+architecture,flush=True)
            outputs, wall, peaks, first = [], [], [], None
            for index in range(len(rows)):
                if active(args.cache) != 0:
                    raise ValueError('Import became active; stopping benchmark')
                result, seconds = launch(command(architecture,python,index=index))
                if result['machine'] != architecture:
                    raise ValueError('Worker architecture mismatch')
                first = first or result; outputs.extend(result['outputs']); wall.append(seconds); peaks.append(result['peakRssBytes'])
                if (index+1)%20 == 0:
                    print('PROGRESS '+architecture+' '+str(index+1)+'/'+str(len(rows)),flush=True)
            all_outputs[architecture] = outputs
            receipt['phases'][architecture] = {'runtime':runtime_receipt(first),'processColdFirst':{'workerWallSeconds':wall[0],'inferSeconds':outputs[0]['seconds'],'peakRssBytes':peaks[0]},
                'freshProcessAll80':{'workerWallSeconds':stats(wall),'inferSeconds':stats([row['seconds'] for row in outputs]),'peakRssBytes':stats(peaks),'timeouts':0,'successfulReviewOnlyFrames':len(outputs)},
                'publicFrameIds':[row['publicImageId'] for row in outputs]}
            if active(args.cache) != 0:
                raise ValueError('Import became active before warm phase')
            warm, warm_wall = launch(command(architecture,python,mode='warm'),timeout=90)
            receipt['phases'][architecture]['inProcessWarmDiagnostic'] = {'wallSeconds':warm_wall,'inferSeconds':stats([row['seconds'] for row in warm['outputs'][1:]]),'peakRssBytes':warm['peakRssBytes'],'frames':len(warm['outputs']),'firstInferSeconds':warm['outputs'][0]['seconds']}
        arm, intel = all_outputs['arm64'], all_outputs['x86_64']
        equal_ids, equal_rounded, comparable, maximum = 0, 0, 0, 0.0
        for left,right in zip(arm,intel):
            a,b = left['signature'],right['signature']
            equal_ids += a['ids']==b['ids']; equal_rounded += a['sha256']==b['sha256']
            if a['ids']==b['ids'] and len(a['values'])==len(b['values']):
                comparable += 1; maximum=max(maximum,max((abs(a-b) for a,b in zip(a['values'],b['values'])),default=0))
        receipt['architectureComparison']={'frames':len(arm),'sameFeatureIds':equal_ids,'sameRounded6Signature':equal_rounded,'comparableNumericVectors':comparable,'maximumAbsoluteNumericDifference':maximum,'meaning':'Numerical execution comparison, not correctness against annotations'}
        if active(args.cache) != 0:
            raise ValueError('Import became active before browser coexistence')
        print('PHASE browser-coexistence',flush=True)
        browser = subprocess.Popen(command('x86_64',args.x86_python,mode='browser'),stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            if not select.select([browser.stdout], [], [], 15)[0]:
                raise ValueError('Isolated browser did not report readiness in 15 seconds')
            ready = json.loads(browser.stdout.readline())
            if not ready.get('ready'):
                raise ValueError('Isolated browser did not become ready')
            coexist = []
            for index in range(20):
                if active(args.cache) != 0:
                    raise ValueError('Import became active during coexistence')
                result, seconds = launch(command('arm64',args.arm_python,index=index))
                coexist.append({'wall':seconds,'infer':result['outputs'][0]['seconds'],'rss':result['peakRssBytes']})
            stdout,stderr = browser.communicate(input='stop\n',timeout=10)
            if browser.returncode:
                raise ValueError('Isolated browser failed: '+stderr[-300:])
            browser_metrics = json.loads(stdout.strip())
            receipt['browserCoexistence']={**ready,**browser_metrics,'photoFrames':20,'workerWallSeconds':stats([row['wall'] for row in coexist]),'inferSeconds':stats([row['infer'] for row in coexist]),'peakRssBytes':stats([row['rss'] for row in coexist]),'activeImports':0,'sharedModelResource':'exclusive-held'}
        finally:
            stop(browser)
        print('PHASE bounded-timeouts',flush=True)
        triggered, remaining = 0, []
        for _ in range(3):
            if active(args.cache) != 0:
                raise ValueError('Import became active before timeout test')
            process = subprocess.Popen(command('arm64',args.arm_python,index=0),stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            try:
                process.communicate(timeout=.001)
            except subprocess.TimeoutExpired:
                triggered += 1
            finally:
                stop(process)
            if process.poll() is None:
                remaining.append(process.pid)
        receipt['timeoutChecks']={'attempts':3,'deadlineSeconds':.001,'triggered':triggered,'workersStillRunning':remaining,'meaning':'Real worker launch terminated at explicit deadline, not accuracy or network timeout'}
        receipt['activityGuards'].append({'phase':'finished','activeImports':active(args.cache),'sharedModelResource':'exclusive-held'})
    receipt['limitations']=['Cold means new process; OS disk cache was not cleared.','RSS is worker high-water resident set, not the sum of browser, server and every descendant.','Browser coexistence uses actual isolated Chrome on synthetic animation/canvas content, not the user browser profile or a listing import.','Runtime timing does not accept furniture accuracy, cross-view instance deduplication, Chinese content quality or watermark accuracy.','Production remains the x86 runtime; this benchmark does not activate the ARM candidate or change its model configuration.']
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
    print('COMPLETE '+str(args.output),flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--cache',type=Path,required=True)
    parser.add_argument('--source-root',type=Path,required=True)
    parser.add_argument('--arm-python',type=Path)
    parser.add_argument('--x86-python',type=Path)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--worker',choices=('one','warm','browser'))
    parser.add_argument('--index',type=int,default=0)
    args = parser.parse_args()
    if args.worker == 'browser':
        browser_worker()
    elif args.worker:
        worker(args)
    else:
        if not args.arm_python or not args.x86_python or not args.output:
            parser.error('Controller needs both runtimes and output path')
        controller(args)


if __name__ == '__main__':
    main()
