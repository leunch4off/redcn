import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
import university_store as store
from test_university_store import fixture

class CampusLifecycleTests(unittest.TestCase):
    def test_current_data_has_bound_points_and_separate_historical_addresses(self):
        data,_=store.read_catalog()
        points=[row for row in data['campuses'] if row.get('coordinateEvidence')]
        self.assertEqual(len(points),4)
        for row in points:
            self.assertEqual(row['coordinateEvidence']['boundCampusId'],row['id'])
            self.assertEqual(row['coordinateEvidence']['boundAddressRu'],row['addressRu'])
        old=next(row for row in data['campuses'] if row['id']=='hse-economics-former-shabolovka')
        self.assertFalse(old['locationLifecycle']['buildingClosed'])
        self.assertIsNone(old['locationLifecycle']['moveDate'])

    def test_relocations_do_not_grant_false_building_closure_or_foreign_replacement(self):
        data=fixture(); current=data['campuses'][0]
        old=copy.deepcopy(current); old.update(id='old-address',ru='Исторический адрес',aliases=['Старый корпус'])
        old['locationLifecycle']={'status':'former-address','currentCampusId':current['id'],'checkedAt':'2026-10-30','sourceIds':['official-one'],'physicalAccessVerified':False,'buildingClosed':False,'noteRu':'Historical comparison, not building closure.'}
        data['campuses'].append(old)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'catalog.json'
            def write():path.write_text(json.dumps(data))
            write();self.assertIsNotNone(store.read_catalog(path))
            for patch in ({'buildingClosed':True},{'currentCampusId':'missing'},{'currentCampusId':old['id']},{'physicalAccessVerified':True},{'checkedAt':'2099-01-01'}):
                previous=copy.deepcopy(old['locationLifecycle']);old['locationLifecycle'].update(patch);write()
                with self.subTest(patch=patch),self.assertRaises(ValueError):store.read_catalog(path)
                old['locationLifecycle']=previous
            old['locationLifecycle'].update(status='closed',closureScope='department',currentCampusId=None);write()
            with self.assertRaisesRegex(ValueError,'отдельного прямого'):store.read_catalog(path)

    def test_closed_unit_requires_its_own_institution_act_and_does_not_close_the_building(self):
        data,_=store.read_catalog();hse=next(row for row in data['universities'] if row['id']=='hse');unit=hse['academicUnitHistory'][0]
        self.assertEqual(unit['closureScope'],'academic-unit-only');self.assertIsNone(unit['effectiveClosureDate'])
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'catalog.json'
            for key,value in [('closureScope','whole-university'),('sourceIds',['faculty-hse-economics-contact']),('documentDate','2099-01-01')]:
                old=unit[key];unit[key]=value;path.write_text(json.dumps(data))
                with self.subTest(key=key),self.assertRaises(ValueError):store.read_catalog(path)
                unit[key]=old

    def test_coordinate_provenance_is_bound_to_exact_address_object(self):
        data,_=store.read_catalog();row=next(row for row in data['campuses'] if row.get('coordinateEvidence'))
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'catalog.json'
            for key,value in [('boundCampusId','other'),('boundAddressRu','Другой адрес'),('accuracyScope','measured-to-centimetres')]:
                prior=row['coordinateEvidence'][key];row['coordinateEvidence'][key]=value;path.write_text(json.dumps(data))
                with self.subTest(key=key),self.assertRaises(ValueError):store.read_catalog(path)
                row['coordinateEvidence'][key]=prior

if __name__=='__main__':unittest.main()
