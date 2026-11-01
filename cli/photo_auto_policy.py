"""Automatic review entries are observations, never unaccepted detector facts.

The pinned first-pass detector has no accepted classes. A high raw score can
prioritize a review entry; it cannot become a calibrated probability or a
publication claim. Original-bound human decisions always take precedence.
"""
import copy
from datetime import datetime, timezone
import hashlib
import math
import re

from local_photo_ai import FEATURE_LABELS


VERSION = 1
TRAINED_MODEL = 'redcn-furniture-v1'
SUGGESTION_SCORE = .85
MAX_JOURNAL_ENTRIES = 600
TRAINED_REVIEW_IDS = frozenset(('sofa', 'bedAny', 'chairs', 'tv', 'fridge',
    'oven', 'microwave', 'sink', 'table', 'desk', 'wardrobe', 'washer',
    'dishwasher', 'piano', 'coffeeMachine', 'mirror', 'curtains', 'coffeeTable'))
_DIGEST = re.compile(r'[a-f0-9]{64}\Z')


def _score(value):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1


def detector_suggestion(candidate, analysis, digest):
    """Recheck evidence, including cached flags, before adding a review entry."""
    if (not isinstance(candidate, dict) or not isinstance(analysis, dict)
            or not isinstance(digest, str) or not _DIGEST.fullmatch(digest)
            or analysis.get('originalId') != digest
            or analysis.get('provider') != 'yolox-trained'
            or analysis.get('model') != TRAINED_MODEL
            or analysis.get('status') not in ('ok', 'review')
            or not isinstance(candidate.get('id'), str) or candidate['id'] not in TRAINED_REVIEW_IDS
            or not _score(candidate.get('confidence'))
            or candidate['confidence'] < SUGGESTION_SCORE):
        return False
    if isinstance(analysis.get('unknownIds'), list) and candidate['id'] in analysis['unknownIds']:
        return False
    evidence = candidate.get('evidence')
    for row in evidence[:8] if isinstance(evidence, list) else []:
        if (not isinstance(row, dict) or row.get('kind') != 'object-detection'
                or row.get('provider') != 'yolox-trained' or row.get('model') != TRAINED_MODEL
                or row.get('source') != 'local-photo' or row.get('originalId') != digest
                or row.get('photoId') != digest or not _score(row.get('confidence'))
                or row['confidence'] != candidate['confidence']):
            continue
        region = row.get('region')
        if (isinstance(region, list) and len(region) == 4
                and all(_score(number) for number in region)
                and region[2] > 0 and region[3] > 0
                and region[0] + region[2] <= 1 + 1e-9
                and region[1] + region[3] <= 1 + 1e-9):
            return True
    return False


def trained_policy(analysis, *, requested=False):
    """Expose configured automation and its actual, review-only effect."""
    result = copy.deepcopy(analysis)
    requested = requested is True
    ready = result.get('status') in ('ok', 'review')
    automatic = 0
    candidates = result.get('candidates')
    for candidate in candidates if isinstance(candidates, list) else []:
        if not isinstance(candidate, dict):
            continue
        # Never trust a worker/cache flag to accept an uncalibrated class.
        candidate.update(autoEligible=False, autoCountEligible=False, count=None,
            automaticSuggestion=False, status='candidate')
        if requested and detector_suggestion(candidate, result, result.get('originalId')):
            candidate.update(automaticSuggestion=True, status='auto-proposed',
                requiresConfirmation=True, reviewReason='model-unaccepted',
                reviewId=hashlib.sha256((result['originalId']+'|'+candidate['id']).encode()).hexdigest())
            automatic += 1
    mode = 'suggestions-only' if ready and requested else 'off' if not requested else 'unavailable'
    result.update(autoApply=requested, autoApplyMode=mode,
        autoApplySummary={'requested': requested, 'mode': mode,
            'automaticFacts': 0, 'automaticSuggestions': automatic,
            'needsReview': len(candidates) if isinstance(candidates, list) else 0,
            'acceptedClasses': 0, 'suggestionScoreThreshold': SUGGESTION_SCORE,
            'reason': 'model-unaccepted'})
    if ready and requested:
        result['message'] = ('Автодобавление в журнал проверки включено: ' + str(automatic)
            + '. Предметы требуют подтверждения; в описание они ещё не добавлены.')
    return result


def human_decisions(fields, url, digest):
    """Only controls tied to this original can confirm or reject this view."""
    fields = fields if isinstance(fields, dict) else {}
    rejected = {key for key in fields.get('photoRejectedFeatures', [])
        if isinstance(key, str) and key in FEATURE_LABELS} if isinstance(fields.get('photoRejectedFeatures'), list) else set()
    edits = fields.get('photoEdits') if isinstance(fields.get('photoEdits'), dict) else {}
    observations = edits.get('observations') if isinstance(edits.get('observations'), dict) else {}
    confirmed = set()
    # Different source URLs can refer to the same cached original. Decisions
    # on that exact photograph must also survive URL aliases and duplicate views.
    for old in list(observations.values())[:600]:
        if not isinstance(old, dict) or not isinstance(old.get('analysis'), dict) or old['analysis'].get('originalId') != digest:
            continue
        values = old.get('rejected')
        if isinstance(values, dict):
            rejected.update(key for key, value in values.items() if key in FEATURE_LABELS and value is True)
        values = old.get('confirmed')
        if isinstance(values, dict):
            confirmed.update(key for key, value in values.items() if key in FEATURE_LABELS and isinstance(value, dict))
    return rejected, confirmed - rejected


def review_journal(fields, photos, *, requested=False, now=None):
    """Merge a bounded per-card review queue without changing confirmed facts.

    IDs bind an observation to one original and category, so rescans and
    duplicate URLs cannot multiply the same suggestion or invent item counts.
    Rejected/confirmed history is retained; replaced originals cannot inherit
    its decisions. Nothing is written to a shared/global owner journal.
    """
    fields = fields if isinstance(fields, dict) else {}
    edits = fields.get('photoEdits') if isinstance(fields.get('photoEdits'), dict) else {}
    previous = edits.get('reviewJournal') if isinstance(edits.get('reviewJournal'), dict) else {}
    old_rows = previous.get('entries') if isinstance(previous.get('entries'), list) else []
    current = {row.get('sourceUrl'): row.get('originalId') for row in photos if isinstance(row, dict)}
    entries = {}
    moment = now or datetime.now(timezone.utc).isoformat(timespec='seconds')
    for row in old_rows[:MAX_JOURNAL_ENTRIES]:
        if not isinstance(row, dict):
            continue
        digest, feature = row.get('originalId'), row.get('featureId')
        if (not isinstance(digest, str) or not _DIGEST.fullmatch(digest)
                or not isinstance(feature, str) or feature not in TRAINED_REVIEW_IDS or row.get('state') not in ('pending', 'confirmed', 'rejected', 'stale')
                or not isinstance(row.get('sourceUrl'), str)):
            continue
        identity = hashlib.sha256((digest + '|' + feature).encode()).hexdigest()
        if row.get('id') != identity:
            continue
        saved = copy.deepcopy(row)
        if saved['state'] == 'pending' and current.get(saved['sourceUrl']) != digest:
            saved['state'] = 'stale'
        rejected, confirmed = human_decisions(fields, saved['sourceUrl'], digest)
        if feature in rejected:
            saved['state'] = 'rejected'
        elif feature in confirmed:
            saved['state'] = 'confirmed'
        entries[identity] = saved
    added = 0
    for photo in photos if requested is True else []:
        if not isinstance(photo, dict) or photo.get('status') != 'review':
            continue
        digest, url = photo.get('originalId'), photo.get('sourceUrl')
        if not isinstance(url, str):
            continue
        rejected, confirmed = human_decisions(fields, url, digest)
        for candidate in photo.get('candidates', []):
            if not detector_suggestion(candidate, photo, digest):
                continue
            feature = candidate['id']
            identity = hashlib.sha256((digest + '|' + feature).encode()).hexdigest()
            old = entries.get(identity, {})
            state = ('rejected' if feature in rejected else 'confirmed' if feature in confirmed
                else old['state'] if old.get('state') in ('confirmed', 'rejected') else 'pending')
            entry = {'id': identity, 'originalId': digest, 'sourceUrl': url,
                'featureId': feature, 'label': FEATURE_LABELS[feature],
                'confidence': candidate['confidence'], 'evidence': copy.deepcopy(candidate['evidence'][:8]),
                'provider': 'yolox-trained', 'model': TRAINED_MODEL, 'automatic': True,
                'state': state, 'reason': 'model-unaccepted', 'count': None,
                'createdAt': old.get('createdAt', moment), 'lastSeenAt': moment}
            if identity not in entries:
                added += 1
            entries[identity] = entry
    # Keep current observations first when old originals fill the bounded journal.
    ordered = sorted(entries.values(), key=lambda row: (
        current.get(row['sourceUrl']) != row['originalId'], row['state'] != 'pending', row['id']))[:MAX_JOURNAL_ENTRIES]
    return {'version': VERSION, 'requested': requested is True, 'entries': ordered,
        'added': added, 'pending': sum(row['state'] == 'pending' for row in ordered),
        'confirmed': sum(row['state'] == 'confirmed' for row in ordered),
        'rejected': sum(row['state'] == 'rejected' for row in ordered)}
