"""Authenticated, card-scoped manual cross-view evidence. Never runs inference."""
import re

from local_photo_ai import FEATURE_LABELS
import photo_instances


def dispatch(directory, path, data=None, *, namespace='local', actor='operator'):
    match = re.fullmatch(r'photo-instances/([a-f0-9]{32})', path)
    if not match:
        return None
    from language_service import _read
    from phone_relay import card_media
    card_id = match[1]
    card, _ = _read(directory, card_id)
    media = card_media(directory, card, include_crops=False)
    photos = [{key: row[key] for key in ('id', 'width', 'height') if key in row} |
              {'url': '/api/' + namespace + '/media/' + row['id']}
              for row in media if re.fullmatch(r'[a-f0-9]{64}', row.get('id', ''))]
    photo_ids = [row['id'] for row in photos]
    try:
        if data is None:
            value = photo_instances.load_review(directory, card_id, photo_ids=photo_ids)
        else:
            if not isinstance(data, dict) or set(data) != {'document', 'expectedRevision'}:
                raise ValueError('Нужны наблюдения и текущая версия проверки')
            value = photo_instances.save_review(directory, card_id, data['document'],
                photo_ids=photo_ids, expected_revision=data['expectedRevision'], actor=actor)
    except photo_instances.InstanceReviewConflict as error:
        return 409, {'error': str(error), 'code': error.code}, 'application/json; charset=utf-8', {}
    categories = {key: label for key, label in FEATURE_LABELS.items()
                  if key not in photo_instances._GROUPS and key != 'bed'}
    return 200, {**value, 'photos': photos, 'categories': categories,
                 'automaticMatching': False, 'automaticPublication': False}, 'application/json; charset=utf-8', {}
