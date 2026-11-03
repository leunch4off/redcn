"""Evaluate the existing fixed detector on public images with human negatives.

No controller, listing import, provider change or training. Optional extra frames
come only from official validation metadata with explicit CC-BY-2.0 attribution.
"""
import argparse
from contextlib import contextmanager
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import re
import socket
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'cli'))
from learning_scheduler import model_resource
from photo_quality_dataset import (class_names, target_mids, human_labels,
    category_truth, presence_metrics, SCENE_NAMES)


def module():
    path = ROOT/'scripts/pilot-open-vocabulary-furniture.py'
    spec = importlib.util.spec_from_file_location('existing_public_photo_protocol', path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def idle():
    with urllib.request.urlopen('http://127.0.0.1:8765/api/local/health', timeout=3) as response:
        state = json.load(response)
    if state.get('activeJobs') != 0:
        raise ValueError('Действующий импорт или недоступная проверка активности')


@contextmanager
def offline_except_health():
    original = socket.socket.connect
    def connect(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6) and not (isinstance(address, tuple) and address[0] == '127.0.0.1' and address[1] == 8765):
            raise OSError('Only the own-server read-only health route is available during evaluation')
        return original(sock, address)
    socket.socket.connect = connect
    try:
        yield
    finally:
        socket.socket.connect = original


def run(args):
    target = Path(args.output)
    if target.exists():
        raise ValueError('Не перезаписывайте прошлые измерения')
    protocol = module()
    classes, rows = protocol.public_dataset(args.manifest)
    names = class_names(args.class_names)
    targets = target_mids(classes, names)
    scene_mids = {name: names[name] for name in SCENE_NAMES if name in names}
    wanted = {mid for values in targets.values() for mid in values} | set(scene_mids.values())
    labels, conflicts, ignored = human_labels(args.human_labels, wanted)
    hashes = {str(path): protocol.digest(path) for path in (args.manifest, args.class_names, args.human_labels)}
    # Some categories have independent human image-level labels but no output
    # neuron in this model. They remain explicitly unsupported, not "absent".
    extra = []
    if args.extra_images:
        folder = Path(args.extra_images)
        manifest_path = folder/'manifest.json'
        value = json.loads(manifest_path.read_text())
        train_ids = {row['imageId'] for row in json.loads(Path(args.manifest).read_text())['splits']['train']}
        known = {row['imageId'] for row in rows}
        for raw in value.get('images', []):
            identity = raw['imageId']
            if not isinstance(identity, str) or not re.fullmatch(r'[a-f0-9]{16}', identity):
                raise ValueError('Неверный ID дополнительного публичного кадра')
            if identity in train_ids or identity in known or len(extra) >= 12:
                raise ValueError('Пересечение либо слишком много дополнительных кадров')
            if raw['sourceUrl'] != 'https://open-images-dataset.s3.amazonaws.com/validation/'+identity+'.jpg' or raw.get('license') != 'https://creativecommons.org/licenses/by/2.0/':
                raise ValueError('Нет разрешённого происхождения дополнительного кадра')
            path = folder/(identity+'.jpg')
            protocol.verified_file(path, raw['sha256'], 12_000_000)
            extra.append({'imageId': identity, 'sha256': raw['sha256'], 'resolvedPath': str(path), 'boxes': [], 'verifiedClassIndices': []})
            known.add(identity)
        hashes[str(manifest_path)] = protocol.digest(manifest_path)
    model = protocol.verified_file(Path(args.cache)/'tools/photo-training/20261101-first-pass/run-01/model.onnx', protocol.BASELINE_SHA, 8_000_000)
    metric = protocol.metrics_module()
    supported = [row['id'] for row in classes]
    records, box_records = [], []
    start = time.monotonic()
    with model_resource(args.cache, timeout=3), offline_except_health():
        idle()
        import onnxruntime as ort
        ort.disable_telemetry_events()
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        session = ort.InferenceSession(str(model), sess_options=options, providers=['CPUExecutionProvider'])
        for row in rows+extra:
            idle()
            if time.monotonic()-start > 120:
                raise TimeoutError('Истёк ограниченный срок проверки публичных кадров')
            tensor, width, height, ratio, _ = protocol.image_input(row['resolvedPath'], 'baseline')
            output = session.run(None, {session.get_inputs()[0].name: tensor})
            predictions = protocol.decode_baseline(output[0], width, height, ratio, metric)
            truth = {category: category_truth(labels, row['imageId'], mids) for category, mids in targets.items()}
            records.append({'imageId': row['imageId'], 'imageSha256': row['sha256'],
                'truth': truth, 'scenes': [name for name, mid in scene_mids.items() if labels.get((row['imageId'], mid)) is True],
                'predictions': [{'classId': supported[p['classIndex']], 'score': p['score']} for p in predictions if p['score'] >= .35]})
            box_records.append({'boxes': row['boxes'], 'verifiedClassIndices': row['verifiedClassIndices'], 'predictions': predictions})
    if any(protocol.digest(path) != value for path, value in hashes.items()):
        raise ValueError('Аннотации изменились во время проверки')
    report = {'version': 1, 'kind': 'independent-human-image-label-evaluation', 'images': len(records),
        'sources': hashes, 'modelSha256': protocol.BASELINE_SHA, 'scoreThreshold': .35,
        'humanLabelSources': ['verification', 'crowdsource-verification'], 'conflictingLabelsExcluded': len(conflicts),
        'nonhumanOrNonbinaryRowsIgnored': ignored, 'categories': presence_metrics(records, targets, supported),
        'boundingBoxMetrics': dict(zip(supported, metric.detection_metrics(box_records, len(supported), .35))),
        'sceneSupport': {name: sum(name in row['scenes'] for row in records) for name in scene_mids},
        'requestedUnsupportedCategoriesWithoutSourceClass': [key for key in ('hood', 'computer') if key not in targets],
        'records': records, 'seconds': time.monotonic()-start, 'autoApply': False, 'automaticPromotion': False,
        'multiviewIdentityTruthAvailable': False, 'independentHumanApartmentInventoryReview': False,
        'limitations': ['Unknown image/class labels are excluded, never made negative.',
            'Presence metrics and bounding-box localization metrics have distinct denominators.',
            'Unsupported vocabulary is reported explicitly; these categories cannot be added automatically.',
            'No independent cross-view apartment grouping or watermark ground truth is supplied by these labels.']}
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({key: report[key] for key in ('images', 'seconds', 'sceneSupport', 'categories')}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('manifest', 'class-names', 'human-labels', 'cache', 'output'):
        parser.add_argument('--'+name, required=True)
    parser.add_argument('--extra-images')
    run(parser.parse_args())
