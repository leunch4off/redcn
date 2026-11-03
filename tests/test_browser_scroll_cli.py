"""The CLI choice reaches Chrome; cached data needs no browser session."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'cli'))
import redcn


class BrowserScrollCLI(unittest.TestCase):
    def test_explicit_strategy_reaches_loader_and_cache_avoids_chrome(self):
        import browser_import
        with tempfile.TemporaryDirectory() as folder:
            connection=redcn.open_cache(Path(folder))
            try:
                fixture={'url':'https://www.cian.ru/rent/flat/123/', 'text':'Квартира 50 м², цена 80000 рублей.', 'images':[]}
                with patch.object(browser_import,'load_browser',return_value=fixture) as browser:
                    first=redcn.load_listing(fixture['url'],connection,loader='chrome',browser_scroll='page')
                    self.assertFalse(first['cached'])
                    self.assertEqual(browser.call_args.kwargs['scroll_strategy'],'page')
                    second=redcn.load_listing(fixture['url'],connection,loader='chrome',browser_scroll='sections')
                    self.assertTrue(second['cached'])
                    self.assertEqual(browser.call_count,1)
                with self.assertRaisesRegex(ValueError,'Режим прокрутки'):
                    redcn.load_listing(fixture['url'],connection,browser_scroll='unknown')
            finally:
                connection.close()

    def test_cli_applies_choice_to_single_and_batch_imports(self):
        with tempfile.TemporaryDirectory() as folder:
            batch=Path(folder)/'batch.json'
            batch.write_text(json.dumps([{'url':'https://www.cian.ru/rent/flat/123/','seller':'owner'}]))
            for arguments in [['https://www.cian.ru/rent/flat/123/'],['--batch',str(batch)]]:
                with self.subTest(arguments=arguments),contextlib.redirect_stdout(io.StringIO()),patch.object(redcn,'load_listing',return_value={'url':'https://www.cian.ru/rent/flat/123/','text':'Квартира 50 м², 80000 рублей.','images':[]}) as loader,patch.object(redcn,'generate',return_value={'titles':['测试'],'body':'测试','tags':'#莫斯科'}):
                    self.assertEqual(redcn.main([*arguments,'--loader','chrome','--browser-scroll','page','--json','--cache-dir',folder]),0)
                    self.assertEqual(loader.call_args.kwargs['browser_scroll'],'page')
