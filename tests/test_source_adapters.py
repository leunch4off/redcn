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

    def test_updated_avito_contract_refreshes_legacy_cache_once_without_changing_cian_cache(self):
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
                self.assertEqual(second['parserVersion'],'avito-v1')
            finally:connection.close()

if __name__=='__main__':unittest.main()
