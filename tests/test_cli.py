import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('redcn', Path(__file__).resolve().parents[1] / 'cli/redcn.py')
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


class CLITests(unittest.TestCase):
    def test_url_restrictions(self):
        for url in ['http://cian.ru/flat/1', 'https://cian.ru.evil.org/x', 'https://localhost/', 'https://user:secret@cian.ru/', 'https://avito.ru:8443/']:
            with self.assertRaises(ValueError):
                cli.allowed_url(url, resolve=False)
        with patch.object(cli.socket,'getaddrinfo',return_value=[(2,1,6,'',('127.0.0.1',443))]):
            with self.assertRaisesRegex(ValueError,'Непубличный'):
                cli.allowed_url('https://www.cian.ru/')
        with self.assertRaises(ValueError):
            cli.CheckedRedirect().redirect_request(None,None,302,'',{},'https://evil.example/')

    def test_filter_boundaries_and_unknown(self):
        self.assertTrue(cli.eligible('owner', None))
        for n in [0, 25, 50]:
            self.assertTrue(cli.eligible('agent', n))
        for n in [50.01, 100, -1, None, 'неизвестно']:
            self.assertFalse(cli.eligible('agent', n))
        self.assertFalse(cli.eligible('unknown', 0))

    def test_jsonld_and_og_photo_extraction(self):
        markup = '<meta property="og:image" content="https://images.cdn-cian.ru/photo.jpg"><script type="application/ld+json">'+json.dumps({'description':'2-комнатная квартира, 52 м². Аренда 85 000 ₽.', 'image':['https://images.cdn-cian.ru/second.jpg']})+'</script><div>Реклама другой квартиры за 100 000 рублей</div>'
        result = cli.parse_html(markup, 'https://www.cian.ru/rent/flat/1/')
        self.assertIn('52 м²', result['text'])
        self.assertNotIn('Реклама', result['text'])
        self.assertEqual(len(result['images']), 2)

    def test_challenge_is_reported(self):
        with self.assertRaisesRegex(ValueError, 'не обходится'):
            cli.parse_html('<html>Verify you are human</html>', 'https://cian.ru/')

    def test_sqlite_cache_and_refresh(self):
        with tempfile.TemporaryDirectory() as folder:
            conn = cli.open_cache(Path(folder))
            with patch.object(cli, 'fetch_bytes', return_value=(b'<p>Apartment description, enough text for the fixture.</p>', 'text/html')) as fetch:
                first = cli.load_listing('https://cian.ru/flat/1', conn)
                second = cli.load_listing('https://cian.ru/flat/1', conn)
                third = cli.load_listing('https://cian.ru/flat/1', conn, refresh=True)
                self.assertFalse(first['cached']);self.assertTrue(second['cached']);self.assertFalse(third['cached']);self.assertEqual(fetch.call_count, 2)
            conn.close()

    def test_photo_manifest_and_signature(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(cli, 'fetch_bytes', side_effect=[(b'\xff\xd8\xfftest', 'image/jpeg'), (b'not-a-png', 'image/png')]):
                result = cli.download_photos(['https://images.cdn-cian.ru/1.jpg', 'https://images.cdn-cian.ru/2.png'], Path(folder))
            self.assertTrue(Path(result[0]['path']).exists());self.assertIn('error', result[1]);self.assertTrue((Path(folder) / 'manifest.json').exists())

    def test_batch_only_new_successful_eligible_items(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'batch.json'
            path.write_text(json.dumps([{'url':'https://cian.ru/1','seller':'owner'},{'url':'https://avito.ru/2','seller':'agent','commission':50},{'url':'https://avito.ru/3','seller':'agent','commission':51}]))
            def run():
                stream=io.StringIO()
                with contextlib.redirect_stdout(stream),patch.object(cli,'load_listing',return_value={'text':'fixture','images':[]}),patch.object(cli,'generate',return_value={'titles':['中文'],'body':'中文','tags':'#莫斯科'}):
                    cli.main(['--batch',str(path),'--cache-dir',folder,'--json'])
                return json.loads(stream.getvalue())
            self.assertEqual([x['status'] for x in run()],['new','new','filtered'])
            self.assertEqual([x['status'] for x in run()],['seen','seen','filtered'])

    def test_actual_shared_generator(self):
        result=cli.generate('2-комнатная квартира, 52 м². Аренда 85 000 ₽, залог 35 000 ₽.',{'universities':'МГУ | 30 | metro','features':['fridge']})
        self.assertEqual(result['fields']['price'],'85000');self.assertEqual(len(result['titles']),3);self.assertIn('冰箱',result['body']);self.assertIn('#莫大租房',result['tags'])

    def test_cli_local_file_json_overrides_output_and_variants(self):
        with tempfile.TemporaryDirectory() as folder:
            text=Path(folder)/'input.txt';text.write_text('Квартира 52 м². Аренда 85 000 ₽.',encoding='utf-8')
            data=Path(folder)/'data.json';data.write_text(json.dumps({'price':'68000','layout':'euro2'}))
            rules=Path(folder)/'rules.json';rules.write_text(json.dumps({'терраса':'露台'}))
            prefs=Path(folder)/'prefs.json';prefs.write_text(json.dumps({'brandTags':'#TEST','benefits':'Наш тест'}))
            output=Path(folder)/'output.json'
            cli.main(['--input',str(text),'--data',str(data),'--rules',str(rules),'--prefs',str(prefs),'--variant','1','--json','--output',str(output),'--cache-dir',folder])
            result=json.loads(output.read_text());self.assertEqual(result['fields']['price'],'68000');self.assertIn('#TEST',result['tags']);self.assertEqual(result['fields']['titleVariant'],1)

    def test_cli_interactive_and_argument_urls(self):
        with tempfile.TemporaryDirectory() as folder:
            for argv in [[],['https://avito.ru/123']]:
                with patch('builtins.input',return_value='https://cian.ru/123'),patch.object(cli,'load_listing',return_value={'text':'fixture','images':[]}) as loader,patch.object(cli,'generate',return_value={'titles':['中文'],'body':'正文','tags':'#TEST','unknown':[]}),contextlib.redirect_stdout(io.StringIO()):
                    cli.main([*argv,'--cache-dir',folder])
                    self.assertEqual(loader.call_count,1)

if __name__ == '__main__':
    unittest.main()
