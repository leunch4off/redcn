"""Authenticated card-scoped photo annotation endpoint; never runs a model."""
import re
import photo_training


def dispatch(directory, path, data=None, *, namespace='local', actor='Mac owner'):
    match = re.fullmatch(r'photo-training/([a-f0-9]{32})', path)
    if not match:
        return None
    card_id = match[1]
    try:
        if data is None:
            value = photo_training.view(directory, card_id, namespace=namespace)
        elif isinstance(data, dict) and set(data) == {'action', 'document', 'expectedRevision'} and data['action'] == 'save':
            photo_training.save(directory, card_id, data['document'], expected_revision=data['expectedRevision'], actor=actor)
            value = photo_training.view(directory, card_id, namespace=namespace)
        elif isinstance(data, dict) and data == {'action': 'export'}:
            value = {'manifest': photo_training.export_manifest(directory, [card_id]), 'private': True}
        else:
            raise ValueError('Нужно сохранение проверенной разметки или приватный экспорт')
    except photo_training.PhotoTrainingConflict as error:
        return 409, {'error': str(error), 'code': error.code}, 'application/json; charset=utf-8', {}
    return 200, value, 'application/json; charset=utf-8', {}
