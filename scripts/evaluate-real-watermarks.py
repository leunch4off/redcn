"""Measure frozen visual watermark annotations without editing any original.

Input pixels are authorized, private evaluation assets. Only hashes, dimensions,
labels, geometry, timings and redacted outcomes are written to the report.
"""
import argparse
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import platform
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'cli'))
import photo_store
from learning_scheduler import model_resource
from watermark_quality import region, crop_measurements, presence_counts


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_manifest(path, folder):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 300_000:
        raise ValueError('Нужен небольшой фиксированный manifest')
    value = json.loads(path.read_text())
    annotation = value.get('annotation', {})
    if value.get('version') != 1 or annotation.get('frozenBeforeDetectorOutput') is not True:
        raise ValueError('Сначала зафиксируйте визуальную разметку до вывода детектора')
    if annotation.get('independentHuman') is not False or annotation.get('kind') != 'Codex visual inspection':
        raise ValueError('Этот протокол явно отмечает визуальную AI-разметку, не выдаёт её за человека')
    rows = value.get('frames')
    if not isinstance(rows, list) or not 2 <= len(rows) <= 30:
        raise ValueError('Нужно от 2 до 30 явно разрешённых кадров')
    ids = set()
    for row in rows:
        if not isinstance(row, dict) or not re.fullmatch(r'frame-\d{2}', row.get('id', '')) or row['id'] in ids:
            raise ValueError('Неверный ID кадра')
        ids.add(row['id'])
        if row.get('sourceClass') not in ('user-attachment', 'licensed-public-open-images'):
            raise ValueError('Нет явно разрешённого происхождения кадра')
        if not re.fullmatch(r'[a-z][a-z0-9-]{1,60}', row.get('sourceGroup', '')):
            raise ValueError('Нужна обезличенная группа источника')
        filename = row.get('file', '')
        if not re.fullmatch(r'[a-zA-Z0-9_-]+\.(?:jpg|png|webp)', filename):
            raise ValueError('Нужно локальное имя без частного пути или URL')
        target = folder/filename
        if target.is_symlink() or not target.is_file() or target.stat().st_size > 12_000_000:
            raise ValueError('Неверный или слишком большой разрешённый кадр')
        if not re.fullmatch(r'[a-f0-9]{64}', row.get('sha256', '')) or digest(target) != row['sha256']:
            raise ValueError('Байты разрешённого оригинала изменились')
        if type(row.get('watermarkPresent')) not in (bool, type(None)):
            raise ValueError('Нужно наличие, отсутствие или явная неизвестность метки')
        if row.get('kind') not in ('room', 'product', 'layout') or row.get('contrast') not in ('low', 'medium', 'high', 'none', 'unknown'):
            raise ValueError('Нужны вид кадра и визуальная оценка контраста')
        if not isinstance(row.get('marks'), list) or len(row['marks']) > 6:
            raise ValueError('Неверные визуальные области watermark')
        for mark in row['marks']:
            region(mark)
        if bool(row['marks']) != (row['watermarkPresent'] is True):
            raise ValueError('Визуальное наличие и размеченные области расходятся')
        if not isinstance(row.get('importantRegions'), list) or len(row['importantRegions']) > 8:
            raise ValueError('Нужны ограниченные важные видимые области')
        for important in row['importantRegions']:
            if not isinstance(important.get('label'), str) or not re.fullmatch(r'[a-z][a-z -]{1,60}', important['label']):
                raise ValueError('Нужен общий тип предмета без адреса, текста или ID источника')
            region(important['region'])
    return value


def idle(no_server):
    if no_server:
        if sys.platform == 'darwin':
            raise ValueError('На пользовательском Mac проверка отсутствия импорта обязательна')
        return
    with urllib.request.urlopen('http://127.0.0.1:8765/api/local/health', timeout=3) as response:
        state = json.load(response)
    if state.get('activeJobs') != 0:
        raise ValueError('Нельзя запускать измерение при действующем импорте')


@contextmanager
def offline():
    original = socket.socket.connect
    def connect(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6) and not (
                isinstance(address, tuple) and address[0] == '127.0.0.1' and address[1] == 8765):
            raise OSError('Evaluation permits only the own-server read-only health endpoint')
        return original(sock, address)
    socket.socket.connect = connect
    try:
        yield
    finally:
        socket.socket.connect = original


def redacted_boxes(boxes):
    return [{key: row[key] for key in ('x', 'y', 'width', 'height', 'confidence', 'brand', 'method')
             if key in row} for row in boxes]


def run(args):
    target = Path(args.output)
    checkpoint = target.with_suffix('.checkpoint.jsonl')
    if target.exists() or target.is_symlink() or checkpoint.exists() or checkpoint.is_symlink():
        raise ValueError('Предыдущий отчёт не перезаписывается')
    manifest_path, folder = Path(args.manifest), Path(args.images)
    manifest = load_manifest(manifest_path, folder)
    target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    checkpoint.touch(mode=0o600, exist_ok=False)
    frozen = {path.name: digest(path) for path in (manifest_path, Path(__file__),
        ROOT/'cli/photo_store.py', ROOT/'cli/recognize-photo.swift', ROOT/'cli/watermark_quality.py')}
    records, start, stop_reason = [], time.monotonic(), None
    executable = shutil.which('tesseract')
    software = {'macOS': platform.mac_ver()[0] if sys.platform == 'darwin' else None,
                'tesseractBinarySha256': digest(executable) if sys.platform != 'darwin' and executable else None}
    if executable and sys.platform != 'darwin':
        software['tesseractVersion'] = subprocess.run([executable, '--version'], capture_output=True, text=True, timeout=5).stdout.splitlines()[0]
    with model_resource(args.cache, timeout=3), offline():
        for row in manifest['frames']:
            idle(args.no_active_server)
            if time.monotonic()-start > args.max_seconds:
                stop_reason = 'bounded-total-time-budget'
                break
            image = photo_store.open_photo((folder/row['file']).read_bytes())
            try:
                if image.size != (row['width'], row['height']) or image.width*image.height > 30_000_000:
                    raise ValueError('Размер кадра расходится с фиксированной разметкой')
                record = {key: row[key] for key in ('id', 'sha256', 'width', 'height', 'sourceClass',
                    'sourceGroup', 'kind', 'contrast', 'watermarkPresent', 'marks', 'importantRegions')}
                begin = time.monotonic()
                try:
                    boxes, engine = photo_store.detect_photo_marks(image, args.cache,
                        allow_id_footer=row['sourceClass'] == 'user-attachment',
                        deadline=min(start+args.max_seconds, time.monotonic()+args.per_frame_seconds))
                    boxes = [box for box in boxes if float(box.get('confidence', 0)) >= .8]
                    record.update(detected=bool(boxes), detectorEngine=engine, boxes=redacted_boxes(boxes))
                    proposal = photo_store.propose_crop(image.width, image.height, boxes)
                    record['proposal'] = {key: proposal[key] for key in ('status', 'reason', 'rect', 'retainedArea') if key in proposal}
                    if row['kind'] == 'layout':
                        record['runtimeAction'] = 'skip-layout'
                    elif proposal['status'] == 'detected':
                        rect = proposal['rect']
                        record['crop'] = crop_measurements(image.width, image.height, rect, row['marks'], row['importantRegions'])
                        cropped = image.crop(rect).convert('RGB')
                        try:
                            after, _ = photo_store.detect_photo_marks(cropped, args.cache,
                                allow_id_footer=row['sourceClass'] == 'user-attachment',
                                deadline=min(start+args.max_seconds, time.monotonic()+args.per_frame_seconds))
                            record['postCropDetected'] = any(float(box.get('confidence', 0)) >= .8 for box in after)
                        except (ValueError, OSError, subprocess.SubprocessError):
                            record['postCropDetectorError'] = True
                        finally:
                            cropped.close()
                        record['runtimeAction'] = 'requires-review' if row['watermarkPresent'] is not True or record['crop']['importantRegionCut'] or record.get('postCropDetected') or record.get('postCropDetectorError') else 'known-regions-excluded-needs-editorial-review'
                    else:
                        record['runtimeAction'] = 'requires-review'
                except (ValueError, OSError, subprocess.SubprocessError) as error:
                    # Exception text can contain paths or OCR content; keep the
                    # type only. Failed OCR is never a confirmed negative.
                    record['detectorError'] = type(error).__name__
                    record['runtimeAction'] = 'requires-review'
                record['seconds'] = time.monotonic()-begin
                record['originalBytesUnchanged'] = digest(folder/row['file']) == row['sha256']
                records.append(record)
                with checkpoint.open('a', encoding='utf-8') as stream:
                    stream.write(json.dumps(record, ensure_ascii=False)+'\n')
            finally:
                image.close()
    for path in (manifest_path, Path(__file__), ROOT/'cli/photo_store.py', ROOT/'cli/recognize-photo.swift', ROOT/'cli/watermark_quality.py'):
        if digest(path) != frozen[path.name]:
            raise ValueError('Корпус или версия детектора изменились во время измерения')
    report = {'version': 1, 'kind': 'real-frame-watermark-calibration', 'sourceSha256': frozen,
        'annotation': manifest['annotation'], 'platform': {'system': platform.system(), 'release': platform.release(), 'machine': platform.machine()},
        'software': software, 'requestedFrames': len(manifest['frames']), 'measuredFrames': len(records),
        'unmeasuredFrameIds': [row['id'] for row in manifest['frames'] if row['id'] not in {record['id'] for record in records}],
        'complete': len(records) == len(manifest['frames']), 'stopReason': stop_reason,
        'scoreThreshold': .8, 'metrics': presence_counts(records), 'frames': records,
        'distinctSourceGroups': len({row['sourceGroup'] for row in records}),
        'metricsBySourceClass': {key: presence_counts([row for row in records if row['sourceClass'] == key]) for key in {row['sourceClass'] for row in records}},
        'metricsByKind': {key: presence_counts([row for row in records if row['kind'] == key]) for key in {row['kind'] for row in records}},
        'allOriginalBytesUnchanged': all(row['originalBytesUnchanged'] for row in records),
        'seconds': time.monotonic()-start, 'automaticPromotion': False, 'productionSettingsChanged': False,
        'originalPixelsIncluded': False, 'cropPixelsIncluded': False, 'noDetectionProvesClean': False,
        'limitations': ['Visual annotations are Codex AI inspection, not independent human truth.',
            'Correlated variants of one listing are not independent apartments.',
            'Important-region retention measures rectangle area, not complete aesthetic composition.',
            'Geometric minimum depends on approximate visual boxes and two-pixel padding.',
            'Post-crop no detection does not prove absence of every logo.',
            'False positives/negatives are local to this fixed corpus and detector version.']}
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({key: report[key] for key in ('metrics', 'seconds', 'allOriginalBytesUnchanged', 'distinctSourceGroups')}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('manifest', 'images', 'cache', 'output'):
        parser.add_argument('--'+name, required=True)
    parser.add_argument('--max-seconds', type=int, choices=range(30, 301), default=240)
    parser.add_argument('--per-frame-seconds', type=int, choices=range(3, 31), default=10)
    parser.add_argument('--no-active-server', action='store_true', help='Only isolated non-Mac offline evaluation')
    run(parser.parse_args())
