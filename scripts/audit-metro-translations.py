#!/usr/bin/env python3
"""Inventory unresolved Chinese names without guessing translations or aliases."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def audit(catalog):
    lines = {row['id']: row for row in catalog['lines']}
    stations = [row for row in catalog['records'] if row['kind'] == 'metro']
    mcd = {identifier for identifier, row in lines.items()
           if row.get('ru', '').startswith('МЦД-')}
    if not mcd:
        raise ValueError('No MCD lines recognized; do not report false zero coverage')
    missing = []
    for row in stations:
        matched = set(row.get('lines', [])) & mcd
        if matched and not row.get('zh', '').strip():
            missing.append({'id': row['id'], 'ru': row['ru'],
                            'lineIds': sorted(matched),
                            'linesRu': [lines[value]['ru'] for value in sorted(matched)],
                            'translationStatus': row.get('translationStatus', 'untranslated')})
    unmatched = []
    for fact in catalog.get('publishedStationNameFacts', []):
        matches = [row['id'] for row in stations
                   if row['ru'] == fact['ru'] and set(row.get('lines', [])) & set(fact['lineIds'])]
        if not matches:
            unmatched.append({**fact, 'reason': 'No exact station-name and line-ID match in source snapshot; no evidence copied to other lines.'})
    return {'version': 1, 'checkedAt': datetime.now(timezone.utc).isoformat(),
            'mode': 'read-only-snapshot-translation-inventory',
            'catalogueMutated': False, 'currentOfficialNetworkCompletenessVerified': False,
            'stationRows': len(stations), 'mcdRows': sum(bool(set(row.get('lines', [])) & mcd) for row in stations),
            'missingMcdRows': len(missing),
            'missingByLine': {line['ru']: sum(identifier in row['lineIds'] for row in missing)
                              for identifier, line in lines.items() if identifier in mcd},
            'translationStatuses': dict(sorted(Counter(row.get('translationStatus', 'untranslated') for row in stations).items())),
            'missing': missing, 'unmatchedPublishedFacts': unmatched,
            'aliasReview': catalog.get('publishedNameAliasReview', []),
            'limitations': ['Counts refer to preserved source snapshot rows, not a verified complete current transit registry.',
                            'Published contractor and community Chinese names are not operator-certified names.',
                            'Aliases do not transfer coordinates, line membership or current-service status.']}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalogue', type=Path, default=ROOT / 'data/moscow-geo.json')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    result = json.dumps(audit(json.loads(args.catalogue.read_text())), ensure_ascii=False, indent=2) + '\n'
    if args.output:
        args.output.write_text(result)
    else:
        print(result, end='')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
