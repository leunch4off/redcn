"""Accept a freshly evaluated adapter, or restore its previous selection."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'cli'))
from model_promotion import promote, rollback, validate_report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', required=True)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--report')
    action.add_argument('--rollback', action='store_true')
    parser.add_argument('--check-only', action='store_true')
    parser.add_argument('--automatic', action='store_true')
    args = parser.parse_args()
    if args.rollback and (args.check_only or args.automatic):
        parser.error('Откат выполняется отдельно от проверки и автоматического принятия')
    result = (rollback(args.cache) if args.rollback else
              validate_report(args.cache, args.report)[1] if args.check_only else
              promote(args.cache, args.report, automatic=args.automatic))
    print(json.dumps(result, ensure_ascii=False, indent=2))
