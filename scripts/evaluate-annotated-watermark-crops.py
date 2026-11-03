"""Crop/redetect every pre-labelled positive, even if the OCR detector misses it.

This separate phase uses the annotation geometry as an oracle; it never claims
that those boxes were detected automatically. Original bytes remain untouched.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'cli'))
import photo_store
from learning_scheduler import model_resource
from watermark_quality import minimal_edge_crop, crop_measurements


def protocol():
    spec = importlib.util.spec_from_file_location('fixed_real_watermark_protocol', ROOT/'scripts/evaluate-real-watermarks.py')
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def run(args):
    fixed = protocol()
    target, folder, manifest_path = Path(args.output), Path(args.images), Path(args.manifest)
    if target.exists() or target.is_symlink():
        raise ValueError('Прежнее измерение не перезаписывается')
    manifest = fixed.load_manifest(manifest_path, folder)
    fingerprints = {path.name: fixed.digest(path) for path in (manifest_path, Path(__file__),
        ROOT/'scripts/evaluate-real-watermarks.py', ROOT/'cli/photo_store.py',
        ROOT/'cli/recognize-photo.swift', ROOT/'cli/watermark_quality.py')}
    rows, start = [], time.monotonic()
    with model_resource(args.cache, timeout=3), fixed.offline():
        for row in manifest['frames']:
            if row['watermarkPresent'] is not True or row['kind'] == 'layout':
                continue
            fixed.idle(args.no_active_server)
            if time.monotonic()-start > args.max_seconds:
                break
            oracle = minimal_edge_crop(row['width'], row['height'], row['marks'])
            result = {'id': row['id'], 'originalSha256': row['sha256'], 'cropSource': 'pre-frozen-visual-annotation-not-detector',
                      'minimalAnnotatedEdgeCrop': oracle, 'automaticApplication': False, 'cleanlinessConfirmed': False}
            if oracle is None or oracle['retainedArea'] < .75 or min(row['width'], row['height']) < photo_store.AUTO_CROP_MIN_SHORT_SIDE:
                result['status'] = 'review-no-crop'
            else:
                original = photo_store.open_photo((folder/row['file']).read_bytes())
                crop = original.crop(oracle['rect']).convert('RGB')
                try:
                    result['geometry'] = crop_measurements(original.width, original.height, oracle['rect'], row['marks'], row['importantRegions'])
                    begin = time.monotonic()
                    try:
                        boxes, engine = photo_store.detect_photo_marks(crop, args.cache,
                            allow_id_footer=row['sourceClass'] == 'user-attachment',
                            deadline=min(start+args.max_seconds, time.monotonic()+args.per_frame_seconds))
                        result['postCropDetected'] = any(float(box.get('confidence', 0)) >= .8 for box in boxes)
                        result['detectorEngine'] = engine
                    except (OSError, ValueError, subprocess.SubprocessError) as error:
                        result['postCropDetectorError'] = type(error).__name__
                    result['seconds'] = time.monotonic()-begin
                    result['status'] = 'requires-review' if result['geometry']['importantRegionCut'] or result.get('postCropDetected') or result.get('postCropDetectorError') else 'known-regions-excluded-needs-editorial-review'
                finally:
                    crop.close()
                    original.close()
            result['originalBytesUnchanged'] = fixed.digest(folder/row['file']) == row['sha256']
            rows.append(result)
    for path in (manifest_path, Path(__file__), ROOT/'scripts/evaluate-real-watermarks.py', ROOT/'cli/photo_store.py', ROOT/'cli/recognize-photo.swift', ROOT/'cli/watermark_quality.py'):
        if fixed.digest(path) != fingerprints[path.name]:
            raise ValueError('Источник или версия проверки изменились')
    requested = sum(row['watermarkPresent'] is True and row['kind'] != 'layout' for row in manifest['frames'])
    report = {'version': 1, 'kind': 'pre-labelled-real-watermark-oracle-crops', 'sourceSha256': fingerprints,
        'annotation': manifest['annotation'], 'requestedPositiveFrames': requested, 'measuredFrames': len(rows),
        'complete': len(rows) == requested, 'frames': rows, 'seconds': time.monotonic()-start,
        'allOriginalBytesUnchanged': all(row['originalBytesUnchanged'] for row in rows),
        'importantRegionCutFrames': sum(bool(row.get('geometry', {}).get('importantRegionCut')) for row in rows),
        'cropFrames': sum('geometry' in row for row in rows), 'automaticApplication': False,
        'limits': ['This oracle phase is not OCR detection recall; no detector success is imputed for a missed mark.',
            'Approximate Codex visual boxes define the geometric minimum, not an independently human-optimal composition.',
            'Post-crop OCR silence cannot confirm every logo is absent.', 'No original or derivative pixels are included in this report.']}
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({key: report[key] for key in ('requestedPositiveFrames', 'measuredFrames', 'cropFrames', 'importantRegionCutFrames', 'seconds', 'allOriginalBytesUnchanged')}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('manifest', 'images', 'cache', 'output'):
        parser.add_argument('--'+name, required=True)
    parser.add_argument('--max-seconds', type=int, choices=range(30, 301), default=180)
    parser.add_argument('--per-frame-seconds', type=int, choices=range(3, 31), default=10)
    parser.add_argument('--no-active-server', action='store_true')
    run(parser.parse_args())
