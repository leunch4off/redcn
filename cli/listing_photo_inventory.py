"""Collect local observations for cached selected originals, once per SHA-256.

This module never downloads photos, changes listing facts or stores image bytes
in the result. The import coordinator merges the returned evidence separately.
"""
import math
import re
import time

from job_progress import emit
from local_photo_ai import AUTO_CONFIDENCE, FEATURE_LABELS, normalize_inventory
import photo_store
import redcn

VERSION = 1
COVERAGE_LIMIT = 30
_DIGEST = re.compile(r'[a-f0-9]{64}\Z')


def _configuration(directory):
    from photo_ai_settings import load_config
    return load_config(directory)


def _cancelled(cancel):
    if cancel is not None and cancel.is_set():
        raise ValueError('Анализ фотографий отменён')


def _sources(result):
    from phone_relay import source_images
    selected = source_images(result)
    fields = result.get('fields') if isinstance(result.get('fields'), dict) else {}
    listing = result.get('listing') if isinstance(result.get('listing'), dict) else {}
    values = fields.get('photos') or listing.get('images') or []
    edits = fields.get('photoEdits') if isinstance(fields.get('photoEdits'), dict) else {}
    excluded = {value for value in edits.get('excluded', []) if isinstance(value, str)} if isinstance(edits.get('excluded', []), list) else set()
    available = set()
    for value in values if isinstance(values, list) else []:
        try:
            url = redcn.allowed_url(value, images=True, resolve=False)
        except (ValueError, TypeError):
            continue
        if url not in excluded:
            available.add(url)
    return selected[:COVERAGE_LIMIT], len(available)


def _evidence(raw):
    rows = []
    for evidence in raw[:8] if isinstance(raw, list) else []:
        if not isinstance(evidence, dict):
            continue
        row = {key: value[:300] for key, value in evidence.items() if key in (
            'kind', 'text', 'label', 'photoId', 'originalId', 'provider', 'model', 'source') and isinstance(value, str)}
        score = evidence.get('confidence')
        if type(score) in (int, float) and math.isfinite(score) and 0 <= score <= 1:
            row['confidence'] = score
        if type(evidence.get('directlyVisible')) is bool:
            row['directlyVisible'] = evidence['directlyVisible']
        region = evidence.get('region')
        if isinstance(region, list) and len(region) == 4 and all(type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1 for value in region):
            row['region'] = region[:]
        if row:
            rows.append(row)
    return rows


def _positive(candidate, analysis, digest, model):
    if candidate.get('autoEligible') is not True or analysis.get('provider') != 'ollama' or analysis.get('model') != model:
        return False
    if isinstance(analysis.get('unknownIds'), list) and candidate['id'] in analysis['unknownIds']:
        return False
    for evidence in candidate['evidence']:
        if (evidence.get('kind') != 'model-visual-observation' or evidence.get('provider') != 'ollama'
                or evidence.get('model') != model or evidence.get('photoId') != digest
                or evidence.get('originalId') != digest or evidence.get('directlyVisible') is not True
                or evidence.get('confidence') != candidate['confidence']):
            continue
        # Revalidate rather than trusting a stale cache's autoEligible flag.
        raw = {'objects': [{'id': candidate['id'], 'visible': True,
            'confidence': candidate['confidence'], 'count': None, 'countExact': False,
            'evidence': evidence.get('text', '')}], 'unknownIds': []}
        normalized = normalize_inventory(raw, digest, model)['candidates']
        if normalized and normalized[0]['autoEligible']:
            return True
    return False


def _candidates(analysis, digest, model):
    output = []
    rows = analysis.get('candidates')
    seen = set()
    for row in rows[:len(FEATURE_LABELS)] if isinstance(rows, list) else []:
        if not isinstance(row, dict) or not isinstance(row.get('id'), str) or row['id'] not in FEATURE_LABELS:
            continue
        feature = 'bedAny' if row['id'] == 'bed' else row['id']
        score = row.get('confidence')
        if feature in seen or type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 1:
            continue
        seen.add(feature)
        count = row.get('count')
        candidate = {'id': feature, 'label': FEATURE_LABELS[feature], 'confidence': score,
            'count': count if type(count) is int and 1 <= count <= 12 else None,
            'status': 'candidate', 'evidence': _evidence(row.get('evidence')),
            'autoEligible': row.get('autoEligible') is True, 'autoCountEligible': False}
        candidate['autoEligible'] = _positive(candidate, analysis, digest, model)
        output.append(candidate)
    return output


def analyze_listing_photos(directory, result, report=None, cancel=None, *, max_seconds=600):
    """Analyze at most 30 cached originals within one explicit wall deadline.

    Missing files, bad responses, timeouts and duplicates are represented per
    source URL. Cancellation raises ValueError. An incomplete album never
    becomes a complete inventory, and no visual result means absence.
    """
    _cancelled(cancel)
    selected, available = _sources(result if isinstance(result, dict) else {})
    output = {'version': VERSION, 'state': 'complete', 'total': len(selected),
        'totalAvailable': available, 'coverageLimit': COVERAGE_LIMIT,
        'truncated': available > COVERAGE_LIMIT, 'checked': 0, 'failed': 0,
        'skipped': 0, 'features': [], 'photoEvidence': [], 'photos': []}
    try:
        config = _configuration(directory)
    except (OSError, ValueError, ImportError):
        config = {}
    if not isinstance(config, dict) or config.get('enabled') is not True or not isinstance(config.get('model'), str) or not config['model'].strip():
        return {**output, 'state': 'disabled', 'skipped': len(selected),
            'message': 'Локальный анализ фото не включён'}
    model = config['model'].strip()
    output['autoApply'] = config.get('autoApply') is True
    configured_budget = config.get('maxSeconds', max_seconds)
    if type(max_seconds) not in (int, float) or not math.isfinite(max_seconds) or not 0 < max_seconds <= 1800:
        return {**output, 'state': 'partial', 'skipped': len(selected), 'message': 'Неверный общий лимит анализа фото'}
    if type(configured_budget) not in (int, float) or not math.isfinite(configured_budget) or configured_budget <= 0:
        configured_budget = max_seconds
    deadline = time.monotonic() + min(max_seconds, configured_budget, 1800)
    by_url = {}
    for row in result.get('media', []) if isinstance(result, dict) and isinstance(result.get('media'), list) else []:
        if isinstance(row, dict) and isinstance(row.get('sourceUrl'), str):
            by_url.setdefault(row['sourceUrl'], row)
    seen, features = {}, set()
    incomplete = output['truncated']
    emit(report, 'Проверяем мебель и технику на фотографиях: ' + str(len(selected)), stage='vision', completed=0, total=len(selected))
    for index, url in enumerate(selected):
        _cancelled(cancel)
        row = by_url.get(url, {})
        digest = row.get('id')
        photo = {'sourceUrl': url, 'originalId': digest if isinstance(digest, str) and _DIGEST.fullmatch(digest) else '',
            'status': 'pending', 'candidates': [], 'unknownIds': [key for key in FEATURE_LABELS if key != 'bed']}
        output['photos'].append(photo)
        if time.monotonic() >= deadline:
            photo['status'] = 'skipped'; photo['reason'] = 'budget'
            output['skipped'] += 1; incomplete = True
        elif not photo['originalId'] or row.get('error'):
            photo['status'] = 'skipped'; photo['reason'] = 'original-unavailable'
            output['skipped'] += 1; incomplete = True
        elif digest in seen:
            photo['status'] = 'duplicate'; photo['duplicateOf'] = seen[digest]
            output['skipped'] += 1
        else:
            seen[digest] = url
            emit(report, 'Фото ' + str(index + 1) + ' из ' + str(len(selected)) + ': распознаём видимые предметы',
                stage='vision', completed=index, total=len(selected))
            try:
                _, _, original = photo_store.asset(directory, digest)
                if original.get('kind') != 'original' or original.get('id') != digest:
                    raise ValueError('Нужен проверенный оригинал')
                analysis = photo_store.analyze_photo(directory, digest, cancel=cancel,
                    deadline=min(deadline, time.monotonic() + 120))
                _cancelled(cancel)
                if not isinstance(analysis, dict) or analysis.get('originalId') != digest:
                    raise ValueError('Анализ относится к другому оригиналу')
                status = analysis.get('status')
                photo['engine'] = str(analysis.get('engine', ''))[:300]
                photo['provider']=str(analysis.get('provider',''))[:40]
                photo['model']=str(analysis.get('model',''))[:150]
                if status not in ('ok', 'review'):
                    photo['status'] = status if status in ('disabled', 'unavailable', 'invalid', 'timeout', 'busy', 'cancelled') else 'invalid'
                    output['failed'] += 1; incomplete = True
                else:
                    candidates = _candidates(analysis, digest, model)
                    photo.update(status='review', candidates=candidates)
                    unknown = analysis.get('unknownIds')
                    if isinstance(unknown, list):
                        photo['unknownIds'] = list(dict.fromkeys(key for key in unknown[:len(FEATURE_LABELS)] if isinstance(key, str) and key in FEATURE_LABELS and key != 'bed'))
                    output['checked'] += 1
                    if analysis.get('provider') != 'ollama' or analysis.get('fallbackReason'):
                        incomplete = True
                    if output['autoApply']:
                        for candidate in candidates:
                            if not candidate['autoEligible'] or candidate['confidence'] < AUTO_CONFIDENCE:
                                continue
                            feature = candidate['id']; features.add(feature)
                            output['photoEvidence'].append({'id': feature, 'status': 'auto-visible',
                                'autoEligible': True, 'originalId': digest, 'photoId': digest,
                                'sourceUrl': url, 'confidence': candidate['confidence'],
                                'evidence': candidate['evidence'], 'engine': photo['engine'], 'count': None})
            except (OSError, ValueError):
                _cancelled(cancel)
                photo['status'] = 'unavailable'
                output['failed'] += 1; incomplete = True
        emit(report, 'Фото проверены: ' + str(index + 1) + ' из ' + str(len(selected)),
            stage='vision', completed=index + 1, total=len(selected))
    _cancelled(cancel)
    output['features'] = sorted(features)
    output['state'] = 'partial' if incomplete else 'complete'
    return output
