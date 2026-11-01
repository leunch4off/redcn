#!/usr/bin/env python3
"""Install only the pinned optional ARM photo runtime; no provider/config edits."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'cli'))
import photo_native_runtime as native


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--cache', type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != 'darwin':
        parser.error('Installer requires the authorized Mac')
    if native.file_sha(native.PYTHON_BASE) != native.PYTHON_SHA:
        raise ValueError('Expected signed Python 3.11.9 is not installed')
    native._codesign(native.PYTHON_BASE, native.PYTHON_SHA)
    folder = args.cache/native.FOLDER
    if folder.is_symlink():
        raise ValueError('Runtime folder must not be a symlink')
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    receipt = folder/'native-runtime.json'
    if receipt.exists():
        command, identity = native.runtime_command(args.cache)
        print(json.dumps({'alreadyInstalled': True, 'runtime': identity})); return
    env = folder/'venv'
    python = env/'bin/python'
    if env.is_symlink():
        raise ValueError('Runtime venv must not be a symlink')
    if not python.exists():
        subprocess.run(['/usr/bin/arch', '-arm64', str(native.PYTHON_BASE), '-I', '-m', 'venv', str(env)], check=True)
    requirements = folder/'pinned-requirements.txt'
    requirements.write_text(''.join(name+'=='+version+' --hash=sha256:'+digest+'\n' for name,(version,digest) in native.WHEELS.items()))
    report = folder/'install-receipt.json'
    subprocess.run(['/usr/bin/arch', '-arm64', str(python), '-I', '-m', 'pip', '--isolated', 'install', '--index-url', 'https://pypi.org/simple', '--only-binary=:all:',
        '--disable-pip-version-check', '--require-hashes', '--no-deps', '--report', str(report), '-r', str(requirements)], check=True)
    ort = folder/native.ORT_PATH
    if native.file_sha(ort) != native.ORT_SHA:
        raise ValueError('Pinned ARM ORT binary identity does not match its verified wheel')
    native._codesign(python, native.PYTHON_SHA); native._codesign(ort, native.ORT_SHA)
    model = args.cache/native.MODEL_PATH
    if native.file_sha(model) != native.MODEL_SHA:
        raise ValueError('Pinned trained photo model identity changed')
    temporary = receipt.with_suffix('.pending.json')
    temporary.write_text(json.dumps(native.expected_receipt(), sort_keys=True, indent=2)+'\n')
    temporary.chmod(0o600); temporary.replace(receipt)
    command, identity = native.runtime_command(args.cache)
    print(json.dumps({'installed': True, 'runtime': identity, 'providerConfigurationChanged': False,
                      'autoApplyChanged': False, 'modelChanged': False}))


if __name__ == '__main__':
    main()
