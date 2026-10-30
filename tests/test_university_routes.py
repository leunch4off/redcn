import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'cli'))
from university_routes import validate_routes


class UniversityRouteValidation(unittest.TestCase):
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
