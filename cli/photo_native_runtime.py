"""Fixed, signed ARM64 runtime for the trained review-only photo detector.

The optional receipt selects no executable or URL. Paths, Python/model identity
and wheel hashes are fixed by this module. Missing installation keeps the old
runtime; an existing invalid receipt fails explicitly instead of falling back.
"""
import hashlib
import importlib.metadata
import json
from pathlib import Path
import subprocess
import sys
import threading
import time

MODEL_SHA = '650c6d96cd8b9e0c0422174467590b6ec377bc7fefa6c6ff01b0546db5392de5'
PYTHON_BASE = Path('/Library/Frameworks/Python.framework/Versions/3.11/bin/python3.11')
PYTHON_SHA = '4e28e811a89aeac6eed668ae641c7f85f5831e42e8dc6cd9a85a3bcc032ec46a'
ORT_SHA = '27a8a1dd75827f54cfacc7e1a3564cd82b20651698babee3536f6622899ed469'
FOLDER = Path('tools/photo-ai-arm')
ORT_PATH = Path('venv/lib/python3.11/site-packages/onnxruntime/capi/onnxruntime_pybind11_state.so')
MODEL_PATH = Path('tools/photo-training/20261101-first-pass/run-01/model.onnx')
WORLD_MODEL_PATH = Path('tools/yoloe-pilot/world-v2/export-v1/furniture-21-worldv2.onnx')
WORLD_MODEL_SHA = 'eb56e6e04067350e6bb4c9603a4664707ec7620f3a3dda81c1ddd8fd9d580bba'
# Optional WORLD preprocessing extension. The original v1 install receipt stays
# valid; WORLD separately requires the same ARM wheel used in its comparison.
OPENCV_VERSION = '4.11.0.86'
OPENCV_WHEEL_SHA = '432f67c223f1dc2824f5e73cdfcd9db0efc8710647d4e813012195dc9122a52a'
OPENCV_FILES = {
    'cv2/cv2.abi3.so': '199208e3b15e14cce3ed597c602e9a83f66f296870f63d6e80afa79f461bdead',
    'cv2/__init__.py': '936bd94c5a5debf0212fc751af79d3a163652f3e850259df2159db6aa3ed8ad8',
    'cv2/config.py': '1db3463a96f58a82123a847d1507228d9e8a80006de67401ae835917658ebe87',
    'cv2/config-3.py': '9a7aadf724b822001f5e963b01fd4e375d45e2cbbe328d2ca7b42a440c083a1c',
    'cv2/load_config_py3.py': '03dc1f11374a667c9b7db10dd5276d640b0c2d5b2e7866b4ccef772732bd3852',
    'cv2/version.py': '86ce72a8385d1fff358a2a306231f86b769d654e89beca6a164c9c36774f7e5a',
}
WHEELS = {
    'numpy': ('1.26.4', 'edd8b5fe47dab091176d21bb6de568acdd906d1887a4584a15a9a96a1dca06ef'),
    'pillow': ('11.3.0', '9c412fddd1b77a75aa904615ebaa6001f169b26fd467b4be93aded278266b288'),
    'onnxruntime': ('1.23.2', '6f91d2c9b0965e86827a5ba01531d5b669770b01775b23199565d6c1f136616c'),
    'coloredlogs': ('15.0.1', '612ee75c546f53e92e70049c9dbfcc18c935a2b9a53b66085ce9ef6a6e5c0934'),
    'flatbuffers': ('25.12.19', '7634f50c427838bb021c2d66a3d1168e9d199b0607e6329399f04846d42e20b4'),
    'packaging': ('26.3', 'd7193f7c8e4e93f444fde0262bf90af30e16fa0ad0ad44cb553c87339b23cd1c'),
    'protobuf': ('7.36.2', 'cbc70b17ee27e28894c7fee8bb04be1abead49e936bc70eb60052531eee2079e'),
    'sympy': ('1.14.0', 'e091cc3e99d2141a0ba2847328f5479b05d94a6635cb96148ccb3f34671bd8f5'),
    'humanfriendly': ('10.0', '1697e1a8a8f550fd43c2865cd84542fc175a61dcb779b6fee18cf6b6ccba1477'),
    'mpmath': ('1.3.0', 'a0b2b9fe80bbcd81a6647ff13108738cfb482d481d826cc0e02f5b35e5c88d2c'),
}
_SIGNED = set()
_LOCK = threading.Lock()


class NativeRuntimeError(ValueError):
    code = 'PHOTO_NATIVE_RUNTIME_INTEGRITY'


def file_sha(path, limit=60_000_000):
    path = Path(path)
    if not path.is_file() or not 0 < path.stat().st_size <= limit:
        raise NativeRuntimeError('Файл ARM runtime отсутствует или имеет неверный размер')
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(262144), b''):
            digest.update(chunk)
    return digest.hexdigest()


def expected_receipt():
    return {'version': 1, 'kind': 'redcn-trained-photo-arm-runtime', 'architecture': 'arm64',
            'pythonVersion': '3.11.9', 'pythonSha256': PYTHON_SHA, 'ortBinarySha256': ORT_SHA,
            'model': 'redcn-furniture-v1', 'modelSha256': MODEL_SHA,
            'wheels': {key:{'version':version,'sha256':digest} for key,(version,digest) in WHEELS.items()},
            'pythonSignatureVerified': True, 'ortSignatureVerified': True}


def _codesign(path, digest, *, deadline=None):
    key = (str(path), digest)
    remaining = 5 if deadline is None else min(5, deadline-time.monotonic())
    if remaining <= 0 or not _LOCK.acquire(timeout=max(0, remaining)):
        raise NativeRuntimeError('Истёк срок проверки подписи ARM runtime')
    try:
        if key in _SIGNED:
            return
        remaining = 5 if deadline is None else min(5, deadline-time.monotonic())
        if remaining <= 0:
            raise NativeRuntimeError('Истёк срок проверки подписи ARM runtime')
        try:
            checked = subprocess.run(['/usr/bin/codesign', '--verify', '--strict', str(path)],
                                     capture_output=True, timeout=remaining)
        except (OSError, subprocess.SubprocessError) as error:
            raise NativeRuntimeError('Не удалось проверить подпись ARM runtime') from error
        if checked.returncode != 0:
            raise NativeRuntimeError('Подпись ARM runtime не подтверждена')
        _SIGNED.add(key)
    finally:
        _LOCK.release()


def _world_preprocessing(folder, installed=None):
    site = folder/'venv/lib/python3.11/site-packages'
    if installed is None:
        installed = {distribution.metadata['Name'].lower():distribution.version
            for distribution in importlib.metadata.distributions(path=[str(site)])}
    if installed.get('opencv-python') != OPENCV_VERSION:
        raise NativeRuntimeError('Для YOLO-World требуется закреплённый OpenCV ARM runtime')
    if (site/'cv2').is_symlink():
        raise NativeRuntimeError('Пакет OpenCV не должен перенаправляться ссылкой')
    for relative, expected in OPENCV_FILES.items():
        path = site/relative
        if path.is_symlink() or file_sha(path,40_000_000) != expected:
            raise NativeRuntimeError('Контрольная сумма OpenCV runtime не совпадает')
    return {'opencvVersion':OPENCV_VERSION,'opencvBinarySha256':OPENCV_FILES['cv2/cv2.abi3.so']}


def cache_identity(directory, *, model='redcn-furniture-v1'):
    receipt = Path(directory)/FOLDER/'native-runtime.json'
    if not receipt.exists():
        return 'legacy-runtime'
    if receipt.is_symlink():
        return 'invalid-native-runtime'
    try:
        identity = file_sha(receipt, 16000)
        if model == 'redcn-furniture-world-v2':
            extra = _world_preprocessing(Path(directory)/FOLDER)
            identity += ':'+extra['opencvVersion']+':'+extra['opencvBinarySha256']
        return identity
    except (OSError, NativeRuntimeError):
        return 'invalid-native-runtime'


def runtime_command(directory, *, deadline=None, model='redcn-furniture-v1'):
    models = {'redcn-furniture-v1': (MODEL_PATH, MODEL_SHA),
        'redcn-furniture-world-v2': (WORLD_MODEL_PATH, WORLD_MODEL_SHA)}
    if model not in models:
        raise NativeRuntimeError('Неизвестная закреплённая модель')
    selected_model_path, selected_model_sha = models[model]
    folder = Path(directory)/FOLDER
    receipt = folder/'native-runtime.json'
    if not receipt.exists():
        if model == 'redcn-furniture-world-v2':
            raise NativeRuntimeError('YOLO-World требует проверенный native runtime macOS')
        return [sys.executable], {'selection': 'legacy', 'status': 'not-verified'}
    if sys.platform != 'darwin' or folder.is_symlink() or receipt.is_symlink():
        raise NativeRuntimeError('ARM runtime доступен только как проверенная локальная установка macOS')
    if not 0 < receipt.stat().st_size <= 16000:
        raise NativeRuntimeError('Неверный размер паспорта ARM runtime')
    try:
        raw = receipt.read_bytes()
        value = json.loads(raw)
    except (OSError, ValueError) as error:
        raise NativeRuntimeError('Неверный паспорт ARM runtime') from error
    if (not isinstance(value, dict) or json.dumps(value, sort_keys=True, separators=(',', ':'))
            != json.dumps(expected_receipt(), sort_keys=True, separators=(',', ':'))):
        raise NativeRuntimeError('Паспорт ARM runtime не совпадает с закреплённой сборкой')
    python = folder/'venv/bin/python'
    ort = folder/ORT_PATH
    model_file = Path(directory)/selected_model_path
    extra = {}
    try:
        if python.resolve(strict=True) != PYTHON_BASE.resolve(strict=True):
            raise NativeRuntimeError('Путь Python ARM runtime не соответствует закреплённому интерпретатору')
        if ort.is_symlink() or model_file.is_symlink() or any((folder/path).is_symlink() for path in (
                'venv','venv/bin','venv/lib','venv/lib/python3.11','venv/lib/python3.11/site-packages',
                'venv/lib/python3.11/site-packages/onnxruntime','venv/lib/python3.11/site-packages/onnxruntime/capi')):
            raise NativeRuntimeError('Файлы ARM runtime или модели не должны перенаправляться ссылками')
        for target, expected in ((python, PYTHON_SHA), (ort, ORT_SHA), (model_file, selected_model_sha)):
            if file_sha(target) != expected:
                raise NativeRuntimeError('Контрольная сумма ARM runtime или модели не совпадает')
        config = dict(line.split('=',1) for line in (folder/'venv/pyvenv.cfg').read_text().splitlines() if '=' in line)
        config = {key.strip():value.strip() for key,value in config.items()}
        if (config.get('include-system-site-packages') != 'false' or config.get('version') != '3.11.9'
                or Path(config.get('home','')).resolve() != PYTHON_BASE.parent.resolve()
                or 'executable' in config and Path(config['executable']).resolve() != PYTHON_BASE.resolve()):
            raise NativeRuntimeError('ARM окружение должно быть изолировано от внешних пакетов')
        site = folder/'venv/lib/python3.11/site-packages'
        installed = {distribution.metadata['Name'].lower():distribution.version
                     for distribution in importlib.metadata.distributions(path=[str(site)])}
        if any(installed.get(name) != version for name,(version,_) in WHEELS.items()):
            raise NativeRuntimeError('Версии пакетов ARM runtime не соответствуют закреплённым wheels')
        if model == 'redcn-furniture-world-v2':
            extra = _world_preprocessing(folder, installed)
        _codesign(python, PYTHON_SHA, deadline=deadline); _codesign(ort, ORT_SHA, deadline=deadline)
    except NativeRuntimeError:
        raise
    except (OSError, RuntimeError, TypeError, AttributeError, ValueError) as error:
        raise NativeRuntimeError('Не удалось проверить файлы ARM runtime') from error
    return ['/usr/bin/arch', '-arm64', str(python), '-I'], {
        'selection': 'native-arm', 'status': 'verified', 'architecture': 'arm64',
        'pythonVersion': '3.11.9', 'onnxRuntimeVersion': WHEELS['onnxruntime'][0],
        'pythonSha256': PYTHON_SHA, 'ortBinarySha256': ORT_SHA,
        'modelSha256': selected_model_sha, 'receiptSha256': hashlib.sha256(raw).hexdigest(),
        'pythonSignatureVerified': True, 'ortSignatureVerified': True, **extra}
