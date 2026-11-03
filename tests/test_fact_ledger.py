"""Isolated fact validation/version/cache checks; no runtime controller is made."""
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
import fact_ledger
import local_server
import redcn
import language_service


class FactLedgerTests(unittest.TestCase):
    def test_shared_schema_cases_and_no_coercion(self):
        cases=json.loads((Path(__file__).parent/'fixtures/field-schema-cases.json').read_text())
        for row in cases:
            with self.subTest(row=row['name']):
                before=json.dumps(row['fields'],sort_keys=True)
                if row['valid']:self.assertIs(fact_ledger.validate_listing_fields(row['fields']),row['fields'])
                else:
                    with self.assertRaises(ValueError):fact_ledger.validate_listing_fields(row['fields'])
                self.assertEqual(json.dumps(row['fields'],sort_keys=True),before)

    def test_result_shape_and_generator_reject_before_persistence_or_subprocess(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):local_server.save_result(Path(folder),{'fields':{'price':[80000]}})
            self.assertFalse((Path(folder)/'listings.sqlite3').exists())
        with patch.object(redcn.subprocess,'run') as execute:
            with self.assertRaises(ValueError):redcn.generate('Описание',{'price':False})
            execute.assert_not_called()
        for data in ({'fields':[]},{'fields':{},'titles':[1]},{'fields':{},'body':{}}):
            with self.assertRaises(ValueError):fact_ledger.validate_result_fields(data)
        legacy={'fields':{'price':'Аренда 80000 ₽ и комиссия 50%','area':'52 м²; комиссия 50%','walk':8},'titles':'Новая квартира'}
        self.assertIs(fact_ledger.validate_result_fields(legacy),legacy)
        self.assertEqual(legacy['fields']['area'],'52 м²; комиссия 50%')
        self.assertEqual(fact_ledger.compare_fact_snapshots({'price':'80 000','walk':8},{'price':80000,'walk':'8'}),[])
        self.assertEqual([row['field'] for row in fact_ledger.compare_fact_snapshots({'price':'по запросу'},{'price':'Аренда 80000 ₽'})],['price'])
        self.assertEqual([row['field'] for row in fact_ledger.compare_fact_snapshots({'price':'1 2'},{'price':'12'})],['price'])

    def test_save_creates_private_atomic_price_condition_revisions_with_actor(self):
        with tempfile.TemporaryDirectory() as folder:
            directory=Path(folder)
            card={'fields':{'price':'80000','deposit':'40000','petPolicy':'no','source':'PRIVATE_HTML','photos':['PRIVATE_PHOTO']},'titles':['测试'],'body':'测试','tags':''}
            card_id=local_server.save_result(directory,card,actor='Евгений',kind='import')
            card['fields'].update(price='85000',petPolicy='yes')
            local_server.save_result(directory,card,card_id,actor='Редактор')
            local_server.save_result(directory,card,card_id,actor='Другой автор')
            rows=fact_ledger.list_fact_revisions(directory,card_id)
            self.assertEqual(len(rows),2);self.assertEqual(rows[0]['actor'],'Редактор');self.assertEqual(rows[0]['revision'],2)
            self.assertEqual([row['field'] for row in rows[0]['changes']],['price','petPolicy'])
            self.assertNotIn('PRIVATE_',json.dumps(rows));self.assertEqual(local_server.read_result(directory,card_id)['fields']['source'],'PRIVATE_HTML')
            card['fields']['price']='99999'
            with self.assertRaises(ValueError):local_server.save_result(directory,card,card_id,actor='')
            self.assertEqual(local_server.read_result(directory,card_id)['fields']['price'],'85000')

    def test_phone_compare_and_swap_also_validates_and_records_fact_revision(self):
        with tempfile.TemporaryDirectory() as folder:
            directory=Path(folder)
            card={'fields':{'price':'80000'},'titles':['测试'],'body':'测试','tags':''}
            card_id=local_server.save_result(directory,card)
            _,original=language_service._read(directory,card_id)
            with self.assertRaises(ValueError):language_service._save(directory,card_id,original,{**card,'fields':{'price':[]}})
            self.assertEqual(len(fact_ledger.list_fact_revisions(directory,card_id)),1)
            changed={**card,'fields':{'price':'81000'}}
            with patch('phone_relay.public_card',return_value={'id':card_id}):language_service._save(directory,card_id,original,changed,actor='Телефон редактора')
            rows=fact_ledger.list_fact_revisions(directory,card_id)
            self.assertEqual(rows[0]['actor'],'Телефон редактора');self.assertEqual(rows[0]['fields']['price'],'81000')
            with self.assertRaises(language_service.LanguageConflict):language_service._save(directory,card_id,original,{**card,'fields':{'price':'82000'}})
            self.assertEqual(len(fact_ledger.list_fact_revisions(directory,card_id)),2)

    def test_route_revision_allowlist_never_retains_nested_html_auth_or_photo_fields(self):
        fields={'universityRoutes':[{'name':'МГУ','time':'30','sourceHtml':'PRIVATE_HTML','auth':'PRIVATE_AUTH','stops':[{'name':'Stop','cookie':'PRIVATE_COOKIE'}]}]}
        result=fact_ledger.fact_snapshot(fields)
        self.assertEqual(result,{'universityRoutes':[{'name':'МГУ','time':'30','stops':[{'name':'Stop'}]}]})

    def test_clear_import_cache_only_preserves_cards_dictionaries_history_seen_and_files(self):
        with tempfile.TemporaryDirectory() as folder:
            directory=Path(folder);card_id=local_server.save_result(directory,{'fields':{'price':'80000'}})
            with redcn.open_cache(directory) as connection:
                connection.execute('INSERT INTO cache VALUES (?,?,?)',('https://www.cian.ru/flat/1',1,'PRIVATE_HTML'))
                connection.execute('INSERT INTO seen VALUES (?,?)',('https://www.cian.ru/flat/1',1))
                connection.execute('CREATE TABLE history (id TEXT)');connection.execute("INSERT INTO history VALUES ('history-kept')")
                connection.execute('CREATE TABLE dictionary (word TEXT)');connection.execute("INSERT INTO dictionary VALUES ('kept')")
            image=directory/'original-photo.bin';image.write_bytes(b'ORIGINAL_PHOTO')
            result=fact_ledger.clear_import_cache(directory)
            self.assertEqual(result,{'cleared':1,'scope':'import-cache'})
            with sqlite3.connect(directory/'listings.sqlite3') as connection:
                self.assertEqual(connection.execute('SELECT COUNT(*) FROM cache').fetchone()[0],0)
                for table in ('seen','results','history','dictionary','fact_revisions'):self.assertEqual(connection.execute('SELECT COUNT(*) FROM '+table).fetchone()[0],1)
            self.assertEqual(image.read_bytes(),b'ORIGINAL_PHOTO');self.assertEqual(local_server.read_result(directory,card_id)['fields']['price'],'80000')


if __name__=='__main__':unittest.main()
