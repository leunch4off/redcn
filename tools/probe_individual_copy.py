#!/usr/bin/env python3
"""Private, read-only-on-cards inference proof for the live individual contract.

Input and output contain listing text: keep them outside the repository. This
does not change settings, adapters, cards, or learning approvals. Model-resource
coordination and measured token telemetry are the production runtime's own.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'cli'))
import local_text_model
from individual_copy import build_brief, generation_request, parse_individual_text, validate_individual


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cache', type=Path, default=Path.home()/'.cache'/'redcn')
    parser.add_argument('--model-path', type=Path)
    parser.add_argument('--python-path', type=Path)
    parser.add_argument('--no-adapter', action='store_true')
    parser.add_argument('--seed', type=int, default=31)
    parser.add_argument('--case', type=int)
    args = parser.parse_args()
    if args.input.resolve() == args.output.resolve():
        parser.error('Input and output must be different files')
    rows = json.loads(args.input.read_text())
    if not isinstance(rows, list) or not 1 <= len(rows) <= 24:
        parser.error('Input must be a list of 1–24 field examples')
    original_settings = local_text_model.settings
    def probe_settings(directory):
        config = original_settings(directory)
        if args.model_path:
            config['modelPath'] = str(args.model_path)
            config['advertisingModelPath'] = None
            config['advertisingPythonPath'] = None
        if args.python_path:
            config['pythonPath'] = str(args.python_path)
        if args.no_adapter or args.model_path:
            config['adapterPath'] = None
        return config
    local_text_model.settings = probe_settings
    results = []
    for index, row in enumerate(rows):
        if args.case is not None and args.case != index:
            continue
        started = time.monotonic()
        fields = row.get('fields', row)
        brief = build_brief(fields)
        request = generation_request(brief, seed=args.seed+index)
        result = local_text_model.generate(request, args.cache)
        draft = None
        issues = []
        if result.get('status') == 'review':
            try:
                draft = parse_individual_text(result.get('text', ''))
                issues = validate_individual(draft, brief)
                if result.get('stopReason') == 'length' or result.get('ai', {}).get('generatedTokens', 0) >= request['maxTokens']:
                    issues.insert(0, 'Model reached token limit; response is not confirmed complete')
            except (ValueError, TypeError) as error:
                issues = [type(error).__name__+': '+str(error)[:240]]
        else:
            issues = [result.get('message', 'No finished model response')]
        receipt = {'case': index, 'contract': brief['contract'], 'factsHash': brief['factsHash'],
            'requestSha256': hashlib.sha256(json.dumps(request, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
            'inventoryCount': len(brief['inventory']), 'sourceContextCharacters': brief['sourceContextCharacters'],
            'elapsedSeconds': time.monotonic()-started, 'accepted': not issues,
            'issues': issues, 'draft': draft, 'rawResult': result}
        results.append(receipt)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps({'contract': brief['contract'], 'cases': results}, ensure_ascii=False, indent=2))
        args.output.chmod(0o600)
        print(json.dumps({key: receipt[key] for key in ('case', 'inventoryCount', 'sourceContextCharacters', 'elapsedSeconds', 'accepted', 'issues')}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
