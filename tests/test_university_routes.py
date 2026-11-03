import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'cli'))
from university_routes import validate_routes


class UniversityRouteValidation(unittest.TestCase):
    def test_route_receipts_walking_transfers_and_segments_match_browser_canonical_shape(self):
        rows=[{'name':'МГУ','campusId':'msu-first','time':'40–55','mode':'public',
               'method':'manual-confirmed','checkedAt':'2026-10-31',
               'sourceUrl':'https://yandex.ru/maps/?rtext=55,37~56,38',
               'accessMinutes':'0','egressMinutes':'7','transfers':'1',
               'segments':[{'mode':'walk','from':'Дом','to':'Остановка','minutes':'5'},
                           {'mode':'bus','transport':'119','from':'Остановка','to':'Корпус','minutes':'25'}]}]
        result=validate_routes(rows)
        program="import {normalizeUniversityRoutes} from './web/university-routes.mjs';let text='';for await(const chunk of process.stdin)text+=chunk;console.log(JSON.stringify(normalizeUniversityRoutes({universityRoutes:JSON.parse(text)})));"
        actual=json.loads(subprocess.check_output(['node','--input-type=module','-e',program],cwd=ROOT,input=json.dumps(rows).encode()))
        self.assertEqual(result,actual)
        self.assertEqual(result[0]['accessMinutes'],0)
        self.assertEqual(result[0]['segments'][1]['transport'],'119')
        self.assertEqual(result[0]['checkedAt'],'2026-10-31')

    def test_invalid_verification_dates_urls_counts_and_segments_are_rejected(self):
        for patch in ({'checkedAt':'2026-02-30'},{'checkedAt':'2026-1-1'},
                      {'sourceUrl':'javascript:alert(1)'},{'sourceUrl':'http://example.org'},
                      {'sourceUrl':'https://user:pass@example.org/a'},
                      {'method':'trusted-without-checking'},{'accessMinutes':True},
                      {'egressMinutes':181},{'transfers':1.5},{'transfers':13},
                      {'segments':[{'mode':'walk','minutes':5}]*9},
                      {'segments':[{'mode':'bus','minutes':2.5}]},
                      {'segments':[{'mode':'bus','url':'https://example.org'}]},
                      {'segments':[{'mode':'airplane','minutes':20}]}):
            with self.subTest(patch=patch),self.assertRaises(ValueError):
                validate_routes([{'name':'МГУ',**patch}])

    def test_canonical_routes_match_shared_javascript_and_preserve_zero(self):
        rows=[{'name':' МГУ ','campusId':'msu-main','latitude':'55.7','longitude':'',
               'time':'40–55','mode':'public','transport':'Автобус 119',
               'routeDistanceKm':0,'stops':[{'name':' Остановка ','latitude':0,'longitude':''}]}]
        result=validate_routes(rows)
        program="import {normalizeUniversityRoutes} from './web/university-routes.mjs';let text='';for await(const chunk of process.stdin)text+=chunk;console.log(JSON.stringify(normalizeUniversityRoutes({universityRoutes:JSON.parse(text)})));"
        actual=json.loads(subprocess.check_output(['node','--input-type=module','-e',program],cwd=ROOT,input=json.dumps(rows).encode()))
        self.assertEqual(result,actual)
        self.assertEqual(result[0]['routeDistanceKm'],0)
        self.assertEqual(result[0]['stops'][0]['latitude'],0)
        self.assertEqual(result[0]['longitude'],'')
        self.assertEqual(rows[0]['name'],' МГУ ')

    def test_empty_list_clears_and_limits_or_unknown_urls_are_rejected(self):
        self.assertEqual(validate_routes([]),[])
        for value in ({},[{'name':'МГУ'}]*11,[{'name':'МГУ','url':'https://example.com'}],[{'name':'МГУ','campusId':'unknown'}],[{'name':'МГУ','stops':[{'name':'A'}]*4}]):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):validate_routes(value)

    def test_nonfinite_boolean_out_of_range_and_unbounded_strings_are_rejected(self):
        for patch in ({'latitude':True},{'latitude':'0x0'},{'latitude':'1_000'},{'longitude':float('inf')},{'routeDistanceKm':float('nan')},{'latitude':91},{'longitude':181},{'routeDistanceKm':-1},{'routeDistanceKm':20001},{'mode':'flight'},{'time':40},{'name':'a'*241},{'transport':'a'*601},{'stops':[{'name':'A','url':'javascript:bad'}]}):
            with self.subTest(patch=patch):
                with self.assertRaises(ValueError):validate_routes([{'name':'МГУ',**patch}])


if __name__=='__main__':unittest.main()
