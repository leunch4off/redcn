"""Inspect an explicit photo manifest without loading a model or changing data.

Only the supplied manifest and its listed image files are read. The JSON report
contains aggregate counts, never image paths, URLs, image IDs or pixel data.
This is a data-readiness diagnostic, not a model acceptance or promotion gate.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import re


SHA256 = re.compile(r'[a-f0-9]{64}\Z')
MIN_TRUE_POSITIVES = 75
MIN_VERIFIED_IMAGES = 20
MIN_NEGATIVE_IMAGES = 20
MIN_PRECISION_LOWER = .95
MIN_RECALL = .5


def file_hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def precision_lower(tp, fp=0):
    n = tp + fp
    if not n:
        return 0.
    z = 1.959963984540054
    p = tp / n
    return (p + z*z/(2*n) - z*math.sqrt(p*(1-p)/n + z*z/(4*n*n))) / (1 + z*z/n)


def indices(row, name, count):
    result = row.get(name, [])
    if (not isinstance(result, list) or len(set(result)) != len(result)
            or any(type(index) is not int or not 0 <= index < count for index in result)):
        raise ValueError('Invalid ' + name)
    return set(result)


def audit_manifest(path, *, verify_images=True):
    """Audit available support; absent labels stay unknown, not negative."""
    path = Path(path)
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= 10_000_000:
        raise ValueError('Manifest must be a regular JSON file of at most 10 MB')
    raw = path.read_bytes()
    manifest = json.loads(raw)
    if not isinstance(manifest, dict):
        raise ValueError('Manifest must be an object')
    classes, splits = manifest.get('classes'), manifest.get('splits')
    if not isinstance(classes, list) or not 1 <= len(classes) <= 80:
        raise ValueError('Manifest needs 1–80 classes')
    class_ids = [item.get('id') if isinstance(item, dict) else None for item in classes]
    if (any(not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', key) for key in class_ids)
            or len(set(class_ids)) != len(class_ids)):
        raise ValueError('Class IDs must be unique simple identifiers')
    if (not isinstance(splits, dict) or not {'train', 'validation'} <= set(splits)
            or set(splits) - {'train', 'validation', 'test'}
            or any(not isinstance(rows, list) or len(rows) > 100_000 for rows in splits.values())):
        raise ValueError('Expected train/validation and optional test lists')

    count = len(classes)
    problems, observed_ids, observed_hashes, group_splits = Counter(), defaultdict(list), defaultdict(list), defaultdict(set)
    split_stats = {}
    class_stats = [{'id': key, 'splits': {}} for key in class_ids]
    for split, rows in splits.items():
        supports = [Counter() for _ in classes]
        stats = Counter(images=len(rows))
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError('Every image row must be an object')
            image_id, digest = row.get('imageId'), row.get('sha256')
            if not isinstance(image_id, str) or not image_id:
                raise ValueError('Every image needs an imageId')
            if not isinstance(digest, str) or not SHA256.fullmatch(digest):
                raise ValueError('Every image needs a SHA-256')
            observed_ids[image_id].append(split)
            observed_hashes[digest].append(split)
            if row.get('sourceSplit') != split:
                problems['sourceSplitMismatch'] += 1
            if row.get('status') != 'downloaded':
                problems['imageNotDownloaded'] += 1

            group_id = row.get('groupId')
            if isinstance(group_id, str) and group_id.strip():
                group_splits[group_id].add(split)
                stats['imagesWithGroupId'] += 1
            else:
                stats['imagesWithoutGroupId'] += 1
            if row.get('domain', manifest.get('domain')) == 'rental-apartment':
                stats['rentalDomainImages'] += 1
            if row.get('individualLicenseVerified') is True:
                stats['individualRightsVerifiedImages'] += 1
            else:
                stats['individualRightsUnverifiedImages'] += 1

            boxes = row.get('boxes')
            if not isinstance(boxes, list) or len(boxes) > 100:
                raise ValueError('Image needs at most 100 explicit boxes')
            box_counts = Counter()
            for box in boxes:
                if (not isinstance(box, dict) or type(box.get('classIndex')) is not int
                        or not 0 <= box['classIndex'] < count):
                    raise ValueError('Invalid box class')
                xyxy = box.get('xyxy')
                if (not isinstance(xyxy, list) or len(xyxy) != 4
                        or any(type(value) not in (int, float) or not math.isfinite(value) for value in xyxy)
                        or not (0 <= xyxy[0] < xyxy[2] <= 1 and 0 <= xyxy[1] < xyxy[3] <= 1)):
                    raise ValueError('Invalid normalized xyxy box')
                box_counts[box['classIndex']] += 1
            # Do not infer verified labels from presence or absence of boxes.
            verified = indices(row, 'verifiedClassIndices', count)
            non_exhaustive = indices(row, 'nonExhaustiveClassIndices', count)
            if verified & non_exhaustive:
                problems['nonExhaustiveClassMarkedVerified'] += len(verified & non_exhaustive)
            explicit_negative = indices(row, 'negativeClassIndices', count)
            if explicit_negative & set(box_counts):
                problems['negativeClassHasPositiveBox'] += len(explicit_negative & set(box_counts))
            if explicit_negative - verified:
                problems['negativeClassNotInVerifiedClasses'] += len(explicit_negative - verified)
            for index in range(count):
                support = supports[index]
                support['annotatedPositiveBoxes'] += box_counts[index]
                if box_counts[index]:
                    support['annotatedPositiveImages'] += 1
                if index in verified and index not in non_exhaustive:
                    support['verifiedImages'] += 1
                    if box_counts[index]:
                        support['verifiedPositiveImages'] += 1
                        support['verifiedPositiveBoxes'] += box_counts[index]
                    else:
                        # Existing trainer schema: explicit verification plus
                        # zero boxes means a verified-negative class/image pair.
                        support['verifiedNegativeImages'] += 1
                else:
                    support['unknownImages'] += 1

            if verify_images:
                candidate = row.get('path')
                if not isinstance(candidate, str) or not candidate:
                    problems['missingImagePath'] += 1
                    continue
                candidate = Path(candidate)
                candidate = candidate if candidate.is_absolute() else path.parent / candidate
                # No globbing/cache discovery and no symlinked image files.
                if candidate.is_symlink() or not candidate.is_file():
                    problems['missingOrUnsafeImage'] += 1
                    continue
                if not 0 < candidate.stat().st_size <= 12_000_000:
                    problems['invalidImageBytes'] += 1
                    continue
                if file_hash(candidate) != digest:
                    problems['imageHashMismatch'] += 1
                    continue
                stats['verifiedImageHashes'] += 1
        for key in ('images', 'imagesWithGroupId', 'imagesWithoutGroupId', 'rentalDomainImages',
                    'individualRightsVerifiedImages', 'individualRightsUnverifiedImages', 'verifiedImageHashes'):
            stats.setdefault(key, 0)
        split_stats[split] = dict(stats)
        for index, support in enumerate(supports):
            class_stats[index]['splits'][split] = {key: support[key] for key in (
                'annotatedPositiveImages', 'annotatedPositiveBoxes', 'verifiedImages',
                'verifiedPositiveImages', 'verifiedPositiveBoxes', 'verifiedNegativeImages', 'unknownImages')}

    problems['duplicateImageIds'] = sum(len(values)-1 for values in observed_ids.values() if len(values)>1)
    problems['duplicateImageHashes'] = sum(len(values)-1 for values in observed_hashes.values() if len(values)>1)
    problems['groupsAcrossSplits'] = sum(len(values)>1 for values in group_splits.values())
    integrity_issues = {key: value for key, value in sorted(problems.items()) if value}
    for item in class_stats:
        support = item['splits']['validation']
        perfect_lower = precision_lower(support['verifiedPositiveBoxes'])
        gaps = []
        if support['verifiedPositiveBoxes'] < MIN_TRUE_POSITIVES:
            gaps.append('fewer-than-75-verified-positive-boxes')
        if support['verifiedImages'] < MIN_VERIFIED_IMAGES:
            gaps.append('fewer-than-20-verified-images')
        if support['verifiedNegativeImages'] < MIN_NEGATIVE_IMAGES:
            gaps.append('fewer-than-20-verified-negative-images')
        if perfect_lower < MIN_PRECISION_LOWER:
            gaps.append('insufficient-perfect-prediction-precision-bound')
        item['validationSupport'] = {
            'couldMeetExistingStatisticalGate': not gaps,
            'precisionWilsonLower95IfAllVerifiedBoxesDetectedWithoutFalsePositives': perfect_lower,
            'gaps': gaps,
        }

    total = sum(row['images'] for row in split_stats.values())
    has_test = bool(split_stats.get('test', {}).get('images'))
    grouping_complete = total > 0 and all(row['imagesWithoutGroupId'] == 0 for row in split_stats.values())
    test_rental = has_test and split_stats['test']['rentalDomainImages'] == split_stats['test']['images']
    blockers = []
    if integrity_issues:
        blockers.append('manifest-or-image-integrity-issues')
    if not verify_images:
        blockers.append('image-hashes-not-checked')
    if not split_stats['train']['images'] or not split_stats['validation']['images']:
        blockers.append('empty-required-split')
    if not grouping_complete:
        blockers.append('apartment-or-source-grouping-unverified')
    if not has_test:
        blockers.append('independent-test-split-absent')
    elif not test_rental:
        blockers.append('rental-domain-test-unverified')
    if any(row['individualRightsUnverifiedImages'] for row in split_stats.values()):
        blockers.append('individual-image-rights-unverified')
    if any(not item['validationSupport']['couldMeetExistingStatisticalGate'] for item in class_stats):
        blockers.append('classwise-validation-support-insufficient')

    return {
        'version': 1,
        'kind': 'photo-training-data-readiness-audit',
        'manifestSha256': hashlib.sha256(raw).hexdigest(),
        'imageHashesChecked': verify_images,
        'integrityIssues': integrity_issues,
        'splits': split_stats,
        'classCount': count,
        'classes': class_stats,
        'explicitDatasetLicenseAllowance': manifest.get('explicitDatasetLicenseAllowance') is True,
        'groups': {'uniqueDeclaredGroups': len(group_splits), 'coverageComplete': grouping_complete,
                   'crossSplitGroupCount': problems['groupsAcrossSplits']},
        'independentTest': {'present': has_test, 'rentalDomainDeclared': test_rental,
                            'independenceVerified': False},
        'existingStatisticalGate': {'minimumTruePositives': MIN_TRUE_POSITIVES,
            'minimumVerifiedImages': MIN_VERIFIED_IMAGES, 'minimumVerifiedNegativeImages': MIN_NEGATIVE_IMAGES,
            'minimumPrecisionWilsonLower95': MIN_PRECISION_LOWER, 'minimumRecall': MIN_RECALL},
        'classesWithPotentiallySufficientValidationSupport': sum(
            item['validationSupport']['couldMeetExistingStatisticalGate'] for item in class_stats),
        'blockers': blockers,
        'automaticAcceptance': False,
        'limitations': [
            'Counts describe supplied annotations; their human correctness is not verified.',
            'Missing class labels remain unknown, never inferred negatives.',
            'File hashes detect exact byte duplicates, not re-encoded images, crops or repeated views.',
            'Group IDs and rental-domain metadata are declarations, not independent proof.',
            'Test independence also requires a frozen unseen protocol and verified provenance.',
            'Sufficient label counts cannot establish model quality; actual predictions and independent acceptance are required.',
            'This command never trains, changes settings, downloads images or promotes weights.',
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--metadata-only', action='store_true', help='Skip image reads; explicitly incomplete integrity audit')
    args = parser.parse_args()
    try:
        report = audit_manifest(args.manifest, verify_images=not args.metadata_only)
    except (ValueError, TypeError, OSError) as error:
        # Avoid leaking supplied private paths through native exception text.
        parser.exit(2, 'Photo manifest audit failed: invalid or unreadable input (' + type(error).__name__ + ').\n')
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
