"""Capture editor deltas and source discovery without automatic approval.

``capture_import`` records unknown source phrases in a separate review queue;
neither a saved import nor generated prose is an approved training example.
"""
import hashlib
import json
import re

from learning_memory import LearningMemory
from learning_discovery import capture_import


def capture_edit(directory, before, after, group_id):
    memory = LearningMemory(directory)
    before = before if isinstance(before, dict) else {}
    recorded, skipped = [], 0
    for key, kind in (('titles', 'title'), ('body', 'description')):
        previous, current = before.get(key), after.get(key)
        if key == 'titles':
            previous = previous[0] if isinstance(previous, list) and previous else ''
            current = current[0] if isinstance(current, list) and current else ''
        if not isinstance(current, str) or not current.strip() or current == previous:
            continue
        payload = {'text': current, 'language': 'zh' if re.search(r'[\u3400-\u9fff]', current) else 'ru'}
        if key == 'body' and isinstance(after.get('fields'),dict):
            # A saved edit is pending evidence, never approval. Capture the
            # complete factual input now; later edits cannot change its prompt.
            try:
                from advertising_learning import prepare_payload
                from advertising_evaluation import active_rules
                titles=after.get('titles')
                title=titles[0] if isinstance(titles,list) and titles else ''
                payload=prepare_payload(after['fields'],{'title':title,'body':current},rules=active_rules(directory))
                kind='grounded_listing'
            except (ValueError,TypeError,OSError):
                # Incomplete plain-text notes remain style memory and are
                # explicitly excluded from the supervised advertising lane.
                pass
        digest = hashlib.sha256(json.dumps([group_id, kind, payload], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        try:
            recorded.append(memory.record(kind, payload, source='user-edit', group_id=group_id, event_id=digest))
        except ValueError:
            skipped += 1
    old_fields, fields = before.get('fields') or {}, after.get('fields') or {}
    items = fields.get('features')
    if isinstance(items, list) and items != old_fields.get('features'):
        excluded=[item for item in old_fields.get('features',[]) if item not in items]
        try:
            if items or excluded:
                recorded.append(memory.record('furniture', {'items': items,**({'excluded':excluded} if excluded else {})}, source='manual', group_id=group_id))
        except ValueError:
            skipped += 1
    return {'recorded': len(recorded), 'skipped': skipped, 'status': 'pending'}
