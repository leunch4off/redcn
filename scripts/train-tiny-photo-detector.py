"""Bounded, explicit YOLOX-Nano transfer training on an attributed manifest.

This offline tool never opens the redcn cache, changes server settings, downloads
files, or promotes weights. YOLOX source, public pretrained weights and image
files must already exist. Classifier output layers are trained by default;
this is a first supervised transfer experiment, not full detector convergence.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import random
import sys
import time

THRESHOLDS = (.35, .5, .7, .85, .9)


class TrainingCancelled(Exception):
    """A foreground request has priority over this background experiment."""


def check_cancel(cancel_file):
    if cancel_file is not None:
        marker = Path(cancel_file)
        # Existence is sufficient; never read a marker or follow its contents.
        if marker.is_symlink() or marker.exists():
            raise TrainingCancelled('Foreground request interrupted photo training')


def training_batches(rows, batch_size, cancel_file=None):
    for offset in range(0, len(rows), batch_size):
        check_cancel(cancel_file)
        yield rows[offset:offset+batch_size]


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def validate_manifest(value, base):
    """Check labels, image provenance and content-disjoint train/validation."""
    if not isinstance(value, dict) or not isinstance(value.get('classes'), list) or not 1 <= len(value['classes']) <= 80:
        raise ValueError('Manifest needs 1–80 explicitly named classes')
    classes = value['classes']
    ids = [row.get('id') if isinstance(row, dict) else None for row in classes]
    if any(not isinstance(key, str) or not key for key in ids) or len(set(ids)) != len(ids):
        raise ValueError('Class IDs must be unique nonempty strings')
    for row in classes:
        index = row.get('cocoIndex')
        if index is not None and (type(index) is not int or not 0 <= index < 80):
            raise ValueError('Invalid pretrained COCO class index')
    splits = value.get('splits')
    if not isinstance(splits, dict) or not all(isinstance(splits.get(key), list) and splits[key] for key in ('train', 'validation')):
        raise ValueError('Separate nonempty train/validation splits are required')
    seen_ids, seen_hashes = set(), set()
    for split in ('train', 'validation'):
        current_ids, current_hashes = set(), set()
        for row in splits[split]:
            if not isinstance(row, dict) or row.get('sourceSplit') != split:
                raise ValueError('Source split must match training role')
            image_id = row.get('imageId')
            if not isinstance(image_id, str) or not image_id or image_id in seen_ids or image_id in current_ids:
                raise ValueError('Duplicate or invalid image ID across splits')
            source = Path(row.get('path', ''))
            path = source if source.is_absolute() else Path(base) / source
            if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= 12_000_000:
                raise ValueError('Missing or unsafe training image')
            digest = sha256(path)
            if digest in seen_hashes or digest in current_hashes:
                raise ValueError('Duplicate image content across dataset splits')
            if row.get('sha256') and row['sha256'] != digest:
                raise ValueError('Training image checksum changed')
            row['resolvedPath'], row['sha256'] = str(path.resolve()), digest
            boxes = row.get('boxes')
            if not isinstance(boxes, list) or len(boxes) > 100:
                raise ValueError('Image needs at most 100 explicit bounding boxes')
            for box in boxes:
                if not isinstance(box, dict) or type(box.get('classIndex')) is not int or not 0 <= box['classIndex'] < len(classes):
                    raise ValueError('Unknown bounding-box class')
                xyxy = box.get('xyxy')
                if not isinstance(xyxy, list) or len(xyxy) != 4 or any(type(x) not in (float, int) or not math.isfinite(x) for x in xyxy):
                    raise ValueError('Invalid bounding-box coordinates')
                if not 0 <= xyxy[0] < xyxy[2] <= 1 or not 0 <= xyxy[1] < xyxy[3] <= 1:
                    raise ValueError('Bounding boxes must be normalized positive xyxy')
            verified = row.get('verifiedClassIndices', sorted({box['classIndex'] for box in boxes}))
            if not isinstance(verified, list) or any(type(index) is not int or not 0 <= index < len(classes) for index in verified):
                raise ValueError('Invalid verified class list')
            row['verifiedClassIndices'] = sorted(set(verified))
            current_ids.add(image_id); current_hashes.add(digest)
        seen_ids.update(current_ids); seen_hashes.update(current_hashes)
    return value


def iou(a, b):
    intersection = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    union = (a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - intersection
    return intersection / union if union > 0 else 0.


def precision_lower(tp, fp):
    n = tp + fp
    if not n:
        return 0.
    z = 1.959963984540054
    p = tp/n
    return (p + z*z/(2*n) - z*math.sqrt(p*(1-p)/n + z*z/(4*n*n))) / (1 + z*z/n)


def comparison_metrics(baseline, final, classes):
    """Descriptive deltas on exactly the same held-out records/thresholds."""
    result = {}
    for threshold in baseline:
        rows = []
        for class_row, before, after in zip(classes, baseline[threshold], final[threshold]):
            row = {'id': class_row['id'], 'baselineTp': before['tp'], 'finalTp': after['tp'],
                'baselineFp': before['fp'], 'finalFp': after['fp'],
                'baselineFn': before['fn'], 'finalFn': after['fn']}
            for metric in ('precision', 'recall'):
                row[metric+'Delta'] = after[metric]-before[metric] if before[metric] is not None and after[metric] is not None else None
            row['bothMetricsNondecreasing'] = (row['precisionDelta'] is not None and row['recallDelta'] is not None and row['precisionDelta'] >= 0 and row['recallDelta'] >= 0)
            rows.append(row)
        result[threshold] = rows
    return result


def detection_metrics(records, class_count, threshold):
    """One-to-one IoU=.5 matching; unknown image/class pairs are not negatives."""
    totals = [{'tp': 0, 'fp': 0, 'fn': 0, 'verifiedImages': 0, 'verifiedNegativeImages': 0} for _ in range(class_count)]
    for row in records:
        verified = set(row['verifiedClassIndices'])
        for class_index in verified:
            result = totals[class_index]
            result['verifiedImages'] += 1
            targets = [box['xyxy'] for box in row['boxes'] if box['classIndex'] == class_index]
            if not targets:
                result['verifiedNegativeImages'] += 1
            predictions = sorted((p for p in row['predictions'] if p['classIndex'] == class_index and p['score'] >= threshold), key=lambda p: -p['score'])
            matched = set()
            for prediction in predictions:
                options = [(iou(prediction['xyxy'], target), index) for index, target in enumerate(targets) if index not in matched]
                best = max(options, default=(0., -1))
                if best[0] >= .5:
                    result['tp'] += 1; matched.add(best[1])
                else:
                    result['fp'] += 1
            result['fn'] += len(targets) - len(matched)
    for row in totals:
        row['precision'] = row['tp'] / (row['tp'] + row['fp']) if row['tp'] + row['fp'] else None
        row['recall'] = row['tp'] / (row['tp'] + row['fn']) if row['tp'] + row['fn'] else None
        row['precisionWilsonLower95'] = precision_lower(row['tp'], row['fp'])
        row['statisticalGatePassed'] = (row['tp'] >= 75 and row['verifiedImages'] >= 20 and row['verifiedNegativeImages'] >= 20 and row['precisionWilsonLower95'] >= .95 and (row['recall'] or 0) >= .5)
    return totals


def predictions_from_output(output, width, height, size, threshold=.05):
    """Decoded YOLOX cx/cy/w/h output, class winner, class-wise NMS."""
    ratio = min(size/width, size/height)
    candidates = []
    for row in output:
        if len(row) < 6 or any(not math.isfinite(float(value)) for value in row):
            continue
        class_index = max(range(len(row)-5), key=lambda index: row[5+index])
        score = float(row[4] * row[5+class_index])
        if score < threshold:
            continue
        cx, cy, bw, bh = (float(x)/ratio for x in row[:4])
        xyxy = [max(0., min(1., (cx-bw/2)/width)), max(0., min(1., (cy-bh/2)/height)), max(0., min(1., (cx+bw/2)/width)), max(0., min(1., (cy+bh/2)/height))]
        if xyxy[0] < xyxy[2] and xyxy[1] < xyxy[3]:
            candidates.append({'classIndex': class_index, 'score': score, 'xyxy': xyxy})
    kept = []
    for candidate in sorted(candidates, key=lambda row: -row['score']):
        if not any(other['classIndex'] == candidate['classIndex'] and iou(other['xyxy'], candidate['xyxy']) > .45 for other in kept):
            kept.append(candidate)
        if len(kept) >= 200:
            break
    return kept


def prepare_image(row, size):
    import numpy as np
    import torch
    from PIL import Image
    with Image.open(row['resolvedPath']) as original:
        if original.width*original.height > 40_000_000 or min(original.size) < 32 or getattr(original, 'n_frames', 1) != 1:
            raise ValueError('Training image dimensions are invalid')
        # Open Images boxes address the downloaded pixels, not an EXIF rotation.
        if original.getexif().get(274, 1) not in (1, None):
            raise ValueError('EXIF-rotated image requires remapped annotations')
        image = original.convert('RGB')
    width, height = image.size
    ratio = min(size/width, size/height)
    resized = image.resize((max(1, int(width*ratio)), max(1, int(height*ratio))), Image.Resampling.BILINEAR)
    array = np.full((size, size, 3), 114, dtype=np.float32)
    array[:resized.height, :resized.width] = np.asarray(resized)[:, :, ::-1]
    tensor = torch.from_numpy(np.ascontiguousarray(array.transpose(2, 0, 1)))
    labels = []
    for box in row['boxes']:
        x1, y1, x2, y2 = box['xyxy']
        labels.append([box['classIndex'], (x1+x2)*width*ratio/2, (y1+y2)*height*ratio/2, (x2-x1)*width*ratio, (y2-y1)*height*ratio])
    image.close(); resized.close()
    return tensor, labels, width, height


def build_model(classes, checkpoint, source, device, train_objectness=False):
    import torch
    sys.path.insert(0, str(Path(source).resolve()))
    from yolox.models import YOLOX, YOLOPAFPN, YOLOXHead
    channels = [256, 512, 1024]
    model = YOLOX(YOLOPAFPN(.33, .25, in_channels=channels, depthwise=True), YOLOXHead(len(classes), .25, in_channels=channels, depthwise=True))
    for module in model.modules():
        if isinstance(module, torch.nn.BatchNorm2d):
            module.eps = 1e-3; module.momentum = .03
    model.head.initialize_biases(1e-2)
    # Only an explicitly supplied local public checkpoint is unpickled; the
    # caller must pin its source/hash before invoking this offline tool.
    pretrained = torch.load(checkpoint, map_location='cpu', weights_only=True)
    pretrained = pretrained.get('model', pretrained)
    current = model.state_dict()
    loaded = 0
    for name, tensor in pretrained.items():
        if name in current and current[name].shape == tensor.shape and not name.startswith('head.cls_preds.'):
            current[name] = tensor; loaded += 1
    mapped = []
    for class_index, row in enumerate(classes):
        old = row.get('cocoIndex')
        if old is None:
            continue
        for layer in range(3):
            for suffix in ('weight', 'bias'):
                key = 'head.cls_preds.'+str(layer)+'.'+suffix
                current[key][class_index] = pretrained[key][old]
        mapped.append(row['id'])
    model.load_state_dict(current)
    trained_layers = ('head.cls_preds.', 'head.obj_preds.') if train_objectness else ('head.cls_preds.',)
    for name, parameter in model.named_parameters():
        parameter.requires_grad_(name.startswith(trained_layers))
    return model.to(device), {'copiedTensors': loaded, 'mappedCocoClassIds': mapped,
        'trainedLayers': list(trained_layers), 'objectnessTrained': train_objectness,
        'trainableParameters': sum(p.numel() for p in model.parameters() if p.requires_grad),
        'totalParameters': sum(p.numel() for p in model.parameters())}


def evaluate(model, rows, classes, size, device, cancel_file=None):
    import torch
    model.eval()
    records = []
    with torch.no_grad():
        for row in rows:
            check_cancel(cancel_file)
            tensor, _, width, height = prepare_image(row, size)
            output = model(tensor[None].to(device))[0].detach().cpu().tolist()
            records.append({'imageId': row['imageId'], 'boxes': row['boxes'], 'verifiedClassIndices': row['verifiedClassIndices'],
                'predictions': predictions_from_output(output, width, height, size)})
    return {str(threshold): detection_metrics(records, len(classes), threshold) for threshold in THRESHOLDS}


def train(args):
    started = time.monotonic()
    cancel_file = getattr(args, 'cancel_file', None)
    check_cancel(cancel_file)
    import torch
    import numpy as np
    if args.output.exists():
        raise ValueError('Output directory must be new; previous weights will not be overwritten')
    if not 1 <= args.epochs <= 100 or not 1 <= args.max_steps <= 10000 or not 1 <= args.batch_size <= 8 or args.size not in (320, 416):
        raise ValueError('Invalid bounded training settings')
    if not 30 <= args.max_seconds <= 7200 or not 0 < args.learning_rate <= .01:
        raise ValueError('Invalid deadline or learning rate')
    random.seed(args.seed); np.random.seed(args.seed); torch.manual_seed(args.seed)
    torch.set_num_threads(2)
    data = validate_manifest(json.loads(args.manifest.read_text()), args.manifest.parent)
    if args.train_objectness and data.get('fullyAnnotatedTargetClasses') is not True:
        raise ValueError('Objectness training requires fullyAnnotatedTargetClasses=true and exhaustive target-class annotations')
    check_cancel(cancel_file)
    model, initialization = build_model(data['classes'], args.pretrained, args.yolox_source, args.device, args.train_objectness)
    initial_parameters = {name: parameter.detach().cpu().clone() for name, parameter in model.named_parameters() if parameter.requires_grad}
    optimizer = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=args.learning_rate, weight_decay=.0001)
    args.output.mkdir(parents=True)
    receipt = {'version': 1, 'kind': 'bounded-supervised-classifier-finetune' if not args.train_objectness else 'bounded-supervised-head-finetune', 'status': 'running',
        'manifestSha256': sha256(args.manifest), 'pretrainedSha256': sha256(args.pretrained),
        'classes': data['classes'], 'initialization': initialization,
        'trainImages': len(data['splits']['train']), 'validationImages': len(data['splits']['validation']),
        'settings': {'size': args.size, 'batchSize': args.batch_size, 'maxSteps': args.max_steps, 'epochs': args.epochs, 'seed': args.seed, 'device': args.device, 'learningRate': args.learning_rate, 'trainObjectness': args.train_objectness},
        'steps': [], 'evaluations': [], 'autoApprovedIds': [],
        'limitations': ['Output classifier layers are trained; backbone and box regression remain frozen.',
            'Open Images labels may be sparse; unknown image/class pairs are excluded from metrics.',
            ('Objectness is explicitly trained on a manifest declaring exhaustive target-class annotations.' if args.train_objectness else 'Objectness is frozen to avoid sparse-label background gradients; inherited proposals/objectness limit recall for new classes.'),
            'Sparse labels and assignment may still limit classifier learning; this is not full detector convergence.',
            'Positive-only verification cannot establish deployment precision without verified negatives.',
            'No independent rental-domain test acceptance; weights never auto-activated.',
            'Pillow bilinear differs from upstream OpenCV preprocessing.']}
    step = 0
    stopped, cancelled = False, False
    try:
        print(json.dumps({'stage': 'baseline-validation', 'images': receipt['validationImages']}), flush=True)
        receipt['baseline'] = evaluate(model, data['splits']['validation'], data['classes'], args.size, args.device, cancel_file)
        for epoch in range(args.epochs):
            rows = list(data['splits']['train']); random.Random(args.seed+epoch).shuffle(rows)
            model.train(); model.backbone.eval()
            for module in model.modules():
                if isinstance(module, torch.nn.BatchNorm2d):
                    module.eval()
            for batch in training_batches(rows, args.batch_size, cancel_file):
                if step >= args.max_steps or time.monotonic()-started >= args.max_seconds:
                    stopped = True; break
                prepared = [prepare_image(row, args.size) for row in batch]
                images = torch.stack([item[0] for item in prepared]).to(args.device)
                targets = torch.zeros((len(batch), max(1, max(len(item[1]) for item in prepared)), 5), dtype=torch.float32, device=args.device)
                for index, (_, labels, _, _) in enumerate(prepared):
                    if labels:
                        targets[index, :len(labels)] = torch.tensor(labels, dtype=torch.float32, device=args.device)
                optimizer.zero_grad(set_to_none=True)
                before = time.monotonic()
                result = model(images, targets)
                loss = result['total_loss']
                if not torch.isfinite(loss):
                    raise ValueError('Non-finite loss; weights will not be promoted')
                loss.backward(); torch.nn.utils.clip_grad_norm_([p for p in model.parameters() if p.requires_grad], 10.)
                optimizer.step(); step += 1
                record = {'step': step, 'epoch': epoch+1, 'loss': float(loss.detach().cpu()), 'seconds': time.monotonic()-before}
                receipt['steps'].append(record)
                if step == 1 or step % 5 == 0:
                    print(json.dumps({'stage': 'training', **record}), flush=True)
            check_cancel(cancel_file)
            print(json.dumps({'stage': 'validation', 'epoch': epoch+1, 'steps': step}), flush=True)
            metrics = evaluate(model, data['splits']['validation'], data['classes'], args.size, args.device, cancel_file)
            receipt['evaluations'].append({'epoch': epoch+1, 'steps': step, 'metrics': metrics})
            if stopped:
                break
    except TrainingCancelled:
        stopped, cancelled = True, True
    weights = args.output/'weights.pt'
    model = model.cpu().eval()
    torch.save({'model': model.state_dict(), 'classes': data['classes'], 'inputSize': args.size, 'autoApprovedIds': []}, weights)
    receipt.update({'status': 'cancelled' if cancelled else 'trained-unaccepted', 'completedSteps': step,
        'stoppedEarly': stopped, 'cancelledForForeground': cancelled,
        'stoppedAtBound': stopped, 'seconds': time.monotonic()-started,
        'weightsSha256': sha256(weights), 'weightsBytes': weights.stat().st_size})
    receipt['changedTrainableTensors'] = [name for name, parameter in model.named_parameters()
        if name in initial_parameters and not torch.equal(parameter.detach().cpu(), initial_parameters[name])]
    current_evaluation = bool(receipt['evaluations'] and receipt['evaluations'][-1]['steps'] == step)
    receipt['evaluationCoversSavedWeights'] = current_evaluation
    receipt['comparison'] = comparison_metrics(receipt['baseline'], receipt['evaluations'][-1]['metrics'], data['classes']) if current_evaluation else {}
    if args.export_onnx and not cancelled:
        try:
            check_cancel(cancel_file)
            model.head.decode_in_inference = False
            target = args.output/'model.onnx'
            torch.onnx.export(model, torch.zeros(1, 3, args.size, args.size), str(target), input_names=['images'], output_names=['output'], opset_version=11)
            receipt['onnx'] = {'path': target.name, 'sha256': sha256(target), 'bytes': target.stat().st_size,
                'inputShape': [1, 3, args.size, args.size], 'outputEncoding': 'raw-yolox', 'classIds': [row['id'] for row in data['classes']]}
        except TrainingCancelled:
            receipt.update(status='cancelled',stoppedEarly=True,cancelledForForeground=True)
            receipt['onnx'] = {'status': 'skipped', 'reason': 'foreground-request'}
        except (ImportError, RuntimeError, ValueError) as error:
            receipt['onnx'] = {'status': 'failed', 'message': str(error)[:300]}
    (args.output/'trainreceipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({'stage': 'complete', 'status': receipt['status'], 'steps': step, 'autoApprovedIds': [], 'receipt': str(args.output/'trainreceipt.json')}), flush=True)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--pretrained', type=Path, required=True)
    parser.add_argument('--yolox-source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--epochs', type=int, default=3)
    parser.add_argument('--max-steps', type=int, default=100)
    parser.add_argument('--batch-size', type=int, default=2)
    parser.add_argument('--size', type=int, default=320)
    parser.add_argument('--seed', type=int, default=20261101)
    parser.add_argument('--device', choices=('cpu', 'mps'), default='cpu')
    parser.add_argument('--learning-rate', type=float, default=.0001)
    parser.add_argument('--max-seconds', type=int, default=1800)
    parser.add_argument('--export-onnx', action='store_true')
    parser.add_argument('--cancel-file', type=Path, help='Cooperative foreground-priority marker; never deleted by this trainer')
    parser.add_argument('--train-objectness', action='store_true', help='Requires exhaustive target-class annotations declared in manifest; default freezes objectness')
    train(parser.parse_args())


if __name__ == '__main__':
    main()
