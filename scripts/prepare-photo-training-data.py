"""Bounded, attributable Open Images subset for a local furniture pilot.

No private cache or listing imports. Open Images annotations are incomplete
across classes: verifiedClassIndices are positive classes, NOT negative labels.
The dataset's image-license declaration is not individual license verification.
"""
import argparse
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import urllib.request

TRAIN_BOXES = 'https://storage.googleapis.com/openimages/v6/oidv6-train-annotations-bbox.csv'
VAL_BOXES = 'https://storage.googleapis.com/openimages/v5/validation-annotations-bbox.csv'
CLASSES_URL = 'https://storage.googleapis.com/openimages/v7/oidv7-class-descriptions-boxable.csv'
TRAIN_META = 'https://storage.googleapis.com/openimages/2018_04/train/train-images-boxable-with-rotation.csv'
VAL_META = 'https://storage.googleapis.com/openimages/2018_04/validation/validation-images-with-rotation.csv'
LICENSE_SOURCE = 'https://storage.googleapis.com/openimages/web/factsfigures_v7.html#licenses'
# IDs match redcn where their semantics are sufficiently precise. Cabinetry and
# cupboard remain explicit source classes rather than proving a kitchen/wardrobe.
TARGETS = [
    ('sofa', 'Couch', 57), ('bedAny', 'Bed', 59), ('chairs', 'Chair', 56),
    ('tv', 'Television', 62), ('fridge', 'Refrigerator', 72), ('oven', 'Oven', 69),
    ('microwave', 'Microwave oven', 68), ('sink', 'Sink', 71),
    ('table', 'Kitchen & dining room table', 60), ('desk', 'Desk', None),
    ('wardrobe', 'Wardrobe', None), ('washer', 'Washing machine', None),
    ('dishwasher', 'Dishwasher', None), ('gasStove', 'Gas stove', None),
    ('piano', 'Piano', None), ('cabinetry', 'Cabinetry', None),
    ('cupboard', 'Cupboard', None), ('coffeeMachine', 'Coffeemaker', None),
    ('mirror', 'Mirror', None), ('curtains', 'Curtain', None),
    ('coffeeTable', 'Coffee table', None),
]
IMAGE_ID = re.compile(r'[0-9a-f]{16}\Z')


def progress(message):
    print(message, flush=True)


@contextmanager
def public_stream(url, *, curl=False):
    """Own and reap only the curl process created for this bounded request."""
    if not curl:
        with urllib.request.urlopen(url, timeout=60) as source:
            yield source, None
        return
    process = subprocess.Popen(['curl', '--fail', '--location', '--silent',
        '--show-error', '--max-time', '60', url], stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL)
    try:
        yield process.stdout, process
    finally:
        process.stdout.close()
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


def source_record(path, url):
    path = Path(path)
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for data in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(data)
    return {'url': url, 'sha256': digest.hexdigest(), 'bytes': path.stat().st_size,
            'complete': True}


def classes_from(path):
    with Path(path).open(encoding='utf-8-sig', newline='') as source:
        by_name = {row[1]: row[0] for row in csv.reader(source) if len(row) == 2}
    missing = [name for _, name, _ in TARGETS if name not in by_name]
    if missing:
        raise ValueError('Missing official box classes: ' + ', '.join(missing))
    return [{'id': key, 'name': name, 'openImagesId': by_name[name], 'cocoIndex': coco}
            for key, name, coco in TARGETS]


def parse_box(row, class_map):
    """Return positive target box or None; never fabricate negatives/groups."""
    if row.get('LabelName') not in class_map or row.get('Confidence') != '1':
        return None
    if any(row.get(key, '0') != '0' for key in ('IsGroupOf', 'IsDepiction', 'IsInside')):
        return None
    try:
        box = [float(row[key]) for key in ('XMin', 'YMin', 'XMax', 'YMax')]
        if not (0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1):
            return None
    except (KeyError, TypeError, ValueError):
        return None
    return {'classIndex': class_map[row['LabelName']], 'xyxy': box,
            'occluded': row.get('IsOccluded') == '1',
            'truncated': row.get('IsTruncated') == '1'}


def choose_groups(rows, classes, per_class, max_images):
    """Select complete contiguous ImageID groups from sorted upstream CSV.

    A caller imposing a byte cap must stop at a complete group, not pretend a
    partially read final group is exhaustive. A non-sorted source is rejected.
    """
    class_map = {value['openImagesId']: index for index, value in enumerate(classes)}
    counts = [0] * len(classes)
    selected, current, group, unsafe = [], None, [], set()

    def consider(image_id, boxes, non_exhaustive):
        positive = sorted({box['classIndex'] for box in boxes})
        if positive and len(selected) < max_images and any(counts[index] < per_class for index in positive):
            selected.append({'imageId': image_id, 'boxes': boxes,
                             'verifiedClassIndices': [index for index in positive if index not in non_exhaustive],
                             'annotatedPositiveClassIndices': positive,
                             'nonExhaustiveClassIndices': sorted(non_exhaustive)})
            for index in positive:
                counts[index] += 1

    for row in rows:
        image_id = row.get('ImageID', '')
        if not IMAGE_ID.fullmatch(image_id):
            raise ValueError('Invalid upstream ImageID')
        if current is not None and image_id < current:
            raise ValueError('Upstream bbox CSV must be sorted by ImageID')
        if current is not None and image_id != current:
            consider(current, group, unsafe)
            if len(selected) >= max_images or all(count >= per_class for count in counts):
                return selected, counts
            group = []
            unsafe = set()
        current = image_id
        box = parse_box(row, class_map)
        if box:
            group.append(box)
        elif row.get('LabelName') in class_map and row.get('Confidence') == '1':
            unsafe.add(class_map[row['LabelName']])
    if current is not None:
        consider(current, group, unsafe)
    return selected, counts


class BoundedCSV:
    """Hash received bytes and drop final ImageID group on an incomplete read."""
    def __init__(self, stream, limit):
        self.stream, self.limit = stream, limit
        self.bytes, self.digest, self.complete = 0, hashlib.sha256(), False

    def __iter__(self):
        # Keep one group delayed. At a byte cutoff the current final group is
        # withheld; complete prior groups remain safe to select.
        header = self.stream.readline(self.limit + 1)
        if len(header) > self.limit:
            raise ValueError('CSV header exceeds scan budget')
        self.bytes += len(header); self.digest.update(header)
        if not header:
            self.complete = True
            return
        yield header.decode('utf-8-sig')
        group, image_id = [], None
        while self.bytes < self.limit:
            raw = self.stream.readline(self.limit - self.bytes + 1)
            if not raw:
                self.complete = True
                yield from group
                return
            self.bytes += len(raw); self.digest.update(raw)
            if self.bytes > self.limit or not raw.endswith(b'\n'):
                return
            line = raw.decode('utf-8')
            next_id = line.split(',', 1)[0]
            if image_id is not None and next_id != image_id:
                yield from group
                group = []
            image_id = next_id
            group.append(line)


def scan_training(classes, per_class, max_images, max_bytes, source_path=None, *, curl=False):
    def consume(stream, process):
        bounded = BoundedCSV(stream, max_bytes)
        result, counts = choose_groups(csv.DictReader(iter(bounded)), classes, per_class, max_images)
        if process is not None and bounded.complete and process.wait(timeout=3) != 0:
            raise ValueError('Public annotation download ended incompletely')
        return result, counts, bounded
    if source_path:
        with Path(source_path).open('rb') as stream:
            result, counts, bounded = consume(stream, None)
    else:
        last_error = None
        for attempt in range(2):
            try:
                with public_stream(TRAIN_BOXES, curl=curl) as (stream, process):
                    result, counts, bounded = consume(stream, process)
                last_error = None
                break
            except (OSError, ValueError, subprocess.SubprocessError) as error:
                last_error = error
                progress('Annotation transport retry ' + str(attempt + 1))
        if last_error is not None:
            raise ValueError('Public annotation transport failed twice: ' + str(last_error))
    provenance = {'url': TRAIN_BOXES, 'sha256': bounded.digest.hexdigest(),
                  'bytes': bounded.bytes, 'complete': bounded.complete,
                  'hashScope': 'received-prefix', 'scanLimitBytes': max_bytes,
                  'transport': 'local' if source_path else ('curl' if curl else 'urllib')}
    return result, counts, provenance


def attribution(path, selected):
    wanted = {row['imageId'] for row in selected}
    output = {}
    if path is None:
        return output
    with Path(path).open(encoding='utf-8-sig', newline='') as source:
        for row in csv.DictReader(source):
            if row.get('ImageID') in wanted:
                output[row['ImageID']] = {key: row.get(key, '') for key in (
                    'OriginalURL', 'OriginalLandingURL', 'License', 'AuthorProfileURL',
                    'Author', 'Title', 'Rotation')}
    return output


def download_image(workdir, split, row, metadata, allow_unverified, *, curl=False):
    item = {**row, 'sourceSplit': split,
            'sourceUrl': f'https://open-images-dataset.s3.amazonaws.com/{split}/{row["imageId"]}.jpg',
            'attribution': metadata.get(row['imageId'], {}),
            'individualLicenseVerified': False}
    item['rightsStatus'] = ('metadata-license-declared' if item['attribution'].get('License')
                            else 'dataset-declaration-only')
    if not allow_unverified:
        return {**item, 'status': 'excluded', 'error': 'Individual image license not verified; explicit dataset-license allowance required'}
    folder = Path(workdir) / 'images' / split
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / (row['imageId'] + '.jpg')
    last_error = None
    for attempt in range(2):
      try:
        if curl:
            response = subprocess.run(['curl', '--fail', '--location', '--silent',
                '--show-error', '--max-time', '30', '--max-filesize', '12000000', item['sourceUrl']],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=35, check=True)
            data = response.stdout
        else:
            with urllib.request.urlopen(item['sourceUrl'], timeout=30) as source:
                data = source.read(12_000_001)
        if not 0 < len(data) <= 12_000_000:
            raise ValueError('Image exceeds 12 MB')
        from PIL import Image
        with Image.open(io.BytesIO(data)) as image:
            if image.format != 'JPEG' or image.width * image.height > 40_000_000:
                raise ValueError('Invalid or oversized JPEG')
            image.verify()
        target.write_bytes(data)
        return {**item, 'path': str(target.resolve()), 'sha256': hashlib.sha256(data).hexdigest(),
                'bytes': len(data), 'status': 'downloaded', 'transport': 'curl' if curl else 'urllib',
                'downloadAttempts': attempt + 1}
      except (OSError, ValueError, subprocess.SubprocessError) as error:
        last_error = error
    return {**item, 'status': 'failed', 'error': str(last_error)[:200], 'downloadAttempts': 2}


def finalize_ready_manifest(path):
    """Derive an auditable downloaded-only view without changing source data.

    This validates metadata, not pixels or independent model quality. Each
    excluded row is reported explicitly and the exact parent bytes are hashed.
    """
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 10_000_000:
        raise ValueError('Invalid source dataset manifest')
    parent_bytes = path.read_bytes()
    original = json.loads(parent_bytes)
    if not isinstance(original, dict) or not isinstance(original.get('classes'), list) or not isinstance(original.get('splits'), dict):
        raise ValueError('Invalid source dataset schema')
    class_count = len(original['classes'])
    ready = {**original, 'splits': {},
        'sourceSummary': original.get('summary', {}),
        'parentManifestPath': str(path.resolve()),
        'parentManifestSha256': hashlib.sha256(parent_bytes).hexdigest(),
        'readyImageCountsByClass': {}, 'readyBoxCountsByClass': {},
        'selectedDataSplitSha256': {},
        'readiness': 'downloaded-only; training and image-license verification not implied'}
    exclusions, seen = [], set()
    for split, rows in original['splits'].items():
        if not isinstance(rows, list):
            raise ValueError('Invalid source split records')
        accepted, image_counts, box_counts = [], [0] * class_count, [0] * class_count
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError('Invalid image record')
            if row.get('status') != 'downloaded':
                exclusions.append({'split': split, 'imageId': row.get('imageId'),
                    'status': row.get('status'), 'reason': row.get('error') or
                    'Record is not downloaded: ' + str(row.get('status'))})
                continue
            if (not isinstance(row.get('sha256'), str) or not re.fullmatch(r'[a-f0-9]{64}', row['sha256'])
                    or not isinstance(row.get('imageId'), str) or not IMAGE_ID.fullmatch(row['imageId'])
                    or not isinstance(row.get('path'), str) or not row['path']
                    or not isinstance(row.get('boxes'), list)):
                raise ValueError('Downloaded record has invalid metadata')
            if row['imageId'] in seen:
                raise ValueError('Duplicate image ID across source splits')
            seen.add(row['imageId'])
            positive = set()
            for box in row['boxes']:
                if not isinstance(box, dict) or type(box.get('classIndex')) is not int or not 0 <= box['classIndex'] < class_count:
                    raise ValueError('Downloaded record has invalid box class')
                box_counts[box['classIndex']] += 1
                positive.add(box['classIndex'])
            for index in positive:
                image_counts[index] += 1
            accepted.append(row)
        accepted.sort(key=lambda row: row['imageId'])
        ready['splits'][split] = accepted
        ready['readyImageCountsByClass'][split] = image_counts
        ready['readyBoxCountsByClass'][split] = box_counts
        # Deliberately hash the selected image identity, actual file digest and
        # actual boxes, not selection targets or a download count alone.
        hash_rows = [{'imageId': row['imageId'], 'sha256': row['sha256'],
            'boxes': sorted(row['boxes'], key=lambda box: json.dumps(box, sort_keys=True))}
            for row in accepted]
        ready['selectedDataSplitSha256'][split] = hashlib.sha256(json.dumps(
            hash_rows, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    ready['exclusions'] = {'count': len(exclusions),
        'bySplit': {split: sum(row['split'] == split for row in exclusions) for split in original['splits']},
        'rows': exclusions}
    ready['summary'] = {split: {'downloaded': len(rows), 'failed': 0, 'excluded': 0}
        for split, rows in ready['splits'].items()}
    target = path.with_name('dataset-ready.json')
    if target.is_symlink():
        raise ValueError('Ready manifest must not be a symbolic link')
    target.write_text(json.dumps(ready, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    return {'manifest': str(target), 'parentManifestSha256': ready['parentManifestSha256'],
            'summary': ready['summary'], 'exclusions': ready['exclusions']['count']}


def archive_ready_manifest(source_ready_path, destination):
    """Archive only the explicitly supplied public manifest and its images.

    Destination must be new. No environments, credentials, unrelated cache
    files or original listing media are searched or copied. Source stays intact.
    """
    source_ready_path, destination = Path(source_ready_path), Path(destination)
    if (source_ready_path.is_symlink() or not source_ready_path.is_file()
            or source_ready_path.stat().st_size > 10_000_000):
        raise ValueError('Invalid source-ready manifest')
    if destination.exists() or destination.is_symlink():
        raise ValueError('Archive destination must not exist')
    source_bytes = source_ready_path.read_bytes()
    source_hash = hashlib.sha256(source_bytes).hexdigest()
    source = json.loads(source_bytes)
    if not isinstance(source, dict) or not isinstance(source.get('splits'), dict):
        raise ValueError('Invalid ready dataset schema')
    portable = {**source, 'splits': {},
        'upstreamParentManifestSha256': source.get('parentManifestSha256'),
        'parentManifestSha256': source_hash, 'parentManifestPath': 'dataset-source.json',
        'sourceReadyManifestSha256': source_hash, 'sourceReadyManifestFile': 'dataset-source.json',
        'imagePathBase': 'directory containing dataset-ready.json'}
    copies, filenames = [], ['dataset-source.json', 'dataset-ready.json']
    for split, rows in source['splits'].items():
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,40}', split) or not isinstance(rows, list):
            raise ValueError('Invalid archive split')
        portable['splits'][split] = []
        seen_ids = set()
        for row in rows:
            if (not isinstance(row, dict) or row.get('status') != 'downloaded'
                    or not isinstance(row.get('imageId'), str) or not IMAGE_ID.fullmatch(row['imageId'])
                    or not isinstance(row.get('sha256'), str) or not re.fullmatch(r'[a-f0-9]{64}', row['sha256'])
                    or not isinstance(row.get('path'), str) or not row['path']):
                raise ValueError('Archive accepts valid downloaded image records only')
            if row['imageId'] in seen_ids:
                raise ValueError('Duplicate archive image ID')
            seen_ids.add(row['imageId'])
            original_path = Path(row['path'])
            if not original_path.is_absolute():
                original_path = source_ready_path.parent / original_path
            if (original_path.is_symlink() or not original_path.is_file()
                    or not 0 < original_path.stat().st_size <= 12_000_000):
                raise ValueError('Invalid source image for archive')
            with original_path.open('rb') as stream:
                actual_sha = hashlib.file_digest(stream, 'sha256').hexdigest()
            if actual_sha != row['sha256']:
                raise ValueError('Source image SHA-256 does not match manifest')
            relative = Path('images') / split / (row['imageId'] + '.jpg')
            copies.append((original_path, relative, actual_sha))
            filenames.append(relative.as_posix())
            portable['splits'][split].append({**row, 'path': relative.as_posix()})
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.mkdir()  # Never overwrite an existing archive, even in a race.
    for original_path, relative, expected_sha in copies:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original_path, target)
        with target.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != expected_sha:
                raise ValueError('Archived image SHA-256 verification failed')
    (destination / 'dataset-source.json').write_bytes(source_bytes)
    (destination / 'dataset-ready.json').write_text(json.dumps(
        portable, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    return {'archive': str(destination.resolve()), 'manifest': str(destination / 'dataset-ready.json'),
        'sourceReadyManifestSha256': source_hash, 'images': len(copies), 'files': filenames}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workdir', required=True, type=Path)
    parser.add_argument('--max-scan-bytes', type=int, default=150_000_000)
    parser.add_argument('--per-class', type=int, default=8)
    parser.add_argument('--val-per-class', type=int, default=5)
    parser.add_argument('--max-train-images', type=int, default=160)
    parser.add_argument('--max-val-images', type=int, default=80)
    parser.add_argument('--train-boxes', type=Path)
    parser.add_argument('--train-attribution', type=Path)
    parser.add_argument('--allow-dataset-license-not-verified', action='store_true')
    parser.add_argument('--curl', action='store_true', help='Use installed curl for bounded public HTTPS downloads')
    parser.add_argument('--finalize-ready-only', action='store_true',
        help='Derive dataset-ready.json from existing dataset.json; no downloads')
    parser.add_argument('--archive-to', type=Path,
        help='With --finalize-ready-only, archive existing dataset-ready.json into a new directory')
    args = parser.parse_args(argv)
    if args.archive_to and not args.finalize_ready_only:
        parser.error('--archive-to requires --finalize-ready-only')
    if args.finalize_ready_only:
        result = (archive_ready_manifest(args.workdir / 'dataset-ready.json', args.archive_to)
                  if args.archive_to else finalize_ready_manifest(args.workdir / 'dataset.json'))
        progress(json.dumps(result, ensure_ascii=False))
        return 0
    if not (1 <= args.per_class <= 100 and 1 <= args.val_per_class <= 100
            and 1 <= args.max_train_images <= 1000 and 1 <= args.max_val_images <= 1000
            and 1024 <= args.max_scan_bytes <= 250_000_000):
        parser.error('Invalid bounded subset limits')
    classes_path, val_path = args.workdir / 'classes.csv', args.workdir / 'validation-boxes.csv'
    val_meta = args.workdir / 'attribution.csv'
    classes = classes_from(classes_path)
    progress('Scanning bounded official train boxes')
    train, counts, train_source = scan_training(classes, args.per_class,
        args.max_train_images, args.max_scan_bytes, args.train_boxes, curl=args.curl)
    progress('Selected train images: ' + str(len(train)))
    with val_path.open(encoding='utf-8-sig', newline='') as source:
        validation, val_counts = choose_groups(csv.DictReader(source), classes,
            args.val_per_class, args.max_val_images)
    # Official upstream splits must never be relabeled or silently overlap.
    if {row['imageId'] for row in train} & {row['imageId'] for row in validation}:
        raise ValueError('Train/validation image IDs overlap')
    train_meta = attribution(args.train_attribution, train)
    validation_meta = attribution(val_meta if val_meta.is_file() else None, validation)
    manifest = {'version': 1, 'source': 'Open Images V7 boxes', 'classes': classes,
        'splits': {'train': [], 'validation': []}, 'annotationLicense': 'CC-BY-4.0',
        'imageLicenseDeclaration': 'CC-BY-2.0', 'licenseSource': LICENSE_SOURCE,
        'individualLicensesVerified': False,
        'transport': 'curl' if args.curl else 'urllib',
        'explicitDatasetLicenseAllowance': args.allow_dataset_license_not_verified,
        'sources': {'trainBoxes': train_source, 'validationBoxes': source_record(val_path, VAL_BOXES),
            'classes': source_record(classes_path, CLASSES_URL),
            'trainAttribution': source_record(args.train_attribution, TRAIN_META) if args.train_attribution else {'url': TRAIN_META, 'available': False},
            'validationAttribution': source_record(val_meta, VAL_META) if val_meta.is_file() else {'url': VAL_META, 'available': False}},
        'selectedImageCountsByClass': {'train': counts, 'validation': val_counts},
        'limitations': ['Missing annotations are unknown, not negative class labels.',
            'verifiedClassIndices lists positive box classes only; no verified negatives are supplied.',
            'Standard all-class-negative detector loss would treat unannotated objects incorrectly; mask unknown classes or obtain full annotations.',
            'No range hood, air conditioner, vacuum or CIAN/Avito watermark boxes in these target classes.',
            'Public Open Images validation is not an independent rental-apartment acceptance corpus.',
            'Dataset license statements do not verify licenses at original image landing pages.']}
    jobs = [('train', train, train_meta), ('validation', validation, validation_meta)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = {pool.submit(download_image, args.workdir, split, row, metadata,
            args.allow_dataset_license_not_verified, curl=args.curl): split for split, rows, metadata in jobs for row in rows}
        for index, future in enumerate(as_completed(pending), 1):
            split = pending[future]
            manifest['splits'][split].append(future.result())
            if index % 10 == 0 or index == len(pending):
                progress(f'Image preparation {index}/{len(pending)}')
    for split in manifest['splits']:
        manifest['splits'][split].sort(key=lambda row: row['imageId'])
    manifest['summary'] = {split: {status: sum(row['status'] == status for row in rows)
        for status in ('downloaded', 'failed', 'excluded')} for split, rows in manifest['splits'].items()}
    target = args.workdir / 'dataset.json'
    if target.is_symlink():
        raise ValueError('Dataset output must not be a symbolic link')
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    progress(json.dumps({'manifest': str(target), 'summary': manifest['summary']}, ensure_ascii=False))
    progress(json.dumps(finalize_ready_manifest(target), ensure_ascii=False))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as error:
        print('Preparation failed: ' + str(error), file=sys.stderr, flush=True)
        raise SystemExit(1)
