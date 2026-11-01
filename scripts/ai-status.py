#!/usr/bin/env python3
"""Inspect local redcn AI without starting models, imports or training."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'cli'))
from ai_status import inspect_ai, russian_status, server_status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, default=Path.home() / '.cache' / 'redcn')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--server', action='store_true', help='Also check loopback /api/local/health')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    result = inspect_ai(args.cache)
    if args.server:
        result['server'] = server_status(args.port)
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else russian_status(result))


if __name__ == '__main__':
    main()
