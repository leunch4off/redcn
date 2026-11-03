import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('geo_update', Path(__file__).resolve().parents[1] / 'scripts/update-moscow-geo.py')
geo = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(geo)


class GeoImportTests(unittest.TestCase):
    def test_official_supplement_verifies_only_named_line_and_preserves_rgb_and_snapshot_scope(self):
        catalog={'sources': [], 'lines':[{'id':'171','ru':' Рублёво-Архангельская','color':'646464','serviceStatus':'not_verified'}], 'records':[
            {'id':'named','kind':'metro','ru':'Звенигородская','lines':['171'],'serviceStatus':'not_verified'},
            {'id':'other-line','kind':'metro','ru':'Народное Ополчение','lines':['97'],'serviceStatus':'not_verified'},
            {'id':'future','kind':'metro','ru':'Ильинская','lines':['171'],'serviceStatus':'not_verified'},
            {'id':'zh','kind':'metro','ru':'Митино','lines':['3'],'zh':'community','translationStatus':'community','translationLicense':'community-license'}]}
        geo.apply_official_evidence(catalog)
        self.assertEqual(catalog['lines'][0]['officialNumber'],'17')
        self.assertEqual(catalog['lines'][0]['color'],'646464')
        self.assertEqual(catalog['lines'][0]['colorStatus'],'snapshot-rgb-not-officially-verified')
        self.assertEqual(catalog['records'][0]['serviceStatus'],'passenger-service-confirmed')
        self.assertEqual(catalog['records'][1]['serviceStatus'],'not_verified')
        self.assertEqual(catalog['records'][2]['serviceStatus'],'not_verified')
        self.assertEqual(catalog['records'][3]['zh'],'米金诺')
        self.assertEqual(catalog['records'][3]['translationVerifiedAt'],'2026-11-01')
        self.assertNotIn('translationLicense',catalog['records'][3])
        geo.apply_official_evidence(catalog)
        self.assertEqual(len(catalog['sources']),4,'rendering is idempotent')

    def test_hh_requires_moscow_and_preserves_interchange_rows(self):
        raw = json.dumps({'id': '1', 'lines': [{'id': '1', 'name': 'Line', 'stations': [
            {'id': '1.1', 'name': 'Тест', 'lat': 55, 'lng': 37},
            {'id': '1.2', 'name': 'Тест', 'lat': 55.1, 'lng': 37.1}]}]}).encode()
        rows, lines = geo.hh_records(raw)
        self.assertEqual(len(rows), 2)
        self.assertNotEqual(rows[0]['id'], rows[1]['id'])
        self.assertEqual(lines[0]['serviceStatus'], 'not_verified')
        with self.assertRaises(ValueError): geo.hh_records(b'{"id":"2","lines":[]}')

    def test_osm_deduplicates_road_names_but_preserves_platform_objects(self):
        data = {'osm3s': {'timestamp_osm_base': '2026-10-30T00:00:00Z'}, 'elements': [
            {'type': 'way', 'id': 1, 'tags': {'highway': 'residential', 'name': 'Тестовая улица'}},
            {'type': 'way', 'id': 2, 'tags': {'highway': 'residential', 'name': 'Тестовая улица'}},
            {'type': 'node', 'id': 3, 'lat': 55, 'lon': 37, 'tags': {'highway': 'bus_stop', 'name': 'Школа'}},
            {'type': 'node', 'id': 4, 'lat': 55.1, 'lon': 37.1, 'tags': {'public_transport': 'platform', 'name': 'Школа'}}]}
        rows, timestamp = geo.osm_records(json.dumps(data).encode())
        self.assertEqual(len(rows), 3)
        self.assertEqual(next(row for row in rows if row['kind'] == 'street')['segments'], 2)
        self.assertEqual(sum(row['kind'] == 'stop' for row in rows), 2)
        self.assertEqual(timestamp, data['osm3s']['timestamp_osm_base'])
        with self.assertRaises(ValueError): geo.osm_records(b'{"elements":[],"remark":"timeout: partial data"}')

    def test_official_json_rows_and_csv_keep_original_names(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / 'metro.json'
            file.write_text(json.dumps([{'global_id': 1, 'Cells': {'NameOfStation': 'Юго-Западная'}}]), encoding='utf-8')
            rows, _ = geo.official_records(file, 'metro')
            self.assertEqual(rows[0]['zh'], '西南')
            self.assertEqual(rows[0]['ru'], 'Юго-Западная')
            file = Path(directory) / 'streets.csv'
            file.write_text('Name;Number\nТестовая улица;1\n', encoding='utf-8')
            rows, _ = geo.official_records(file, 'street')
            self.assertEqual(rows[0]['ru'], 'Тестовая улица')
            file.write_text('wrong;fields\na;b\n', encoding='utf-8')
            with self.assertRaises(ValueError): geo.official_records(file, 'street')


if __name__ == '__main__': unittest.main()
