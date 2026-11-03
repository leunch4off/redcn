"""Import explicitly authored assistant visual reviews without impersonating humans.

These are positive, image-bound witnesses. They are neither detector scores nor
exhaustive bounding-box annotations and must not enter a human evaluation set.
"""
import copy
import hashlib
import json
import re

from local_photo_ai import FEATURE_LABELS

_DIGEST = re.compile(r'[a-f0-9]{64}\Z')
_DISALLOWED = {'bed', 'internet', 'closet', 'pantry', 'balcony', 'loggia',
               'homeCinema', 'roomFurniture'}
ALLOWED_FEATURES = frozenset(FEATURE_LABELS) - _DISALLOWED


def valid_review(value, original_id=None):
    if not isinstance(value, dict):
        return False
    if (value.get('version') != 1 or value.get('kind') != 'assistant-visual-review'
            or value.get('humanVerified') is not False or value.get('exhaustive') is not False
            or not _DIGEST.fullmatch(str(value.get('originalId', '')))
            or not _DIGEST.fullmatch(str(value.get('reviewId', '')))
            or not _DIGEST.fullmatch(str(value.get('datasetId', '')))):
        return False
    if original_id is not None and value['originalId'] != original_id:
        return False
    features = value.get('features')
    return (isinstance(features, list) and len(features) <= len(ALLOWED_FEATURES)
            and all(isinstance(item, str) and item in ALLOWED_FEATURES for item in features)
            and len(features) == len(set(features))
            and isinstance(value.get('reviewedAt'), str) and 10 <= len(value['reviewedAt']) <= 40)


def make_review(original_id, features, *, dataset_id, reviewed_at, note=''):
    features = sorted(set(features))
    payload = {'version': 1, 'kind': 'assistant-visual-review', 'humanVerified': False,
               'exhaustive': False, 'originalId': original_id, 'datasetId': dataset_id,
               'features': features, 'reviewedAt': reviewed_at, 'note': str(note)[:2000]}
    payload['reviewId'] = hashlib.sha256(json.dumps(payload, sort_keys=True,
        ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    if not valid_review(payload):
        raise ValueError('Неверный разбор фотографии помощником')
    return payload


def review_evidence(review, source_url):
    if not valid_review(review):
        return []
    return [{'id': feature, 'status': 'assistant-reviewed', 'sourceUrl': source_url,
        'originalId': review['originalId'], 'photoId': review['originalId'],
        'engine': 'assistant-visual-review', 'autoEligible': False, 'count': None,
        'humanVerified': False, 'exhaustive': False, 'reviewId': review['reviewId'],
        'datasetId': review['datasetId'], 'reviewedAt': review['reviewedAt'],
        'evidence': [{'kind': 'assistant-visual-review', 'source': 'assistant-visual-review',
            'originalId': review['originalId'], 'photoId': review['originalId'],
            'directlyVisible': True, 'humanVerified': False, 'exhaustive': False,
            'reviewId': review['reviewId']}]} for feature in review['features']]


def retain_teacher_candidates(analysis, review):
    if not valid_review(review, analysis.get('originalId')):
        return analysis
    candidates = list(analysis.get('candidates') or [])
    present = {candidate.get('id') for candidate in candidates if isinstance(candidate, dict)}
    candidates.extend({'id': feature, 'label': FEATURE_LABELS[feature], 'count': None,
        'autoEligible': False, 'automaticSuggestion': False,
        'reviewOrigin': 'assistant-visual-review', 'reviewOriginalId': review['originalId'],
        'evidence': []} for feature in review['features'] if feature not in present)
    return {**analysis, 'candidates': candidates}


def merge_reviews(result, reviews):
    """Return a copy; keep text, manual decisions, model output and counts intact.

    A review is attached only to the currently selected original. Explicit user
    rejection and contradictory source facts are kept for operator resolution.
    """
    from photo_auto_policy import human_decisions, reconcile_saved_review_journal
    from phone_relay import source_images
    updated = copy.deepcopy(result)
    fields = updated.setdefault('fields', {})
    media = {row.get('sourceUrl'): row.get('id') for row in updated.get('media', [])
             if isinstance(row, dict) and row.get('kind', 'original') == 'original'}
    selected = set(source_images(updated))
    edits = fields.get('photoEdits') if isinstance(fields.get('photoEdits'), dict) else {}
    excluded = set(edits.get('excluded') or [])
    observations = edits.setdefault('observations', {})
    rejected = set(fields.get('photoRejectedFeatures') or [])
    conflict_ids = {row.get('id') for row in (fields.get('sourceAudit') or {}).get('furniture', [])
                    if isinstance(row, dict) and row.get('status') in ('absent', 'planned', 'conflict')}
    existing = set(fields.get('features') or [])
    applied, skipped, conflicts, all_evidence = [], [], [], []
    for row in reviews:
        url, review = row.get('sourceUrl'), row.get('review')
        if (not isinstance(url, str) or url not in selected or url in excluded
                or not valid_review(review, media.get(url)) or not media.get(url)):
            skipped.append({'sourceUrl': url, 'reason': 'original-changed-or-unselected'})
            continue
        negative, _ = human_decisions(fields, url, review['originalId'])
        blocked = rejected | negative | conflict_ids
        usable = [feature for feature in review['features'] if feature not in blocked]
        conflicts.extend({'sourceUrl': url, 'id': feature,
            'reason': 'operator-rejected' if feature in rejected | negative else 'source-conflict'}
            for feature in review['features'] if feature in blocked)
        stored = make_review(review['originalId'], usable, dataset_id=review['datasetId'],
                             reviewed_at=review['reviewedAt'], note=review.get('note', ''))
        old = observations.get(url) if isinstance(observations.get(url), dict) else {}
        analysis = old.get('analysis') if isinstance(old.get('analysis'), dict) else {}
        same_original = analysis.get('originalId') == review['originalId']
        if not same_original:
            analysis = {'version': 2, 'status': 'review', 'originalId': review['originalId'],
                'provider': 'assistant-visual-review', 'engine': 'Разбор помощником по фото',
                'autoApply': False, 'candidates': [], 'unknownIds': []}
        observations[url] = {**old, 'analysis': retain_teacher_candidates(analysis, stored),
            'teacherReview': stored,
            # Per-photo operator decisions belong to the bytes they examined.
            # A reused source URL must not rebind old counts or rejections to a
            # new original. Global manual facts and the review journal remain.
            'confirmed': old.get('confirmed', {}) if same_original else {},
            'rejected': old.get('rejected', {}) if same_original else {}}
        all_evidence.extend(review_evidence(stored, url))
        applied.append(url)
    retained = [row for row in fields.get('photoEvidence', []) if isinstance(row, dict)
                and not (row.get('status') == 'assistant-reviewed' and row.get('sourceUrl') in applied)]
    fields['photoEvidence'] = retained + all_evidence
    added = sorted({row['id'] for row in all_evidence} - existing)
    baseline = fields.get('photoFeatureBaseline') if isinstance(fields.get('photoFeatureBaseline'), dict) else {}
    for feature in added:
        baseline.setdefault(feature, {'checked': False, 'count': ''})
    fields['photoFeatureBaseline'] = baseline
    fields['features'] = sorted(existing | {row['id'] for row in all_evidence})
    fields['photoEdits'] = edits
    summary = {'version': 1, 'origin': 'assistant-visual-review', 'humanVerified': False,
        'exhaustive': False, 'reviewedPhotos': len(applied), 'positiveWitnesses': len(all_evidence),
        'addedFeatures': added, 'conflicts': conflicts, 'skipped': skipped,
        'weightsChanged': False, 'textChanged': False}
    updated['assistantPhotoReview'] = summary
    if isinstance(updated.get('photoInventory'), dict):
        updated['photoInventory']['assistantReview'] = copy.deepcopy(summary)
    return reconcile_saved_review_journal(updated), summary
