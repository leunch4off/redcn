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
REVIEW_MODELS = {TRAINED_MODEL: 'yolox-trained', 'redcn-furniture-world-v2': 'onnx-world'}
SUGGESTION_SCORE = .85
MAX_JOURNAL_ENTRIES = 600
TRAINED_REVIEW_IDS = frozenset(('sofa', 'bedAny', 'chairs', 'tv', 'fridge',
    'oven', 'microwave', 'sink', 'table', 'desk', 'wardrobe', 'washer',
    'dishwasher', 'piano', 'coffeeMachine', 'mirror', 'curtains', 'coffeeTable', 'stove', 'cabinet'))
_DIGEST = re.compile(r'[a-f0-9]{64}\Z')


def _score(value):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1


def detector_suggestion(candidate, analysis, digest):
    """Recheck evidence, including cached flags, before adding a review entry."""
    if (not isinstance(candidate, dict) or not isinstance(analysis, dict)
            or not isinstance(digest, str) or not _DIGEST.fullmatch(digest)
            or analysis.get('originalId') != digest
            or not isinstance(analysis.get('model'), str) or analysis['model'] not in REVIEW_MODELS
            or analysis.get('provider') != REVIEW_MODELS[analysis['model']]
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
                or row.get('provider') != analysis['provider'] or row.get('model') != analysis['model']
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


def assistant_reviews(fields, digest):
    """Explicit reviews of these bytes, separate from human/model acceptance."""
    from photo_teacher_review import valid_review
    fields = fields if isinstance(fields, dict) else {}
    edits = fields.get('photoEdits') if isinstance(fields.get('photoEdits'), dict) else {}
    observations = edits.get('observations') if isinstance(edits.get('observations'), dict) else {}
    reviews = {}
    for observation in list(observations.values())[:MAX_JOURNAL_ENTRIES]:
        if not isinstance(observation, dict):
            continue
        analysis, review = observation.get('analysis'), observation.get('teacherReview')
        if (not isinstance(analysis, dict) or analysis.get('originalId') != digest
                or not valid_review(review, digest)):
            continue
        for feature in review['features']:
            reviews.setdefault(feature, review)
    return reviews


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
    reviews_by_original = {}
    current_originals = set(current.values())

    def resolve(entry):
        saved = copy.deepcopy(entry)
        digest, feature = saved['originalId'], saved['featureId']
        # Assistant decisions must be revalidated, never retained as a human
        # confirmation after removing the review or replacing the photograph.
        if saved['state'] == 'assistant-reviewed':
            saved['state'] = 'pending'
        for key in ('reviewOrigin', 'assistantReviewId', 'humanVerified'):
            saved.pop(key, None)
        if saved['state'] == 'pending' and current.get(saved['sourceUrl']) != digest:
            saved['state'] = 'stale'
        rejected, confirmed = human_decisions(fields, saved['sourceUrl'], digest)
        if feature in rejected:
            saved['state'] = 'rejected'
        elif feature in confirmed:
            saved['state'] = 'confirmed'
        elif saved['state'] not in ('confirmed', 'rejected') and digest in current_originals:
            if digest not in reviews_by_original:
                reviews_by_original[digest] = assistant_reviews(fields, digest)
            review = reviews_by_original[digest].get(feature)
            if review:
                saved.update(state='assistant-reviewed', reviewOrigin='assistant-visual-review',
                    assistantReviewId=review['reviewId'], humanVerified=False)
        return saved

    for row in old_rows[:MAX_JOURNAL_ENTRIES]:
        if not isinstance(row, dict):
            continue
        digest, feature = row.get('originalId'), row.get('featureId')
        if (not isinstance(digest, str) or not _DIGEST.fullmatch(digest)
                or not isinstance(feature, str) or feature not in TRAINED_REVIEW_IDS or row.get('state') not in ('pending', 'confirmed', 'rejected', 'stale', 'assistant-reviewed')
                or not isinstance(row.get('sourceUrl'), str)):
            continue
        identity = hashlib.sha256((digest + '|' + feature).encode()).hexdigest()
        if row.get('id') != identity:
            continue
        entries[identity] = resolve(row)
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
                'provider': photo['provider'], 'model': photo['model'], 'automatic': True,
                'state': state, 'reason': 'model-unaccepted', 'count': None,
                'createdAt': old.get('createdAt', moment), 'lastSeenAt': moment}
            if identity not in entries:
                added += 1
            entries[identity] = resolve(entry)
    # Keep current observations first when old originals fill the bounded journal.
    ordered = sorted(entries.values(), key=lambda row: (
        current.get(row['sourceUrl']) != row['originalId'], row['state'] != 'pending', row['id']))[:MAX_JOURNAL_ENTRIES]
    return {'version': VERSION, 'requested': requested is True, 'entries': ordered,
        'added': added, 'pending': sum(row['state'] == 'pending' for row in ordered),
        'confirmed': sum(row['state'] == 'confirmed' for row in ordered),
        'rejected': sum(row['state'] == 'rejected' for row in ordered),
        'assistantReviewed': sum(row['state'] == 'assistant-reviewed' for row in ordered)}


def reconcile_saved_review_journal(result):
    """Return a copy with current review state, without inference or new facts.

    Used after a visual review and by explicit saved-card migrations. Scan
    coverage, model output, human confirmation and publication text stay intact.
    """
    from phone_relay import source_images
    updated = copy.deepcopy(result)
    fields = updated.get('fields') if isinstance(updated.get('fields'), dict) else {}
    edits = fields.get('photoEdits') if isinstance(fields.get('photoEdits'), dict) else {}
    previous = edits.get('reviewJournal')
    if not isinstance(previous, dict) or previous.get('version') != VERSION:
        return updated
    observations = edits.get('observations') if isinstance(edits.get('observations'), dict) else {}
    media = {row.get('sourceUrl'): row.get('id') for row in updated.get('media', [])
        if isinstance(row, dict) and row.get('kind', 'original') == 'original'}
    photos = []
    for url in source_images(updated):
        digest = media.get(url)
        if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
            continue
        observation = observations.get(url) if isinstance(observations.get(url), dict) else {}
        analysis = observation.get('analysis') if isinstance(observation.get('analysis'), dict) else {}
        photos.append({**(analysis if analysis.get('originalId') == digest else {}),
            'sourceUrl': url, 'originalId': digest})
    journal = review_journal(fields, photos, requested=False)
    journal.update(requested=previous.get('requested') is True, added=0)
    edits['reviewJournal'] = journal
    inventory = updated.get('photoInventory')
    if isinstance(inventory, dict):
        inventory['reviewJournal'] = copy.deepcopy(journal)
        current = {photo['sourceUrl']: photo['originalId'] for photo in photos}
        # The phone renders per-photo candidates as well as the aggregate.
        # Detach this derived view from any in-memory aliases of observations;
        # original model output and accepted listing fields remain untouched.
        inventory_photos = inventory.get('photos')
        if isinstance(inventory_photos, list):
            inventory['photos'] = copy.deepcopy(inventory_photos)
            for photo in inventory['photos']:
                if not isinstance(photo, dict):
                    continue
                digest, url = photo.get('originalId'), photo.get('sourceUrl')
                active = current.get(url) == digest and digest is not None
                rejected, confirmed = human_decisions(fields, url, digest) if active else (set(), set())
                assistant = assistant_reviews(fields, digest) if active else {}
                candidates = photo.get('candidates') if isinstance(photo.get('candidates'), list) else []
                for candidate in candidates:
                    if not isinstance(candidate, dict) or candidate.get('id') not in FEATURE_LABELS:
                        continue
                    feature = candidate['id']
                    if candidate.get('reviewDecision') == 'assistant-reviewed':
                        for key in ('reviewDecision', 'reviewOrigin', 'assistantReviewId', 'humanVerified'):
                            candidate.pop(key, None)
                        candidate.update(status='candidate', autoEligible=False,
                            automaticSuggestion=active and photo.get('autoApply') is True
                                and detector_suggestion(candidate, photo, digest))
                    if feature in rejected:
                        candidate.update(status='rejected', autoEligible=False,
                            automaticSuggestion=False, reviewDecision='rejected')
                    elif feature in confirmed:
                        candidate.update(status='confirmed', automaticSuggestion=False, reviewDecision='confirmed')
                    elif feature in assistant:
                        candidate.update(status='assistant-reviewed', autoEligible=False,
                            automaticSuggestion=False, reviewDecision='assistant-reviewed',
                            reviewOrigin='assistant-visual-review', humanVerified=False,
                            assistantReviewId=assistant[feature]['reviewId'])
                if isinstance(photo.get('autoApplySummary'), dict):
                    photo['autoApplySummary'].update(
                        automaticSuggestions=sum(row.get('automaticSuggestion') is True
                            for row in candidates if isinstance(row, dict)),
                        assistantReviewed=sum(row.get('reviewDecision') == 'assistant-reviewed'
                            for row in candidates if isinstance(row, dict)),
                        needsReview=sum(row.get('reviewDecision') not in ('confirmed', 'rejected', 'assistant-reviewed')
                            and not (photo.get('autoApply') and row.get('autoEligible'))
                            for row in candidates if isinstance(row, dict)))
        summary = inventory.get('autoApplySummary')
        if isinstance(summary, dict):
            pending, reviewed = set(), set()
            for photo in photos:
                digest = photo['originalId']
                rejected, confirmed = human_decisions(fields, photo['sourceUrl'], digest)
                assistant = assistant_reviews(fields, digest)
                for candidate in photo.get('candidates', []):
                    if not isinstance(candidate, dict) or candidate.get('id') not in FEATURE_LABELS:
                        continue
                    feature = candidate['id']
                    if feature in rejected or feature in confirmed:
                        continue
                    if feature in assistant:
                        reviewed.add((digest, feature))
                    elif not (photo.get('autoApply') and candidate.get('autoEligible')):
                        pending.add((digest, feature))
            summary.update(automaticSuggestions=journal['pending'], newSuggestions=0,
                needsReview=len(pending), assistantReviewed=len(reviewed))
    return updated
