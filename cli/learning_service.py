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
        from text_model_contract import BASE_MODEL, EXPECTED_TRAINABLE, file_sha
    except ImportError:
        return {'installed': False, 'enabled': False, 'trainedParameters': None}
    config = settings(directory)
    model, runtime = Path(config['modelPath']), Path(config['pythonPath'])
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
    return {'installed': runtime.is_file() and model.is_dir() and (model/'config.json').is_file(),
        'enabled': config['enabled'], 'model': BASE_MODEL,
        'candidateAdapterSelected': bool(config.get('adapterPath')),
        'trainedParameters': trained, 'stepsCompleted': steps}


def _overview(directory, memory, *, status=None, limit=30):
    scheduler = _scheduler(directory)
    return {'status': memory.status(), 'events': memory.list_events(status=status, limit=limit),
        'scheduler': scheduler, 'text_ai': _text_ai(directory, scheduler)}


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
                event_id=data.get('eventId'))}
        elif action == 'review':
            value = {'event': memory.review(data.get('id'), data.get('status'))}
        elif action == 'list':
            value = _overview(directory, memory, status=data.get('status'), limit=data.get('limit', 30))
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
