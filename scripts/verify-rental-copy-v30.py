#!/usr/bin/env python3
"""Native Chrome check on disposable caches, plus a read-only saved-card replay.

Run this from the exact release worktree being considered for installation.
The test creates its own cache and operating-system-assigned HTTP ports. It
does not open or rewrite production cards, and does not invoke a local model.
Playwright or Chrome being unavailable is an incomplete check, not a pass.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
import traceback
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE_FILES = (
    'web/rental-copy.mjs', 'web/engine.mjs', 'web/app.js',
    'web/language-studio.mjs', 'web/template-catalog.mjs', 'web/phone.mjs',
    'cli/language.mjs', 'cli/language_service.py', 'cli/local_server.py',
    'cli/node_runtime.py', 'tests/test_phone_browser.py',
    'tests/test_language_studio_browser.py', 'tests/test_phone_language_browser.py',
    'scripts/audit-rental-copy.mjs', 'scripts/verify-rental-copy-v30.py',
    'docs/index.html', 'docs/phone.html', 'data/build-identity.json',
)
TEST_METHOD = 'test_rewrites_change_real_words_keep_manual_other_parts_and_survive_reload'


def source_hashes():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            if (ROOT / name).is_file() else None for name in SOURCE_FILES}


def current_revision():
    completed = subprocess.run(
        ['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], capture_output=True,
        text=True, timeout=10, check=False)
    return completed.stdout.strip() if completed.returncode == 0 else None


def write_private_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    # The receipt contains no actual card text, but remains private with its input.
    with path.open('w', encoding='utf-8') as stream:
        os.chmod(path, 0o600)
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cards', type=Path,
                        help='Optional private JSON card snapshot; read-only replay of its newest 65')
    parser.add_argument('--expected-revision', help='Required base Git HEAD for this run')
    args = parser.parse_args()
    output = args.output.expanduser().resolve()
    started = time.monotonic()
    before = source_hashes()
    revision = current_revision()
    report = {
        'version': 1,
        'createdAt': datetime.now(timezone.utc).isoformat(),
        'scope': 'native Chrome; iPhone-sized viewport; not physical iPhone/Safari acceptance',
        'productionMutation': False,
        'localModelInvoked': False,
        'platform': platform.platform(),
        'macOS': platform.mac_ver()[0] or None,
        'python': sys.version.split()[0],
        'gitBaseRevision': revision,
        'expectedRevision': args.expected_revision,
        'sourceSha256': before,
        'browser': {'attempted': False, 'passed': False},
        'recentCardReplay': {'requested': args.cards is not None},
    }
    errors = []
    try:
        if args.expected_revision and revision != args.expected_revision:
            raise RuntimeError('Git base revision does not match --expected-revision')
        if any(value is None for value in before.values()):
            raise RuntimeError('The source set is incomplete: ' + ', '.join(
                name for name, value in before.items() if value is None))
        # The HTTP server serves the generated, self-contained docs shell.
        # Hashing only web/app.js would accidentally verify an older UI.
        desktop_shell = (ROOT / 'docs/index.html').read_text(encoding='utf-8')
        phone_shell = (ROOT / 'docs/phone.html').read_text(encoding='utf-8')
        if ('contentState.copyHistory=rememberPublication' not in desktop_shell
                or 'function rentalCopyStats' not in desktop_shell
                or 'Словарных пар RU → ZH:' not in phone_shell):
            raise RuntimeError('Generated docs shells are stale. Run npm run build in this worktree before verification.')
        report['builtShellPreflight'] = True
        sys.path.insert(0, str(ROOT / 'cli'))
        sys.path.insert(0, str(ROOT / 'tests'))
        from test_language_studio_browser import LanguageStudioAcceptance
        from test_phone_language_browser import PhoneLanguageBrowserAcceptance

        case = LanguageStudioAcceptance(TEST_METHOD)
        phone_case = PhoneLanguageBrowserAcceptance('test_language_stats_show_finite_styles_and_dictionary_records')
        log = io.StringIO()
        report['browser']['attempted'] = True
        browser_started = time.monotonic()
        result = unittest.TextTestRunner(stream=log, verbosity=2).run(unittest.TestSuite([case, phone_case]))
        proof = getattr(case, 'copy_acceptance_receipt', None)
        phone_proof = getattr(phone_case, 'language_truth_receipt', None)
        report['browser'].update({
            'test': 'LanguageStudioAcceptance.' + TEST_METHOD,
            'testsRun': result.testsRun,
            'failures': len(result.failures),
            'errors': len(result.errors),
            'skipped': [reason for _, reason in result.skipped],
            'elapsedSeconds': round(time.monotonic() - browser_started, 3),
            'proof': proof,
            'phoneProof': phone_proof,
            'log': log.getvalue(),
            'passed': bool(result.wasSuccessful() and result.testsRun == 2
                           and not result.skipped and proof
                           and proof.get('rewrites') == 20
                           and proof.get('distinctNormalizedBodies') == 20
                           and proof.get('reload')
                           and not proof.get('pageErrors') and phone_proof
                           and not phone_proof.get('pageErrors')),
        })
        if not report['browser']['passed']:
            errors.append('Native browser check failed or was skipped; inspect browser.log')

        if args.cards is not None:
            from node_runtime import resolve_node

            replay_path = output.with_name(output.stem + '-recent-cards.json')
            completed = subprocess.run(
                [resolve_node(), str(ROOT / 'scripts/audit-rental-copy.mjs'),
                 '--cards', str(args.cards.expanduser().resolve()),
                 '--output', str(replay_path), '--recent', '65', '--variants', '1'],
                cwd=ROOT, capture_output=True, text=True, encoding='utf-8',
                timeout=120, check=False)
            if completed.returncode != 0:
                raise RuntimeError('Saved-card replay failed: ' + completed.stderr[-4000:])
            replay = json.loads(replay_path.read_text(encoding='utf-8'))
            report['recentCardReplay'].update({
                'reportFile': replay_path.name, 'aggregate': replay,
                'passed': bool(replay.get('passed') and replay.get('selectedMostRecentCards') == 65
                               and replay.get('distinctInitialFamilies', 0) > 1
                               and replay.get('distinctInitialLeadFrames', 0) > 1),
            })
            if not report['recentCardReplay']['passed']:
                errors.append('Saved-card replay did not cover 65 cards with multiple initial styles')
    except Exception as error:
        errors.append(str(error))
        report['exception'] = traceback.format_exc()
    finally:
        after = source_hashes()
        report['sourceUnchangedDuringCheck'] = before == after
        if before != after:
            report['sourceSha256After'] = after
            errors.append('Source files changed during verification; repeat on a stable worktree')
        report['elapsedSeconds'] = round(time.monotonic() - started, 3)
        report['errors'] = errors
        report['passed'] = not errors and report['browser']['passed']
        write_private_json(output, report)
    print(json.dumps({
        'passed': report['passed'], 'output': str(output),
        'browserPassed': report['browser']['passed'],
        'recentCardsPassed': report['recentCardReplay'].get('passed'),
        'sourceUnchangedDuringCheck': report['sourceUnchangedDuringCheck'],
        'errors': errors,
    }, ensure_ascii=False))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
