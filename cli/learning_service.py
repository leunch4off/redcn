"""Learning-memory routes; callers enforce desktop token or phone session/role.

Recording editorial memory does not run training or promote model weights.
"""
from learning_memory import LearningMemory
from pathlib import Path
import json


def _scheduler(directory):
    try:
        import learning_scheduler
    except ImportError:
        return {'phase': 'unavailable', 'message': 'Планировщик обучения пока не установлен', 'automaticPromotion': False}
    return learning_scheduler.status(directory)


def _text_ai(directory, scheduler):
    try:
        from local_text_model import settings
        from text_model_contract import BASE_MODEL, EXPECTED_TRAINABLE, file_sha, inference_model
    except ImportError:
        return {'installed': False, 'enabled': False, 'trainedParameters': None}
    config = settings(directory)
    model, runtime = Path(config['modelPath']), Path(config['pythonPath'])
    def describe_profile(model_folder, python):
        try:
            model_folder, python = Path(model_folder), Path(python)
            path = model_folder/'config.json'
            installed = python.is_file() and path.is_file() and path.stat().st_size <= 1_000_000
            identity = inference_model(json.loads(path.read_text())) if installed else None
            return {'installed': installed, 'supported': bool(identity), 'model': identity or 'configured-local-model'}
        except (OSError, ValueError, TypeError):
            return {'installed': False, 'supported': False, 'model': 'configured-local-model'}
    text_profile = describe_profile(model, runtime)
    separate_advertising = bool(config.get('advertisingModelPath'))
    advertising = describe_profile(config.get('advertisingModelPath') or model,
        (config.get('advertisingPythonPath') or runtime) if separate_advertising else runtime)
    advertising.update(enabled=config['enabled'], automaticGenerationEnabled=config.get('advertisingEnabled') is True,
        separateProfile=separate_advertising, candidateAdapterSelected=False if separate_advertising else bool(config.get('adapterPath')),
        modelSource='pretrained-local-inference' if separate_advertising else 'shared-text-profile',
        trainedByRedcn=False if separate_advertising else None)
    receipt_folder = Path(config.get('adapterPath') or Path(directory)/'tools/text-ai/runs/first')
    receipt_path, weights = receipt_folder/'trainreceipt.json', receipt_folder/'adapters.safetensors'
    trained = None
    steps = 0
    try:
        if (not receipt_folder.is_symlink() and receipt_path.is_file() and not receipt_path.is_symlink()
                and receipt_path.stat().st_size <= 2_000_000 and weights.is_file()
                and not weights.is_symlink() and weights.stat().st_size <= 10_000_000):
            receipt = json.loads(receipt_path.read_text())
            if (isinstance(receipt, dict) and receipt.get('status') == 'trained-unaccepted'
                    and type(receipt.get('stepsCompleted')) is int and receipt['stepsCompleted'] > 0
                    and receipt.get('trainableParameters') == EXPECTED_TRAINABLE
                    and receipt.get('adapterSha256') == file_sha(weights)):
                trained, steps = receipt['trainableParameters'], receipt['stepsCompleted']
    except (OSError, ValueError, TypeError):
        pass
    return {**text_profile, 'enabled': config['enabled'],
        'candidateAdapterSelected': bool(config.get('adapterPath')),
        'trainedParameters': trained, 'stepsCompleted': steps, 'advertising': advertising}


def _activity(directory, memory):
    scheduler = _scheduler(directory)
    from learning_discovery import status as discovery_status
    return {'status': memory.status(),
        'scheduler': scheduler, 'text_ai': _text_ai(directory, scheduler),
        'discovery': discovery_status(directory, limit=0)}


def _overview(directory, memory, *, status=None, limit=30):
    from learning_discovery import status as discovery_status
    return {**_activity(directory, memory), 'events': memory.list_events(status=status, limit=limit),
            'discovery': discovery_status(directory)}


def dispatch(directory, path, data=None, *, actor='operator'):
    if path != 'learning':
        return None
    memory = LearningMemory(directory)
    if data is None:
        value = _overview(directory, memory)
    else:
        if not isinstance(data, dict):
            raise ValueError('Нужен объект запроса памяти')
        action = data.get('action', 'record')
        if action == 'record-post-stat':
            if set(data)-{'action', 'post', 'approved'}:
                raise ValueError('Неизвестные поля наблюдения публикации')
            value = {'event': memory.record_post_observation(data.get('post'), approved=data.get('approved', False))}
        elif action == 'compare-post-stats':
            if set(data) != {'action', 'eligibleTags'}:
                raise ValueError('Нужны только теги, подходящие по фактам текущей квартиры')
            value = memory.compare_post_observations(data['eligibleTags'])
        elif action in ('generate-preview', 'translate-preview'):
            import local_content
            value = (local_content.generate_preview if action == 'generate-preview'
                else local_content.translate_preview)(directory, data)
        elif action == 'record':
            value = {'event': memory.record(data.get('kind'), data.get('payload'),
                source=data.get('source', 'manual'), status=data.get('status', 'pending'),
                group_id=data.get('groupId'), supersedes=data.get('supersedes'),
                event_id=data.get('eventId'), actor=actor,
                derived_from=data.get('derivedFrom'))}
        elif action == 'review-source-phrase':
            if set(data) != {'action', 'id', 'zh'}:
                raise ValueError('Нужны ID исходной фразы и проверенный китайский перевод')
            from learning_discovery import review_phrase
            value = review_phrase(directory, data['id'], data['zh'], actor=actor)
        elif action == 'review':
            value = {'event': memory.review(data.get('id'), data.get('status'), actor=actor)}
        elif action == 'withdraw':
            if not {'action', 'id'} <= set(data) <= {'action', 'id', 'reason'}:
                raise ValueError('Для отзыва нужны ID и необязательная причина')
            value = memory.withdraw(data['id'], actor=actor, reason=data.get('reason', ''))
            from learning_scheduler import touch_foreground
            touch_foreground(directory)
        elif action == 'provenance':
            if set(data) != {'action', 'id'}:
                raise ValueError('Для истории нужен только ID примера')
            value = memory.provenance(data['id'])
        elif action == 'model-history':
            if not {'action'} <= set(data) <= {'action','limit'}:
                raise ValueError('Неверные поля истории моделей')
            from model_history import public_history
            value = public_history(directory, data.get('limit',25))
        elif action == 'list':
            value = _overview(directory, memory, status=data.get('status'), limit=data.get('limit', 30))
        elif action == 'activity':
            if set(data) != {'action'}:
                raise ValueError('Состояние обучения не принимает дополнительные поля')
            value = _activity(directory, memory)
        elif action == 'scheduler-config':
            keys = set(data)-{'action'}
            if not keys or not keys <= {'enabled', 'automaticPromotion'} or any(type(data[key]) is not bool for key in keys):
                raise ValueError('Переключатели обучения и приёмки должны быть логическими значениями')
            import learning_scheduler
            learning_scheduler.save_config(directory, {key: data[key] for key in keys})
            value = {'scheduler': _scheduler(directory)}
        elif action == 'rollback-model':
            if set(data) != {'action'}:
                raise ValueError('Откат использует только сохранённую предыдущую конфигурацию')
            from model_promotion import rollback
            value = {**rollback(directory), 'scheduler': _scheduler(directory)}
        elif action == 'retrieve':
            value = {'matches': memory.retrieve(data.get('query', ''), limit=data.get('limit', 6))}
        elif action == 'rank':
            value = {'tags': memory.rank_tags(data.get('tags', []), limit=data.get('limit', 8))}
        elif action == 'snapshot':
            # Snapshot is private on the Mac, never a training trigger or promotion.
            prepared = memory.snapshot()
            value = {'snapshot': prepared.get('manifest', {}), 'status': memory.status()}
        else:
            raise ValueError('Неизвестное действие памяти')
    return 200, value, 'application/json; charset=utf-8', {}
