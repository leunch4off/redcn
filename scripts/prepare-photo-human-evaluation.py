"""Bounded extra public validation frames; never import or read a user photo."""
import argparse
import csv
import hashlib
import io
import json
from pathlib import Path
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'cli'))
from photo_quality_dataset import class_names, target_mids, human_labels, category_truth


def sha(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for part in iter(lambda: stream.read(1024*1024), b''):
            result.update(part)
    return result.hexdigest()


def prepare(args):
    if not args.allow_declared_license:
        raise ValueError('Нужно ранее разрешённое использование лицензии, заявленной официальным набором; индивидуальная проверка Flickr этим не имитируется')
    if sha(args.manifest) != 'dc53ecb5e38e0a87e2fe6084c1e6738ab33c3c7f5016b9b272e7f047b49151ee':
        raise ValueError('Исходный публичный manifest изменился')
    dataset = json.loads(Path(args.manifest).read_text())
    names = class_names(args.class_names)
    targets = {key: value for key, value in target_mids(dataset['classes'], names).items() if key in ('hood', 'computer')}
    mids = {mid for values in targets.values() for mid in values}
    labels, conflicts, ignored = human_labels(args.human_labels, mids)
    known = {row['imageId'] for split in dataset['splits'].values() for row in split}
    known_hashes = {row['sha256'] for split in dataset['splits'].values() for row in split if row.get('sha256')}
    candidates = {key: [identity for identity in sorted({pair[0] for pair in labels})
                        if identity not in known and category_truth(labels, identity, values) is True][:12]
                  for key, values in targets.items()}
    wanted = {identity for values in candidates.values() for identity in values}
    metadata = {}
    with Path(args.image_metadata).open(encoding='utf-8-sig', newline='') as stream:
        for row in csv.DictReader(stream):
            if row.get('ImageID') in wanted:
                metadata[row['ImageID']] = row
    folder = Path(args.output)
    if folder.exists() or folder.is_symlink():
        raise ValueError('Используйте новый каталог, прежние данные не перезаписываются')
    folder.mkdir(parents=True, mode=0o700)
    images, failures, counts, begin = [], [], {}, time.monotonic()
    from PIL import Image
    for category, identities in candidates.items():
        counts[category] = 0
        for identity in identities:
            if counts[category] >= args.per_category or len(images) >= 12 or time.monotonic()-begin > 120:
                break
            row = metadata.get(identity, {})
            if row.get('License') != 'https://creativecommons.org/licenses/by/2.0/':
                failures.append({'imageId': identity, 'reason': 'No explicit CC-BY-2.0 metadata declaration'})
                continue
            if identity in {item['imageId'] for item in images}:
                counts[category] += 1
                continue
            url = 'https://open-images-dataset.s3.amazonaws.com/validation/'+identity+'.jpg'
            try:
                with urllib.request.urlopen(url, timeout=15) as response:
                    content = response.read(12_000_001)
                if len(content) > 12_000_000 or not content.startswith(b'\xff\xd8'):
                    raise ValueError('Not a bounded JPEG photograph')
                digest = hashlib.sha256(content).hexdigest()
                if digest in known_hashes:
                    raise ValueError('Image bytes overlap existing training or validation content')
                with Image.open(io.BytesIO(content)) as image:
                    image.verify()
                path = folder/(identity+'.jpg')
                path.write_bytes(content)
                path.chmod(0o600)
                images.append({'imageId': identity, 'sourceUrl': url, 'sha256': digest, 'bytes': len(content),
                               'license': row['License'], 'individualLicenseVerified': False,
                               'attribution': {key: row.get(key, '') for key in ('OriginalLandingURL', 'Author', 'Title')},
                               'selectionCategory': category, 'annotation': 'official human-verified positive image-level label'})
                known_hashes.add(digest)
                counts[category] += 1
            except (OSError, ValueError) as error:
                failures.append({'imageId': identity, 'reason': str(error)[:200]})
    report = {'version': 1, 'source': 'Open Images official validation subset',
              'selection': 'first IDs in lexicographic order; never selected by model output',
              'perCategoryRequested': args.per_category, 'perCategorySaved': counts,
              'explicitDatasetLicenseAllowance': True, 'individualLicenseVerified': False,
              'sources': {Path(path).name: sha(path) for path in (args.manifest, args.class_names, args.human_labels, args.image_metadata)},
              'missingSourceCategories': [key for key in ('hood', 'computer') if key not in targets],
              'images': images, 'failures': failures,
              'humanAnnotationConflictsExcluded': len(conflicts), 'nonhumanRowsIgnored': ignored}
    (folder/'manifest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({'saved': len(images), 'counts': counts, 'failed': len(failures), 'missingCategories': report['missingSourceCategories']}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('manifest', 'class-names', 'human-labels', 'image-metadata', 'output'):
        parser.add_argument('--'+name, required=True)
    parser.add_argument('--per-category', type=int, choices=range(1, 7), default=3)
    parser.add_argument('--allow-declared-license', action='store_true')
    prepare(parser.parse_args())
