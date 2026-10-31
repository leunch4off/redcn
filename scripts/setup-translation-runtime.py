#!/usr/bin/env python3
"""Install an isolated native local translation worker, without Torch or Stanza.

Downloads the two official Argos package files (~227 MB), retaining upstream
notices and extracting inference files only. Does not modify the app's .venv.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import shlex
import ssl
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
INDEX_URL = 'https://raw.githubusercontent.com/argosopentech/argospm-index/main/index.json'
MODELS = [
 {'name':'translate-ru_en-1_9','from_code':'ru','to_code':'en','package_version':'1.9',
  'url':'https://argos-net.com/v1/translate-ru_en-1_9.argosmodel','size':156239112,
  'license':'Not explicitly stated for the whole model in upstream package; retain README training-data notices.'},
 {'name':'translate-en_zh-1_9','from_code':'en','to_code':'zh','package_version':'1.9',
  'url':'https://argos-net.com/v1/translate-en_zh-1_9.argosmodel','size':70743021,
  'license':'Upstream README: original OPUS model CC-BY-4.0.'},
]
ALLOWED = {'README.md','metadata.json','sentencepiece.model','model/model.bin',
           'model/config.json','model/shared_vocabulary.json','model/shared_vocabulary.txt'}


def emit(message):
    print(message, flush=True)


def context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def request(url):
    return urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0 (redcn-local-translation-setup)'})


def download(url, target, expected_size):
    digest = hashlib.sha256(); size = 0; last = 0
    with urllib.request.urlopen(request(url), context=context(), timeout=45) as response, target.open('wb') as stream:
        if response.status != 200:
            raise ValueError('Unexpected HTTP status '+str(response.status))
        while True:
            chunk = response.read(1024*1024)
            if not chunk:
                break
            size += len(chunk)
            if size > expected_size:
                raise ValueError('Model package exceeds expected size')
            digest.update(chunk); stream.write(chunk)
            if time.monotonic()-last>2:
                emit(f'  downloaded {size/1_000_000:.1f} / {expected_size/1_000_000:.1f} MB')
                last = time.monotonic()
    if size != expected_size:
        raise ValueError(f'Model size mismatch: {size} != {expected_size}')
    return digest.hexdigest()


def extract_package(archive, staging, model):
    prefix = model['name']+'/'
    found = set(); total = 0
    with zipfile.ZipFile(archive) as package:
        for info in package.infolist():
            name = PurePosixPath(info.filename)
            if name.is_absolute() or '..' in name.parts or '\\' in info.filename:
                raise ValueError('Unsafe archive path')
            if stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError('Archive symlinks are not allowed')
            if info.is_dir() or not info.filename.startswith(prefix):
                continue
            relative = info.filename[len(prefix):]
            if relative not in ALLOWED:
                continue  # Deliberately omit Stanza, not required for inference.
            total += info.file_size
            if total > 600_000_000 or info.file_size > 500_000_000:
                raise ValueError('Expanded model exceeds limit')
            destination = staging/relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            with package.open(info) as source, destination.open('wb') as target:
                shutil.copyfileobj(source, target, 1024*1024)
            found.add(relative)
    required = {'metadata.json','sentencepiece.model','model/model.bin','model/config.json','README.md'}
    if not required <= found:
        raise ValueError('Incomplete model archive: '+','.join(sorted(required-found)))
    metadata = json.loads((staging/'metadata.json').read_text())
    for key in ('from_code','to_code','package_version'):
        if metadata.get(key) != model[key]:
            raise ValueError('Package metadata mismatch: '+key)


def native_python():
    if platform.system() != 'Darwin':
        return [sys.executable]
    # Launch a native ARM interpreter without changing architecture of the app env.
    candidates = ['/opt/homebrew/bin/python3','/usr/bin/python3',sys.executable]
    for candidate in dict.fromkeys(candidates):
        if not Path(candidate).exists():
            continue
        command = ['/usr/bin/arch','-arm64',candidate]
        probe = subprocess.run(command+['-c','import platform;print(platform.machine())'],capture_output=True,text=True)
        if probe.returncode == 0 and probe.stdout.strip() == 'arm64':
            return command
    raise RuntimeError('Native ARM Python 3 is required for the isolated worker')


def enforce_native_launcher(runtime):
    if platform.system() != 'Darwin':
        return
    python = runtime/'bin/python'
    universal = runtime/'bin/python-universal'
    if python.is_file() and not python.is_symlink() and '# redcn native ARM launcher' in python.read_text(errors='ignore')[:200]:
        return
    if universal.exists():
        raise RuntimeError('Unexpected existing python-universal launcher target')
    python.rename(universal)
    python.write_text('#!/bin/sh\n# redcn native ARM launcher\nexec /usr/bin/arch -arm64 '+shlex.quote(str(universal))+' "$@"\n')
    python.chmod(0o755)


def setup(args):
    runtime = Path(args.runtime).expanduser().resolve()
    models = Path(args.models).expanduser().resolve()
    emit('Installing isolated translation runtime: '+str(runtime))
    if not (runtime/'bin/python').exists():
        subprocess.run(native_python()+['-m','venv',str(runtime)],check=True)
    enforce_native_launcher(runtime)
    python = runtime/'bin/python'
    # Binary wheels avoid compiling or pulling heavyweight training frameworks.
    subprocess.run([str(python),'-m','pip','install','--disable-pip-version-check','--force-reinstall','--only-binary=:all:',
                    'ctranslate2>=4.6,<5','sentencepiece>=0.2,<0.3'],check=True)
    subprocess.run([str(python),'-m','pip','install','--disable-pip-version-check',
                    'opencc-python-reimplemented==0.1.7'],check=True)
    if platform.system() == 'Darwin':
        architecture = subprocess.check_output([str(python),'-c','import platform;print(platform.machine())'],text=True).strip()
        if architecture != 'arm64':
            raise RuntimeError('Refusing non-native translation runtime: '+architecture)
    emit('Checking official Argos package index')
    with urllib.request.urlopen(request(INDEX_URL), context=context(), timeout=30) as response:
        raw = response.read(2_000_001)
    if len(raw)>2_000_000:
        raise ValueError('Official model index exceeds expected size')
    index = json.loads(raw)
    if not isinstance(index,list):
        raise ValueError('Unexpected official index format')
    models.mkdir(parents=True, exist_ok=True)
    manifest_path = models/'redcn-models.json'
    old = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    installed = []
    for model in MODELS:
        matches = [row for row in index if all(row.get(key)==model[key] for key in ('from_code','to_code','package_version'))]
        if not any(model['url'] in row.get('links',[]) for row in matches):
            raise ValueError('Pinned package is no longer in the official index: '+model['name'])
        destination = models/model['name']
        previous = next((row for row in old.get('models',[]) if row.get('name')==model['name']),None)
        sidecar = destination/'.redcn-package.json'
        if previous is None and sidecar.is_file():
            previous = json.loads(sidecar.read_text())
        if previous and any(previous.get(key)!=model[key] for key in ('name','url','size')):
            raise ValueError('Existing model provenance does not match pinned upstream package')
        if previous and all((destination/name).is_file() for name in ('metadata.json','sentencepiece.model','model/model.bin','model/config.json','README.md')):
            emit('Already installed: '+model['name']);installed.append(previous);continue
        emit('Downloading official model: '+model['name'])
        with tempfile.TemporaryDirectory(prefix='.redcn-model-',dir=models) as work:
            work = Path(work); archive = work/'package.zip'; staging = work/'extracted'
            checksum = download(model['url'],archive,model['size'])
            extract_package(archive,staging,model)
            record = {**model,'sha256':checksum,'hashSource':'computed after TLS download; upstream index provides no published checksum'}
            (staging/'.redcn-package.json').write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
            if destination.exists():
                raise ValueError('Incomplete existing model directory; move it aside before retrying: '+str(destination))
            staging.rename(destination)
        installed.append(record)
    manifest = {'version':1,'indexUrl':INDEX_URL,'models':installed,
                'licenseNotice':'Local inference install. Keep original README files. No claim that software MIT license covers model weights.'}
    temporary = manifest_path.with_suffix('.tmp');temporary.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n');temporary.replace(manifest_path)
    emit('Verifying runtime and model status')
    subprocess.run([str(python),str(ROOT/'cli/nmt_worker.py'),'--models',str(models),'--status'],check=True)
    emit('Ready. Local translations remain review previews; app .venv was not modified.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime',default=str(ROOT/'.venv-translate'))
    parser.add_argument('--models',default=os.environ.get('REDCN_TRANSLATION_MODELS',str(Path.home()/'.cache/redcn/translation-models')))
    args = parser.parse_args()
    try:
        setup(args)
    except Exception as error:
        print('Translation setup failed: '+str(error),file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
