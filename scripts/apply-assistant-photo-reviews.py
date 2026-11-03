#!/usr/bin/env python3
"""Apply a private, reviewed photo corpus to matching originals only.

Default is a dry run. This imports image observations, never model weights or
human training labels. Text and hand-edited facts are retained.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'cli'))
from fact_ledger import record_fact_revision, validate_result_fields
from photo_teacher_review import merge_reviews


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--receipt', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    dataset = json.loads(args.dataset.read_text())
    if (dataset.get('provenance') != 'assistant-visual-review'
            or dataset.get('humanVerified') is not False
            or dataset.get('exhaustive') is not False
            or not isinstance(dataset.get('cards'), list)):
        raise ValueError('Нужен корпус с явным происхождением разбора')
    rows = dataset['cards']
    if len(rows) > 1000 or len({row['cardId'] for row in rows}) != len(rows):
        raise ValueError('Повторяющиеся карточки или превышение лимита')
    database = args.cache / 'listings.sqlite3'
    if not database.is_file():
        raise ValueError('База карточек не найдена')
    receipt = {'version': 1, 'operation': 'assistant-visual-review-import',
        'at': datetime.now(timezone.utc).isoformat(), 'applied': args.apply,
        'datasetSha256': hashlib.sha256(args.dataset.read_bytes()).hexdigest(),
        'humanVerified': False, 'weightsChanged': False, 'sourceOrderPreserved': True, 'cards': []}
    def persist_receipt():
        # A dropped tool connection must leave a reviewable partial receipt.
        temporary = args.receipt.with_suffix('.json.tmp')
        with temporary.open('w', encoding='utf-8') as output:
            temporary.chmod(0o600)
            json.dump(receipt, output, ensure_ascii=False, indent=2)
            output.write('\n')
        temporary.replace(args.receipt)
    with sqlite3.connect(database, timeout=30) as db:
        if args.apply:
            backup_path = args.receipt.with_suffix('.sqlite3')
            if backup_path.exists():
                raise ValueError('Резервная копия уже существует; выберите новый receipt')
            with sqlite3.connect(backup_path) as backup:
                db.backup(backup)
            backup_path.chmod(0o600)
            receipt['backup'] = str(backup_path)
        persist_receipt()
        for row in rows:
            card_id = row['cardId']
            if not re.fullmatch(r'[a-f0-9]{32}', card_id):
                raise ValueError('Неверный ID карточки')
            try:
                db.execute('BEGIN IMMEDIATE' if args.apply else 'BEGIN')
                stored = db.execute('SELECT updated,payload FROM results WHERE id=?', (card_id,)).fetchone()
                if not stored:
                    receipt['cards'].append({'id': card_id, 'state': 'missing'})
                    db.rollback()
                    continue
                current = json.loads(stored[1])
                # Bind to actual bytes, not just a pasted URL or detector cache.
                for entry in row['photos']:
                    digest = entry['review']['originalId']
                    if not re.fullmatch(r'[a-f0-9]{64}', digest):
                        raise ValueError('Неверный SHA-256 фотографии')
                    path = args.cache / 'media' / digest
                    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                        raise ValueError('Оригинал фотографии отсутствует или изменён')
                updated, summary = merge_reviews(current, row['photos'])
                validate_result_fields(updated)
                if args.apply:
                    # Reviewing older photos is not a new import. Preserve the
                    # library's chronological order; the review has its own time.
                    changed = db.execute('UPDATE results SET payload=? WHERE id=? AND updated=?',
                        (json.dumps(updated, ensure_ascii=False), card_id, stored[0]))
                    if changed.rowcount != 1:
                        raise ValueError('Карточка изменилась во время применения обзора')
                    record_fact_revision(db, card_id, updated['fields'], actor='assistant-visual-review', kind='confirm')
                    db.commit()
                else:
                    db.rollback()
                receipt['cards'].append({'id': card_id, 'state': 'applied' if args.apply else 'preview', **summary})
                persist_receipt()
            except Exception:
                db.rollback()
                receipt['interruptedAtCard'] = card_id
                persist_receipt()
                raise
    receipt['summary'] = {'cards': len(receipt['cards']),
        'reviewedPhotos': sum(row.get('reviewedPhotos', 0) for row in receipt['cards']),
        'positiveWitnesses': sum(row.get('positiveWitnesses', 0) for row in receipt['cards']),
        'addedFeatures': sum(len(row.get('addedFeatures', [])) for row in receipt['cards']),
        'conflicts': sum(len(row.get('conflicts', [])) for row in receipt['cards']),
        'skipped': sum(len(row.get('skipped', [])) for row in receipt['cards'])}
    persist_receipt()
    print(json.dumps(receipt['summary'], ensure_ascii=False))


if __name__ == '__main__':
    main()
