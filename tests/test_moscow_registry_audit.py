import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import contextlib
import io
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('registry_audit',ROOT/'scripts/audit-moscow-registry.py');audit=importlib.util.module_from_spec(spec);spec.loader.exec_module(audit)

class RegistryAuditTests(unittest.TestCase):
    def test_comparison_keeps_duplicate_objects_and_reports_missing_names_and_publisher_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);data=[{'global_id':1,'Cells':{'Name':'Улица Теста'}},{'global_id':2,'Cells':{'Name':'Парк'}},{'global_id':3,'Cells':{'Name':'Парк'}}];raw=json.dumps(data).encode();(root/'stops.json').write_bytes(raw)
            meta=dict(kind='stop',file='stops.json',url='https://data.mos.ru/opendata/example',asOf='2026-10-30',scope='Synthetic test only; not a downloaded registry',sha256=hashlib.sha256(raw).hexdigest(),expectedRecords=3)
            catalog={'records':[{'id':'osm:1','kind':'stop','ru':'Улица теста'},{'id':'osm:2','kind':'stop','ru':'Улица теста'}]};before=json.dumps(catalog)
            result=audit.compare(catalog,[meta],manifest_root=root);report=result['reports'][0]
            self.assertEqual(report['recognizedRecords'],3);self.assertEqual(report['uniqueNames'],2);self.assertEqual(report['missingNames'],[{'ru':'Парк','recordIds':['mos:stop:2','mos:stop:3']}]);self.assertEqual(report['asOf'],'2026-10-30');self.assertFalse(report['objectIdentityVerified']);self.assertFalse(report['latestCompleteRegistryIndependentlyVerified']);self.assertEqual(json.dumps(catalog),before)
            for changes in ({'sha256':'0'*64},{'expectedRecords':4},{'url':'https://example.com/data'},{'asOf':'2099-01-01'},{'scope':''}):
                with self.subTest(changes=changes),self.assertRaises(ValueError):audit.compare(catalog,[{**meta,**changes}],manifest_root=root)

    def test_cli_official_import_does_not_replace_the_existing_geographical_kind(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'data').mkdir();(root/'web').mkdir();(root/'scripts').mkdir()
            (root/'scripts/geo-catalog-runtime.mjs').write_text('export function fixture(){}')
            catalog={'version':1,'sources':[],'lines':[],'records':[{'id':'osm:old','kind':'stop','ru':'Старая остановка','zh':'旧站','source':'osm','lat':55.7,'lng':37.5},{'id':'hh:one','kind':'metro','ru':'Станция','zh':'车站','source':'hh','lines':['one']}]}
            path=root/'data/moscow-geo.json';path.write_text(json.dumps(catalog))
            export=root/'official.json';export.write_text(json.dumps([{'global_id':1,'Cells':{'Name':'Новая остановка'}}]))
            with patch.object(audit.geo,'ROOT',root),contextlib.redirect_stdout(io.StringIO()):
                audit.geo.main(['--stop-export',str(export),'--official-source-url','https://data.mos.ru/opendata/example','--official-as-of','2026-10-30'])
            result=json.loads(path.read_text());rows={row['id']:row for row in result['records']}
            self.assertEqual(rows['osm:old']['zh'],'旧站');self.assertEqual(rows['osm:old']['lat'],55.7);self.assertEqual(rows['hh:one']['lines'],['one']);self.assertIn('mos:stop:1',rows)
            for url in ('https://data.mos.ru/data?api_key=secret','https://user:secret@data.mos.ru/data','http://data.mos.ru/data'):
                with self.subTest(url=url),self.assertRaises(ValueError):audit.geo.validate_official_export_metadata(url,'2026-10-30')
            audit.geo.validate_official_export_metadata('https://data.mos.ru/data?id=123&format=csv','2026-10-30')

    def test_official_merge_preserves_snapshot_ids_chinese_provenance_and_interchanges(self):
        old=[{'id':'hh:1','kind':'metro','ru':'Тест','zh':'测试','source':'hh','lines':['1']},{'id':'hh:2','kind':'metro','ru':'Тест','zh':'测试','source':'hh','lines':['2']},{'id':'osm:1','kind':'stop','ru':'Парк'}]
        incoming=[{'id':'mos:metro:1','kind':'metro','ru':'Тест','source':'official'},{'id':'mos:metro:2','kind':'metro','ru':'Новая','source':'official'}]
        rows=audit.geo.merge_official_records(old,incoming,'mos-source-sha','2026-10-30');self.assertEqual(len(rows),4)
        self.assertEqual([row['id'] for row in rows[:3]],['hh:1','hh:2','osm:1']);self.assertEqual(rows[0]['zh'],'测试');self.assertEqual(rows[1]['lines'],['2']);self.assertEqual(rows[0]['source'],'hh');self.assertEqual(rows[0]['officialRegistryEvidence'][0]['scope'],'normalized-name-presence-only')
        self.assertEqual(audit.geo.merge_official_records(rows,incoming,'mos-source-sha','2026-10-30'),rows)
        with self.assertRaises(ValueError):audit.geo.merge_official_records(rows,[{'id':'mos:metro:2','kind':'metro','ru':'Переименована'}],'new','2026-10-31')

if __name__=='__main__':unittest.main()
