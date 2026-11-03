"""Independent provider contracts and the shared CLI use the same source parser."""
import json
from pathlib import Path
import sys
import unittest
import tempfile
import time
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
import redcn
from browser_dom import source_scripts
from source_adapters import adapter_for

class SourceAdapterTests(unittest.TestCase):
    def test_provider_contracts_never_contain_other_platform_markers(self):
        cian=adapter_for('https://www.cian.ru/rent/flat/1/')
        avito=adapter_for('https://www.avito.ru/moskva/kvartiry/flat_1')
        self.assertNotIn('item-view',json.dumps(cian))
        self.assertNotIn('data-name',json.dumps(avito))
        self.assertEqual(set(cian['sections']),{'gallery','description','characteristics'})
        self.assertEqual(set(avito['headings']),{'Особенности','О квартире','Условия аренды','Правила','О доме','Расположение','Описание'})
        with self.assertRaises(ValueError):adapter_for('https://avito.ru.evil.example/')

    def test_snapshot_and_probe_are_bound_to_the_chosen_provider(self):
        for site in ['cian','avito']:
            snapshot,probe=source_scripts(adapter_for('https://www.'+site+'.ru/'))
            self.assertIn('const config = ',snapshot);self.assertIn('"id":"'+site+'"',probe)
        self.assertNotEqual(source_scripts(adapter_for('https://cian.ru/')),source_scripts(adapter_for('https://avito.ru/')))

    def test_cli_avito_fixture_has_structured_fields_without_recommendation_or_private_data(self):
        try:
            from node_runtime import resolve_node
            resolve_node()
        except Exception as error:self.skipTest(str(error))
        markup=(Path(__file__).parent/'fixtures/avito-52.html').read_text()
        result=redcn.parse_html(markup,'https://www.avito.ru/moskva/kvartiry/flat_7462093377')
        self.assertEqual(result['platform'],'avito');self.assertEqual(result['fields']['price'],'75000')
        self.assertEqual(result['fields']['living'],'30.6');self.assertEqual(result['fields']['petPolicy'],'no')
        self.assertEqual(len(result['images']),12);self.assertNotIn('PRIVATE_SELLER_PHONE',json.dumps(result))
        self.assertNotIn('wrong-other-flat',json.dumps(result))

    def test_updated_avito_contract_refreshes_legacy_cache_once(self):
        url='https://www.avito.ru/moskva/kvartiry/flat_7462093377'
        with tempfile.TemporaryDirectory() as folder:
            connection=redcn.open_cache(Path(folder))
            try:
                connection.execute('INSERT INTO cache VALUES (?,?,?)',(url,time.time(),json.dumps({'text':'old cached shell','images':[]})))
                connection.commit()
                markup=(Path(__file__).parent/'fixtures/avito-52.html').read_text()
                with patch.object(redcn,'fetch_bytes',return_value=(markup.encode(),'text/html')) as fetch:
                    first=redcn.load_listing(url,connection,loader='http')
                    second=redcn.load_listing(url,connection,loader='http')
                self.assertFalse(first['cached']);self.assertTrue(second['cached']);self.assertEqual(fetch.call_count,1)
                self.assertEqual(second['parserVersion'],'avito-v2-gallery-variants')
            finally:connection.close()

    def test_updated_cian_contract_replaces_a_legacy_one_photo_cache_once_then_reuses_new_capture(self):
        url='https://www.cian.ru/rent/flat/123456789/'
        with tempfile.TemporaryDirectory() as folder:
            connection=redcn.open_cache(Path(folder))
            try:
                connection.execute('INSERT INTO cache VALUES (?,?,?)',(url,time.time(),json.dumps({'text':'old one-photo capture','images':['https://images.cdn-cian.ru/legacy.jpg'],'parserVersion':'cian-v2-gallery-variants'})));connection.commit()
                markup=(Path(__file__).parent/'fixtures/cian-54.html').read_text()
                with patch.object(redcn,'fetch_bytes',return_value=(markup.encode(),'text/html')) as fetch:
                    first=redcn.load_listing(url,connection,loader='http');second=redcn.load_listing(url,connection,loader='http')
                self.assertFalse(first['cached']);self.assertTrue(second['cached']);self.assertEqual(fetch.call_count,1)
                self.assertEqual(second['parserVersion'],'cian-v3-observed-main-gallery');self.assertNotIn('legacy.jpg',json.dumps(second))
                self.assertEqual(second['images'],first['images']);self.assertEqual(second['photoItems'],first['photoItems'])
            finally:connection.close()

    def test_browser_capture_without_a_js_parser_stamp_is_cached_with_the_active_gallery_contract(self):
        url='https://www.cian.ru/rent/flat/123456789/'
        with tempfile.TemporaryDirectory() as folder:
            connection=redcn.open_cache(Path(folder))
            try:
                with patch('browser_import.load_browser',return_value={'url':url,'text':'captured synthetic listing','images':[]}) as browser:
                    first=redcn.load_listing(url,connection,loader='chrome');second=redcn.load_listing(url,connection,loader='chrome')
                self.assertEqual(browser.call_count,1);self.assertTrue(second['cached']);self.assertEqual(first['parserVersion'],second['parserVersion'])
            finally:connection.close()

    def test_failed_manual_refresh_keeps_previous_cache_payload_and_version_for_both_sources(self):
        for platform,url in [('cian','https://www.cian.ru/rent/flat/123456789/'),('avito','https://www.avito.ru/moskva/kvartiry/flat_7462093377')]:
            with self.subTest(platform=platform),tempfile.TemporaryDirectory() as folder:
                connection=redcn.open_cache(Path(folder));old={'text':'previous readable capture','images':['https://images.cdn-cian.ru/legacy.jpg'],'parserVersion':platform+'-v1'}
                try:
                    connection.execute('INSERT INTO cache VALUES (?,?,?)',(url,time.time(),json.dumps(old)));connection.commit()
                    with patch.object(redcn,'fetch_bytes',side_effect=OSError('synthetic source temporarily unavailable')):
                        with self.assertRaises(OSError):redcn.load_listing(url,connection,loader='http')
                    saved=json.loads(connection.execute('SELECT payload FROM cache WHERE url=?',(url,)).fetchone()[0]);self.assertEqual(saved,old)
                    self.assertNotEqual(saved['parserVersion'],adapter_for(url)['parserVersion'])
                finally:connection.close()

if __name__=='__main__':unittest.main()
