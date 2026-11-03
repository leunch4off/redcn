#!/usr/bin/env python3
"""Plan, validate, or explicitly apply independently reviewed body replacements.

The default mode is plan. Inputs and outputs are private JSON artifacts; no
model inference or training approval is performed by any subcommand.
"""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'cli'))
from individual_refresh import apply_refresh, plan_refresh, prepare_refresh


def read_json(path):
    path = Path(path)
    if not path.is_file() or path.stat().st_size > 50_000_000:
        raise ValueError('JSON-документ отсутствует или слишком велик')
    return json.loads(path.read_text(encoding='utf-8'))


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] not in ('plan', 'prepare', 'apply', '-h', '--help'):
        argv.insert(0, 'plan')
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    plan = commands.add_parser('plan', help='Только прочитать текущие карточки и SHA-256')
    prepare = commands.add_parser('prepare', help='Проверить отдельные описания без сохранения')
    apply = commands.add_parser('apply', help='Применить отдельно проверенный пакет с CAS и backup')
    for command in (plan, prepare, apply):
        command.add_argument('--cache', required=True, type=Path)
        command.add_argument('--out', required=True, type=Path, help='Новый private JSON; существующий файл не заменяется')
    plan.add_argument('--id', action='append', dest='identifiers')
    prepare.add_argument('--plan', required=True, type=Path)
    prepare.add_argument('--candidates', required=True, type=Path)
    prepare.add_argument('--reconcile-photo-journal', action='store_true')
    apply.add_argument('--prepared', required=True, type=Path)
    apply.add_argument('--review', required=True, type=Path)
    apply.add_argument('--backup-dir', required=True, type=Path)
    args = parser.parse_args(argv)
    output = None
    applied = False
    try:
        # Reserve a writable private receipt before any database mutation.
        # Failed operations leave an empty artifact, never a false success.
        if args.out.exists() or args.out.is_symlink() or not args.out.parent.is_dir():
            raise ValueError('Выходной файл уже существует или его папка отсутствует')
        descriptor = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                             getattr(os, 'O_NOFOLLOW', 0), 0o600)
        output = os.fdopen(descriptor, 'w', encoding='utf-8')
        if args.command == 'plan':
            result = plan_refresh(args.cache, args.identifiers)
        elif args.command == 'prepare':
            result = prepare_refresh(args.cache, read_json(args.plan), read_json(args.candidates),
                                     reconcile_photo_journal=args.reconcile_photo_journal)
        else:
            result = apply_refresh(args.cache, read_json(args.prepared), read_json(args.review),
                                   backup_directory=args.backup_dir)
            applied = True
        json.dump(result, output, ensure_ascii=False, sort_keys=True, allow_nan=False)
        output.write('\n')
        output.flush()
        os.fsync(output.fileno())
        print(json.dumps({'command': args.command, 'rows': len(result['rows']),
            'counts': result.get('counts'), 'applied': result.get('applied', 0), 'modelCalls': 0}))
        return 0
    except (ValueError, OSError, sqlite3.Error) as error:
        prefix = ('Изменения уже сохранены, но выходной документ записать не удалось; '
                  'точные оригиналы и журнал individual_body_revisions сохранены. '
                  if applied else 'Обновление описаний: ')
        print(prefix+str(error), file=sys.stderr)
        return 1
    finally:
        if output is not None:
            output.close()


if __name__ == '__main__':
    raise SystemExit(main())
