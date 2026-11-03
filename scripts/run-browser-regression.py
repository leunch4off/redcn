"""Required browser CI: missing browsers and skipped acceptance are failures."""
from pathlib import Path
import sys
import unittest

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'tests'))
names = [
    'test_content_studio_browser.ContentStudioBrowserAcceptance',
    'test_twenty_editor_browser.TwentyEditorBrowserAcceptance',
    'test_ci_editor_browser.RequiredEditorRegression',
]
suite = unittest.defaultTestLoader.loadTestsFromNames(names)
result = unittest.TextTestRunner(verbosity=2).run(suite)
if result.skipped:
    print('Required browser acceptance was skipped; CI cannot pass.', file=sys.stderr)
raise SystemExit(0 if result.wasSuccessful() and not result.skipped else 1)
